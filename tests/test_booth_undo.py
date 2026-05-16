import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def booth_setup(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('R', '1234', 2)"
        )
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('B', '5678', 1)"
        )
        sid = await conn.fetchval(
            "INSERT INTO students (name, class, tokens) "
            "VALUES ('Alice', '3E1', 10) RETURNING id"
        )
    await client.post("/api/booth/login", json={"code": "1234"})
    pr = await client.post("/api/booth/pay", json={"student_id": sid})
    return client, sid, pr.json()["transaction_id"]


async def test_undo_within_60s(booth_setup, session_pool) -> None:
    client, sid, tx_id = booth_setup
    r = await client.post("/api/booth/undo", json={"transaction_id": tx_id})
    assert r.status_code == 200
    async with session_pool.acquire() as conn:
        tokens = await conn.fetchval(
            "SELECT tokens FROM students WHERE id = $1", sid
        )
        tally = await conn.fetchval("SELECT tally FROM booths WHERE code = '1234'")
        reversed_by = await conn.fetchval(
            "SELECT reversed_by FROM transactions WHERE id = $1", tx_id
        )
        undo_row = await conn.fetchrow(
            "SELECT type, amount FROM transactions WHERE id = $1", reversed_by
        )
    assert tokens == 10
    assert tally == 0
    assert undo_row["type"] == "undo"
    assert undo_row["amount"] == 2


async def test_undo_after_60s_rejected(booth_setup, session_pool) -> None:
    client, _, tx_id = booth_setup
    async with session_pool.acquire() as conn:
        await conn.execute(
            "UPDATE transactions SET created_at = NOW() - INTERVAL '61 seconds' "
            "WHERE id = $1",
            tx_id,
        )
    r = await client.post("/api/booth/undo", json={"transaction_id": tx_id})
    assert r.status_code == 409


async def test_undo_twice_rejected(booth_setup) -> None:
    client, _, tx_id = booth_setup
    await client.post("/api/booth/undo", json={"transaction_id": tx_id})
    r = await client.post("/api/booth/undo", json={"transaction_id": tx_id})
    assert r.status_code == 409


async def test_undo_wrong_booth_rejected(booth_setup) -> None:
    client, _, tx_id = booth_setup
    await client.post("/api/booth/logout")
    await client.post("/api/booth/login", json={"code": "5678"})
    r = await client.post("/api/booth/undo", json={"transaction_id": tx_id})
    assert r.status_code == 403


async def test_undo_unknown_tx(booth_setup) -> None:
    client, _, _ = booth_setup
    r = await client.post("/api/booth/undo", json={"transaction_id": 99999})
    assert r.status_code == 404
