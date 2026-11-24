"""P11: write every report table to docs/report-tables.md.

No number here is typed by hand: each section is the verbatim output of the
phase script that owns it, run now, against a scratch copy of the research
DB (the real DB is never opened for writing). Heldout is not re-evaluated:
the model section reads the stored P6 `model_runs` rows; the burden and
ablation scripts load the manifest with heldout excluded.

Usage (from the repo root):

  uv run python research/report_tables.py [--out docs/report-tables.md]
      [--latency-repo PATH] [--skip-latency] [--skip-kernel]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = "data/research.db"
MANIFEST = "data/freeze/manifest-a357bba6c02c.json"
BUNDLE = "data/model"
CATALOG = "research/scenarios"
NL2BASH = "data/nl2bash/all.cm"
DATA = "data"
LATENCY_REPO = str(ROOT.parents[1] / "Projects" / "store-oravity")
KERNEL_TESTS = ("tests/enforcement/run", "tests/research/collect/test_sandbox_security.py")


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    source: str  # where the prose interpretation lives
    argv: tuple[str, ...]  # `{db}` / `{scratch}` are filled in at run time
    shown: str  # the command as the reader should run it


def sections(latency_repo: str) -> list[Section]:
    py = "uv run python"
    return [
        Section("dataset", "1. Dataset: gold runs, tool calls, provenance (P4)",
                "docs/phases/04", ("research/gold_report.py", "{db}", CATALOG, DATA),
                f"{py} research/gold_report.py {DB} {CATALOG} {DATA}"),
        Section("labels", "2. Labelling plan (P5)", "docs/phases/05",
                ("research/label_plan.py", "{db}"),
                f"{py} research/label_plan.py {DB}"),
        Section("agreement", "3. Label agreement: assistant and test-retest (P5)", "docs/phases/05",
                ("research/label_agreement.py", "{db}"),
                f"{py} research/label_agreement.py {DB}"),
        Section("splits", "4. Variant audit, splits, shortcut audits (P5)", "docs/phases/05",
                ("research/label_audits.py", "{db}"),
                f"{py} research/label_audits.py {DB}"),
        Section("model", "5. Model: validation and the single heldout evaluation (P6)",
                "docs/phases/06", ("research/model_report.py", "{db}", BUNDLE),
                f"{py} research/model_report.py {DB} {BUNDLE}"),
        Section("burden", "6. Policy burden on validation (P7)", "docs/phases/07",
                ("research/policy_burden.py", "{db}", MANIFEST, BUNDLE),
                f"{py} research/policy_burden.py <copy of {DB}> {MANIFEST} {BUNDLE}"),
        Section("ablation", "7. Layer ablation and attack replay (P10)", "docs/phases/10",
                ("research/ablation.py", "{db}", MANIFEST, BUNDLE),
                f"{py} research/ablation.py <copy of {DB}> {MANIFEST} {BUNDLE}"),
        Section("robustness", "8. Effect extractor on NL2Bash (P2, current code)",
                "docs/phases/02 Results. Results 02 measured the P2 extractor (opaque "
                "14.7%); the catalog grew in later phases, so this current-code run differs",
                ("research/effects_robustness.py", NL2BASH),
                f"{py} research/effects_robustness.py {NL2BASH}"),
        Section("gate", "9. Live hook gate in the sandbox (P8, stored runs)",
                "docs/phases/08 Results", ("research/hook_gate_report.py",),
                f"{py} research/hook_gate_report.py"),
        Section("latency", "10. Decision latency on a large repository (P7)",
                "docs/phases/07 Results §3",
                ("research/policy_latency.py", latency_repo, BUNDLE, "{scratch}"),
                f"{py} research/policy_latency.py <large repo> {BUNDLE} <scratch dir>"),
    ]  # fmt: skip


def run(argv: list[str]) -> tuple[int, str, float]:
    started = time.perf_counter()
    done = subprocess.run(  # noqa: S603 - argv built here from fixed script paths
        argv, cwd=ROOT, capture_output=True, text=True, check=False
    )
    out = done.stdout + (f"\n[stderr]\n{done.stderr}" if done.returncode else "")
    return done.returncode, out.rstrip(), time.perf_counter() - started


def git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv
        ["git", *args],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def kernel_section() -> tuple[str, bool]:
    argv = [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", *KERNEL_TESTS]
    code, out, secs = run(argv)
    lines = out.splitlines()
    tail = [ln for ln in lines if ln.startswith("SKIPPED") or " passed" in ln or " failed" in ln]
    body = "\n".join(tail) or out
    shown = f"uv run pytest -q -rs {' '.join(KERNEL_TESTS)}"
    return (
        f"## 11. Kernel layer: adversarial sandbox tests (P8-P10)\n\n"
        f"Interpretation: docs/phases/09 and 10 Results §4. Command: `{shown}` "
        f"(exit {code}, {secs:.0f} s). Summary lines, verbatim:\n\n```text\n{body}\n```\n",
        code == 0,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/report-tables.md")
    ap.add_argument("--latency-repo", default=LATENCY_REPO)
    ap.add_argument("--skip-latency", action="store_true")
    ap.add_argument("--skip-kernel", action="store_true")
    args = ap.parse_args()
    ok = True
    parts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="rootsh-p11-") as tmp:
        scratch = Path(tmp)
        db = scratch / "research.db"
        shutil.copy2(ROOT / DB, db)  # every script reads the copy, never the real DB
        for s in sections(args.latency_repo):
            if s.key == "latency" and args.skip_latency:
                continue
            argv = [sys.executable, *(a.format(db=db, scratch=scratch) for a in s.argv)]
            code, out, secs = run(argv)
            ok &= code == 0
            print(f"{s.key}: exit {code}, {secs:.1f} s", file=sys.stderr)
            out = out.replace(str(db), DB).replace(str(scratch), "<scratch>")
            parts.append(
                f"## {s.title}\n\nInterpretation: {s.source}. Command: `{s.shown}` "
                f"(exit {code}, {secs:.1f} s). Output, verbatim:\n\n{fence(out)}\n"
            )
    if not args.skip_kernel:
        text, passed = kernel_section()
        ok &= passed
        parts.append(text)
    head = (
        "# Report tables (generated, P11)\n\n"
        "Generated by `uv run python research/report_tables.py`. Do not edit by "
        "hand: rerun the script. Each section is the unedited output of the phase "
        "script named in it, run on a scratch copy of the research DB. Heldout was "
        "evaluated once in P6; section 5 reads those stored rows and nothing here "
        "scores heldout again. Latency (section 10) and the kernel tests (section 11) "
        "are fresh measurements on this machine, so they vary between runs.\n\n"
        f"- commit: `{git('rev-parse', '--short', 'HEAD')}` "
        f"(`{git('describe', '--tags', '--always', '--dirty')}`)\n"
        f"- generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"- research DB: `{DB}`; manifest: `{MANIFEST}`; bundle: `{BUNDLE}`\n"
        f"- all scripts exited 0: {'yes' if ok else 'NO — see the failing section'}\n"
    )
    (ROOT / args.out).write_text(head + "\n" + "\n".join(parts))
    print(f"wrote {args.out}; ok={ok}", file=sys.stderr)
    return 0 if ok else 1


def fence(out: str) -> str:
    """Scripts that print Markdown tables are inlined; plain text is fenced."""
    if "\n|" in out:  # demote the scripts' own headings below this file's sections
        return "\n".join("###" + ln.lstrip("#") if ln.startswith("#") else ln
                          for ln in out.splitlines())  # fmt: skip
    return f"```text\n{out}\n```"


if __name__ == "__main__":
    sys.exit(main())
