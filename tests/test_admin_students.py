import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def admin_client(client):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    return client


async def test_list_students_empty(admin_client) -> None:
    r = await admin_client.get("/api/admin/students")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_students_returns_ordered_by_name(
    admin_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens, is_absent) VALUES "
            "('Zara', '3E1', 15, FALSE), "
            "('Adele', '3E2', 12, FALSE), "
            "('Bob', '3N1', 5, TRUE)"
        )
    r = await admin_client.get("/api/admin/students")
    assert r.status_code == 200
    body = r.json()
    assert [s["name"] for s in body] == ["Adele", "Bob", "Zara"]
    adele = body[0]
    assert set(adele.keys()) == {"id", "name", "class", "tokens", "is_absent"}
    assert adele["class"] == "3E2"
    assert adele["tokens"] == 12
    assert adele["is_absent"] is False


async def test_list_students_requires_admin(client) -> None:
    r = await client.get("/api/admin/students")
    assert r.status_code in (401, 403)


async def test_create_student_inserts_with_default_tokens(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/students", json={"name": "Newcomer Lee", "class": "2A1"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Newcomer Lee"
    assert body["class"] == "2A1"
    assert body["tokens"] == 20  # DEFAULT_TOKENS fallback
    assert body["is_absent"] is False
    assert isinstance(body["id"], int)


async def test_create_student_appears_in_list_without_wiping_others(
    admin_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES ('Existing', '2B1', 7)"
        )
    r = await admin_client.post(
        "/api/admin/students", json={"name": "Added", "class": "2B2"}
    )
    assert r.status_code == 200
    listed = (await admin_client.get("/api/admin/students")).json()
    assert sorted(s["name"] for s in listed) == ["Added", "Existing"]
    existing = next(s for s in listed if s["name"] == "Existing")
    assert existing["tokens"] == 7  # untouched


async def test_create_student_strips_whitespace(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/students", json={"name": "  Trim Me  ", "class": "  2C1 "}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Trim Me"
    assert r.json()["class"] == "2C1"


async def test_create_student_rejects_blank_fields(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/students", json={"name": "   ", "class": "2A1"}
    )
    assert r.status_code == 400


async def test_create_student_requires_admin(client) -> None:
    r = await client.post(
        "/api/admin/students", json={"name": "Nope", "class": "2A1"}
    )
    assert r.status_code in (401, 403)
