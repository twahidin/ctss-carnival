from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from auth import require_role

router = APIRouter(prefix="/api/admin")
require_admin = require_role("admin")


DEFAULT_CARNIVAL_NAME = "Carnival"
DEFAULT_TOKENS = 20


async def _get_setting(pool, key: str, fallback: str) -> str:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT value FROM settings WHERE key = $1", key
        )
    return value if value is not None else fallback


async def _set_setting(pool, key: str, value: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO settings (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            key, value,
        )


class SettingsBody(BaseModel):
    carnival_name: str | None = Field(default=None, min_length=1, max_length=200)
    default_tokens: int | None = Field(default=None, gt=0, le=10_000)


@router.get("/settings")
async def get_settings(
    request: Request, _: dict = Depends(require_admin)
) -> dict[str, Any]:
    pool = request.app.state.pool
    name = await _get_setting(pool, "carnival_name", DEFAULT_CARNIVAL_NAME)
    tokens = await _get_setting(pool, "default_tokens", str(DEFAULT_TOKENS))
    return {"carnival_name": name, "default_tokens": int(tokens)}


@router.post("/settings")
async def set_settings(
    body: SettingsBody, request: Request, _: dict = Depends(require_admin)
) -> dict[str, str]:
    pool = request.app.state.pool
    if body.carnival_name is not None:
        await _set_setting(pool, "carnival_name", body.carnival_name)
    if body.default_tokens is not None:
        await _set_setting(pool, "default_tokens", str(body.default_tokens))
    return {"status": "ok"}
