from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from auth import require_role

router = APIRouter(prefix="/api/teacher")
require_teacher = require_role("teacher")


@router.get("/students")
async def students(
    request: Request, _: dict = Depends(require_teacher)
) -> list[dict[str, Any]]:
    async with request.app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, class, tokens, is_absent FROM students ORDER BY name"
        )
    return [dict(r) for r in rows]


@router.get("/student/{student_id}")
async def student_detail(
    student_id: int, request: Request, _: dict = Depends(require_teacher)
) -> dict[str, Any]:
    async with request.app.state.pool.acquire() as conn:
        s = await conn.fetchrow(
            "SELECT id, name, class, tokens, is_absent FROM students WHERE id = $1",
            student_id,
        )
        if not s:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
        history = await conn.fetch(
            """
            SELECT t.id, t.amount, t.type, t.note, t.created_at, b.name AS booth_name
            FROM transactions t LEFT JOIN booths b ON b.id = t.booth_id
            WHERE t.student_id = $1
            ORDER BY t.id DESC LIMIT 20
            """,
            student_id,
        )
    return {
        **dict(s),
        "history": [
            {
                "id": h["id"],
                "amount": h["amount"],
                "type": h["type"],
                "note": h["note"],
                "booth_name": h["booth_name"],
                "created_at": h["created_at"].isoformat(),
            }
            for h in history
        ],
    }


class BalanceOp(BaseModel):
    student_id: int
    amount: int = Field(gt=0, le=10_000)
    note: str = Field(min_length=3, max_length=200)


async def _apply_balance_change(pool, *, student_id: int, delta: int, type_: str, note: str) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            s = await conn.fetchrow(
                "SELECT tokens FROM students WHERE id = $1 FOR UPDATE", student_id
            )
            if not s:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
            new_balance = s["tokens"] + delta
            if new_balance < 0:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Cannot reduce below 0 (current: {s['tokens']})",
                )
            await conn.execute(
                "UPDATE students SET tokens = $1 WHERE id = $2",
                new_balance, student_id,
            )
            await conn.execute(
                "INSERT INTO transactions (student_id, amount, type, note) "
                "VALUES ($1, $2, $3, $4)",
                student_id, abs(delta), type_, note,
            )
    # Invalidate the booth-side students cache so the next search shows the new balance.
    from routes.booth import _invalidate_students_cache
    _invalidate_students_cache()
    return new_balance


@router.post("/refund")
async def refund(
    body: BalanceOp, request: Request, _: dict = Depends(require_teacher)
) -> dict[str, int]:
    new_balance = await _apply_balance_change(
        request.app.state.pool,
        student_id=body.student_id, delta=body.amount, type_="refund", note=body.note,
    )
    return {"new_balance": new_balance}


@router.post("/deduct")
async def deduct(
    body: BalanceOp, request: Request, _: dict = Depends(require_teacher)
) -> dict[str, int]:
    new_balance = await _apply_balance_change(
        request.app.state.pool,
        student_id=body.student_id, delta=-body.amount, type_="deduct", note=body.note,
    )
    return {"new_balance": new_balance}


class AbsentBody(BaseModel):
    student_id: int
    is_absent: bool


class AbsentBulkBody(BaseModel):
    student_ids: list[int] = Field(min_length=1, max_length=1000)
    is_absent: bool


@router.post("/absent")
async def absent(
    body: AbsentBody, request: Request, _: dict = Depends(require_teacher)
) -> dict[str, str]:
    async with request.app.state.pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE students SET is_absent = $1 WHERE id = $2",
            body.is_absent, body.student_id,
        )
    if result.endswith("0"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    from routes.booth import _invalidate_students_cache
    _invalidate_students_cache()
    return {"status": "ok"}


@router.post("/absent-bulk")
async def absent_bulk(
    body: AbsentBulkBody, request: Request, _: dict = Depends(require_teacher)
) -> dict[str, int]:
    async with request.app.state.pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE students SET is_absent = $1 WHERE id = ANY($2::int[])",
            body.is_absent, body.student_ids,
        )
    affected = int(result.rsplit(" ", 1)[-1])
    from routes.booth import _invalidate_students_cache
    _invalidate_students_cache()
    return {"affected": affected}


@router.get("/students-by-class")
async def students_by_class(
    request: Request, _: dict = Depends(require_teacher)
) -> list[dict[str, Any]]:
    async with request.app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, class, tokens, is_absent FROM students "
            "ORDER BY class, name"
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault(r["class"], []).append(
            {"id": r["id"], "name": r["name"], "tokens": r["tokens"], "is_absent": r["is_absent"]}
        )
    return [{"class": k, "students": v} for k, v in grouped.items()]
