# main.py
# Главный файл: телеграм-бот принимает картинку, зовёт ИИ и отвечает пользователю.

import os
import asyncio
import base64

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart, Command

from openai import OpenAI

from db_pg import (
    init_db,
    get_count,
    inc_count,
    save_feedback_and_grant_bonus,
    already_sent_feedback_this_month,
    month_stats,
)
from prompts import SYSTEM_PROMPT, USER_PROMPT
from utils import downscale

# Доп. инструкция: даже если это фото — анализ всё равно делаем
EXTRA_INSTRUCTION = (
    "Важно: если изображение окажется фотографией, всё равно выполни краткий анализ по тем же пунктам, "
    "как для иллюстрации. В начале коротко предупреди, что это фото, и продолжи.\n"
)

# Загружаем переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FREE_LIMIT = int(os.getenv("FREE_LIMIT", "3"))

# Твой Telegram ID (владелец бота)
OWNER_ID = 151541823

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Не заданы TELEGRAM_BOT_TOKEN или OPENAI_API_KEY в переменных окружения.")

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
client = OpenAI(api_key=OPENAI_API_KEY)

WELCOME_TEXT = (
    "Привет! Я Арт-feedback БОТ.\n"
    "Пришли мне изображение — дам короткий, по делу разбор: композиция, ритмы, цвет/свет, стилизация, эмоции, уместность для ЦА.\n\n"
    f"На старте у тебя {FREE_LIMIT} бесплатных запросов в месяц.\n"
    "Совет: загружай картинку хорошего качества, без сильной компрессии."
)

@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(WELCOME_TEXT)

@dp.message(Command("stats"))
async def stats(m: Message):
    if m.from_user.id != OWNER_ID:
        await m.answer("Команда доступна только владельцу.")
        return
    # month_stats может принимать free_limit или брать из ENV — поддержим оба варианта
    try:
        users_total, users_hit_limit, total_requests, feedback_count = await month_stats(FREE_LIMIT)
    except TypeError:
        users_total, users_hit_limit, total_requests, feedback_count = await month_stats()
    await m.answer(
        "📊 Статистика за текущий месяц:\n"
        f"• Уникальных пользователей: {users_total}\n"
        f"• Дошли до лимита (использовали {FREE_LIMIT}): {users_hit_limit}\n"
        f"• Всего запросов: {total_requests}\n"
        f"• Отправили отзыв: {feedback_count}"
    )

@dp.message(Command("feedback"))
async def feedback(m: Message):
    user_id = m.from_user.id
    text = m.text or ""
    parts = text.split(" ", 1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    if user_id == OWNER_ID:
        await m.answer("Эта команда для пользователей, чтобы прислать тебе отзыв :)")
        return

    used = await get_count(user_id)
    if used < FREE_LIMIT:
        await m.answer("У тебя ещё есть бесплатные запросы — доиспользуй их, а потом приходи за +3 🙂")
        return

    if await already_sent_feedback_this_month(user_id):
        await m.answer("Ты уже присылал отзыв в этом месяце и получил +3. Спасибо!")
        return

    if not payload:
        await m.answer("Напиши так:\n/feedback Что понравилось/не понравилось и что улучшить.")
        return

    # Перешлём отзыв владельцу (как просил — фидбек не трогаем)
    try:
        await bot.send_message(OWNER_ID, f"📝 Отзыв от @{m.from_user.username or user_id} (id {user_id}):\n\n{payload}")
    except Exception:
        await bot.send_message(OWNER_ID, f"📝 Отзыв от id {user_id}:\n\n{payload}")

    # Сохраним и дадим бонус
    await save_feedback_and_grant_bonus(user_id, payload, FREE_LIMIT)
    await m.answer("Принял! Спасибо за отзыв — накинул тебе ещё 3 бесплатных попытки в этом месяце. Жду новую иллюстрацию.")

def bytes_to_data_url(jpeg_bytes: bytes) -> str:
    """Кодирует байты JPEG в data:URL для передачи в GPT-4o."""
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"

async def analyze_image_with_gpt(image_bytes: bytes) -> str:
    """Отправляет картинку + инструкцию в GPT-4o и возвращает текст ответа."""
    data_url = bytes_to_data_url(image_bytes)

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRA_INSTRUCTION + USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        max_tokens=700,     # чутка больше места под разбор
        temperature=0.5,    # немного живее стиль, но без рандома
    )
    reply = completion.choices[0].message.content or ""
    return reply.strip()

@dp.message(F.photo | F.document)
async def handle_image(m: Message):
    """Обрабатываем присланные изображения (фото/док) — анализируем всё."""
    user_id = m.from_user.id

    # проверяем лимит
    used = await get_count(user_id)
    if used >= FREE_LIMIT:
        if await already_sent_feedback_this_month(user_id):
            await m.answer(
                "Лимит исчерпан на этот месяц. Ты уже отправлял фидбек и получил +3. "
                "Дальше — платные лимиты. Напиши автору, если хочешь Pro/Unlimited."
            )
        else:
            await m.answer(
                "Лимит исчерпан. Хочешь ещё +3 бесплатных в этом месяце? "
                "Отправь команду:\n\n"
                "/feedback Что понравилось/не понравилось в боте и что улучшить"
            )
        return

    # вытаскиваем файл
    file_id = None
    if m.photo:
        file_id = m.photo[-1].file_id
    elif m.document and m.document.mime_type and m.document.mime_type.startswith("image/"):
        file_id = m.document.file_id
    else:
        await m.answer("Пришли, пожалуйста, фото или картинку (image/*).")
        return

    try:
        tg_file = await bot.get_file(file_id)
        file_stream = await bot.download_file(tg_file.file_path)
        raw = file_stream.read()

        prepared = downscale(raw, max_side=1536)
        await m.answer("Принял! Секунду, анализирую твою работу… 🤔")

        reply = await analyze_image_with_gpt(prepared)

        # увеличиваем счётчик и сообщаем остаток
        new_count = await inc_count(user_id)
        left = max(FREE_LIMIT - new_count, 0)
        await m.answer(f"{reply}\n\nОсталось бесплатных запросов в этом месяце: {left}")

    except Exception as e:
        await m.answer("Упс, что-то пошло не так при обработке изображения. Попробуй ещё раз или пришли другую картинку.")
        print("ERROR:", e)

async def main():
    await init_db()
    print("Artdir feedback bot is up and running.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
