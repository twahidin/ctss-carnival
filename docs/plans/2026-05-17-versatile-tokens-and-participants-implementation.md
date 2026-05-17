# Versatile Token Payments & Participants Tab — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the booth's fixed-cost confirm modal with a tappable virtual-token grid (booth staff choose how many tokens to charge per play), and replace the admin's bare CSV-upload "roster" tab with a searchable participants list (CSV import becomes a button).

**Architecture:** Two-surface change. Booth: `POST /api/booth/pay` accepts an `amount` field; the UI sends per-play counts derived from a token-grid modal. `cost_per_play` stays in the DB as the default pre-selection hint. Admin: new `GET /api/admin/students` endpoint feeds a new list view; existing CSV-import flow is preserved and surfaced behind a button. No DB migration.

**Tech Stack:** FastAPI, asyncpg, Pydantic v2; vanilla JS + Tailwind (CDN) frontend; pytest-asyncio.

**Design doc:** `docs/plans/2026-05-17-versatile-tokens-and-participants-design.md`

**Conventions for this plan:**
- All commands run from repo root `/Users/joetay/Developer/tokens-system`.
- Test runner: `pytest -q`. To run one test: `pytest tests/test_file.py::test_name -q`.
- Tests use `loop_scope="session"` and a `session_pool` fixture (see `tests/conftest.py`).
- Commits are atomic per task. Use the `feat:` / `test:` / `refactor:` / `chore:` prefixes already in this repo's log.

---

## Phase 1 — Backend: versatile pay amount

### Task 1: Add failing test for `amount` parameter on `POST /api/booth/pay`

**Files:**
- Modify: `tests/test_booth_pay.py`

**Step 1: Append the new test cases**

Add these tests to the bottom of `tests/test_booth_pay.py`:

```python
async def test_pay_with_explicit_amount(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
    r = await booth_client.post(
        "/api/booth/pay", json={"student_id": sid, "amount": 5}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["new_balance"] == 5
    async with session_pool.acquire() as conn:
        tokens = await conn.fetchval("SELECT tokens FROM students WHERE id = $1", sid)
        tally = await conn.fetchval("SELECT tally FROM booths WHERE code = '1234'")
        tx = await conn.fetchrow(
            "SELECT type, amount FROM transactions WHERE id = $1",
            body["transaction_id"],
        )
    assert tokens == 5
    assert tally == 5
    assert tx["amount"] == 5


async def test_pay_amount_zero_rejected(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
    r = await booth_client.post(
        "/api/booth/pay", json={"student_id": sid, "amount": 0}
    )
    assert r.status_code == 422


async def test_pay_amount_negative_rejected(booth_client, session_pool) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
    r = await booth_client.post(
        "/api/booth/pay", json={"student_id": sid, "amount": -1}
    )
    assert r.status_code == 422


async def test_pay_amount_exceeds_balance_rejected(
    booth_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM students WHERE name = 'Alice'")
    r = await booth_client.post(
        "/api/booth/pay", json={"student_id": sid, "amount": 999}
    )
    assert r.status_code == 409
    body = r.json()
    msg = body.get("error") or body.get("detail", {}).get("error") or str(body.get("detail", ""))
    assert "insufficient" in msg.lower()
```

**Step 2: Run tests — confirm new ones fail (and the old `cost_per_play=2` test may now also fail since `amount` becomes required)**

Run: `pytest tests/test_booth_pay.py -q`
Expected: the 4 new tests fail (422/200 mismatches). The existing `test_pay_deducts_tokens_and_increments_tally` may still pass at this stage since the field isn't required yet — that's fine, it'll need updating in Task 3.

**Step 3: Commit**

```bash
git add tests/test_booth_pay.py
git commit -m "test(booth): add failing tests for versatile pay amount"
```

---

### Task 2: Make `amount` required and use it for deduction

**Files:**
- Modify: `routes/booth.py` (around lines 72-126)

**Step 1: Update `PayBody` and `pay`**

In `routes/booth.py`:

Replace the `PayBody` class:

```python
class PayBody(BaseModel):
    student_id: int
    amount: int = Field(ge=1, le=1000)
```

Add the `Field` import:

```python
from pydantic import BaseModel, Field
```

Replace the body of `pay` so it uses `body.amount` instead of `booth["cost_per_play"]`. The booth lookup is still needed to validate the booth exists. Keep `cost` as the local variable name so the rest of the function reads naturally:

```python
@router.post("/pay")
async def pay(
    body: PayBody, request: Request, session: dict = Depends(require_booth)
) -> dict[str, Any]:
    booth_id = session["booth_id"]
    pool = request.app.state.pool
    cost = body.amount
    async with pool.acquire() as conn:
        async with conn.transaction():
            booth = await conn.fetchrow(
                "SELECT id FROM booths WHERE id = $1", booth_id
            )
            if not booth:
                raise _api_error("Booth not found", "BOOTH_NOT_FOUND", 404)
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
```

**Step 2: Run tests — confirm new pay-amount tests pass, old `test_pay_deducts_tokens_and_increments_tally` now fails (missing required `amount`)**

Run: `pytest tests/test_booth_pay.py -q`
Expected: 3 new tests pass (`test_pay_with_explicit_amount`, `test_pay_amount_zero_rejected`, `test_pay_amount_negative_rejected`, `test_pay_amount_exceeds_balance_rejected`). The original `test_pay_deducts_tokens_and_increments_tally` now fails with 422 because it doesn't send `amount`. That's expected — Task 3 fixes it.

**Step 3: Commit**

```bash
git add routes/booth.py
git commit -m "feat(booth): pay endpoint takes explicit amount per transaction"
```

---

### Task 3: Update existing pay call sites in tests

**Files:**
- Modify: `tests/test_booth_pay.py:27` — first existing test
- Modify: `tests/test_booth_pay.py:42-63` — other existing tests (insufficient, absent, unknown)
- Modify: `tests/test_booth_undo.py:22`
- Modify: `tests/test_pay_race.py:32`
- Modify: `tests/test_booth_recent_stats.py:25,37,51,53`

**Step 1: Add `"amount": 2` to every existing `/api/booth/pay` call**

Every existing test fixture creates booths with `cost_per_play = 2`. To preserve the old semantics in these tests, add `"amount": 2` to each existing pay call. Concretely, change each:

```python
await client.post("/api/booth/pay", json={"student_id": sid})
```

to:

```python
await client.post("/api/booth/pay", json={"student_id": sid, "amount": 2})
```

Apply to all 7 call sites listed above. Leave the new tests added in Task 1 alone.

Note: `tests/test_booth_pay.py::test_pay_unknown_student` calls pay with `student_id=99999`. That test now needs `"amount"` too — without it the request fails validation (422) before reaching the 404 path. Add `"amount": 2` so the test still asserts what it claims to (404 unknown student).

**Step 2: Run the full booth+pay test suite**

Run: `pytest tests/test_booth_pay.py tests/test_booth_undo.py tests/test_pay_race.py tests/test_booth_recent_stats.py -q`
Expected: all pass.

**Step 3: Run the entire test suite to catch anything else**

Run: `pytest -q`
Expected: all pass. If something else fails (e.g. teacher or summary tests that indirectly use pay), update those call sites too.

**Step 4: Commit**

```bash
git add tests/
git commit -m "test: pass explicit amount to /api/booth/pay in existing tests"
```

---

## Phase 2 — Backend: admin students list endpoint

### Task 4: Add failing test for `GET /api/admin/students`

**Files:**
- Create: `tests/test_admin_students.py`

**Step 1: Write the new test file**

Look at `tests/test_admin_booths.py` for the existing pattern of `admin_client` fixture and admin login.

Create `tests/test_admin_students.py`:

```python
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def admin_client(client, admin_password):
    await client.post("/api/admin/login", json={"password": admin_password})
    return client


async def test_list_students_empty(admin_client) -> None:
    r = await admin_client.get("/api/admin/students")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_students_returns_ordered_by_name(
    admin_client, session_pool
) -> None:
    async with session_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO students (name, class, tokens, is_absent) VALUES "
            "('Zara', '3E1', 15, FALSE), "
            "('Adele', '3E2', 12, FALSE), "
            "('Bob', '3N1', 5, TRUE)"
        )
    r = await admin_client.get("/api/admin/students")
    assert r.status_code == 200
    body = r.json()
    assert [s["name"] for s in body] == ["Adele", "Bob", "Zara"]
    adele = body[0]
    assert set(adele.keys()) == {"id", "name", "class", "tokens", "is_absent"}
    assert adele["class"] == "3E2"
    assert adele["tokens"] == 12
    assert adele["is_absent"] is False


async def test_list_students_requires_admin(client) -> None:
    r = await client.get("/api/admin/students")
    assert r.status_code in (401, 403)
```

**Step 2: Check fixture names**

Run: `grep -n "admin_password\|admin_client" tests/conftest.py tests/test_admin_booths.py | head -20`

If `admin_password` isn't a fixture and `admin_client` already exists in `conftest.py`, simplify by importing it. If neither exists, copy whichever pattern `test_admin_booths.py` uses and adapt. The point: stick to the existing repo convention rather than inventing one.

**Step 3: Run new test — confirm it fails**

Run: `pytest tests/test_admin_students.py -q`
Expected: FAIL (endpoint not found — 404).

**Step 4: Commit**

```bash
git add tests/test_admin_students.py
git commit -m "test(admin): add failing tests for students list endpoint"
```

---

### Task 5: Implement `GET /api/admin/students`

**Files:**
- Modify: `routes/admin.py` (add new endpoint near the booth list endpoint, ~line 93)

**Step 1: Add the endpoint**

In `routes/admin.py`, after the `list_booths` function (around line 102), add:

```python
@router.get("/students")
async def list_students(
    request: Request, _: dict = Depends(require_admin)
) -> list[dict[str, Any]]:
    async with request.app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, class, tokens, is_absent FROM students "
            "ORDER BY name"
        )
    return [dict(r) for r in rows]
```

**Step 2: Run tests — confirm they pass**

Run: `pytest tests/test_admin_students.py -q`
Expected: all 3 tests pass.

**Step 3: Run the full suite to ensure nothing else regressed**

Run: `pytest -q`
Expected: all pass.

**Step 4: Commit**

```bash
git add routes/admin.py
git commit -m "feat(admin): add GET /api/admin/students endpoint"
```

---

## Phase 3 — Frontend: booth token-grid modal

### Task 6: Read the existing `confirmModal` to understand the modal pattern

**Files:**
- Read: `static/shared.js`

**Step 1: Look at the shared modal helper**

Run: `grep -n 'confirmModal\|toast\|api(' static/shared.js | head -20`

Identify how `confirmModal({ title, body, confirmLabel, confirmKind })` works — we're going to write a sibling helper (or call it differently) rather than re-using it as-is. If `confirmModal` is built to accept arbitrary HTML in `body` *and* render via DOM (not innerHTML), reuse it. Otherwise inline a new modal in `booth.html`.

For this plan, assume we inline a new modal directly in `static/booth.html` since the token-grid needs interactive elements and per-token state. Don't add complexity to `shared.js`.

**Step 2: No commit** — this is a read-only investigation step.

---

### Task 7: Replace `doPay` with token-grid modal in `static/booth.html`

**Files:**
- Modify: `static/booth.html:121-138` (the `doPay` function)

**Step 1: Replace `doPay`**

In `static/booth.html`, replace the entire `doPay` function with the implementation below. This injects a modal directly into the DOM, manages selection state, and posts the chosen amount.

```javascript
async function doPay(student_id) {
  const s = students.find(x => x.id === student_id);
  if (!s) return;
  if (s.is_absent) return toast('Student is marked absent', 'warn');
  if (s.tokens < 1) return toast('No tokens to deduct', 'warn');

  const defaultSel = Math.min(me.cost_per_play, s.tokens);
  const selected = new Set();
  for (let i = 0; i < defaultSel; i++) selected.add(i);

  const overlay = document.createElement('div');
  overlay.className = 'fixed inset-0 bg-black/50 flex items-end sm:items-center justify-center z-20';
  overlay.innerHTML = `
    <div class="bg-white w-full sm:max-w-sm rounded-t-2xl sm:rounded-2xl p-4 max-h-[90vh] flex flex-col">
      <div class="font-bold text-lg">${s.name}</div>
      <div class="text-sm text-gray-500 mb-1">${s.class}</div>
      <div class="text-sm mb-3">Balance: <strong>${s.tokens}</strong> tokens — tap to deduct</div>
      <div id="tokgrid" class="grid grid-cols-5 gap-2 overflow-y-auto py-2"></div>
      <div class="flex items-center justify-between mt-3 pt-3 border-t">
        <button id="tokcancel" class="px-4 py-2 rounded bg-gray-200">Cancel</button>
        <button id="tokpay" class="px-6 py-3 rounded bg-green-600 text-white font-bold disabled:opacity-50">PAY <span id="toknum">0</span></button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const grid = overlay.querySelector('#tokgrid');
  const num = overlay.querySelector('#toknum');
  const payBtn = overlay.querySelector('#tokpay');

  function paint() {
    grid.innerHTML = '';
    for (let i = 0; i < s.tokens; i++) {
      const b = document.createElement('button');
      const on = selected.has(i);
      b.className = on
        ? 'aspect-square rounded-full bg-red-600 text-white text-xs font-bold'
        : 'aspect-square rounded-full bg-red-200 border-2 border-red-300';
      b.textContent = on ? '✓' : '';
      b.onclick = () => { on ? selected.delete(i) : selected.add(i); paint(); };
      grid.appendChild(b);
    }
    num.textContent = selected.size;
    payBtn.disabled = selected.size === 0;
  }
  paint();

  function close() { overlay.remove(); }
  overlay.querySelector('#tokcancel').onclick = close;
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });

  payBtn.onclick = async () => {
    if (payLock || selected.size === 0) return;
    payLock = true;
    setTimeout(() => { payLock = false; }, 1000);
    payBtn.disabled = true;
    try {
      await api('POST', '/api/booth/pay', { student_id, amount: selected.size });
      close();
      toast('Paid');
      await Promise.all([loadMe(), loadRecent(), loadStudents()]);
      render();
    } catch (e) {
      payBtn.disabled = false;
      toast(e.message, 'error');
    }
  };
}
```

Key behaviors this captures:
- Absent / zero-balance students get a toast and never open the modal.
- `cost_per_play` tokens are pre-selected (capped at student's balance).
- Each token is a tappable button that toggles selection.
- `PAY n` reflects current count; disabled at 0.
- Click outside the modal cancels.
- `payLock` semantics are preserved (1 s debounce against double-tap).
- On error, modal stays open with PAY re-enabled so the user can retry or cancel.

**Step 2: Manual smoke check (no automated test for the UI)**

Start the dev server and exercise the flow:
```bash
docker compose up -d db
# then in another shell:
uvicorn app:app --reload
```

In a browser:
1. Log in as a booth.
2. Tap a student → modal opens with red token grid.
3. Verify `cost_per_play` tokens are pre-selected.
4. Tap to add/remove → `PAY n` updates.
5. PAY → balance decreases by `n`, modal closes, toast shows "Paid".
6. Try a student marked absent → toast "Student is marked absent", no modal.
7. Refresh — booth header tally reflects the new total.

If anything misbehaves, fix before committing.

**Step 3: Commit**

```bash
git add static/booth.html
git commit -m "feat(booth-ui): tap virtual tokens to pick pay amount per play"
```

---

## Phase 4 — Frontend: admin participants tab

### Task 8: Rename "roster" → "participants" in the admin tab nav

**Files:**
- Modify: `static/admin.html:24` — nav button labels
- Modify: `static/admin.html:59-60` — tab routing
- Modify: `static/admin.html:68` — function name

**Step 1: Update label and tab-routing**

In `static/admin.html`:

Line 24:
```javascript
${['settings','participants','booths','resets'].map(t =>
```

Lines 59-60: change
```javascript
} else if (state.tab === 'roster') {
  renderRosterTab(panel);
```
to
```javascript
} else if (state.tab === 'participants') {
  renderParticipantsTab(panel);
```

Line 68: rename the function from `renderRosterTab(panel)` to `renderParticipantsTab(panel)`. (Body is rewritten in Task 9.)

**Step 2: Manual smoke check**

Reload `/admin`. The tab should read `participants`. Clicking it should still show the CSV upload (since we haven't rewritten the body yet) without errors in console.

**Step 3: Commit**

```bash
git add static/admin.html
git commit -m "refactor(admin-ui): rename roster tab to participants"
```

---

### Task 9: Rewrite the participants tab to show the student list

**Files:**
- Modify: `static/admin.html` — entire `renderParticipantsTab` function (was `renderRosterTab`, lines 68-99)
- Modify: `static/admin.html` — add a `loadStudents` helper and `state.students` field

**Step 1: Add state and loader**

Near `loadSettings` / `loadBooths` (around line 172-174), add:

```javascript
async function loadStudents() { state.students = await api('GET', '/api/admin/students'); }
```

Update `loadAll` to fetch students too:

```javascript
async function loadAll() {
  await Promise.all([loadSettings(), loadBooths(), loadStudents()]);
}
```

Update `state` declaration (line 15):

```javascript
let state = { tab: 'settings', settings: null, booths: [], students: [] };
```

**Step 2: Rewrite `renderParticipantsTab`**

Replace the entire function body with:

```javascript
function renderParticipantsTab(panel) {
  const students = state.students || [];
  if (students.length === 0) {
    renderImportCsv(panel);
    return;
  }
  panel.innerHTML = `
    <div class="flex items-center justify-between mb-3">
      <div class="text-sm text-gray-600"><strong>${students.length}</strong> students</div>
      <button id="show-import" class="bg-blue-600 text-white px-3 py-1 rounded text-sm">Import CSV</button>
    </div>
    <input id="pq" placeholder="Search name or class…" class="w-full border rounded p-2 mb-3" autocomplete="off">
    <table class="w-full text-sm">
      <thead><tr class="text-left border-b">
        <th class="py-2">Name</th><th>Class</th><th>Tokens</th><th>Absent</th>
      </tr></thead>
      <tbody id="ptbody"></tbody>
    </table>
    <div id="import-panel" class="mt-6"></div>`;

  function paintRows(query) {
    const q = (query || '').toLowerCase();
    const matches = !q ? students : students.filter(s =>
      s.name.toLowerCase().includes(q) || s.class.toLowerCase().includes(q)
    );
    document.getElementById('ptbody').innerHTML = matches.map(s => `
      <tr class="border-b">
        <td class="py-1">${s.name}</td>
        <td>${s.class}</td>
        <td>${s.tokens}</td>
        <td>${s.is_absent ? '<span class="text-red-600">●</span>' : '<span class="text-gray-400">○</span>'}</td>
      </tr>`).join('');
  }
  paintRows('');

  let timer;
  document.getElementById('pq').addEventListener('input', e => {
    clearTimeout(timer);
    timer = setTimeout(() => paintRows(e.target.value.trim()), 150);
  });

  document.getElementById('show-import').onclick = () => {
    renderImportCsv(document.getElementById('import-panel'));
  };
}

function renderImportCsv(panel) {
  panel.innerHTML = `
    <div class="border rounded p-3 bg-amber-50">
      <p class="text-sm mb-2 text-amber-800">Uploading a new CSV replaces ALL existing students.</p>
      <input type="file" id="csv" accept=".csv" class="block mb-3">
      <button id="preview" class="bg-blue-600 text-white px-4 py-2 rounded">Preview</button>
      <div id="preview-out" class="mt-4"></div>
    </div>`;
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
          await loadStudents();
          renderPanel();
        } catch (e) { toast(e.message, 'error'); }
      };
    } catch (e) { toast(e.message, 'error'); }
  };
}
```

Notes:
- The empty-state path calls `renderImportCsv(panel)` directly — first-time setup still works.
- After a successful import, we re-fetch `state.students` and re-render so the new list shows up.
- The CSV warning text is preserved (matches the existing semantics of `/api/admin/upload-csv` requiring a full reset first).

**Step 3: Manual smoke check**

1. Log in as admin with an empty DB → tab shows the upload UI only.
2. Import a CSV → tab now shows the student list with count + search + Import CSV button.
3. Search by name and by class → list filters live.
4. Click Import CSV → upload panel appears below.

**Step 4: Commit**

```bash
git add static/admin.html
git commit -m "feat(admin-ui): participants tab lists students with CSV import behind a button"
```

---

## Phase 5 — Verification

### Task 10: Full test pass and manual end-to-end

**Step 1: Run the full test suite**

Run: `pytest -q`
Expected: all green.

**Step 2: Linting / type check (if configured)**

Run: `grep -E "lint|ruff|mypy|pyright" pyproject.toml requirements-dev.txt 2>/dev/null`

If `ruff` is present: `ruff check .`
If `pyright` is present: `pyright` (note: Joe expects IDE Pyright noise per memory — CLI run is the authoritative one).

**Step 3: End-to-end smoke**

With the dev server running:
1. Admin login → participants tab → import the sample roster (`sample_students.csv`) — verify list renders.
2. Admin → booths tab → create a booth with `cost_per_play=3`.
3. Booth login → tap student → modal pre-selects 3 tokens → tap to change to 5 → PAY → balance decreases by 5, booth tally increases by 5.
4. Undo within 60s → balance restored, tally back to 0.
5. Admin → participants tab → verify the student's balance matches what's expected after the pay+undo.

**Step 4: No commit needed unless fixes were applied.**

If something needed a fix, commit it as `fix: <what>` before declaring done.

---

## Summary of files touched

| File | Why |
|---|---|
| `routes/booth.py` | `PayBody.amount` (required, ≥1); deduct `body.amount` instead of `cost_per_play` |
| `routes/admin.py` | New `GET /api/admin/students` |
| `static/booth.html` | New token-grid payment modal in `doPay` |
| `static/admin.html` | Tab rename, new participants list view, CSV import moved behind button |
| `tests/test_booth_pay.py` | New tests for amount path + amount param added to existing tests |
| `tests/test_booth_undo.py` | Add `amount: 2` to existing pay call |
| `tests/test_pay_race.py` | Add `amount: 2` to existing pay call |
| `tests/test_booth_recent_stats.py` | Add `amount: 2` to existing pay calls |
| `tests/test_admin_students.py` | New |

No DB migration, no schema changes.
