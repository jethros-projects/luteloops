# Use lute from Claude Code or Codex

Devs live in Claude Code and Codex. lute is not another interface — it is the
harness those agents can *hand a task to* when the task is a bounded,
machine-checkable, walk-away job (fix a failing suite, repair a dependency-bump
PR, a migration). This wires lute into both so the agent reaches for it at the
right moment, instead of grinding the task inline.

lute exposes **every verb as an MCP tool** through a stdio server built on the
Python standard library — no extra dependency. `lute mcp` is that server; you
register it once with your agent and never run it by hand.

## Claude Code

Register the server (project scope shares it via `.mcp.json`; drop `--scope project`
for just you):

```sh
claude mcp add lute --scope project -- lute mcp
```

or write `.mcp.json` in the repo root:

```json
{
  "mcpServers": {
    "lute": { "type": "stdio", "command": "lute", "args": ["mcp"] }
  }
}
```

Verify with `claude mcp list` (look for `lute ✓ Connected`) or `/mcp` in a
session. Claude now sees `lute_once`, `lute_plan`, `lute_run`, `lute_watch`, and
the rest as tools.

**Teach it *when* to hand off** by installing the skill — copy
`contrib/integrations/claude/skills/lute/` into `.claude/skills/` (project) or
`~/.claude/skills/` (global). The skill's trigger is deliberately conservative:
it fires only when there is a clear pass/fail command, the work is grindy and
bounded, and running to completion is fine — otherwise Claude keeps doing the
work itself.

If `lute` isn't on PATH (running from a checkout), use the module form:
`claude mcp add lute -- python3 -m lute_core.cli mcp` (from the checkout dir), or
install lute first.

## Codex

Register the server:

```sh
codex mcp add lute -- lute mcp
```

or add to `~/.codex/config.toml` (or a project `.codex/config.toml`):

```toml
[mcp_servers.lute]
command = "lute"
args = ["mcp"]
```

Verify with `codex mcp list`. **Teach it *when* to hand off** by pasting
`contrib/integrations/codex/AGENTS.md` into your repo's `AGENTS.md` — Codex reads
it and applies the same conservative trigger.

## The tools

| tool | verb | use |
|---|---|---|
| `lute_once` | `once` | hand off a single check-gated task (no manifest) |
| `lute_plan` | `plan` | decompose a goal into a nested `lute.proposed.yaml` |
| `lute_run` | `run` | run an existing manifest to green (detaches; poll `lute_watch`) |
| `lute_lint` | `lint` | validate + dry-run every exam before a run |
| `lute_status` / `lute_watch` | `status` / `watch` | where the run stands (live / replay) |
| `lute_inbox` / `lute_answer` | `inbox` / `answer` | a blocked or gated loop and how to unblock it |
| `lute_land` | `land` | merge only if the exam still passes on the merged tree |
| `lute_judge` | `judge` | grade HEAD's diff with the configured judge |
| `lute_quarantine` | `quarantine` | inspect edits lute set aside from protected exams |
| `lute_attest` | `attest` | a verifiable "exam passed at SHA" statement |
| `lute_stop` / `lute_init` / `lute_cron` | `stop` / `init` / `cron` | stop a run · scaffold · schedule |

Each tool call runs the real `lute` CLI in a fresh process — the agent operates
lute exactly as you would from the shell, with the same exit codes and
guarantees. Pass `repo` on any tool to target a repository other than the
server's working directory.

## How a handoff looks

1. The agent recognizes a walk-away, check-gated task and calls
   `lute_once` (or `lute_plan` → `lute_run`) with the check, an engine, and a
   budget. The run detaches.
2. It polls `lute_watch` (JSON `outcome`: running → closed / blocked / gated),
   or you follow along with `lute watch` in a terminal — the loop tree renders
   with a mark per loop.
3. On close, the work is a branch (`lute/<root>`) with one commit per attempt and
   an inspectable transcript. The agent reviews it and merges, or `lute_land`
   merges only if the exam re-passes.

The point: the agent hands you *verified* work with a trail, because lute's finish
line is a test the agent can't fake — not the agent's own say-so.
