import os

import pytest
import pytest_asyncio

os.environ.setdefault(
    "DATABASE_URL", "postgresql://carnival:carnival@localhost:5433/carnival"
)
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")
os.environ.setdefault("TEACHER_PASSWORD", "test-teacher-pw")
os.environ.setdefault("SESSION_SECRET", "test-secret-at-least-32-chars-long-ok")


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ["DATABASE_URL"]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def session_pool(database_url: str):
    from db import init_pool, init_schema, close_pool

    pool = await init_pool(database_url)
    await init_schema(pool)
    yield pool
    await close_pool(pool)


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_db(session_pool):
    from db import truncate_all

    await truncate_all(session_pool)
    yield


@pytest_asyncio.fixture(loop_scope="session")
async def client(session_pool):
    from httpx import ASGITransport, AsyncClient
    from app import app
    from auth import bootstrap_passwords
    # Inject pool into app state so endpoints can use it without running lifespan
    app.state.pool = session_pool
    await bootstrap_passwords(session_pool)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def reset_rate_limit():
    yield
    # cleanup: clear the rate-limit bucket between tests
    try:
        from routes.auth_routes import _attempts
        _attempts.clear()
    except ImportError:
        pass
