"""Replay-only rendering for `lute watch`.

Everything here is a pure projection of `.lute/events.jsonl` — no check is
ever re-run, so watching is free and cannot perturb a live run.
`snapshot_lines` draws the loop tree with glyph marks; `state_json` emits the
stable machine shape (`root`/`outcome`/`exit`/`ended`/`branch`/`tree`/`cards`)
that wrappers and cron probes branch on; `noise_filter` collapses repeated
log blocks to one copy with a `×N` marker for `--filter`.
"""

from __future__ import annotations

import json

from . import render
from .cards import CardService
from .domain import LoopSpec
from .events import GLYPH_WORD, output_line, replay_events
from .formatting import human


def noise_filter(text: str, maxblock: int = 64) -> str:
    lines = text.splitlines(keepends=True)
    out, i, n = [], 0, len(lines)
    while i < n:
        for size in range(3, min(maxblock, (n - i) // 2) + 1):
            if lines[i:i + size] == lines[i + size:i + 2 * size]:
                reps = 2
                while lines[i:i + size] == lines[i + reps * size:i + (reps + 1) * size]:
                    reps += 1
                out += lines[i:i + size] + [f"··· ×{reps}\n"]
                i += reps * size
                break
        else:
            out.append(lines[i])
            i += 1
    return "".join(out)


def snapshot_cell(state: dict) -> "callable":
    def cell(loop: LoopSpec) -> tuple[str, str, str]:
        loop_state = state["loops"].get(str(loop.id), {})
        mark = loop_state.get("mark", "◌")
        runs = loop_state.get("runs", 0)
        if loop_state.get("active"):
            cap = loop_state.get("cap")
            meta = f"run {loop_state.get('run')}" + (f"/{cap}" if cap else "")
        elif runs:
            meta = f"{runs} run{'s' if runs != 1 else ''} · {human(loop_state.get('secs', 0))}"
        else:
            meta = ""
        return mark, GLYPH_WORD.get(mark, ""), meta

    return cell


def snapshot_lines(root: LoopSpec, state: dict) -> list[str]:
    return render.tree_lines(root, snapshot_cell(state))


def state_json(root: LoopSpec, state: dict, cards: CardService) -> dict:
    def node(loop: LoopSpec, depth: int) -> dict:
        loop_state = state["loops"].get(str(loop.id), {})
        mark = loop_state.get("mark", "◌")
        return {
            "id": str(loop.id),
            "depth": depth,
            "mark": mark,
            "word": GLYPH_WORD.get(mark, ""),
            "runs": loop_state.get("runs", 0),
            "secs": round(loop_state.get("secs", 0.0), 1),
            "active": loop_state.get("active", False),
            "children": [node(child, depth + 1) for child in loop.children],
        }

    card_rows = cards.open_cards()
    waiting = [card for card in card_rows if not card["answered"]]
    if any(not card["gated"] for card in waiting):
        outcome, code = "blocked", 3
    elif waiting:
        outcome, code = "gated", 4
    elif state.get("ended"):
        outcome, code = "closed", 0
    else:
        outcome, code = "running", None
    return {
        "root": str(root.id),
        "outcome": outcome,
        "exit": code,
        "ended": state.get("ended", False),
        "branch": state.get("branch", ""),
        "tree": node(root, 0),
        "cards": card_rows,
    }


def render_snapshot(root: LoopSpec, events_path: str) -> None:
    state = replay_events(events_path)
    output_line(render.rule(
        f"lute snapshot · replayed from events, free but may be stale · "
        f"last event {state['last'] or 'none'}"
    ))
    for line in snapshot_lines(root, state):
        output_line(line)
    closed = sum(1 for ls in state["loops"].values() if ls.get("mark") == "✔")
    total = len(root.flatten())
    if state.get("ended") and closed == total and total:
        output_line(render.rule(f"all loops closed — land it: lute land  (or git merge {state.get('branch') or ('lute/' + str(root.id))})"))


def render_json(root: LoopSpec, events_path: str, cards: CardService) -> None:
    print(json.dumps(state_json(root, replay_events(events_path), cards), indent=2))


def render_filtered(path: str) -> None:
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in noise_filter(f.read()).splitlines():
            output_line(line)
