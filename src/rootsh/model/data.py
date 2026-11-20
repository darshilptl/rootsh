"""The frozen dataset, rebuilt and checked against its manifest (design 06 §1).

P6 never trusts a rebuilt dataset by itself: it must hash to the frozen
manifest, and its heldout part must equal the sealed file byte for byte.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from rootsh.labels.dataset import TRAINING_SOURCES, Example, assemble
from rootsh.labels.freeze import manifest
from rootsh.labels.steps import all_steps


class FrozenDataError(RuntimeError):
    """The rebuilt dataset does not match the frozen one."""


@dataclass(frozen=True, slots=True)
class Frozen:
    manifest_sha256: str
    train: tuple[Example, ...]
    validation: tuple[Example, ...]
    heldout: tuple[Example, ...]  # every heldout example, R1 included


def sealed_path(manifest_path: Path) -> Path:
    digest = manifest_path.stem.removeprefix("manifest-")
    return manifest_path.with_name(f"heldout-{digest}.sealed.json")


def load(conn: sqlite3.Connection, manifest_path: Path, *, with_heldout: bool) -> Frozen:
    dataset = assemble(conn, list(all_steps(conn)))
    body, sealed = manifest(dataset)
    frozen = manifest_path.read_bytes()
    if body != frozen:
        raise FrozenDataError(
            f"rebuilt manifest {hashlib.sha256(body).hexdigest()[:12]} differs from "
            f"{manifest_path.name}"
        )
    if with_heldout and sealed != sealed_path(manifest_path).read_bytes():
        raise FrozenDataError("rebuilt heldout labels differ from the sealed file")

    def trains(split: str) -> tuple[Example, ...]:
        return tuple(
            sorted(
                (e for e in dataset.examples if e.split == split and e.source in TRAINING_SOURCES),
                key=lambda e: e.id,
            )
        )

    heldout = (
        tuple(sorted((e for e in dataset.examples if e.split == "heldout"), key=lambda e: e.id))
        if with_heldout
        else ()
    )
    digest = hashlib.sha256(frozen).hexdigest()
    return Frozen(digest, trains("train"), trains("validation"), heldout)


def target(example: Example) -> int:
    """1 = not authorized (the risk the model scores)."""
    return int(not example.verdict.authorized)
