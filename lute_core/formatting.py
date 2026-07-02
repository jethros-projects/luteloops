"""Small display formatting helpers.

`human` renders seconds the way the status line speaks (`3m07s`), and `tail`
keeps the last N lines — the shape every check's output is clipped to before
it rides into a prompt.
"""

from __future__ import annotations


def human(secs: float) -> str:
    minutes, seconds = divmod(int(secs), 60)
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


def tail(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[-n:])
