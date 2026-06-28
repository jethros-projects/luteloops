"""Prompt construction for `lute plan`."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable

from .git_repo import GitRepo

SKIP_DIRS = {
    ".git",
    ".hg",
    ".lute",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "INBOX",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
TEXT_LIMIT = 10000
LIST_LIMIT = 80
STATUS_LIMIT = 40
STOPWORDS = {
    "about",
    "after",
    "against",
    "and",
    "into",
    "make",
    "the",
    "this",
    "that",
    "then",
    "with",
    "without",
    "from",
    "for",
    "you",
    "your",
}


def _repo_files(root: str) -> list[str]:
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in sorted(dirnames) if d not in SKIP_DIRS and not d.startswith(".lute")]
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            rel = os.path.join(rel_dir, name) if rel_dir else name
            paths.append(rel.replace(os.sep, "/"))
    return paths


def _existing(paths: Iterable[str]) -> list[str]:
    return [path for path in paths if os.path.exists(path)]


def _read_text(path: str, limit: int = TEXT_LIMIT) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(limit + 1)
    except (OSError, UnicodeDecodeError):
        return ""
    if len(text) > limit:
        return text[:limit] + "\n... [truncated]\n"
    return text


def _format_list(items: Iterable[str], limit: int = LIST_LIMIT) -> str:
    values = list(items)
    if not values:
        return "- none detected"
    shown = values[:limit]
    lines = [f"- {item}" for item in shown]
    if len(values) > limit:
        lines.append(f"- ... {len(values) - limit} more omitted")
    return "\n".join(lines)


def _package_scripts() -> list[str]:
    if not os.path.exists("package.json"):
        return []
    try:
        data = json.loads(_read_text("package.json", 200000))
    except json.JSONDecodeError:
        return ["package.json exists but could not be parsed"]
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return ["package.json has no scripts object"]
    return [f"npm run {name}: {cmd}" for name, cmd in sorted(scripts.items()) if isinstance(cmd, str)]


def _pyproject_facts() -> list[str]:
    if not os.path.exists("pyproject.toml"):
        return []
    text = _read_text("pyproject.toml")
    facts = ["pyproject.toml exists"]
    for key in ("name", "requires-python"):
        match = re.search(rf"(?m)^{re.escape(key)}\s*=\s*([^\n]+)", text)
        if match:
            facts.append(f"pyproject {key}: {match.group(1).strip()}")
    for section in ("[project.scripts]", "[tool.pytest.ini_options]", "[tool.ruff]", "[tool.mypy]"):
        if section in text:
            facts.append(f"pyproject section: {section}")
    return facts


def _make_targets() -> list[str]:
    if not os.path.exists("Makefile"):
        return []
    targets: list[str] = []
    for line in _read_text("Makefile").splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*):(?:\s|$)", line)
        if match and not match.group(1).startswith("."):
            targets.append(f"make {match.group(1)}")
    return sorted(set(targets))


def _ci_commands(files: list[str]) -> list[str]:
    ci_files = [
        path
        for path in files
        if path.startswith(".github/workflows/")
        or path in {".gitlab-ci.yml", ".circleci/config.yml", "azure-pipelines.yml"}
    ]
    commands: list[str] = []
    for path in ci_files[:10]:
        commands.append(f"CI file: {path}")
        for line in _read_text(path, 20000).splitlines():
            stripped = line.strip()
            if stripped.startswith("run:"):
                commands.append(f"{path}: {stripped}")
            elif stripped.startswith("- run:"):
                commands.append(f"{path}: {stripped}")
            if len(commands) >= 40:
                return commands
    return commands


def _test_and_check_files(files: list[str]) -> list[str]:
    names: list[str] = []
    for path in files:
        base = os.path.basename(path).lower()
        if (
            path.startswith("tests/")
            or path.startswith("test/")
            or path.startswith("spec/")
            or path.startswith("scripts/check")
            or path.startswith("scripts/test")
            or base.startswith("test")
            or "check" in base
            or base in {"pytest.ini", "tox.ini", "noxfile.py"}
        ):
            names.append(path)
    return names


def _goal_related_files(goal: str, files: list[str]) -> list[str]:
    terms = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", goal.lower())
        if token not in STOPWORDS
    }
    if not terms:
        return []
    matches = [path for path in files if any(term in path.lower() for term in terms)]
    return matches[:LIST_LIMIT]


def _top_level() -> list[str]:
    try:
        return sorted(name + ("/" if os.path.isdir(name) else "") for name in os.listdir(".") if name not in SKIP_DIRS)
    except OSError:
        return []


def _git_status(git: GitRepo) -> str:
    try:
        status = git.status_porcelain()
    except Exception as exc:  # pragma: no cover - defensive for unusual git states
        return f"- unavailable: {exc}"
    if not status.strip():
        return "- clean"
    lines = status.splitlines()
    shown = lines[:STATUS_LIMIT]
    out = [f"- {line}" for line in shown]
    if len(lines) > STATUS_LIMIT:
        out.append(f"- ... {len(lines) - STATUS_LIMIT} more omitted")
    return "\n".join(out)


def repo_briefing(goal: str, git: GitRepo) -> str:
    """Return a bounded planning context snapshot for an agent drafting a manifest."""
    root = git.root
    files = _repo_files(root)
    commands = (
        _package_scripts()
        + _pyproject_facts()
        + _make_targets()
        + (["./test.sh"] if os.path.exists("test.sh") else [])
        + _ci_commands(files)
    )
    test_files = _test_and_check_files(files)
    goal_files = _goal_related_files(goal, files)
    instructions = _read_text("AGENTS.md", 12000) if os.path.exists("AGENTS.md") else ""
    existing_manifests = _existing(("lute.yaml", "lute.proposed.yaml", "lute.plan.yaml", "Luteloops"))

    sections = [
        "# Repository Briefing",
        "This is a bounded snapshot generated before drafting `lute.proposed.yaml`. "
        "Treat it as orientation, not exhaustive truth: inspect files directly before relying on a fact.",
        "",
        "## Git Status",
        _git_status(git),
        "",
        "## Existing Lute Files",
        _format_list(existing_manifests),
        "",
        "## Build, Test, And CI Signals",
        _format_list(commands),
        "",
        "## Existing Tests And Check Materials",
        _format_list(test_files),
        "",
        "## Goal-Related Path Hints",
        _format_list(goal_files),
        "",
        "## Top-Level Entries",
        _format_list(_top_level()),
        "",
        "## Representative Repo Files",
        _format_list(files, 160),
    ]
    if instructions:
        sections.extend(
            [
                "",
                "## Root AGENTS.md",
                "```",
                instructions.rstrip(),
                "```",
            ]
        )
    return "\n".join(sections)


def build_plan_task(goal: str, skill_source: str, skill_body: str, briefing: str, dag_instructions: str) -> str:
    """Build the planner task that becomes the synthetic `plan` loop's goal."""
    return f"""You are drafting a Lute implementation contract, not implementing the requested feature.

<goal>
{goal}
</goal>

<critical_instructions>
1. Write `lute.proposed.yaml` for the goal. Do not overwrite `lute.yaml`.
2. Do not change product code while planning. Your durable output is the manifest draft; with `--keep-dag`, also write `lute.plan.yaml`.
3. Use the repository briefing below to choose real build/test/check commands and likely protected exam materials. If the briefing is insufficient, inspect the repo before writing.
4. If your environment supports subagents, use them deliberately before writing YAML: scouts inspect independent repo areas and report facts; workers draft bounded milestone/check proposals for disjoint implementation slices. You must integrate and reconcile their findings yourself.
5. First derive the implementation topology: target behavior, code surfaces, data/API contracts, dependency order, integration points, and existing or needed exams. Then compile that topology into loops.
6. Decompose by independently verifiable functional milestones, not activities. A loop is valid only when it corresponds to an implementation state that must become true; never create loops just because "research", "implement", or "test" are phases.
7. Prefer existing repo commands and tests. Do not invent unavailable commands, placeholder checks, `done_when: "true"`, or circular grep checks that merely detect text the worker can type.
8. If a `done_when` depends on tests, fixtures, scripts, Makefiles, package scripts, or other exam materials the worker could edit, add `protected:` globs for those materials.
9. Use only Lute-native YAML keys in `lute.proposed.yaml`: `loop`, `task`, `agent`, `done_when`, `budget`, `confirm`, `loops`, `check_every`, `gate`, `protected`, `parallel`, and top-level `schedules`.
10. Leave the repository in a state where `lute lint lute.proposed.yaml` succeeds. The runner will re-check this after every attempt and feed failures back to you.
</critical_instructions>

<authoring_workflow>
1. Read the goal and repository briefing.
2. Inspect any relevant files needed to avoid guessing: package/CI commands, existing tests/check scripts, target paths, and files that should be protected.
3. Dispatch scouts for separate discovery questions when useful: existing tests/check commands, likely target modules, dependency boundaries, and protected exam materials. Ask for concise cited findings.
4. Dispatch workers only for independent planning slices, not product edits: each worker may propose functional milestones, checks, budgets, and protected globs for its slice. Merge their proposals yourself; do not paste incompatible mini-plans together.
5. Internally sketch the implementation map: files/modules likely to change, behavioral contracts, sequencing constraints, integration risks, and the strongest available verification for each required state.
6. Convert only the required implementation states from that map into 3-7 independently checkable loops unless the goal is truly tiny. Fold non-verifiable investigation or coding advice into `task:` text instead of making it a loop.
7. Audit the draft for loops that do not trace to functional necessity, weak exams, missing budgets, DAG-only keys, unsafe parallelism, unprotected exam materials, and root-exam weakness.
8. Write `lute.proposed.yaml` only after that audit.
</authoring_workflow>

<repository_briefing>
{briefing}
</repository_briefing>

<luteloops_skill source="{skill_source}">
{skill_body.strip()}
</luteloops_skill>
{dag_instructions}

<final_reminder>
Before finishing, make the manifest lintable and self-contained. The next check is `lute lint lute.proposed.yaml`; if it fails, read the existing `lute.proposed.yaml`, repair the lint-reported issues while preserving valid structure, and rerun or mentally audit the same check.
</final_reminder>"""
