# main.py
# Телеграм-бот: принимает картинку, зовёт ИИ и отвечает пользователю.
# Обновлено: добавлена админ-команда /reset_all с двойным подтверждением,
# отдельная /reset_limits, бонусы за отзыв; отзыв можно отправлять просто текстом.

import os
import asyncio
import base64
import secrets
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart, Command

from openai import OpenAI
from db_pg import (
    reset_bot,
    init_db,
    get_count,
    inc_count,
    save_feedback_and_grant_bonus,
    already_sent_feedback_this_month,
    month_stats,
    reset_all_limits,
)
from prompts import SYSTEM_PROMPT, USER_PROMPT
from utils import downscale

# Логгер
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FREE_LIMIT = int(os.getenv("FREE_LIMIT", "3"))
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
FEEDBACK_GROUP_ID = int(os.getenv("FEEDBACK_GROUP_ID", "0"))

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Не заданы TELEGRAM_BOT_TOKEN или OPENAI_API_KEY.")

bot = Bot(TELEGRAM_BOT_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)
dp = Dispatcher()

EXTRA_INSTRUCTION = (
    "Важно: если изображение окажется фотографией, всё равно выполни краткий анализ по тем же пунктам, "
    "как для иллюстрации. В начале коротко предупреди, что это фото, и продолжи.\n"
)

WELCOME_TEXT = (
    "Привет! Я Арт-feedback БОТ.\n"
    "Пришли мне изображение — дам короткий, по делу разбор: композиция, ритмы, цвет/свет, стилизация, эмоции, уместность для ЦА.\n\n"
    f"На старте у тебя {FREE_LIMIT} бесплатных запросов в месяц.\n"
    "Совет: загружай картинку хорошего качества, без сильной компрессии."
)

# ===== Хелперы =====

def bytes_to_data_url(jpeg_bytes: bytes) -> str:
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"

async def analyze_image_with_gpt(image_bytes: bytes) -> str:
    data_url = bytes_to_data_url(image_bytes)
    def _call_openai():
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
            max_tokens=600,
            temperature=0.4,
        )
        return completion.choices[0].message.content or ""

    reply = await asyncio.to_thread(_call_openai)
    return reply.strip()

# ===== Команды =====

@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(WELCOME_TEXT)

@dp.message(Command("stats"))
async def stats(m: Message):
    if m.from_user.id != OWNER_ID:
        return await m.answer("Команда доступна только владельцу.")
    users_total, users_hit_limit, total_requests, feedback_count = await month_stats(FREE_LIMIT)
    await m.answer(
        "📊 Статистика за текущий месяц:\n"
        f"• Уникальных пользователей: {users_total}\n"
        f"• Дошли до лимита ({FREE_LIMIT}): {users_hit_limit}\n"
        f"• Всего запросов: {total_requests}\n"
        f"• Отправили отзыв: {feedback_count}"
    )

@dp.message(Command("reset_limits"))
async def reset_limits_cmd(m: Message):
    if m.from_user.id != OWNER_ID:
        return await m.answer("Команда доступна только владельцу.")
    await reset_all_limits()
    await m.answer("✅ Лимиты для всех пользователей на текущий месяц сброшены.")

# /reset_all с подтверждением
_pending_reset_code: str | None = None

@dp.message(Command("reset_all"))
async def reset_all_cmd(m: Message):
    global _pending_reset_code
    if m.from_user.id != OWNER_ID:
        return await m.answer("Команда доступна только владельцу.")

    parts = (m.text or "").strip().split()

    if len(parts) == 1:
        _pending_reset_code = secrets.token_hex(4)
        await m.answer(
            "⚠️ Полный сброс ВСЕХ данных (лимиты, история, отзывы). Это необратимо.\n"
            f"Чтобы подтвердить, отправь:\n/reset_all CONFIRM {_pending_reset_code}"
        )
        return

    if len(parts) == 3 and parts[1].upper() == "CONFIRM":
        if not _pending_reset_code or parts[2] != _pending_reset_code:
            return await m.answer("Код подтверждения не совпал или устарел.")
        _pending_reset_code = None
        try:
            await reset_bot()
            await m.answer("✅ Готово. Все данные обнулены.")
        except Exception as e:
            await m.answer(f"❌ Ошибка: {e}")
        return

    await m.answer("Неверный формат. Используй: /reset_all или /reset_all CONFIRM <code>.")

@dp.message(Command("feedback"))
async def feedback(m: Message):
    user_id = m.from_user.id
    text = m.text or ""
    parts = text.split(" ", 1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    if user_id == OWNER_ID:
        return await m.answer("Эта команда для пользователей.")

    used = await get_count(user_id)
    if used < FREE_LIMIT:
        return await m.answer("У тебя ещё есть бесплатные запросы. Используй их сначала 🙂")

    if await already_sent_feedback_this_month(user_id):
        return await m.answer("Ты уже присылал отзыв в этом месяце и получил +3. Спасибо!")

    if not payload:
        return await m.answer("Напиши так:\n/feedback Твой отзыв.")

    try:
        await bot.send_message(
            FEEDBACK_GROUP_ID,
            f"📝 Отзыв от @{m.from_user.username or user_id} (id {user_id}):\n\n{payload}",
        )
    except Exception:
        await bot.send_message(OWNER_ID, f"📝 Отзыв от id {user_id}:\n\n{payload}")

    await save_feedback_and_grant_bonus(user_id, payload, FREE_LIMIT)
    await m.answer("Спасибо за отзыв! Накинул ещё 3 бесплатных попытки.")

# Текстовые отзывы без команды
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_feedback(m: Message):
    user_id = m.from_user.id
    text = (m.text or "").strip()
    if not text:
        return

    used = await get_count(user_id)
    if used < FREE_LIMIT or await already_sent_feedback_this_month(user_id):
        return

    try:
        await bot.send_message(
            FEEDBACK_GROUP_ID,
            f"📝 Отзыв от @{m.from_user.username or user_id} (id {user_id}):\n\n{text}",
        )
    except Exception:
        await bot.send_message(OWNER_ID, f"📝 Отзыв от id {user_id}:\n\n{text}")

    await save_feedback_and_grant_bonus(user_id, text, FREE_LIMIT)
    await m.answer("Спасибо за отзыв! Накинул ещё 3 бесплатных попытки.")

# Обработка изображений
@dp.message(F.photo | F.document)
async def handle_image(m: Message):
    user_id = m.from_user.id
    used = await get_count(user_id)
    if used >= FREE_LIMIT:
        if await already_sent_feedback_this_month(user_id):
            await m.answer("Лимит исчерпан. Ты уже получал +3 за отзыв.")
        else:
            await m.answer(
                "Лимит исчерпан. Хочешь +3? Пришли короткий отзыв — что понравилось/не понравилось."
            )
        return

    file_id = m.photo[-1].file_id if m.photo else (
        m.document.file_id if m.document and str(m.document.mime_type).startswith("image/") else None
    )
    if not file_id:
        return await m.answer("Пришли фото или картинку (image/*).")

    try:
        tg_file = await bot.get_file(file_id)
        file_stream = await bot.download_file(tg_file.file_path)
        raw = file_stream.read()
        prepared = downscale(raw, max_side=1536)

        await m.answer("Принял! Секунду, анализирую… 🤔")
        reply = await analyze_image_with_gpt(prepared)
        new_count = await inc_count(user_id)
        left = max(FREE_LIMIT - new_count, 0)
        await m.answer(f"{reply}\n\nОсталось бесплатных запросов: {left}")
    except Exception as e:
        await m.answer("Упс, что-то пошло не так. Попробуй ещё раз.")
        log.error("Image handling error: %s", e)

# ===== Точка входа =====

async def main():
    await init_db()
    log.info(
        "Bot is up. OWNER_ID=%s FEEDBACK_GROUP_ID=%s FREE_LIMIT=%s",
        OWNER_ID, FEEDBACK_GROUP_ID, FREE_LIMIT
    )
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
