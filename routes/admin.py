import re
import secrets
from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from auth import require_role
from csv_import import CsvImportError, parse_roster_csv

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


CODE_RE = re.compile(r"^\d{4,6}$")


class BoothCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    code: str
    cost_per_play: int = Field(ge=1, le=1000)


class BoothUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    code: str | None = None
    cost_per_play: int | None = Field(default=None, ge=1, le=1000)


def _validate_code(code: str) -> None:
    if not CODE_RE.match(code):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Code must be 4-6 digits numeric"
        )


@router.get("/booths")
async def list_booths(
    request: Request, _: dict = Depends(require_admin)
) -> list[dict[str, Any]]:
    async with request.app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, code, cost_per_play, tally FROM booths ORDER BY id"
        )
    return [dict(r) for r in rows]


@router.post("/booths")
async def create_booth(
    body: BoothCreate, request: Request, _: dict = Depends(require_admin)
) -> dict[str, Any]:
    _validate_code(body.code)
    async with request.app.state.pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO booths (name, code, cost_per_play)
                VALUES ($1, $2, $3)
                RETURNING id, name, code, cost_per_play, tally
                """,
                body.name, body.code, body.cost_per_play,
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(status.HTTP_409_CONFLICT, "Code already in use")
            raise
    assert row is not None
    return dict(row)


@router.put("/booths/{booth_id}")
async def update_booth(
    booth_id: int,
    body: BoothUpdate,
    request: Request,
    _: dict = Depends(require_admin),
) -> dict[str, Any]:
    if body.code is not None:
        _validate_code(body.code)
    fields: list[str] = []
    values: list[Any] = []
    for col in ("name", "code", "cost_per_play"):
        v = getattr(body, col)
        if v is not None:
            fields.append(f"{col} = ${len(values) + 1}")
            values.append(v)
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    values.append(booth_id)
    async with request.app.state.pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                f"UPDATE booths SET {', '.join(fields)} "
                f"WHERE id = ${len(values)} "
                "RETURNING id, name, code, cost_per_play, tally",
                *values,
            )
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(status.HTTP_409_CONFLICT, "Code already in use")
            raise
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booth not found")
    return dict(row)


@router.delete("/booths/{booth_id}")
async def delete_booth(
    booth_id: int, request: Request, _: dict = Depends(require_admin)
) -> dict[str, str]:
    async with request.app.state.pool.acquire() as conn:
        result = await conn.execute("DELETE FROM booths WHERE id = $1", booth_id)
    if result.endswith("0"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booth not found")
    return {"status": "ok"}


_pending_uploads: dict[str, list[tuple[str, str]]] = {}
MAX_CSV_BYTES = 1_000_000


@router.post("/upload-csv")
async def upload_csv_preview(
    request: Request,
    file: UploadFile = File(...),
    _: dict = Depends(require_admin),
) -> dict[str, Any]:
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        tx_count = await conn.fetchval("SELECT COUNT(*) FROM transactions")
    if tx_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot re-upload roster while transactions exist. Run Full Reset first.",
        )
    data = await file.read(MAX_CSV_BYTES + 1)
    if len(data) > MAX_CSV_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File too large (max 1MB)")
    try:
        rows = parse_roster_csv(data)
    except CsvImportError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    token = secrets.token_urlsafe(16)
    _pending_uploads[token] = rows
    counts = Counter(klass for _, klass in rows)
    return {
        "row_count": len(rows),
        "by_class": sorted(counts.items()),
        "sample": rows[:5],
        "token": token,
    }


class ConfirmBody(BaseModel):
    token: str


@router.post("/upload-csv/confirm")
async def upload_csv_confirm(
    body: ConfirmBody, request: Request, _: dict = Depends(require_admin)
) -> dict[str, int]:
    rows = _pending_uploads.pop(body.token, None)
    if rows is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired token")
    pool = request.app.state.pool
    default_tokens = int(
        await _get_setting(pool, "default_tokens", str(DEFAULT_TOKENS))
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM students")
            await conn.executemany(
                "INSERT INTO students (name, class, tokens) VALUES ($1, $2, $3)",
                [(name, klass, default_tokens) for name, klass in rows],
            )
    return {"inserted": len(rows)}
