import asyncio

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_concurrent_pays_serialized_by_for_update(
    session_pool, monkeypatch
) -> None:
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('R', '1234', 1)"
        )
        sid = await conn.fetchval(
            "INSERT INTO students (name, class, tokens) "
            "VALUES ('Alice', '3E1', 1) RETURNING id"
        )

    from app import app
    from auth import bootstrap_passwords
    app.state.pool = session_pool
    await bootstrap_passwords(session_pool)

    from httpx import ASGITransport, AsyncClient

    async def one_pay() -> int:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://test") as client:
            await client.post("/api/booth/login", json={"code": "1234"})
            r = await client.post("/api/booth/pay", json={"student_id": sid})
            return r.status_code

    statuses = await asyncio.gather(*[one_pay() for _ in range(20)])
    successes = sum(1 for s in statuses if s == 200)
    failures = sum(1 for s in statuses if s == 409)
    assert successes == 1, f"Expected exactly 1 success, got {successes}: {statuses}"
    assert failures == 19

    async with session_pool.acquire() as conn:
        tokens = await conn.fetchval("SELECT tokens FROM students WHERE id = $1", sid)
        tx_count = await conn.fetchval(
            "SELECT COUNT(*) FROM transactions WHERE type = 'play'"
        )
    assert tokens == 0
    assert tx_count == 1
