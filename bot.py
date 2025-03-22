import logging
import openai
import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackContext
from telegram.error import TelegramError

# Получи эти значения заранее и вставь сюда
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHANNEL_USERNAME = os.environ.get('CHANNEL_USERNAME')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

openai.api_key = OPENAI_API_KEY
logging.basicConfig(level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Привет! Чтобы получить фидбек по иллюстрации, подпишись на {CHANNEL_USERNAME} и нажми /check ✅"
    )

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = context.bot
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["member", "creator", "administrator"]:
            await update.message.reply_text("Отлично! Пришли мне иллюстрацию ✨")
        else:
            await update.message.reply_text("Пока не вижу подписки. Подпишись на канал и снова нажми /check")
    except:
        await update.message.reply_text("Не удалось проверить подписку. Попробуй позже.")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправь изображение.")
        return

    photo = update.message.photo[-1]
    photo_file = await photo.get_file()
    file_url = photo_file.file_path

    prompt = "Проанализируй иллюстрацию и дай доброжелательную рецензию по композиции, цвету и выразительности персонажа."

    # Отправка изображения в Vision-модель GPT-4
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-vision-preview",
            messages=[
                {"role": "system", "content": "Ты — опытный арт-директор, который даёт художникам подробный фидбек."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": file_url}}
                    ]
                }
            ],
            max_tokens=500
        )
        feedback = response.choices[0].message.content
        await update.message.reply_text(f"Вот фидбек на твою иллюстрацию:\n\n{feedback}")
    except Exception as e:
        await update.message.reply_text("Произошла ошибка при анализе изображения. Попробуй позже.")
        print(f"Ошибка OpenAI Vision: {e}")

async def error_handler(update: object, context: CallbackContext) -> None:
    print(f"Произошла ошибка: {context.error}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_error_handler(error_handler)

    print("Бот запущен...")
    app.run_polling()
