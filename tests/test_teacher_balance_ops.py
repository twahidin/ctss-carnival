import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def teacher_client_with_student(client, session_pool):
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval(
            "INSERT INTO students (name, class, tokens) "
            "VALUES ('Alice', '3E1', 10) RETURNING id"
        )
    await client.post("/api/teacher/login", json={"password": "test-teacher-pw"})
    return client, sid


async def test_refund_adds_tokens_and_logs(
    teacher_client_with_student, session_pool
) -> None:
    client, sid = teacher_client_with_student
    r = await client.post(
        "/api/teacher/refund",
        json={"student_id": sid, "amount": 3, "note": "Ride broke"},
    )
    assert r.status_code == 200
    assert r.json()["new_balance"] == 13
    async with session_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT type, amount, note FROM transactions WHERE student_id = $1",
            sid,
        )
    assert row["type"] == "refund"
    assert row["amount"] == 3
    assert row["note"] == "Ride broke"


async def test_deduct_subtracts_tokens(teacher_client_with_student) -> None:
    client, sid = teacher_client_with_student
    r = await client.post(
        "/api/teacher/deduct",
        json={"student_id": sid, "amount": 2, "note": "Missed pay catch"},
    )
    assert r.status_code == 200
    assert r.json()["new_balance"] == 8


async def test_refund_requires_note(teacher_client_with_student) -> None:
    client, sid = teacher_client_with_student
    r = await client.post(
        "/api/teacher/refund",
        json={"student_id": sid, "amount": 3, "note": "ab"},
    )
    assert r.status_code == 422  # pydantic min_length


async def test_refund_amount_must_be_positive(teacher_client_with_student) -> None:
    client, sid = teacher_client_with_student
    r = await client.post(
        "/api/teacher/refund",
        json={"student_id": sid, "amount": 0, "note": "test note"},
    )
    assert r.status_code == 422


async def test_deduct_cannot_make_negative(teacher_client_with_student) -> None:
    client, sid = teacher_client_with_student
    r = await client.post(
        "/api/teacher/deduct",
        json={"student_id": sid, "amount": 999, "note": "huge"},
    )
    assert r.status_code == 409
