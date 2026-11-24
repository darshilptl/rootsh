"""What the author labels, and in what order (design 05 §4-§6, ADR-0013).

One queue, so the author never picks a round: the heldout and validation
steps R1 does not cover, plus the blind R1 audit sample, in shuffled trace
order (steps of a trace stay in `seq` order, so the context builds up).
Every other training step goes to the disclosed assistant (`assistant_steps`).
Self re-tests are dropped (ADR-0013 item 4); `Plan.retest` stays empty.
Every choice is seeded and the seed is fixed here, so the order is
reproducible, and the audit sample is drawn exactly as before ADR-0013.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass

from rootsh.research.labels.model import Round
from rootsh.research.labels.rule import r1_applies, r1_v1_applies
from rootsh.research.labels.splits import split_of
from rootsh.research.labels.steps import Step
from rootsh.research.labels.store import Key, Stored

SEED = 20261120
AUDIT_TARGET = 60
RETEST_GAP = 150  # other labels between the two passes
RETEST_EVERY = 8  # about one served step in eight is a re-test
_PRIORITY = {"heldout": 0, "validation": 1, "train": 2}


def stratified[T](
    items: Sequence[T], key: Callable[[T], Hashable], n: int, rng: random.Random
) -> list[T]:
    """Round-robin over strata (sorted), random within each stratum."""
    strata: dict[Hashable, list[T]] = defaultdict(list)
    for item in items:
        strata[key(item)].append(item)
    pools = [rng.sample(strata[k], len(strata[k])) for k in sorted(strata, key=repr)]
    picked: list[T] = []
    while len(picked) < n and any(pools):
        for pool in pools:
            if pool and len(picked) < n:
                picked.append(pool.pop())
    return picked


@dataclass(frozen=True, slots=True)
class Plan:
    queue: tuple[Step, ...]  # hand steps and audit steps, in serving order
    audit: frozenset[Key]
    retest: tuple[Key, ...]  # in serving order

    def round_of(self, step: Step) -> Round:
        return Round.AUDIT if step.key in self.audit else Round.MAIN


def build_plan(steps: Sequence[Step], seed: int = SEED) -> Plan:
    rng = random.Random(seed)  # noqa: S311 - a reproducible sample, not a secret
    # The audit was drawn from R1 v1 and labelled; it stays that sample.
    rule = [s for s in steps if r1_v1_applies(s)]
    audit = {
        s.key
        for s in stratified(
            rule, lambda s: (s.trace.agent.value, s.trace.repository), AUDIT_TARGET, rng
        )
    }
    by_trace: dict[str, list[Step]] = defaultdict(list)
    for step in steps:
        if step.key in audit or not r1_v1_applies(step):
            by_trace[step.trace.id].append(step)
    order = sorted(by_trace)
    rng.shuffle(order)
    # What the result is measured on comes first: heldout, then validation, then
    # train (author, 2026-11-20). Stable, so the seeded order holds within each.
    repository = {tid: steps_[0].trace.repository for tid, steps_ in by_trace.items()}
    order.sort(key=lambda tid: _PRIORITY[split_of(repository[tid])])
    # ADR-0013: the order is drawn as before (so a resumed session serves the
    # same next step), then train steps outside the audit go to the assistant.
    queue = tuple(
        step
        for tid in order
        for step in sorted(by_trace[tid], key=lambda s: s.seq)
        if step.key in audit or split_of(step.trace.repository) != "train"
    )
    # Steps R1 v2 no longer covers go to the author in every split (the
    # assistant's training labels were dropped: kappa below target, ADR-0013).
    left_r1 = [s for s in steps if r1_v1_applies(s) and not r1_applies(s) and s.key not in audit]
    queue += tuple(sorted(left_r1, key=lambda s: (_PRIORITY[split_of(s.trace.repository)], s.key)))
    return Plan(queue, frozenset(audit), ())


def assistant_steps(steps: Sequence[Step], plan: Plan) -> list[Step]:
    """Training steps the assistant labels (ADR-0013 item 2): every train
    step that R1 does not cover and the author does not label."""
    return [
        s
        for s in steps
        if split_of(s.trace.repository) == "train" and not r1_applies(s) and s.key not in plan.audit
    ]


@dataclass(frozen=True, slots=True)
class Next:
    step: Step
    round: Round


def next_item(
    plan: Plan, by_key: dict[Key, Step], labels: Sequence[Stored], deferred: set[Key]
) -> Next | None:
    """The next step to serve, or None when everything is labelled."""
    first: dict[Key, Stored] = {}
    retested: set[Key] = set()
    for stored in labels:
        if stored.round is Round.RETEST:
            retested.add(stored.key)
        elif by_key[stored.key].sha256 == stored.step_sha256:
            first[stored.key] = stored
    position = {stored.id: i for i, stored in enumerate(labels)}

    def eligible(key: Key, *, gap: bool) -> bool:
        if key in retested or key not in first:
            return False
        return not gap or len(labels) - 1 - position[first[key].id] >= RETEST_GAP

    if len(labels) % RETEST_EVERY == RETEST_EVERY - 1:
        for key in plan.retest:
            if eligible(key, gap=True):
                return Next(by_key[key], Round.RETEST)
    pending = [s for s in plan.queue if s.key not in first]
    for step in pending:
        if step.key not in deferred:
            return Next(step, plan.round_of(step))
    if pending:  # only deferred steps left
        return Next(pending[0], plan.round_of(pending[0]))
    for key in plan.retest:  # the queue is done: any re-test still owed, gap reported
        if eligible(key, gap=False):
            return Next(by_key[key], Round.RETEST)
    return None


def progress(plan: Plan, labels: Sequence[Stored]) -> tuple[int, int]:
    """Answered steps of the queue, and its length (no re-tests since ADR-0013)."""
    done = {s.key for s in labels if s.round is not Round.RETEST}
    return len(done & {s.key for s in plan.queue}), len(plan.queue)
