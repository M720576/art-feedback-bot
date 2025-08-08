import logging
import os
import re
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
from db_pg import (
    init_db,
    add_user,
    get_attempts,
    decrement_attempt,
    reset_attempts,
    count_users,
    count_users_with_zero_attempts,
    count_feedbacks
)

API_TOKEN = os.getenv("BOT_TOKEN")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "-1002092711646"))  # чат для фидбеков и статистики
FREE_LIMIT = int(os.getenv("FREE_LIMIT", 3))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Инициализация базы
init_db()

# Хелпер: определение, фото это или иллюстрация
def is_photo(file_name: str) -> bool:
    ext = file_name.lower().split('.')[-1]
    return ext in ["jpg", "jpeg", "png"]  # простой способ, можно расширить через file.mime_type

# Команда /start
@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    user_id = message.from_user.id
    add_user(user_id)
    await message.answer(
        "🎨 Привет! Я арт-директор с опытом в сто лет, но слегка поехавший.\n"
        "Присылай иллюстрацию, эскиз или скетч, и я дам тебе оценку по 10-бальной шкале.\n"
        f"У тебя есть {FREE_LIMIT} бесплатных попыток!"
    )

# Команда /stats — доступна только владельцу
@dp.message(Command("stats"))
async def send_stats(message: types.Message):
    if message.chat.id != OWNER_CHAT_ID:
        await message.answer("⛔ У тебя нет прав для этой команды.")
        return

    total_users = count_users()
    zero_attempts = count_users_with_zero_attempts()
    total_feedbacks = count_feedbacks()

    stats_text = (
        "📊 Статистика за всё время:\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"❌ Пользователей без попыток: {zero_attempts}\n"
        f"💬 Получено фидбеков: {total_feedbacks}"
    )

    await bot.send_message(OWNER_CHAT_ID, stats_text)

# Обработка изображений
@dp.message(lambda m: m.photo or (m.document and is_photo(m.document.file_name)))
async def handle_image(message: types.Message):
    user_id = message.from_user.id
    attempts = get_attempts(user_id)

    if attempts <= 0:
        await message.answer(
            "⛔ У тебя закончились попытки!\n"
            "Напиши, что понравилось или не понравилось в боте, и я дам ещё 3 бесплатных."
        )
        return

    # Проверка на фото
    if message.photo:
        await message.answer("📷 Это похоже на фото. Я работаю только с иллюстрациями, рисунками и эскизами.")
        return

    decrement_attempt(user_id)

    score = 7  # тут можно внедрить реальную оценку
    review = (
        f"🎯 Оценка: {score}/10\n"
        "1️⃣ Сильные стороны: динамика, интересные детали, смелые решения.\n"
        "2️⃣ Слабые стороны: кое-где каша в композиции.\n"
        "3️⃣ Советы: подчисти линии, добавь воздуха и не бойся убирать лишнее.\n"
        "💥 Общий вердикт: У тебя талант, но давай поднажмём!"
    )

    await message.answer(review)

# Обработка фидбека
@dp.message(lambda m: not m.photo and not m.document and not m.text.startswith("/"))
async def handle_feedback(message: types.Message):
    user_id = message.from_user.id
    attempts = get_attempts(user_id)

    if attempts > 0:
        return  # фидбек только если закончились попытки

    feedback_text = f"📢 Фидбек от {message.from_user.username or message.from_user.id}:\n{message.text}"
    await bot.send_message(OWNER_CHAT_ID, feedback_text)

    reset_attempts(user_id)
    await message.answer("✅ Принял фидбек! Я добавил тебе ещё 3 бесплатных попытки.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
