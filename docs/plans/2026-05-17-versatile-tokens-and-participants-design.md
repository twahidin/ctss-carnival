# Versatile Token Payments & Participants Tab — Design

**Date:** 2026-05-17
**Status:** Approved

## Summary

Two related UX changes to the carnival token system:

1. **Booth payment becomes versatile.** Tapping a student opens a token-grid modal where booth staff tap individual virtual tokens to decide how many to charge for *this* play. Replaces the current fixed-cost confirmation modal.
2. **Admin "roster" tab becomes "participants"** and shows the current student list (searchable) instead of a bare CSV upload. CSV import moves behind an `Import CSV` button.

## Section 1 — Booth: virtual-token payment

### Flow

1. Booth person taps a student row (existing search results, unchanged).
2. Payment modal opens (replaces current "Confirm payment" modal):
   - Header: student name, class, `Balance: 15 tokens — tap to deduct`.
   - Grid of N red circle buttons (N = student's current balance). ~44px hit targets, 5 across on a phone, responsive wrap.
   - First `cost_per_play` tokens are pre-selected (filled darker / with a check overlay). Booth staff can tap to deselect, or tap unselected ones to add to the charge.
   - Sticky footer: `[Cancel]   [PAY n]` where `n` updates live. PAY disabled when `n === 0`.
3. PAY → `POST /api/booth/pay { student_id, amount }`. Server validates `1 ≤ amount ≤ student.tokens`. On success: modal closes, toast, balance refreshes.

### Backend (`routes/booth.py`)

- `PayBody` adds `amount: int`.
- `pay` uses `body.amount` instead of `booth.cost_per_play` for deduction and tally increment.
- `cost_per_play` stays in schema and admin booth setup — used only as the default pre-selection hint, returned via `/api/booth/me`.
- Undo logic unchanged: it already reverses `tx.amount`, which now varies per transaction.

### Error paths

- `amount < 1` → 422, `INVALID_AMOUNT`.
- `amount > student.tokens` → 409, `INSUFFICIENT_TOKENS` (message updated to show requested vs available).
- Existing paths (absent, booth not found, undo window) unchanged.

### Trade-off

Modal vs full-screen vs inline-expand. Modal wins because it's the smallest delta from the current pattern and works well on a phone with a 15-token grid. Modal scrolls when a student has a very high balance.

## Section 2 — Admin: Participants tab

### Label rename

Admin nav: `roster` → `participants`. Backend routes, API paths, and CSV terminology stay as-is — pure UI label change.

### Tab contents

```
Participants  ·  342 students                          [Import CSV]

[search name or class…]

Name                          Class    Tokens   Absent
─────────────────────────────────────────────────────────
ADELE LIM YING XUAN           S2-R1      15      ○
LEE ZHI YAO, ADELE            S2-R3      15      ○
LOO XIN HUI ADELE             S2-R3      15      ○
…
```

- Top bar: student count left, `Import CSV` button right.
- Search: client-side filter on name + class.
- Table: name, class, tokens balance, absent indicator. Read-only.
- Empty state: if `students.count === 0`, show only the upload UI so first-time setup still works.
- `Import CSV` button: opens the existing file-picker + preview + "wipe and import" confirmation flow (unchanged — just moved behind a button).

### Backend (`routes/admin.py`)

New endpoint `GET /api/admin/students` → `[{ id, name, class, tokens, is_absent }]` ordered by name. No ETag caching needed (admin use is infrequent).

### Trade-off

Inline edit of tokens/absent flag was considered and rejected — keeps scope small and avoids competing with the teacher UI which owns those mutations.

## Section 3 — Files, testing, scope

### Files

- `static/booth.html` — replace `doPay` modal with new token-grid modal; pass `amount` to the API.
- `static/admin.html` — rename tab to `participants`; rewrite `renderRosterTab` → `renderParticipantsTab` (list + `Import CSV` button opening existing flow).
- `routes/booth.py` — `PayBody.amount`; validate `1 ≤ amount ≤ tokens`.
- `routes/admin.py` — new `GET /api/admin/students` endpoint.
- `tests/` — update existing booth pay tests for the new body; add tests for invalid amounts (0, negative, > balance); add a test for the admin students list endpoint.

### No DB migration

`cost_per_play` column stays, `transactions.amount` already varies per row, undo already reverses `tx.amount`.

### Out of scope

- Teacher UI changes
- Inline editing of student tokens/absent in Participants tab
- Per-student cost overrides stored anywhere
- New roles/permissions
