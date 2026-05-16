from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

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
