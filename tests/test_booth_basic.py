import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def booth_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play, tally) "
            "VALUES ('Ring Toss', '1234', 2, 7)"
        )
        await conn.execute(
            "INSERT INTO students (name, class, tokens, is_absent) VALUES "
            "('Alice', '3E1', 10, FALSE), "
            "('Bob',   '3E2',  3, FALSE), "
            "('Carl',  '3N1',  5, TRUE)"
        )
    await client.post("/api/booth/login", json={"code": "1234"})
    return client


async def test_booth_me_returns_session_info(booth_client) -> None:
    r = await booth_client.get("/api/booth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Ring Toss"
    assert body["cost_per_play"] == 2
    assert body["tally"] == 7


async def test_students_returns_all_with_balance_and_absent(booth_client) -> None:
    r = await booth_client.get("/api/booth/students")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    bob = next(s for s in body if s["name"] == "Bob")
    assert bob["tokens"] == 3
    assert bob["is_absent"] is False
    carl = next(s for s in body if s["name"] == "Carl")
    assert carl["is_absent"] is True


async def test_students_etag_returns_304(booth_client) -> None:
    r1 = await booth_client.get("/api/booth/students")
    etag = r1.headers["etag"]
    r2 = await booth_client.get("/api/booth/students", headers={"If-None-Match": etag})
    assert r2.status_code == 304


async def test_booth_requires_booth_role(client) -> None:
    r = await client.get("/api/booth/students")
    assert r.status_code == 401
