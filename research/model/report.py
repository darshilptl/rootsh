"""P6 tables from `model_runs` (design 06 §6). Every number is read from the DB.

Usage: uv run python research/model/report.py data/gold/research.db data/models/p6-a357bba6c02c
"""

from __future__ import annotations

import json
import sqlite3
import sys

ORDER = ("full", "impact", "provenance", "action", "rules")


def _f(x: object) -> str:
    return "n/a" if x is None else f"{x:.3f}" if isinstance(x, float) else str(x)


def _ci(x: object) -> str:
    return "n/a" if not x else f"{x[0]:.3f} to {x[1]:.3f}"  # type: ignore[index]


def main(path: str) -> None:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT model_name, split, model_sha256, bundle_sha256, manifest_sha256, metrics_json, "
        "created_at FROM model_runs ORDER BY id"
    ).fetchall()
    bundles = {r[3] for r in rows}
    print(
        f"bundles: {', '.join(b[:12] for b in sorted(bundles))}; manifests: "
        f"{', '.join(sorted({r[4][:12] for r in rows}))}; rows: {len(rows)}"
    )
    print(
        "heldout rows per model:",
        dict(
            conn.execute(
                "SELECT model_name, count(*) FROM model_runs WHERE split = 'heldout' "
                "GROUP BY model_name"
            ).fetchall()
        ),
    )
    data = {(r[0], r[1]): json.loads(r[5]) for r in rows}
    for split, subset in (("validation", "validation"), ("heldout", "human"), ("heldout", "rule")):
        print(f"\n### {split} / {subset}\n")
        print(
            "| model | n | auc | auc 95% (trace bootstrap) | allow ok/bad | ask ok/bad "
            "| block ok/bad | false-allow (Wilson 95%) | asks per 1k ok | blocks per 1k ok |"
        )
        print("|---|---|---|---|---|---|---|---|---|---|")
        for name in ORDER:
            m = data[(name, split)][subset]
            c = m["counts"]
            fa = m["false_allow_rate"]
            print(
                f"| {name} | {m['n']} | {_f(m['auc'])} | {_ci(m['auc_bootstrap95'])} "
                f"| {c['authorized:allow']}/{c['unauthorized:allow']} "
                f"| {c['authorized:ask']}/{c['unauthorized:ask']} "
                f"| {c['authorized:block']}/{c['unauthorized:block']} "
                f"| {_f(fa)} ({_ci(m['false_allow_wilson95'])}) "
                f"| {_f(m['asks_per_1k_authorized'])} | {_f(m['blocks_per_1k_authorized'])} |"
            )


def thresholds(bundle_dir: str) -> None:
    with open(f"{bundle_dir}/bundle.json") as fh:
        models = json.load(fh)["models"]
    print("\n### calibration range and conformal thresholds (bundle.json)\n")
    print(
        "| model | calibrated p range | t_authorized (on p) | t_unauthorized (on 1-p) "
        "| allow needs p < | model sha256 |"
    )
    print("|---|---|---|---|---|---|")
    for name in ORDER:
        m = models[name]
        ys, c = m["isotonic"]["ys"], m["conformal"]
        bad = c["threshold_unauthorized"]
        print(
            f"| {name} | {min(ys):.3f} to {max(ys):.3f} | {_f(c['threshold_authorized'])} "
            f"| {_f(bad)} | {_f(None if bad is None else 1 - bad)} | {m['model_sha256'][:12]} |"
        )


if __name__ == "__main__":
    main(sys.argv[1])
    thresholds(sys.argv[2])
