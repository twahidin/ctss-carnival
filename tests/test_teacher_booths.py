import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def teacher_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('Alice', '2A', 10), ('Bob', '2B', 5)"
        )
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play, owning_class) VALUES "
            "('Karaoke', '1111', 1, '3I1'), "
            "('Haunted', '2222', 3, '3I1')"
        )
    await client.post("/api/teacher/login", json={"password": "test-teacher-pw"})
    return client


async def test_teacher_lists_booths_with_stats(
    teacher_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
        bid = await conn.fetchval("SELECT id FROM booths WHERE name = 'Karaoke'")
        await conn.execute(
            "INSERT INTO transactions (student_id, booth_id, amount, type) "
            "VALUES ($1, $2, 1, 'play'), ($1, $2, 1, 'play')",
            sid, bid,
        )
    r = await teacher_client.get("/api/teacher/booths")
    assert r.status_code == 200
    booths = {b["name"]: b for b in r.json()}
    assert booths["Karaoke"]["plays"] == 2
    assert booths["Karaoke"]["avg_amount"] == 1.0
    assert booths["Karaoke"]["suspicious"] is False
    assert booths["Haunted"]["plays"] == 0


async def test_teacher_flags_suspicious_booth(
    teacher_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
        bid = await conn.fetchval("SELECT id FROM booths WHERE name = 'Karaoke'")
        # Karaoke declared cost = 1, but charging 3 → suspicious (3 > 1*1.5)
        await conn.execute(
            "INSERT INTO transactions (student_id, booth_id, amount, type) "
            "VALUES ($1, $2, 3, 'play')",
            sid, bid,
        )
    r = await teacher_client.get("/api/teacher/booths")
    assert r.status_code == 200
    karaoke = next(b for b in r.json() if b["name"] == "Karaoke")
    assert karaoke["suspicious"] is True


async def test_teacher_booth_detail_lists_plays(
    teacher_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
        bid = await conn.fetchval("SELECT id FROM booths WHERE name = 'Karaoke'")
        await conn.execute(
            "INSERT INTO transactions (student_id, booth_id, amount, type) "
            "VALUES ($1, $2, 2, 'play')",
            sid, bid,
        )
    r = await teacher_client.get(f"/api/teacher/booth/{bid}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Karaoke"
    assert body["cost_per_play"] == 1
    assert len(body["history"]) == 1
    h = body["history"][0]
    assert h["student_name"] == "Alice"
    assert h["student_class"] == "2A"
    assert h["amount"] == 2
    assert h["reversed"] is False


async def test_teacher_booth_detail_404(teacher_client) -> None:
    r = await teacher_client.get("/api/teacher/booth/999999")
    assert r.status_code == 404


async def test_teacher_booth_endpoints_require_auth(client) -> None:
    r = await client.get("/api/teacher/booths")
    assert r.status_code == 401
    r = await client.get("/api/teacher/booth/1")
    assert r.status_code == 401
