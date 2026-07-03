"""Rendering for `lute status` and `lute inbox`.

Unlike `watch`, `status` is live: for each loop without an unanswered card it
re-runs the check once and prints the glyph the fresh verdict earns; a loop
with an open card keeps its blocked/gated mark rather than being re-examined
behind the answer's back. The footer totals runs and agent time from the
ledger and names the next command — an answer, or `lute land` once the root
is green.
"""

from __future__ import annotations

from . import render
from .cards import CardService
from .checks import CheckRunner
from .domain import LoopSpec
from .events import GLYPH_WORD, output_line
from .formatting import human
from .ledger import ledger_totals, read_entries, total_runs


def render_inbox(cards: CardService) -> None:
    rows = cards.open_cards()
    if not rows:
        output_line(render.rule("inbox · nothing waiting on you; loops are running or closed"))
        return
    for row in rows:
        glyph, word = ("✋", "gated") if row["gated"] else ("✗", "blocked")
        output_line(f"{render.paint(glyph, word)} {row['lid']}  {render.dim('· ' + row['summary'])}")
        output_line(render.dim("    answered: applies on the next `lute run`" if row["answered"] else f"    next: {row['next']}"))


def render_status(root: LoopSpec, checks: CheckRunner, cards: CardService, ledger_path: str) -> None:
    output_line(render.rule("lute status · re-runs each check once, except loops with an unanswered card"))
    waiting = {card["lid"]: card for card in cards.open_cards() if not card["answered"]}

    def mark_of(loop: LoopSpec) -> str:
        loop_id = str(loop.id)
        if loop_id in waiting:
            return "✋" if waiting[loop_id]["gated"] else "✗"
        result = checks.run(loop)
        return {"pass": "✔", "not_yet": "⏳"}.get(result.verdict.value) or (
            "↻" if total_runs(read_entries(ledger_path), loop_id) else "◌"
        )

    marks = {str(loop.id): mark_of(loop) for _, loop in root.flatten()}
    for line in render.tree_lines(root, lambda loop: (marks[str(loop.id)], GLYPH_WORD[marks[str(loop.id)]], "")):
        output_line(line)
    for card in waiting.values():
        output_line(render.rule(f"next: {card['next']}"))
    runs, secs = ledger_totals(read_entries(ledger_path))
    if runs:
        output_line(render.rule(f"{runs} run(s) · {human(secs)} of agent time so far"))
    if marks[str(root.id)] == "✔":
        output_line(render.rule(f"done · land it: lute land  (or git merge lute/{root.id})"))
