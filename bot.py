import logging
import openai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# Получи эти значения заранее и вставь сюда
import os
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

    prompt = (
        "Ты — арт-директор. Пользователь прислал иллюстрацию."
        " Дай конструктивную рецензию: композиция, цвет, выразительность персонажа."
        " Будь доброжелателен, пиши понятно."
    )

    fake_input = "Это иллюстрация персонажа в мультяшном стиле."

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": fake_input}
        ]
    )
    feedback = response.choices[0].message.content
    await update.message.reply_text(f"Вот фидбек на твою работу:\n\n{feedback}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    print("Бот запущен...")
    app.run_polling()