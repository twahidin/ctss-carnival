import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_admin_login_success_sets_cookie(client) -> None:
    r = await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    assert r.status_code == 200
    assert "carnival_session=" in r.headers.get("set-cookie", "")


async def test_admin_login_wrong_password(client) -> None:
    r = await client.post("/api/admin/login", json={"password": "wrong"})
    assert r.status_code == 401


async def test_teacher_login_accepts_teacher_pw(client) -> None:
    r = await client.post("/api/teacher/login", json={"password": "test-teacher-pw"})
    assert r.status_code == 200


async def test_teacher_login_accepts_admin_pw(client) -> None:
    r = await client.post("/api/teacher/login", json={"password": "test-admin-pw"})
    assert r.status_code == 200


async def test_booth_login_success(client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        booth_id = await conn.fetchval(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('Ring Toss', '1234', 1) RETURNING id"
        )
    r = await client.post("/api/booth/login", json={"code": "1234"})
    assert r.status_code == 200
    assert r.json()["booth"]["id"] == booth_id


async def test_booth_login_bad_code(client) -> None:
    r = await client.post("/api/booth/login", json={"code": "9999"})
    assert r.status_code == 401


async def test_rate_limit_triggers_after_10_failed_attempts(client) -> None:
    for _ in range(10):
        await client.post("/api/admin/login", json={"password": "wrong"})
    r = await client.post("/api/admin/login", json={"password": "wrong"})
    assert r.status_code == 429
