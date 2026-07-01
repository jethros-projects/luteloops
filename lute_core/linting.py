"""Manifest lint policy.

Everything here answers one question about an authored manifest: is each
loop's contract administrable and honest? ERRORS enforce integrity and
administrability; WARNINGS advise on exam quality. The CLI prints; this
module only judges.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess

from .cage import looks_like_container_runtime
from .cron import validate_schedules
from .domain import Gate, LoopSpec
from .protection import glob_re, protected_files


def shell_argv(command: str) -> list[str]:
    """shlex-split a check command, dropping leading VAR=value assignments."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    while parts and re.match(r"[A-Za-z_]\w*=", parts[0]):
        parts.pop(0)
    return parts


def resolvable(command: str) -> bool:
    parts = shell_argv(command)
    return bool(parts) and bool(shutil.which(parts[0]))


def repo_rel(path: str) -> str | None:
    rel = os.path.relpath(os.path.abspath(path))
    if rel == "." or rel == ".." or rel.startswith(".." + os.sep):
        return None
    return rel.replace(os.sep, "/")


def protected_covers(path: str, globs: list[str]) -> bool:
    return any(glob_re(pattern).match(path) for pattern in globs)


def local_check_paths(command: str) -> list[str]:
    parts = shell_argv(command)
    if not parts or parts[0].startswith("judge:"):
        return []

    def existing_file(token: str) -> str | None:
        token = token.rstrip(";")
        if token.startswith("-") or token == "--":
            return None
        if token.startswith("./") or "/" in token or os.path.exists(token):
            rel = repo_rel(token)
            if rel and os.path.exists(rel) and not os.path.isdir(rel):
                return rel
        return None

    cmd, rest = parts[0], parts[1:]
    candidates: list[str] = []
    if cmd in {"sh", "bash", "dash", "zsh", "python", "python3", "node", "ruby", "perl"}:
        for token in rest:
            if token == "-c":
                break
            found = existing_file(token)
            if found:
                candidates.append(found)
                break
    elif cmd == "make":
        if os.path.exists("Makefile"):
            candidates.append("Makefile")
    elif cmd in {"npm", "pnpm", "yarn"}:
        if os.path.exists("package.json"):
            candidates.append("package.json")
    else:
        found = existing_file(cmd)
        if found:
            candidates.append(found)
    return sorted(set(candidates))


def is_placeholder_check(command: str) -> bool:
    try:
        parts = shlex.split(command.strip())
    except ValueError:
        return False
    return parts in (["true"], [":"], ["exit", "0"])


def circular_exam_target(command: str) -> str | None:
    """The repo file a done_when merely probes for existence or text, when the
    whole check is that probe — the classic circular exam an agent satisfies by
    simply writing the file. Deliberately narrow: it flags only the common bare
    shapes (`test -f X`, `[ -f X ]`, non-recursive `grep PAT FILE`) and stays a
    warning, so an author set on a tautology can still write one — the point is to
    catch the insidious accident, not to wall off every evasion. Returns the path
    normalized like a `protected:` glob (so the "protect it" advice actually
    silences it), or None for real logic or a path outside the repo the agent
    cannot write anyway."""
    try:
        parts = shlex.split(command.strip())
    except ValueError:
        return None
    file_tests = {"-f", "-e", "-s", "-r", "-d"}
    probe: str | None = None
    if len(parts) == 3 and parts[0] == "test" and parts[1] in file_tests:
        probe = parts[2]
    elif len(parts) == 4 and parts[0] == "[" and parts[1] in file_tests and parts[-1] == "]":
        probe = parts[2]
    elif parts and parts[0] == "grep":
        flags = [p for p in parts[1:] if p.startswith("-")]
        recursive = any(
            f in ("--recursive", "--dereference-recursive")
            or (not f.startswith("--") and ("r" in f or "R" in f))  # a short -r/-R bundle
            for f in flags
        )  # a recursive grep searches a tree, not a single writable file
        operands = [p for p in parts[1:] if not p.startswith("-")]
        if not recursive and len(operands) == 2 and not operands[1].endswith("/"):
            probe = operands[1]  # grep PATTERN FILE — a single writable file
    return repo_rel(probe) if probe else None


def lint_manifest(root, schedules, ctx, checks, budget, *, default_agent, no_exec):
    """Judge every loop's contract. Returns (rows, warnings, errors, counts):
    one row per loop for the CLI to print, in walk order."""
    rows: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    counts = {"pass": 0, "fail": 0, "error": 0, "not_yet": 0, "skipped": 0}
    judge_cmd = ctx.config.get("judge")
    cage = ctx.config.get("cage")
    caged = bool(cage)

    budget_authority_loops: list[str] = []

    def walk(loop: LoopSpec, inherited: str | None) -> None:
        effective = loop.agent or inherited or default_agent
        if effective and not caged and not resolvable(effective):
            errors.append(f"{loop.id}: agent not found: {effective}")
        elif not effective and loop.task is not None:
            warnings.append(f"{loop.id}: has a task but no agent (agent:, --agent, or config)")
        if loop.gate == Gate.HUMAN and not caged:
            errors.append(
                f"{loop.id}: gate: human requires cage in {ctx.paths.config}; "
                "uncaged agents can read Lute's answer-auth key and forge approval"
            )
        elif loop.gate == Gate.HUMAN and not looks_like_container_runtime(cage):
            warnings.append(
                f"{loop.id}: cage template does not look like a container runtime "
                "(heuristic: expected cage: docker or a docker/podman run template), "
                "so the gate: human guarantee that the agent cannot read Lute's "
                "answer-auth key is not actually enforced"
            )
        if not caged and loop.task is not None and loop.budget.limits:
            budget_authority_loops.append(str(loop.id))
        for pattern in loop.protected:
            if not protected_files([pattern]):
                warnings.append(f"{loop.id}: protected glob {pattern!r} matches no files")
        for check_path in local_check_paths(loop.done_when.command):
            if not protected_covers(check_path, list(loop.protected)):
                warnings.append(f"{loop.id}: done_when invokes {check_path} but it is not covered by protected:")
        if loop.parallel and len(loop.children) < 2:
            warnings.append(f"{loop.id}: parallel with fewer than 2 children runs nothing concurrently")
        if is_placeholder_check(loop.done_when.command):
            warnings.append(f"{loop.id}: done_when looks like a placeholder; use an exam that measures the goal")
            if loop.parallel and loop.children:
                warnings.append(f"{loop.id}: parallel parent needs a real integration done_when for the merged children")
        probe = circular_exam_target(loop.done_when.command)
        if probe and loop.task is not None and not protected_covers(probe, list(loop.protected)):
            warnings.append(
                f"{loop.id}: done_when only checks that {probe} exists — an agent can satisfy that by "
                f"writing it (a circular exam). Measure behavior, or list {probe!r} under protected: as ground truth."
            )
        if loop.done_when.command.startswith("judge:"):
            if not judge_cmd:
                errors.append(f"{loop.id}: judge: check but no judge configured in {ctx.paths.config}")
                cls = "error"
            else:
                if judge_cmd == effective:
                    warnings.append(f"{loop.id}: judge equals the worker agent; the doer must not grade its own homework (§6)")
                if loop.confirm < 2:
                    warnings.append(f"{loop.id}: judge: checks should use confirm: 2")
                if caged or no_exec:
                    cls = "skipped"
                    why = "no-exec lint" if no_exec else "cage"
                    warnings.append(f"{loop.id}: judge dry-run skipped under {why}; verify the judge exists in {ctx.config.get('cage_image', 'alpine:3')}")
                elif not resolvable(judge_cmd):
                    errors.append(f"{loop.id}: judge: check but no resolvable judge in {ctx.paths.config}")
                    cls = "error"
                else:
                    cls = checks.run(loop).verdict.value
        else:
            if no_exec:
                cls = "skipped"
                if subprocess.run(["sh", "-n", "-c", loop.done_when.command], capture_output=True).returncode:
                    cls = "error"
            else:
                cls = checks.run(loop, classify=True).verdict.value
            if cls == "error":
                errors.append(f"{loop.id}: done_when not administrable: `{loop.done_when.command}` (command not found / not on PATH, or not valid shell)")
            if cls == "not_yet" and budget.secs_cap(loop) is None:
                errors.append(
                    f"{loop.id}: done_when returned 75 but budget has no time cap; "
                    "not-yet loops need an s/m/h budget because run budgets do not tick while waiting"
                )
        counts[cls] += 1
        rows.append(f"{cls:7} {loop.id}: {loop.done_when.command}")
        for child in loop.children:
            walk(child, effective)

    walk(root, None)
    if budget_authority_loops:
        preview = ", ".join(budget_authority_loops[:5])
        if len(budget_authority_loops) > 5:
            preview += ", ..."
        warnings.append(
            "uncaged agents can read Lute's answer-auth key; answered cards can refresh budgets for "
            f"{preview}. Configure cage if budget reset is a security boundary."
        )
    errors.extend(validate_schedules(schedules, str(root.id)))
    return rows, warnings, errors, counts
