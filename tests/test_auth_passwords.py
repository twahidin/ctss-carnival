import os

import pytest

from auth import bootstrap_passwords, verify_password


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_bootstrap_hashes_admin_and_teacher(session_pool) -> None:
    await bootstrap_passwords(session_pool)
    async with session_pool.acquire() as conn:
        admin_hash = await conn.fetchval(
            "SELECT value FROM settings WHERE key = 'admin_password_hash'"
        )
        teacher_hash = await conn.fetchval(
            "SELECT value FROM settings WHERE key = 'teacher_password_hash'"
        )
    assert admin_hash and admin_hash.startswith("$2")
    assert teacher_hash and teacher_hash.startswith("$2")
    assert await verify_password(session_pool, "admin", os.environ["ADMIN_PASSWORD"])
    assert await verify_password(session_pool, "teacher", os.environ["TEACHER_PASSWORD"])
    assert not await verify_password(session_pool, "admin", "wrong")


async def test_bootstrap_rehashes_when_env_var_changes(
    session_pool, monkeypatch
) -> None:
    await bootstrap_passwords(session_pool)
    monkeypatch.setenv("ADMIN_PASSWORD", "rotated-admin")
    await bootstrap_passwords(session_pool)
    assert await verify_password(session_pool, "admin", "rotated-admin")
    assert not await verify_password(session_pool, "admin", "test-admin-pw")
