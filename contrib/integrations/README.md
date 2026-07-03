# Integrations — reach lute from Claude Code and Codex

lute exposes every verb as an MCP tool (`lute mcp`, a stdlib stdio server), so
the agents devs already live in can hand a bounded, machine-checkable, walk-away
task to lute instead of grinding it inline.

- **[claude/skills/lute/](claude/skills/lute/)** — a Claude Code skill: the
  when-to-hand-off judgement (a conservative trigger) plus how. Copy into
  `.claude/skills/` or `~/.claude/skills/`.
- **[codex/AGENTS.md](codex/AGENTS.md)** — the same guidance to paste into your
  repo's `AGENTS.md` for Codex.
- **Registration + the full tool list:** [`docs/INTEGRATIONS.md`](../../docs/INTEGRATIONS.md).

The server itself is `lute mcp` — you register it once with your agent and never
run it by hand.
