# CTSS Carnival Token System — Design

**Date:** 2026-05-16
**Status:** Approved, ready for implementation plan

## Goal

A Railway-deployed web app that runs the CTSS Carnival token economy for ~800 students and ~10 booth operators during a single ~3-hour event. Admin sets up roster + booths, booth operators deduct tokens at booths, refund teachers fix mistakes and mark absentees. Every transaction logged for audit.

## Stack

- **Backend:** Python 3.11+, FastAPI, asyncpg, passlib[bcrypt], itsdangerous, python-multipart, uvicorn.
- **Database:** PostgreSQL (Railway plugin). No SQLite — Railway filesystems are ephemeral and we need real row-level locking.
- **Frontend:** Vanilla JS + Tailwind via CDN, one HTML file per role.
- **Deploy:** Railway, single service, Nixpacks build, `$PORT` + `0.0.0.0` bind, `/health` probe.

## Roles & Auth

| Role | Credential | Scope |
|---|---|---|
| Admin | `ADMIN_PASSWORD` env var | Everything (admin + teacher pages) |
| Teacher | `TEACHER_PASSWORD` env var | `/teacher` only |
| Booth operator | Booth `code` (DB) | `/booth` only |

- Passwords bcrypt-hashed into `settings` table on first boot. SHA-256 of the source env var also stored; if env var changes between deploys, re-hash.
- Sessions: signed cookies via `itsdangerous`, persistent `SESSION_SECRET` env var (set once, never rotated mid-event), 12h lifetime, `HttpOnly` `Secure` `SameSite=Lax`.
- Login rate limit: 10 attempts / IP / minute, in-memory sliding window.

## Data Model

```sql
CREATE TABLE IF NOT EXISTS students (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  class TEXT NOT NULL,
  tokens INTEGER NOT NULL,
  is_absent BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_students_name ON students (LOWER(name));
CREATE INDEX idx_students_class ON students (class);

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
  type VARCHAR(20) NOT NULL,            -- 'play', 'refund', 'deduct', 'undo', 'reset'
  note TEXT,
  reversed_by INTEGER REFERENCES transactions(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_tx_student ON transactions(student_id);
CREATE INDEX idx_tx_booth ON transactions(booth_id);
CREATE INDEX idx_tx_created ON transactions(created_at);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- keys: carnival_name, default_tokens,
--       admin_password_hash, admin_password_source_hash,
--       teacher_password_hash, teacher_password_source_hash
```

**Conventions:**
- `amount` is always positive. Direction inferred from `type`: `play` and `deduct` debit the student; `refund` and `undo` credit; `reset` is a balance snapshot (note records prior balance).
- `students.tokens` is the live, denormalized balance — maintained by every transaction inside `BEGIN ... COMMIT` with `FOR UPDATE`.
- `transactions` is append-only. Undos add a new row + populate `reversed_by` on the original.
- No `short_id` (booth search is by name+class).

**Reconciliation invariant:** for any student, `tokens` should equal `default_tokens_at_reset` minus sum of `play`/`deduct` plus sum of `refund`/`undo` since the last `reset` row for that student. Useful in tests and the export CSV.

## Critical Flow: Payment

`POST /api/booth/pay` body `{student_id}`, booth_id from session:

```
BEGIN
  SELECT id, tokens, is_absent FROM students WHERE id = $1 FOR UPDATE
  -- reject if not found, absent, or tokens < booth.cost
  UPDATE students SET tokens = tokens - cost WHERE id = $1
  UPDATE booths   SET tally  = tally  + cost WHERE id = booth_id
  INSERT INTO transactions (student_id, booth_id, amount, type) VALUES (..., 'play')
COMMIT
```

`FOR UPDATE` serializes concurrent attempts on the same student — exactly one of N concurrent pays succeeds.

Client defences (not source of truth): confirm modal, PAY disabled 1s after first tap.

## Critical Flow: Undo (booth, own last 5, within 60s)

`POST /api/booth/undo` body `{transaction_id}`:

```
BEGIN
  SELECT ... FROM transactions WHERE id=$1 AND booth_id=session FOR UPDATE
  -- reject if type != 'play', already reversed, age > 60s, wrong booth
  SELECT ... FROM students WHERE id=student_id FOR UPDATE
  UPDATE students SET tokens = tokens + amount
  UPDATE booths   SET tally  = tally  - amount
  INSERT INTO transactions (...,'undo','undo of tx #N') RETURNING id INTO undo_id
  UPDATE transactions SET reversed_by = undo_id WHERE id = $1
COMMIT
```

Same locking discipline. Audit log preserves both rows.

## Critical Flow: Refund / Deduct (teacher)

Same `FOR UPDATE` pattern. Required `note` (≥3 chars). Type `refund` or `deduct`.

## Critical Flow: Absent

Teacher sets `students.is_absent`. The booth UI may show stale value for up to ~5s (ETag cache), but the **payment endpoint re-checks `is_absent` inside the FOR UPDATE transaction**, so an absent student can never be charged regardless of UI state.

## CSV Upload

Two-step, in-memory only (never written to disk).

1. `POST /api/admin/upload-csv` (multipart):
   - **Block if any transactions exist.** Force admin to run Full Reset first. Prevents mid-event audit-trail loss from a mis-click.
   - Strip UTF-8 BOM, accept CRLF/LF, require headers `name,class`.
   - Trim whitespace, skip blank rows.
   - Return preview JSON: row count, class breakdown, sample rows, one-time confirmation `token`.
2. `POST /api/admin/upload-csv/confirm` `{token}`:
   - Atomic: `DELETE FROM students` (cascades to transactions), `INSERT` parsed rows with `tokens = default_tokens`.

## Endpoints

```
GET  /health
GET  /                                        → redirect /admin
GET  /admin /booth /teacher /summary          → HTML

POST /api/admin/login           {password}
POST /api/admin/logout
GET  /api/admin/settings
POST /api/admin/settings        {carnival_name?, default_tokens?}
POST /api/admin/upload-csv      multipart → preview + token
POST /api/admin/upload-csv/confirm {token}
GET  /api/admin/booths
POST /api/admin/booths          {name, code, cost_per_play}
PUT  /api/admin/booths/{id}
DELETE /api/admin/booths/{id}
POST /api/admin/reset-tokens    resets students.tokens to default, logs 'reset' per student
POST /api/admin/reset-tallies   zeros booth tallies (doesn't touch transactions)
POST /api/admin/full-reset      wipes students + transactions + booth tallies
GET  /api/admin/export-transactions   CSV stream

POST /api/teacher/login         {password}    accepts admin or teacher pw
POST /api/teacher/logout
GET  /api/teacher/students
GET  /api/teacher/student/{id}                details + last 20 tx
POST /api/teacher/refund        {student_id, amount, note}
POST /api/teacher/deduct        {student_id, amount, note}
POST /api/teacher/absent        {student_id, is_absent}
POST /api/teacher/absent-bulk   {student_ids, is_absent}
GET  /api/teacher/students-by-class

POST /api/booth/login           {code}
POST /api/booth/logout
GET  /api/booth/me
GET  /api/booth/students                       ETag cached 5s server-side
POST /api/booth/pay             {student_id}
POST /api/booth/undo            {transaction_id}
GET  /api/booth/recent
GET  /api/booth/stats

GET  /api/summary
GET  /api/summary/top-spenders
```

## Frontend

Mobile-first, Tailwind CDN, vanilla JS. Shared `shared.js` provides fetch wrapper, toast component, confirm modal.

- **`/admin`** — tabs: Settings, Roster (upload + preview), Booths (CRUD table), Resets (confirm-by-typing-carnival-name modal), Export button.
- **`/booth`** — login → operator screen with sticky header (booth name + live tally + tx count + cost), debounced search, confirm-pay modal, last-5 strip with 60s undo, collapsible class-breakdown stats drawer (30s poll).
- **`/teacher`** — Find student tab (search → detail view with refund/deduct/absent toggle) + Mark absentees tab (per-class accordion with checkboxes, bulk submit).
- **`/summary`** — by-class table, by-booth table, top-10 spenders, 5s auto-poll.

## Error Handling

- API errors: JSON `{error, code}` with proper HTTP status. Frontend toasts the `error` verbatim.
- Unhandled exceptions → 500 with generic message, logged.
- Startup fails loud (missing env var, DB unreachable) so Railway restarts.
- DB pool size: `min_size=2, max_size=10`.

## Testing

`pytest` + `pytest-asyncio` + `httpx.AsyncClient`, real Postgres (no SQLite — concurrency tests need real `FOR UPDATE`). Key tests:

1. **Race-safe pay** — 20 concurrent pays on a 1-token student, exactly one succeeds.
2. Absent flag blocks pay even if UI thinks otherwise.
3. Undo: within 60s ok; expired rejected; double-undo rejected; wrong booth rejected.
4. Refund + deduct balance + log notes correctly.
5. CSV upload — bad headers, BOM, CRLF, blocked when transactions exist.
6. Auth — each role's endpoint isolation; admin can hit teacher routes.
7. Health endpoint returns 200.

Coverage target 80%+ on `routes/` and payment-locking code.

## Project Layout

```
carnival/
├── app.py                FastAPI app + lifespan (pool, migrations)
├── db.py                 asyncpg pool, schema init, get_conn
├── auth.py               bcrypt, session cookies, require_role
├── csv_import.py         parse + validate (in-memory)
├── routes/
│   ├── admin.py
│   ├── booth.py
│   ├── teacher.py
│   └── summary.py
├── static/
│   ├── admin.html
│   ├── booth.html
│   ├── teacher.html
│   ├── summary.html
│   └── shared.js
├── tests/
│   ├── conftest.py
│   ├── test_pay_race.py
│   ├── test_admin.py
│   ├── test_teacher.py
│   ├── test_booth.py
│   └── test_csv.py
├── seed.py
├── sample_students.csv
├── Procfile
├── railway.json
├── requirements.txt
├── .env.example
└── README.md
```

## Environment Variables

```
DATABASE_URL       Railway-injected
PORT               Railway-injected
ADMIN_PASSWORD     manual, hashed on first boot
TEACHER_PASSWORD   manual, hashed on first boot
SESSION_SECRET     manual, long random, set once
```

## Out of Scope (YAGNI)

- Student-facing UI / self-service balance check.
- OAuth, SSO, per-teacher accounts.
- Physical token cards, QR codes, NFC.
- Multi-day events / multiple carnivals in one DB.
- WebSockets / live push (5s polling is enough).
- Manual "set new balance" tool (refund + deduct cover all real cases without breaking the audit trail).

## Open Items for Implementation Plan

- Concrete startup sequence: pool init → migrations → seed password hashes from env.
- Exact bcrypt cost factor (default 12).
- Whether the ETag 5s cache lives in the route handler (in-memory) or via `asyncpg` query result memoization.
- Sample-data seed values for `sample_students.csv` (4 classes × 5 students = 20) and `seed.py` (3 booths).
