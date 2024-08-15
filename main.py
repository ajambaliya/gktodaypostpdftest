import io
import os
import requests
from bs4 import BeautifulSoup
from odf.opendocument import load
from odf.text import H, P, List
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
DB_NAME = os.environ.get('DB_NAME')
COLLECTION_NAME = os.environ.get('COLLECTION_NAME')
MONGO_CONNECTION_STRING = os.environ.get('MONGO_CONNECTION_STRING')

if not all([DB_NAME, COLLECTION_NAME, MONGO_CONNECTION_STRING]):
    raise ValueError("One or more required MongoDB environment variables are not set")

client = pymongo.MongoClient(MONGO_CONNECTION_STRING)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

class DownloadError(Exception):
    pass

class ConversionError(Exception):
    pass

class ContentInsertionError(Exception):
    pass

class TelegramSendError(Exception):
    pass

def fetch_article_urls(base_url, pages):
    article_urls = []
    logging.info(f"Fetching article URLs from {base_url} across {pages} pages")
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
        content_list.append({'type': 'heading', 'text': heading_text})
        
        for tag in main_content.find_all(recursive=False):
            if tag.get('class') in [['sharethis-inline-share-buttons', 'st-center', 'st-has-labels', 'st-inline-share-buttons', 'st-animated'], ['prenext']]:
                continue
            text = tag.get_text()
            translated_text = translate_to_gujarati(text)
            if tag.name == 'p':
                content_list.append({'type': 'paragraph', 'text': translated_text})
                content_list.append({'type': 'paragraph', 'text': text})
            elif tag.name == 'h2':
                content_list.append({'type': 'heading_2', 'text': translated_text})
                content_list.append({'type': 'heading_2', 'text': text})
            elif tag.name == 'h4':
                content_list.append({'type': 'heading_4', 'text': translated_text})
                content_list.append({'type': 'heading_4', 'text': text})
            elif tag.name == 'ul':
                for li in tag.find_all('li'):
                    li_text = li.get_text()
                    translated_li_text = translate_to_gujarati(li_text)
                    content_list.append({'type': 'list_item', 'text': f"• {translated_li_text}"})
                    content_list.append({'type': 'list_item', 'text': f"• {li_text}"})
        return content_list
    except Exception as e:
        logging.error(f"Error scraping content from {url}: {e}")
        raise

def insert_content_between_placeholders(doc, content_list):
    try:
        start_placeholder = end_placeholder = None
        
        for i, para in enumerate(doc.text.getElementsByType(P)):
            if para.firstChild is None:
                logging.warning(f"Empty paragraph found at index {i}")
                continue
            if para.firstChild.data is None:
                logging.warning(f"Paragraph at index {i} has no data")
                continue
            if "START_CONTENT" in para.firstChild.data:
                start_placeholder = i
            elif "END_CONTENT" in para.firstChild.data:
                end_placeholder = i
                break
        
        if start_placeholder is None or end_placeholder is None:
            raise ContentInsertionError("Could not find both placeholders")
        
        if start_placeholder is None or end_placeholder is None:
            raise ContentInsertionError("Could not find both placeholders")
        
        logging.info("Removing existing content between placeholders")
        for i in range(end_placeholder - 1, start_placeholder, -1):
            doc.text.removeElement(doc.text.getElementsByType(P)[i])
        
        logging.info("Inserting new content between placeholders")
        content_list = content_list[::-1]
        
        for content in content_list:
            if content['type'] == 'heading':
                doc.text.addElement(H(level=1, text=content['text']))
            elif content['type'] == 'paragraph':
                doc.text.addElement(P(text=content['text']))
            elif content['type'] == 'heading_2':
                doc.text.addElement(H(level=2, text=content['text']))
            elif content['type'] == 'heading_4':
                doc.text.addElement(H(level=4, text=content['text']))
            elif content['type'] == 'list_item':
                doc.text.addElement(List(text=content['text']))
        
        logging.info("Clearing placeholder text")
        doc.text.getElementsByType(P)[start_placeholder].setAttribute('text', "")
        doc.text.getElementsByType(P)[end_placeholder].setAttribute('text', "")
    except Exception as e:
        logging.error(f"Error inserting content into ODT document: {e}")
        raise
    except AttributeError as e:
        logging.error(f"AttributeError in insert_content_between_placeholders: {e}")
        raise ContentInsertionError("Error accessing paragraph data")
    except Exception as e:
        logging.error(f"Error inserting content into ODT document: {e}")
        raise
        
def download_template(url):
    try:
        # Use direct download link format
        download_url = url.replace('/edit?usp=sharing', '/uc?export=download')
        logging.info(f"Downloading template from {download_url}")
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
    logging.info("Checking and inserting URLs")
    for url in urls:
        if 'daily-current-affairs-quiz' in url:
            continue
        if not collection.find_one({'url': url}):
            new_urls.append(url)
            collection.insert_one({'url': url})
    return new_urls

def convert_odt_to_pdf(odt_path, pdf_path):
    try:
        logging.info(f"Converting ODT file {odt_path} to PDF at {pdf_path}")
        result = subprocess.run(['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', 
                                os.path.dirname(pdf_path), odt_path], 
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logging.info(f"Conversion output: {result.stdout.decode()}")
        logging.error(f"Conversion errors: {result.stderr.decode()}")
        
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
        logging.info(f"Renaming PDF file to {new_pdf_path}")
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
        logging.info("PDF sent to Telegram successfully")
    except Exception as e:
        logging.error(f"Error sending PDF to Telegram: {e}")
        raise

async def main():
    try:
        base_url = 'https://www.gktoday.in/current-affairs/'
        pages = 2
        template_url = 'https://drive.google.com/uc?export=download&id=1Qr96XcaRrmODZl4tvxhcZkneebj9OpGO'

        urls = fetch_article_urls(base_url, pages)
        new_urls = check_and_insert_urls(urls)
        
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        channel_id = os.environ.get('TELEGRAM_CHANNEL_ID')
        if not all([bot_token, channel_id]):
            raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID environment variable is not set")
        
        caption = "Generated PDF document"
        
        for url in new_urls:
            content_list = await scrape_and_get_content(url)
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.odt') as odt_file:
                odt_file_path = odt_file.name
                logging.info(f"Creating temporary ODT file at {odt_file_path}")
                template = download_template(template_url)
                with open(odt_file_path, 'wb') as file:
                    file.write(template.read())

                doc = load(odt_file_path)
                insert_content_between_placeholders(doc, content_list)
                
                with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as pdf_file:
                    pdf_file_path = pdf_file.name
                    convert_odt_to_pdf(odt_file_path, pdf_file_path)
                    
                    new_pdf_name = f"Article_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                    final_pdf_path = rename_pdf(pdf_file_path, new_pdf_name)
                    
                    await send_pdf_to_telegram(final_pdf_path, bot_token, channel_id, caption)
                    
                os.remove(odt_file_path)
                os.remove(final_pdf_path)
    except Exception as e:
        logging.error(f"Error in main execution: {e}")

if __name__ == "__main__":
    asyncio.run(main())
