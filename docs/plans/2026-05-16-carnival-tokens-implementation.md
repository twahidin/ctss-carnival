# Carnival Token System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Railway-deployable FastAPI + Postgres carnival token system per `docs/plans/2026-05-16-carnival-tokens-design.md`.

**Architecture:** Single FastAPI service serving JSON APIs + four static HTML pages. asyncpg pool against Railway Postgres. Auth via bcrypt + signed cookies. Row-level locking (`SELECT ... FOR UPDATE`) on every balance mutation. Append-only transactions table.

**Tech Stack:** Python 3.11+, FastAPI, asyncpg, passlib[bcrypt], itsdangerous, python-multipart, uvicorn, pytest + pytest-asyncio + httpx, Tailwind CDN, vanilla JS.

**Working directory:** `/Users/joetay/Developer/tokens-system/`

**Reference:** Full design at `docs/plans/2026-05-16-carnival-tokens-design.md` — read it before starting.

---

## Conventions used throughout this plan

- **TDD strict:** every task is _write test → run & watch it fail → implement → run & watch pass → commit_. Skip the "watch fail" step at your peril; it catches stub-tests that pass-by-accident.
- **Exact file paths only**, relative to project root.
- **Commits per task.** Message style: Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`, `docs:`).
- **Async everywhere.** Every route handler, every DB call is `async def`.
- **Type hints everywhere.** `mypy` not enforced but write as if it were.
- **Comments only when WHY is non-obvious.** No prose docstrings on small functions.
- **Tests use a real Postgres** (Docker compose below), schema created per-session, one transaction per test that gets rolled back. Concurrency tests are the exception (they use separate connections).

---

## Phase 0 — Project skeleton

### Task 1: Initialize repo and write skeleton files

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `Procfile`
- Create: `railway.json`
- Create: `.env.example`
- Create: `docker-compose.yml`
- Create: `app.py` (stub)

**Step 1: `git init` and configure**

```bash
cd /Users/joetay/Developer/tokens-system
git init
git branch -m main
```

**Step 2: Write `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
venv/
.env
.pytest_cache/
.coverage
htmlcov/
*.egg-info/
.DS_Store
```

**Step 3: Write `requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
asyncpg==0.30.0
python-multipart==0.0.12
passlib[bcrypt]==1.7.4
itsdangerous==2.2.0
```

**Step 4: Write `requirements-dev.txt`**

```
-r requirements.txt
pytest==8.3.3
pytest-asyncio==0.24.0
httpx==0.27.2
```

**Step 5: Write `Procfile`**

```
web: uvicorn app:app --host 0.0.0.0 --port $PORT
```

**Step 6: Write `railway.json`**

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": { "builder": "NIXPACKS" },
  "deploy": {
    "startCommand": "uvicorn app:app --host 0.0.0.0 --port $PORT",
    "healthcheckPath": "/health",
    "healthcheckTimeout": 100,
    "restartPolicyType": "ON_FAILURE"
  }
}
```

**Step 7: Write `.env.example`**

```
DATABASE_URL=postgresql://carnival:carnival@localhost:5432/carnival
ADMIN_PASSWORD=change-me-admin
TEACHER_PASSWORD=change-me-teacher
SESSION_SECRET=change-me-long-random-string-min-32-chars
PORT=8000
```

**Step 8: Write `docker-compose.yml`** (local Postgres for dev/test)

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: carnival
      POSTGRES_PASSWORD: carnival
      POSTGRES_DB: carnival
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
volumes:
  pgdata:
```

**Step 9: Write minimal `app.py`** so Python imports succeed in later tasks

```python
from fastapi import FastAPI

app = FastAPI(title="CTSS Carnival Token System")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

**Step 10: Create venv, install deps, smoke-test import**

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -c "import app; print(app.app.title)"
```

Expected output: `CTSS Carnival Token System`

**Step 11: Commit**

```bash
git add .gitignore requirements.txt requirements-dev.txt Procfile railway.json .env.example docker-compose.yml app.py
git commit -m "chore: project skeleton with Railway + FastAPI scaffolding"
```

---

### Task 2: Bring up local Postgres and verify connection

**Files:**
- Create: `tests/conftest.py` (initial version)
- Create: `tests/__init__.py`
- Create: `tests/test_db_connection.py`

**Step 1: Start local Postgres**

```bash
docker compose up -d
docker compose ps  # verify "running" / healthy
```

**Step 2: Write `tests/__init__.py`** (empty file, just marks package)

**Step 3: Write `tests/conftest.py`**

```python
import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql://carnival:carnival@localhost:5432/carnival"
)
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")
os.environ.setdefault("TEACHER_PASSWORD", "test-teacher-pw")
os.environ.setdefault("SESSION_SECRET", "test-secret-at-least-32-chars-long-ok")


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ["DATABASE_URL"]
```

**Step 4: Write the failing test `tests/test_db_connection.py`**

```python
import asyncpg
import pytest


@pytest.mark.asyncio
async def test_can_connect_to_postgres(database_url: str) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        version = await conn.fetchval("SELECT version()")
        assert "PostgreSQL" in version
    finally:
        await conn.close()
```

**Step 5: Configure pytest** — append to `requirements-dev.txt` already has pytest-asyncio. Create `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 6: Run test**

```bash
pytest tests/test_db_connection.py -v
```

Expected: PASS (Postgres is up, asyncpg connects).

**Step 7: Commit**

```bash
git add tests/ pyproject.toml
git commit -m "test: verify local Postgres connection via asyncpg"
```

---

### Task 3: Schema init + idempotent migration

**Files:**
- Create: `db.py`
- Create: `tests/test_db_schema.py`

**Step 1: Write the failing test `tests/test_db_schema.py`**

```python
import asyncpg
import pytest

from db import init_pool, init_schema, close_pool


@pytest.fixture
async def pool(database_url: str):
    pool = await init_pool(database_url)
    yield pool
    await close_pool(pool)


async def test_init_schema_creates_all_tables(pool: asyncpg.Pool) -> None:
    await init_schema(pool)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        names = {r["tablename"] for r in rows}
    assert {"students", "booths", "transactions", "settings"} <= names


async def test_init_schema_is_idempotent(pool: asyncpg.Pool) -> None:
    await init_schema(pool)
    await init_schema(pool)  # should not raise
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM students")
    assert count == 0
```

**Step 2: Run test, watch it fail**

```bash
pytest tests/test_db_schema.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'db'`.

**Step 3: Implement `db.py`**

```python
import asyncpg

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    class TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    is_absent BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_students_name ON students (LOWER(name));
CREATE INDEX IF NOT EXISTS idx_students_class ON students (class);

CREATE TABLE IF NOT EXISTS booths (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    code VARCHAR(10) UNIQUE NOT NULL,
    cost_per_play INTEGER NOT NULL DEFAULT 1 CHECK (cost_per_play > 0),
    tally INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
    booth_id INTEGER REFERENCES booths(id) ON DELETE SET NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    type VARCHAR(20) NOT NULL,
    note TEXT,
    reversed_by INTEGER REFERENCES transactions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tx_student ON transactions(student_id);
CREATE INDEX IF NOT EXISTS idx_tx_booth ON transactions(booth_id);
CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


async def init_pool(database_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(database_url, min_size=2, max_size=10)


async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()


async def init_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def truncate_all(pool: asyncpg.Pool) -> None:
    """Test helper: wipe data, preserve schema."""
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE students, booths, transactions, settings "
            "RESTART IDENTITY CASCADE"
        )
```

**Step 4: Add a cleanup fixture** — update `tests/conftest.py`:

```python
import os

import asyncpg
import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql://carnival:carnival@localhost:5432/carnival"
)
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")
os.environ.setdefault("TEACHER_PASSWORD", "test-teacher-pw")
os.environ.setdefault("SESSION_SECRET", "test-secret-at-least-32-chars-long-ok")


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
async def session_pool(database_url: str):
    from db import init_pool, init_schema, close_pool

    pool = await init_pool(database_url)
    await init_schema(pool)
    yield pool
    await close_pool(pool)


@pytest.fixture(autouse=True)
async def clean_db(session_pool):
    from db import truncate_all

    await truncate_all(session_pool)
    yield
```

Remove the local `pool` fixture from `test_db_schema.py` and use `session_pool` instead:

```python
async def test_init_schema_creates_all_tables(session_pool) -> None:
    async with session_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        names = {r["tablename"] for r in rows}
    assert {"students", "booths", "transactions", "settings"} <= names


async def test_init_schema_is_idempotent(session_pool) -> None:
    from db import init_schema
    await init_schema(session_pool)  # second call, must not raise
    async with session_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM students")
    assert count == 0
```

**Step 5: Run tests**

```bash
pytest tests/ -v
```

Expected: all pass.

**Step 6: Commit**

```bash
git add db.py tests/
git commit -m "feat: postgres schema with idempotent init"
```

---

### Task 4: Wire schema init into app startup; verify /health

**Files:**
- Modify: `app.py`
- Create: `tests/test_health.py`

**Step 1: Write the failing test `tests/test_health.py`**

```python
from httpx import ASGITransport, AsyncClient

from app import app


async def test_health_returns_ok() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

**Step 2: Run, expect it to pass** (health stub from Task 1 still works). Actually verify:

```bash
pytest tests/test_health.py -v
```

Expected: PASS.

**Step 3: Now upgrade `app.py` with lifespan + DB pool**

```python
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import init_pool, init_schema, close_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.environ["DATABASE_URL"]
    pool = await init_pool(database_url)
    await init_schema(pool)
    app.state.pool = pool
    try:
        yield
    finally:
        await close_pool(pool)


app = FastAPI(title="CTSS Carnival Token System", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

**Step 4: Re-run health test**

```bash
pytest tests/test_health.py -v
```

Expected: PASS (lifespan runs against the real Postgres, schema is created).

**Step 5: Manually start server and curl**

```bash
. .venv/bin/activate
export $(grep -v '^#' .env.example | xargs)  # quick local env load
uvicorn app:app --port 8000 &
sleep 2
curl -s localhost:8000/health
kill %1
```

Expected: `{"status":"ok"}`.

**Step 6: Commit**

```bash
git add app.py tests/test_health.py
git commit -m "feat: app lifespan with pool + schema init"
```

---

## Phase 1 — Auth

### Task 5: Password hashing + settings bootstrap

**Files:**
- Create: `auth.py`
- Create: `tests/test_auth_passwords.py`

**Step 1: Write failing test `tests/test_auth_passwords.py`**

```python
import os

from auth import bootstrap_passwords, verify_password


async def test_bootstrap_hashes_admin_and_teacher(session_pool) -> None:
    await bootstrap_passwords(session_pool)
    async with session_pool.acquire() as conn:
        admin_hash = await conn.fetchval(
            "SELECT value FROM settings WHERE key = 'admin_password_hash'"
        )
        teacher_hash = await conn.fetchval(
            "SELECT value FROM settings WHERE key = 'teacher_password_hash'"
        )
    assert admin_hash and admin_hash.startswith("$2")
    assert teacher_hash and teacher_hash.startswith("$2")
    assert await verify_password(session_pool, "admin", os.environ["ADMIN_PASSWORD"])
    assert await verify_password(session_pool, "teacher", os.environ["TEACHER_PASSWORD"])
    assert not await verify_password(session_pool, "admin", "wrong")


async def test_bootstrap_rehashes_when_env_var_changes(
    session_pool, monkeypatch
) -> None:
    await bootstrap_passwords(session_pool)
    monkeypatch.setenv("ADMIN_PASSWORD", "rotated-admin")
    await bootstrap_passwords(session_pool)
    assert await verify_password(session_pool, "admin", "rotated-admin")
    assert not await verify_password(session_pool, "admin", "test-admin-pw")
```

**Step 2: Run, watch it fail**

```bash
pytest tests/test_auth_passwords.py -v
```

Expected: FAIL — no `auth` module.

**Step 3: Implement `auth.py`**

```python
import hashlib
import os
from typing import Literal

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
```

**Step 4: Run tests**

```bash
pytest tests/test_auth_passwords.py -v
```

Expected: PASS.

**Step 5: Wire bootstrap into app lifespan** — edit `app.py`:

```python
# inside lifespan, after init_schema:
from auth import bootstrap_passwords
await bootstrap_passwords(pool)
```

**Step 6: Re-run full suite**

```bash
pytest -v
```

Expected: all pass.

**Step 7: Commit**

```bash
git add auth.py app.py tests/test_auth_passwords.py
git commit -m "feat: bcrypt-hash admin/teacher passwords on startup"
```

---

### Task 6: Session cookies + role dependency

**Files:**
- Modify: `auth.py`
- Create: `tests/test_auth_sessions.py`

**Step 1: Write failing test `tests/test_auth_sessions.py`**

```python
from auth import make_session, read_session


def test_make_and_read_session_roundtrip() -> None:
    cookie = make_session(role="booth", booth_id=42)
    data = read_session(cookie)
    assert data["role"] == "booth"
    assert data["booth_id"] == 42


def test_read_session_rejects_tampered_cookie() -> None:
    cookie = make_session(role="admin")
    tampered = cookie[:-2] + "xx"
    assert read_session(tampered) is None


def test_read_session_rejects_expired(monkeypatch) -> None:
    import time
    cookie = make_session(role="admin")
    monkeypatch.setattr("time.time", lambda: time.time() + 13 * 3600)
    assert read_session(cookie) is None
```

**Step 2: Run, watch it fail**

**Step 3: Add to `auth.py`**

```python
import time
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

SESSION_MAX_AGE_SECONDS = 12 * 3600


def _signer() -> TimestampSigner:
    return TimestampSigner(os.environ["SESSION_SECRET"])


def make_session(*, role: str, booth_id: int | None = None) -> str:
    import json
    payload = {"role": role, "booth_id": booth_id, "iat": int(time.time())}
    return _signer().sign(json.dumps(payload).encode()).decode()


def read_session(cookie: str) -> dict[str, Any] | None:
    import json
    try:
        raw = _signer().unsign(cookie.encode(), max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None
```

**Step 4: Run tests**

```bash
pytest tests/test_auth_sessions.py -v
```

Expected: PASS.

**Step 5: Add `require_role` FastAPI dependency** in `auth.py`:

```python
from fastapi import Cookie, HTTPException, status

SESSION_COOKIE = "carnival_session"


def require_role(*allowed: str):
    """FastAPI dependency factory. `admin` always passes any check."""
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
```

**Step 6: Commit**

```bash
git add auth.py tests/test_auth_sessions.py
git commit -m "feat: signed session cookies with role guard"
```

---

### Task 7: Login endpoints + rate limiting

**Files:**
- Create: `routes/__init__.py` (empty)
- Create: `routes/auth_routes.py`
- Modify: `app.py`
- Create: `tests/test_login.py`

**Step 1: Write failing test `tests/test_login.py`**

```python
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    from app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_admin_login_success_sets_cookie(client) -> None:
    r = await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    assert r.status_code == 200
    assert "carnival_session" in r.cookies


async def test_admin_login_wrong_password(client) -> None:
    r = await client.post("/api/admin/login", json={"password": "wrong"})
    assert r.status_code == 401


async def test_teacher_login_accepts_teacher_pw(client) -> None:
    r = await client.post("/api/teacher/login", json={"password": "test-teacher-pw"})
    assert r.status_code == 200


async def test_teacher_login_accepts_admin_pw(client) -> None:
    r = await client.post("/api/teacher/login", json={"password": "test-admin-pw"})
    assert r.status_code == 200


async def test_booth_login_success(client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        booth_id = await conn.fetchval(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('Ring Toss', '1234', 1) RETURNING id"
        )
    r = await client.post("/api/booth/login", json={"code": "1234"})
    assert r.status_code == 200
    assert r.json()["booth"]["id"] == booth_id


async def test_booth_login_bad_code(client) -> None:
    r = await client.post("/api/booth/login", json={"code": "9999"})
    assert r.status_code == 401


async def test_rate_limit_triggers_after_10_failed_attempts(client) -> None:
    for _ in range(10):
        await client.post("/api/admin/login", json={"password": "wrong"})
    r = await client.post("/api/admin/login", json={"password": "wrong"})
    assert r.status_code == 429
```

**Step 2: Run, watch it fail.**

**Step 3: Implement `routes/auth_routes.py`**

```python
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
```

**Step 4: Wire router into `app.py`** — add at bottom of file, after `app = FastAPI(...)`:

```python
from routes.auth_routes import router as auth_router
app.include_router(auth_router)
```

**Step 5: Run tests**

```bash
pytest tests/test_login.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add routes/ app.py tests/test_login.py
git commit -m "feat: login endpoints for admin, teacher, booth with rate limit"
```

---

## Phase 2 — Admin endpoints

### Task 8: Settings GET/POST

**Files:**
- Create: `routes/admin.py`
- Modify: `app.py`
- Create: `tests/test_admin_settings.py`

**Step 1: Write failing test `tests/test_admin_settings.py`**

```python
import pytest


@pytest.fixture
async def admin_client(client):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    return client


async def test_get_settings_returns_defaults(admin_client) -> None:
    r = await admin_client.get("/api/admin/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["carnival_name"] == "Carnival"
    assert body["default_tokens"] == 20


async def test_set_settings_persists(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/settings",
        json={"carnival_name": "CTSS 2026", "default_tokens": 25},
    )
    assert r.status_code == 200
    r = await admin_client.get("/api/admin/settings")
    body = r.json()
    assert body["carnival_name"] == "CTSS 2026"
    assert body["default_tokens"] == 25


async def test_set_settings_requires_admin(client) -> None:
    r = await client.post("/api/admin/settings", json={"carnival_name": "x"})
    assert r.status_code == 401


async def test_default_tokens_must_be_positive(admin_client) -> None:
    r = await admin_client.post("/api/admin/settings", json={"default_tokens": 0})
    assert r.status_code == 400
```

The `client` fixture is in `tests/test_login.py` — move it to `conftest.py` first:

In `tests/conftest.py`, add:

```python
@pytest.fixture
async def client():
    from httpx import ASGITransport, AsyncClient
    from app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

And remove the duplicate from `test_login.py`.

**Step 2: Run, watch it fail.**

**Step 3: Implement `routes/admin.py`**

```python
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
```

**Step 4: Register router in `app.py`**

```python
from routes.admin import router as admin_router
app.include_router(admin_router)
```

**Step 5: Run tests**

```bash
pytest tests/test_admin_settings.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add routes/admin.py app.py tests/conftest.py tests/test_admin_settings.py tests/test_login.py
git commit -m "feat: admin settings endpoints"
```

---

### Task 9: Booth CRUD

**Files:**
- Modify: `routes/admin.py`
- Create: `tests/test_admin_booths.py`

**Step 1: Write failing test `tests/test_admin_booths.py`**

```python
import pytest


@pytest.fixture
async def admin_client(client):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    return client


async def test_create_booth(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/booths",
        json={"name": "Ring Toss", "code": "1234", "cost_per_play": 2},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Ring Toss"
    assert body["code"] == "1234"
    assert body["cost_per_play"] == 2


async def test_list_booths(admin_client) -> None:
    await admin_client.post(
        "/api/admin/booths", json={"name": "A", "code": "1111", "cost_per_play": 1}
    )
    await admin_client.post(
        "/api/admin/booths", json={"name": "B", "code": "2222", "cost_per_play": 1}
    )
    r = await admin_client.get("/api/admin/booths")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_update_booth(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/booths", json={"name": "A", "code": "1111", "cost_per_play": 1}
    )
    booth_id = r.json()["id"]
    r = await admin_client.put(
        f"/api/admin/booths/{booth_id}", json={"cost_per_play": 3}
    )
    assert r.status_code == 200
    assert r.json()["cost_per_play"] == 3


async def test_delete_booth(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/booths", json={"name": "A", "code": "1111", "cost_per_play": 1}
    )
    booth_id = r.json()["id"]
    r = await admin_client.delete(f"/api/admin/booths/{booth_id}")
    assert r.status_code == 200
    r = await admin_client.get("/api/admin/booths")
    assert r.json() == []


async def test_duplicate_code_rejected(admin_client) -> None:
    await admin_client.post(
        "/api/admin/booths", json={"name": "A", "code": "1111", "cost_per_play": 1}
    )
    r = await admin_client.post(
        "/api/admin/booths", json={"name": "B", "code": "1111", "cost_per_play": 1}
    )
    assert r.status_code == 409


async def test_code_must_be_numeric_4_to_6(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/booths", json={"name": "A", "code": "abc", "cost_per_play": 1}
    )
    assert r.status_code == 400
```

**Step 2: Run, watch it fail.**

**Step 3: Append to `routes/admin.py`**

```python
import re

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
```

**Step 4: Run tests**

```bash
pytest tests/test_admin_booths.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add routes/admin.py tests/test_admin_booths.py
git commit -m "feat: admin booth CRUD with code validation"
```

---

### Task 10: CSV upload (preview + confirm, blocked when tx exist)

**Files:**
- Create: `csv_import.py`
- Modify: `routes/admin.py`
- Create: `tests/test_csv_import.py`
- Create: `tests/test_admin_csv_upload.py`

**Step 1: Write failing unit test `tests/test_csv_import.py`**

```python
import pytest

from csv_import import parse_roster_csv, CsvImportError


def test_parses_basic_csv() -> None:
    data = b"name,class\nJohn Tan,3E1\nMary Lim,3E1\n"
    rows = parse_roster_csv(data)
    assert rows == [("John Tan", "3E1"), ("Mary Lim", "3E1")]


def test_handles_utf8_bom() -> None:
    data = "﻿name,class\nJohn Tan,3E1\n".encode()
    rows = parse_roster_csv(data)
    assert rows == [("John Tan", "3E1")]


def test_handles_crlf() -> None:
    data = b"name,class\r\nJohn Tan,3E1\r\n"
    rows = parse_roster_csv(data)
    assert rows == [("John Tan", "3E1")]


def test_skips_blank_lines() -> None:
    data = b"name,class\n\nJohn Tan,3E1\n\n"
    rows = parse_roster_csv(data)
    assert rows == [("John Tan", "3E1")]


def test_trims_whitespace() -> None:
    data = b"name,class\n  John Tan  ,  3E1 \n"
    rows = parse_roster_csv(data)
    assert rows == [("John Tan", "3E1")]


def test_rejects_missing_headers() -> None:
    with pytest.raises(CsvImportError, match="header"):
        parse_roster_csv(b"foo,bar\nx,y\n")


def test_rejects_empty_file() -> None:
    with pytest.raises(CsvImportError):
        parse_roster_csv(b"")


def test_rejects_row_with_blank_name() -> None:
    with pytest.raises(CsvImportError, match="blank name"):
        parse_roster_csv(b"name,class\n,3E1\n")
```

**Step 2: Run, watch it fail.**

**Step 3: Implement `csv_import.py`**

```python
import csv
import io


class CsvImportError(ValueError):
    pass


def parse_roster_csv(data: bytes) -> list[tuple[str, str]]:
    text = data.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise CsvImportError("File is empty")
    reader = csv.reader(io.StringIO(text))
    try:
        headers = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        raise CsvImportError("File is empty")
    if headers[:2] != ["name", "class"]:
        raise CsvImportError("First row must be header: name,class")
    rows: list[tuple[str, str]] = []
    for i, raw in enumerate(reader, start=2):
        if not raw or all(not c.strip() for c in raw):
            continue
        if len(raw) < 2:
            raise CsvImportError(f"Row {i}: not enough columns")
        name = raw[0].strip()
        klass = raw[1].strip()
        if not name:
            raise CsvImportError(f"Row {i}: blank name")
        if not klass:
            raise CsvImportError(f"Row {i}: blank class")
        rows.append((name, klass))
    if not rows:
        raise CsvImportError("No student rows found")
    return rows
```

**Step 4: Run unit test**

```bash
pytest tests/test_csv_import.py -v
```

Expected: PASS.

**Step 5: Write integration test `tests/test_admin_csv_upload.py`**

```python
import pytest


@pytest.fixture
async def admin_client(client):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    return client


CSV_BASIC = b"name,class\nJohn Tan,3E1\nMary Lim,3E1\nAhmad bin Ali,3N2\n"


async def test_preview_returns_token_and_counts(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("roster.csv", CSV_BASIC, "text/csv")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["row_count"] == 3
    assert sorted(body["by_class"]) == [["3E1", 2], ["3N2", 1]]
    assert body["token"]
    assert len(body["sample"]) == 3


async def test_confirm_inserts_students(admin_client) -> None:
    pr = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("roster.csv", CSV_BASIC, "text/csv")},
    )
    token = pr.json()["token"]
    r = await admin_client.post(
        "/api/admin/upload-csv/confirm", json={"token": token}
    )
    assert r.status_code == 200
    assert r.json()["inserted"] == 3


async def test_confirm_uses_default_tokens(admin_client, session_pool) -> None:
    await admin_client.post(
        "/api/admin/settings", json={"default_tokens": 25}
    )
    pr = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("roster.csv", CSV_BASIC, "text/csv")},
    )
    await admin_client.post(
        "/api/admin/upload-csv/confirm", json={"token": pr.json()["token"]}
    )
    async with session_pool.acquire() as conn:
        tokens = await conn.fetchval("SELECT tokens FROM students LIMIT 1")
    assert tokens == 25


async def test_token_is_single_use(admin_client) -> None:
    pr = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("roster.csv", CSV_BASIC, "text/csv")},
    )
    token = pr.json()["token"]
    await admin_client.post("/api/admin/upload-csv/confirm", json={"token": token})
    r = await admin_client.post("/api/admin/upload-csv/confirm", json={"token": token})
    assert r.status_code == 400


async def test_blocked_when_transactions_exist(admin_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        student_id = await conn.fetchval(
            "INSERT INTO students (name, class, tokens) "
            "VALUES ('Test', '3E1', 10) RETURNING id"
        )
        await conn.execute(
            "INSERT INTO transactions (student_id, amount, type) VALUES ($1, 1, 'play')",
            student_id,
        )
    r = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("roster.csv", CSV_BASIC, "text/csv")},
    )
    assert r.status_code == 409
    assert "transactions exist" in r.json()["error"].lower()


async def test_rejects_bad_csv(admin_client) -> None:
    r = await admin_client.post(
        "/api/admin/upload-csv",
        files={"file": ("bad.csv", b"foo,bar\n1,2\n", "text/csv")},
    )
    assert r.status_code == 400
```

**Step 6: Run, watch it fail.**

**Step 7: Append to `routes/admin.py`**

```python
import secrets
from collections import Counter

from fastapi import UploadFile, File

from csv_import import CsvImportError, parse_roster_csv

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
```

**Step 8: Run tests**

```bash
pytest tests/test_csv_import.py tests/test_admin_csv_upload.py -v
```

Expected: PASS.

**Step 9: Commit**

```bash
git add csv_import.py routes/admin.py tests/test_csv_import.py tests/test_admin_csv_upload.py
git commit -m "feat: CSV roster upload with preview/confirm and tx-safety guard"
```

---

### Task 11: Resets (tokens, tallies, full)

**Files:**
- Modify: `routes/admin.py`
- Create: `tests/test_admin_resets.py`

**Step 1: Write failing test `tests/test_admin_resets.py`**

```python
import pytest


@pytest.fixture
async def admin_client(client, session_pool):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('A', '3E1', 5), ('B', '3E1', 0)"
        )
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play, tally) VALUES "
            "('R', '1111', 1, 30)"
        )
    return client


async def test_reset_tokens_sets_balance_to_default(
    admin_client, session_pool
) -> None:
    r = await admin_client.post("/api/admin/reset-tokens")
    assert r.status_code == 200
    async with session_pool.acquire() as conn:
        rows = await conn.fetch("SELECT tokens FROM students")
    assert {r["tokens"] for r in rows} == {20}


async def test_reset_tokens_logs_reset_transaction_per_student(
    admin_client, session_pool
) -> None:
    await admin_client.post("/api/admin/reset-tokens")
    async with session_pool.acquire() as conn:
        tx_types = await conn.fetch("SELECT type FROM transactions")
    assert {r["type"] for r in tx_types} == {"reset"}
    assert len(tx_types) == 2


async def test_reset_tallies_zeros_booths(admin_client, session_pool) -> None:
    r = await admin_client.post("/api/admin/reset-tallies")
    assert r.status_code == 200
    async with session_pool.acquire() as conn:
        tallies = await conn.fetch("SELECT tally FROM booths")
    assert {r["tally"] for r in tallies} == {0}


async def test_reset_tallies_does_not_delete_transactions(
    admin_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students LIMIT 1")
        await conn.execute(
            "INSERT INTO transactions (student_id, amount, type) "
            "VALUES ($1, 1, 'play')",
            sid,
        )
    await admin_client.post("/api/admin/reset-tallies")
    async with session_pool.acquire() as conn:
        c = await conn.fetchval("SELECT COUNT(*) FROM transactions")
    assert c == 1


async def test_full_reset_wipes_students_and_transactions(
    admin_client, session_pool
) -> None:
    r = await admin_client.post("/api/admin/full-reset")
    assert r.status_code == 200
    async with session_pool.acquire() as conn:
        students = await conn.fetchval("SELECT COUNT(*) FROM students")
        tx = await conn.fetchval("SELECT COUNT(*) FROM transactions")
        booths = await conn.fetchval("SELECT COUNT(*) FROM booths")
        tally = await conn.fetchval("SELECT SUM(tally) FROM booths")
    assert students == 0
    assert tx == 0
    assert booths == 1  # booths preserved
    assert (tally or 0) == 0
```

**Step 2: Run, watch it fail.**

**Step 3: Append to `routes/admin.py`**

```python
@router.post("/reset-tokens")
async def reset_tokens(
    request: Request, _: dict = Depends(require_admin)
) -> dict[str, int]:
    pool = request.app.state.pool
    default_tokens = int(
        await _get_setting(pool, "default_tokens", str(DEFAULT_TOKENS))
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            student_ids = await conn.fetch("SELECT id, tokens FROM students")
            await conn.execute("UPDATE students SET tokens = $1", default_tokens)
            await conn.executemany(
                "INSERT INTO transactions (student_id, amount, type, note) "
                "VALUES ($1, $2, 'reset', $3)",
                [(r["id"], default_tokens, f"prior balance: {r['tokens']}") for r in student_ids],
            )
    return {"affected": len(student_ids)}


@router.post("/reset-tallies")
async def reset_tallies(
    request: Request, _: dict = Depends(require_admin)
) -> dict[str, str]:
    async with request.app.state.pool.acquire() as conn:
        await conn.execute("UPDATE booths SET tally = 0")
    return {"status": "ok"}


@router.post("/full-reset")
async def full_reset(
    request: Request, _: dict = Depends(require_admin)
) -> dict[str, str]:
    async with request.app.state.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM transactions")
            await conn.execute("DELETE FROM students")
            await conn.execute("UPDATE booths SET tally = 0")
    return {"status": "ok"}
```

**Step 4: Run tests**

```bash
pytest tests/test_admin_resets.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add routes/admin.py tests/test_admin_resets.py
git commit -m "feat: admin reset endpoints (tokens, tallies, full)"
```

---

### Task 12: Export transactions CSV

**Files:**
- Modify: `routes/admin.py`
- Create: `tests/test_admin_export.py`

**Step 1: Write failing test `tests/test_admin_export.py`**

```python
import pytest


@pytest.fixture
async def admin_client(client, session_pool):
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval(
            "INSERT INTO students (name, class, tokens) "
            "VALUES ('Alice', '3E1', 10) RETURNING id"
        )
        bid = await conn.fetchval(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('Ring Toss', '1234', 1) RETURNING id"
        )
        await conn.execute(
            "INSERT INTO transactions (student_id, booth_id, amount, type) "
            "VALUES ($1, $2, 1, 'play')",
            sid, bid,
        )
    return client


async def test_export_returns_csv_with_expected_columns(admin_client) -> None:
    r = await admin_client.get("/api/admin/export-transactions")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.text
    header = text.splitlines()[0]
    assert header == "id,created_at,student_name,class,booth_name,type,amount,note"
    assert "Alice" in text
    assert "Ring Toss" in text
```

**Step 2: Run, watch it fail.**

**Step 3: Append to `routes/admin.py`**

```python
import csv as _csv
from fastapi.responses import StreamingResponse


@router.get("/export-transactions")
async def export_transactions(
    request: Request, _: dict = Depends(require_admin)
) -> StreamingResponse:
    async with request.app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id, t.created_at, s.name AS student_name, s.class AS klass,
                   b.name AS booth_name, t.type, t.amount, t.note
            FROM transactions t
            LEFT JOIN students s ON s.id = t.student_id
            LEFT JOIN booths   b ON b.id = t.booth_id
            ORDER BY t.id
            """
        )

    buf = io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(
        ["id", "created_at", "student_name", "class", "booth_name",
         "type", "amount", "note"]
    )
    for r in rows:
        writer.writerow([
            r["id"], r["created_at"].isoformat(),
            r["student_name"] or "", r["klass"] or "",
            r["booth_name"] or "", r["type"], r["amount"], r["note"] or "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )
```

Add `import io` at the top of `routes/admin.py` if not already there.

**Step 4: Run tests**

```bash
pytest tests/test_admin_export.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add routes/admin.py tests/test_admin_export.py
git commit -m "feat: export transactions as CSV"
```

---

## Phase 3 — Booth endpoints

### Task 13: Booth /me and /students with ETag cache

**Files:**
- Create: `routes/booth.py`
- Modify: `app.py`
- Create: `tests/test_booth_basic.py`

**Step 1: Write failing test `tests/test_booth_basic.py`**

```python
import pytest


@pytest.fixture
async def booth_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play, tally) "
            "VALUES ('Ring Toss', '1234', 2, 7)"
        )
        await conn.execute(
            "INSERT INTO students (name, class, tokens, is_absent) VALUES "
            "('Alice', '3E1', 10, FALSE), "
            "('Bob',   '3E2',  3, FALSE), "
            "('Carl',  '3N1',  5, TRUE)"
        )
    await client.post("/api/booth/login", json={"code": "1234"})
    return client


async def test_booth_me_returns_session_info(booth_client) -> None:
    r = await booth_client.get("/api/booth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Ring Toss"
    assert body["cost_per_play"] == 2
    assert body["tally"] == 7


async def test_students_returns_all_with_balance_and_absent(booth_client) -> None:
    r = await booth_client.get("/api/booth/students")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    bob = next(s for s in body if s["name"] == "Bob")
    assert bob["tokens"] == 3
    assert bob["is_absent"] is False
    carl = next(s for s in body if s["name"] == "Carl")
    assert carl["is_absent"] is True


async def test_students_etag_returns_304(booth_client) -> None:
    r1 = await booth_client.get("/api/booth/students")
    etag = r1.headers["etag"]
    r2 = await booth_client.get("/api/booth/students", headers={"If-None-Match": etag})
    assert r2.status_code == 304


async def test_booth_requires_booth_role(client) -> None:
    r = await client.get("/api/booth/students")
    assert r.status_code == 401
```

**Step 2: Run, watch it fail.**

**Step 3: Implement `routes/booth.py`**

```python
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
```

**Step 4: Register router in `app.py`**

```python
from routes.booth import router as booth_router
app.include_router(booth_router)
```

**Step 5: Run tests**

```bash
pytest tests/test_booth_basic.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add routes/booth.py app.py tests/test_booth_basic.py
git commit -m "feat: booth /me and /students with ETag cache"
```

---

### Task 14: Pay endpoint with row-level locking and race-safety test

This is the most critical task in the project. The race test must pass.

**Files:**
- Modify: `routes/booth.py`
- Create: `tests/test_booth_pay.py`
- Create: `tests/test_pay_race.py`

**Step 1: Write basic pay tests `tests/test_booth_pay.py`**

```python
import pytest


@pytest.fixture
async def booth_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) VALUES ('R', '1234', 2)"
        )
        await conn.execute(
            "INSERT INTO students (name, class, tokens, is_absent) VALUES "
            "('Alice', '3E1', 10, FALSE), "
            "('Bob',   '3E2',  1, FALSE), "
            "('Carl',  '3N1',  5, TRUE)"
        )
    await client.post("/api/booth/login", json={"code": "1234"})
    return client


async def test_pay_deducts_tokens_and_increments_tally(
    booth_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
    r = await booth_client.post("/api/booth/pay", json={"student_id": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["new_balance"] == 8
    assert body["transaction_id"]
    async with session_pool.acquire() as conn:
        tokens = await conn.fetchval("SELECT tokens FROM students WHERE id = $1", sid)
        tally = await conn.fetchval("SELECT tally FROM booths WHERE code = '1234'")
        tx = await conn.fetchrow("SELECT type, amount FROM transactions WHERE id = $1", body["transaction_id"])
    assert tokens == 8
    assert tally == 2
    assert tx["type"] == "play"
    assert tx["amount"] == 2


async def test_pay_insufficient_tokens_rejected(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Bob'")
    r = await booth_client.post("/api/booth/pay", json={"student_id": sid})
    assert r.status_code == 409
    assert "insufficient" in r.json()["error"].lower()


async def test_pay_absent_rejected(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Carl'")
    r = await booth_client.post("/api/booth/pay", json={"student_id": sid})
    assert r.status_code == 409
    assert "absent" in r.json()["error"].lower()


async def test_pay_unknown_student(booth_client) -> None:
    r = await booth_client.post("/api/booth/pay", json={"student_id": 99999})
    assert r.status_code == 404
```

**Step 2: Run, watch it fail.**

**Step 3: Append to `routes/booth.py`**

```python
from pydantic import BaseModel


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
    return {"transaction_id": tx_id, "new_balance": new_balance}
```

Also bust the students cache after a pay (later in undo too). Add at top of `routes/booth.py`:

```python
def _invalidate_students_cache() -> None:
    _students_cache.pop("v", None)
```

Call it inside `pay` after the `COMMIT` (after `async with conn.transaction(): ...` block ends). Actually since the balance affects the cached list, we should bust it:

```python
    _invalidate_students_cache()
    return {"transaction_id": tx_id, "new_balance": new_balance}
```

**Step 4: Run basic tests**

```bash
pytest tests/test_booth_pay.py -v
```

Expected: PASS.

**Step 5: Write the critical race test `tests/test_pay_race.py`**

```python
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient


async def _make_client():
    from app import app
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), transport


async def test_concurrent_pays_serialized_by_for_update(session_pool) -> None:
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('R', '1234', 1)"
        )
        sid = await conn.fetchval(
            "INSERT INTO students (name, class, tokens) "
            "VALUES ('Alice', '3E1', 1) RETURNING id"
        )

    # 20 concurrent pay requests; only 1 should succeed (balance == 1, cost == 1).
    async def one_pay() -> int:
        client, _ = await _make_client()
        async with client:
            await client.post("/api/booth/login", json={"code": "1234"})
            r = await client.post("/api/booth/pay", json={"student_id": sid})
            return r.status_code

    statuses = await asyncio.gather(*[one_pay() for _ in range(20)])
    successes = sum(1 for s in statuses if s == 200)
    failures = sum(1 for s in statuses if s == 409)
    assert successes == 1, f"Expected exactly 1 success, got {successes}: {statuses}"
    assert failures == 19

    async with session_pool.acquire() as conn:
        tokens = await conn.fetchval("SELECT tokens FROM students WHERE id = $1", sid)
        tx_count = await conn.fetchval(
            "SELECT COUNT(*) FROM transactions WHERE type = 'play'"
        )
    assert tokens == 0
    assert tx_count == 1
```

**Step 6: Run race test**

```bash
pytest tests/test_pay_race.py -v
```

Expected: PASS. If it fails (e.g. 2 successes), the `FOR UPDATE` lock isn't doing its job — re-check that `SELECT ... FOR UPDATE` runs inside the `conn.transaction()` block and that the balance check happens *after* the lock acquires.

**Step 7: Commit**

```bash
git add routes/booth.py tests/test_booth_pay.py tests/test_pay_race.py
git commit -m "feat: race-safe pay endpoint with SELECT FOR UPDATE"
```

---

### Task 15: Undo endpoint

**Files:**
- Modify: `routes/booth.py`
- Create: `tests/test_booth_undo.py`

**Step 1: Write failing test `tests/test_booth_undo.py`**

```python
import pytest


@pytest.fixture
async def booth_setup(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('R', '1234', 2)"
        )
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('B', '5678', 1)"
        )
        sid = await conn.fetchval(
            "INSERT INTO students (name, class, tokens) "
            "VALUES ('Alice', '3E1', 10) RETURNING id"
        )
    await client.post("/api/booth/login", json={"code": "1234"})
    pr = await client.post("/api/booth/pay", json={"student_id": sid})
    return client, sid, pr.json()["transaction_id"]


async def test_undo_within_60s(booth_setup, session_pool) -> None:
    client, sid, tx_id = booth_setup
    r = await client.post("/api/booth/undo", json={"transaction_id": tx_id})
    assert r.status_code == 200
    async with session_pool.acquire() as conn:
        tokens = await conn.fetchval(
            "SELECT tokens FROM students WHERE id = $1", sid
        )
        tally = await conn.fetchval("SELECT tally FROM booths WHERE code = '1234'")
        reversed_by = await conn.fetchval(
            "SELECT reversed_by FROM transactions WHERE id = $1", tx_id
        )
        undo_row = await conn.fetchrow(
            "SELECT type, amount FROM transactions WHERE id = $1", reversed_by
        )
    assert tokens == 10
    assert tally == 0
    assert undo_row["type"] == "undo"
    assert undo_row["amount"] == 2


async def test_undo_after_60s_rejected(booth_setup, session_pool) -> None:
    client, _, tx_id = booth_setup
    async with session_pool.acquire() as conn:
        await conn.execute(
            "UPDATE transactions SET created_at = NOW() - INTERVAL '61 seconds' "
            "WHERE id = $1",
            tx_id,
        )
    r = await client.post("/api/booth/undo", json={"transaction_id": tx_id})
    assert r.status_code == 409


async def test_undo_twice_rejected(booth_setup) -> None:
    client, _, tx_id = booth_setup
    await client.post("/api/booth/undo", json={"transaction_id": tx_id})
    r = await client.post("/api/booth/undo", json={"transaction_id": tx_id})
    assert r.status_code == 409


async def test_undo_wrong_booth_rejected(booth_setup) -> None:
    client, _, tx_id = booth_setup
    await client.post("/api/booth/logout")
    await client.post("/api/booth/login", json={"code": "5678"})
    r = await client.post("/api/booth/undo", json={"transaction_id": tx_id})
    assert r.status_code == 403


async def test_undo_unknown_tx(booth_setup) -> None:
    client, _, _ = booth_setup
    r = await client.post("/api/booth/undo", json={"transaction_id": 99999})
    assert r.status_code == 404
```

**Step 2: Run, watch it fail.**

**Step 3: Append to `routes/booth.py`**

```python
class UndoBody(BaseModel):
    transaction_id: int


@router.post("/undo")
async def undo(
    body: UndoBody, request: Request, session: dict = Depends(require_booth)
) -> dict[str, Any]:
    booth_id = session["booth_id"]
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                """
                SELECT id, student_id, booth_id, amount, type, reversed_by,
                       created_at, EXTRACT(EPOCH FROM (NOW() - created_at)) AS age
                FROM transactions WHERE id = $1 FOR UPDATE
                """,
                body.transaction_id,
            )
            if not tx:
                raise _api_error("Transaction not found", "TX_NOT_FOUND", 404)
            if tx["booth_id"] != booth_id:
                raise _api_error("Wrong booth", "WRONG_BOOTH", 403)
            if tx["type"] != "play":
                raise _api_error("Only plays can be undone", "NOT_A_PLAY", 409)
            if tx["reversed_by"] is not None:
                raise _api_error("Already undone", "ALREADY_UNDONE", 409)
            if tx["age"] > 60:
                raise _api_error("Undo window expired (60s)", "UNDO_EXPIRED", 409)

            await conn.execute(
                "SELECT id FROM students WHERE id = $1 FOR UPDATE",
                tx["student_id"],
            )
            await conn.execute(
                "UPDATE students SET tokens = tokens + $1 WHERE id = $2",
                tx["amount"], tx["student_id"],
            )
            await conn.execute(
                "UPDATE booths SET tally = tally - $1 WHERE id = $2",
                tx["amount"], booth_id,
            )
            undo_id = await conn.fetchval(
                """
                INSERT INTO transactions (student_id, booth_id, amount, type, note)
                VALUES ($1, $2, $3, 'undo', $4)
                RETURNING id
                """,
                tx["student_id"], booth_id, tx["amount"],
                f"undo of tx #{body.transaction_id}",
            )
            await conn.execute(
                "UPDATE transactions SET reversed_by = $1 WHERE id = $2",
                undo_id, body.transaction_id,
            )
    _invalidate_students_cache()
    return {"undo_transaction_id": undo_id}
```

**Step 4: Run tests**

```bash
pytest tests/test_booth_undo.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add routes/booth.py tests/test_booth_undo.py
git commit -m "feat: booth undo with 60s window and audit trail"
```

---

### Task 16: Booth recent + stats

**Files:**
- Modify: `routes/booth.py`
- Create: `tests/test_booth_recent_stats.py`

**Step 1: Write failing test `tests/test_booth_recent_stats.py`**

```python
import pytest


@pytest.fixture
async def booth_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) VALUES ('R', '1234', 1)"
        )
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('A1', '3E1', 10), ('A2', '3E1', 10), ('A3', '3E1', 10), "
            "('B1', '3N2', 10), ('B2', '3N2', 10)"
        )
    await client.post("/api/booth/login", json={"code": "1234"})
    return client


async def test_recent_returns_last_5_plays_only(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sids = [r["id"] for r in await conn.fetch("SELECT id FROM students ORDER BY id")]
    for sid in sids:
        await booth_client.post("/api/booth/pay", json={"student_id": sid})
    r = await booth_client.get("/api/booth/recent")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 5
    assert items[0]["student_name"] == "B2"  # most recent first
    assert "transaction_id" in items[0]


async def test_recent_excludes_undone(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students LIMIT 1")
    pr = await booth_client.post("/api/booth/pay", json={"student_id": sid})
    await booth_client.post(
        "/api/booth/undo", json={"transaction_id": pr.json()["transaction_id"]}
    )
    r = await booth_client.get("/api/booth/recent")
    assert r.json() == []


async def test_stats_returns_tally_and_class_breakdown(
    booth_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id FROM students ORDER BY id")
    for r in rows[:3]:  # 3 from 3E1
        await booth_client.post("/api/booth/pay", json={"student_id": r["id"]})
    for r in rows[3:]:  # 2 from 3N2
        await booth_client.post("/api/booth/pay", json={"student_id": r["id"]})
    r = await booth_client.get("/api/booth/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["tally"] == 5
    assert body["tx_count"] == 5
    breakdown = {row["class"]: row["count"] for row in body["by_class"]}
    assert breakdown == {"3E1": 3, "3N2": 2}
```

**Step 2: Run, watch it fail.**

**Step 3: Append to `routes/booth.py`**

```python
@router.get("/recent")
async def recent(
    request: Request, session: dict = Depends(require_booth)
) -> list[dict[str, Any]]:
    async with request.app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.id AS transaction_id, t.amount, t.created_at,
                   s.name AS student_name, s.class AS klass,
                   EXTRACT(EPOCH FROM (NOW() - t.created_at)) AS age
            FROM transactions t
            JOIN students s ON s.id = t.student_id
            WHERE t.booth_id = $1 AND t.type = 'play' AND t.reversed_by IS NULL
            ORDER BY t.id DESC LIMIT 5
            """,
            session["booth_id"],
        )
    return [
        {
            "transaction_id": r["transaction_id"],
            "amount": r["amount"],
            "student_name": r["student_name"],
            "class": r["klass"],
            "age_seconds": int(r["age"]),
        }
        for r in rows
    ]


@router.get("/stats")
async def stats(
    request: Request, session: dict = Depends(require_booth)
) -> dict[str, Any]:
    booth_id = session["booth_id"]
    async with request.app.state.pool.acquire() as conn:
        booth = await conn.fetchrow(
            "SELECT tally FROM booths WHERE id = $1", booth_id
        )
        tx_count = await conn.fetchval(
            "SELECT COUNT(*) FROM transactions "
            "WHERE booth_id = $1 AND type = 'play' AND reversed_by IS NULL",
            booth_id,
        )
        by_class = await conn.fetch(
            """
            SELECT s.class AS class, COUNT(*) AS count
            FROM transactions t JOIN students s ON s.id = t.student_id
            WHERE t.booth_id = $1 AND t.type = 'play' AND t.reversed_by IS NULL
            GROUP BY s.class ORDER BY count DESC
            """,
            booth_id,
        )
    return {
        "tally": booth["tally"] if booth else 0,
        "tx_count": tx_count,
        "by_class": [{"class": r["class"], "count": r["count"]} for r in by_class],
    }
```

**Step 4: Run tests**

```bash
pytest tests/test_booth_recent_stats.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add routes/booth.py tests/test_booth_recent_stats.py
git commit -m "feat: booth recent plays and stats endpoints"
```

---

## Phase 4 — Teacher endpoints

### Task 17: Teacher search + student detail

**Files:**
- Create: `routes/teacher.py`
- Modify: `app.py`
- Create: `tests/test_teacher_search.py`

**Step 1: Write failing test `tests/test_teacher_search.py`**

```python
import pytest


@pytest.fixture
async def teacher_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('Alice', '3E1', 10), ('Bob', '3E2', 5)"
        )
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play) "
            "VALUES ('R', '1234', 1)"
        )
    await client.post("/api/teacher/login", json={"password": "test-teacher-pw"})
    return client


async def test_teacher_lists_students(teacher_client) -> None:
    r = await teacher_client.get("/api/teacher/students")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert names == {"Alice", "Bob"}


async def test_teacher_student_detail_includes_history(
    teacher_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
        bid = await conn.fetchval("SELECT id FROM booths LIMIT 1")
        await conn.execute(
            "INSERT INTO transactions (student_id, booth_id, amount, type) "
            "VALUES ($1, $2, 1, 'play')",
            sid, bid,
        )
    r = await teacher_client.get(f"/api/teacher/student/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Alice"
    assert body["tokens"] == 10
    assert len(body["history"]) == 1
    assert body["history"][0]["type"] == "play"


async def test_teacher_endpoints_require_teacher_or_admin(client) -> None:
    r = await client.get("/api/teacher/students")
    assert r.status_code == 401
```

**Step 2: Run, watch it fail.**

**Step 3: Implement `routes/teacher.py`**

```python
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
```

**Step 4: Register router in `app.py`**

```python
from routes.teacher import router as teacher_router
app.include_router(teacher_router)
```

**Step 5: Run tests**

```bash
pytest tests/test_teacher_search.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add routes/teacher.py app.py tests/test_teacher_search.py
git commit -m "feat: teacher student list and detail endpoints"
```

---

### Task 18: Refund + manual deduct

**Files:**
- Modify: `routes/teacher.py`
- Create: `tests/test_teacher_balance_ops.py`

**Step 1: Write failing test `tests/test_teacher_balance_ops.py`**

```python
import pytest


@pytest.fixture
async def teacher_client_with_student(client, session_pool):
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval(
            "INSERT INTO students (name, class, tokens) "
            "VALUES ('Alice', '3E1', 10) RETURNING id"
        )
    await client.post("/api/teacher/login", json={"password": "test-teacher-pw"})
    return client, sid


async def test_refund_adds_tokens_and_logs(
    teacher_client_with_student, session_pool
) -> None:
    client, sid = teacher_client_with_student
    r = await client.post(
        "/api/teacher/refund",
        json={"student_id": sid, "amount": 3, "note": "Ride broke"},
    )
    assert r.status_code == 200
    assert r.json()["new_balance"] == 13
    async with session_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT type, amount, note FROM transactions WHERE student_id = $1",
            sid,
        )
    assert row["type"] == "refund"
    assert row["amount"] == 3
    assert row["note"] == "Ride broke"


async def test_deduct_subtracts_tokens(teacher_client_with_student) -> None:
    client, sid = teacher_client_with_student
    r = await client.post(
        "/api/teacher/deduct",
        json={"student_id": sid, "amount": 2, "note": "Missed pay catch"},
    )
    assert r.status_code == 200
    assert r.json()["new_balance"] == 8


async def test_refund_requires_note(teacher_client_with_student) -> None:
    client, sid = teacher_client_with_student
    r = await client.post(
        "/api/teacher/refund",
        json={"student_id": sid, "amount": 3, "note": "ab"},
    )
    assert r.status_code == 422  # pydantic min_length


async def test_refund_amount_must_be_positive(teacher_client_with_student) -> None:
    client, sid = teacher_client_with_student
    r = await client.post(
        "/api/teacher/refund",
        json={"student_id": sid, "amount": 0, "note": "test"},
    )
    assert r.status_code == 422


async def test_deduct_cannot_make_negative(teacher_client_with_student) -> None:
    client, sid = teacher_client_with_student
    r = await client.post(
        "/api/teacher/deduct",
        json={"student_id": sid, "amount": 999, "note": "huge"},
    )
    assert r.status_code == 409
```

**Step 2: Run, watch it fail.**

**Step 3: Append to `routes/teacher.py`**

```python
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
```

Also invalidate the booth students cache after refund/deduct. At the bottom of `_apply_balance_change`, add:

```python
    # cache lives in routes.booth; import lazily to avoid circular import
    from routes.booth import _invalidate_students_cache
    _invalidate_students_cache()
```

**Step 4: Run tests**

```bash
pytest tests/test_teacher_balance_ops.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add routes/teacher.py tests/test_teacher_balance_ops.py
git commit -m "feat: teacher refund and deduct with required note"
```

---

### Task 19: Absent toggle + bulk + by-class

**Files:**
- Modify: `routes/teacher.py`
- Create: `tests/test_teacher_absent.py`

**Step 1: Write failing test `tests/test_teacher_absent.py`**

```python
import pytest


@pytest.fixture
async def teacher_client(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('Alice', '3E1', 10), ('Bob', '3E1', 10), ('Carl', '3N1', 10)"
        )
    await client.post("/api/teacher/login", json={"password": "test-teacher-pw"})
    return client


async def test_absent_single(teacher_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
    r = await teacher_client.post(
        "/api/teacher/absent", json={"student_id": sid, "is_absent": True}
    )
    assert r.status_code == 200
    async with session_pool.acquire() as conn:
        absent = await conn.fetchval(
            "SELECT is_absent FROM students WHERE id = $1", sid
        )
    assert absent is True


async def test_absent_bulk(teacher_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        ids = [r["id"] for r in await conn.fetch("SELECT id FROM students")]
    r = await teacher_client.post(
        "/api/teacher/absent-bulk",
        json={"student_ids": ids, "is_absent": True},
    )
    assert r.status_code == 200
    assert r.json()["affected"] == 3


async def test_students_by_class_groups_correctly(teacher_client) -> None:
    r = await teacher_client.get("/api/teacher/students-by-class")
    assert r.status_code == 200
    body = r.json()
    by_class = {entry["class"]: entry["students"] for entry in body}
    assert sorted(s["name"] for s in by_class["3E1"]) == ["Alice", "Bob"]
    assert [s["name"] for s in by_class["3N1"]] == ["Carl"]
```

**Step 2: Run, watch it fail.**

**Step 3: Append to `routes/teacher.py`**

```python
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
```

**Step 4: Run tests**

```bash
pytest tests/test_teacher_absent.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add routes/teacher.py tests/test_teacher_absent.py
git commit -m "feat: teacher absent flag, bulk absent, and by-class listing"
```

---

## Phase 5 — Summary

### Task 20: Summary endpoints

**Files:**
- Create: `routes/summary.py`
- Modify: `app.py`
- Create: `tests/test_summary.py`

**Step 1: Write failing test `tests/test_summary.py`**

```python
import pytest


@pytest.fixture
async def admin_with_data(client, session_pool):
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens) VALUES "
            "('A1', '3E1', 5),  ('A2', '3E1', 0), ('B1', '3N2', 10)"
        )
        await conn.execute(
            "INSERT INTO booths (name, code, cost_per_play, tally) VALUES "
            "('Ring',  '1111', 1, 12), "
            "('Darts', '2222', 2, 4)"
        )
        # Two plays for ring (one each class), one play for darts (3E1)
        sids = await conn.fetch("SELECT id, class FROM students ORDER BY name")
        bring = await conn.fetchval("SELECT id FROM booths WHERE name='Ring'")
        bdarts = await conn.fetchval("SELECT id FROM booths WHERE name='Darts'")
        await conn.execute(
            "INSERT INTO transactions (student_id, booth_id, amount, type) VALUES "
            "($1,$2,1,'play'), ($3,$2,1,'play'), ($1,$4,2,'play')",
            sids[0]["id"], bring, sids[2]["id"], bdarts,
        )
    await client.post("/api/admin/login", json={"password": "test-admin-pw"})
    return client


async def test_summary_by_class(admin_with_data) -> None:
    r = await admin_with_data.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    by_class = {row["class"]: row for row in body["by_class"]}
    assert by_class["3E1"]["total_students"] == 2
    assert by_class["3E1"]["total_remaining"] == 5
    by_booth = {row["name"]: row for row in body["by_booth"]}
    assert by_booth["Ring"]["tally"] == 12
    assert by_booth["Ring"]["top_class"] in {"3E1", "3N2"}


async def test_top_spenders(admin_with_data) -> None:
    r = await admin_with_data.get("/api/summary/top-spenders")
    assert r.status_code == 200
    body = r.json()
    assert len(body) <= 10
    assert "name" in body[0]
    assert "spent" in body[0]
```

**Step 2: Run, watch it fail.**

**Step 3: Implement `routes/summary.py`**

```python
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
```

**Step 4: Register in `app.py`**

```python
from routes.summary import router as summary_router
app.include_router(summary_router)
```

**Step 5: Run tests**

```bash
pytest tests/test_summary.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add routes/summary.py app.py tests/test_summary.py
git commit -m "feat: summary endpoints (by-class, by-booth, top spenders)"
```

---

## Phase 6 — Frontend

For frontend tasks, manual smoke testing (curl + browser) substitutes for unit tests. Keep `static/*.html` self-contained. Tailwind via CDN.

### Task 21: Static serving + shared.js + admin page (UI v1)

**Files:**
- Modify: `app.py`
- Create: `static/shared.js`
- Create: `static/admin.html`

**Step 1: Wire static mount in `app.py`**

Add after the routers:

```python
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/admin")


for _path, _file in (
    ("/admin", "admin.html"),
    ("/booth", "booth.html"),
    ("/teacher", "teacher.html"),
    ("/summary", "summary.html"),
):
    def _make(file: str):
        async def serve() -> FileResponse:
            return FileResponse(f"static/{file}")
        return serve
    app.get(_path, include_in_schema=False)(_make(_file))
```

**Step 2: Write `static/shared.js`**

```javascript
// Tiny shared helpers: api(), toast(), confirmModal()
window.api = async function api(method, url, body) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    if (body instanceof FormData) {
      opts.body = body;
    } else {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
  }
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch (_) {}
  if (!r.ok) {
    const msg = (data && (data.error || data.detail?.error || data.detail)) || `HTTP ${r.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
};

window.toast = function toast(msg, kind = 'success') {
  const el = document.createElement('div');
  const color = { success: 'bg-green-600', error: 'bg-red-600', warn: 'bg-amber-600' }[kind];
  el.className = `${color} text-white px-4 py-2 rounded shadow-lg fixed top-4 left-1/2 -translate-x-1/2 z-50`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2000);
};

window.confirmModal = function confirmModal({ title, body, confirmLabel = 'Confirm', confirmKind = 'green' }) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-40';
    overlay.innerHTML = `
      <div class="bg-white rounded-lg shadow-xl p-6 max-w-sm w-11/12">
        <h3 class="text-lg font-bold mb-2">${title}</h3>
        <div class="mb-4">${body}</div>
        <div class="flex gap-2 justify-end">
          <button class="px-4 py-2 rounded bg-gray-200" data-act="cancel">Cancel</button>
          <button class="px-4 py-2 rounded bg-${confirmKind}-600 text-white" data-act="ok">${confirmLabel}</button>
        </div>
      </div>`;
    overlay.addEventListener('click', e => {
      if (e.target.dataset.act === 'ok') { overlay.remove(); resolve(true); }
      else if (e.target.dataset.act === 'cancel' || e.target === overlay) { overlay.remove(); resolve(false); }
    });
    document.body.appendChild(overlay);
  });
};
```

**Step 3: Write `static/admin.html`**

Minimal but functional. Includes Tailwind CDN, login form, four tabs (Settings, Roster, Booths, Resets), and an Export button.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Carnival · Admin</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="/static/shared.js" defer></script>
</head>
<body class="bg-gray-50 text-gray-900">
  <div id="app" class="max-w-3xl mx-auto p-4"></div>
  <script>
    document.addEventListener('DOMContentLoaded', () => {
      const app = document.getElementById('app');
      let state = { tab: 'settings', settings: null, booths: [] };

      function render() {
        app.innerHTML = `
          <header class="flex justify-between items-center mb-6">
            <h1 class="text-2xl font-bold">Admin · ${state.settings?.carnival_name || 'Carnival'}</h1>
            <button id="export" class="text-sm bg-gray-200 px-3 py-1 rounded">Export CSV</button>
          </header>
          <nav class="flex gap-2 mb-4">
            ${['settings','roster','booths','resets'].map(t =>
              `<button data-tab="${t}" class="px-3 py-1 rounded ${state.tab===t?'bg-blue-600 text-white':'bg-gray-200'}">${t}</button>`
            ).join('')}
          </nav>
          <div id="panel"></div>
        `;
        document.getElementById('export').onclick = () => location.href = '/api/admin/export-transactions';
        for (const btn of app.querySelectorAll('[data-tab]')) {
          btn.onclick = () => { state.tab = btn.dataset.tab; renderPanel(); };
        }
        renderPanel();
      }

      function renderPanel() {
        const panel = document.getElementById('panel');
        if (state.tab === 'settings') {
          panel.innerHTML = `
            <label class="block mb-2">Carnival name
              <input id="cn" class="w-full border rounded p-2" value="${state.settings?.carnival_name || ''}">
            </label>
            <label class="block mb-4">Default tokens
              <input id="dt" type="number" min="1" class="w-full border rounded p-2" value="${state.settings?.default_tokens || 20}">
            </label>
            <button id="save" class="bg-blue-600 text-white px-4 py-2 rounded">Save</button>`;
          document.getElementById('save').onclick = async () => {
            try {
              await api('POST', '/api/admin/settings', {
                carnival_name: document.getElementById('cn').value,
                default_tokens: parseInt(document.getElementById('dt').value, 10),
              });
              toast('Settings saved');
              await loadSettings();
              render();
            } catch (e) { toast(e.message, 'error'); }
          };
        } else if (state.tab === 'roster') {
          renderRosterTab(panel);
        } else if (state.tab === 'booths') {
          renderBoothsTab(panel);
        } else if (state.tab === 'resets') {
          renderResetsTab(panel);
        }
      }

      function renderRosterTab(panel) {
        panel.innerHTML = `
          <input type="file" id="csv" accept=".csv" class="block mb-3">
          <button id="preview" class="bg-blue-600 text-white px-4 py-2 rounded">Preview</button>
          <div id="preview-out" class="mt-4"></div>`;
        document.getElementById('preview').onclick = async () => {
          const f = document.getElementById('csv').files[0];
          if (!f) return toast('Pick a file', 'warn');
          const fd = new FormData(); fd.append('file', f);
          try {
            const res = await api('POST', '/api/admin/upload-csv', fd);
            const out = document.getElementById('preview-out');
            out.innerHTML = `
              <p class="mb-2"><strong>${res.row_count}</strong> students will be imported (current data will be wiped).</p>
              <ul class="mb-3 text-sm">${res.by_class.map(([c,n]) => `<li>${c}: ${n}</li>`).join('')}</ul>
              <button id="confirm" class="bg-red-600 text-white px-4 py-2 rounded">Confirm import</button>`;
            document.getElementById('confirm').onclick = async () => {
              const ok = await confirmModal({
                title: 'Wipe and import?',
                body: `This will replace all existing students with ${res.row_count} new entries.`,
                confirmLabel: 'Yes, import', confirmKind: 'red',
              });
              if (!ok) return;
              try {
                const c = await api('POST', '/api/admin/upload-csv/confirm', { token: res.token });
                toast(`Imported ${c.inserted} students`);
                out.innerHTML = '';
              } catch (e) { toast(e.message, 'error'); }
            };
          } catch (e) { toast(e.message, 'error'); }
        };
      }

      function renderBoothsTab(panel) {
        panel.innerHTML = `
          <div class="mb-4 flex gap-2">
            <input id="bn" placeholder="Name" class="border rounded p-2 flex-1">
            <input id="bc" placeholder="Code (4-6 digit)" class="border rounded p-2 w-32">
            <input id="bp" type="number" min="1" value="1" class="border rounded p-2 w-20">
            <button id="add" class="bg-blue-600 text-white px-3 rounded">Add</button>
          </div>
          <table class="w-full text-sm">
            <thead><tr class="text-left border-b"><th>Name</th><th>Code</th><th>Cost</th><th>Tally</th><th></th></tr></thead>
            <tbody>${state.booths.map(b => `
              <tr class="border-b" data-id="${b.id}">
                <td>${b.name}</td><td>${b.code}</td><td>${b.cost_per_play}</td><td>${b.tally}</td>
                <td><button class="text-red-600" data-del="${b.id}">Delete</button></td>
              </tr>`).join('')}</tbody>
          </table>`;
        document.getElementById('add').onclick = async () => {
          try {
            await api('POST', '/api/admin/booths', {
              name: document.getElementById('bn').value,
              code: document.getElementById('bc').value,
              cost_per_play: parseInt(document.getElementById('bp').value, 10),
            });
            toast('Booth added');
            await loadBooths();
            renderPanel();
          } catch (e) { toast(e.message, 'error'); }
        };
        for (const btn of panel.querySelectorAll('[data-del]')) {
          btn.onclick = async () => {
            const id = btn.dataset.del;
            const ok = await confirmModal({ title: 'Delete booth?', body: 'This cannot be undone.', confirmLabel: 'Delete', confirmKind: 'red' });
            if (!ok) return;
            await api('DELETE', '/api/admin/booths/' + id);
            await loadBooths(); renderPanel();
          };
        }
      }

      function renderResetsTab(panel) {
        panel.innerHTML = `
          <div class="space-y-3">
            <button id="rt" class="w-full bg-amber-500 text-white py-3 rounded">Reset all student tokens to default</button>
            <button id="ra" class="w-full bg-amber-500 text-white py-3 rounded">Reset booth tallies</button>
            <button id="fr" class="w-full bg-red-600 text-white py-3 rounded">Full reset (wipe everything)</button>
          </div>`;
        const guarded = (id, body, url, kind = 'red') => {
          document.getElementById(id).onclick = async () => {
            const carnivalName = state.settings?.carnival_name || 'Carnival';
            const typed = prompt(`Type "${carnivalName}" to confirm.`);
            if (typed !== carnivalName) return toast('Confirmation failed', 'warn');
            try {
              await api('POST', url);
              toast('Done');
              await loadAll(); render();
            } catch (e) { toast(e.message, 'error'); }
          };
        };
        guarded('rt', 'Reset tokens to default for ALL students?', '/api/admin/reset-tokens');
        guarded('ra', 'Reset all booth tallies to zero?', '/api/admin/reset-tallies');
        guarded('fr', 'Wipe all students and transactions?', '/api/admin/full-reset');
      }

      async function loadSettings() { state.settings = await api('GET', '/api/admin/settings'); }
      async function loadBooths()   { state.booths   = await api('GET', '/api/admin/booths'); }
      async function loadAll() { await Promise.all([loadSettings(), loadBooths()]); }

      async function showLogin() {
        app.innerHTML = `
          <div class="max-w-xs mx-auto mt-12">
            <h1 class="text-xl font-bold mb-4">Admin login</h1>
            <input id="pw" type="password" placeholder="Password" class="w-full border rounded p-2 mb-3">
            <button id="login" class="w-full bg-blue-600 text-white py-2 rounded">Log in</button>
          </div>`;
        document.getElementById('login').onclick = async () => {
          try {
            await api('POST', '/api/admin/login', { password: document.getElementById('pw').value });
            await loadAll(); render();
          } catch (e) { toast(e.message, 'error'); }
        };
      }

      (async () => {
        try { await loadAll(); render(); }
        catch { showLogin(); }
      })();
    });
  </script>
</body>
</html>
```

**Step 4: Smoke test in browser**

```bash
docker compose up -d
export $(grep -v '^#' .env.example | xargs)
uvicorn app:app --reload --port 8000
```

Open `http://localhost:8000/admin`, log in with `change-me-admin`, set carnival name, add a booth.

**Step 5: Commit**

```bash
git add app.py static/
git commit -m "feat: admin HTML page with settings, roster, booths, resets"
```

---

### Task 22: Booth HTML page

**Files:**
- Create: `static/booth.html`

**Step 1: Write `static/booth.html`**

Mobile-first layout: login → header card with live tally + tx count → debounced search + results → confirm modal → recent strip with undo → stats drawer.

(Length is similar to admin.html; same structure: `api()` calls, `confirmModal()` for pay, `setInterval` for 30s stats poll, `setInterval` for 1s recent-strip age tick.)

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Carnival · Booth</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="/static/shared.js" defer></script>
</head>
<body class="bg-gray-50 text-gray-900">
  <div id="app" class="max-w-md mx-auto p-3"></div>
  <script>
    document.addEventListener('DOMContentLoaded', () => {
      const app = document.getElementById('app');
      let me = null, students = [], recent = [], stats = null, etag = null;
      let payLock = false;

      async function loadStudents() {
        const headers = etag ? { 'If-None-Match': etag } : {};
        const r = await fetch('/api/booth/students', { credentials: 'same-origin', headers });
        if (r.status === 304) return;
        etag = r.headers.get('etag');
        students = await r.json();
      }
      async function loadMe()     { me = await api('GET', '/api/booth/me'); }
      async function loadRecent() { recent = await api('GET', '/api/booth/recent'); }
      async function loadStats()  { stats = await api('GET', '/api/booth/stats'); }

      function renderHeader() {
        return `
          <header class="bg-blue-600 text-white rounded-lg p-4 sticky top-0 z-10 mb-3">
            <div class="text-xl font-bold">${me.name}</div>
            <div class="grid grid-cols-3 gap-2 mt-2 text-center text-sm">
              <div><div class="text-2xl font-bold">${me.tally}</div><div>tokens</div></div>
              <div><div class="text-2xl font-bold">${me.tx_count}</div><div>plays</div></div>
              <div><div class="text-2xl font-bold">${me.cost_per_play}</div><div>per play</div></div>
            </div>
            <button id="logout" class="absolute top-2 right-2 text-xs underline">log out</button>
          </header>`;
      }

      function renderSearch() {
        return `
          <input id="q" placeholder="Search name or class…" class="w-full border-2 rounded-lg p-3 text-lg mb-3" autocomplete="off">
          <div id="results" class="space-y-1"></div>`;
      }

      function renderRecent() {
        const items = recent.filter(r => r.age_seconds < 60);
        if (!items.length) return '';
        return `
          <div class="mt-4 bg-white border rounded-lg p-3">
            <div class="text-xs uppercase text-gray-500 mb-2">Recent plays</div>
            ${items.map(r => `
              <div class="flex justify-between items-center py-1">
                <span>${r.student_name} <span class="text-gray-500">(${r.class})</span> · −${r.amount}</span>
                <button class="text-red-600 text-sm underline" data-undo="${r.transaction_id}">Undo (${60 - r.age_seconds}s)</button>
              </div>`).join('')}
          </div>`;
      }

      function renderStats() {
        if (!stats) return '';
        return `
          <details class="mt-4">
            <summary class="cursor-pointer text-sm text-gray-600">Today's stats by class</summary>
            <div class="mt-2 flex flex-wrap gap-2">
              ${stats.by_class.map(c => `<span class="bg-gray-200 px-2 py-1 rounded text-sm">${c.class}: ${c.count}</span>`).join('')}
            </div>
          </details>`;
      }

      function render() {
        app.innerHTML = renderHeader() + renderSearch() + `<div id="recent">${renderRecent()}</div>` + `<div id="stats">${renderStats()}</div>`;
        document.getElementById('logout').onclick = async () => {
          await api('POST', '/api/booth/logout'); location.reload();
        };
        const q = document.getElementById('q');
        let timer;
        q.addEventListener('input', () => {
          clearTimeout(timer);
          timer = setTimeout(async () => {
            await loadStudents();
            renderResults(q.value.trim().toLowerCase());
          }, 200);
        });
        for (const btn of document.querySelectorAll('[data-undo]')) {
          btn.onclick = async () => {
            try {
              await api('POST', '/api/booth/undo', { transaction_id: parseInt(btn.dataset.undo, 10) });
              toast('Undone');
              await Promise.all([loadMe(), loadRecent(), loadStudents()]);
              render();
            } catch (e) { toast(e.message, 'error'); }
          };
        }
      }

      function renderResults(query) {
        const out = document.getElementById('results');
        const q = query.toLowerCase();
        const matches = !q ? [] : students.filter(s =>
          s.name.toLowerCase().includes(q) || s.class.toLowerCase().includes(q)
        ).slice(0, 30);
        out.innerHTML = matches.map(s => `
          <button class="w-full text-left flex justify-between items-center bg-white border rounded-lg p-3 ${s.is_absent ? 'opacity-60' : ''}" data-id="${s.id}">
            <span>
              <span class="font-medium">${s.name}</span>
              <span class="text-gray-500"> · ${s.class}</span>
              ${s.is_absent ? '<span class="ml-2 text-xs bg-red-600 text-white px-2 py-0.5 rounded">ABSENT</span>' : ''}
            </span>
            <span class="font-mono">${s.tokens}</span>
          </button>`).join('');
        for (const btn of out.querySelectorAll('button[data-id]')) {
          btn.onclick = () => doPay(parseInt(btn.dataset.id, 10));
        }
      }

      async function doPay(student_id) {
        const s = students.find(x => x.id === student_id);
        if (!s) return;
        const ok = await confirmModal({
          title: `Confirm payment`,
          body: `<strong>${s.name}</strong> (${s.class})<br>Balance: ${s.tokens}<br>Deduct <strong>${me.cost_per_play}</strong> for <strong>${me.name}</strong>?`,
          confirmLabel: 'PAY', confirmKind: 'green',
        });
        if (!ok || payLock) return;
        payLock = true;
        setTimeout(() => { payLock = false; }, 1000);
        try {
          await api('POST', '/api/booth/pay', { student_id });
          toast('Paid');
          await Promise.all([loadMe(), loadRecent(), loadStudents()]);
          render();
        } catch (e) { toast(e.message, 'error'); }
      }

      async function showLogin() {
        app.innerHTML = `
          <div class="mt-12">
            <h1 class="text-xl font-bold mb-4 text-center">Booth login</h1>
            <input id="code" inputmode="numeric" placeholder="Booth code" class="w-full border-2 rounded-lg p-3 text-center text-2xl mb-3">
            <button id="login" class="w-full bg-blue-600 text-white py-3 rounded-lg">Log in</button>
          </div>`;
        document.getElementById('login').onclick = async () => {
          try {
            await api('POST', '/api/booth/login', { code: document.getElementById('code').value });
            location.reload();
          } catch (e) { toast(e.message, 'error'); }
        };
      }

      (async () => {
        try {
          await Promise.all([loadMe(), loadStudents(), loadRecent(), loadStats()]);
          render();
          setInterval(async () => { await loadMe(); document.querySelector('header')?.replaceWith(document.createRange().createContextualFragment(renderHeader()).firstElementChild); }, 5000);
          setInterval(async () => { await loadStats(); document.getElementById('stats').innerHTML = renderStats(); }, 30000);
          setInterval(async () => { await loadRecent(); document.getElementById('recent').innerHTML = renderRecent(); }, 1000);
        } catch { showLogin(); }
      })();
    });
  </script>
</body>
</html>
```

**Step 2: Smoke test** — log in to `/booth` with the code you created in the admin tab. Search a student, tap, confirm, check the header tally increments. Undo. Mis-tap a busy student.

**Step 3: Commit**

```bash
git add static/booth.html
git commit -m "feat: booth HTML page with search, pay, undo, stats"
```

---

### Task 23: Teacher HTML page

**Files:**
- Create: `static/teacher.html`

**Step 1: Write `static/teacher.html`**

Two tabs: **Find** (search → detail view with refund/deduct/absent toggle), **Absentees** (per-class accordion with checkboxes + bulk submit).

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Carnival · Teacher</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="/static/shared.js" defer></script>
</head>
<body class="bg-gray-50 text-gray-900">
  <div id="app" class="max-w-md mx-auto p-3"></div>
  <script>
    document.addEventListener('DOMContentLoaded', () => {
      const app = document.getElementById('app');
      let tab = 'find', students = [], byClass = [], current = null;

      async function loadStudents() { students = await api('GET', '/api/teacher/students'); }
      async function loadByClass()  { byClass  = await api('GET', '/api/teacher/students-by-class'); }
      async function loadDetail(id) { current = await api('GET', `/api/teacher/student/${id}`); }

      function render() {
        app.innerHTML = `
          <nav class="flex gap-2 mb-3">
            ${['find','absentees'].map(t => `<button data-tab="${t}" class="flex-1 py-2 rounded ${tab===t?'bg-blue-600 text-white':'bg-gray-200'}">${t}</button>`).join('')}
            <button id="logout" class="px-2 text-sm underline">out</button>
          </nav>
          <div id="panel"></div>`;
        for (const b of app.querySelectorAll('[data-tab]')) b.onclick = () => { tab = b.dataset.tab; current = null; renderPanel(); };
        document.getElementById('logout').onclick = async () => { await api('POST', '/api/teacher/logout'); location.reload(); };
        renderPanel();
      }

      function renderPanel() {
        const panel = document.getElementById('panel');
        if (tab === 'find') renderFind(panel);
        else renderAbsentees(panel);
      }

      function renderFind(panel) {
        if (current) return renderDetail(panel);
        panel.innerHTML = `<input id="q" placeholder="Search…" class="w-full border-2 rounded p-2 mb-2"><div id="results" class="space-y-1"></div>`;
        const q = document.getElementById('q');
        q.addEventListener('input', () => renderResults(q.value.toLowerCase()));
        renderResults('');
      }

      function renderResults(q) {
        const out = document.getElementById('results');
        const matches = !q ? students.slice(0, 30) :
          students.filter(s => s.name.toLowerCase().includes(q) || s.class.toLowerCase().includes(q)).slice(0, 30);
        out.innerHTML = matches.map(s => `
          <button data-id="${s.id}" class="w-full text-left flex justify-between bg-white border rounded p-2 ${s.is_absent ? 'opacity-60' : ''}">
            <span>${s.name} <span class="text-gray-500">· ${s.class}</span>${s.is_absent ? ' <span class="text-xs bg-red-600 text-white px-1 rounded">ABS</span>' : ''}</span>
            <span class="font-mono">${s.tokens}</span>
          </button>`).join('');
        for (const b of out.querySelectorAll('[data-id]')) {
          b.onclick = async () => { await loadDetail(parseInt(b.dataset.id, 10)); renderPanel(); };
        }
      }

      function renderDetail(panel) {
        const s = current;
        panel.innerHTML = `
          <button id="back" class="text-sm underline mb-2">← back</button>
          <h2 class="text-lg font-bold">${s.name} <span class="text-gray-500 text-base">· ${s.class}</span></h2>
          <p>Balance: <strong>${s.tokens}</strong>${s.is_absent ? ' <span class="text-red-600">ABSENT</span>' : ''}</p>
          <div class="grid grid-cols-2 gap-2 mt-3">
            <button id="refund" class="bg-green-600 text-white py-3 rounded">Refund</button>
            <button id="deduct" class="bg-orange-600 text-white py-3 rounded">Deduct</button>
            <button id="abs" class="col-span-2 ${s.is_absent ? 'bg-blue-600' : 'bg-red-600'} text-white py-3 rounded">${s.is_absent ? 'Mark Present' : 'Mark Absent'}</button>
          </div>
          <h3 class="mt-4 font-semibold">History</h3>
          <ul class="text-sm space-y-1">${s.history.map(h => `<li>${h.created_at.slice(11,16)} · ${h.type} · ${h.amount}${h.booth_name?` @ ${h.booth_name}`:''}${h.note?` — ${h.note}`:''}</li>`).join('')}</ul>`;
        document.getElementById('back').onclick = () => { current = null; renderPanel(); };
        const ask = async (label, url) => {
          const amount = parseInt(prompt(`${label} amount?`) || '0', 10);
          if (!amount) return;
          const note = prompt('Note (required, min 3 chars)?') || '';
          try {
            await api('POST', url, { student_id: s.id, amount, note });
            toast('Done');
            await loadStudents(); await loadDetail(s.id); renderPanel();
          } catch (e) { toast(e.message, 'error'); }
        };
        document.getElementById('refund').onclick = () => ask('Refund', '/api/teacher/refund');
        document.getElementById('deduct').onclick = () => ask('Deduct', '/api/teacher/deduct');
        document.getElementById('abs').onclick = async () => {
          try {
            await api('POST', '/api/teacher/absent', { student_id: s.id, is_absent: !s.is_absent });
            toast('Updated');
            await loadStudents(); await loadDetail(s.id); renderPanel();
          } catch (e) { toast(e.message, 'error'); }
        };
      }

      function renderAbsentees(panel) {
        panel.innerHTML = byClass.map(g => `
          <details class="bg-white border rounded mb-2">
            <summary class="p-2 cursor-pointer font-semibold">${g.class} (${g.students.length})</summary>
            <div class="p-2">
              ${g.students.map(s => `<label class="flex items-center gap-2 py-1"><input type="checkbox" data-id="${s.id}" ${s.is_absent ? 'checked' : ''}> ${s.name}</label>`).join('')}
            </div>
          </details>`).join('') + `
          <div class="flex gap-2 mt-3 sticky bottom-0 bg-gray-50 py-2">
            <button id="mabs"  class="flex-1 bg-red-600 text-white py-3 rounded">Mark ticked ABSENT</button>
            <button id="mpres" class="flex-1 bg-blue-600 text-white py-3 rounded">Mark ticked PRESENT</button>
          </div>`;
        const submit = async (is_absent) => {
          const ids = Array.from(panel.querySelectorAll('input[type="checkbox"]:checked'), c => parseInt(c.dataset.id, 10));
          if (!ids.length) return toast('Tick some students first', 'warn');
          try {
            const r = await api('POST', '/api/teacher/absent-bulk', { student_ids: ids, is_absent });
            toast(`Updated ${r.affected}`);
            await loadByClass(); await loadStudents(); renderPanel();
          } catch (e) { toast(e.message, 'error'); }
        };
        document.getElementById('mabs').onclick = () => submit(true);
        document.getElementById('mpres').onclick = () => submit(false);
      }

      async function showLogin() {
        app.innerHTML = `
          <div class="mt-12">
            <h1 class="text-xl font-bold mb-4 text-center">Teacher login</h1>
            <input id="pw" type="password" placeholder="Password" class="w-full border-2 rounded p-3 mb-3">
            <button id="login" class="w-full bg-blue-600 text-white py-3 rounded">Log in</button>
          </div>`;
        document.getElementById('login').onclick = async () => {
          try {
            await api('POST', '/api/teacher/login', { password: document.getElementById('pw').value });
            location.reload();
          } catch (e) { toast(e.message, 'error'); }
        };
      }

      (async () => {
        try { await Promise.all([loadStudents(), loadByClass()]); render(); }
        catch { showLogin(); }
      })();
    });
  </script>
</body>
</html>
```

**Step 2: Smoke test** — log in to `/teacher`, refund a student, mark absent, check bulk absentees tab.

**Step 3: Commit**

```bash
git add static/teacher.html
git commit -m "feat: teacher HTML page with refund/deduct/absent + bulk absentees"
```

---

### Task 24: Summary HTML page

**Files:**
- Create: `static/summary.html`

**Step 1: Write `static/summary.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Carnival · Summary</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="/static/shared.js" defer></script>
</head>
<body class="bg-gray-50 text-gray-900">
  <div id="app" class="max-w-3xl mx-auto p-4"></div>
  <script>
    document.addEventListener('DOMContentLoaded', () => {
      const app = document.getElementById('app');
      let lastFetch = null;

      async function load() {
        const [s, t] = await Promise.all([
          api('GET', '/api/summary'),
          api('GET', '/api/summary/top-spenders'),
        ]);
        lastFetch = new Date();
        return { s, t };
      }

      function render({ s, t }) {
        app.innerHTML = `
          <h1 class="text-2xl font-bold mb-1">Summary</h1>
          <p class="text-xs text-gray-500 mb-4">Updated ${lastFetch.toLocaleTimeString()}</p>
          <h2 class="font-semibold mb-1">By class</h2>
          <table class="w-full text-sm mb-4 bg-white border rounded">
            <thead><tr class="text-left border-b"><th class="p-2">Class</th><th class="p-2">Students</th><th class="p-2">Spent</th><th class="p-2">Remaining</th></tr></thead>
            <tbody>${s.by_class.map(r => `<tr class="border-b"><td class="p-2">${r.class}</td><td class="p-2">${r.total_students}</td><td class="p-2">${r.total_spent}</td><td class="p-2">${r.total_remaining}</td></tr>`).join('')}</tbody>
          </table>
          <h2 class="font-semibold mb-1">By booth</h2>
          <table class="w-full text-sm mb-4 bg-white border rounded">
            <thead><tr class="text-left border-b"><th class="p-2">Booth</th><th class="p-2">Plays</th><th class="p-2">Tally</th><th class="p-2">Top class</th></tr></thead>
            <tbody>${s.by_booth.map(r => `<tr class="border-b"><td class="p-2">${r.name}</td><td class="p-2">${r.tx_count}</td><td class="p-2">${r.tally}</td><td class="p-2">${r.top_class || '—'}</td></tr>`).join('')}</tbody>
          </table>
          <h2 class="font-semibold mb-1">Top spenders</h2>
          <ol class="bg-white border rounded text-sm">${t.map((r,i) => `<li class="border-b p-2 flex justify-between"><span>${i+1}. ${r.name} <span class="text-gray-500">· ${r.class}</span></span><span class="font-mono">${r.spent}</span></li>`).join('')}</ol>`;
      }

      async function tick() {
        try { render(await load()); } catch (e) {
          if (e.message.includes('Login')) {
            app.innerHTML = `<div class="mt-12 max-w-xs mx-auto"><p class="mb-3">Log in via <a class="underline" href="/admin">/admin</a> first.</p></div>`;
            return;
          }
          toast(e.message, 'error');
        }
      }
      tick();
      setInterval(tick, 5000);
    });
  </script>
</body>
</html>
```

**Step 2: Smoke test** — `/summary` shows tables, updates every 5s.

**Step 3: Commit**

```bash
git add static/summary.html
git commit -m "feat: summary HTML page with 5s auto-refresh"
```

---

## Phase 7 — Polish & deploy

### Task 25: Seed script + sample CSV

**Files:**
- Create: `seed.py`
- Create: `sample_students.csv`

**Step 1: Write `sample_students.csv`** (4 classes × 5 students)

```csv
name,class
Aiden Lim,3E1
Bella Tan,3E1
Caleb Wong,3E1
Damien Chua,3E1
Emma Lee,3E1
Faizah binte Hassan,3E2
Gavin Ng,3E2
Hannah Goh,3E2
Ian Soh,3E2
Jia Hui Teo,3E2
Kai Yang Loh,3N1
Lina Ismail,3N1
Marcus Tay,3N1
Nadia Aziz,3N1
Oliver Chen,3N1
Priya Subramaniam,3N2
Qi Hua Lim,3N2
Rachel Koh,3N2
Sam Iskandar,3N2
Tasha Wee,3N2
```

**Step 2: Write `seed.py`**

```python
"""Seed local dev DB with sample booths. Idempotent."""
import asyncio
import os

from db import init_pool, init_schema, close_pool

SAMPLE_BOOTHS = [
    ("Ring Toss", "1111", 1),
    ("Darts",     "2222", 2),
    ("Lucky Dip", "3333", 1),
]


async def main() -> None:
    pool = await init_pool(os.environ["DATABASE_URL"])
    await init_schema(pool)
    async with pool.acquire() as conn:
        for name, code, cost in SAMPLE_BOOTHS:
            await conn.execute(
                """
                INSERT INTO booths (name, code, cost_per_play)
                VALUES ($1, $2, $3)
                ON CONFLICT (code) DO NOTHING
                """,
                name, code, cost,
            )
    await close_pool(pool)
    print(f"Seeded {len(SAMPLE_BOOTHS)} booths.")


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 3: Smoke test**

```bash
. .venv/bin/activate
export $(grep -v '^#' .env.example | xargs)
python seed.py
```

Expected: `Seeded 3 booths.` (or no-op if already present).

**Step 4: Commit**

```bash
git add seed.py sample_students.csv
git commit -m "chore: sample roster CSV and seed script for 3 booths"
```

---

### Task 26: Final test suite run + README

**Files:**
- Create: `README.md`

**Step 1: Run the full suite**

```bash
docker compose up -d
pytest -v
```

Expected: every test passes.

**Step 2: Write `README.md`**

```markdown
# CTSS Carnival Token System

Single FastAPI + Postgres service for running carnival token economy. Deployed to Railway.

## Local development

1. **Install Python 3.11+** and Docker.
2. **Start Postgres**: `docker compose up -d`
3. **Set up environment**:
   ```bash
   python3.11 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements-dev.txt
   cp .env.example .env  # edit if you want, defaults work for Docker
   export $(grep -v '^#' .env | xargs)
   ```
4. **Run tests**: `pytest -v`
5. **Seed booths** (optional): `python seed.py`
6. **Start server**: `uvicorn app:app --reload --port 8000`
7. Open http://localhost:8000/admin (password from `ADMIN_PASSWORD`).

## Railway deployment

1. **Create Railway project**, link this GitHub repo.
2. **Add the Postgres plugin** to the project (`DATABASE_URL` is auto-injected).
3. **Set env vars** in Railway dashboard:
   - `ADMIN_PASSWORD` — your admin password
   - `TEACHER_PASSWORD` — your teacher/refund password
   - `SESSION_SECRET` — long random string (≥32 chars). Set once, never rotate mid-event.
4. **Deploy** — Railway picks up `Procfile` and `railway.json` automatically.
5. **Verify** the deployment by curling `/health`:
   ```
   curl https://your-app.up.railway.app/health
   {"status":"ok"}
   ```
6. **Log in** to `/admin`, set carnival name + default tokens, upload roster CSV, add booths.

## First-time setup flow (on event day)

1. Admin → **Settings**: set carnival name and default tokens.
2. Admin → **Roster**: upload student CSV (`name,class`).
3. Admin → **Booths**: add each booth with a unique 4-6 digit code. Hand the code to the operator.
4. Teachers: log in to `/teacher` with `TEACHER_PASSWORD`. Use **Absentees** tab to mark no-shows before the carnival starts.
5. Operators: log in to `/booth` with their booth code on a phone/iPad.
6. Admin: open `/summary` on a screen for live monitoring.
7. After event: admin → **Export CSV** for audit.

## Architecture

- **Backend**: FastAPI, asyncpg, bcrypt (passlib), signed session cookies (itsdangerous).
- **Database**: PostgreSQL. Schema auto-creates on first boot. Row-level locking (`SELECT ... FOR UPDATE`) on every balance change.
- **Frontend**: Vanilla JS + Tailwind CDN. Mobile-first.
- **Roles**: admin (full access), teacher (refund/absent), booth (own booth, pay+undo).

See `docs/plans/2026-05-16-carnival-tokens-design.md` for full design notes.

## Troubleshooting

- **`uvicorn: command not found`** — venv not activated. `. .venv/bin/activate`.
- **`asyncpg.exceptions.InvalidPasswordError`** — `DATABASE_URL` env var wrong. Check Postgres credentials.
- **Railway: app keeps restarting** — check Railway logs, usually a missing env var (`ADMIN_PASSWORD`, `SESSION_SECRET`).
- **`/health` returns 502 on Railway** — uvicorn isn't binding to `$PORT`. Make sure `Procfile` is correct.
- **Tests fail with "Postgres unreachable"** — `docker compose ps` should show `db` running. Restart: `docker compose down && docker compose up -d`.
- **Stuck "Login required" on `/admin`** — cookie blocked by browser. Use https in production, or set `secure=False` on the cookie in `routes/auth_routes.py` for local dev only.
```

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README with local dev + Railway deploy instructions"
```

---

### Task 27: End-to-end manual test

**No new files.** Walk through the full event flow against local Postgres.

**Step 1: Reset state**

```bash
docker compose down -v && docker compose up -d
. .venv/bin/activate
export $(grep -v '^#' .env.example | xargs)
python seed.py
uvicorn app:app --reload --port 8000
```

**Step 2: Run the script**

1. Open `/admin`, log in with `change-me-admin`.
2. Settings tab → set carnival name "CTSS Carnival 2026", default tokens 20. Save.
3. Roster tab → upload `sample_students.csv` → preview → confirm.
4. Booths tab → verify 3 booths from seed. Add one more.
5. Resets tab → don't reset; verify confirm-by-typing-carnival-name is required.
6. Open `/booth` in a private window, log in with `1111`.
7. Search "Ali" → tap Aiden Lim → confirm PAY → tally goes from 0 to 1.
8. Undo within 60s → tally back to 0, balance restored.
9. Pay 6 more times rapidly across different students → header updates, recent strip shows last 5.
10. Open `/teacher` in another private window, log in with `change-me-teacher`.
11. Refund Aiden Lim 2 tokens with note "test refund". Check balance.
12. Absentees tab → mark a class absent. Go back to booth and try to pay one of them → see "Student is marked absent" error.
13. Open `/summary` → see classes + booth tallies + top spenders. Wait 5s, see "updated" timestamp tick.
14. Admin → Export CSV → check transactions all present.
15. Admin → Resets → Full reset (typing carnival name). Verify students and transactions gone, booths preserved.

**Step 3: Sign-off commit**

If anything was tweaked during testing, commit those fixes:

```bash
git add -p
git commit -m "fix: <whatever needed adjusting during E2E>"
```

**Step 4: Push & deploy**

```bash
gh repo create ctss-carnival --public --source=. --remote=origin --push
```

Then in Railway:
1. New project → Deploy from GitHub repo → pick `ctss-carnival`.
2. Add Postgres plugin.
3. Set `ADMIN_PASSWORD`, `TEACHER_PASSWORD`, `SESSION_SECRET` env vars.
4. Wait for deploy. Hit `https://<app>.up.railway.app/health` → expect `{"status":"ok"}`.
5. Final smoke test: log in to `/admin`, add a booth, log in to `/booth`, pay once.

---

## Done

Total: 27 tasks, ~30 commits. Each commit is small, tested, deployable.

Key invariants maintained across all changes:
- **Append-only transactions log** — never UPDATE/DELETE existing transaction rows except to set `reversed_by`.
- **FOR UPDATE on every balance mutation** — pay, undo, refund, deduct.
- **Server-side re-validation of is_absent** — UI staleness is harmless.
- **Two-step CSV upload** with tx-existence guard.
- **Confirm-by-typing-carnival-name** for destructive resets.

If anything in this plan turns out to be wrong during implementation, STOP and ask before improvising. The race-safe payment flow is especially load-bearing — don't reorder the SQL.
