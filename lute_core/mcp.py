"""A Model Context Protocol server that exposes lute's verbs as tools.

Stdlib only — a hand-rolled JSON-RPC 2.0 server over the stdio transport, no
SDK — so the one-dependency rule holds. Each tool call shells out to the real
`lute` CLI in a fresh process (perfect isolation, no logic duplicated), so an
MCP client (Claude Code, Codex, anything that speaks MCP) operates lute exactly
as a human typing the verb would. The tool descriptions carry the judgement an
agent needs: hand a bounded, machine-checkable, walk-away task to lute instead
of grinding it inline — lute re-runs the exam itself and cannot fake done.

Only protocol messages go to stdout; everything else (there is nothing else)
would go to stderr. One JSON-RPC message per line, newline-delimited.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from . import __version__
from .runner import self_argv

PROTOCOL_VERSION = "2025-11-25"

SERVER_INSTRUCTIONS = (
    "lute runs a coding agent in a loop until a machine-checkable shell check passes, "
    "committing every attempt to a git branch; the runner re-runs the check itself, so a "
    "close means the exam passed, not that the agent said so. Reach for these tools when a "
    "task is bounded, has a clear pass/fail command, and is a walk-away job you would rather "
    "run to completion unattended than grind inline — fixing a failing suite, repairing a dependency "
    "bump, a mechanical migration. Do NOT use lute for interactive or exploratory work, or when "
    "there is no machine-checkable finish line; do that yourself. Flow: lute_once for a single "
    "check, or lute_plan then lute_run for a decomposed goal; poll lute_watch or lute_status; "
    "answer a blocked card with lute_answer. Runs detach by default — the call returns fast and "
    "you poll; pass wait=true only for a short job you want to block on."
)

_AGENT = {
    "type": "string",
    "description": "the coding-agent command lute drives, e.g. 'codex exec --sandbox workspace-write' "
    "or 'claude -p --permission-mode acceptEdits'. Omit to use the manifest's agent or .lute/config.yaml.",
}
_REPO = {"type": "string", "description": "path to the target git repository (default: the server's working directory)."}
_FILE = {"type": "string", "description": "manifest path (default lute.yaml)."}


def _obj(properties: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": {**properties, "repo": _REPO}, "required": list(required)}


def _flag(argv: list, name: str, value) -> None:
    if value is not None and value is not False:
        argv.append(name)
        if value is not True:
            argv.append(str(value))


# --- the verb → argv builders. Each returns the lute argv AFTER the verb name. ---

def _run(a):
    argv = ["run"]
    _flag(argv, "--file", a.get("file"))
    _flag(argv, "--agent", a.get("agent"))
    _flag(argv, "--skip-if-running", a.get("skip_if_running"))
    argv.append("--plain" if a.get("wait") else "--bg")
    if a.get("dry_run"):
        argv = ["run", "--dry-run"] + (["--file", a["file"]] if a.get("file") else []) + (["--agent", a["agent"]] if a.get("agent") else [])
    if a.get("root_id"):
        argv.append(str(a["root_id"]))
    return argv


def _once(a):
    argv = ["once", "--until", a["until"]]
    _flag(argv, "--agent", a.get("agent"))
    _flag(argv, "--id", a.get("id"))
    _flag(argv, "--budget", a.get("budget"))
    return argv + ["--", a["task"]]


def _plan(a):
    argv = ["plan"]
    _flag(argv, "--dag", a.get("dag"))
    _flag(argv, "--keep-dag", a.get("keep_dag"))
    _flag(argv, "--agent", a.get("agent"))
    return argv + [a["goal"]]


def _lint(a):
    argv = ["lint"]
    _flag(argv, "--no-exec", a.get("no_exec"))
    _flag(argv, "--agent", a.get("agent"))
    if a.get("file"):
        argv.append(str(a["file"]))
    return argv


def _watch(a):
    argv = ["watch"]
    if a.get("filter"):
        return ["watch", "--filter", str(a["filter"])]
    argv.append("--json" if a.get("json", True) else "--snapshot")
    if a.get("file"):
        argv.append(str(a["file"]))
    return argv


def _quarantine(a):
    action = a.get("action", "list")
    if action == "drop_all":
        return ["quarantine", "drop", "--all"]
    argv = ["quarantine", action]
    if a.get("id"):
        argv.append(str(a["id"]))
    return argv


def _attest(a):
    argv = ["attest"]
    _flag(argv, "--verify", a.get("verify"))
    _flag(argv, "--file", a.get("file"))
    return argv


TOOLS = [
    {
        "name": "lute_once",
        "description": "Hand off a single bounded task: run a coding agent in a self-verifying loop until "
        "a shell check passes, on a throwaway git branch (no manifest written). Use this instead of doing "
        "the task yourself when there is one clear pass/fail command and you'd rather it run to completion "
        "— failing tests to fix, a mechanical change gated by a test. The loop re-runs the check itself and "
        "cannot fake done. Detaches by default; poll with lute_watch.",
        "inputSchema": _obj({
            "task": {"type": "string", "description": "what one iteration should do."},
            "until": {"type": "string", "description": "the shell check whose exit 0 means done (e.g. 'pytest -q')."},
            "agent": _AGENT,
            "budget": {"type": "string", "description": "cap, e.g. '20 runs' or '2h' (default 10 runs)."},
            "id": {"type": "string", "description": "branch name suffix (default 'once')."},
            "wait": {"type": "boolean", "description": "block until the loop finishes instead of detaching (default false)."},
        }, ("task", "until")),
        "argv": _once,
    },
    {
        "name": "lute_plan",
        "description": "Decompose a larger goal into a lute.yaml of nested, independently-checkable milestones "
        "(draft written to lute.proposed.yaml). Use for a multi-step job that needs sequencing/parallelism "
        "before an unattended run. Review the draft, then lute_run it.",
        "inputSchema": _obj({
            "goal": {"type": "string", "description": "the goal to compile into loops."},
            "agent": _AGENT,
            "dag": {"type": "boolean", "description": "reason from a dependency graph first (still emits native YAML)."},
            "keep_dag": {"type": "boolean", "description": "with dag, also write lute.plan.yaml for review."},
        }, ("goal",)),
        "argv": _plan,
    },
    {
        "name": "lute_run",
        "description": "Run an existing lute.yaml until every check is green, committing each attempt. Detaches "
        "by default (returns fast; poll lute_watch); pass wait=true for a short job. Use dry_run to preview the "
        "plan and first prompt without spending anything.",
        "inputSchema": _obj({
            "file": _FILE,
            "agent": _AGENT,
            "root_id": {"type": "string", "description": "run a specific root loop id."},
            "wait": {"type": "boolean", "description": "run in the foreground and return the result (default false = detach)."},
            "dry_run": {"type": "boolean", "description": "print the resolved plan + first prompt, spend nothing."},
            "skip_if_running": {"type": "boolean", "description": "exit without work if another run holds this repo's lock."},
        }),
        "argv": _run,
    },
    {
        "name": "lute_lint",
        "description": "Validate a manifest and (unless no_exec) execute every done_when once, classifying each "
        "pass/fail/error/not-yet/skipped. Run this before lute_run to confirm the exams are administrable and "
        "non-circular. An error fails the lint.",
        "inputSchema": _obj({"file": _FILE, "agent": _AGENT, "no_exec": {"type": "boolean", "description": "schema + resolvability only; execute nothing."}}),
        "argv": _lint,
    },
    {
        "name": "lute_status",
        "description": "Live status: re-run each check once (except loops with an unanswered card) and print the "
        "loop tree with a mark per loop and cumulative agent time. Use to see where an attended run stands.",
        "inputSchema": _obj({"file": _FILE}),
        "argv": lambda a: ["status"] + ([str(a["file"])] if a.get("file") else []),
    },
    {
        "name": "lute_watch",
        "description": "Read-only snapshot of a running or finished run, replayed from the event file (no checks "
        "re-run). Returns machine-readable JSON by default (outcome/exit/tree/cards) — poll this after a detached "
        "lute_run/lute_once. Set json=false for the human tree, or pass filter=<logpath> to de-noise a transcript.",
        "inputSchema": _obj({
            "file": _FILE,
            "json": {"type": "boolean", "description": "machine-readable JSON snapshot (default true)."},
            "filter": {"type": "string", "description": "a .lute/logs/<loop>.run<N>.log path to print with repeated blocks collapsed."},
        }),
        "argv": _watch,
    },
    {
        "name": "lute_inbox",
        "description": "List every blocked or gated loop waiting on a human, with the exact lute_answer to send. "
        "Check this when a detached run stops making progress.",
        "inputSchema": _obj({}),
        "argv": lambda a: ["inbox"],
    },
    {
        "name": "lute_answer",
        "description": "Reply to a blocked or gated loop's INBOX card. For a blocked loop the text is injected into "
        "the next run and refreshes its budget once; for a gated loop only the exact text 'approve' seals it.",
        "inputSchema": _obj({
            "loop": {"type": "string", "description": "the loop id (from lute_inbox)."},
            "text": {"type": "string", "description": "guidance for a blocked loop, or 'approve' to seal a gate."},
        }, ("loop", "text")),
        "argv": lambda a: ["answer", a["loop"], a["text"]],
    },
    {
        "name": "lute_land",
        "description": "Merge lute/<root> into the start branch ONLY if the root exam still passes against the "
        "merged tree; a conflict or failed re-check aborts clean and escalates. Opt-in; the default is review-then-"
        "merge-yourself.",
        "inputSchema": _obj({"branch": {"type": "string", "description": "branch to merge into (default the run's origin)."}, "file": _FILE}),
        "argv": lambda a: ["land"] + ([str(a["branch"])] if a.get("branch") else []) + (["--file", str(a["file"])] if a.get("file") else []),
    },
    {
        "name": "lute_judge",
        "description": "Grade HEAD's diff once with the configured LLM judge (the oracle behind done_when: 'judge: ...'). "
        "Exits 0 only when the grader's first line is exactly PASS. Use only where a shell check can't capture the goal.",
        "inputSchema": _obj({"rubric": {"type": "string", "description": "the itemized, citation-demanding rubric."}}, ("rubric",)),
        "argv": lambda a: ["judge", "--", a["rubric"]],
    },
    {
        "name": "lute_quarantine",
        "description": "Inspect or remove edits lute quarantined because they touched protected exam materials. "
        "action: list (default), diff (needs id), drop (needs id), drop_all.",
        "inputSchema": _obj({
            "action": {"type": "string", "enum": ["list", "diff", "drop", "drop_all"], "description": "default list."},
            "id": {"type": "string", "description": "quarantine id for diff/drop."},
        }),
        "argv": _quarantine,
    },
    {
        "name": "lute_attest",
        "description": "Emit (or with verify, check) a signed-shape statement binding a CLOSED run's verdict to git "
        "facts: the pinned exam's hash, base+head SHAs, run count, and an event hash chain. Use to hand a reviewer "
        "or CI proof that this exam passed at this commit.",
        "inputSchema": _obj({"verify": {"type": "string", "description": "a statement file to verify instead of emit."}, "file": _FILE}),
        "argv": _attest,
    },
    {
        "name": "lute_stop",
        "description": "Cleanly stop the active run (and any parallel children) in this repo. A half-done iteration "
        "is dropped on the next run; state is git-derived.",
        "inputSchema": _obj({}),
        "argv": lambda a: ["stop"],
    },
    {
        "name": "lute_init",
        "description": "Scaffold a starter lute.yaml and .lute/ in the repo (or set skill=true to write a local copy "
        "of the packaged authoring skill). Use before hand-writing a manifest.",
        "inputSchema": _obj({"skill": {"type": "boolean", "description": "write a local luteloops/SKILL.md instead of scaffolding."}}),
        "argv": lambda a: ["init"] + (["--skill"] if a.get("skill") else []),
    },
    {
        "name": "lute_cron",
        "description": "Compile the manifest's schedules: block into a managed crontab block (sync) or remove it "
        "(remove). Each tick is a fresh lute run --skip-if-running.",
        "inputSchema": _obj({"action": {"type": "string", "enum": ["sync", "remove"], "description": "sync or remove."}}, ("action",)),
        "argv": lambda a: ["cron", a["action"]],
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS}


def call_tool(name: str, arguments: dict) -> dict:
    tool = _BY_NAME.get(name)
    if not tool:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}
    arguments = dict(arguments or {})
    cwd = arguments.pop("repo", None)
    try:
        verb_argv = tool["argv"](arguments)
    except (KeyError, ValueError, TypeError) as exc:
        return {"content": [{"type": "text", "text": f"bad arguments for {name}: {exc}"}], "isError": True}
    try:
        result = subprocess.run(self_argv() + verb_argv, cwd=cwd, capture_output=True, text=True)
    except OSError as exc:
        return {"content": [{"type": "text", "text": f"could not run lute: {exc}"}], "isError": True}
    body = ((result.stdout or "") + (result.stderr or "")).strip() or f"(lute {' '.join(verb_argv)} exited {result.returncode})"
    return {
        "content": [{"type": "text", "text": body}],
        "isError": result.returncode != 0,
        "structuredContent": {"exit_code": result.returncode},
    }


def _ok(mid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return _ok(mid, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "lute", "version": __version__},
            "instructions": SERVER_INSTRUCTIONS,
        })
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": [{k: tool[k] for k in ("name", "description", "inputSchema")} for tool in TOOLS]})
    if method == "tools/call":
        return _ok(mid, call_tool(params.get("name"), params.get("arguments") or {}))
    if mid is None:  # a notification we don't act on (initialized, cancelled, …)
        return None
    return _err(mid, -32601, f"method not found: {method}")


def serve() -> int:
    """Read newline-delimited JSON-RPC from stdin, reply on stdout, until EOF."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0
