import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def admin_client(client, session_pool):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval(
            "INSERT INTO students (name, class, tokens) "
            "VALUES ('Alice', '3E1', 10) RETURNING id"
        )
        bid = await conn.fetchval(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('Ring Toss', '1234', 1) RETURNING id"
        )
        await conn.execute(
            "INSERT INTO transactions (student_id, booth_id, amount, type) "
            "VALUES ($1, $2, 1, 'play')",
            sid, bid,
        )
    return client


async def test_export_returns_csv_with_expected_columns(admin_client) -> None:
    r = await admin_client.get("/api/admin/export-transactions")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.text
    header = text.splitlines()[0]
    assert header == "id,created_at,student_name,class,booth_name,type,amount,note"
    assert "Alice" in text
    assert "Ring Toss" in text
