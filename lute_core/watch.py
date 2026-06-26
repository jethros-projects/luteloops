"""Replay-only watch/snapshot state."""

from __future__ import annotations

import datetime
import json
import os

from .cards import CardService
from .domain import LoopSpec
from .events import GLYPH_WORD, output_line, replay_events


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


def iso2t(value: str) -> float:
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=datetime.timezone.utc
        ).timestamp()
    except (TypeError, ValueError):
        return 0


def read_tail(path: str, max_bytes: int = 65536) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - max_bytes))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def human(secs: float) -> str:
    minutes, seconds = divmod(int(secs), 60)
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


def loop_row(depth: int, loop: LoopSpec, state: dict, root: LoopSpec) -> str:
    loop_state = state["loops"].get(str(loop.id), {})
    mark, runs = loop_state.get("mark", "◌"), loop_state.get("runs", 0)
    tag = " (root)" if loop is root and loop.children else ""
    if loop_state.get("active"):
        info = f"run {loop_state.get('run')}{'/' + str(loop_state['cap']) if loop_state.get('cap') else ''} · done_when: {loop.done_when.command[:40]}"
    elif runs:
        info = f"{runs} run{'s' if runs != 1 else ''} · {human(loop_state.get('secs', 0))}"
    else:
        info = ""
    gloss = f" [{GLYPH_WORD[mark]}]" if mark in GLYPH_WORD else ""
    return f"{'  ' * depth}{mark} {loop.id}{tag}{gloss}" + (f"    {info}" if info else "")


def snapshot_lines(root: LoopSpec, state: dict) -> list[str]:
    return [loop_row(depth, loop, state, root) for depth, loop in root.flatten()]


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
    print(f"lute snapshot: replayed from events (free; may be stale, last event {state['last'] or 'none'})")
    for line in snapshot_lines(root, state):
        output_line(line)


def render_json(root: LoopSpec, events_path: str, cards: CardService) -> None:
    print(json.dumps(state_json(root, replay_events(events_path), cards), indent=2))


def render_filtered(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        print(noise_filter(f.read()), end="")
