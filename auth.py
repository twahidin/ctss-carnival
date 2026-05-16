import hashlib
import json
import os
import time
from typing import Any, Literal

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


from fastapi import Cookie, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

SESSION_COOKIE = "carnival_session"
SESSION_MAX_AGE_SECONDS = 12 * 3600


def _signer() -> TimestampSigner:
    return TimestampSigner(os.environ["SESSION_SECRET"])


def make_session(*, role: str, booth_id: int | None = None) -> str:
    payload = {"role": role, "booth_id": booth_id, "iat": int(time.time())}
    return _signer().sign(json.dumps(payload).encode()).decode()


def read_session(cookie: str) -> dict[str, Any] | None:
    try:
        raw = _signer().unsign(cookie.encode(), max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def require_role(*allowed: str):
    """FastAPI dependency factory. 'admin' role always passes any check."""
    async def dep(
        carnival_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, Any]:
        if not carnival_session:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required")
        data = read_session(carnival_session)
        if not data:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
        if data["role"] != "admin" and data["role"] not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
        return data
    return dep
