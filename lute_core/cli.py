#!/usr/bin/env python3
"""CLI boundary for lute."""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import sys
from importlib import resources

from . import cli_args, judge, processes, schema
from .cards import summarize_card
from .detached import spawn_run, terminal_ok
from .config import load_config
from .context import AppContext, Paths
from .cron import sync_or_remove
from .domain import Budget, CheckSpec, LoopId, LoopSpec, RunMode
from .errors import Blocked, Gated, LuteError, PreconditionError, UsageError
from .git_repo import GitRepo
from .landing import land
from .linting import lint_manifest
from .planner import build_plan_task, repo_briefing
from .runner import Runner, resolved_loop, self_cmd
from .state_store import StateStore
from .status import render_inbox, render_status
from .watch import render_filtered, render_json, render_snapshot

VERSION = "lute 0.1.0"
SKILL_PATH = "luteloops/SKILL.md"
SKILLS = (SKILL_PATH,)
PLAN_PROTECTED = ("lute", "lute_core/**", SKILL_PATH)

USAGE = """\
lute: a while-loop for agents
usage:
  lute init [--skill]          scaffold a lute.yaml and .lute/ (--skill: write a local copy of the skill)
  lute lint [--no-exec] [file] validate schema + dry-run checks (caged judges skipped)
  lute run [root-id]           run loops until green (--file F, --agent CMD, --plain, --bg, --dry-run)
  lute once --until C -- TASK   one-shot, no file: run an agent until check C passes
  lute land [branch]           merge lute/<root> into [branch] iff the root exam still passes
  lute watch [file]            read-only event snapshot (--snapshot, --json, --filter LOG)
  lute stop                    stop the active run (and any parallel children) in this repo
  lute status [file]           may execute checks for loops without unanswered cards
  lute inbox                   list what's waiting on you (blocked/gated cards)
  lute answer <loop> "text"    reply to an escalation card in INBOX/
  lute judge -- "<rubric>"     grade HEAD's diff with the configured judge (the oracle behind done_when: "judge: ...")
  lute quarantine [list]       inspect quarantined trusted-exam edits
  lute plan [--dag] [--keep-dag] "<goal>"  an agent drafts lute.proposed.yaml via the skill
  lute cron sync|remove        compile schedules: into a managed crontab block
a check may exit 75 = "not yet": no agent wakes, no run budget spent; the loop
re-asks every check_every (default 60s) while any time budget keeps ticking;
an uncapped not-yet blocks immediately instead of hanging
gate: human pauses a passing loop for approval and requires cage · exit codes: 0 all closed,
3 blocked (needs help: lute answer), 4 gated (lute answer <loop> approve)
new here? → lute init  (scaffold a file)  ·  lute plan "<goal>"  (an agent drafts it)
then: lute lint  (validate)  →  lute run  (until green)
"""

HELP = {
    "run": "lute run [root-id]: run loops until every done_when is green.\n"
    "  --file F   use manifest F (default lute.yaml)      --agent CMD  override the agent\n"
    "  --plain    stream one line/event in foreground     --bg         detach into its own session\n"
    "  --dry-run  print the resolved plan + first prompt, spend nothing.\n"
    "  --skip-if-running  exit 0 without work if another run holds this repo's lock.",
    "once": 'lute once --until "<check>" --agent <cli> -- "<task>": one-shot, no file written.\n'
    "  Runs an agent until <check> (the done_when) passes, on branch lute/<id>.\n"
    "  --id NAME  name the branch (default 'once')        --budget SPEC  e.g. \"20 runs\" or \"2h\".",
    "watch": "lute watch [file]: read-only snapshot rendered from events.\n"
    "  --snapshot  replay events only; no checks run, free but may be stale.\n"
    "  --json      machine-readable snapshot from the same replay-only state.",
    "status": "lute status [file]: live status that may execute done_when/judge checks.\n"
    "  Use lute watch --snapshot for replay-only output from events.",
    "quarantine": "lute quarantine [list|diff <id>|drop <id>|drop --all]: inspect or drop quarantined trusted-exam edits.\n"
    "  list is read-only. diff prints the stored patch. drop removes stored quarantine records only.",
    "plan": 'lute plan [--dag] [--keep-dag] "<goal>": draft lute.proposed.yaml via the skill.\n'
    "  --dag       ask the planner to reason from a workflow DAG, then emit normal Lute YAML\n"
    "  --keep-dag  with --dag, also write lute.plan.yaml as a review/debug artifact.",
}

QID_RE = re.compile(r"[A-Za-z0-9_.-]+")


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
        if os.path.lexists(path):
            if os.path.islink(path) or not os.path.isfile(path):
                raise PreconditionError(f"{path} must be a regular file")
            with open(path, encoding="utf-8") as f:
                return path, f.read()
    return "packaged luteloops skill", packaged_skill_body()


def trusted_file(path: str, label: str, shared_root: str) -> str:
    full = os.path.abspath(path)
    if os.path.islink(full):
        raise PreconditionError(f"{label} must be a regular file, not a symlink: {path}")
    if os.path.exists(full):
        real, root = os.path.realpath(full), os.path.realpath(shared_root)
        try:
            inside = os.path.commonpath([root, real]) == root
        except ValueError:
            inside = False
        if not inside:
            raise PreconditionError(f"{label} must live inside this repository: {path}")
    return full


def make_context(root_id: str = "", manifest_path: str = "", mode: RunMode = RunMode.FILE, plain: bool = False):
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
    return ctx, git, store


def make_runtime(root_id: str = "", manifest_path: str = "", mode: RunMode = RunMode.FILE, plain: bool = False):
    ctx, git, store = make_context(root_id, manifest_path, mode, plain)
    return ctx, git, store, Runner(ctx, git, store)


def load_manifest(path: str, *, run_message: bool = False, shared_root: str | None = None):
    if not os.path.exists(path):
        if run_message:
            raise PreconditionError(
                f'no {path} here; scaffold one: lute init  ·  draft it: lute plan "<goal>"  ·  '
                'one-shot (no file): lute once --until "<check>" --agent <cli> -- "<task>"'
            )
        raise PreconditionError(f'no {path} here; scaffold one: lute init   (or draft it: lute plan "<goal>")')
    path = trusted_file(path, "manifest", shared_root or os.getcwd())
    root, schedules, errors = schema.load(path)
    if errors or not root:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise PreconditionError(f"{path} failed validation; see the errors above or run: lute lint {path}")
    return root, schedules


def quarantine_records(paths: Paths) -> list[dict]:
    store = StateStore(paths)
    records: list[dict] = []
    if not store.is_dir(paths.quarantine):
        return records
    for name in sorted(os.listdir(paths.quarantine)):
        if not QID_RE.fullmatch(name):
            continue
        qdir = os.path.join(paths.quarantine, name)
        if not store.is_dir(qdir):
            continue
        path = os.path.join(qdir, "meta.json")
        patch = os.path.join(qdir, "changes.patch")
        if not store.is_regular_file(path) or not store.is_regular_file(patch):
            continue
        try:
            record = json.loads(store.read_text(path))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        qid = str(record.get("id") or name)
        if not QID_RE.fullmatch(qid):
            continue
        record["id"] = qid
        record["_meta"] = path
        record["_dir"] = qdir
        record["_patch"] = patch
        records.append(record)
    return records


def require_quarantine_id(qid: str) -> str:
    if not QID_RE.fullmatch(qid):
        raise UsageError(f"invalid quarantine id {qid!r}")
    return qid


def cmd_run(args: list[str]) -> int:
    pos, opts = parse(args, {"--agent", "--file"}, {"--plain", "--bg", "--dry-run", "--skip-if-running"})
    need_pos(pos, "usage: lute run [root-id]", 0, 1)
    ctx0, git, store = make_context()
    manifest = os.path.abspath(opts.get("file") or default_file())
    manifest = trusted_file(manifest, "manifest", ctx0.shared_root)
    root, _ = load_manifest(manifest, run_message=True, shared_root=ctx0.shared_root)
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
    if opts.get("skip-if-running"):
        info = runner.active_lock_info()
        if info:
            print(
                f"lute: skip {target.id}; another run is active in this repo "
                f"(pid {info.get('pid')}, since {info.get('start', '?')})"
            )
            return 0
    if opts.get("bg"):
        proc = spawn_run(args, store, ctx.paths.runner_log)
        print(f"detached: run continues (pid {proc.pid}) · re-attach: lute watch · stop: lute stop")
        return 0
    if not opts.get("plain") and terminal_ok():
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


def dag_plan_instructions(keep_dag: bool) -> str:
    keep = (
        "\nBecause --keep-dag was passed, also write lute.plan.yaml as a review/debug artifact "
        "showing the workflow DAG you used. lute.plan.yaml is not the runtime contract; "
        "lute.proposed.yaml is."
        if keep_dag
        else "\nDo not write a separate DAG artifact unless explicitly asked; keep the dependency reasoning inside your planning process."
    )
    return (
        "\n\nDAG planning mode:\n"
        "- First derive a workflow DAG of independently verifiable milestones for the goal: nodes, prerequisite edges, and possible fan-out/fan-in.\n"
        "- Use that DAG only as an authoring aid. The final file you write must be lute.proposed.yaml, and it must be ordinary Lute YAML.\n"
        "- Do not put DAG-only keys in lute.proposed.yaml: no depends_on, dag, nodes, or edges.\n"
        "- Compile dependencies into Lute-native structure: list order for sequence, nesting for AND/integration, done_when shell logic for conditions, and parallel: true only for direct sibling loops that touch disjoint files/resources.\n"
        "- Any parallel parent must have its own done_when integration check for the merged result.\n"
        f"{keep}"
    )


def cmd_plan(args: list[str]) -> int:
    pos, opts = parse(args, {"--agent"}, {"--dag", "--keep-dag"})
    if len(pos) != 1:
        raise UsageError('usage: lute plan [--dag] [--keep-dag] "<goal>"')
    if opts.get("keep-dag") and not opts.get("dag"):
        raise UsageError("--keep-dag requires --dag")
    ctx, git, store, runner = make_runtime(root_id="plan", mode=RunMode.FILELESS, plain=True)
    source, body = load_skill_body()
    body = strip_skill_frontmatter(body)
    agent = opts.get("agent") or ctx.config.get("agent")
    if not agent:
        raise UsageError(f"no agent: pass --agent or set agent in {ctx.paths.config}")
    dag_instructions = dag_plan_instructions(bool(opts.get("keep-dag"))) if opts.get("dag") else ""
    check = f"{self_cmd()} lint --no-exec lute.proposed.yaml"
    if opts.get("keep-dag"):
        check = f"test -f lute.plan.yaml && {check}"
    task = build_plan_task(pos[0], source, body, repo_briefing(pos[0], git), dag_instructions)
    loop = LoopSpec(
        id=LoopId("plan"),
        task=task,
        agent=agent,
        done_when=CheckSpec(check),
        budget=Budget.from_pairs([("runs", 10)]),
        protected=PLAN_PROTECTED,
    )
    runner.run_toplevel(loop, {"plan": agent})
    if opts.get("keep-dag"):
        print("✔ dag plan closed: review lute.plan.yaml, then lute.proposed.yaml and rename it to lute.yaml")
    elif opts.get("dag"):
        print("✔ dag plan closed: review lute.proposed.yaml and rename it to lute.yaml")
    else:
        print("✔ plan closed: review lute.proposed.yaml and rename it to lute.yaml")
    return 0


def cmd_lint(args: list[str]) -> int:
    pos, opts = parse(args, {"--agent"}, {"--no-exec"})
    need_pos(pos, "usage: lute lint [file]", 0, 1)
    ctx, git, store, runner = make_runtime()
    path = pos[0] if pos else default_file()
    if not os.path.exists(path):
        raise PreconditionError(f'no {path} here; scaffold one: lute init   (or draft it: lute plan "<goal>")')
    path = trusted_file(path, "manifest", ctx.shared_root)
    root, schedules, errors = schema.load(path)
    warnings, counts = [], {"pass": 0, "fail": 0, "error": 0, "not_yet": 0, "skipped": 0}
    if root:
        rows, warnings, walk_errors, counts = lint_manifest(
            root,
            schedules,
            ctx,
            runner.checks,
            runner.budget,
            default_agent=opts.get("agent") or ctx.config.get("agent"),
            no_exec=bool(opts.get("no-exec")),
        )
        for row in rows:
            print(row)
        errors.extend(walk_errors)
    for warning in warnings:
        print(f"warn: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    print(
        f"lint: {sum(counts.values())} check(s), {counts['pass']} pass, {counts['fail']} fail, {counts['error']} error"
        + (f", {counts['not_yet']} not-yet" if counts["not_yet"] else "")
        + (f", {counts['skipped']} skipped" if counts["skipped"] else "")
    )
    print("exam note: avoid circular exams that pass by echoing the task string; measure behavior or protected ground truth.")
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
        store.safe_write_regular(ctx.paths.config, "# lute config\n# agent: claude -p\n# judge: codex exec\n# cage: docker\n")
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
    card = None
    path = runner.cards.path(pos[0])
    if runner.store.is_regular_file(path):
        with open(path, encoding="utf-8") as f:
            card = summarize_card(pos[0], f.read())
    error = runner.cards.answer_card(pos[0], pos[1])
    if error:
        raise UsageError(error)
    if card and card.kind == "ready":
        if pos[1].strip() == "approve":
            msg = f"answer recorded: the next run of {pos[0]} re-verifies and seals the gate if it still passes"
        else:
            msg = f"answer recorded: non-approve text will not seal {pos[0]}; the gate remains closed until you answer approve"
        print(f"{msg} (to change it first, edit or delete {path})")
    else:
        print(f"answer recorded: the next run of {pos[0]} injects it and refreshes its budget once (to change it first, edit or delete {path})")
    return 0


def cmd_quarantine(args: list[str]) -> int:
    pos, opts = parse(args, set(), {"--all"})
    git = GitRepo.discover()
    paths = Paths.for_repo(git.root, os.environ.get("LUTE_STATE_DIR"))
    store = StateStore(paths)
    records = quarantine_records(paths)
    verb = pos[0] if pos else "list"
    if verb == "list":
        need_pos(pos, "usage: lute quarantine [list|diff <id>|drop <id>|drop --all]", 0, 1)
        if not records:
            print("quarantine: empty")
            return 0
        for record in records:
            paths_text = ", ".join(record.get("paths") or [])
            count = len(record.get("paths") or [])
            print(f"{record['id']}  {record.get('loop', '')} {record.get('run', '')}  {count} file(s)  {paths_text}")
        return 0
    if verb == "diff":
        need_pos(pos, "usage: lute quarantine diff <id>", 2, 2)
        qid = require_quarantine_id(pos[1])
        record = next((r for r in records if r["id"] == qid), None)
        if not record:
            raise UsageError(f"no quarantine record {qid}")
        sys.stdout.write(store.read_text(record["_patch"]))
        return 0
    if verb == "drop":
        if opts.get("all") and len(pos) == 1:
            for record in records:
                if record.get("_dir"):
                    shutil.rmtree(record["_dir"], ignore_errors=True)
            print(f"dropped {len(records)} quarantine record(s)")
            return 0
        if opts.get("all"):
            raise UsageError("usage: lute quarantine drop <id|--all>")
        need_pos(pos, "usage: lute quarantine drop <id|--all>", 2, 2)
        qid = require_quarantine_id(pos[1])
        record = next((r for r in records if r["id"] == qid), None)
        if not record:
            raise UsageError(f"no quarantine record {qid}")
        shutil.rmtree(record["_dir"], ignore_errors=True)
        print(f"dropped quarantine {qid}")
        return 0
    raise UsageError("usage: lute quarantine [list|diff <id>|drop <id>|drop --all]")


def cmd_status(args: list[str]) -> int:
    pos, _ = parse(args, set())
    need_pos(pos, "usage: lute status [file]", 0, 1)
    ctx, git, store, runner = make_runtime()
    root, _ = load_manifest(pos[0] if pos else default_file(), shared_root=ctx.shared_root)
    render_status(root, runner.checks, runner.cards, ctx.paths.ledger)
    return 0


def cmd_watch(args: list[str]) -> int:
    pos, opts = parse(args, {"--filter"}, {"--snapshot", "--json"})
    need_pos(pos, "usage: lute watch [file]", 0, 0 if opts.get("filter") else 1)
    if opts.get("filter"):
        render_filtered(opts["filter"])
        return 0
    ctx, git, store, runner = make_runtime()
    root, _ = load_manifest(pos[0] if pos else default_file(), shared_root=ctx.shared_root)
    if opts.get("json"):
        render_json(root, ctx.paths.events, runner.cards)
    else:
        if not opts.get("snapshot") and not terminal_ok():
            print("lute: no interactive terminal here; one-shot snapshot instead:", file=sys.stderr)
        render_snapshot(root, ctx.paths.events)
    return 0


def cmd_judge(args: list[str]) -> int:
    pos, _ = parse(args, set())
    rubric = " ".join(pos).strip()
    if not rubric:
        raise UsageError('usage: lute judge -- "<rubric>"   (usually written as done_when: "judge: <rubric>")')
    ctx, git, store, runner = make_runtime()
    return judge.grade(rubric, ctx, git, runner.agents.cage_wrap)


# How a lock-holding lute invocation looks on ps: the entrypoint carries "lute"
# (the script, or `-m lute_core.cli`) followed by a verb that takes the run lock.
# The lock file itself is untrusted (a crash leaves it stale, a pid gets reused,
# anything in the repo can write it); this live argv shape is the identity factor
# a forged or recycled pid cannot fake.
RUN_MARKER = r"lute\S* (run|once|plan|land)\b"


def cmd_stop(args: list[str]) -> int:
    pos, _ = parse(args, set())
    need_pos(pos, "usage: lute stop", 0, 0)
    ctx, git, store, runner = make_runtime()
    lock = ctx.paths.lock
    if not store.is_regular_file(lock):
        print("no active run in this repo")
        return 0
    try:
        with open(lock, encoding="utf-8") as f:
            info = json.loads(f.read())
    except (OSError, ValueError):
        info = {}
    pid = info.get("pid")
    # Identify the runner from live host facts, never from the lock alone (the
    # same two-factor identity reap_orphans uses); then let it tear down what it
    # owns. Anything short of proof is reported, not signalled.
    owned = processes.owns(pid, git.root, RUN_MARKER) if isinstance(pid, int) and pid > 0 else False
    if owned is False:
        store.remove_runner_file(lock)
        print(f"no active run here; cleared a stale lock (pid {pid})")
        return 0
    if owned is None:
        print(
            f"could not confirm pid {pid} is this repo's runner; lock preserved. "
            f"If it is, try: kill -INT {pid}",
            file=sys.stderr,
        )
        return 1
    if processes.stop_run(pid):
        print(f"stopped run pid {pid}; a half-done iteration is dropped on the next run (state is git-derived)")
        return 0
    print(f"could not confirm pid {pid} stopped; it may still be running; check with: kill -0 {pid}", file=sys.stderr)
    return 1


def cmd_land(args: list[str]) -> int:
    pos, opts = parse(args, {"--file"})
    need_pos(pos, "usage: lute land [branch]", 0, 1)
    ctx, git, store, runner = make_runtime()
    manifest = os.path.abspath(opts.get("file") or default_file())
    manifest = trusted_file(manifest, "manifest", ctx.shared_root)
    root, _ = load_manifest(manifest, shared_root=ctx.shared_root)
    ctx.manifest_path = manifest
    ctx.root_id = str(root.id)
    land(runner, root, pos[0] if pos else None, runner.cards, runner.checks)
    return 0


def cmd_cron(args: list[str]) -> int:
    if args not in (["sync"], ["remove"]):
        raise UsageError("usage: lute cron sync|remove")
    ctx, git, store, runner = make_runtime()
    root = schedules = None
    if args[0] == "sync":
        root, schedules = load_manifest(default_file(), shared_root=ctx.shared_root)
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
        "judge": cmd_judge,
        "quarantine": cmd_quarantine,
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
