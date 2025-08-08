# main.py
# Главный файл: телеграм-бот принимает картинку, зовёт ИИ и отвечает пользователю.

import os
import asyncio
import base64

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart, Command

from openai import OpenAI

from db import init_db, get_count, inc_count, reset_count
from prompts import SYSTEM_PROMPT, USER_PROMPT
from utils import downscale

# Загружаем переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FREE_LIMIT = int(os.getenv("FREE_LIMIT", "3"))

# chat_id твоей группы
FEEDBACK_GROUP_ID = -1002092711646

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Не заданы TELEGRAM_BOT_TOKEN или OPENAI_API_KEY в переменных окружения.")

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
client = OpenAI(api_key=OPENAI_API_KEY)

WELCOME_TEXT = (
    "Привет! Я Арт-feedback БОТ.
"
    "Пришли мне изображение — дам разбор. На старте у тебя {FREE_LIMIT} бесплатных запросов." 
)

@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(WELCOME_TEXT)

def bytes_to_data_url(jpeg_bytes: bytes) -> str:
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"

async def analyze_image_with_gpt(image_bytes: bytes) -> str:
    data_url = bytes_to_data_url(image_bytes)

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        max_tokens=600,
        temperature=0.4,
    )
    reply = completion.choices[0].message.content or ""
    return reply.strip()

@dp.message(F.photo | F.document)
async def handle_image(m: Message):
    user_id = m.from_user.id

    used = await get_count(user_id)
    if used >= FREE_LIMIT:
        await m.answer(
            "Лимит бесплатных запросов исчерпан. Напиши сюда, что тебе понравилось или не понравилось, и получи ещё 3 запроса."
        )
        return

    file_id = None
    if m.photo:
        file_id = m.photo[-1].file_id
    elif m.document and m.document.mime_type and m.document.mime_type.startswith("image/"):
        file_id = m.document.file_id
    else:
        await m.answer("Пришли, пожалуйста, фото или картинку.")
        return

    try:
        tg_file = await bot.get_file(file_id)
        file_stream = await bot.download_file(tg_file.file_path)
        raw = file_stream.read()

        prepared = downscale(raw, max_side=1536)

        await m.answer("Принял! Секунду, анализирую…")

        reply = await analyze_image_with_gpt(prepared)

        new_count = await inc_count(user_id)
        left = max(FREE_LIMIT - new_count, 0)

        await m.answer(f"{reply}

Осталось бесплатных запросов: {left}")

    except Exception as e:
        await m.answer("Упс, что-то пошло не так. Попробуй ещё раз.")
        print("ERROR:", e)

# Фидбек — любое сообщение, когда лимит исчерпан
@dp.message(F.text)
async def handle_feedback(m: Message):
    user_id = m.from_user.id
    used = await get_count(user_id)

    if used >= FREE_LIMIT:
        feedback_text = f"Фидбек от @{m.from_user.username or m.from_user.full_name} (id {user_id}):
{m.text}"
        
        # Отправляем в группу
        await bot.send_message(FEEDBACK_GROUP_ID, feedback_text)

        # Сбрасываем счётчик
        await reset_count(user_id)
        await m.answer("Спасибо за фидбек! Тебе добавлены ещё 3 бесплатных запроса.")
    else:
        await m.answer("Пришли картинку для анализа или используй команду /start.")

async def main():
    await init_db()
    print("Artdir feedback bot is running.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
