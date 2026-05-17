import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def admin_with_data(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('A1', '3E1', 5),  ('A2', '3E1', 0), ('B1', '3N2', 10)"
        )
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play, tally) VALUES "
            "('Ring',  '1111', 1, 12), "
            "('Darts', '2222', 2, 4)"
        )
        sids = await conn.fetch("SELECT id, class FROM students ORDER BY name")
        bring = await conn.fetchval("SELECT id FROM booths WHERE name='Ring'")
        bdarts = await conn.fetchval("SELECT id FROM booths WHERE name='Darts'")
        await conn.execute(
            "INSERT INTO transactions (student_id, booth_id, amount, type) VALUES "
            "($1,$2,1,'play'), ($3,$2,1,'play'), ($1,$4,2,'play')",
            sids[0]["id"], bring, sids[2]["id"], bdarts,
        )
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    return client


async def test_summary_by_class(admin_with_data) -> None:
    r = await admin_with_data.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    by_class = {row["class"]: row for row in body["by_class"]}
    assert by_class["3E1"]["total_students"] == 2
    assert by_class["3E1"]["total_remaining"] == 5
    by_booth = {row["name"]: row for row in body["by_booth"]}
    assert by_booth["Ring"]["tally"] == 12
    assert by_booth["Ring"]["top_class"] in {"3E1", "3N2"}


async def test_top_spenders(admin_with_data) -> None:
    r = await admin_with_data.get("/api/summary/top-spenders")
    assert r.status_code == 200
    body = r.json()
    assert len(body) <= 10
    assert "name" in body[0]
    assert "spent" in body[0]


async def test_summary_by_owning_class_groups_booths(client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play, tally, owning_class) VALUES "
            "('Haunted', '1111', 2, 30, '3I4'), "
            "('Lucky',   '2222', 1, 12, '3I4'), "
            "('Ring',    '3333', 1, 47, '3E1')"
        )
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    r = await client.get("/api/summary")
    assert r.status_code == 200
    by_owning = {row["class"]: row for row in r.json()["by_owning_class"]}
    assert by_owning["3I4"]["earned"] == 42
    assert sorted(by_owning["3I4"]["booths"]) == ["Haunted", "Lucky"]
    assert by_owning["3E1"]["earned"] == 47
    assert by_owning["3E1"]["booths"] == ["Ring"]
    earned_seq = [row["earned"] for row in r.json()["by_owning_class"]]
    assert earned_seq == sorted(earned_seq, reverse=True)


async def test_summary_unassigned_booths_grouped(client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play, tally, owning_class) "
            "VALUES ('Old', '1111', 1, 5, '')"
        )
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    r = await client.get("/api/summary")
    by_owning = {row["class"]: row for row in r.json()["by_owning_class"]}
    assert by_owning["(unassigned)"]["earned"] == 5
    assert by_owning["(unassigned)"]["booths"] == ["Old"]
