"""Small domain objects for lute's internal vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_YET = "not_yet"
    ERROR = "error"


class Gate(str, Enum):
    HUMAN = "human"


class RunMode(str, Enum):
    FILE = "file"
    FILELESS = "fileless"
    CHILD = "child"


class ExitCode(int, Enum):
    SUCCESS = 0
    USAGE = 1
    INTERNAL = 2
    BLOCKED = 3
    GATED = 4


@dataclass(frozen=True)
class LoopId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class BudgetLimit:
    kind: str
    amount: int


@dataclass(frozen=True)
class Budget:
    limits: tuple[BudgetLimit, ...]

    @classmethod
    def from_pairs(cls, pairs: list[tuple[str, int]]) -> "Budget":
        return cls(tuple(BudgetLimit(kind, amount) for kind, amount in pairs))

    def as_pairs(self) -> list[tuple[str, int]]:
        return [(limit.kind, limit.amount) for limit in self.limits]


@dataclass(frozen=True)
class CheckSpec:
    command: str

    def __str__(self) -> str:
        return self.command


@dataclass(frozen=True)
class CheckResult:
    verdict: Verdict
    output: str = ""


@dataclass(frozen=True)
class LoopSpec:
    id: LoopId
    task: str | None
    agent: str | None
    done_when: CheckSpec
    budget: Budget
    confirm: int = 1
    every: int = 60
    every_str: str = "60s"
    gate: Gate | None = None
    protected: tuple[str, ...] = ()
    parallel: bool = False
    children: tuple["LoopSpec", ...] = ()

    @classmethod
    def from_legacy_dict(cls, raw: dict[str, Any]) -> "LoopSpec":
        gate = Gate.HUMAN if raw.get("gate") == Gate.HUMAN.value else None
        return cls(
            id=LoopId(raw["id"]),
            task=raw.get("task"),
            agent=raw.get("agent"),
            done_when=CheckSpec(raw["done_when"]),
            budget=Budget.from_pairs(raw.get("budget", [])),
            confirm=raw.get("confirm", 1),
            every=raw.get("every", 60),
            every_str=raw.get("every_str", "60s"),
            gate=gate,
            protected=tuple(raw.get("protected", ())),
            parallel=bool(raw.get("parallel", False)),
            children=tuple(cls.from_legacy_dict(c) for c in raw.get("children", ())),
        )

    def find(self, loop_id: str) -> "LoopSpec | None":
        if str(self.id) == loop_id:
            return self
        for child in self.children:
            found = child.find(loop_id)
            if found:
                return found
        return None

    def flatten(self, depth: int = 0) -> list[tuple[int, "LoopSpec"]]:
        rows: list[tuple[int, LoopSpec]] = []
        for child in self.children:
            rows.extend(child.flatten(depth + 1))
        rows.append((depth, self))
        return rows


@dataclass(frozen=True)
class Card:
    loop: str
    kind: str
    answered: bool
    summary: str
    next_command: str
