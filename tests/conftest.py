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
