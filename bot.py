import os
import logging
import base64
import aiohttp
from openai import OpenAI
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, CallbackContext, filters
)

# Загружаем переменные окружения
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHANNEL_USERNAME = os.environ.get('CHANNEL_USERNAME')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# Настройка клиента OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Привет! Чтобы получить фидбек по иллюстрации, подпишись на {CHANNEL_USERNAME} и нажми /check ✅"
    )

# Команда /check — проверка подписки
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = context.bot
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["member", "creator", "administrator"]:
            await update.message.reply_text(
                "Отлично! Пришли мне иллюстрацию ✨\n"
                "Можешь также добавить подпись к изображению, чтобы я точнее понял, что ты хочешь."
            )
        else:
            await update.message.reply_text("Похоже, ты ещё не подписан на канал. Подпишись и снова нажми /check")
    except Exception:
        await update.message.reply_text("Не удалось проверить подписку. Попробуй позже.")
        logging.error("Ошибка при проверке подписки:", exc_info=True)

# Обработка изображения и анализ через GPT-4 Turbo (Vision)
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправь изображение как фото, а не как файл.")
        return

    user_input = update.message.caption or "Это иллюстрация персонажа в мультяшном стиле."

    prompt = (
        "Ты — опытный арт-директор. Пользователь прислал иллюстрацию. "
        "Дай доброжелательный, понятный и конструктивный фидбек: что хорошо, а что можно улучшить. "
        "Обрати внимание на композицию, цвет, форму, анатомию и выразительность."
    )

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_url = file.file_path

    # Скачиваем изображение и кодируем в base64
    async with aiohttp.ClientSession() as session:
        async with session.get(file_url) as resp:
            image_bytes = await resp.read()
            base64_image = base64.b64encode(image_bytes).decode("utf-8")

    await update.message.reply_text("Анализирую твою иллюстрацию... 🧠")

    try:
        response = client.chat.completions.create(
            model="gpt-4-turbo",  # актуальная модель
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt + "\n\n" + user_input},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000,
        )
        feedback = response.choices[0].message.content
        await update.message.reply_text(f"🎨 Вот фидбек на твою иллюстрацию:\n\n{feedback}")
    except Exception as e:
        await update.message.reply_text("Произошла ошибка при анализе. Попробуй позже.")
        logging.error("Ошибка при обращении к GPT-4 Turbo:", exc_info=True)

# Обработка ошибок
async def error_handler(update: object, context: CallbackContext) -> None:
    logging.error(f"Произошла ошибка: {context.error}", exc_info=True)

# Запуск бота
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_error_handler(error_handler)

    logging.info("Бот запущен...")
    app.run_polling(close_loop=False)
