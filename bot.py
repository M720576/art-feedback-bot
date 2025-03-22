import logging
import openai
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackContext

# Получаем токены из переменных окружения
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHANNEL_USERNAME = os.environ.get('CHANNEL_USERNAME')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

openai.api_key = OPENAI_API_KEY
logging.basicConfig(level=logging.INFO)

# Стартовая команда
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Привет! Чтобы получить фидбек по иллюстрации, подпишись на {CHANNEL_USERNAME} и нажми /check ✅"
    )

# Проверка подписки на канал
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = context.bot
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["member", "creator", "administrator"]:
            await update.message.reply_text("Отлично! Пришли мне иллюстрацию ✨\nМожешь также добавить подпись к изображению, чтобы я точнее понял, что ты хочешь.")
        else:
            await update.message.reply_text("Похоже, ты ещё не подписан на канал. Подпишись и снова нажми /check")
    except Exception as e:
        await update.message.reply_text("Не удалось проверить подписку. Попробуй позже.")
        print(f"Ошибка при проверке подписки: {e}")

# Обработка изображения
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправь изображение как фото, а не как файл.")
        return

    user_input = update.message.caption or "Это иллюстрация персонажа в мультяшном стиле."

    prompt = (
        "Ты — опытный арт-директор. Пользователь прислал иллюстрацию."
        " Дай доброжелательный, понятный и конструктивный фидбек: что хорошо, а что можно улучшить."
        " Обрати внимание на композицию, цвет, форму, анатомию и выразительность."
    )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_input}
            ],
            max_tokens=600
        )
        feedback = response.choices[0].message.content
        await update.message.reply_text(f"Вот фидбек на твою иллюстрацию:\n\n{feedback}")
    except Exception as e:
        await update.message.reply_text("Произошла ошибка при анализе. Попробуй позже.")
        print(f"Ошибка OpenAI: {e}")

# Обработка ошибок
async def error_handler(update: object, context: CallbackContext) -> None:
    print(f"Произошла ошибка: {context.error}")

# Запуск бота
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_error_handler(error_handler)

    print("Бот запущен...")
    app.run_polling(close_loop=False)
