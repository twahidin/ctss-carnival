import hashlib
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from auth import require_role

router = APIRouter(prefix="/api/booth")
require_booth = require_role("booth")

_students_cache: dict[str, tuple[float, str, list[dict[str, Any]]]] = {}
_STUDENTS_TTL = 5.0  # seconds


def _invalidate_students_cache() -> None:
    _students_cache.pop("v", None)


@router.get("/me")
async def me(
    request: Request, session: dict = Depends(require_booth)
) -> dict[str, Any]:
    booth_id = session["booth_id"]
    async with request.app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, cost_per_play, tally FROM booths WHERE id = $1",
            booth_id,
        )
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Booth not found")
        tx_count = await conn.fetchval(
            "SELECT COUNT(*) FROM transactions WHERE booth_id = $1 AND type = 'play'",
            booth_id,
        )
    return {**dict(row), "tx_count": tx_count}


async def _load_students(pool) -> tuple[str, list[dict[str, Any]]]:
    cached = _students_cache.get("v")
    now = time.time()
    if cached and now - cached[0] < _STUDENTS_TTL:
        return cached[1], cached[2]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, class, tokens, is_absent FROM students ORDER BY name"
        )
    students = [dict(r) for r in rows]
    payload = json.dumps(students, default=str, sort_keys=True).encode()
    etag = '"' + hashlib.sha1(payload).hexdigest() + '"'
    _students_cache["v"] = (now, etag, students)
    return etag, students


@router.get("/students")
async def list_students(
    request: Request,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    _: dict = Depends(require_booth),
):
    etag, students = await _load_students(request.app.state.pool)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return students
