# 01 — Event model and storage

**Phase:** P1 · **Branch:** `feature/p1-events-storage`
**Gate (plan.md):** migrations run; a test writing to a non-temporary database
fails.

## 1. Scope

In:
- **Event model.** One normalized `AgentEvent` type for both agents, with
  pure normalizers from the hook payloads verified in P0.
- **Storage.** Two SQLite databases (runtime, research), each with its own
  forward-only, checksummed migrations and an ingest API.
- **Test-database guard.**
- **CLI.** `rootsh db migrate` and `rootsh db status`.

Out (later phases add their own migrations):
- decisions (P7)
- labels (P5)
- kernel-denial events (P9)
- the hook transport (P7/P8)
- default install paths (P8)

## 2. Package and tooling

```
src/rootsh/
├── __init__.py          # __version__
├── events.py            # AgentEvent, enums, EventParseError
├── normalize/
│   ├── __init__.py      # normalize(agent, hook, payload, now) dispatch
│   ├── _fields.py       # typed access to untrusted payload fields
│   ├── claude_code.py
│   └── opencode.py
├── storage/
│   ├── __init__.py
│   ├── db.py            # open_database, Database kind, pragmas
│   ├── migrations.py    # Migration, migrate(), MigrationError
│   ├── schema.py        # migration lists for runtime and research
│   └── events_store.py  # ingest(), session/turn resolution
└── cli.py               # `rootsh db …`
tests/
```

- **Python 3.12** (uv-managed; `requires-python = ">=3.12"`).
- **Runtime dependencies for P1: none.** stdlib `sqlite3`, `json`,
  `hashlib`, `dataclasses`, `enum`, `argparse`.
- **Dev dependencies:** pytest, ruff, mypy (`--strict`), pinned in `uv.lock`.
- **Checks:** `uv run ruff check`, `uv run ruff format --check`,
  `uv run mypy`, `uv run pytest`. All four must be green before the merge.

## 3. Event model (`events.py`)

```python
class Agent(StrEnum):      claude_code, opencode
class EventKind(StrEnum):  prompt, tool_pre, tool_post, tool_failure
class Tool(StrEnum):       shell, write, edit, read, fetch, mcp, other

@dataclass(frozen=True, slots=True)
class AgentEvent:
    agent: Agent
    kind: EventKind
    session_id: str               # non-empty
    turn_id: str | None           # links tool events to the user prompt
    tool_call_id: str | None      # set for tool events, None for prompts
    tool: Tool | None             # None for prompts
    tool_raw: str | None          # agent's own tool name, never dropped
    args: Mapping[str, JSON]      # tool input; {} for prompts
    text: str | None              # prompt text, or tool output / error
    truncated: bool               # text was cut to MAX_TEXT_CHARS
    cwd: str | None
    received_at: datetime         # UTC, injected clock
    raw_sha256: str               # sha256 of canonical JSON of the payload
```

Invariants are enforced in `__post_init__`; a violation raises `ValueError`:
- `session_id` is non-empty;
- tool kinds have `tool`, `tool_raw` and `tool_call_id`, and prompts have
  none of them;
- `received_at` is timezone-aware UTC.

**Canonical JSON** (for `raw_sha256` and stored `args`): `sort_keys=True`,
`separators=(",", ":")`, `ensure_ascii=False`.

**Text limit:** `MAX_TEXT_CHARS = 262_144`. Longer text is cut at that
length and `truncated = True`; it is never silently dropped.

## 4. Normalizers (`normalize/`)

Pure functions: `(hook_name, payload: Mapping, now: datetime) -> AgentEvent`.
A payload that is not a JSON object, an unknown hook name, or a missing or
wrong-typed required field raises **`EventParseError`**. The caller turns that
into a block (fail closed, PLAN B5). Unknown *extra* fields are ignored, so
agent upgrades that add fields keep working; the digest still covers them.

### 4.1 Claude Code (fields verified in P0 §1 and P1 [RUN])

| Hook | Kind | `turn_id` | `tool_call_id` | `args` | `text` |
|---|---|---|---|---|---|
| `UserPromptSubmit` | prompt | `prompt_id` | — | `{}` | `prompt` |
| `PreToolUse` | tool_pre | `prompt_id` | `tool_use_id` | `tool_input` | — |
| `PostToolUse` | tool_post | `prompt_id` | `tool_use_id` | `tool_input` | from `tool_response` (below) |
| `PostToolUseFailure` | tool_failure | `prompt_id` | `tool_use_id` | `tool_input` | `error` |

Required on every hook: `session_id`, `prompt_id`, `hook_event_name` (must
equal the hook name). `cwd` is optional.

`tool_response` → text:
- a string → itself;
- an object with `stdout`/`stderr` → stdout, then stderr on a new line if
  non-empty (Bash, [RUN]);
- anything else → canonical JSON.

Tool map: `Bash`→shell and `Write`→write [RUN]; `Edit`→edit, `Read`→read and
`WebFetch`→fetch [DOC]; names starting `mcp__`→mcp. **Every other name →
`other`**, with `tool_raw` kept, until verified. Unverified tools are still
scored later; they are never dropped.

`prompt_id` is shared by the prompt and every tool event it causes [RUN], so
Claude Code tool events link to their prompt directly.

### 4.2 OpenCode v2.3.0 (fields verified in P0 §2 and P1 [RUN])

| Plugin hook | Kind | `turn_id` | `tool_call_id` | `args` | `text` |
|---|---|---|---|---|---|
| `session.prompt` | prompt | `messageID` | — | `{}` | `prompt.text` |
| `tool.execute.before` | tool_pre | **None** (resolved at ingest) | `id` | `input` | — |
| `tool.execute.after`, `status = "completed"` | tool_post | None | `id` | `input` | `result.content[*].text` joined by `\n` |
| `tool.execute.after`, `status = "error"` | tool_failure | None | `id` | `input` | `error.message`, plus `error.error.reason` when present; canonical JSON of `error` if neither exists; None if there is no `error` (there is no `result` [RUN]) |

Required: `sessionID` on every hook. Tool events also require `tool`, `id`
and `input` (an object). Any `status` other than `completed` or `error` →
`EventParseError`.

Tool map: `shell`→shell and `write`→write [RUN]; every other name → `other`.

**Turn linkage differs from Claude Code:** the prompt's `messageID` is the
*user* message, while tool events carry the *assistant* message [RUN]. Tool
events are linked at ingest to the latest prompt in the same session (§5.4).

## 5. Storage (`storage/`)

### 5.1 Two databases

| | Runtime | Research |
|---|---|---|
| Holds | live sessions and events (P7 adds decisions) | collected traces and evaluation runs (P5 adds labels) |
| `PRAGMA application_id` | `0x52535254` ("RSRT") | `0x52535253` ("RSRS") |
| Written by | the daemon | the harness, trainer and evaluator |

`open_database(path, kind)` checks `application_id`. A new file gets the
kind's ID; a file of the other kind, or a foreign SQLite file, raises
`DatabaseKindError`. So the runtime DB can never be migrated as the research
DB, or the reverse.

Connection pragmas: `journal_mode=WAL`, `synchronous=FULL` (events are an
audit trail), `foreign_keys=ON`, `busy_timeout=5000`, `trusted_schema=OFF`.
All tables are `STRICT`, and enum columns carry `CHECK (… IN (…))`.

### 5.2 Migrations (`migrations.py`)

`Migration(version: int, name: str, sql: str)`; checksum = sha256 of `sql`.
Each kind has a tuple whose versions are 1..N with no gaps; this is checked
at import time, and a gap is a programming error.

`status(conn, migrations)` is read-only: it reads the
`schema_migrations(version INTEGER PRIMARY KEY, name TEXT, checksum TEXT,
applied_at TEXT) STRICT` ledger (absent = nothing applied) and reports
applied, pending and problems.

`migrate(conn, migrations) -> list[int]` (the versions applied):
1. Run `status`; create the ledger only after it reports no problems.
2. Read the applied rows. Fail with `MigrationError` if any applied version
   is unknown (the DB is newer than the code), or if any checksum differs
   (the migration was edited after it was applied, or the DB was tampered
   with). **Nothing is applied in either case.**
3. Apply each pending migration in its own `BEGIN IMMEDIATE … COMMIT`,
   together with its `schema_migrations` row. A failure rolls that migration
   back and re-raises; earlier migrations stay committed.
4. Running it again is a no-op.

### 5.3 Schema v1

**Runtime — `0001_sessions_events`**
```
sessions(key TEXT PK              -- "<agent>:<session_id>"
         agent TEXT CHECK(...), session_id TEXT, cwd TEXT,
         first_seen_at TEXT, UNIQUE(agent, session_id)) STRICT
events(id INTEGER PK, session_key TEXT REFERENCES sessions(key),
       seq INTEGER                -- per-session arrival order, from 1
       kind TEXT CHECK(...), turn_id TEXT, tool_call_id TEXT,
       tool TEXT CHECK(... OR NULL), tool_raw TEXT, args_json TEXT,
       text TEXT, truncated INTEGER CHECK(truncated IN (0,1)),
       cwd TEXT, received_at TEXT, raw_sha256 TEXT,
       UNIQUE(session_key, seq), UNIQUE(session_key, raw_sha256)) STRICT
index events(session_key, turn_id)
```

**Research — `0001_traces_runs`**
```
traces(id TEXT PK, agent TEXT CHECK(...), task_id TEXT, scenario TEXT,
       collected_at TEXT, notes TEXT) STRICT
trace_events(id INTEGER PK, trace_id TEXT REFERENCES traces(id),
             seq INTEGER, <same event columns as runtime events>,
             UNIQUE(trace_id, seq)) STRICT
runs(id TEXT PK, kind TEXT CHECK(kind IN ('train','validation','heldout')),
     model_sha256 TEXT, dataset_sha256 TEXT, metrics_json TEXT,
     created_at TEXT, notes TEXT) STRICT
UNIQUE INDEX one_heldout_per_model ON runs(model_sha256, dataset_sha256)
  WHERE kind = 'heldout'
```
The partial unique index enforces **"heldout is evaluated once per frozen
model"** (AGENTS non-negotiable 6) in the database itself. A second heldout
run for the same model and dataset is a constraint error, not a policy that
someone has to remember (old audit, finding D8).

### 5.4 Ingest (`events_store.py`)

`ingest(conn, event) -> IngestResult(event_id, seq, turn_id, duplicate)`, in
one `BEGIN IMMEDIATE` transaction:
1. Upsert the session (`first_seen_at`/`cwd` come from its first event).
2. If `(session_key, raw_sha256)` already exists, return the existing row
   with `duplicate=True`. Hooks can be delivered twice, so ingest is
   idempotent.
3. Resolve `turn_id`: an event's own `turn_id` wins. An OpenCode tool event
   (`turn_id` None) takes the `turn_id` of the latest `prompt` event in its
   session. If no prompt exists, it stays None; P7 treats an action with no
   known user request as having empty authority.
4. Assign `seq = max(seq) + 1` for the session and insert.

## 6. Test-database guard

`tests/conftest.py` (session-scoped, autouse) replaces `sqlite3.connect` with
a wrapper. The wrapper allows only `":memory:"` and paths that resolve inside
pytest's base temp directory; anything else (including `file:` URIs and
symlinks resolving outside) raises **`DatabaseGuardError`** (in the test-only
module `tests/support/db_guard.py`; pytest would collect a class named `Test…`) before
a file is opened. Production code has no knowledge of tests: the guard wraps the one
function every SQLite access goes through.

The library has **no default database path**. Every call takes an explicit
path, so no test can reach a real database by forgetting an argument. Default
locations arrive with the installer (P8).

## 7. CLI

```
rootsh db migrate {runtime|research} PATH   # apply pending; print versions
rootsh db status  {runtime|research} PATH   # applied / pending / problems
```
Exit codes: `0` ok · `1` `MigrationError` / `DatabaseKindError` (message on
stderr) · `2` usage error.

## 8. Edge cases

| Case | Behavior |
|---|---|
| Hook payload not an object / not JSON-decodable upstream | `EventParseError` |
| Hook name unknown to the normalizer | `EventParseError` |
| Claude `hook_event_name` disagrees with the hook it was delivered as | `EventParseError` |
| Unknown tool name | `Tool.other`, `tool_raw` kept |
| Text longer than the limit | cut, `truncated=True` |
| Same payload delivered twice | one row; second call `duplicate=True` |
| OpenCode tool event before any prompt | `turn_id` None |
| Two sessions with the same `session_id` from different agents | separate (`key` includes agent) |
| DB newer than code / edited migration | `MigrationError`, nothing applied |
| Runtime DB opened as research (or foreign SQLite file) | `DatabaseKindError` |
| Concurrent writers | `BEGIN IMMEDIATE` + `busy_timeout`; `seq` assigned inside the transaction |
| Test opens a non-temporary DB | `DatabaseGuardError` |

## 9. Acceptance criteria (P1 gate)

1. `rootsh db migrate` creates both DBs from nothing; a second run applies
   nothing; `rootsh db status` reports them clean. Real output recorded in
   `docs/results/01-events-storage.md`.
2. A test that opens a database outside the temp directory fails with
   `DatabaseGuardError`.
3. The tampered-migration, newer-DB and wrong-kind cases each raise and leave
   the DB unchanged (tests).
4. Normalizer tests use **real P0/P1 payload shapes** (sanitized fixtures)
   for every row of §4.1 and §4.2, plus every `EventParseError` path.
5. Ingest tests cover idempotency, OpenCode turn resolution, and per-session
   `seq`.
6. A second heldout run for the same model and dataset is rejected by the
   research DB (test).
7. ruff, ruff format, mypy `--strict` and pytest are all green.
