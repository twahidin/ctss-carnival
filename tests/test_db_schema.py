import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_init_schema_creates_all_tables(session_pool) -> None:
    async with session_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        names = {r["tablename"] for r in rows}
    assert {"students", "booths", "transactions", "settings"} <= names


async def test_init_schema_is_idempotent(session_pool) -> None:
    from db import init_schema
    await init_schema(session_pool)  # second call, must not raise
    async with session_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM students")
    assert count == 0


async def test_booths_have_owning_class_column(session_pool) -> None:
    async with session_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT column_name, is_nullable, column_default, data_type
            FROM information_schema.columns
            WHERE table_name = 'booths' AND column_name = 'owning_class'
            """
        )
    assert row is not None, "booths.owning_class column missing"
    assert row["is_nullable"] == "NO"
    assert row["column_default"] == "''::text"
    assert row["data_type"] == "text"
