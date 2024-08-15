import os
import asyncio
import requests
from bs4 import BeautifulSoup
from odf.opendocument import load
from odf.text import P, H
from odf import tei  # Import for ODF handling
import pymongo
from datetime import datetime
import telegram

# MongoDB setup
DB_NAME = os.environ.get('DB_NAME')
COLLECTION_NAME = os.environ.get('COLLECTION_NAME')
MONGO_CONNECTION_STRING = os.environ.get('MONGO_CONNECTION_STRING')

if not all([DB_NAME, COLLECTION_NAME, MONGO_CONNECTION_STRING]):
    raise ValueError("One or more required MongoDB environment variables are not set")

client = pymongo.MongoClient(MONGO_CONNECTION_STRING)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

def fetch_article_urls(base_url, pages):
    article_urls = []
    for page in range(1, pages + 1):
        url = base_url if page == 1 else f"{base_url}page/{page}/"
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')
        for h1_tag in soup.find_all('h1', id='list'):
            a_tag = h1_tag.find('a')
            if a_tag and a_tag.get('href'):
                article_urls.append(a_tag['href'])
    return article_urls

def translate_to_gujarati(text):
    try:
        translator = GoogleTranslator(source='auto', target='gu')
        return translator.translate(text)
    except exceptions.TranslationNotFoundException:
        return text
    except Exception as e:
        print(f"Translation Error: {e}")
        return text

async def scrape_and_get_content(url):
    try:
        response = requests.get(url)
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
        print(f"Scraping Error for {url}: {e}")
        return []

def insert_content_between_placeholders(doc, content_list):
    start_placeholder = end_placeholder = None
    
    for i, element in enumerate(doc.text):
        if "START_CONTENT" in element.text:
            start_placeholder = i
        elif "END_CONTENT" in element.text:
            end_placeholder = i
            break
    
    if start_placeholder is None or end_placeholder is None:
        raise Exception("Could not find both placeholders")

    for i in range(end_placeholder - 1, start_placeholder, -1):
        del doc.text[i]

    content_list = content_list[::-1]

    for content in content_list:
        if content['type'] == 'heading':
            doc.text.insert(start_placeholder, H(1, text=content['text']))
        elif content['type'] == 'paragraph':
            doc.text.insert(start_placeholder, P(text=content['text']))
        elif content['type'] == 'heading_2':
            doc.text.insert(start_placeholder, H(2, text=content['text']))
        elif content['type'] == 'heading_4':
            doc.text.insert(start_placeholder, H(4, text=content['text']))
        elif content['type'] == 'list_item':
            doc.text.insert(start_placeholder, P(text=content['text']))

def download_template(url):
    download_url = url.replace('/edit?usp=sharing', '/export?format=odt')
    try:
        response = requests.get(download_url)
        response.raise_for_status()
        return io.BytesIO(response.content)
    except requests.exceptions.RequestException as e:
        print(f"Download Error: {e}")
        raise

def check_and_insert_urls(urls):
    existing_urls = set(doc['url'] for doc in collection.find({}, {'url': 1}))
    new_urls = []
    for url in urls:
        if url not in existing_urls:
            new_urls.append(url)
            collection.insert_one({'url': url})
        else:
            print(f"URL already exists in MongoDB: {url}")
    return new_urls

def convert_odt_to_pdf(odt_path, pdf_path):
    try:
        subprocess.run(['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', 
                        os.path.dirname(pdf_path), odt_path], 
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        original_pdf = os.path.splitext(os.path.basename(odt_path))[0] + '.pdf'
        original_pdf_path = os.path.join(os.path.dirname(pdf_path), original_pdf)
        os.rename(original_pdf_path, pdf_path)
    except subprocess.CalledProcessError as e:
        print(f"Conversion Error: {e}")
        raise

def rename_pdf(pdf_path, new_name):
    new_pdf_path = os.path.join(os.path.dirname(pdf_path), new_name)
    os.rename(pdf_path, new_pdf_path)
    return new_pdf_path

async def send_pdf_to_telegram(pdf_path, bot_token, channel_id, caption):
    bot = telegram.Bot(token=bot_token)
    for _ in range(3):
        try:
            with open(pdf_path, 'rb') as pdf_file:
                await bot.send_document(chat_id=channel_id, document=pdf_file, filename=os.path.basename(pdf_path), caption=caption)
            break
        except telegram.error.TimedOut:
            await asyncio.sleep(5)

async def main():
    try:
        base_url = "https://www.gktoday.in/current-affairs/"
        article_urls = fetch_article_urls(base_url, 2)
        
        # Add a hardcoded test URL for guaranteed processing
        test_url = "https://www.gktoday.in/bengaluru-researchers-discover-three-new-edible-bug-species/"
        article_urls.append(test_url)

        print(f"Extracted URLs: {article_urls}")

        new_urls = check_and_insert_urls(article_urls)

        print(f"New URLs to process: {new_urls}")

        if not new_urls:
            print("No new URLs found.")
            return

        template_url = os.environ.get('TEMPLATE_URL')
        if not template_url:
            raise ValueError("TEMPLATE_URL environment variable is not set")

        template_bytes = download_template(template_url)
        doc = load(template_bytes)

        content_lists = await asyncio.gather(*[scrape_and_get_content(url) for url in new_urls])

        print(f"Content lists: {content_lists}")

        for content_list in content_lists:
            insert_content_between_placeholders(doc, content_list)

        pdf_path = '/tmp/converted_document.pdf'
        convert_odt_to_pdf('/tmp/template.odt', pdf_path)
        new_pdf_name = f"Current_Affairs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        renamed_pdf_path = rename_pdf(pdf_path, new_pdf_name)

        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        channel_id = os.environ.get('TELEGRAM_CHANNEL_ID')

        if not bot_token or not channel_id:
            raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID environment variable is not set")

        await send_pdf_to_telegram(renamed_pdf_path, bot_token, channel_id, "Current Affairs PDF")

    except Exception as e:
        print(f"Main Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
