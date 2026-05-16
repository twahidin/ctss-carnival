"""Seed local dev DB with sample booths. Idempotent."""
import asyncio
import os

from db import init_pool, init_schema, close_pool

SAMPLE_BOOTHS = [
    ("Ring Toss", "1111", 1),
    ("Darts",     "2222", 2),
    ("Lucky Dip", "3333", 1),
]


async def main() -> None:
    pool = await init_pool(os.environ["DATABASE_URL"])
    await init_schema(pool)
    async with pool.acquire() as conn:
        for name, code, cost in SAMPLE_BOOTHS:
            await conn.execute(
                """
                INSERT INTO booths (name, code, cost_per_play)
                VALUES ($1, $2, $3)
                ON CONFLICT (code) DO NOTHING
                """,
                name, code, cost,
            )
    await close_pool(pool)
    print(f"Seeded {len(SAMPLE_BOOTHS)} booths.")


if __name__ == "__main__":
    asyncio.run(main())
