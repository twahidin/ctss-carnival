import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def admin_client(client):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    return client


async def test_get_settings_returns_defaults(admin_client) -> None:
    r = await admin_client.get("/api/admin/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["carnival_name"] == "Carnival"
    assert body["default_tokens"] == 20


async def test_set_settings_persists(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/settings",
        json={"carnival_name": "CTSS 2026", "default_tokens": 25},
    )
    assert r.status_code == 200
    r = await admin_client.get("/api/admin/settings")
    body = r.json()
    assert body["carnival_name"] == "CTSS 2026"
    assert body["default_tokens"] == 25


async def test_set_settings_requires_admin(client) -> None:
    r = await client.post("/api/admin/settings", json={"carnival_name": "x"})
    assert r.status_code == 401


async def test_default_tokens_must_be_positive(admin_client) -> None:
    r = await admin_client.post("/api/admin/settings", json={"default_tokens": 0})
    assert r.status_code in (400, 422)  # pydantic v2 returns 422 on validation
