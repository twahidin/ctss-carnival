import hashlib
import os
from typing import Literal

import asyncpg
from passlib.hash import bcrypt

Role = Literal["admin", "teacher"]


def _source_hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


async def bootstrap_passwords(pool: asyncpg.Pool) -> None:
    for role, env_var in (("admin", "ADMIN_PASSWORD"), ("teacher", "TEACHER_PASSWORD")):
        plaintext = os.environ[env_var]
        new_source = _source_hash(plaintext)
        async with pool.acquire() as conn:
            existing_source = await conn.fetchval(
                "SELECT value FROM settings WHERE key = $1",
                f"{role}_password_source_hash",
            )
            if existing_source == new_source:
                continue
            pw_hash = bcrypt.using(rounds=12).hash(plaintext)
            await conn.execute(
                """
                INSERT INTO settings (key, value) VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                f"{role}_password_hash", pw_hash,
            )
            await conn.execute(
                """
                INSERT INTO settings (key, value) VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                f"{role}_password_source_hash", new_source,
            )


async def verify_password(pool: asyncpg.Pool, role: Role, plaintext: str) -> bool:
    async with pool.acquire() as conn:
        pw_hash = await conn.fetchval(
            "SELECT value FROM settings WHERE key = $1",
            f"{role}_password_hash",
        )
    if not pw_hash:
        return False
    return bcrypt.verify(plaintext, pw_hash)
