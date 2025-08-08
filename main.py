# main.py
# Телеграм-бот: принимает картинку, зовёт ИИ и отвечает пользователю.
# Обновлено: добавлена админ-команда /reset_all с двойным подтверждением,
# отдельная /reset_limits, бонусы за отзыв; теперь отзыв можно отправлять просто текстом (без /feedback).

import os
import asyncio
import base64
import secrets

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

EXTRA_INSTRUCTION = (
    "Важно: если изображение окажется фотографией, всё равно выполни краткий анализ по тем же пунктам, "
    "как для иллюстрации. В начале коротко предупреди, что это фото, и продолжи.\n"
)

# Переменные окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FREE_LIMIT = int(os.getenv("FREE_LIMIT", "3"))
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # Телеграм ID владельца

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
    # Синхронный клиент OpenAI — вызываем в отдельном потоке, чтобы не блокировать event loop
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

# /reset_all с двойным подтверждением
_pending_reset_code: str | None = None

@dp.message(Command("reset_all"))
async def reset_all_cmd(m: Message):
    global _pending_reset_code
    if m.from_user.id != OWNER_ID:
        await m.answer("Команда доступна только владельцу.")
        return

    parts = (m.text or "").strip().split()

    # Шаг 1 — выдать код подтверждения
    if len(parts) == 1:
        _pending_reset_code = secrets.token_hex(4)
        await m.answer(
            "⚠️ Полный сброс ВСЕХ данных (лимиты, история, отзывы). Это необратимо.\n"
            "Чтобы подтвердить, отправь:\n"
            f"/reset_all CONFIRM {_pending_reset_code}",
            parse_mode="Markdown",
        )
        return

    # Шаг 2 — подтверждение
    if len(parts) == 3 and parts[1].upper() == "CONFIRM":
        code = parts[2]
        if not _pending_reset_code or code != _pending_reset_code:
            await m.answer("Код подтверждения не совпал или устарел.")
            return
        _pending_reset_code = None
        try:
            await reset_bot()
            await m.answer("✅ Готово. Все данные обнулены.")
        except Exception as e:
            await m.answer(f"❌ Ошибка при сбросе: {e}")
        return

    await m.answer(
        "Неверный формат. Используй: /reset_all или /reset_all CONFIRM <code>.",
        parse_mode="Markdown",
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

    try:
        await bot.send_message(
            OWNER_ID,
            f"📝 Отзыв от @{m.from_user.username or user_id} (id {user_id}):\n\n{payload}",
        )
    except Exception:
        await bot.send_message(OWNER_ID, f"📝 Отзыв от id {user_id}:\n\n{payload}")

    await save_feedback_and_grant_bonus(user_id, payload, FREE_LIMIT)
    await m.answer(
        "Принял! Спасибо за отзыв — накинул тебе ещё 3 бесплатных попытки в этом месяце. Жду новую иллюстрацию."
    )

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

    # Есть ещё бесплатные запросы — ничего не делаем
    used = await get_count(user_id)
    if used < FREE_LIMIT:
        return

    # Отзыв уже присылал в этом месяце — ничего не делаем
    if await already_sent_feedback_this_month(user_id):
        return

    # Сохраняем отзыв и выдаём бонус
    try:
        await bot.send_message(
            OWNER_ID,
            f"📝 Отзыв от @{m.from_user.username or user_id} (id {user_id}):\n\n{text}",
        )
    except Exception:
        await bot.send_message(OWNER_ID, f"📝 Отзыв от id {user_id}:\n\n{text}")

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
        await m.answer(
            "Упс, что-то пошло не так при обработке изображения. Попробуй ещё раз или пришли другую картинку."
        )
        print("ERROR:", e)

# ===== Точка входа =====

async def main():
    await init_db()
    print("Artdir feedback bot is up and running.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
