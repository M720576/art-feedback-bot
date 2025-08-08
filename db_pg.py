# db_pg.py — храним лимиты в PostgreSQL через asyncpg (данные не пропадают при перезапуске)

import os
import asyncpg
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL")
_pool = None  # пул соединений

def current_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")

async def init_db():
    """Создаём пул и таблицу (если нет)."""
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан. Проверь Variables в Railway.")

    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with _pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            user_id BIGINT NOT NULL,
            month   TEXT   NOT NULL,
            count   INTEGER NOT NULL,
            PRIMARY KEY (user_id, month)
        );
        """)

async def get_count(user_id: int) -> int:
    """Сколько запросов сделал пользователь в этом месяце."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT count FROM usage WHERE user_id=$1 AND month=$2",
            user_id, current_month()
        )
        return int(row["count"]) if row else 0

async def inc_count(user_id: int) -> int:
    """Увеличивает счётчик на 1 и возвращает новое значение."""
    m = current_month()
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT count FROM usage WHERE user_id=$1 AND month=$2",
            user_id, m
        )
        if row:
            new_count = int(row["count"]) + 1
            await conn.execute(
                "UPDATE usage SET count=$1 WHERE user_id=$2 AND month=$3",
                new_count, user_id, m
            )
        else:
            new_count = 1
            await conn.execute(
                "INSERT INTO usage(user_id, month, count) VALUES ($1, $2, $3)",
                user_id, m, new_count
            )
        return new_count
