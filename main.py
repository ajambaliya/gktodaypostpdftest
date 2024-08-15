import os
import pymongo
import asyncio
from telegram import Bot
from telegram.constants import ParseMode
from pymongo import MongoClient
import random
import math
from docx import Document
from datetime import datetime
import tempfile
import subprocess

# Environment variables
mongo_uri = os.getenv('MONGO_URI')
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEFAULT_CHANNEL = os.getenv('DEFAULT_CHANNEL')

# Initialize MongoDB client and Telegram bot
client = MongoClient(mongo_uri)
bot = Bot(token=BOT_TOKEN)

# MongoDB Databases
questions_db = client['MasterQuestions']
days_db = client['QuizDays']

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

async def send_intro_message(collection_name, num_questions, quiz_day):
    intro_message = (
        f"🎯 *આજની કવિઝ ({quiz_day})* 🎯\n\n"
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

def generate_pdf(collection_name, questions, quiz_day):
    doc = Document()
    doc.add_heading(f'{quiz_day} - {collection_name} Quiz', level=1)
    
    for i, question in enumerate(questions):
        question_text = question.get('Question', 'No question text')
        options = [
            question.get('Option A', 'No option'), 
            question.get('Option B', 'No option'), 
            question.get('Option C', 'No option'), 
            question.get('Option D', 'No option')
        ]
        correct_option = question.get('Answer', 'a').upper()
        doc.add_heading(f'Q{i+1}: {question_text}', level=2)
        for option in options:
            doc.add_paragraph(option, style='List Bullet')
        doc.add_paragraph(f'Answer: {correct_option}', style='Intense Quote')
        doc.add_paragraph('')

    doc.add_paragraph("Join our Telegram channel for daily quizzes: [@CurrentAdda](https://telegram.me/currentadda)", style='Intense Quote')
    
    with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp_docx:
        doc.save(tmp_docx.name)
        return tmp_docx.name

def convert_docx_to_pdf(docx_path, pdf_path):
    subprocess.run(['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', 
                    os.path.dirname(pdf_path), docx_path], check=True)
    os.rename(docx_path.replace('.docx', '.pdf'), pdf_path)

async def send_pdf_to_telegram(pdf_path, quiz_day):
    caption = f"📄 {quiz_day} Quiz PDF\n\nJoin us on Telegram: [@CurrentAdda](https://telegram.me/currentadda)"
    
    for _ in range(3):
        try:
            with open(pdf_path, 'rb') as pdf_file:
                await bot.send_document(chat_id=DEFAULT_CHANNEL, document=pdf_file, caption=caption, parse_mode=ParseMode.MARKDOWN)
            break
        except Exception as e:
            print(f"Error sending PDF: {e}")
            await asyncio.sleep(5)

async def main():
    quiz_day = datetime.now().strftime('%A, %d %B %Y')
    days_collection = days_db['QuizDays']
    if days_collection.find_one({'day': quiz_day}):
        print(f"Quiz already sent for {quiz_day}. Exiting.")
        return
    
    days_collection.insert_one({'day': quiz_day})
    
    collections = fetch_collections('MasterQuestions')
    if not collections:
        print("No collections found in the database.")
        return
    
    selected_collection = random.choice(collections)
    num_questions = 10
    questions = fetch_questions_from_collection('MasterQuestions', selected_collection, num_questions)
    
    await send_intro_message(selected_collection, num_questions, quiz_day)
    await asyncio.sleep(5)
    
    for question in questions:
        question_text = question.get('Question', 'No question text')
        options = [question.get('Option A', 'No option'), question.get('Option B', 'No option'), 
                   question.get('Option C', 'No option'), question.get('Option D', 'No option')]
        correct_option_index = get_correct_option_index(question.get('Answer', 'a'))
        explanation = question.get('Explanation', None)
        
        if correct_option_index is not None:
            await send_quiz_to_channel(question_text, options, correct_option_index, explanation)
            await asyncio.sleep(3)
    
    pdf_path = generate_pdf(selected_collection, questions, quiz_day)
    await send_pdf_to_telegram(pdf_path, quiz_day)

if __name__ == "__main__":
    asyncio.run(main())
