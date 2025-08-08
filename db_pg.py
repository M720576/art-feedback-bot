# db_pg.py — Postgres: лимиты, фидбек, статистика (месячная)
import os
import asyncpg
from datetime import datetime
from typing import Optional, Tuple

DATABASE_URL = os.getenv("DATABASE_URL")
pool: Optional[asyncpg.Pool] = None  # глобальный пул

# ---- утилиты ----

def current_month() -> str:
    """YYYY-MM по UTC."""
    return datetime.utcnow().strftime("%Y-%m")

# ---- инициализация ----

async def init_db() -> None:
    """Создаёт пул и таблицы (если ещё нет)."""
    global pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан. Проверь переменные окружения.")

    if pool is None:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as conn:
        # Таблица учёта количества запросов на пользователя в пределах месяца
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS public."usage" (
            user_id BIGINT NOT NULL,
            month   TEXT   NOT NULL,
            count   INTEGER NOT NULL,
            PRIMARY KEY (user_id, month)
        );
        """)

        # Таблица пользовательских отзывов (фидбек для +3 попыток)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS public."feedback" (
            id         BIGSERIAL PRIMARY KEY,
            user_id    BIGINT NOT NULL,
            month      TEXT   NOT NULL,
            text       TEXT   NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)

# ---- функции лимитов/фидбека ----

async def get_count(user_id: int) -> int:
    """Сколько запросов сделал пользователь в текущем месяце."""
    if pool is None:
        raise RuntimeError("DB pool is not initialized. Call init_db() first.")
    m = current_month()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT "count" FROM public."usage" WHERE user_id=$1 AND month=$2',
            user_id, m
        )
        return int(row["count"]) if row else 0

async def inc_count(user_id: int) -> int:
    """Увеличивает счётчик на 1 и возвращает новое значение."""
    if pool is None:
        raise RuntimeError("DB pool is not initialized. Call init_db() first.")
    m = current_month()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT "count" FROM public."usage" WHERE user_id=$1 AND month=$2',
            user_id, m
        )
        if row:
            new_count = int(row["count"]) + 1
            await conn.execute(
                'UPDATE public."usage" SET "count"=$1 WHERE user_id=$2 AND month=$3',
                new_count, user_id, m
            )
        else:
            new_count = 1
            await conn.execute(
                'INSERT INTO public."usage"(user_id, month, "count") VALUES ($1, $2, $3)',
                user_id, m, new_count
            )
        return new_count

async def already_sent_feedback_this_month(user_id: int) -> bool:
    """
    Проверка: отправлял ли фидбек в этом месяце (для выдачи бонуса только 1 раз/мес).
    """
    if pool is None:
        raise RuntimeError("DB pool is not initialized. Call init_db() first.")
    m = current_month()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            'SELECT 1 FROM public."feedback" WHERE user_id=$1 AND month=$2 LIMIT 1',
            user_id, m
        )
        return bool(exists)

async def save_feedback_and_grant_bonus(
    user_id: int,
    text: str,
    free_limit: Optional[int] = None
) -> None:
    """
    Сохраняет фидбек и «выдаёт +free_limit» (по факту — уменьшает счётчик на free_limit),
    но только 1 раз в текущем месяце.
    """
    if pool is None:
        raise RuntimeError("DB pool is not initialized. Call init_db() first.")

    if free_limit is None:
        try:
            free_limit = int(os.getenv("FREE_LIMIT", "3"))
        except Exception:
            free_limit = 3

    m = current_month()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # если уже был фидбек в этом месяце — бонус не выдаём
            exists = await conn.fetchval(
                'SELECT 1 FROM public."feedback" WHERE user_id=$1 AND month=$2 LIMIT 1',
                user_id, m
            )
            if exists:
                return

            # сохраняем сам фидбек
            await conn.execute(
                'INSERT INTO public."feedback"(user_id, month, text) VALUES ($1, $2, $3)',
                user_id, m, text
            )

            # уменьшаем текущий счётчик на free_limit (минимум 0)
            row = await conn.fetchrow(
                'SELECT "count" FROM public."usage" WHERE user_id=$1 AND month=$2',
                user_id, m
            )
            if row:
                new_count = max(int(row["count"]) - free_limit, 0)
                await conn.execute(
                    'UPDATE public."usage" SET "count"=$1 WHERE user_id=$2 AND month=$3',
                    new_count, user_id, m
                )
            else:
                # если записей не было, просто создадим с 0
                await conn.execute(
                    'INSERT INTO public."usage"(user_id, month, "count") VALUES ($1, $2, 0)',
                    user_id, m
                )

# ---- статистика ----

async def month_stats(free_limit: Optional[int] = None) -> Tuple[int, int, int, int]:
    """
    Возвращает (users_total, users_hit_limit, total_requests, feedback_count) за текущий месяц.
    users_hit_limit — пользователи, у кого count >= FREE_LIMIT.
    """
    if pool is None:
        raise RuntimeError("DB pool is not initialized. Call init_db() first.")

    if free_limit is None:
        try:
            free_limit = int(os.getenv("FREE_LIMIT", "3"))
        except Exception:
            free_limit = 3

    m = current_month()
    async with pool.acquire() as conn:
        users_total = await conn.fetchval(
            'SELECT COUNT(DISTINCT user_id) FROM public."usage" WHERE month=$1',
            m
        ) or 0
        users_hit_limit = await conn.fetchval(
            'SELECT COUNT(*) FROM public."usage" WHERE month=$1 AND "count" >= $2',
            m, free_limit
        ) or 0
        total_requests = await conn.fetchval(
            'SELECT COALESCE(SUM("count"), 0) FROM public."usage" WHERE month=$1',
            m
        ) or 0
        feedback_count = await conn.fetchval(
            'SELECT COUNT(*) FROM public."feedback" WHERE month=$1',
            m
        ) or 0
    return int(users_total), int(users_hit_limit), int(total_requests), int(feedback_count)

# ---- сбросы ----

async def reset_all_limits() -> None:
    """Сбрасывает счётчики запросов для всех пользователей в текущем месяце (только usage.count)."""
    if pool is None:
        raise RuntimeError("DB pool is not initialized. Call init_db() first.")
    m = current_month()
    async with pool.acquire() as conn:
        await conn.execute(
            'UPDATE public."usage" SET "count" = 0 WHERE month = $1',
            m
        )

async def reset_bot() -> None:
    """
    Полный сброс бота: очищает все основные таблицы (usage, feedback).
    ВНИМАНИЕ: это необратимо. История и счётчики будут потеряны.
    """
    if pool is None:
        raise RuntimeError("DB pool is not initialized. Call init_db() first.")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Экраннирование нужно: "usage" — зарезервированное слово
            await conn.execute('TRUNCATE TABLE public."usage" RESTART IDENTITY CASCADE;')
            await conn.execute('TRUNCATE TABLE public."feedback" RESTART IDENTITY CASCADE;')
            # Если у тебя есть другие таблицы — добавь их сюда аналогично.
