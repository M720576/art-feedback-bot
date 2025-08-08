# db.py
# Простая БД на SQLite для учёта бесплатных запросов по месяцам

import aiosqlite
from datetime import datetime

DB_PATH = "bot.db"

def current_month() -> str:
    """Возвращает текущий месяц в формате YYYY-MM (например, '2025-08')."""
    # Для простоты используем UTC — этого достаточно.
    return datetime.utcnow().strftime("%Y-%m")

async def init_db():
    """Создаёт таблицу, если её ещё нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            user_id INTEGER,
            month   TEXT,
            count   INTEGER,
            PRIMARY KEY (user_id, month)
        );
        """)
        await db.commit()

async def get_count(user_id: int) -> int:
    """Сколько запросов сделал пользователь в этом месяце."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT count FROM usage WHERE user_id = ? AND month = ?",
            (user_id, current_month())
        )
        row = await cur.fetchone()
        return row[0] if row else 0

async def inc_count(user_id: int) -> int:
    """
    Увеличивает счётчик на 1 и возвращает новое значение.
    Если записи ещё нет — создаёт её с 1.
    """
    m = current_month()
    async with aiosqlite.connect(DB_PATH) as db:
        # читаем текущее значение
        cur = await db.execute(
            "SELECT count FROM usage WHERE user_id = ? AND month = ?",
            (user_id, m)
        )
        row = await cur.fetchone()
        if row:
            new_count = row[0] + 1
            await db.execute(
                "UPDATE usage SET count = ? WHERE user_id = ? AND month = ?",
                (new_count, user_id, m)
            )
        else:
            new_count = 1
            await db.execute(
                "INSERT INTO usage(user_id, month, count) VALUES (?,?,?)",
                (user_id, m, new_count)
            )
        await db.commit()
        return new_count
