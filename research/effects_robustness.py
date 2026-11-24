"""P2 robustness gate: run the effect extractor over the NL2Bash corpus.

Prints aggregate results as JSON (no commands are emitted). Exits 1 if the
gate fails: any crash, a parse error not reported as opaque, or p99 > 5 ms.

Usage: uv run python research/effects_robustness.py data/nl2bash/all.cm
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from rootsh.analysis.effects import Context, analyze_shell

EXPECTED_SHA256 = "3a72eaced7fa14a0938354cefc42b2dcafb2d47297102f1279086e18c3abe57e"
CTX = Context("/w", "/home/u", {"USER": "u"})
P99_LIMIT_MS = 5.0


def percentile(sorted_values: list[float], fraction: float) -> float:
    index = min(len(sorted_values) - 1, round(fraction * (len(sorted_values) - 1)))
    return sorted_values[index]


def main(path: Path) -> int:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        print(
            f"corpus sha256 {digest} does not match the pinned {EXPECTED_SHA256}", file=sys.stderr
        )
        return 1
    commands = raw.decode("utf-8").splitlines()

    crashes: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    effect_kinds: Counter[str] = Counter()
    timings_ms: list[float] = []
    parse_errors = opaque = unreported_parse_errors = fully_analysed = 0

    for command in commands:
        started = time.perf_counter_ns()
        try:
            analysis = analyze_shell(command, CTX)
        except Exception as exc:  # noqa: BLE001 - counting crashes is the point
            crashes[type(exc).__name__] += 1
            continue
        finally:
            timings_ms.append((time.perf_counter_ns() - started) / 1e6)
        parse_errors += analysis.parse_error
        if analysis.parse_error and "parse_error" not in analysis.opaque:
            unreported_parse_errors += 1
        if analysis.fully_analysed:
            fully_analysed += 1
        else:
            opaque += 1
        for reason in analysis.opaque:
            kind, _, name = reason.partition(":")
            reasons[kind] += 1
            if kind == "unknown_program":
                unknown[name] += 1
        effect_kinds.update(e.kind.value for e in analysis.effects)

    timings_ms.sort()
    total = len(commands)
    report: dict[str, Any] = {
        "corpus": {"file": "NL2Bash all.cm", "sha256": digest, "commands": total},
        "crashes": sum(crashes.values()),
        "crash_types": dict(crashes),
        "parse_errors": parse_errors,
        "parse_errors_not_reported_opaque": unreported_parse_errors,
        "fully_analysed": fully_analysed,
        "opaque": opaque,
        "opaque_rate": round(opaque / total, 4),
        "commands_by_opaque_reason": dict(reasons.most_common()),
        "top_unknown_programs": dict(unknown.most_common(25)),
        "effects_by_kind": dict(effect_kinds.most_common()),
        "timing_ms": {
            "mean": round(statistics.fmean(timings_ms), 4),
            "p50": round(percentile(timings_ms, 0.50), 4),
            "p95": round(percentile(timings_ms, 0.95), 4),
            "p99": round(percentile(timings_ms, 0.99), 4),
            "max": round(timings_ms[-1], 4),
        },
    }
    report["gate_passed"] = (
        report["crashes"] == 0
        and unreported_parse_errors == 0
        and report["timing_ms"]["p99"] <= P99_LIMIT_MS
    )
    print(json.dumps(report, indent=2))
    return 0 if report["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
