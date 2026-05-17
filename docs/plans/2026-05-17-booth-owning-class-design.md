# Booth Owning Class — Design

**Date:** 2026-05-17
**Status:** Approved, ready for implementation plan

## Goal

Track which class runs each booth so the carnival admin can report total tokens earned per organizing class (e.g., "3I4 earned 142 tokens across Haunted House + Lucky Dip"). Participants and booth-operating classes are conceptually separate populations.

## Background

The current schema treats the student roster as the only kind of "class" and aggregates spending by student class. Booths have no owning-class concept — the current workaround is to encode the class in the booth name (e.g., "Haunted House 3I4"), which is fragile and unsuitable for aggregation.

## Change

**One new column:** `booths.owning_class TEXT NOT NULL DEFAULT ''`.

Existing booths get `''` on migration. Future booths require non-empty `owning_class` (Pydantic + DB-level NOT NULL).

The booth's `cost_per_play` is unchanged — admin sets it, operator UI displays it in the confirmation modal, student confirms. No operator workflow change.

## Data model

```sql
ALTER TABLE booths
  ADD COLUMN IF NOT EXISTS owning_class TEXT NOT NULL DEFAULT '';
```

Idempotent. Safe to run on every boot via the existing `init_schema` mechanism.

## API surface

**`POST /api/admin/booths`** body adds `owning_class: str` (Pydantic `min_length=1, max_length=50`).
**`PUT /api/admin/booths/{id}`** body adds optional `owning_class: str | None`.
**`GET /api/admin/booths`** and create/update responses include `owning_class` in the returned row.

**`GET /api/summary`** response gains a `by_owning_class` key:

```json
{
  "by_class": [...],
  "by_booth": [...],
  "by_owning_class": [
    {"class": "3I4", "earned": 142, "booths": ["Haunted House", "Lucky Dip"]},
    {"class": "3E1", "earned": 87,  "booths": ["Ring Toss"]}
  ]
}
```

Sorted by `earned DESC`. Empty `owning_class` rolls up under `"(unassigned)"` so legacy booths still surface.

Earnings = `SUM(b.tally)` grouped by `b.owning_class`. We use the denormalized tally for speed (kept in sync by the existing pay/undo flow); the audit-log SUM is equivalent.

## Admin UI changes

**Booths tab:**
- New "Owning class" text input next to Name / Code / Cost in the create row.
- New "Owning class" column in the table, between Code and Cost.
- Existing rows show `(unassigned)` if `owning_class` is empty. Inline edit isn't needed; users can delete and recreate, or we add a quick edit later if requested.

**Settings/Roster/Resets tabs:** unchanged.

## Summary page changes

Three sections, in this top-to-bottom order:

1. **By Owning Class — Earnings** *(new, prominent)*
   Table: `Class · Earned · Booths run`.
2. **By Student Class — Spending** *(existing, renamed header)*
   Table: `Class · Students · Spent · Remaining`.
3. **Top Spenders** *(existing, unchanged)*.

5-second auto-refresh continues to cover all three.

## Booth operator flow

Unchanged. The booth-side endpoints (`/api/booth/me`, `/api/booth/students`, `/api/booth/pay`, `/api/booth/undo`, `/api/booth/recent`, `/api/booth/stats`) need no updates — `owning_class` isn't shown to operators.

## Testing

- Update `tests/test_admin_booths.py`: assert `owning_class` round-trips through create + list + update.
- Update `tests/test_admin_booths.py`: assert create rejects empty `owning_class` (422).
- Update `tests/test_summary.py`: assert `by_owning_class` aggregates correctly when one class runs multiple booths.
- Add a small migration smoke test: re-running `init_schema` after data exists doesn't error (the existing `test_init_schema_is_idempotent` already covers this, but worth a manual verify post-deploy).

## Migration on Railway

The `init_schema` is called from app lifespan on every boot. Deploying this change:
1. Push to main.
2. Railway auto-rebuilds and restarts.
3. On boot, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS owning_class TEXT NOT NULL DEFAULT ''` runs and is a no-op if already present.
4. The pre-existing "Haunted House 3I4" booth ends up with `owning_class = ''` until admin edits it.

Zero downtime. No data loss. No manual SQL.

## Out of scope

- Per-class withdrawal / payout tracking.
- Multi-class booths (one booth owned by 2+ classes). YAGNI; admins can encode "3I4+3I5" as the string if absolutely needed.
- Renaming a class globally (e.g., reassigning all "3I4" rows to "3I5"). YAGNI; can be a one-off SQL.
- Class authentication (logging in as a class to see your own booth performance). YAGNI; the admin export covers this.
