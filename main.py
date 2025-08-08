# main.py
# Телеграм-бот: принимает картинку, зовёт ИИ и отвечает пользователю.
# Версия: упрощённый /reset_all (без подтверждения) + логирование ID, текстовый фидбек без команды.

import os
import asyncio
import base64
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

# ---- Логирование ----
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("art-feedback-bot")

EXTRA_INSTRUCTION = (
    "Важно: если изображение окажется фотографией, всё равно выполни краткий анализ по тем же пунктам, "
    "как для иллюстрации. В начале коротко предупреди, что это фото, и продолжи.\n"
)

# ---- Переменные окружения ----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def _to_int(env_name: str, default: int) -> int:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        log.warning("ENV %s=%r не удалось распарсить как int. Использую %d", env_name, raw, default)
        return default

FREE_LIMIT = _to_int("FREE_LIMIT", 3)
OWNER_ID   = _to_int("OWNER_ID", 0)

# Куда слать отзывы: группа/канал или владелец. Можно не задавать.
_FEEDBACK_GID = os.getenv("FEEDBACK_GROUP_ID")
FEEDBACK_GROUP_ID = None
if _FEEDBACK_GID:
    try:
        FEEDBACK_GROUP_ID = int(_FEEDBACK_GID.strip())
    except Exception:
        log.warning("ENV FEEDBACK_GROUP_ID=%r не int. Отправка отзывов пойдёт владельцу.", _FEEDBACK_GID)

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
    raise RuntimeError("Не заданы TELEGRAM_BOT_TOKEN или OPENAI_API_KEY в переменных окружения.")

bot = Bot(TELEGRAM_BOT_TOKEN)
client = OpenAI(api_key=OPENAI_API_KEY)
dp = Dispatcher()

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

async def _send_feedback_to_owner_or_group(text: str) -> None:
    """Пытаемся слать в группу, если задана; иначе — владельцу."""
    if FEEDBACK_GROUP_ID:
        try:
            await bot.send_message(FEEDBACK_GROUP_ID, text)
            return
        except Exception as e:
            log.warning("Не удалось отправить в FEEDBACK_GROUP_ID=%s: %s. Пошлю владельцу.", FEEDBACK_GROUP_ID, e)
    await bot.send_message(OWNER_ID, text)

# ===== Команды =====

@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(WELCOME_TEXT)

@dp.message(Command("stats"))
async def stats(m: Message):
    if m.from_user.id != OWNER_ID:
        await m.answer("Команда доступна только владельцу.")
        return
    users_total, users_hit_limit, total_requests, feedback_count = await month_stats(FREE_LIMIT)
    await m.answer(
        "📊 Статистика за текущий месяц:\n"
        f"• Уникальных пользователей: {users_total}\n"
        f"• Дошли до лимита (использовали {FREE_LIMIT}): {users_hit_limit}\n"
        f"• Всего запросов: {total_requests}\n"
        f"• Отправили отзыв: {feedback_count}"
    )

@dp.message(Command("reset_limits"))
async def reset_limits_cmd(m: Message):
    if m.from_user.id != OWNER_ID:
        await m.answer("Команда доступна только владельцу.")
        return
    await reset_all_limits()
    await m.answer("✅ Лимиты для всех пользователей на текущий месяц сброшены.")

# /reset_all — упрощённо, без подтверждения (для надёжной отладки)
@dp.message(Command("reset_all"))
async def reset_all_cmd(m: Message):
    log.info("DEBUG /reset_all: from_user.id=%s, OWNER_ID=%s", m.from_user.id, OWNER_ID)
    if m.from_user.id != OWNER_ID:
        await m.answer("Команда доступна только владельцу.")
        return
    try:
        await reset_bot()
        await m.answer("✅ Полный сброс выполнен: лимиты, история и отзывы очищены.")
        log.info("DEBUG reset_bot(): успешно выполнен.")
    except Exception as e:
        await m.answer(f"❌ Ошибка при сбросе: {e}")
        log.exception("ERROR reset_bot(): %s", e)

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

    # Отправляем отзыв во внешний чат/владельцу
    header = f"📝 Отзыв от @{m.from_user.username or user_id} (id {user_id}):\n\n{payload}"
    await _send_feedback_to_owner_or_group(header)

    await save_feedback_and_grant_bonus(user_id, payload, FREE_LIMIT)
    await m.answer("Принял! Спасибо за отзыв — накинул тебе ещё 3 бесплатных попытки в этом месяце. Жду новую иллюстрацию.")

# ===== Текстовые отзывы без команды =====
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_feedback(m: Message):
    """
    Пользователь может отправить отзыв обычным текстом, без /feedback,
    если у него закончились бесплатные попытки и он ещё не присылал отзыв в этом месяце.
    """
    user_id = m.from_user.id
    text = (m.text or "").strip()
    if not text:
        return

    used = await get_count(user_id)
    if used < FREE_LIMIT:
        return

    if await already_sent_feedback_this_month(user_id):
        return

    header = f"📝 Отзыв от @{m.from_user.username or user_id} (id {user_id}):\n\n{text}"
    await _send_feedback_to_owner_or_group(header)

    await save_feedback_and_grant_bonus(user_id, text, FREE_LIMIT)
    await m.answer("Спасибо за отзыв! Я накинул тебе ещё 3 бесплатных попытки. Жду новую иллюстрацию.")

# ===== Обработка изображений =====
@dp.message(F.photo | F.document)
async def handle_image(m: Message):
    user_id = m.from_user.id

    # Лимиты и бонусы
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
                "Просто отправь короткий текстовый отзыв: что понравилось/не понравилось в боте и что улучшить — и я накину тебе ещё +3 попытки."
            )
        return

    # Выделяем file_id
    file_id = None
    if m.photo:
        file_id = m.photo[-1].file_id
    elif m.document and m.document.mime_type and str(m.document.mime_type).startswith("image/"):
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
        new_count = await inc_count(user_id)
        left = max(FREE_LIMIT - new_count, 0)
        await m.answer(f"{reply}\n\nОсталось бесплатных запросов в этом месяце: {left}")

    except Exception as e:
        await m.answer("Упс, что-то пошло не так при обработке изображения. Попробуй ещё раз или пришли другую картинку.")
        log.exception("ERROR handle_image: %s", e)

# ===== Точка входа =====

async def main():
    await init_db()
    log.info("Artdir feedback bot is up and running. OWNER_ID=%s FEEDBACK_GROUP_ID=%s FREE_LIMIT=%s",
             OWNER_ID, FEEDBACK_GROUP_ID, FREE_LIMIT)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
