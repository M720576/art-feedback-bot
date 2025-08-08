# main.py — бот: лимиты, фидбек (+3), статистика в группу, проверка фото
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
    month_stats
)
from prompts import SYSTEM_PROMPT, USER_PROMPT
from utils import downscale

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FREE_LIMIT = int(os.getenv("FREE_LIMIT", "3"))
OWNER_ID = int(os.getenv("OWNER_ID", "151541823"))
FEEDBACK_GROUP_ID = int(os.getenv("FEEDBACK_GROUP_ID", "-1002092711646"))

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Не заданы TELEGRAM_BOT_TOKEN или OPENAI_API_KEY в переменных окружения.")

bot = Bot(TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
client = OpenAI(api_key=OPENAI_API_KEY)

WELCOME_TEXT = (
    "Привет! Я Арт-feedback БОТ.\n"
    f"Пришли мне изображение — дам разбор. На старте у тебя {FREE_LIMIT} бесплатных запросов."
)

@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(WELCOME_TEXT)

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
                    {"type": "text", "text": USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        max_tokens=700,
        temperature=0.5,
    )
    reply = completion.choices[0].message.content or ""
    return reply.strip()

# ====== ОБРАБОТКА: ФОТО (говорим, что работаем только с иллюстрациями) ======
@dp.message(F.photo)
async def handle_photo(m: Message):
    await m.answer(
        "Это фото. Я анализирую только рисованные материалы: иллюстрации, рисунки, эскизы, скетчи. "
        "Пришли файл как документ (image/*) — так сохранится качество."
    )

# ====== ОБРАБОТКА: ДОКУМЕНТ-КАРТИНКА (анализ) ======
@dp.message(F.document)
async def handle_image_doc(m: Message):
    user_id = m.from_user.id

    # Проверяем лимит
    used = await get_count(user_id)
    if used >= FREE_LIMIT:
        if await already_sent_feedback_this_month(user_id):
            await m.answer(
                "Лимит исчерпан на этот месяц. Ты уже отправлял фидбек и получил +3. "
                "Дальше — платные лимиты."
            )
        else:
            await m.answer(
                "Лимит исчерпан. Хочешь ещё +3 бесплатных в этом месяце? "
                "Отправь:\n\n/feedback Что понравилось/не понравилось в боте и что улучшить"
            )
        return

    # Проверяем, что документ — картинка
    if not (m.document and m.document.mime_type and m.document.mime_type.startswith("image/")):
        await m.answer("Пришли, пожалуйста, картинку (image/*) как документ.")
        return

    try:
        tg_file = await bot.get_file(m.document.file_id)
        file_stream = await bot.download_file(tg_file.file_path)
        raw = file_stream.read()

        prepared = downscale(raw, max_side=1536)
        await m.answer("Принял! Секунду, анализирую…")

        reply = await analyze_image_with_gpt(prepared)

        # Увеличиваем счётчик и показываем остаток
        new_count = await inc_count(user_id)
        left = max(FREE_LIMIT - new_count, 0)
        await m.answer(f"{reply}\n\nОсталось бесплатных запросов: {left}")

    except Exception as e:
        await m.answer("Упс, что-то пошло не так при обработке изображения. Попробуй ещё раз или пришли другую картинку.")
        print("ERROR:", e)

# ====== ФИДБЕК (+3 попытки 1 раз/мес), отправляем в группу ======
@dp.message(Command("feedback"))
async def feedback(m: Message):
    user_id = m.from_user.id
    text = m.text or ""
    parts = text.split(" ", 1)
    payload = parts[1].strip() if len(parts) > 1 else ""

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

    # Отправляем фидбек в группу
    uname = m.from_user.username
    header = f"📝 Отзыв от @{uname} (id {user_id}):" if uname else f"📝 Отзыв от id {user_id}:"
    try:
        await bot.send_message(FEEDBACK_GROUP_ID, f"{header}\n\n{payload}")
    except Exception as e:
        print("ERROR sending feedback to group:", e)

    # Сохраняем и выдаём бонус (+3)
    try:
        await save_feedback_and_grant_bonus(user_id, payload)
        await m.answer("Спасибо! За фидбек накинул ещё 3 бесплатных попытки на этот месяц.")
    except Exception as e:
        await m.answer("Принял фидбек, но не получилось обновить счётчик. Попробуй позже.")
        print("ERROR saving feedback:", e)

# ====== /stats — в группу фидбеков, запускать может только владелец ======
@dp.message(Command("stats"))
async def stats(m: Message):
    if m.from_user.id != OWNER_ID:
        await m.answer("Команда доступна только владельцу.")
        return

    try:
        users_total, users_hit_limit, total_requests, feedback_count = await month_stats()
    except TypeError:
        # На случай старой сигнатуры
        users_total, users_hit_limit, total_requests, feedback_count = await month_stats(FREE_LIMIT)

    stats_msg = (
        "📊 Статистика за текущий месяц:\n"
        f"• Уникальных пользователей: {users_total}\n"
        f"• Дошли до лимита ({FREE_LIMIT}): {users_hit_limit}\n"
        f"• Всего запросов: {total_requests}\n"
        f"• Отправили отзыв: {feedback_count}"
    )

    try:
        await bot.send_message(FEEDBACK_GROUP_ID, stats_msg)
    except Exception as e:
        await m.answer("Не удалось отправить статистику в группу. Проверьте FEEDBACK_GROUP_ID.")
        print("ERROR sending stats to group:", e)
        return

    if m.chat.id != FEEDBACK_GROUP_ID:
        await m.answer("Готово. Статистика отправлена в группу фидбеков.")

# ====== RUN ======
async def main():
    await init_db()
    print("Artdir feedback bot is up and running.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
