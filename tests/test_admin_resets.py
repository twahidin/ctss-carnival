import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def admin_client(client, session_pool):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('A', '3E1', 5), ('B', '3E1', 0)"
        )
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play, tally) VALUES "
            "('R', '1111', 1, 30)"
        )
    return client


async def test_reset_tokens_sets_balance_to_default(
    admin_client, session_pool
) -> None:
    r = await admin_client.post("/api/admin/reset-tokens")
    assert r.status_code == 200
    async with session_pool.acquire() as conn:
        rows = await conn.fetch("SELECT tokens FROM students")
    assert {r["tokens"] for r in rows} == {20}


async def test_reset_tokens_logs_reset_transaction_per_student(
    admin_client, session_pool
) -> None:
    await admin_client.post("/api/admin/reset-tokens")
    async with session_pool.acquire() as conn:
        tx_types = await conn.fetch("SELECT type FROM transactions")
    assert {r["type"] for r in tx_types} == {"reset"}
    assert len(tx_types) == 2


async def test_reset_tallies_zeros_booths(admin_client, session_pool) -> None:
    r = await admin_client.post("/api/admin/reset-tallies")
    assert r.status_code == 200
    async with session_pool.acquire() as conn:
        tallies = await conn.fetch("SELECT tally FROM booths")
    assert {r["tally"] for r in tallies} == {0}


async def test_reset_tallies_does_not_delete_transactions(
    admin_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students LIMIT 1")
        await conn.execute(
            "INSERT INTO transactions (student_id, amount, type) "
            "VALUES ($1, 1, 'play')",
            sid,
        )
    await admin_client.post("/api/admin/reset-tallies")
    async with session_pool.acquire() as conn:
        c = await conn.fetchval("SELECT COUNT(*) FROM transactions")
    assert c == 1


async def test_full_reset_wipes_students_and_transactions(
    admin_client, session_pool
) -> None:
    r = await admin_client.post("/api/admin/full-reset")
    assert r.status_code == 200
    async with session_pool.acquire() as conn:
        students = await conn.fetchval("SELECT COUNT(*) FROM students")
    tx = 0
    booths = 0
    tally = 0
    async with session_pool.acquire() as conn:
        tx = await conn.fetchval("SELECT COUNT(*) FROM transactions")
        booths = await conn.fetchval("SELECT COUNT(*) FROM booths")
        tally = await conn.fetchval("SELECT COALESCE(SUM(tally), 0) FROM booths")
    assert students == 0
    assert tx == 0
    assert booths == 1  # booths preserved
    assert tally == 0
