#!/usr/bin/env python3
"""CLI boundary for lute."""

from __future__ import annotations

import difflib
import os
import re
import shlex
import shutil
import sys
from importlib import resources

from . import cli_args, processes, schema
from .budget import budget_pairs
from .checks import CheckRunner
from .cockpit import spawn_run, tui_ok
from .config import load_config
from .context import AppContext, Paths
from .cron import sync_or_remove, validate_schedules
from .domain import Budget, CheckSpec, LoopId, LoopSpec, RunMode
from .errors import Blocked, Gated, GitError, InternalError, LuteError, PreconditionError, UsageError
from .events import EventBus
from .git_repo import GitRepo
from .landing import land
from .protection import protected_files
from .runner import Runner, entrypoint_path, resolved_loop, self_cmd
from .state_store import StateStore
from .status import render_inbox, render_status
from .watch import render_filtered, render_json, render_snapshot

VERSION = "lute 0.1.0"
SKILL_PATH = "luteloops/SKILL.md"
SKILLS = (SKILL_PATH,)

USAGE = """\
lute: a while-loop for agents
usage:
  lute init [--skill]          scaffold a lute.yaml and .lute/ (--skill: write a local copy of the skill)
  lute lint [file]             validate schema + dry-run every done_when once
  lute run [root-id]           run loops until green (--file F, --agent CMD, --plain, --bg, --dry-run)
  lute once --until C -- TASK   one-shot, no file: run an agent until check C passes
  lute land [branch]           merge lute/<root> into [branch] iff the root exam still passes
  lute watch [file]            read-only cockpit from events (--snapshot, --json, --filter LOG)
  lute stop                    stop the active run (and any parallel children) in this repo
  lute status [file]           may execute checks for loops without open cards, print the loop hierarchy
  lute inbox                   list what's waiting on you (blocked/gated cards)
  lute answer <loop> "text"    reply to an escalation card in INBOX/
  lute plan "<goal>"           an agent drafts lute.proposed.yaml via the skill
  lute cron sync|remove        compile schedules: into a managed crontab block
a check may exit 75 = "not yet": no agent wakes, no run budget spent; the loop
re-asks every check_every (default 60s) while any time budget keeps ticking
gate: human pauses a passing loop for approval · exit codes: 0 all closed,
3 blocked (needs help: lute answer), 4 gated (lute answer <loop> approve)
new here? → lute init  (scaffold a file)  ·  lute plan "<goal>"  (an agent drafts it)
then: lute lint  (validate)  →  lute run  (until green)
"""

HELP = {
    "run": "lute run [root-id]: run loops until every done_when is green.\n"
    "  --file F   use manifest F (default lute.yaml)      --agent CMD  override the agent\n"
    "  --plain    stream one line/event (no cockpit)      --bg         detach into its own session\n"
    "  --dry-run  print the resolved plan + first prompt, spend nothing.  CLI runs select the root; children run through their parent.",
    "once": 'lute once --until "<check>" --agent <cli> -- "<task>": one-shot, no file written.\n'
    "  Runs an agent until <check> (the done_when) passes, on branch lute/<id>.\n"
    "  --id NAME  name the branch (default 'once')        --budget SPEC  e.g. \"20 runs\" or \"2h\".",
    "watch": "lute watch [file]: read-only cockpit rendered from events.\n"
    "  --snapshot  replay events only; no checks run, free but may be stale.\n"
    "  --json      machine-readable snapshot from the same replay-only state.",
    "status": "lute status [file]: live status that may execute done_when/judge checks.\n"
    "  Use lute watch --snapshot for replay-only output from events.",
}


def parse(args, flags, bools=()):
    try:
        return cli_args.parse_args(args, flags, bools)
    except cli_args.UsageError as exc:
        raise UsageError(str(exc)) from exc


def need_pos(pos, usage, min_count=0, max_count=None):
    try:
        cli_args.require_positionals(pos, usage, min_count, max_count)
    except cli_args.UsageError as exc:
        raise UsageError(str(exc)) from exc


def default_file() -> str:
    if not os.path.exists("lute.yaml") and os.path.exists("Luteloops"):
        print('lute: warning: "Luteloops" is deprecated; rename it to lute.yaml', file=sys.stderr)
        return "Luteloops"
    return "lute.yaml"


def strip_skill_frontmatter(body: str) -> str:
    if body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end != -1:
            return body[end + 5:]
    return body


def packaged_skill_body() -> str:
    try:
        return resources.files("luteloops").joinpath("SKILL.md").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise PreconditionError("packaged luteloops skill missing; reinstall Lute or run from a complete checkout") from exc


def load_skill_body() -> tuple[str, str]:
    for path in SKILLS:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return path, f.read()
    return "packaged luteloops skill", packaged_skill_body()


def make_runtime(root_id: str = "", manifest_path: str = "", mode: RunMode = RunMode.FILE, plain: bool = False):
    git = GitRepo.discover()
    os.chdir(git.root)
    paths = Paths.for_repo(git.root, os.environ.get("LUTE_STATE_DIR"))
    store = StateStore(paths)
    store.ensure_layout()
    ctx = AppContext(
        repo_root=git.root,
        paths=paths,
        config=load_config(paths.config),
        manifest_path=manifest_path,
        root_id=root_id,
        mode=mode,
        plain=plain,
    )
    return ctx, git, store, Runner(ctx, git, store)


def load_manifest(path: str, *, run_message: bool = False):
    if not os.path.exists(path):
        if run_message:
            raise PreconditionError(
                f'no {path} here; scaffold one: lute init  ·  draft it: lute plan "<goal>"  ·  '
                'one-shot (no file): lute once --until "<check>" --agent <cli> -- "<task>"'
            )
        raise PreconditionError(f'no {path} here; scaffold one: lute init   (or draft it: lute plan "<goal>")')
    root, schedules, errors = schema.load(path)
    if errors or not root:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise PreconditionError(f"{path} failed validation; see the errors above or run: lute lint {path}")
    return root, schedules


def resolvable(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    while parts and re.match(r"[A-Za-z_]\w*=", parts[0]):
        parts.pop(0)
    return bool(parts) and bool(shutil.which(parts[0]))


def cmd_run(args: list[str]) -> int:
    pos, opts = parse(args, {"--agent", "--file"}, {"--plain", "--bg", "--dry-run"})
    need_pos(pos, "usage: lute run [root-id]", 0, 1)
    ctx0, git, store, _ = make_runtime()
    manifest = os.path.abspath(opts.get("file") or default_file())
    root, _ = load_manifest(manifest, run_message=True)
    child_mode = "LUTE_STATE_DIR" in os.environ
    target = resolved_loop(root, pos[0] if pos else None, child_mode)
    ctx = AppContext(git.root, store.paths, load_config(store.paths.config), manifest, str(root.id), RunMode.CHILD if child_mode else RunMode.FILE, bool(opts.get("plain")))
    runner = Runner(ctx, git, store)
    agents = runner.assign_agents(root, opts.get("agent") or ctx.config.get("agent"))
    if opts.get("dry-run"):
        rows = root.flatten()
        print(f"dry run: {len(rows)} loop(s), run order (children first):")
        for _, loop in rows:
            print(f"  {loop.id}: agent={agents.get(str(loop.id)) or '(none)'}  done_when={loop.done_when.command}")
        first = next((loop for _, loop in rows if loop.task is not None), root)
        print(f"\nfirst prompt → {first.id}\n" + "-" * 40 + "\n" + runner.agents.build_prompt(first, agents.get(str(first.id)) or "", "(no check output yet; dry-run preview)", None))
        return 0
    if opts.get("bg"):
        proc = spawn_run(args, store, ctx.paths.runner_log)
        print(f"detached: run continues (pid {proc.pid}) · re-attach: lute watch · stop: lute stop")
        return 0
    if not opts.get("plain") and tui_ok():
        proc = spawn_run(args, store, ctx.paths.runner_log)
        print(f"detached: run continues (pid {proc.pid}) · re-attach: lute watch · stop: lute stop")
        return 0
    ctx.plain = True
    if child_mode:
        runner.run_child(target, agents)
    else:
        runner.run_toplevel(root, agents)
    return 0


def cmd_once(args: list[str]) -> int:
    pos, opts = parse(args, {"--until", "--agent", "--id", "--budget"})
    ctx, git, store, runner = make_runtime(mode=RunMode.FILELESS, plain=True)
    until, task = opts.get("until"), " ".join(pos).strip()
    if not until or not task:
        raise UsageError('usage: lute once --until "<check>" --agent <cli> -- "<task>"')
    agent = opts.get("agent") or ctx.config.get("agent")
    if not agent:
        raise UsageError(f"no agent: pass --agent or set agent in {ctx.paths.config}")
    loop_id = opts.get("id") or "once"
    if not schema.ID_RE.match(loop_id):
        raise UsageError(f"--id must be a slug like 'fix-tests', got {loop_id!r}")
    errors: list[str] = []
    budget = schema.parse_budget(opts.get("budget", "10 runs"), loop_id, errors)
    if errors:
        raise UsageError("; ".join(errors))
    loop = LoopSpec(LoopId(loop_id), task, agent, CheckSpec(until), Budget.from_pairs(budget))
    ctx.root_id = loop_id
    runner.run_toplevel(loop, {loop_id: agent})
    return 0


def cmd_plan(args: list[str]) -> int:
    pos, opts = parse(args, {"--agent"})
    if len(pos) != 1:
        raise UsageError('usage: lute plan "<goal>"')
    ctx, git, store, runner = make_runtime(root_id="plan", mode=RunMode.FILELESS, plain=True)
    source, body = load_skill_body()
    body = strip_skill_frontmatter(body)
    agent = opts.get("agent") or ctx.config.get("agent")
    if not agent:
        raise UsageError(f"no agent: pass --agent or set agent in {ctx.paths.config}")
    loop = LoopSpec(
        LoopId("plan"),
        f"Write lute.proposed.yaml for: {pos[0]}\n\n"
        f"The luteloops skill from {source} follows. Obey it:\n\n{body.strip()}",
        agent,
        CheckSpec(f"{self_cmd()} lint lute.proposed.yaml"),
        Budget.from_pairs([("runs", 10)]),
    )
    runner.run_toplevel(loop, {"plan": agent})
    print("✔ plan closed: review lute.proposed.yaml and rename it to lute.yaml")
    return 0


def cmd_lint(args: list[str]) -> int:
    pos, opts = parse(args, {"--agent"})
    need_pos(pos, "usage: lute lint [file]", 0, 1)
    ctx, git, store, runner = make_runtime()
    path = pos[0] if pos else default_file()
    if not os.path.exists(path):
        raise PreconditionError(f'no {path} here; scaffold one: lute init   (or draft it: lute plan "<goal>")')
    root, schedules, errors = schema.load(path)
    warnings, counts = [], {"pass": 0, "fail": 0, "error": 0, "not_yet": 0}
    if root:
        default_agent = opts.get("agent") or ctx.config.get("agent")
        judge_cmd = ctx.config.get("judge")
        caged = bool(ctx.config.get("cage"))

        def walk(loop: LoopSpec, inherited: str | None) -> None:
            effective = loop.agent or inherited or default_agent
            if effective and not caged and not resolvable(effective):
                errors.append(f"{loop.id}: agent not found: {effective}")
            elif not effective and loop.task is not None:
                warnings.append(f"{loop.id}: has a task but no agent (agent:, --agent, or config)")
            for pattern in loop.protected:
                if not protected_files([pattern]):
                    warnings.append(f"{loop.id}: protected glob {pattern!r} matches no files")
            if loop.parallel and len(loop.children) < 2:
                warnings.append(f"{loop.id}: parallel with fewer than 2 children runs nothing concurrently")
            if loop.done_when.command.startswith("judge:"):
                if not judge_cmd:
                    errors.append(f"{loop.id}: judge: check but no judge configured in {ctx.paths.config}")
                    cls = "error"
                else:
                    if judge_cmd == effective:
                        warnings.append(f"{loop.id}: judge equals the worker agent; the doer must not grade its own homework (§6)")
                    if caged:
                        cls = "pass"
                        warnings.append(f"{loop.id}: judge dry-run skipped under cage; verify the judge exists in {ctx.config.get('cage_image', 'alpine:3')}")
                    elif not resolvable(judge_cmd):
                        errors.append(f"{loop.id}: judge: check but no resolvable judge in {ctx.paths.config}")
                        cls = "error"
                    else:
                        cls = runner.checks.run(loop, lenient=True).verdict.value
            else:
                cls = runner.checks.run(loop, classify=True).verdict.value
                if cls == "error":
                    errors.append(f"{loop.id}: done_when not administrable: `{loop.done_when.command}` (command not found / not on PATH, or not valid shell)")
            counts[cls] += 1
            print(f"{cls:5} {loop.id}: {loop.done_when.command}")
            for child in loop.children:
                walk(child, effective)

        walk(root, None)
        errors.extend(validate_schedules(schedules, str(root.id)))
    for warning in warnings:
        print(f"warn: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    print(f"lint: {sum(counts.values())} check(s), {counts['pass']} pass, {counts['fail']} fail, {counts['error']} error" + (f", {counts['not_yet']} not-yet" if counts["not_yet"] else ""))
    if not errors:
        no_agent = any("no agent" in warning for warning in warnings)
        print("no schema errors; set an agent first (see the warning above), then: lute run" if no_agent else "no schema errors; next: lute run")
    return 1 if errors else 0


def cmd_init(args: list[str]) -> int:
    pos, opts = parse(args, set(), {"--skill"})
    need_pos(pos, "usage: lute init [--skill]", 0, 0)
    ctx, git, store, runner = make_runtime()
    if opts.get("skill"):
        path = SKILL_PATH
        if os.path.exists(path):
            raise PreconditionError(f"{path} already exists")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(packaged_skill_body())
        print(f'scaffolded {path}; now: lute plan "<goal>"')
        return 0
    if os.path.exists("lute.yaml"):
        raise PreconditionError("lute.yaml already exists")
    if os.path.exists("Luteloops"):
        raise PreconditionError('"Luteloops" (deprecated) already exists; rename it to lute.yaml')
    store.ensure_dir(ctx.paths.journal)
    with open("lute.yaml", "w", encoding="utf-8") as f:
        f.write("# lute.yaml - a luteloops file: turtles all the way down for the lute runner (see\n"
                "# README.md and the packaged luteloops skill for how to decompose a goal).\n"
                "loop: my-goal\n# agent: claude -p\n"
                "task: Replace me with instructions for one agent iteration.\n"
                'done_when: "false"\nbudget: 10 runs\n')
    if not store.is_regular_file(ctx.paths.config):
        store.safe_write_regular(ctx.paths.config, "# lute config\n# agent: claude -p\n# judge: codex exec\n")
    print("initialized lute.yaml and .lute/; set an agent (uncomment agent:, or use --agent / config), replace the task: and done_when: lines, then: lute lint")
    return 0


def cmd_inbox(args: list[str]) -> int:
    pos, _ = parse(args, set())
    need_pos(pos, "usage: lute inbox", 0, 0)
    *_, runner = make_runtime()
    render_inbox(runner.cards)
    return 0


def cmd_answer(args: list[str]) -> int:
    pos, _ = parse(args, set())
    if len(pos) != 2:
        raise UsageError('usage: lute answer <loop> "..."')
    *_, runner = make_runtime()
    error = runner.cards.answer_card(pos[0], pos[1])
    if error:
        raise UsageError(error)
    print(f"answer recorded: the next run of {pos[0]} injects it and refreshes its budget once (to change it first, edit or delete {runner.cards.path(pos[0])})")
    return 0


def cmd_status(args: list[str]) -> int:
    pos, _ = parse(args, set())
    need_pos(pos, "usage: lute status [file]", 0, 1)
    ctx, git, store, runner = make_runtime()
    root, _ = load_manifest(pos[0] if pos else default_file())
    render_status(root, runner.checks, runner.cards, ctx.paths.ledger)
    return 0


def cmd_watch(args: list[str]) -> int:
    pos, opts = parse(args, {"--filter"}, {"--snapshot", "--json"})
    need_pos(pos, "usage: lute watch [file]", 0, 0 if opts.get("filter") else 1)
    if opts.get("filter"):
        render_filtered(opts["filter"])
        return 0
    ctx, git, store, runner = make_runtime()
    root, _ = load_manifest(pos[0] if pos else default_file())
    if opts.get("json"):
        render_json(root, ctx.paths.events, runner.cards)
    else:
        if not opts.get("snapshot") and tui_ok():
            render_snapshot(root, ctx.paths.events)
        else:
            if not opts.get("snapshot"):
                print("lute: no TTY/curses here; one-shot snapshot instead:", file=sys.stderr)
            render_snapshot(root, ctx.paths.events)
    return 0


def cmd_stop(args: list[str]) -> int:
    pos, _ = parse(args, set())
    need_pos(pos, "usage: lute stop", 0, 0)
    ctx, git, store, runner = make_runtime()
    lock = ctx.paths.lock
    if not store.is_regular_file(lock):
        print("no active run in this repo")
        return 0
    try:
        import json
        info = json.loads(open(lock).read())
    except (OSError, ValueError):
        info = {}
    pid = info.get("pid")
    entry = entrypoint_path()
    if not processes.command_contains(pid, entry) or not processes.serves_repo(pid, git.root):
        store.remove_runner_file(lock)
        print(f"no active run here; cleared a stale lock (pid {pid})")
        return 0
    children = 0
    if os.path.isdir(ctx.paths.worktrees):
        for name in sorted(os.listdir(ctx.paths.worktrees)):
            if not name.endswith(".pid"):
                continue
            try:
                child_pid = int(open(os.path.join(ctx.paths.worktrees, name)).read().split("\n", 1)[0])
            except (OSError, ValueError):
                continue
            if processes.command_contains(child_pid, entry):
                processes.stop_group(child_pid)
                children += 1
    gone = processes.stop_group(pid)
    tail = f" + {children} parallel child group(s)" if children else ""
    if gone:
        print(f"stopped run pid {pid}{tail}; a half-done iteration is dropped on the next run (state is git-derived)")
        return 0
    print(f"could not confirm pid {pid} stopped{tail}; it may still be running; check with: kill -0 {pid}", file=sys.stderr)
    return 1


def cmd_land(args: list[str]) -> int:
    pos, opts = parse(args, {"--file"})
    need_pos(pos, "usage: lute land [branch]", 0, 1)
    ctx, git, store, runner = make_runtime()
    manifest = os.path.abspath(opts.get("file") or default_file())
    root, _ = load_manifest(manifest)
    ctx.manifest_path = manifest
    ctx.root_id = str(root.id)
    land(runner, root, pos[0] if pos else None, runner.cards, runner.checks, runner.events)
    return 0


def cmd_cron(args: list[str]) -> int:
    if args not in (["sync"], ["remove"]):
        raise UsageError("usage: lute cron sync|remove")
    ctx, git, store, runner = make_runtime()
    root = schedules = None
    if args[0] == "sync":
        root, schedules = load_manifest(default_file())
    sync_or_remove(args[0], git.root, root, schedules or [])
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except Exception:
            pass
    if any(arg in ("-V", "--version") for arg in argv):
        print(VERSION)
        return 0
    if any(arg in ("-h", "--help", "help") for arg in argv):
        verb = argv[0] if argv else ""
        sys.stdout.write(HELP[verb] + "\n" if verb in HELP else USAGE)
        return 0
    commands = {
        "init": cmd_init,
        "lint": cmd_lint,
        "run": cmd_run,
        "status": cmd_status,
        "answer": cmd_answer,
        "plan": cmd_plan,
        "cron": cmd_cron,
        "watch": cmd_watch,
        "inbox": cmd_inbox,
        "once": cmd_once,
        "stop": cmd_stop,
        "land": cmd_land,
    }
    if argv and argv[0] not in commands:
        near = difflib.get_close_matches(argv[0], commands, 1)
        if near:
            print(f"lute: unknown command {argv[0]!r}; did you mean {near[0]}?\n", file=sys.stderr)
    if not argv or argv[0] not in commands:
        print(USAGE, end="")
        return 1
    try:
        return commands[argv[0]](argv[1:])
    except (Blocked, Gated) as exc:
        if str(exc) not in ("blocked", "gated"):
            print(f"lute: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except LuteError as exc:
        print(f"lute: {exc}", file=sys.stderr)
        return int(exc.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
