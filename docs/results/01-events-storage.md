# P1 — Event model and storage: results

**Design:** `docs/design/01-events-storage.md` · **Branch:**
`feature/p1-events-storage` · **Date:** 2026-11-18
**Environment:** Python 3.12.15 (uv), SQLite 3.53.1, mypy 2.4.1,
pytest 9.2.0, ruff 0.17.5.

## Gate

| Gate item (plan.md) | Result |
|---|---|
| Migrations run | ✅ both DBs created from nothing, second run applies nothing, status clean (output below) [RUN] |
| A test writing to a non-temporary database fails | ✅ `tests/storage/test_db_guard.py`, 7/7 passed: outside-tmp path, raw `sqlite3.connect`, escaping symlink, URIs and odd arguments rejected before any file is created [RUN] |

### `rootsh db` on fresh files [RUN]

```
$ rootsh --version
rootsh 0.1.0

$ rootsh db status runtime runtime.db   # before
rootsh: error: runtime.db does not exist
exit=1

$ rootsh db migrate runtime runtime.db
applied now: 1
runtime database: runtime.db
applied: 1 (sessions_events)
pending: none
status: ok
exit=0

$ rootsh db migrate runtime runtime.db   # again
applied now: none (up to date)
runtime database: runtime.db
applied: 1 (sessions_events)
pending: none
status: ok
exit=0

$ rootsh db status runtime runtime.db
runtime database: runtime.db
applied: 1 (sessions_events)
pending: none
status: ok
exit=0

$ rootsh db migrate research research.db
applied now: 1
research database: research.db
applied: 1 (traces_runs)
pending: none
status: ok
exit=0

$ rootsh db migrate research research.db   # again
applied now: none (up to date)
…
exit=0

$ rootsh db migrate research runtime.db   # wrong kind
rootsh: error: this is a runtime database, not research
exit=1
```

## Acceptance criteria (design doc §9)

| # | Criterion | Evidence |
|---|---|---|
| 1 | migrate from nothing, idempotent, status clean | gate output above; `test_migrate_from_nothing_then_noop`, `test_migrate_then_status` |
| 2 | non-temporary DB in a test fails | `tests/storage/test_db_guard.py` |
| 3 | edited migration, newer DB, wrong kind raise and leave the DB unchanged | `test_edited_migration_is_refused_and_nothing_applies`, `test_newer_database_is_refused`, `test_kinds_cannot_be_swapped`, `test_wrong_kind_is_an_error`, `test_failed_migration_rolls_back_completely` |
| 4 | normalizers on real payloads, every row and parse-error path | `tests/normalize/test_normalize.py` (58) on `tests/fixtures/hook_payloads.json`, sanitized from P0/P1 recordings |
| 5 | ingest: idempotency, OpenCode turn linking, per-session seq | `test_ingest_*`, `test_opencode_tool_events_join_the_latest_prompt` |
| 6 | second heldout run for the same model and dataset rejected | `test_heldout_is_evaluated_once_per_model_and_dataset` |
| 7 | ruff, ruff format, mypy `--strict`, pytest green | below |

## Checks [RUN]

| Check | Result |
|---|---|
| `uv run pytest` | **112 passed** — test_normalize 58, test_storage 26, test_events 14, test_cli 7, test_db_guard 7 |
| `uv run ruff check` | All checks passed |
| `uv run ruff format --check` | 21 files already formatted |
| `uv run mypy` (strict) | no issues in 19 source files |

## Found while building

1. **OpenCode failure payloads carry the reason.** The design assumed a
   failed `tool.execute.after` had no text. The recorded payload has
   `error.message` and `error.error.reason`, so the failure text now says why
   the call was blocked. Design doc corrected.
2. **`status` must not write.** The first version created the migration
   ledger while reporting status. Now only `migrate` creates it, with a test
   that `status` leaves an unmigrated DB with no tables.
3. **`executescript` would break atomicity.** Python's `executescript`
   commits an open transaction before running, so migrations split their SQL
   with `sqlite3.complete_statement` and run inside one `BEGIN IMMEDIATE`.
   A failing migration rolls back completely (tested).
4. **ruff 0.17 formats code blocks inside Markdown.** `docs/` is excluded
   from ruff; design-doc pseudo-code is not Python.

## Decision

**Gate passed → merge P1.** Next is P2, the effect parser and host-state
probes, on tree-sitter-bash (P0 §4).
