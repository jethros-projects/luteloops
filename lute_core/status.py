"""Live status and inbox rendering."""

from __future__ import annotations

from .cards import CardService
from .checks import CheckRunner
from .domain import LoopSpec
from .events import GLYPH_WORD, output_line
from .formatting import human
from .ledger import ledger_totals, read_entries, total_runs


def render_inbox(cards: CardService) -> None:
    rows = cards.open_cards()
    if not rows:
        print("inbox: nothing waiting on you; loops are running or closed")
        return
    for row in rows:
        output_line(f"{'✋' if row['gated'] else '✗'} {row['lid']}: {row['summary']}")
        print("    answered: applies on the next `lute run`" if row["answered"] else f"    next: {row['next']}")


def render_status(root: LoopSpec, checks: CheckRunner, cards: CardService, ledger_path: str) -> None:
    print("lute status: may execute done_when/judge checks for loops without unanswered cards (not replay-only)")
    waiting = {card["lid"]: card for card in cards.open_cards() if not card["answered"]}

    def walk(loop: LoopSpec, depth: int) -> str:
        loop_id = str(loop.id)
        if loop_id in waiting:
            mark = "✋" if waiting[loop_id]["gated"] else "✗"
        else:
            result = checks.run(loop)
            mark = {"pass": "✔", "not_yet": "⏳"}.get(result.verdict.value) or (
                "↻" if total_runs(read_entries(ledger_path), loop_id) else "◌"
            )
        output_line(f"{'  ' * depth}{mark} {loop_id}  [{GLYPH_WORD[mark]}]")
        for child in loop.children:
            walk(child, depth + 1)
        return mark

    root_mark = walk(root, 0)
    for card in waiting.values():
        print(f"  next: {card['next']}")
    runs, secs = ledger_totals(read_entries(ledger_path))
    if runs:
        print(f"  {runs} run(s) · {human(secs)} of agent time so far")
    if root_mark == "✔":
        print(f"  done: land it: lute land  (or git merge lute/{root.id})")
