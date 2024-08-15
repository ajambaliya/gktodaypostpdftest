import os
import asyncio
import requests
import logging
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError
from pymongo import MongoClient
import random
from datetime import datetime
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
import io
import subprocess
import tempfile

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Read environment variables
mongo_uri = os.getenv('MONGO_URI')
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEFAULT_CHANNEL = os.getenv('DEFAULT_CHANNEL')
TEMPLATE_URL = os.getenv('TEMPLATE_URL', 'https://docs.google.com/document/d/12t9nJzPPHqXbRcH3As4PitcJi9w0SeuD/edit?usp=sharing&ouid=108520131839767724661&rtpof=true&sd=true')

# Initialize MongoDB client and Telegram bot
client = MongoClient(mongo_uri)
bot = Bot(token=BOT_TOKEN)

def fetch_collections(database_name):
    db = client[database_name]
    return db.list_collection_names()

def fetch_questions_from_collection(database_name, collection_name, num_questions):
    db = client[database_name]
    collection = db[collection_name]
    questions = collection.aggregate([{ '$sample': { 'size': num_questions } }])
    return list(questions)

def get_correct_option_index(answer_key):
    option_mapping = {'a': 0, 'b': 1, 'c': 2, 'd': 3}
    return option_mapping.get(answer_key.lower(), None)

def get_quiz_day():
    db = client['QuizDays']
    collection = db['Days']
    today = datetime.now().date()
    today_datetime = datetime.combine(today, datetime.min.time())

    day_record = collection.find_one({'date': today_datetime})
    
    if day_record:
        return day_record['day']
    else:
        last_day_record = collection.find_one(sort=[('date', pymongo.DESCENDING)])
        new_day = 1 if not last_day_record else last_day_record['day'] + 1
        
        collection.insert_one({'date': today_datetime, 'day': new_day})
        return new_day

def update_quiz_counter(collection_name):
    db = client['QuizCounters']
    collection = db['Counters']
    counter_record = collection.find_one({'collection_name': collection_name})
    
    if counter_record:
        new_count = counter_record['count'] + 1
        collection.update_one({'collection_name': collection_name}, {'$set': {'count': new_count}})
        return new_count
    else:
        collection.insert_one({'collection_name': collection_name, 'count': 1})
        return 1

async def send_intro_message(collection_name, num_questions):
    day = get_quiz_day()
    intro_message = (
        f"🎯 *આજની કવિઝ - Day {day}* 🎯\n\n"
        f"📚 વિષય: *{collection_name}*\n"
        f"🔢 પ્રશ્નોની સંખ્યા: *{num_questions}*\n\n"
        f"🕐 અમારા ટેલીગ્રામ ચેનલમાં દરરોજ બપોરે *1 વાગ્યે* અને રાત્રે *9 વાગ્યે* "
        f"*{num_questions}* પ્રશ્નોની કવિઝ મુકવામાં આવે છે.\n\n"
        f"🔗 *Join* : @CurrentAdda\n\n"
        f"🏆 તૈયાર રહો! કવિઝ શરૂ થવાની તૈયારીમાં છે... 🚀"
    )
    
    try:
        await bot.send_message(
            chat_id=DEFAULT_CHANNEL,
            text=intro_message,
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info("Intro message sent successfully")
    except TelegramError as e:
        logger.error(f"Error sending intro message: {e}")

async def send_quiz_to_channel(question, options, correct_option_index, explanation):
    question_text = f"{question}\n[@CurrentAdda]"
    
    if explanation is None or (isinstance(explanation, float) and math.isnan(explanation)):
        explanation = "@CurrentAdda"
    
    try:
        await bot.send_poll(
            chat_id=DEFAULT_CHANNEL,
            question=question_text,
            options=options,
            type='quiz',
            correct_option_id=correct_option_index,
            explanation=explanation,
            is_anonymous=True,
            allows_multiple_answers=False,
        )
        logger.info(f"Quiz sent successfully: {question}")
    except TelegramError as e:
        logger.error(f"Error sending quiz: {e}")

def download_template(url):
    download_url = url.replace('/edit?usp=sharing', '/export?format=docx')
    try:
        response = requests.get(download_url)
        response.raise_for_status()
        return io.BytesIO(response.content)
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading template: {e}")
        raise

def update_document_with_content(doc_io, intro_message, questions):
    # Save the BytesIO object to a temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as temp_docx_file:
        temp_docx_file.write(doc_io.read())
        temp_docx_path = temp_docx_file.name
    
    # Load the document from the temporary file
    doc = Document(temp_docx_path)
    
    # Insert intro message
    intro_found = False
    for paragraph in doc.paragraphs:
        if '<<START_CONTENT>>' in paragraph.text:
            paragraph.text = intro_message
            paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
            paragraph.style.font.size = Pt(12)
            intro_found = True
            break

    if not intro_found:
        logger.warning("No <<START_CONTENT>> placeholder found in the document.")

    # Insert questions
    for paragraph in doc.paragraphs:
        if '<<END_CONTENT>>' in paragraph.text:
            for q in questions:
                question_paragraph = doc.add_paragraph(f"{q['question']}")
                question_paragraph.style.font.size = Pt(10)
            break

    # Save the updated document to a temporary file
    updated_doc_path = os.path.join(tempfile.gettempdir(), 'updated-template.docx')
    doc.save(updated_doc_path)
    
    return updated_doc_path

def convert_docx_to_pdf(docx_file, pdf_path):
    try:
        output_dir = os.path.dirname(pdf_path)
        result = subprocess.run(
            ['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', output_dir, docx_file],
            check=True, capture_output=True, text=True
        )
        logger.info(f"LibreOffice conversion output: {result.stdout}")
        logger.error(f"LibreOffice conversion error output: {result.stderr}")
        
        pdf_temp_path = os.path.join(output_dir, os.path.splitext(os.path.basename(docx_file))[0] + '.pdf')
        if os.path.exists(pdf_temp_path):
            os.rename(pdf_temp_path, pdf_path)
            logger.info(f"Successfully converted DOCX to PDF: {pdf_path}")
        else:
            raise FileNotFoundError(f"PDF file not found at expected location: {pdf_temp_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"LibreOffice conversion failed: {e}")
        logger.error(f"LibreOffice stderr: {e.stderr}")
        raise
    except Exception as e:
        logger.error(f"Error converting DOCX to PDF: {e}")
        raise

async def send_pdf_to_channel(pdf_path, caption):
    try:
        with open(pdf_path, 'rb') as pdf_file:
            await bot.send_document(
                chat_id=DEFAULT_CHANNEL,
                document=pdf_file,
                caption=caption
            )
        logger.info(f"PDF sent successfully")
    except TelegramError as e:
        logger.error(f"Failed to send PDF: {e.message}")

async def main():
    collections = fetch_collections('MasterQuestions')
    
    if not collections:
        logger.info("No collections found in the database.")
        return
    
    selected_collection = random.choice(collections)
    num_questions = 3
    questions = fetch_questions_from_collection('MasterQuestions', selected_collection, num_questions)
    
    await send_intro_message(selected_collection, num_questions)
    await asyncio.sleep(5)
    
    for question in questions:
        question_text = question.get('Question', 'No question text')
        options = [str(question.get('Option A', 'No option')), str(question.get('Option B', 'No option')), 
                   str(question.get('Option C', 'No option')), str(question.get('Option D', 'No option'))]
        correct_option_index = get_correct_option_index(question.get('Answer', 'a'))
        explanation = question.get('Explanation', None)
        
        if correct_option_index is not None:
            await send_quiz_to_channel(question_text, options, correct_option_index, explanation)
            await asyncio.sleep(3)
    
    template_io = download_template(TEMPLATE_URL)
    intro_message = (
        f"🎯 *Day {get_quiz_day()}* 🎯\n\n"
        f"📚 *Quiz Collection*: {selected_collection}\n"
        f"🔢 *Number of Questions*: {num_questions}\n\n"
        f"🕐 Daily quizzes are posted at *1 PM* and *9 PM*.\n\n"
        f"🔗 *Join*: @CurrentAdda\n\n"
        f"🏆 Get ready for your quiz! 🚀"
    )
    updated_doc_path = update_document_with_content(template_io, intro_message, questions)
    pdf_path = os.path.join(tempfile.gettempdir(), f'{datetime.now().strftime("%d %B %Y")} Current Affairs.pdf')
    
    convert_docx_to_pdf(updated_doc_path, pdf_path)
    await send_pdf_to_channel(pdf_path, f"Daily Quiz - {datetime.now().strftime('%d %B %Y')}")

if __name__ == "__main__":
    asyncio.run(main())
