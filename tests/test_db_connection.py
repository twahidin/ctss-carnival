import asyncpg
import pytest


@pytest.mark.asyncio
async def test_can_connect_to_postgres(database_url: str) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        version = await conn.fetchval("SELECT version()")
        assert "PostgreSQL" in version
    finally:
        await conn.close()
