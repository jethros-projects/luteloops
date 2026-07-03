"""Small display formatting helpers.

`human` renders seconds the way the status line speaks (`3m07s`); `tail` keeps
the last N lines (the journal slice in a card); and `excerpt` is the shape a
failing check's output is clipped to before it rides into an agent prompt — a
BYTE-budgeted head+tail window, so a top-anchored diagnostic (a compiler's first
error) is never evicted by a wall of trailing noise, the summary most runners
print last is kept too, and a handful of very long lines can't blow up the
prompt (a line count cannot see line width). Deterministic, no model in the loop.
"""

from __future__ import annotations

_HEAD_B, _TAIL_B, _LINE_B = 1500, 3500, 400


def human(secs: float) -> str:
    minutes, seconds = divmod(int(secs), 60)
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


def tail(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[-n:])


def excerpt(text: str) -> str:
    # Collapse each carriage-return redraw to its final frame (a progress bar is
    # one logical line, not fifty), on \n boundaries only so a lone \r cannot
    # inflate the line count, and drop trailing blanks.
    lines = [segment.rstrip("\r").rsplit("\r", 1)[-1] for segment in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()

    def size(line: str) -> int:
        return len(line.encode("utf-8")) + 1  # + the newline

    if sum(size(line) for line in lines) <= _HEAD_B + _TAIL_B:
        return "\n".join(lines)  # fits whole — no clamp, no claim

    # First try clamping only over-long individual lines: often that alone fits.
    clipped = 0

    def clamp(line: str) -> str:
        nonlocal clipped
        raw = line.encode("utf-8")
        if len(raw) <= _LINE_B:
            return line
        clipped += 1
        kept = raw[:_LINE_B].decode("utf-8", "ignore")
        return f"{kept} …[+{len(raw) - len(kept.encode('utf-8'))}B clipped]"

    lines = [clamp(line) for line in lines]
    if sum(size(line) for line in lines) <= _HEAD_B + _TAIL_B:
        note = f"… {clipped} long lines clipped — re-run the check for the full log …"
        return "\n".join(lines + [note])

    # Still over budget: keep as much head and tail as each byte budget allows.
    head, used = 0, 0
    while head < len(lines) and used + size(lines[head]) <= _HEAD_B:
        used += size(lines[head])
        head += 1
    tail_i, used = len(lines), 0
    while tail_i > head and used + size(lines[tail_i - 1]) <= _TAIL_B:
        used += size(lines[tail_i - 1])
        tail_i -= 1
    marker = f"… {tail_i - head} lines elided — re-run the check for the full log …"
    return "\n".join(lines[:head] + [marker] + lines[tail_i:])
