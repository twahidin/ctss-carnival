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
