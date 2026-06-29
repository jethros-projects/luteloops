"""Small display formatting helpers."""

from __future__ import annotations


def human(secs: float) -> str:
    minutes, seconds = divmod(int(secs), 60)
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


def tail(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[-n:])
