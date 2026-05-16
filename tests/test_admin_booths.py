import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def admin_client(client):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    return client


async def test_create_booth(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/booths",
        json={"name": "Ring Toss", "code": "1234", "cost_per_play": 2},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Ring Toss"
    assert body["code"] == "1234"
    assert body["cost_per_play"] == 2


async def test_list_booths(admin_client) -> None:
    await admin_client.post(
        "/api/admin/booths", json={"name": "A", "code": "1111", "cost_per_play": 1}
    )
    await admin_client.post(
        "/api/admin/booths", json={"name": "B", "code": "2222", "cost_per_play": 1}
    )
    r = await admin_client.get("/api/admin/booths")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_update_booth(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/booths", json={"name": "A", "code": "1111", "cost_per_play": 1}
    )
    booth_id = r.json()["id"]
    r = await admin_client.put(
        f"/api/admin/booths/{booth_id}", json={"cost_per_play": 3}
    )
    assert r.status_code == 200
    assert r.json()["cost_per_play"] == 3


async def test_delete_booth(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/booths", json={"name": "A", "code": "1111", "cost_per_play": 1}
    )
    booth_id = r.json()["id"]
    r = await admin_client.delete(f"/api/admin/booths/{booth_id}")
    assert r.status_code == 200
    r = await admin_client.get("/api/admin/booths")
    assert r.json() == []


async def test_duplicate_code_rejected(admin_client) -> None:
    await admin_client.post(
        "/api/admin/booths", json={"name": "A", "code": "1111", "cost_per_play": 1}
    )
    r = await admin_client.post(
        "/api/admin/booths", json={"name": "B", "code": "1111", "cost_per_play": 1}
    )
    assert r.status_code == 409


async def test_code_must_be_numeric_4_to_6(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/booths", json={"name": "A", "code": "abc", "cost_per_play": 1}
    )
    assert r.status_code == 400
