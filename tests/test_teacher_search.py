import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def teacher_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('Alice', '3E1', 10), ('Bob', '3E2', 5)"
        )
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('R', '1234', 1)"
        )
    await client.post("/api/teacher/login", json={"password": "test-teacher-pw"})
    return client


async def test_teacher_lists_students(teacher_client) -> None:
    r = await teacher_client.get("/api/teacher/students")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert names == {"Alice", "Bob"}


async def test_teacher_student_detail_includes_history(
    teacher_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
        bid = await conn.fetchval("SELECT id FROM booths LIMIT 1")
        await conn.execute(
            "INSERT INTO transactions (student_id, booth_id, amount, type) "
            "VALUES ($1, $2, 1, 'play')",
            sid, bid,
        )
    r = await teacher_client.get(f"/api/teacher/student/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Alice"
    assert body["tokens"] == 10
    assert len(body["history"]) == 1
    assert body["history"][0]["type"] == "play"


async def test_teacher_endpoints_require_teacher_or_admin(client) -> None:
    r = await client.get("/api/teacher/students")
    assert r.status_code == 401
