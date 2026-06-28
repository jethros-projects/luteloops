import json
import os
import subprocess
import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from unittest import mock

from lute_core import cards, cli, cli_args, events, ledger, planner, processes, protection, schema
from lute_core.cage import CageTemplate, expand_cage_template
from lute_core.context import AppContext, Paths
from lute_core.domain import LoopSpec
from lute_core.git_repo import GitRepo
from lute_core.state_store import StateStore


def auth_for(loop, nonce):
    return f"auth:{loop}:{nonce}"


class SchemaTests(unittest.TestCase):
    def test_normalizes_loop_and_reports_unknown_key(self):
        errors = []
        raw = {
            "loop": "root",
            "tsk": "typo",
            "done_when": "true",
            "budget": "2 runs / 3s",
            "loops": [{"loop": "child", "done_when": "false"}],
        }
        loop = schema.norm_loop(raw, errors, set())
        self.assertEqual(loop["id"], "root")
        self.assertEqual(loop["budget"], [("runs", 2), ("secs", 3)])
        self.assertEqual(loop["children"][0]["id"], "child")
        self.assertTrue(any("did you mean tsk -> task" in e for e in errors))
        spec = LoopSpec.from_legacy_dict(loop)
        self.assertEqual(str(spec.id), "root")

    def test_load_returns_loop_specs_with_typed_children(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "lute.yaml")
            with open(path, "w") as f:
                f.write(
                    "loop: root\n"
                    "done_when: \"true\"\n"
                    "budget: 2 runs\n"
                    "loops:\n"
                    "  - loop: child\n"
                    "    done_when: \"false\"\n"
                )
            loop, schedules, errors = schema.load(path)
        self.assertEqual(errors, [])
        self.assertEqual(schedules, [])
        self.assertIsInstance(loop, LoopSpec)
        self.assertIsInstance(loop.children[0], LoopSpec)
        self.assertEqual(str(loop.children[0].id), "child")


class LedgerTests(unittest.TestCase):
    def test_budget_refresh_uses_only_authenticated_non_replayed_answers(self):
        entries = [
            {"loop": "a", "run": 1, "duration": 0.6},
            {"loop": "a", "event": "answer"},
            {"loop": "a", "run": 2, "duration": 0.6},
        ]
        self.assertEqual(ledger.runs_since_authenticated_answer(entries, "a", auth_for), (2, 1.2))
        self.assertTrue(ledger.budget_spent("a", [("secs", 1)], entries, auth_for, git_runs=2))

        valid = {"loop": "a", "event": "answer", "n": "n1", "auth": auth_for("a", "n1")}
        replay = dict(valid)
        entries = [
            {"loop": "a", "run": 1, "duration": 0.6},
            valid,
            {"loop": "a", "run": 2, "duration": 0.6},
            replay,
            {"loop": "a", "run": 3, "duration": 0.6},
        ]
        self.assertEqual(ledger.runs_since_authenticated_answer(entries, "a", auth_for), (2, 1.2))
        self.assertTrue(ledger.budget_spent("a", [("runs", 1)], entries, auth_for, git_runs=3))

    def test_restore_if_changed_replaces_symlink_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            state = os.path.join(td, ".lute")
            os.makedirs(state)
            target = os.path.join(td, "target")
            Path(target).write_text("sentinel\n")
            path = os.path.join(state, "ledger.jsonl")
            os.symlink(target, path)
            trusted = ledger.LedgerSnapshot(b'{"loop":"a","run":1,"duration":1}\n', [{"loop": "a", "run": 1, "duration": 1}])

            self.assertTrue(ledger.restore_if_changed(state, path, trusted))

            self.assertFalse(os.path.islink(path))
            self.assertEqual(Path(path).read_text(), trusted.raw.decode())
            self.assertEqual(Path(target).read_text(), "sentinel\n")

    def test_append_entry_replaces_symlink_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            state = os.path.join(td, ".lute")
            os.makedirs(state)
            target = os.path.join(td, "target")
            Path(target).write_text("sentinel\n")
            path = os.path.join(state, "ledger.jsonl")
            os.symlink(target, path)

            ledger.append_entry(state, path, {"loop": "a", "run": 1, "duration": 1})

            self.assertFalse(os.path.islink(path))
            self.assertIn('"run": 1', Path(path).read_text())
            self.assertEqual(Path(target).read_text(), "sentinel\n")

    def test_negative_duration_does_not_reduce_time(self):
        entries = [
            {"loop": "a", "run": 1, "duration": 0.7},
            {"loop": "a", "run": 2, "duration": -100},
            {"loop": "a", "run": 3, "duration": 0.4},
        ]
        self.assertTrue(ledger.budget_spent("a", [("secs", 1)], entries, auth_for, git_runs=3))


class ProcessTests(unittest.TestCase):
    def test_proc_cwd_without_proc_or_lsof_is_unknown_and_stop_is_conservative(self):
        with mock.patch.object(processes.os.path, "exists", return_value=False), \
             mock.patch.object(processes.shutil, "which", return_value=None), \
             mock.patch.object(processes.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(processes.proc_cwd(12345))

        with mock.patch.object(processes, "proc_cwd", return_value=None):
            self.assertFalse(processes.serves_repo(12345, "/tmp/repo"))


class CardAndEventTests(unittest.TestCase):
    def test_card_summary_and_event_replay_ignore_bad_lines(self):
        ready = cards.summarize_card("ship", "READY: ok\nANSWER: yes\n")
        self.assertEqual(ready.kind, "ready")
        self.assertTrue(ready.answered)
        self.assertEqual(ready.next_command, "lute answer ship approve")

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "events.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps({"ts": "1", "ev": "run_start", "loop": "r", "branch": "lute/r"}) + "\n")
                f.write("{truncated\n")
                f.write(json.dumps({"ts": "2", "ev": "loop_closed", "loop": "r"}) + "\n")
                f.write(json.dumps({"ts": "3", "ev": "run_end", "loop": "r"}) + "\n")
            state = events.replay_events(path)
        self.assertTrue(state["ended"])
        self.assertEqual(state["loops"]["r"]["mark"], "✔")


class CageTests(unittest.TestCase):
    def test_template_quotes_shell_sensitive_values_and_preserves_unknown_braces(self):
        with tempfile.TemporaryDirectory(prefix="repo with spaces ; touch BAD ") as repo:
            cmd = expand_cage_template(
                "printf 'R=%s\nI=%s\nK={keep}\n' {repo} {image} > out.txt; sh -lc {cmd}",
                repo,
                "image;touch BAD",
                [],
                "printf done > done.txt",
            )
            subprocess.run(["sh", "-c", cmd], cwd=repo, check=True)
            self.assertTrue(os.path.exists(os.path.join(repo, "done.txt")))
            self.assertFalse(os.path.exists(os.path.join(repo, "BAD")))
            text = Path(repo, "out.txt").read_text()
            self.assertIn(f"R={repo}", text)
            self.assertIn("I=image;touch BAD", text)
            self.assertIn("K={keep}", text)

        with self.assertRaisesRegex(ValueError, "must contain"):
            CageTemplate("echo nope").expand("/repo", "img", [], "true")


class CliAndProtectionTests(unittest.TestCase):
    def test_cli_arity_and_protected_globs(self):
        pos, opts = cli_args.parse_args(["--file", "lute.yaml", "root"], {"--file"})
        self.assertEqual(pos, ["root"])
        self.assertEqual(opts["file"], "lute.yaml")
        with self.assertRaises(cli_args.UsageError):
            cli_args.parse_args(["--file"], {"--file"})
        with self.assertRaises(cli_args.UsageError):
            cli_args.require_positionals(["a", "b"], "usage", 0, 1)

        with tempfile.TemporaryDirectory() as td:
            old = os.getcwd()
            try:
                os.chdir(td)
                os.makedirs("tests/.hidden")
                Path("top.sh").write_text("x")
                Path("tests/.hidden/exam.sh").write_text("x")
                os.makedirs(".lute")
                Path(".lute/ignore.sh").write_text("x")
                self.assertEqual(protection.protected_files(["*.sh"]), ["top.sh"])
                self.assertIn("tests/.hidden/exam.sh", protection.protected_files(["tests/**"]))
            finally:
                os.chdir(old)

    def test_quarantine_list_diff_and_drop(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=td, check=True)
            old = os.getcwd()
            try:
                os.chdir(td)
                paths = Paths.for_repo(td)
                os.makedirs(paths.quarantine)
                q1 = Path(paths.quarantine, "q0001")
                q1.mkdir()
                Path(q1, "changes.patch").write_text(
                    "diff --git a/tests/exam.sh b/tests/exam.sh\n"
                    "--- a/tests/exam.sh\n"
                    "+++ b/tests/exam.sh\n"
                    "@@ -1 +1 @@\n"
                    "-exit 1\n"
                    "+exit 0\n"
                )
                Path(q1, "meta.json").write_text(json.dumps({
                    "id": "q0001",
                    "loop": "cheater",
                    "run": "run2",
                    "paths": ["tests/exam.sh"],
                    "patch": "changes.patch",
                }))

                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cli.cmd_quarantine([]), 0)
                self.assertIn("q0001", out.getvalue())
                self.assertIn("tests/exam.sh", out.getvalue())

                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cli.cmd_quarantine(["diff", "q0001"]), 0)
                self.assertIn("+exit 0", out.getvalue())

                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cli.cmd_quarantine(["drop", "q0001"]), 0)
                self.assertFalse(q1.exists())

                for qid in ("q0002", "q0003"):
                    qdir = Path(paths.quarantine, qid)
                    qdir.mkdir()
                    Path(qdir, "meta.json").write_text(json.dumps({"id": qid, "patch": "changes.patch"}))
                    Path(qdir, "changes.patch").write_text("patch\n")
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cli.cmd_quarantine(["drop", "--all"]), 0)
                self.assertEqual(cli.quarantine_records(paths), [])
            finally:
                os.chdir(old)


class PlannerPromptTests(unittest.TestCase):
    def test_repo_briefing_collects_bounded_planning_facts(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=td, check=True)
            old = os.getcwd()
            try:
                os.chdir(td)
                Path("pyproject.toml").write_text('[project]\nname = "demo"\nrequires-python = ">=3.10"\n')
                Path("package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))
                Path("test.sh").write_text("#!/bin/sh\npytest -q\n")
                Path("AGENTS.md").write_text("Protect tests when they define the exam.\n")
                Path("src").mkdir()
                Path("src/report_export.py").write_text("# implementation lives here\n")
                Path("tests").mkdir()
                Path("tests/test_report_export.py").write_text("# tests live here\n")

                briefing = planner.repo_briefing("add report export", GitRepo(td))
            finally:
                os.chdir(old)

        self.assertIn("# Repository Briefing", briefing)
        self.assertIn("pyproject name: \"demo\"", briefing)
        self.assertIn("npm run test: vitest run", briefing)
        self.assertIn("./test.sh", briefing)
        self.assertIn("tests/test_report_export.py", briefing)
        self.assertIn("src/report_export.py", briefing)
        self.assertIn("Protect tests when they define the exam.", briefing)

    def test_build_plan_task_wraps_goal_context_and_guardrails(self):
        task = planner.build_plan_task(
            "ship the feature",
            "luteloops/SKILL.md",
            "Skill body",
            "Repo facts",
            "\nDAG planning mode:\n- no depends_on\n",
        )

        self.assertIn("<goal>\nship the feature\n</goal>", task)
        self.assertIn("<repository_briefing>\nRepo facts\n</repository_briefing>", task)
        self.assertIn("<luteloops_skill source=\"luteloops/SKILL.md\">", task)
        self.assertIn("Do not change product code while planning", task)
        self.assertIn("scouts inspect independent repo areas", task)
        self.assertIn("workers draft bounded milestone/check proposals", task)
        self.assertIn("Dispatch scouts for separate discovery questions", task)
        self.assertIn("Dispatch workers only for independent planning slices", task)
        self.assertIn("derive the implementation topology", task)
        self.assertIn("functional milestones, not activities", task)
        self.assertIn("loops that do not trace to functional necessity", task)
        self.assertIn('done_when: "true"', task)
        self.assertIn("DAG planning mode", task)


class ContextTests(unittest.TestCase):
    def test_paths_distinguish_repo_and_shared_state(self):
        paths = Paths.for_repo("/repo/worktree", "/repo/main/.lute")
        self.assertEqual(paths.state, "/repo/main/.lute")
        self.assertEqual(paths.inbox, "/repo/main/INBOX")
        self.assertEqual(paths.worktrees, "/repo/main/.lute/wt")

    def test_state_store_recreates_deleted_logs(self):
        with tempfile.TemporaryDirectory() as td:
            paths = Paths.for_repo(td)
            store = StateStore(paths)
            store.ensure_layout()
            os.rmdir(paths.logs)

            store.ensure_layout()

            self.assertTrue(os.path.isdir(paths.logs))
            self.assertFalse(os.path.islink(paths.logs))

    def test_state_store_replaces_symlinked_logs(self):
        with tempfile.TemporaryDirectory() as td:
            paths = Paths.for_repo(td)
            store = StateStore(paths)
            store.ensure_layout()
            os.rmdir(paths.logs)
            target = os.path.join(td, "elsewhere")
            os.mkdir(target)
            os.symlink(target, paths.logs)

            store.ensure_layout()

            self.assertTrue(os.path.isdir(paths.logs))
            self.assertFalse(os.path.islink(paths.logs))

    def test_state_store_replaces_symlinked_state_dir(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "outside")
            os.mkdir(target)
            os.symlink(target, os.path.join(td, ".lute"))

            paths = Paths.for_repo(td)
            StateStore(paths).ensure_layout()

            self.assertTrue(os.path.isdir(paths.state))
            self.assertFalse(os.path.islink(paths.state))

    def test_state_store_safe_write_does_not_follow_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            paths = Paths.for_repo(td)
            store = StateStore(paths)
            store.ensure_layout()
            target = os.path.join(td, "target")
            Path(target).write_text("sentinel")
            path = os.path.join(paths.state, "ledger.jsonl")
            os.symlink(target, path)

            store.safe_write_regular(path, b"trusted\n")

            self.assertFalse(os.path.islink(path))
            self.assertEqual(Path(path).read_text(), "trusted\n")
            self.assertEqual(Path(target).read_text(), "sentinel")

    def test_app_context_carries_runtime_fields(self):
        paths = Paths.for_repo("/repo")
        ctx = AppContext("/repo", paths, {"agent": "true"}, "/repo/lute.yaml", "root", mode="file")
        self.assertEqual(ctx.repo_root, "/repo")
        self.assertEqual(ctx.manifest_path, "/repo/lute.yaml")
        self.assertEqual(ctx.root_id, "root")
        self.assertEqual(ctx.active_config()["agent"], "true")


if __name__ == "__main__":
    unittest.main()
