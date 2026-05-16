import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def booth_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) VALUES ('R', '1234', 2)"
        )
        await conn.execute(
            "INSERT INTO students (name, class, tokens, is_absent) VALUES "
            "('Alice', '3E1', 10, FALSE), "
            "('Bob',   '3E2',  1, FALSE), "
            "('Carl',  '3N1',  5, TRUE)"
        )
    await client.post("/api/booth/login", json={"code": "1234"})
    return client


async def test_pay_deducts_tokens_and_increments_tally(
    booth_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
    r = await booth_client.post("/api/booth/pay", json={"student_id": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["new_balance"] == 8
    assert body["transaction_id"]
    async with session_pool.acquire() as conn:
        tokens = await conn.fetchval("SELECT tokens FROM students WHERE id = $1", sid)
        tally = await conn.fetchval("SELECT tally FROM booths WHERE code = '1234'")
        tx = await conn.fetchrow("SELECT type, amount FROM transactions WHERE id = $1", body["transaction_id"])
    assert tokens == 8
    assert tally == 2
    assert tx["type"] == "play"
    assert tx["amount"] == 2


async def test_pay_insufficient_tokens_rejected(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Bob'")
    r = await booth_client.post("/api/booth/pay", json={"student_id": sid})
    assert r.status_code == 409
    body = r.json()
    msg = body.get("error") or body.get("detail", {}).get("error") or str(body.get("detail", ""))
    assert "insufficient" in msg.lower()


async def test_pay_absent_rejected(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Carl'")
    r = await booth_client.post("/api/booth/pay", json={"student_id": sid})
    assert r.status_code == 409
    body = r.json()
    msg = body.get("error") or body.get("detail", {}).get("error") or str(body.get("detail", ""))
    assert "absent" in msg.lower()


async def test_pay_unknown_student(booth_client) -> None:
    r = await booth_client.post("/api/booth/pay", json={"student_id": 99999})
    assert r.status_code == 404
