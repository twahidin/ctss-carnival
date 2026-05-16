import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def teacher_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('Alice', '3E1', 10), ('Bob', '3E1', 10), ('Carl', '3N1', 10)"
        )
    await client.post("/api/teacher/login", json={"password": "test-teacher-pw"})
    return client


async def test_absent_single(teacher_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
    r = await teacher_client.post(
        "/api/teacher/absent", json={"student_id": sid, "is_absent": True}
    )
    assert r.status_code == 200
    async with session_pool.acquire() as conn:
        absent = await conn.fetchval(
            "SELECT is_absent FROM students WHERE id = $1", sid
        )
    assert absent is True


async def test_absent_bulk(teacher_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        ids = [r["id"] for r in await conn.fetch("SELECT id FROM students")]
    r = await teacher_client.post(
        "/api/teacher/absent-bulk",
        json={"student_ids": ids, "is_absent": True},
    )
    assert r.status_code == 200
    assert r.json()["affected"] == 3


async def test_students_by_class_groups_correctly(teacher_client) -> None:
    r = await teacher_client.get("/api/teacher/students-by-class")
    assert r.status_code == 200
    body = r.json()
    by_class = {entry["class"]: entry["students"] for entry in body}
    assert sorted(s["name"] for s in by_class["3E1"]) == ["Alice", "Bob"]
    assert [s["name"] for s in by_class["3N1"]] == ["Carl"]
