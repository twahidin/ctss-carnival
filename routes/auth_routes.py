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


def _get_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    """Raise 429 if this IP has too many recent failures. Does NOT record an attempt."""
    ip = _get_ip(request)
    now = time.time()
    bucket = _attempts[ip]
    while bucket and now - bucket[0] > _WINDOW:
        bucket.popleft()
    if len(bucket) >= _LIMIT:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts"
        )


def _record_failure(request: Request) -> None:
    """Record a failed login attempt for rate-limiting purposes."""
    ip = _get_ip(request)
    _attempts[ip].append(time.time())


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
    _check_rate_limit(request)
    if not body.password or not await verify_password(
        request.app.state.pool, "admin", body.password
    ):
        _record_failure(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")
    _set_cookie(response, make_session(role="admin"))
    return {"role": "admin"}


@router.post("/api/teacher/login")
async def teacher_login(
    body: LoginBody, request: Request, response: Response
) -> dict[str, Any]:
    _check_rate_limit(request)
    if not body.password:
        _record_failure(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")
    pool = request.app.state.pool
    if await verify_password(pool, "admin", body.password):
        _set_cookie(response, make_session(role="admin"))
        return {"role": "admin"}
    if await verify_password(pool, "teacher", body.password):
        _set_cookie(response, make_session(role="teacher"))
        return {"role": "teacher"}
    _record_failure(request)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")


@router.post("/api/booth/login")
async def booth_login(
    body: LoginBody, request: Request, response: Response
) -> dict[str, Any]:
    _check_rate_limit(request)
    if not body.code:
        _record_failure(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code")
    async with request.app.state.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, cost_per_play, tally FROM booths WHERE code = $1",
            body.code,
        )
    if not row:
        _record_failure(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code")
    _set_cookie(response, make_session(role="booth", booth_id=row["id"]))
    return {"role": "booth", "booth": dict(row)}


@router.post("/api/admin/logout")
@router.post("/api/teacher/logout")
@router.post("/api/booth/logout")
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "ok"}
