"""YAML loading and normalization for lute manifests."""

from __future__ import annotations

import difflib
import re
from typing import Any

import yaml

from .domain import LoopSpec

LOOP_KEYS = {
    "loop",
    "task",
    "agent",
    "done_when",
    "budget",
    "confirm",
    "loops",
    "check_every",
    "gate",
    "protected",
    "parallel",
}
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def parse_duration(part: str) -> int | None:
    if m := re.match(r"^(\d+)(s|m|h)$", part):
        return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]
    return None


def parse_budget(spec: Any, lid: str, errors: list[str]) -> list[tuple[str, int]]:
    caps: list[tuple[str, int]] = []
    for part in str(spec).split("/"):
        p = part.strip()
        if m := re.match(r"^(\d+)\s*runs?$", p):
            caps.append(("runs", int(m.group(1))))
        elif (duration := parse_duration(p)) is not None:
            caps.append(("secs", duration))
        else:
            errors.append(
                f'{lid}: bad budget part {p!r} (use "N runs" or a duration like "90m", joined by "/")'
            )
    return caps


def norm_loop(node: Any, errors: list[str], seen: set[str]) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        errors.append(f"loop entry must be a mapping, got {node!r}")
        return None
    if unknown := sorted(set(node) - LOOP_KEYS):
        near = {k: m[0] for k in unknown if (m := difflib.get_close_matches(k, LOOP_KEYS, 1))}
        hint = f"; did you mean {', '.join(f'{k} -> {v}' for k, v in near.items())}?" if near else ""
        errors.append(f"unknown key(s) {unknown} in loop {node.get('loop')!r}{hint}")
    lid = node.get("loop")
    if not isinstance(lid, str) or not ID_RE.match(lid):
        errors.append(f"loop id must be a kebab-case string, got {lid!r}")
        lid = str(lid)
    if lid in seen:
        errors.append(f"duplicate loop id '{lid}'")
    seen.add(lid)
    task, agent = node.get("task"), node.get("agent")
    if task is not None and not isinstance(task, str):
        errors.append(f"{lid}: task must be a string")
        task = str(task)
    if agent is not None and not isinstance(agent, str):
        errors.append(f"{lid}: agent must be a string")
        agent = None
    done_when = node.get("done_when")
    if not isinstance(done_when, str) or not done_when.strip():
        errors.append(f"{lid}: done_when is required and must be a string (quote it)")
        done_when = "false"
    confirm = node.get("confirm", 1)
    if isinstance(confirm, bool) or not isinstance(confirm, int) or confirm < 1:
        errors.append(f"{lid}: confirm must be an integer >= 1")
        confirm = 1
    kids = node.get("loops", [])
    if not isinstance(kids, list):
        errors.append(f"{lid}: loops must be a list")
        kids = []
    check_every = node.get("check_every", "60s")
    every = parse_duration(check_every.strip()) if isinstance(check_every, str) else None
    if every is None or every <= 0:
        errors.append(f"{lid}: bad check_every {check_every!r} (use a positive duration like 30s, 5m, 2h)")
        check_every, every = "60s", 60
    gate = node.get("gate")
    if gate is not None and gate != "human":
        errors.append(f"{lid}: gate must be exactly 'human', got {gate!r}")
        gate = None
    protected = node.get("protected", [])
    if not isinstance(protected, list) or not all(isinstance(p, str) for p in protected):
        errors.append(f"{lid}: protected must be a list of glob strings")
        protected = [p for p in protected if isinstance(p, str)] if isinstance(protected, list) else []
    parallel = node.get("parallel", False)
    if not isinstance(parallel, bool):
        errors.append(f"{lid}: parallel must be true or false")
        parallel = False
    return {
        "id": lid,
        "task": task,
        "agent": agent,
        "done_when": done_when,
        "budget": parse_budget(node.get("budget", "10 runs"), lid, errors),
        "confirm": confirm,
        "every": every,
        "every_str": check_every.strip(),
        "gate": gate,
        "protected": protected,
        "parallel": parallel,
        "children": [c for c in (norm_loop(k, errors, seen) for k in kids) if c],
    }


def load(path: str) -> tuple[LoopSpec | None, list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    try:
        with open(path) as f:
            doc = yaml.safe_load(f)
    except OSError as e:
        return None, [], [f"cannot read {path}: {e}"]
    except yaml.YAMLError as e:
        return None, [], [f"invalid YAML in {path}: {e}"]
    if not isinstance(doc, dict):
        return None, [], [f"{path}: top level must be a mapping with a 'loop:' key"]
    schedules = doc.pop("schedules", None) or []
    if not isinstance(schedules, list):
        errors.append("schedules: must be a list")
        schedules = []
    raw = norm_loop(doc, errors, set())
    return (LoopSpec.from_legacy_dict(raw) if raw else None), schedules, errors
