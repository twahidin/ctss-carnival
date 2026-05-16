from typing import Any

from fastapi import APIRouter, Depends, Request

from auth import require_role

router = APIRouter(prefix="/api/summary")
require_admin = require_role("admin")


@router.get("")
async def summary(
    request: Request, _: dict = Depends(require_admin)
) -> dict[str, Any]:
    async with request.app.state.pool.acquire() as conn:
        by_class = await conn.fetch(
            """
            SELECT s.class,
                   COUNT(*) AS total_students,
                   COALESCE(SUM(spent.spent), 0) AS total_spent,
                   COALESCE(SUM(s.tokens), 0)    AS total_remaining
            FROM students s
            LEFT JOIN LATERAL (
                SELECT COALESCE(SUM(amount), 0) AS spent
                FROM transactions t
                WHERE t.student_id = s.id AND t.type IN ('play','deduct')
            ) spent ON TRUE
            GROUP BY s.class ORDER BY s.class
            """
        )
        by_booth = await conn.fetch(
            """
            SELECT b.id, b.name, b.tally,
                   COALESCE(tx_count.c, 0) AS tx_count,
                   tc.top_class
            FROM booths b
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS c FROM transactions t
                WHERE t.booth_id = b.id AND t.type = 'play' AND t.reversed_by IS NULL
            ) tx_count ON TRUE
            LEFT JOIN LATERAL (
                SELECT s.class AS top_class FROM transactions t
                JOIN students s ON s.id = t.student_id
                WHERE t.booth_id = b.id AND t.type = 'play' AND t.reversed_by IS NULL
                GROUP BY s.class ORDER BY COUNT(*) DESC LIMIT 1
            ) tc ON TRUE
            ORDER BY b.id
            """
        )
    return {
        "by_class": [dict(r) for r in by_class],
        "by_booth": [dict(r) for r in by_booth],
    }


@router.get("/top-spenders")
async def top_spenders(
    request: Request, _: dict = Depends(require_admin)
) -> list[dict[str, Any]]:
    async with request.app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.id, s.name, s.class,
                   COALESCE(SUM(t.amount), 0) AS spent
            FROM students s
            LEFT JOIN transactions t
              ON t.student_id = s.id AND t.type IN ('play','deduct')
            GROUP BY s.id, s.name, s.class
            ORDER BY spent DESC, s.name
            LIMIT 10
            """
        )
    return [dict(r) for r in rows]
