import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from auth import SESSION_COOKIE, SESSION_MAX_AGE_SECONDS, make_session, verify_password

router = APIRouter()

_attempts: dict[str, deque[float]] = defaultdict(deque)
_WINDOW = 60.0
_LIMIT = 10


def _rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    bucket = _attempts[ip]
    while bucket and now - bucket[0] > _WINDOW:
        bucket.popleft()
    if len(bucket) >= _LIMIT:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts"
        )
    bucket.append(now)


def _set_cookie(response: Response, cookie_value: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        cookie_value,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )


class LoginBody(BaseModel):
    password: str | None = None
    code: str | None = None


@router.post("/api/admin/login")
async def admin_login(
    body: LoginBody, request: Request, response: Response
) -> dict[str, Any]:
    _rate_limit(request)
    if not body.password or not await verify_password(
        request.app.state.pool, "admin", body.password
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")
    _set_cookie(response, make_session(role="admin"))
    return {"role": "admin"}


@router.post("/api/teacher/login")
async def teacher_login(
    body: LoginBody, request: Request, response: Response
) -> dict[str, Any]:
    _rate_limit(request)
    if not body.password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")
    pool = request.app.state.pool
    if await verify_password(pool, "admin", body.password):
        _set_cookie(response, make_session(role="admin"))
        return {"role": "admin"}
    if await verify_password(pool, "teacher", body.password):
        _set_cookie(response, make_session(role="teacher"))
        return {"role": "teacher"}
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")


@router.post("/api/booth/login")
async def booth_login(
    body: LoginBody, request: Request, response: Response
) -> dict[str, Any]:
    _rate_limit(request)
    if not body.code:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code")
    async with request.app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, cost_per_play, tally FROM booths WHERE code = $1",
            body.code,
        )
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code")
    _set_cookie(response, make_session(role="booth", booth_id=row["id"]))
    return {"role": "booth", "booth": dict(row)}


@router.post("/api/admin/logout")
@router.post("/api/teacher/logout")
@router.post("/api/booth/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "ok"}
