"""Ledger reading and budget accounting.

Ledger lines are append-only JSON.  A killed process may truncate the final
line, so readers ignore malformed records.
"""

from __future__ import annotations

import json
import os
import hmac
from dataclasses import dataclass
from collections.abc import Callable, Iterable
from typing import Any

from .context import Paths
from .state_store import FileSnapshot, StateStore

AnswerAuth = Callable[[str, str], str]


@dataclass(frozen=True)
class LedgerSnapshot:
    raw: bytes | None
    entries: list[dict[str, Any]]


def secure_equal(actual: Any, expected: str) -> bool:
    return isinstance(actual, str) and hmac.compare_digest(actual.encode(), expected.encode())


def _parse_jsonl_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def read_entries(path: str) -> list[dict[str, Any]]:
    store = StateStore(Paths.for_repo(os.getcwd(), os.path.dirname(path) or "."))
    if not store.is_regular_file(path):
        return []
    with open(path) as f:
        return _parse_jsonl_lines(f)


def snapshot(path: str) -> LedgerSnapshot:
    store = StateStore(Paths.for_repo(os.getcwd(), os.path.dirname(path) or "."))
    if not store.is_regular_file(path):
        return LedgerSnapshot(None, [])
    with open(path, "rb") as f:
        raw = f.read()
    return LedgerSnapshot(raw, _parse_jsonl_lines(raw.decode("utf-8", "replace").splitlines()))


def restore_if_changed(state_dir: str, ledger_path: str, trusted: LedgerSnapshot) -> bool:
    store = StateStore(Paths.for_repo(os.getcwd(), state_dir))
    store.ensure_layout()
    return store.restore_if_changed(ledger_path, FileSnapshot(trusted.raw))


def append_entry(state_dir: str, ledger_path: str, obj: dict[str, Any]) -> None:
    store = StateStore(Paths.for_repo(os.getcwd(), state_dir))
    store.ensure_layout()
    store.append_jsonl(ledger_path, obj)


def is_authenticated_answer(entry: dict[str, Any], lid: str, answer_auth: AnswerAuth) -> bool:
    if entry.get("loop") != lid or entry.get("event") != "answer":
        return False
    nonce = entry.get("n", "")
    auth = entry.get("auth")
    return secure_equal(auth, answer_auth(lid, str(nonce)))


def trusted_duration(entry: dict[str, Any]) -> float:
    try:
        return max(0.0, float(entry.get("duration", 0)))
    except (TypeError, ValueError):
        return 0.0


def authenticated_answer_count(
    entries: Iterable[dict[str, Any]], lid: str, answer_auth: AnswerAuth
) -> int:
    return len({e["auth"] for e in entries if is_authenticated_answer(e, lid, answer_auth)})


def runs_since_authenticated_answer(
    entries: Iterable[dict[str, Any]], lid: str, answer_auth: AnswerAuth
) -> tuple[int, float]:
    runs, secs = 0, 0.0
    seen_answers: set[str] = set()
    for entry in entries:
        if entry.get("loop") != lid:
            continue
        if is_authenticated_answer(entry, lid, answer_auth):
            auth = entry["auth"]
            if auth not in seen_answers:
                seen_answers.add(auth)
                runs, secs = 0, 0.0
        elif "run" in entry:
            runs += 1
            secs += trusted_duration(entry)
    return runs, secs


def total_runs(entries: Iterable[dict[str, Any]], lid: str) -> int:
    return sum(1 for e in entries if e.get("loop") == lid and "run" in e)


def ledger_totals(entries: Iterable[dict[str, Any]]) -> tuple[int, float]:
    runs = secs = 0.0
    for entry in entries:
        if "run" in entry:
            runs += 1
            secs += trusted_duration(entry)
    return int(runs), secs


def budget_spent(
    lid: str,
    budget: list[tuple[str, int]],
    entries: list[dict[str, Any]],
    answer_auth: AnswerAuth,
    git_runs: int,
    waited: float = 0.0,
) -> bool:
    """Two trust models back the runs cap, and it takes the max of both.

    The ledger view (`runs`) is precise — it resets at each authenticated
    answer — but the file is agent-reachable, so alone it could be emptied.
    The committed-history view (`git_runs`) cannot be un-written, but it never
    resets, so each authenticated answer forgives one full cap from it
    (`git_runs - cap * answers`). An agent that wipes the ledger falls to the
    committed floor; a forged answer authenticates nothing and forgives
    nothing. Seconds have no committed counterpart (commits carry no
    durations), so the secs cap rests on the ledger view alone — tamper there
    is bounded by the whole-file restore after every run, not by a floor.
    """
    runs, secs = runs_since_authenticated_answer(entries, lid, answer_auth)
    answers = authenticated_answer_count(entries, lid, answer_auth)
    for kind, cap in budget:
        if kind == "runs" and max(runs, git_runs - cap * answers) >= cap:
            return True
        if kind == "secs" and secs + waited >= cap:
            return True
    return False
