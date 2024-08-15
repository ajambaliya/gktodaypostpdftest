import os
import pymongo
import asyncio
from telegram import Bot
from telegram.constants import ParseMode
from pymongo import MongoClient
import random
import math
from datetime import datetime, timedelta
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
import pdfkit
import requests
from datetime import datetime, timedelta

# Read environment variables
mongo_uri = os.getenv('MONGO_URI')
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEFAULT_CHANNEL = os.getenv('DEFAULT_CHANNEL')
TEMPLATE_URL = os.getenv('TEMPLATE_URL','https://docs.google.com/document/d/12t9nJzPPHqXbRcH3As4PitcJi9w0SeuD/edit?usp=sharing&ouid=108520131839767724661&rtpof=true&sd=true')

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
    today = datetime.now().date()  # Get today's date
    today_datetime = datetime.combine(today, datetime.min.time())  # Convert date to datetime

    # Find a record for today
    day_record = collection.find_one({'date': today_datetime})
    
    if day_record:
        return day_record['day']
    else:
        last_day_record = collection.find_one(sort=[('date', pymongo.DESCENDING)])
        new_day = 1 if not last_day_record else last_day_record['day'] + 1
        
        # Store today’s date as datetime
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
        print("Intro message sent successfully")
    except Exception as e:
        print(f"Error sending intro message: {e}")

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
        print(f"Quiz sent successfully: {question}")
    except Exception as e:
        print(f"Error sending quiz: {e}")

def fetch_template():
    response = requests.get(TEMPLATE_URL)
    with open("template.docx", "wb") as file:
        file.write(response.content)
    return "template.docx"

def update_document_with_content(doc_path, intro_message, questions):
    doc = Document(doc_path)
    
    # Insert intro message
    for paragraph in doc.paragraphs:
        if '<<START_CONTENT>>' in paragraph.text:
            paragraph.text = intro_message
            paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
            paragraph.style.font.size = Pt(12)
            break
    
    # Insert questions
    for paragraph in doc.paragraphs:
        if '<<END_CONTENT>>' in paragraph.text:
            for q in questions:
                question_paragraph = doc.add_paragraph(f"{q['question']}")
                question_paragraph.style.font.size = Pt(10)
            break
    
    doc.save(doc_path)

def convert_to_pdf(doc_path, pdf_name):
    pdfkit.from_file(doc_path, pdf_name)

async def send_pdf_to_channel(pdf_path, caption):
    try:
        with open(pdf_path, 'rb') as pdf_file:
            await bot.send_document(chat_id=DEFAULT_CHANNEL, document=pdf_file, caption=caption)
        print("PDF sent successfully")
    except Exception as e:
        print(f"Error sending PDF: {e}")

async def main():
    collections = fetch_collections('MasterQuestions')
    
    if not collections:
        print("No collections found in the database.")
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
    
    template_path = fetch_template()
    intro_message = (
        f"🎯 *Day {get_quiz_day()}* 🎯\n\n"
        f"📚 વિષય: *{selected_collection}*\n"
        f"🔢 પ્રશ્નોની સંખ્યા: *{num_questions}*\n"
    )
    
    update_document_with_content(template_path, intro_message, questions)
    
    pdf_count = update_quiz_counter(selected_collection)
    pdf_name = f"{selected_collection} Quiz {pdf_count}.pdf"
    convert_to_pdf(template_path, pdf_name)
    
    caption = f"📄 *{selected_collection} Quiz {pdf_count}* - Day {get_quiz_day()}\n\nJoin our channel for more quizzes! @CurrentAdda"
    await send_pdf_to_channel(pdf_name, caption)

if __name__ == "__main__":
    asyncio.run(main())
