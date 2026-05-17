# CTSS Carnival Token System

Single FastAPI + Postgres service for running carnival token economy. Deployed to Railway.

## Local development

1. **Install Python 3.11+** and Docker.
2. **Start Postgres**: `docker compose up -d` (exposes on port 5433 to avoid clashing with a system Postgres)
3. **Set up environment**:
   ```bash
   python3.11 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements-dev.txt
   cp .env.example .env  # defaults work for the local Docker Postgres
   export $(grep -v '^#' .env | xargs)
   ```
4. **Run tests**: `pytest -v` — expect 76 passing
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
2. Admin → **Roster**: upload student CSV (`name,class`). Preview, confirm.
3. Admin → **Booths**: add each booth with a unique 4-6 digit code. Share the code with that booth's operator.
4. Teachers: log in to `/teacher` with `TEACHER_PASSWORD`. Use the **Absentees** tab to mark no-shows before the carnival starts.
5. Operators: log in to `/booth` with their booth code on a phone/iPad.
6. Admin: open `/summary` on a screen for live monitoring (auto-refreshes every 5s).
7. After event: admin → **Export CSV** for audit.

## Architecture

- **Backend**: FastAPI, asyncpg, bcrypt (passlib), signed session cookies (itsdangerous).
- **Database**: PostgreSQL. Schema auto-creates on first boot. Row-level locking (`SELECT ... FOR UPDATE`) on every balance change.
- **Frontend**: Vanilla JS + Tailwind CDN. Mobile-first.
- **Roles**: admin (full access), teacher (refund/absent), booth (own booth, pay + undo within 60s).

Detailed design notes: `docs/plans/2026-05-16-carnival-tokens-design.md`.
Implementation plan: `docs/plans/2026-05-16-carnival-tokens-implementation.md`.

## Sample data

- `sample_students.csv` — 20 students across 4 classes (3E1, 3E2, 3N1, 3N2)
- `seed.py` — 3 sample booths (Ring Toss / 1111, Darts / 2222, Lucky Dip / 3333)

## Concurrency safety

The pay endpoint uses PostgreSQL row-level locking (`SELECT ... FOR UPDATE`) inside an explicit transaction. ~10 simultaneous booth operators can hit the same student row safely — exactly one succeeds, the rest get 409 with "Insufficient tokens" or "Already absent". The `tests/test_pay_race.py` race test fires 20 concurrent pays at a 1-token student and verifies exactly 1 succeeds.

## Troubleshooting

- **`uvicorn: command not found`** — venv not activated. `. .venv/bin/activate`.
- **`asyncpg.exceptions.InvalidPasswordError`** — `DATABASE_URL` env var wrong. Check Postgres credentials.
- **Railway: app keeps restarting** — check Railway logs, usually a missing env var (`ADMIN_PASSWORD`, `TEACHER_PASSWORD`, or `SESSION_SECRET`).
- **`/health` returns 502 on Railway** — uvicorn isn't binding to `$PORT`. Make sure `Procfile` is correct.
- **Tests fail with "Postgres unreachable"** — `docker compose ps` should show `db` running. Restart: `docker compose down && docker compose up -d`.
- **Local "Login required" loop on `/admin`** — cookie blocked by browser. The cookie uses `secure=True`, so local HTTP won't work in some browsers. Use a tool like Firefox in dev (more permissive on localhost), or temporarily set `secure=False` in `routes/auth_routes.py`. Never do this in production.
