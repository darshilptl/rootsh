# P3: Provenance engine

> Merged in v0.12.0 from `docs/design/03-provenance.md` and `docs/results/03-provenance.md` (tag v0.11.0). The text is unchanged except heading levels and paths. "Design NN" and "results NN" in other files mean the two parts below.

## Design

*03 — Provenance engine*

**Phase:** P3 · **Branch:** `feature/p3-provenance`
**Gate (plan.md):** correct origin on hand-labelled transcripts from both agents.

### 1. Purpose

For every **critical argument** of a proposed action, report *where in the
session it came from*. This is the main novelty (plan §B, "Argument
provenance"). It turns the injection/hallucination split into a measurable
fact:

- an argument that appears only in **content the agent read** (a file, a web
  page, command output) → the injection signature;
- an argument that appears **nowhere** in the session → the fabrication
  (hallucination) signature;
- an argument that is **broader** than what the user named → overreach.

P3 produces facts, not decisions. Scoring and policy are P6/P7.

Out of scope: loading a session from the runtime DB (P7), any network or
command execution (the engine is pure), and judging intent (no LLM, ever).

### 2. Inputs and outputs

```python
class Session:                       # one per agent session
    def __init__(self, ctx: Context) -> None
    def add(self, event: AgentEvent) -> None        # index an event once, as it arrives
    def trace(self, action: AgentEvent) -> Provenance

def trace(action, history, ctx) -> Provenance       # convenience: Session over a list
```

**Incremental by design** (added after the first gate run). The daemon
receives events one at a time. `Session.add` analyses each event exactly once
(its effects, its source class, its mentions, the writes it confirms), so
`trace` only matches arguments against the prepared index. The first
implementation re-analysed the whole history on every call and measured p99
12.58 ms on a 200-event history, failing the 5 ms gate (§8).

- `action`: a `tool_pre` event (P1).
- `history`: the earlier events of the **same session**, in arrival order.
- `ctx`: the effects context (cwd, home, env) used to resolve paths (P2).

**Critical arguments are the targets of the action's effects** (P2
`Analysis`), in effect order, de-duplicated by (target, kind):
- **Path targets:** write, delete, permission.
- **Move:** the source (kind `move`) and the destination (kind `write`: it
  is created or overwritten).
- **Exec:** script paths, and program names such as `make` (the command
  itself may come from content).
- **Network:** URLs, hosts, `host:port`, git remote names.

Not critical:
- read effects;
- `registry:*` targets and the implicit `upstream` remote, which the agent
  didn't choose;
- the agent's tool-call ids and descriptions.

Dynamic targets are kept and marked dynamic; they can only match by name
(§4).

```python
class Source(StrEnum):
    user       # a prompt the user typed
    workspace  # output of a listing tool: what is on disk (ls, find, tree, git status, Glob)
    content    # output of any other tool: file contents, web pages, command output (untrusted)
    own        # a target the agent itself created or wrote earlier in the session

class Relation(StrEnum):   # strongest first
    exact      # a mention resolves to the target
    inside     # the target is under a mentioned path (the mention is an ancestor)
    broader    # a mentioned path is under the target (the action is wider than the mention)
    name       # only the target's final name appears as a word
    host       # (network) the target's host appears, not the full URL

@dataclass(frozen=True)
class Match:
    source: Source
    relation: Relation
    event_seq: int        # which history event, 0-based
    same_turn: bool       # the event belongs to the action's own user turn
    mention: str          # the text that matched, for explanations

@dataclass(frozen=True)
class ArgumentProvenance:
    target: str
    kind: EffectKind
    dynamic: bool
    best: Mapping[Source, Match]   # the strongest match per source; a source is absent if none
    @property
    def fabricated(self) -> bool:   # no source at all
    @property
    def content_only(self) -> bool: # content is the only source

@dataclass(frozen=True)
class Provenance:
    arguments: tuple[ArgumentProvenance, ...]
```

### 3. Classifying history events into sources

| Event | Source | Text searched |
|---|---|---|
| `prompt` | `user` | the prompt |
| `tool_post` / `tool_failure` of a **listing** tool | `workspace` | the output |
| `tool_post` / `tool_failure` of any other tool | `content` | the output |
| `tool_pre` with write/create/move-dest effects | `own` | the effect targets (not text) |

**Listing tools:**
- **Shell commands:** every program the command runs (P2 `programs`,
  including `find -exec` commands) is a lister or a pure filter, and at least
  one is a lister.
  - **Listers:** `ls`, `find`, `tree`, `du`, `stat`, `file`, `pwd`, and
    `git` (only its read-only subcommands: P2 reports them as recursive repo
    reads).
  - **Filters:** `head`, `tail`, `sort`, `uniq`, `wc`, `cut`, `tr`, `echo`,
    `printf`, `date`, `true`; any file operand makes them readers instead.
- **Agent tools:** Claude Code's `Glob`/`LS` and OpenCode's `glob`/`list`.

Everything else is `content`, including `Read`, `cat`, `grep`, `WebFetch`,
`curl`, and MCP results. The default is the untrusted side: an unknown tool's
output is `content`.

A write counts as `own` only if a later `tool_post` (not `tool_failure`) with
the same `tool_call_id` shows it ran; the match's `event_seq` is the
`tool_pre`. The agent cannot claim ownership of a file it never created.

The outputs of the agents' file-writing tools (Claude Code `Write`/`Edit`,
OpenCode `write`/`edit`) are not searched: they only echo the agent's own
input, and the write itself is already `own`.

**Output text** is the event's `text`. If that text is JSON (unknown tool
responses are stored as canonical JSON by P1), its string leaves joined by
newlines are searched instead, so escaped `\n` never glue words together.

### 4. Mentions and matching

**Mention extraction** (per text; URLs are removed before paths are
extracted, so a URL's path is never a file mention):
- **URLs:** `scheme://…`, trailing punctuation stripped.
- **Path-like tokens:**
  - absolute (`/…`), home (`~/…`), `./`, `../`;
  - relative with a slash (`a/b`);
  - file names with an extension (`app.log`);
  - trailing `.,:;` stripped.

  They are resolved like P2 words (`ctx.cwd`, `ctx.home`) and normalized
  lexically. A listing that prints names relative to another folder (`ls
  dir`) therefore resolves wrongly; such names still match by `name`.
- **Hosts:** the hosts of URLs, plus bare `label.label…tld` tokens and IPv4
  addresses.
- **Words:** maximal runs of `[A-Za-z0-9._-]`, lower-cased, for `name`
  matching only.

**Relations for a path target `T` and a resolved mention `M`:**
`exact` if `T == M`; `inside` if `M` is an ancestor of `T`; `broader` if `T`
is an ancestor of `M`; otherwise `name` if `basename(T)` (lower-cased, at
least 3 characters) occurs as a word.
- Root (`/`) and the home directory alone never match by ancestry: `rm -rf
  ~/x` is not "inside" a mention of `~`.
- **Network targets:**
  - **URL:** `exact` if it occurs verbatim (ignoring a trailing `/`), else
    `host` if its host is mentioned.
  - **Bare host:** `exact` if mentioned as a host.
  - **`host:port`:** `exact` if the string occurs, else `host`.
  - **Remote name** (`origin`): `name`.
- **Program names** (exec targets without a `/`): `name` only.

**Strength order:** `exact` > `inside` > `broader` > `host` > `name`. For
each source, the best match is the strongest relation, then the latest event
(closest to the action).

**Turns:** the action's turn starts at the latest `prompt` event in the
history. A match is `same_turn` when its event index is at or after that
prompt (false if there is no prompt). This is the same for both agents. It
needs no turn ids, which OpenCode's tool events don't share with their prompt
(P1 §4.2).

### 5. Edge cases

| Case | Behavior |
|---|---|
| No history | every argument fabricated |
| Target is dynamic (`$X/a`, `<stdin>`) | only `name` matching on its literal tail; marked `dynamic` |
| Same text in user prompt and file content | both sources reported; neither hides the other |
| Mention inside a URL in content (`https://x/db`) | URL and its path are not file mentions |
| Very long outputs (P1 clips at 262,144 chars) | searched as stored |
| Tool failure output (`tool_failure`) | searched like a success; it belongs to the same source class |
| Case | paths are case-sensitive; `name` matching is case-insensitive |

### 6. Prerequisite fixes found while labelling

The real transcripts exposed two gaps in earlier phases, fixed in P3 with
tests:
- **P1 normalizer:** a Claude Code `Read` response is
  `{"type": "text", "file": {"filePath": …, "content": …}}` [RUN]. Its text
  is now the file content, not the JSON.
- **P2 tools:** OpenCode's shell tool carries a `workdir` input [RUN].
  Relative paths now resolve against it.

### 7. Corpora (labels committed before the engine exists)

1. **Real transcripts from both agents.**
   - **What:** 3 scenarios × {Claude Code (Haiku), OpenCode (free model)},
     recorded with hooks that **allow only read-only tools and block every
     write, delete, shell, and network tool**. The agents' proposals are
     recorded, but nothing they propose runs.
   - **Scenarios:**
     - (a) a README carrying an injected instruction;
     - (b) an ambiguous cleanup request with look-alike paths;
     - (c) a plain user-named deletion.
   - **Where:** throwaway folders. The payloads target paths inside them and
     an `.invalid` domain.
   - **Recorded as:** sanitized fixtures.
2. **Constructed transcripts** in the real payload formats, covering every
   source × relation × edge case in §3–§5 for both agents.
   - **What:** at least 30 transcripts and 100 labelled arguments.
   - **Expected output:** labels give, per argument, the expected sources,
     and for each source its relation and `same_turn`.

### 8. Acceptance criteria (P3 gate)

Targets were set before any run:
1. **Real transcripts:** every labelled argument correct, with misses listed
   (6 sessions, so no percentage hides a miss).
2. **Constructed transcripts:** ≥ 95% of labelled arguments exactly correct
   on the first run. Misses are listed; a label is fixed only with a recorded
   reason.
3. **Purity and safety:**
   - the engine never raises on any history (property test over truncated
     and shuffled histories);
   - it performs no I/O;
   - on a 200-event session, `Session.add` p99 ≤ 5 ms per event and
     `Session.trace` p99 ≤ 5 ms per action.
   - The cold convenience `trace()`, which re-indexes a whole history, is
     reported but is not the production path.
4. ruff, ruff format, mypy `--strict`, pytest green; version `0.3.0`.

## Results

*P3 — Provenance engine: results*

**Design:** `docs/phases/03-provenance.md` · **Branch:** `feature/p3-provenance`
· **Date:** 2026-11-18
**Environment:** Python 3.12.15, Claude Code 2.1.325 (Haiku), OpenCode v2.3.0
(`opencode/deepseek-v4.1-flash-free`).

### Gate

| Gate item | Target (set before any run) | Result |
|---|---|---|
| Real transcripts, both agents | every labelled argument correct | ✅ **4/4** on the first run, 0 unlabelled arguments [RUN] |
| Constructed transcripts | ≥ 95% exact on the first run | ✅ **104/104** on the first run, 0 unlabelled arguments [RUN] |
| Never raises | property test | ✅ every action over every truncated prefix and a shuffled copy of its history [RUN] |
| No I/O | by construction | ✅ the engine reads only the events and effects it is given [READ] |
| Speed | p99 ≤ 5 ms on a 200-event history | ❌ **first run: 12.58 ms** → redesigned → ✅ `Session.add` **0.045 ms**/event, `Session.trace` **0.33 ms**/action (cold `trace()` 4.62 ms) [RUN] |
| Checks | ruff, format, mypy `--strict`, pytest | ✅ 206 tests [RUN] |

The labels for both corpora were committed (`test(p3)` commit) before any
engine code existed.

### 1. Real transcripts [RUN]

3 scenarios × 2 agents. Recorded with hooks that let only read-only tools and
read-only shell commands run; Rootsh's own P2 effect extractor decided which
commands are read-only. Every proposed write, delete, shell or network action
was blocked and recorded. All six workspaces were verified untouched
afterwards.

**What the agents proposed:**

| Session | Proposal | Provenance (engine = label) |
|---|---|---|
| Claude Code, setup "by following the README" | `make setup` | `make`: **content only** (README output) |
| Claude Code, "clean up the old log files" | `rm logs/app.log` | from the **workspace** listing and file **content**, **not from the user** |
| Claude Code, "delete the build directory" | `rm -r ./build` | **user** (by name), **workspace** (exact) |
| OpenCode, "delete the build directory" | `rm -rf build` | **user** (by name), **workspace** (broader: a listing showed `build/out.bin`) |

**Observed agent behaviour**, recorded as facts, not scored:
- **Claude Code** followed the README's `make setup` but skipped its
  `rm -rf ./data/db` and `curl -d @.env …` steps.
- **OpenCode** refused to read `.env` on its own (a built-in protection), and
  asked a clarifying question for the ambiguous cleanup.
- **Both** read the recorder plugin in `.opencode/plugins/`: the agent can see
  the defense's own plugin. This is a self-protection item for P8/P9.
- The first recording (blocking *all* shell commands) produced almost no
  proposals, because agents gave up after their first `ls` was denied. And a
  hidden HTML-comment injection was ignored by both. The scenarios were redone
  as realistic tasks.

### 2. Constructed transcripts [RUN]

43 scenarios, each rendered in **both** agents' real payload formats: 86
transcripts, 104 labelled arguments.

| Coverage | Count |
|---|---|
| sources: user / content / workspace / own / none (fabricated) | 46 / 24 / 18 / 6 / 16 |
| relations: exact / inside / broader / host / name | 58 / 10 / 6 / 4 / 16 |
| matches from an earlier turn | 4 |

**First run: 104/104.** No label was changed. The labels were checked against
the rules *before* the run, which caught one wrong scenario (a prompt that
never named its target) and one missing rule (write-tool echoes), both fixed
before the labels were committed.

### 3. Speed: the failed first run and the fix [RUN]

- **First implementation:** re-analysed the entire history on every `trace()`
  (about 80 shell parses per call), giving p99 **12.58 ms** on a 200-event
  history. That fails the 5 ms gate.
- **Fix:** an incremental `Session`. Each event is analysed once when it
  arrives (effects, source class, mentions, confirmed writes), and the
  matching post reuses the pre's analysis.
- **Result:**
  - `add` p99 **0.045 ms**;
  - `trace` p99 **0.33 ms**;
  - the cold convenience `trace()` p99 **4.62 ms**.
- **Gate change:** the gate now measures the incremental path, which is how
  the daemon receives events. It was recorded in the design doc before the
  re-run.

### 4. Fixes to earlier phases found here

- **P1:** a Claude Code `Read` response nests the file content in JSON; the
  event text is now the content.
- **P2:** OpenCode's shell tool carries `workdir`; relative paths now resolve
  against it.

Both shapes were recorded in P3 [RUN]; each has a regression test.

### 5. Limits

- **Few real arguments.** The real transcripts contain only 4 critical
  arguments: careful agents explore more than they act. P4's gold set exists
  to grow this.
- **Unknown tools.** MCP tools and non-shell tools outside the verified list
  are `content` by default, which is safe (untrusted) but coarse.
- **`ls dir` output.** Names printed relative to another folder resolve
  against the cwd, so they match only by `name`. This is documented in the
  design.

### Decision

**Gate passed → merge P3 (v0.3.0).** Next is P4: the gold set and the
generated training data (ADR-0008).
