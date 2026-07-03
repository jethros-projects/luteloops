"""Small display formatting helpers.

`human` renders seconds the way the status line speaks (`3m07s`); `tail` keeps
the last N lines (the journal slice in a card); and `excerpt` is the shape a
failing check's output is clipped to before it rides into an agent prompt — a
head+tail window, so a top-anchored diagnostic (a compiler's first error) is
never evicted by a wall of trailing noise, while the summary most runners print
last is kept too. Deterministic, no model in the loop.
"""

from __future__ import annotations


def human(secs: float) -> str:
    minutes, seconds = divmod(int(secs), 60)
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


def tail(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[-n:])


def excerpt(text: str, head: int = 15, tail_n: int = 45) -> str:
    # Collapse each carriage-return redraw to its final frame (a progress bar is
    # one logical line, not fifty), on \n boundaries only so a lone \r cannot
    # inflate the line count. tail() splits on \r too, which is fine for a card's
    # journal slice but wrong here, where the count decides what survives.
    lines = [segment.rstrip("\r").rsplit("\r", 1)[-1] for segment in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    if len(lines) <= head + tail_n:
        return "\n".join(lines)
    elided = len(lines) - head - tail_n
    marker = f"… {elided} lines elided — re-run the check for the full log …"
    return "\n".join(lines[:head] + [marker] + lines[-tail_n:])
