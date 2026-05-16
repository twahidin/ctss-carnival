import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def admin_client(client):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    return client


CSV_BASIC = b"name,class\nJohn Tan,3E1\nMary Lim,3E1\nAhmad bin Ali,3N2\n"


async def test_preview_returns_token_and_counts(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("roster.csv", CSV_BASIC, "text/csv")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["row_count"] == 3
    # by_class should be a sorted list of [class, count] pairs
    by_class = sorted([list(p) for p in body["by_class"]])
    assert by_class == [["3E1", 2], ["3N2", 1]]
    assert body["token"]
    assert len(body["sample"]) == 3


async def test_confirm_inserts_students(admin_client) -> None:
    pr = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("roster.csv", CSV_BASIC, "text/csv")},
    )
    token = pr.json()["token"]
    r = await admin_client.post(
        "/api/admin/upload-csv/confirm", json={"token": token}
    )
    assert r.status_code == 200
    assert r.json()["inserted"] == 3


async def test_confirm_uses_default_tokens(admin_client, session_pool) -> None:
    await admin_client.post(
        "/api/admin/settings", json={"default_tokens": 25}
    )
    pr = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("roster.csv", CSV_BASIC, "text/csv")},
    )
    await admin_client.post(
        "/api/admin/upload-csv/confirm", json={"token": pr.json()["token"]}
    )
    async with session_pool.acquire() as conn:
        tokens = await conn.fetchval("SELECT tokens FROM students LIMIT 1")
    assert tokens == 25


async def test_token_is_single_use(admin_client) -> None:
    pr = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("roster.csv", CSV_BASIC, "text/csv")},
    )
    token = pr.json()["token"]
    await admin_client.post("/api/admin/upload-csv/confirm", json={"token": token})
    r = await admin_client.post("/api/admin/upload-csv/confirm", json={"token": token})
    assert r.status_code == 400


async def test_blocked_when_transactions_exist(admin_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        student_id = await conn.fetchval(
            "INSERT INTO students (name, class, tokens) "
            "VALUES ('Test', '3E1', 10) RETURNING id"
        )
        await conn.execute(
            "INSERT INTO transactions (student_id, amount, type) VALUES ($1, 1, 'play')",
            student_id,
        )
    r = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("roster.csv", CSV_BASIC, "text/csv")},
    )
    assert r.status_code == 409
    body = r.json()
    # FastAPI puts the message under "detail" by default; accept either shape.
    msg = body.get("error") or body.get("detail") or ""
    assert "transactions" in str(msg).lower()


async def test_rejects_bad_csv(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("bad.csv", b"foo,bar\n1,2\n", "text/csv")},
    )
    assert r.status_code == 400
