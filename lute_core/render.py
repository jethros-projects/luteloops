"""Read-only rendering: the loop hierarchy drawn as a connected tree.

`watch` and `status` both show the same shape — a state glyph per loop, nested
under its parent — so the drawing lives here, once. Everything is a pure
projection: `watch` reads the event file, `status` re-runs each check; neither
touches the run. Colour appears only on a real terminal (off when the output is
piped or captured, so grep-able logs and the machine surfaces stay plain), and
box-drawing degrades to ASCII through `events.output_line`.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

from .domain import LoopSpec
from .events import ascii_stdout

# state word -> SGR colour code; the glyph and the trailing [word] wear it,
# the loop id stays default so it reads cleanly against any theme.
_COLOUR = {
    "done": "32", "running": "36", "waiting": "33", "pending": "90",
    "blocked": "31", "gated": "35", "parallel": "34",
}


def colour_on() -> bool:
    if os.environ.get("NO_COLOR") or ascii_stdout():
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _sgr(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if (code and colour_on()) else text


def dim(text: str) -> str:
    return _sgr(text, "2")


def bold(text: str) -> str:
    return _sgr(text, "1")


def cyan(text: str) -> str:
    return _sgr(text, "36")


def paint(text: str, word: str) -> str:
    """Colour `text` by the state `word` it represents (a glyph, usually)."""
    return _sgr(text, _COLOUR.get(word, ""))


# Semantic palette for human-facing lines — one vocabulary across every verb, so
# a command reads the same in help, in the lint report, and in a run's summary.
def ok(text: str) -> str:      # a success / a closed loop
    return paint(text, "done")


def warn(text: str) -> str:    # an advisory the run can survive
    return paint(text, "waiting")


def err(text: str) -> str:     # a stop-the-line problem
    return paint(text, "blocked")


def cmd(text: str) -> str:     # a command the reader should run next (verbs are cyan)
    return cyan(text)


# Narration lines begin with a state glyph; colour just that glyph so the rest of
# the line stays legible against any theme. ASCII-degraded lines have no glyph to
# match, and colour is already off there, so this is a no-op in that case.
_GLYPH_COLOUR = {
    "✔": "done", "↻": "running", "▶": "running", "⏳": "waiting",
    "◌": "pending", "✗": "blocked", "✋": "gated", "‖": "parallel",
    "⚠": "waiting", "↳": "pending", "·": "pending",
}


def paint_glyph(line: str) -> str:
    word = _GLYPH_COLOUR.get(line[:1])
    return paint(line[0], word) + line[1:] if word else line


def rule(text: str) -> str:
    """A subtle heading line — dim on a terminal, plain when captured."""
    return dim(text)


def _connectors(root: LoopSpec) -> list[tuple[str, LoopSpec]]:
    rows: list[tuple[str, LoopSpec]] = []

    def walk(node: LoopSpec, connector: str, pad: str) -> None:
        rows.append((connector, node))
        kids = node.children
        for index, kid in enumerate(kids):
            last = index == len(kids) - 1
            walk(kid, pad + ("└─ " if last else "├─ "), pad + ("   " if last else "│  "))

    walk(root, "", "")
    return rows


Cell = Callable[[LoopSpec], "tuple[str, str, str]"]


def tree_lines(root: LoopSpec, cell: Cell) -> list[str]:
    """Draw the loop tree. `cell(loop)` returns (glyph, word, meta): the state
    mark, its ASCII word, and a short right-hand detail (`""` for none). Rows are
    aligned on a common column so the details line up under each other."""
    rows = _connectors(root)
    # Measure against the UNcoloured label (colour codes are zero-width but would
    # inflate len), so the detail column aligns whether or not colour is on.
    labels = [f"{connector}{cell(loop)[0]} {loop.id}" for connector, loop in rows]
    width = max((len(label) for label in labels), default=0)
    lines: list[str] = []
    for (connector, loop), label in zip(rows, labels):
        glyph, word, meta = cell(loop)
        head = f"{connector}{_sgr(glyph, _COLOUR.get(word, ''))} {loop.id}"
        gap = " " * (width - len(label) + 3)
        detail = (dim(meta) + "  " if meta else "") + dim(f"[{word}]")
        lines.append(f"{head}{gap}{detail}")
    return lines
