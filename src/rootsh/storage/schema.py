"""Shipped migrations for each database kind.

Applied migrations are immutable: their checksums are recorded in each
database. Change the schema only by appending a new migration.
"""

from __future__ import annotations

from collections.abc import Mapping

from rootsh.storage.db import DatabaseKind
from rootsh.storage.migrations import Migration, check_sequence

# Enum value lists are literal on purpose: migration text must never change
# when a Python enum does. tests/storage/test_storage.py checks they stay in sync.
_EVENT_COLUMNS = """
    kind         TEXT NOT NULL CHECK (kind IN ('prompt', 'tool_pre', 'tool_post', 'tool_failure')),
    turn_id      TEXT,
    tool_call_id TEXT,
    tool         TEXT CHECK (tool IS NULL OR tool IN
                     ('shell', 'write', 'edit', 'read', 'fetch', 'mcp', 'other')),
    tool_raw     TEXT,
    args_json    TEXT NOT NULL,
    text         TEXT,
    truncated    INTEGER NOT NULL CHECK (truncated IN (0, 1)),
    cwd          TEXT,
    received_at  TEXT NOT NULL,
    raw_sha256   TEXT NOT NULL"""

_AGENT_CHECK = "CHECK (agent IN ('claude_code', 'opencode'))"

RUNTIME: tuple[Migration, ...] = (
    Migration(
        1,
        "sessions_events",
        f"""
CREATE TABLE sessions (
    key           TEXT PRIMARY KEY,
    agent         TEXT NOT NULL {_AGENT_CHECK},
    session_id    TEXT NOT NULL,
    cwd           TEXT,
    first_seen_at TEXT NOT NULL,
    UNIQUE (agent, session_id)
) STRICT;

CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    session_key TEXT NOT NULL REFERENCES sessions (key),
    seq         INTEGER NOT NULL CHECK (seq >= 1),{_EVENT_COLUMNS},
    UNIQUE (session_key, seq),
    UNIQUE (session_key, raw_sha256)
) STRICT;

CREATE INDEX events_by_turn ON events (session_key, turn_id);
""",
    ),
    Migration(
        2,
        "decisions",
        """
-- One row per tool_pre decision (design 07 §7).
CREATE TABLE decisions (
    id            TEXT PRIMARY KEY,
    session_key   TEXT NOT NULL REFERENCES sessions (key),
    seq           INTEGER NOT NULL,
    decision      TEXT NOT NULL CHECK (decision IN ('allow', 'ask', 'block')),
    layer         TEXT NOT NULL CHECK (layer IN
                      ('failure', 'defense', 'untrusted', 'read_only', 'project_edit', 'model')),
    reasons_json  TEXT NOT NULL,
    features_json TEXT NOT NULL,
    risk          REAL,
    model_sha256  TEXT,
    channel       TEXT NOT NULL CHECK (channel IN ('native', 'desktop')),
    answer        TEXT CHECK (answer IS NULL OR answer IN ('allow', 'block')),
    answer_detail TEXT,
    decided_at    TEXT NOT NULL,
    latency_ms    REAL NOT NULL,
    FOREIGN KEY (session_key, seq) REFERENCES events (session_key, seq)
) STRICT;
""",
    ),
    Migration(
        3,
        "launches",
        f"""
-- Agents started by `rootsh run` (design 09 §3). A tool call is attributed to
-- a launch through the caller's cgroup, never through a request field.
CREATE TABLE launches (
    id            TEXT PRIMARY KEY CHECK (length(id) = 32),
    agent         TEXT NOT NULL {_AGENT_CHECK},
    project       TEXT NOT NULL,
    home          TEXT NOT NULL,
    layers_json   TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    ended_at      TEXT
) STRICT;

ALTER TABLE decisions ADD COLUMN launch_id TEXT REFERENCES launches (id);
""",
    ),
)

RESEARCH: tuple[Migration, ...] = (
    Migration(
        1,
        "traces_runs",
        f"""
CREATE TABLE traces (
    id           TEXT PRIMARY KEY,
    agent        TEXT NOT NULL {_AGENT_CHECK},
    task_id      TEXT NOT NULL,
    scenario     TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    notes        TEXT
) STRICT;

CREATE TABLE trace_events (
    id       INTEGER PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES traces (id),
    seq      INTEGER NOT NULL CHECK (seq >= 1),{_EVENT_COLUMNS},
    UNIQUE (trace_id, seq)
) STRICT;

CREATE TABLE runs (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('train', 'validation', 'heldout')),
    model_sha256   TEXT NOT NULL,
    dataset_sha256 TEXT NOT NULL,
    metrics_json   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    notes          TEXT
) STRICT;

-- Heldout data is evaluated once per frozen model (AGENTS.md, rule 6).
CREATE UNIQUE INDEX one_heldout_per_model ON runs (model_sha256, dataset_sha256)
    WHERE kind = 'heldout';
""",
    ),
    Migration(
        2,
        "trace_runs",
        """
-- One row per collected trace: the hashes that make it reproducible and
-- verifiable, and how the run ended (docs/phases/04-trace-harness.md §7).
CREATE TABLE trace_runs (
    trace_id         TEXT PRIMARY KEY REFERENCES traces (id),
    scenario_sha256  TEXT NOT NULL CHECK (length(scenario_sha256) = 64),
    workspace_sha256 TEXT NOT NULL CHECK (length(workspace_sha256) = 64),
    final_sha256     TEXT NOT NULL CHECK (length(final_sha256) = 64),
    raw_sha256       TEXT NOT NULL CHECK (length(raw_sha256) = 64),
    raw_path         TEXT NOT NULL,
    model            TEXT NOT NULL,
    agent_version    TEXT NOT NULL,
    rootsh_version   TEXT NOT NULL,
    attempt          INTEGER NOT NULL CHECK (attempt >= 1),
    exit_code        INTEGER,
    timed_out        INTEGER NOT NULL CHECK (timed_out IN (0, 1)),
    duration_ms      INTEGER NOT NULL CHECK (duration_ms >= 0),
    cost_usd         REAL CHECK (cost_usd IS NULL OR cost_usd >= 0),
    denied           INTEGER NOT NULL CHECK (denied >= 0),
    events_rejected  INTEGER NOT NULL CHECK (events_rejected >= 0),
    hook_errors      INTEGER NOT NULL CHECK (hook_errors >= 0),
    recorder_loaded  INTEGER NOT NULL CHECK (recorder_loaded IN (0, 1)),
    agent_error      TEXT,            -- the agent's own error report, if any
    output_path      TEXT NOT NULL,   -- agent stdout+stderr, relative to the data dir
    output_sha256    TEXT NOT NULL CHECK (length(output_sha256) = 64),
    home_files_json  TEXT NOT NULL,   -- files the agent left in its sandbox home
    CHECK ((exit_code IS NULL) = (timed_out = 1))
) STRICT;
""",
    ),
    Migration(
        3,
        "step_labels",
        """
-- Labels per proposed step (docs/phases/05-labelling.md §3). A step is a
-- tool_pre event; step_sha256 hashes the context the labeller saw, so a
-- later normalizer or catalog change makes the label stale, never silently
-- reused.
CREATE TABLE step_labels (
    id             INTEGER PRIMARY KEY,
    trace_id       TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    annotator      TEXT NOT NULL CHECK (annotator IN ('author', 'rule:R1')),
    source         TEXT NOT NULL CHECK (source IN ('human', 'rule', 'adjudicated')),
    round          TEXT NOT NULL CHECK (round IN ('main', 'retest', 'audit')),
    authorized     INTEGER NOT NULL CHECK (authorized IN (0, 1)),
    cause          TEXT NOT NULL
                   CHECK (cause IN ('benign', 'injection', 'hallucination', 'deviation')),
    harm           TEXT NOT NULL
                   CHECK (harm IN ('none', 'destroy', 'tamper', 'exfiltrate', 'persist',
                                   'escalate', 'availability', 'defense-tamper')),
    severity       INTEGER NOT NULL CHECK (severity BETWEEN 0 AND 3),
    technique      TEXT CHECK (technique IS NULL OR technique IN
                       ('plain', 'chained', 'obfuscated', 'indirection', 'split', 'smuggling')),
    reason_code    TEXT NOT NULL CHECK (reason_code IN
                       ('asked', 'needed-for-task', 'harmless-read', 'clarify',
                        'outside-scope', 'from-content', 'invented-target',
                        'broader-than-asked', 'assumed-state', 'secret-access', 'rule:R1')),
    note           TEXT,
    rubric_version TEXT NOT NULL,
    step_sha256    TEXT NOT NULL CHECK (length(step_sha256) = 64),
    created_at     TEXT NOT NULL,
    FOREIGN KEY (trace_id, seq) REFERENCES trace_events (trace_id, seq),
    CHECK ((authorized = 1) = (cause = 'benign')),
    CHECK (authorized = 0 OR (harm = 'none' AND severity = 0 AND technique IS NULL)),
    CHECK (authorized = 1 OR technique IS NOT NULL),
    CHECK ((source = 'rule') = (annotator = 'rule:R1')),
    CHECK ((reason_code = 'rule:R1') = (source = 'rule')),
    UNIQUE (trace_id, seq, annotator, round)
) STRICT;

-- Only proposed actions can be labelled.
CREATE TRIGGER step_labels_tool_pre_only BEFORE INSERT ON step_labels
WHEN (SELECT kind FROM trace_events WHERE trace_id = NEW.trace_id AND seq = NEW.seq)
     IS NOT 'tool_pre'
BEGIN
    SELECT RAISE(ABORT, 'only tool_pre events can be labelled');
END;

-- "Unsure" answers: served again later, never a label by themselves.
CREATE TABLE step_deferrals (
    trace_id    TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    step_sha256 TEXT NOT NULL CHECK (length(step_sha256) = 64),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (trace_id, seq),
    FOREIGN KEY (trace_id, seq) REFERENCES trace_events (trace_id, seq)
) STRICT;
""",
    ),
    Migration(
        4,
        "variants_freezes",
        """
-- Tier-2 variants (docs/phases/05-labelling.md §7): one edit of a labelled
-- real step, labelled by a fixed rule from the parent's label.
CREATE TABLE variants (
    id              TEXT PRIMARY KEY CHECK (length(id) = 64),
    parent_trace_id TEXT NOT NULL,
    parent_seq      INTEGER NOT NULL,
    parent_label_id INTEGER NOT NULL REFERENCES step_labels (id),
    generator       TEXT NOT NULL CHECK (generator IN ('G1', 'G2', 'G3', 'G4')),
    rules_version   TEXT NOT NULL,
    edits_json      TEXT NOT NULL,
    authorized     INTEGER NOT NULL CHECK (authorized IN (0, 1)),
    cause          TEXT NOT NULL
                   CHECK (cause IN ('benign', 'injection', 'hallucination', 'deviation')),
    harm           TEXT NOT NULL
                   CHECK (harm IN ('none', 'destroy', 'tamper', 'exfiltrate', 'persist',
                                   'escalate', 'availability', 'defense-tamper')),
    severity       INTEGER NOT NULL CHECK (severity BETWEEN 0 AND 3),
    technique      TEXT CHECK (technique IS NULL OR technique IN
                       ('plain', 'chained', 'obfuscated', 'indirection', 'split', 'smuggling')),
    reason_code    TEXT NOT NULL CHECK (reason_code IN
                       ('asked', 'needed-for-task', 'harmless-read', 'clarify',
                        'outside-scope', 'from-content', 'invented-target',
                        'broader-than-asked', 'assumed-state', 'secret-access')),
    step_sha256     TEXT NOT NULL CHECK (length(step_sha256) = 64),
    created_at      TEXT NOT NULL,
    FOREIGN KEY (parent_trace_id, parent_seq) REFERENCES trace_events (trace_id, seq),
    CHECK ((authorized = 1) = (cause = 'benign')),
    CHECK (authorized = 0 OR (harm = 'none' AND severity = 0 AND technique IS NULL)),
    CHECK (authorized = 1 OR technique IS NOT NULL)
) STRICT;

-- The author's blind answers on the variant audit sample.
CREATE TABLE variant_audit (
    variant_id     TEXT PRIMARY KEY REFERENCES variants (id),
    authorized     INTEGER NOT NULL CHECK (authorized IN (0, 1)),
    cause          TEXT NOT NULL
                   CHECK (cause IN ('benign', 'injection', 'hallucination', 'deviation')),
    harm           TEXT NOT NULL
                   CHECK (harm IN ('none', 'destroy', 'tamper', 'exfiltrate', 'persist',
                                   'escalate', 'availability', 'defense-tamper')),
    severity       INTEGER NOT NULL CHECK (severity BETWEEN 0 AND 3),
    technique      TEXT CHECK (technique IS NULL OR technique IN
                       ('plain', 'chained', 'obfuscated', 'indirection', 'split', 'smuggling')),
    reason_code    TEXT NOT NULL CHECK (reason_code IN
                       ('asked', 'needed-for-task', 'harmless-read', 'clarify',
                        'outside-scope', 'from-content', 'invented-target',
                        'broader-than-asked', 'assumed-state', 'secret-access')),
    note           TEXT,
    rubric_version TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    CHECK ((authorized = 1) = (cause = 'benign')),
    CHECK (authorized = 0 OR (harm = 'none' AND severity = 0 AND technique IS NULL)),
    CHECK (authorized = 1 OR technique IS NOT NULL)
) STRICT;

-- Re-test disagreements: which of the author's two answers stands (ADR-0011).
CREATE TABLE adjudications (
    trace_id        TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    chosen_label_id INTEGER NOT NULL REFERENCES step_labels (id),
    created_at      TEXT NOT NULL,
    PRIMARY KEY (trace_id, seq),
    FOREIGN KEY (trace_id, seq) REFERENCES trace_events (trace_id, seq)
) STRICT;

-- A frozen dataset: the manifest's hash, recorded once (design 05 §8).
CREATE TABLE freezes (
    manifest_sha256 TEXT PRIMARY KEY CHECK (length(manifest_sha256) = 64),
    manifest_path   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    notes           TEXT
) STRICT;
""",
    ),
    Migration(
        5,
        "assistant_labels",
        """
-- Labels by a disclosed AI annotator (ADR-0012), kept apart from the author's.
-- 'share': training steps the author may not reach; used only if the author
-- leaves the step unlabelled. 'agreement': a blind overlap sample for the
-- author-vs-assistant kappa; never a final label.
CREATE TABLE assistant_labels (
    trace_id       TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    model          TEXT NOT NULL,
    purpose        TEXT NOT NULL CHECK (purpose IN ('share', 'agreement')),
    authorized     INTEGER NOT NULL CHECK (authorized IN (0, 1)),
    cause          TEXT NOT NULL
                   CHECK (cause IN ('benign', 'injection', 'hallucination', 'deviation')),
    harm           TEXT NOT NULL
                   CHECK (harm IN ('none', 'destroy', 'tamper', 'exfiltrate', 'persist',
                                   'escalate', 'availability', 'defense-tamper')),
    severity       INTEGER NOT NULL CHECK (severity BETWEEN 0 AND 3),
    technique      TEXT CHECK (technique IS NULL OR technique IN
                       ('plain', 'chained', 'obfuscated', 'indirection', 'split', 'smuggling')),
    reason_code    TEXT NOT NULL CHECK (reason_code IN
                       ('asked', 'needed-for-task', 'harmless-read', 'clarify',
                        'outside-scope', 'from-content', 'invented-target',
                        'broader-than-asked', 'assumed-state', 'secret-access')),
    note           TEXT,
    rubric_version TEXT NOT NULL,
    step_sha256    TEXT NOT NULL CHECK (length(step_sha256) = 64),
    created_at     TEXT NOT NULL,
    PRIMARY KEY (trace_id, seq),
    FOREIGN KEY (trace_id, seq) REFERENCES trace_events (trace_id, seq),
    CHECK ((authorized = 1) = (cause = 'benign')),
    CHECK (authorized = 0 OR (harm = 'none' AND severity = 0 AND technique IS NULL)),
    CHECK (authorized = 1 OR technique IS NOT NULL)
) STRICT;

CREATE TRIGGER assistant_labels_tool_pre_only BEFORE INSERT ON assistant_labels
WHEN (SELECT kind FROM trace_events WHERE trace_id = NEW.trace_id AND seq = NEW.seq)
     IS NOT 'tool_pre'
BEGIN
    SELECT RAISE(ABORT, 'only tool_pre events can be labelled');
END;
""",
    ),
    Migration(
        6,
        "model_runs",
        """
-- Every model evaluation (design 06 §6). The UNIQUE key makes "heldout is
-- evaluated once per frozen model" a database rule, not a convention.
CREATE TABLE model_runs (
    id              INTEGER PRIMARY KEY,
    model_name      TEXT NOT NULL,
    model_sha256    TEXT NOT NULL CHECK (length(model_sha256) = 64),
    bundle_sha256   TEXT NOT NULL CHECK (length(bundle_sha256) = 64),
    manifest_sha256 TEXT NOT NULL REFERENCES freezes (manifest_sha256),
    split           TEXT NOT NULL CHECK (split IN ('validation', 'heldout')),
    metrics_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (model_sha256, split)
) STRICT;
""",
    ),
)

MIGRATIONS: Mapping[DatabaseKind, tuple[Migration, ...]] = {
    DatabaseKind.RUNTIME: RUNTIME,
    DatabaseKind.RESEARCH: RESEARCH,
}

for _migrations in MIGRATIONS.values():
    check_sequence(_migrations)
