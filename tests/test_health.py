import pytest
from httpx import ASGITransport, AsyncClient

from app import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_health_returns_ok() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
