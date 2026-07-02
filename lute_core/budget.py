"""Budget decisions, asked fresh every iteration.

`BudgetService.spent` recomputes a loop's spend from the ledger entries plus
the committed run-commit count — the floor an agent cannot edit away — and
the authenticated answers that each forgive one cap; the accounting itself
lives in `ledger.budget_spent`. `runs_cap`/`secs_cap` read the parsed limits
off the `LoopSpec`.
"""

from __future__ import annotations

from .domain import LoopSpec
from .git_repo import GitRepo
from .ledger import AnswerAuth, budget_spent


class BudgetService:
    def __init__(self, git: GitRepo, ledger_entries, answer_auth: AnswerAuth):
        self.git = git
        self.ledger_entries = ledger_entries
        self.answer_auth = answer_auth

    def runs_cap(self, loop: LoopSpec) -> int | None:
        return next((limit.amount for limit in loop.budget.limits if limit.kind == "runs"), None)

    def secs_cap(self, loop: LoopSpec) -> int | None:
        return next((limit.amount for limit in loop.budget.limits if limit.kind == "secs"), None)

    def spent(self, loop: LoopSpec, waited: float = 0.0) -> bool:
        entries = self.ledger_entries()
        lid = str(loop.id)
        return budget_spent(
            lid,
            loop.budget.as_pairs(),
            entries,
            self.answer_auth,
            self.git.run_commit_count(lid),
            waited,
        )
