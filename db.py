import asyncpg

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    class TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    is_absent BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_students_name ON students (LOWER(name));
CREATE INDEX IF NOT EXISTS idx_students_class ON students (class);

CREATE TABLE IF NOT EXISTS booths (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    code VARCHAR(10) UNIQUE NOT NULL,
    cost_per_play INTEGER NOT NULL DEFAULT 1 CHECK (cost_per_play > 0),
    tally INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    booth_id INTEGER REFERENCES booths(id) ON DELETE SET NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    type VARCHAR(20) NOT NULL,
    note TEXT,
    reversed_by INTEGER REFERENCES transactions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tx_student ON transactions(student_id);
CREATE INDEX IF NOT EXISTS idx_tx_booth ON transactions(booth_id);
CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


async def init_pool(database_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(database_url, min_size=2, max_size=10)


async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()


async def init_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def truncate_all(pool: asyncpg.Pool) -> None:
    """Test helper: wipe data, preserve schema."""
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE students, booths, transactions, settings "
            "RESTART IDENTITY CASCADE"
        )
