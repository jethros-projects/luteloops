"""Event stream projections."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from .context import AppContext
from .state_store import StateStore

GLYPH_WORD = {"✔": "done", "↻": "running", "⏳": "waiting", "◌": "pending",
              "✗": "blocked", "✋": "gated", "‖": "parallel"}
ASCII_MAP = {"▶": ">", "↻": "~", "↳": "<", "✔": "OK", "✗": "X", "✋": "GATE", "⏳": "WAIT",
             "‖": "||", "⚠": "!", "·": "-", "×": "x", "→": "->", "…": "..."}


def now_iso() -> str:
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%03dZ" % int(t % 1 * 1000)


def ascii_stdout() -> bool:
    return (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "") != "utf8"


def output_line(line: str) -> None:
    if ascii_stdout():
        for glyph, replacement in ASCII_MAP.items():
            line = line.replace(glyph, replacement)
        line = line.encode("ascii", "replace").decode("ascii")
    print(line, flush=True)


def plain_line(event: dict[str, Any]) -> str | None:
    lid = event.get("loop", "")
    paths = event.get("paths") or []
    if event.get("ev") == "quarantine":
        n = len(paths)
        suffix = "s" if n != 1 else ""
        return (
            f"⚠ {lid}: quarantined trusted exam edit {event.get('id', '')} · "
            f"{n} file{suffix} restored · inspect: lute quarantine diff {event.get('id', '')}"
        )
    if event.get("tampered"):
        check = f"⚠ {lid}: exam materials modified: {len(event['tampered'])} file(s)"
    elif event.get("verdict") == "not_yet":
        check = f"⏳ {lid}: not yet · next check in {event.get('next', '')}"
    else:
        check = f"· exam {lid}: {event.get('verdict', '')}" + (f" ({event['streak']})" if event.get("streak") else "")
    land = (f"merge it: git merge lute/{lid}" if event.get("fileless")
            else f"land it: lute land  (or merge by hand: git merge lute/{lid})")
    return {"run_start": f"▶ {lid}: branch lute/{lid}",
            "parallel": f"‖ {lid}: {len(event.get('children', []))} children in parallel: {', '.join(event.get('children', []))}",
            "check": check,
            "agent_start": f"↻ {lid} run {event.get('run')}{'/' + str(event['cap']) if event.get('cap') else ''} → {event.get('log')}",
            "agent_end": f"↳ {lid} run {event.get('run')} exit {event.get('exit')} · {event.get('secs')}s",
            "loop_closed": f"✔ {lid} closed",
            "gated": f"✋ {lid}: ready: approve: lute answer {lid} approve  ({event.get('card')})",
            "escalated": f'✗ {lid} blocked: answer: lute answer {lid} "..."  ({event.get("card")})',
            "run_end": f"✔ all loops closed: work is on lute/{lid}; {land}"}.get(event.get("ev"))


class EventBus:
    def __init__(self, ctx: AppContext, store: StateStore):
        self.ctx = ctx
        self.store = store

    def emit(self, ev: str, loop: str, **fields: Any) -> dict[str, Any]:
        event = {"ts": now_iso(), "ev": ev, "loop": loop}
        event.update(fields)
        self.store.append_jsonl(self.ctx.paths.events, event)
        if self.ctx.plain:
            line = plain_line(event)
            if line:
                output_line(line)
        return event


def replay_events(events_path: str) -> dict[str, Any]:
    state: dict[str, Any] = {
        "loops": {},
        "root": "",
        "branch": "",
        "started": "",
        "last": "",
        "log": "",
        "ended": False,
    }
    if not os.path.exists(events_path):
        return state
    with open(events_path) as f:
        for line in f:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            loop_state = state["loops"].setdefault(
                event.get("loop", ""),
                {
                    "mark": "◌",
                    "runs": 0,
                    "secs": 0.0,
                    "active": False,
                    "run": 0,
                    "log": "",
                    "card": "",
                },
            )
            ev, state["last"] = event.get("ev"), event.get("ts", state["last"])
            if ev == "run_start":
                state.update(
                    root=event.get("loop", ""),
                    branch=event.get("branch", ""),
                    started=event.get("ts", ""),
                    ended=False,
                )
            elif ev == "parallel":
                loop_state["mark"] = "‖"
            elif ev == "agent_start":
                loop_state.update(
                    active=True,
                    mark="↻",
                    run=event.get("run", 0),
                    cap=event.get("cap"),
                    log=event.get("log", ""),
                )
                state["log"] = loop_state["log"]
            elif ev == "agent_end":
                loop_state.update(
                    active=False,
                    runs=loop_state["runs"] + 1,
                    secs=loop_state["secs"] + event.get("secs", 0),
                )
            elif ev == "loop_closed":
                loop_state.update(mark="✔", active=False)
            elif ev == "gated":
                loop_state.update(mark="✋", active=False, card=event.get("card", ""))
            elif ev == "escalated":
                loop_state.update(mark="✗", active=False, card=event.get("card", ""))
            elif ev == "check" and event.get("verdict") == "not_yet":
                loop_state["mark"] = "⏳"
            elif ev == "check" and event.get("verdict") != "pass" and loop_state["mark"] == "✔":
                loop_state["mark"] = "↻"
            elif ev == "run_end":
                state["ended"] = True
    return state
