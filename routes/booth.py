import hashlib
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel

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


class PayBody(BaseModel):
    student_id: int


def _api_error(detail: str, code: str, status_code: int) -> HTTPException:
    return HTTPException(status_code, detail={"error": detail, "code": code})


@router.post("/pay")
async def pay(
    body: PayBody, request: Request, session: dict = Depends(require_booth)
) -> dict[str, Any]:
    booth_id = session["booth_id"]
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            booth = await conn.fetchrow(
                "SELECT cost_per_play FROM booths WHERE id = $1", booth_id
            )
            if not booth:
                raise _api_error("Booth not found", "BOOTH_NOT_FOUND", 404)
            cost = booth["cost_per_play"]
            student = await conn.fetchrow(
                "SELECT id, name, tokens, is_absent FROM students "
                "WHERE id = $1 FOR UPDATE",
                body.student_id,
            )
            if not student:
                raise _api_error("Student not found", "STUDENT_NOT_FOUND", 404)
            if student["is_absent"]:
                raise _api_error(
                    "Student is marked absent. Please see a teacher.",
                    "STUDENT_ABSENT", 409,
                )
            if student["tokens"] < cost:
                raise _api_error(
                    f"Insufficient tokens (has {student['tokens']}, needs {cost})",
                    "INSUFFICIENT_TOKENS", 409,
                )
            new_balance = student["tokens"] - cost
            await conn.execute(
                "UPDATE students SET tokens = $1 WHERE id = $2",
                new_balance, body.student_id,
            )
            await conn.execute(
                "UPDATE booths SET tally = tally + $1 WHERE id = $2",
                cost, booth_id,
            )
            tx_id = await conn.fetchval(
                "INSERT INTO transactions (student_id, booth_id, amount, type) "
                "VALUES ($1, $2, $3, 'play') RETURNING id",
                body.student_id, booth_id, cost,
            )
    _invalidate_students_cache()
    return {"transaction_id": tx_id, "new_balance": new_balance}
