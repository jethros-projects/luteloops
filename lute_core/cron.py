"""Crontab sync/remove command logic."""

from __future__ import annotations

import os
import shlex
import subprocess

from .domain import LoopSpec
from .errors import InternalError, UsageError
from .runner import self_cmd


def validate_schedules(schedules: list[dict], root_id: str) -> list[str]:
    errors: list[str] = []
    for schedule in schedules:
        if not isinstance(schedule, dict) or set(schedule) != {"run", "at"}:
            errors.append(f"schedules: each entry needs exactly 'run' and 'at', got {schedule!r}")
        elif schedule["run"] != root_id:
            errors.append(f"schedules: '{schedule['run']}' is not the root; only the root loop '{root_id}' is schedulable")
        elif not isinstance(schedule["at"], str) or len(schedule["at"].split()) != 5:
            errors.append(f"schedules: bad cron expression {schedule['at']!r} (need 5 fields)")
        elif "\n" in schedule["at"] or "\r" in schedule["at"]:
            errors.append("schedules: cron expression must be a single line")
    return errors


def sync_or_remove(action: str, repo: str, root: LoopSpec | None, schedules: list[dict]) -> None:
    if action not in ("sync", "remove"):
        raise UsageError("usage: lute cron sync|remove")
    if "\n" in repo or "\r" in repo:
        raise UsageError("repo path contains a newline; refusing to write an ambiguous crontab block")
    begin, end = f"# BEGIN lute {repo}", f"# END lute {repo}"
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode and "no crontab" not in (result.stderr or "").lower():
        raise InternalError(
            f"could not read the current crontab ({(result.stderr or '').strip() or 'crontab -l failed'}); "
            "refusing to overwrite it; fix that, then re-run"
        )
    keep, skipping = [], False
    for line in (result.stdout if result.returncode == 0 else "").splitlines():
        if line == begin:
            skipping = True
        elif line == end:
            skipping = False
        elif not skipping:
            keep.append(line)
    if skipping:
        raise InternalError(
            f"crontab has a '{begin}' marker with no matching '# END lute {repo}'; the managed block is "
            "malformed; fix or remove it by hand, then re-run (refusing to guess and risk dropping lines)"
        )
    block: list[str] = []
    if action == "sync" and root:
        errors = validate_schedules(schedules, str(root.id))
        if errors:
            raise UsageError("; ".join(errors))
        for schedule in schedules:
            block.append(f"{schedule['at']} cd {shlex.quote(repo)} && {self_cmd()} run --skip-if-running {root.id}")
        if block:
            block = [begin, *block, end]
    text = "\n".join(keep + block).strip("\n")
    writer = subprocess.run(["crontab", "-"], input=text + "\n" if text else "", text=True)
    if writer.returncode:
        raise InternalError("crontab update failed")
    print(
        f"crontab: {max(len(block) - 2, 0)} lute schedule(s) for {repo}"
        if action == "sync" else f"crontab: removed the lute block for {repo}"
    )
