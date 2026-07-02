"""Runtime paths and the per-run application context.

`Paths.for_repo` names every runner-owned location once: state under
`.lute/`, cards in `INBOX/`, and the ledger deliberately pinned to the repo's
own tree so it is committed with the branch's work. `AppContext` is the bag
of run-scoped facts threaded through the runner — manifest, mode, frozen
config, trusted base, quarantine bookkeeping — and `active_config` prefers
the frozen copy, so a mid-run config edit changes nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .domain import RunMode

@dataclass(frozen=True)
class Paths:
    state: str
    ledger: str
    journal: str
    config: str
    events: str
    logs: str
    runner_log: str
    inbox: str
    lock: str
    worktrees: str
    quarantine: str

    @classmethod
    def for_repo(cls, repo: str, state_dir: str | None = None) -> "Paths":
        state = os.path.abspath(state_dir or os.path.join(repo, ".lute"))
        logs = os.path.join(state, "logs")
        return cls(
            state=state,
            # The ledger is accounting FOR a branch's work, so it lives in that
            # branch's tree and is committed with it: each parallel child
            # worktree writes its own copy, merged like any other work (a
            # `merge=union` attribute keeps sibling appends from conflicting).
            # Everything else is shared session state.
            ledger=os.path.join(os.path.abspath(repo), ".lute", "ledger.jsonl"),
            journal=os.path.join(state, "journal"),
            config=os.path.join(state, "config.yaml"),
            events=os.path.join(state, "events.jsonl"),
            logs=logs,
            runner_log=os.path.join(logs, "runner.log"),
            inbox=os.path.join(os.path.dirname(state), "INBOX"),
            lock=os.path.join(state, "lock"),
            worktrees=os.path.join(state, "wt"),
            quarantine=os.path.join(state, "quarantine"),
        )


@dataclass
class AppContext:
    repo_root: str
    paths: Paths
    config: dict[str, Any]
    manifest_path: str
    root_id: str
    mode: RunMode
    plain: bool = False
    run_pre_untracked: set[str] = field(default_factory=set)
    frozen_config: dict[str, Any] | None = None
    nagged: dict[str, bool] = field(default_factory=dict)
    trusted_base: str = ""
    quarantined_paths: set[str] = field(default_factory=set)

    @property
    def shared_root(self) -> str:
        return os.path.dirname(self.paths.state)

    def active_config(self) -> dict[str, Any]:
        return self.frozen_config if self.frozen_config is not None else self.config
