# db_pg.py — лимиты + фидбек + статистика (PostgreSQL)
import os
import asyncpg
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL")
_pool = None  # пул соединений

def current_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")

async def init_db():
    """Создаём пул и таблицы (если нет)."""
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан. Проверь Variables в Railway.")

    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with _pool.acquire() as conn:
        # основная таблица по лимитам
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            user_id BIGINT NOT NULL,
            month   TEXT   NOT NULL,
            count   INTEGER NOT NULL,
            PRIMARY KEY (user_id, month)
        );
        """)

        # таблица фидбека (для +3 попыток и твоей статистики)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id         BIGSERIAL PRIMARY KEY,
            user_id    BIGINT NOT NULL,
            month      TEXT   NOT NULL,
            text       TEXT   NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)

async def get_count(user_id: int) -> int:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT count FROM usage WHERE user_id=$1 AND month=$2",
            user_id, current_month()
        )
        return int(row["count"]) if row else 0

async def inc_count(user_id: int) -> int:
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

# --- фидбек + бонусы ---
async def already_sent_feedback_this_month(user_id: int) -> bool:
    m = current_month()
    async with _pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM feedback WHERE user_id=$1 AND month=$2 LIMIT 1",
            user_id, m
        )
        return bool(exists)

async def save_feedback_and_grant_bonus(user_id: int, text: str, free_limit: int) -> None:
    """Сохраняет фидбек и выдаёт +3 попытки один раз в месяц."""
    m = current_month()
    async with _pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT 1 FROM feedback WHERE user_id=$1 AND month=$2 LIMIT 1",
                user_id, m
            )
            if not exists:
                await conn.execute(
                    "INSERT INTO feedback(user_id, month, text) VALUES ($1, $2, $3)",
                    user_id, m, text
                )
                row = await conn.fetchrow(
                    "SELECT count FROM usage WHERE user_id=$1 AND month=$2",
                    user_id, m
                )
                if row:
                    new_count = max(int(row["count"]) - free_limit, 0)
                    await conn.execute(
                        "UPDATE usage SET count=$1 WHERE user_id=$2 AND month=$3",
                        new_count, user_id, m
                    )
                else:
                    await conn.execute(
                        "INSERT INTO usage(user_id, month, count) VALUES ($1, $2, 0)",
                        user_id, m
                    )

# --- статистика ---
async def month_stats(free_limit: int):
    """Возвращает (users_total, users_hit_limit, total_requests, feedback_count) за текущий месяц."""
    m = current_month()
    async with _pool.acquire() as conn:
        users_total = await conn.fetchval(
            "SELECT COUNT(*) FROM (SELECT DISTINCT user_id FROM usage WHERE month=$1) t",
            m
        ) or 0
        users_hit_limit = await conn.fetchval(
            "SELECT COUNT(*) FROM usage WHERE month=$1 AND count >= $2",
            m, free_limit
        ) or 0
        total_requests = await conn.fetchval(
            "SELECT COALESCE(SUM(count),0) FROM usage WHERE month=$1",
            m
        ) or 0
        feedback_count = await conn.fetchval(
            "SELECT COUNT(*) FROM feedback WHERE month=$1",
            m
        ) or 0
    return int(users_total), int(users_hit_limit), int(total_requests), int(feedback_count)
