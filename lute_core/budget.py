"""Budget parsing and spending decisions."""

from __future__ import annotations

from dataclasses import dataclass

from .domain import LoopSpec
from .git_repo import GitRepo
from .ledger import AnswerAuth, budget_spent, runs_since_authenticated_answer


@dataclass(frozen=True)
class BudgetDecision:
    spent: bool
    runs: int
    seconds: float


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
        return self.decision(loop, waited).spent

    def decision(self, loop: LoopSpec, waited: float = 0.0) -> BudgetDecision:
        entries = self.ledger_entries()
        lid = str(loop.id)
        runs, seconds = runs_since_authenticated_answer(entries, lid, self.answer_auth)
        spent = budget_spent(
            lid,
            loop.budget.as_pairs(),
            entries,
            self.answer_auth,
            self.git.run_commit_count(lid),
            waited,
        )
        return BudgetDecision(spent, runs, seconds)
