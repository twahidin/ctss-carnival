import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def booth_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) VALUES ('R', '1234', 1)"
        )
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('A1', '3E1', 10), ('A2', '3E1', 10), ('A3', '3E1', 10), "
            "('B1', '3N2', 10), ('B2', '3N2', 10)"
        )
    await client.post("/api/booth/login", json={"code": "1234"})
    return client


async def test_recent_returns_last_5_plays_only(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sids = [r["id"] for r in await conn.fetch("SELECT id FROM students ORDER BY id")]
    for sid in sids:
        await booth_client.post("/api/booth/pay", json={"student_id": sid, "amount": 1})
    r = await booth_client.get("/api/booth/recent")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 5
    assert items[0]["student_name"] == "B2"  # most recent first
    assert "transaction_id" in items[0]


async def test_recent_excludes_undone(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students LIMIT 1")
    pr = await booth_client.post("/api/booth/pay", json={"student_id": sid, "amount": 1})
    await booth_client.post(
        "/api/booth/undo", json={"transaction_id": pr.json()["transaction_id"]}
    )
    r = await booth_client.get("/api/booth/recent")
    assert r.json() == []


async def test_stats_returns_tally_and_class_breakdown(
    booth_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id FROM students ORDER BY id")
    for row in rows[:3]:  # 3 from 3E1
        await booth_client.post("/api/booth/pay", json={"student_id": row["id"], "amount": 1})
    for row in rows[3:]:  # 2 from 3N2
        await booth_client.post("/api/booth/pay", json={"student_id": row["id"], "amount": 1})
    r = await booth_client.get("/api/booth/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["tally"] == 5
    assert body["tx_count"] == 5
    breakdown = {row["class"]: row["count"] for row in body["by_class"]}
    assert breakdown == {"3E1": 3, "3N2": 2}
