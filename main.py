import os
import io
import requests
from bs4 import BeautifulSoup
from odf.opendocument import load
from odf.text import H, P, Span
from datetime import datetime
import pymongo
from deep_translator import GoogleTranslator, exceptions
import asyncio
import telegram
import tempfile
import subprocess
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# MongoDB setup
DB_NAME = os.getenv('DB_NAME')
COLLECTION_NAME = os.getenv('COLLECTION_NAME')
MONGO_CONNECTION_STRING = os.getenv('MONGO_CONNECTION_STRING')

if not all([DB_NAME, COLLECTION_NAME, MONGO_CONNECTION_STRING]):
    raise ValueError("One or more required MongoDB environment variables are not set")

client = pymongo.MongoClient(MONGO_CONNECTION_STRING)
collection = client[DB_NAME][COLLECTION_NAME]

class DownloadError(Exception):
    pass

class ConversionError(Exception):
    pass

def fetch_article_urls(base_url, pages):
    article_urls = []
    for page in range(1, pages + 1):
        url = base_url if page == 1 else f"{base_url}page/{page}/"
        try:
            response = requests.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            for h1_tag in soup.find_all('h1', id='list'):
                a_tag = h1_tag.find('a')
                if a_tag and a_tag.get('href'):
                    article_urls.append(a_tag['href'])
        except requests.RequestException as e:
            logging.error(f"Error fetching URL {url}: {e}")
    return article_urls

def translate_to_gujarati(text):
    try:
        translator = GoogleTranslator(source='auto', target='gu')
        return translator.translate(text)
    except exceptions.TranslationNotFoundException:
        logging.warning("Translation not found, returning original text")
        return text
    except Exception as e:
        logging.error(f"Translation error: {e}")
        return text

async def scrape_and_get_content(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        main_content = soup.find('div', class_='inside_post column content_width')
        if not main_content:
            raise Exception("Main content div not found")

        heading = main_content.find('h1', id='list')
        if not heading:
            raise Exception("Heading not found")

        content_list = []
        heading_text = heading.get_text()
        translated_heading = translate_to_gujarati(heading_text)
        content_list.append({'type': 'heading', 'text': translated_heading})

        for tag in main_content.find_all(recursive=False):
            if tag.get('class') in [['sharethis-inline-share-buttons'], ['prenext']]:
                continue
            text = tag.get_text()
            translated_text = translate_to_gujarati(text)
            if tag.name in ['p', 'h2', 'h4']:
                content_list.append({'type': tag.name, 'text': translated_text})
            elif tag.name == 'ul':
                for li in tag.find_all('li'):
                    li_text = li.get_text()
                    translated_li_text = translate_to_gujarati(li_text)
                    content_list.append({'type': 'list_item', 'text': f"• {translated_li_text}"})
        return content_list
    except Exception as e:
        logging.error(f"Error scraping content from {url}: {e}")
        raise

def insert_content_between_placeholders(doc, content_list):
    try:
        start_placeholder = end_placeholder = None
        
        for i, para in enumerate(doc.text.getElementsByType(P)):
            para_text = "".join([element.text if hasattr(element, 'text') else '' for element in para.childNodes])
            if "START_CONTENT" in para_text.strip():
                start_placeholder = i
            elif "END_CONTENT" in para_text.strip():
                end_placeholder = i
                break

        if start_placeholder is None:
            raise Exception("Could not find the START_CONTENT placeholder")
        if end_placeholder is None:
            raise Exception("Could not find the END_CONTENT placeholder")

        # Clear content between the placeholders
        for i in range(end_placeholder - 1, start_placeholder, -1):
            doc.text.removeElement(doc.text.getElementsByType(P)[i])

        # Insert the new content
        content_list.reverse()
        for content in content_list:
            p = P()
            p.addElement(Span(text=content['text']))
            doc.text.insertBefore(p, doc.text.getElementsByType(P)[start_placeholder + 1])

        # Clear the placeholders themselves
        doc.text.getElementsByType(P)[start_placeholder].setTextContent("")
        doc.text.getElementsByType(P)[end_placeholder].setTextContent("")
    except Exception as e:
        logging.error(f"Error inserting content into ODT document: {e}")
        raise


def download_template(url):
    try:
        download_url = url.replace('/edit?usp=sharing', '/uc?export=download')
        response = requests.get(download_url)
        response.raise_for_status()
        if not response.content:
            raise DownloadError("Downloaded file is empty")
        return io.BytesIO(response.content)
    except requests.RequestException as e:
        logging.error(f"Error downloading template: {e}")
        raise DownloadError("Failed to download template")

def check_and_insert_urls(urls):
    new_urls = []
    for url in urls:
        if 'daily-current-affairs-quiz' in url:
            continue
        if not collection.find_one({'url': url}):
            new_urls.append(url)
            collection.insert_one({'url': url})
    return new_urls

def convert_odt_to_pdf(odt_path, pdf_path):
    try:
        result = subprocess.run(['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', 
                                os.path.dirname(pdf_path), odt_path], 
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        original_pdf = os.path.splitext(os.path.basename(odt_path))[0] + '.pdf'
        original_pdf_path = os.path.join(os.path.dirname(pdf_path), original_pdf)
        if not os.path.exists(original_pdf_path):
            raise ConversionError(f"Converted PDF file does not exist at {original_pdf_path}")
        os.rename(original_pdf_path, pdf_path)
    except subprocess.CalledProcessError as e:
        logging.error(f"Error converting ODT to PDF: {e}")
        raise ConversionError("Failed to convert ODT to PDF")

def rename_pdf(pdf_path, new_name):
    try:
        new_pdf_path = os.path.join(os.path.dirname(pdf_path), new_name)
        os.rename(pdf_path, new_pdf_path)
        return new_pdf_path
    except OSError as e:
        logging.error(f"Error renaming PDF file: {e}")
        raise

async def send_pdf_to_telegram(pdf_path, bot_token, channel_id, caption):
    try:
        bot = telegram.Bot(token=bot_token)
        with open(pdf_path, 'rb') as pdf_file:
            await bot.send_document(chat_id=channel_id, document=pdf_file, caption=caption)
    except Exception as e:
        logging.error(f"Error sending PDF to Telegram: {e}")
        raise

async def main():
    try:
        base_url = 'https://www.gktoday.in/current-affairs/'
        pages = 2
        template_url = 'https://drive.google.com/uc?export=download&id=1Qr96XcaRrmODZl4tvxhcZkneebj9OpGO'
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        channel_id = os.getenv('TELEGRAM_CHANNEL_ID')
        caption = "Generated PDF document"
        
        if not all([bot_token, channel_id]):
            raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID environment variable is not set")
        
        urls = fetch_article_urls(base_url, pages)
        new_urls = check_and_insert_urls(urls)

        for url in new_urls:
            content_list = await scrape_and_get_content(url)

            with tempfile.NamedTemporaryFile(delete=False, suffix='.odt') as odt_file:
                odt_file_path = odt_file.name
                template = download_template(template_url)
                with open(odt_file_path, 'wb') as file:
                    file.write(template.read())

                doc = load(odt_file_path)
                insert_content_between_placeholders(doc, content_list)
                doc.save(odt_file_path)

                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as pdf_file:
                    pdf_file_path = pdf_file.name
                    convert_odt_to_pdf(odt_file_path, pdf_file_path)

                    new_pdf_name = f"Article_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                    final_pdf_path = rename_pdf(pdf_file_path, new_pdf_name)

                    await send_pdf_to_telegram(final_pdf_path, bot_token, channel_id, caption)

                os.remove(odt_file_path)
                os.remove(final_pdf_path)

            logging.info(f"Finished processing URL: {url}")
    except Exception as e:
        logging.error(f"Error in main execution: {e}")

if __name__ == "__main__":
    asyncio.run(main())
