import json
import os
import random
import subprocess
import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from unittest import mock

from lute_core import cards, cli, cli_args, events, formatting, ledger, planner, processes, protection, schema
from lute_core.cage import DEFAULT_CAGE_TEMPLATE, CageTemplate, expand_cage_template
from lute_core.checks import CheckRunner
from lute_core.config import freeze_config, load_config
from lute_core.context import AppContext, Paths
from lute_core.domain import LoopSpec, RunMode
from lute_core.errors import Gated, PreconditionError
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
        spec = LoopSpec.from_normalized(loop)
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


class ConfigTests(unittest.TestCase):
    def test_load_config_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td, "target.yaml")
            target.write_text('agent: "true"\n')
            link = Path(td, "config.yaml")
            link.symlink_to(target)

            with self.assertRaisesRegex(PreconditionError, "regular file"):
                load_config(str(link))

    def test_parallel_child_freezes_parent_state_config_from_trusted_base(self):
        with tempfile.TemporaryDirectory() as td:
            parent = os.path.join(td, "parent")
            child = os.path.join(td, "child")
            os.makedirs(parent)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=parent, check=True)
            os.makedirs(os.path.join(parent, ".lute"))
            Path(parent, ".lute", "config.yaml").write_text('agent: "true"\njudge: "printf FAIL"\n')
            Path(parent, "lute.yaml").write_text('loop: root\ndone_when: "false"\nbudget: 1 runs\n')
            subprocess.run(["git", "add", "-f", ".lute/config.yaml"], cwd=parent, check=True)
            subprocess.run(["git", "add", "lute.yaml"], cwd=parent, check=True)
            subprocess.run(
                ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "fixture"],
                cwd=parent,
                check=True,
            )
            trusted = subprocess.run(["git", "rev-parse", "HEAD"], cwd=parent, check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "worktree", "add", "-q", "-b", "child", child, trusted], cwd=parent, check=True)
            Path(parent, ".lute", "config.yaml").write_text('agent: "true"\njudge: "printf PASS"\n')

            paths = Paths.for_repo(child, os.path.join(parent, ".lute"))
            ctx = AppContext(child, paths, load_config(paths.config), os.path.join(child, "lute.yaml"), "root", RunMode.CHILD, True)
            ctx.trusted_base = trusted

            self.assertEqual(ctx.config["judge"], "printf PASS")
            self.assertEqual(freeze_config(ctx, GitRepo(child))["judge"], "printf FAIL")


class FormattingTests(unittest.TestCase):
    def test_human_duration_and_tail_helpers(self):
        self.assertEqual(formatting.human(0), "0s")
        self.assertEqual(formatting.human(7.9), "7s")
        self.assertEqual(formatting.human(65), "1m05s")
        self.assertEqual(formatting.tail("a\nb\nc", 2), "b\nc")
        self.assertEqual(formatting.tail("", 10), "")


class ProcessTests(unittest.TestCase):
    def test_proc_cwd_without_proc_or_lsof_is_unknown_and_stop_is_conservative(self):
        with mock.patch.object(processes.os.path, "exists", return_value=False), \
             mock.patch.object(processes.shutil, "which", return_value=None), \
             mock.patch.object(processes.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(processes.proc_cwd(12345))

        with mock.patch.object(processes, "proc_cwd", return_value=None):
            self.assertIsNone(processes.serves_repo(12345, "/tmp/repo"))

    def _seeded_repo_with_lock(self, td, pid):
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=td, check=True)
        Path(td, "seed").write_text("x")
        subprocess.run(["git", "add", "seed"], cwd=td, check=True)
        subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "seed"],
            cwd=td,
            check=True,
        )
        paths = Paths.for_repo(td)
        os.makedirs(paths.state)
        Path(paths.lock).write_text('{"pid": %d, "start": "x"}' % pid)
        return paths

    def test_stop_asks_the_runner_to_tear_itself_down(self):
        # Cooperative stop: confirm the runner from the lock, then ask IT to stop
        # (it reaps the children it owns). No pid files, no ancestry, no forensics.
        with tempfile.TemporaryDirectory() as td:
            self._seeded_repo_with_lock(td, 4242)
            old = os.getcwd()
            try:
                os.chdir(td)
                out = io.StringIO()
                with mock.patch.object(processes, "pid_alive", return_value=True), \
                     mock.patch.object(processes, "serves_repo", return_value=True), \
                     mock.patch.object(processes, "stop_run", return_value=True) as stop_run, \
                     contextlib.redirect_stdout(out):
                    rc = cli.cmd_stop([])
                self.assertEqual(rc, 0)
                stop_run.assert_called_once_with(4242)
                self.assertIn("stopped run pid 4242", out.getvalue())
            finally:
                os.chdir(old)

    def test_stop_preserves_lock_when_runner_cwd_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            self._seeded_repo_with_lock(td, 12345)
            old = os.getcwd()
            try:
                os.chdir(td)
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(processes, "pid_alive", return_value=True), \
                     mock.patch.object(processes, "serves_repo", return_value=None), \
                     mock.patch.object(processes, "stop_run", return_value=True) as stop_run, \
                     contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = cli.cmd_stop([])
                self.assertEqual(rc, 1)
                self.assertTrue(Path(Paths.for_repo(td).lock).exists())
                self.assertNotIn("stale lock", out.getvalue())
                self.assertIn("kill -INT", err.getvalue())
                stop_run.assert_not_called()  # never signal a run we cannot confirm is ours
            finally:
                os.chdir(old)


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

    def test_gate_halt_diffstat_uses_trusted_base(self):
        class FakeGit:
            def __init__(self):
                self.diff_args = None

            def branch_base(self):
                return "wrong-base"

            def text(self, *args):
                self.diff_args = args
                return " diffstat\n"

        class CaptureCards(cards.CardService):
            def __init__(self, ctx, git):
                self.ctx = ctx
                self.store = StateStore(ctx.paths)
                self.git = git
                self.captured = ""

            def raise_gate(self, lid, text=None, commit_msg=None):
                self.captured = text or ""
                raise Gated()

        with tempfile.TemporaryDirectory() as td:
            paths = Paths.for_repo(td)
            os.makedirs(paths.inbox)
            ctx = AppContext(td, paths, {}, os.path.join(td, "lute.yaml"), "gate", RunMode.FILE)
            ctx.trusted_base = "trusted-base"
            git = FakeGit()
            svc = CaptureCards(ctx, git)
            loop = LoopSpec.from_normalized({"id": "gate", "done_when": "true", "budget": []})

            with self.assertRaises(Gated):
                svc.gate_halt(loop)

            self.assertEqual(git.diff_args, ("diff", "--stat", "trusted-base...HEAD"))
            self.assertIn("diffstat", svc.captured)

    def test_answer_auth_binds_answer_body(self):
        self.assertNotEqual(cards.answer_basis("READY\n", "no"), cards.answer_basis("READY\n", "approve"))


class CheckRunnerTests(unittest.TestCase):
    def test_judge_checks_do_not_reenter_self_command(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=td, check=True)
            Path(td, "seed").write_text("x")
            subprocess.run(["git", "add", "seed"], cwd=td, check=True)
            subprocess.run(
                ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "seed"],
                cwd=td,
                check=True,
            )
            paths = Paths.for_repo(td)
            os.makedirs(paths.state)
            Path(paths.config).write_text('judge: "printf PASS"\n')
            proof = Path(td, "self-ran")
            ctx = AppContext(td, paths, load_config(paths.config), os.path.join(td, "lute.yaml"), "j", RunMode.FILE)
            runner = CheckRunner(ctx, GitRepo(td), lambda command, repo_root=None: command, lambda: f"sh -c 'touch {proof}; false'")
            loop = LoopSpec.from_normalized({"id": "j", "done_when": "judge: ok", "budget": []})

            result = runner.run(loop)

            self.assertEqual(result.verdict.value, "pass")
            self.assertFalse(proof.exists())


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

    def test_default_cage_template_leaves_egress_to_operator_policy(self):
        # Egress is ON by default: caged LLM agents and judges need network to
        # reach their model API. Sealing is an opt-in operator template. The
        # guard is structural so a future edit cannot silently re-brick the cage
        # with a different spelling (the round-1 regression).
        def disables_network(template):
            toks = template.replace("=", " ").split()
            return any(
                toks[i] in ("--network", "--net") and i + 1 < len(toks) and toks[i + 1] == "none"
                for i in range(len(toks))
            )

        self.assertFalse(disables_network(DEFAULT_CAGE_TEMPLATE))
        # the structural guard catches every spelling the old substring missed
        self.assertTrue(disables_network("docker run --net none img sh"))
        self.assertTrue(disables_network("docker run --net=none img sh"))
        self.assertTrue(disables_network("docker run --network=none img sh"))


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

    def test_quarantine_records_reject_symlinked_patch(self):
        with tempfile.TemporaryDirectory() as td:
            paths = Paths.for_repo(td)
            store = StateStore(paths)
            store.ensure_layout()
            qdir = Path(paths.quarantine, "qbad")
            qdir.mkdir()
            Path(qdir, "meta.json").write_text(json.dumps({"id": "qbad", "patch": "changes.patch"}))
            target = Path(td, "secret")
            target.write_text("secret")
            Path(qdir, "changes.patch").symlink_to(target)

            self.assertEqual(cli.quarantine_records(paths), [])


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

    def test_process_output_replaces_symlink_leaf(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "log")
            target = os.path.join(td, "target")
            Path(target).write_text("sentinel")
            os.symlink(target, path)

            with processes.open_output(path, append=False) as f:
                f.write("log\n")

            self.assertFalse(os.path.islink(path))
            self.assertEqual(Path(path).read_text(), "log\n")
            self.assertEqual(Path(target).read_text(), "sentinel")

    def test_app_context_carries_runtime_fields(self):
        paths = Paths.for_repo("/repo")
        ctx = AppContext("/repo", paths, {"agent": "true"}, "/repo/lute.yaml", "root", mode="file")
        self.assertEqual(ctx.repo_root, "/repo")
        self.assertEqual(ctx.manifest_path, "/repo/lute.yaml")
        self.assertEqual(ctx.root_id, "root")
        self.assertEqual(ctx.active_config()["agent"], "true")


class FuzzParserTests(unittest.TestCase):
    """The two parsers that turn untrusted text into trusted decisions are the
    invariant's soft underbelly: `schema.load` (the manifest an author writes)
    and the `ledger` accounting (the JSONL an adversarial agent can rewrite).
    Feed each malformed and random input and assert it never crashes and never
    misclassifies junk into a pass or a budget refund (see INVARIANT.md)."""

    SEED = 0xC0FFEE  # fixed so the fuzz is deterministic and reproducible

    def _scalars(self, rng):
        return [
            None, True, False, 0, 1, -1, rng.randint(-(10**9), 10**9),
            rng.random() * 1e6, -rng.random() * 1e6, float("inf"), float("nan"),
            "", "x" * rng.randint(0, 6), "10 runs", "run", "answer", "human",
            "60s", "\x00", "🙂", "-", "--", "/", "..", "true", [], {}, [1, 2],
            {"k": 1}, ("t",),
        ]

    def _rand_node(self, rng, depth=0):
        keys = ["loop", "task", "agent", "done_when", "budget", "confirm",
                "loops", "check_every", "gate", "protected", "parallel", "xyz"]
        node = {}
        for k in rng.sample(keys, rng.randint(0, len(keys))):
            node[k] = rng.choice(self._scalars(rng))
        if depth < 3 and rng.random() < 0.5:
            node["loops"] = [self._rand_node(rng, depth + 1)
                             for _ in range(rng.randint(0, 3))]
        return node

    def test_schema_norm_loop_never_crashes_on_garbage(self):
        rng = random.Random(self.SEED)
        for _ in range(600):
            errors = []
            node = rng.choice([self._rand_node(rng), rng.choice(self._scalars(rng))])
            loop = schema.norm_loop(node, errors, set())
            self.assertIsInstance(errors, list)
            if loop is not None:
                # a normalized loop is always fully typed and buildable
                self.assertIsInstance(loop["id"], str)
                self.assertIsInstance(loop["done_when"], str)
                self.assertIsInstance(loop["confirm"], int)
                self.assertIsInstance(loop["budget"], list)
                spec = LoopSpec.from_normalized(loop)  # must never raise
                self.assertIsInstance(str(spec.id), str)

    def test_schema_load_returns_a_clean_triple_for_any_text(self):
        rng = random.Random(self.SEED + 1)
        fragments = ["loop: r\n", "done_when:\n", "budget: 9\n", "- - -\n",
                     "\t: :\n", "loops: 3\n", "confirm: nope\n", "gate: []\n",
                     ": !!python/object\n", "\x00\x00\n", "{{{\n", "%YAML\n"]
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "lute.yaml")
            for _ in range(400):
                text = "".join(rng.choice(fragments) for _ in range(rng.randint(0, 6)))
                Path(path).write_text(text)
                loop, schedules, errors = schema.load(path)  # must never raise
                self.assertIsInstance(errors, list)
                self.assertIsInstance(schedules, list)
                self.assertTrue(loop is None or isinstance(loop, LoopSpec))

    def _rand_entry(self, rng, lid="a"):
        keys = ["loop", "run", "duration", "event", "auth", "n", "ts", "junk"]
        entry = {k: rng.choice(self._scalars(rng))
                 for k in rng.sample(keys, rng.randint(0, len(keys)))}
        if rng.random() < 0.5:   # bias many entries onto the queried loop so the
            entry["loop"] = lid   # loop-scoped accounting path is actually fuzzed,
        return entry              # not skipped by the `loop != lid` short-circuit.

    def test_ledger_accounting_is_total_and_safe_on_forged_entries(self):
        rng = random.Random(self.SEED + 2)
        for _ in range(600):
            entries = [self._rand_entry(rng) for _ in range(rng.randint(0, 25))]
            # a well-formed, author-trusted budget (as parse_budget always yields)
            budget = [("runs", rng.randint(0, 40)), ("secs", rng.randint(0, 120))]
            git_runs = rng.randint(0, 80)
            spent = ledger.budget_spent(
                "a", budget, entries, auth_for, git_runs, waited=rng.random() * 10
            )
            self.assertIsInstance(spent, bool)  # never raises, always a verdict
            runs, secs = ledger.runs_since_authenticated_answer(entries, "a", auth_for)
            self.assertIsInstance(runs, int)
            total_runs, total_secs = ledger.ledger_totals(entries)
            self.assertGreaterEqual(total_runs, 0)
            # safe classification: entries now reach the token comparison (loop==lid,
            # event=="answer") with junk `auth` values that are never our HMAC, so a
            # rewritten ledger still mints no budget refund. Non-vacuous: were the
            # auth check dropped, some of these forgeries would count.
            self.assertEqual(
                ledger.authenticated_answer_count(entries, "a", auth_for), 0
            )

    def test_ledger_authentication_discriminates_valid_from_forged(self):
        # The positive + negative controls the broad fuzz can't express: only a
        # genuine, distinct HMAC token authenticates; a right-shape / wrong-token
        # forgery never does — so a rewritten ledger cannot mint a budget refund.
        rng = random.Random(self.SEED + 4)
        for _ in range(400):
            entries, valid = [], set()
            for i in range(rng.randint(0, 12)):
                kind = rng.choice(["run", "forged", "valid", "junk"])
                if kind == "run":
                    entries.append({"loop": "a", "run": i, "duration": rng.random()})
                elif kind == "forged":
                    nonce = str(rng.randint(0, 4))  # right shape, deliberately wrong token
                    entries.append({"loop": "a", "event": "answer", "n": nonce,
                                    "auth": "forged-" + auth_for("a", nonce)})
                elif kind == "valid":
                    nonce = str(rng.randint(0, 4))
                    token = auth_for("a", nonce)
                    valid.add(token)
                    entries.append({"loop": "a", "event": "answer", "n": nonce, "auth": token})
                else:
                    entries.append(self._rand_entry(rng))  # unrelated junk never authenticates
            self.assertEqual(
                ledger.authenticated_answer_count(entries, "a", auth_for), len(valid)
            )
            self.assertIsInstance(
                ledger.budget_spent("a", [("runs", 3)], entries, auth_for, git_runs=len(entries)),
                bool,
            )

    def test_ledger_jsonl_parser_ignores_malformed_lines(self):
        rng = random.Random(self.SEED + 3)
        chunks = ['{"loop":"a","run":1}', "{truncated", "", "null", "[1,2]",
                  '{"x":', "\x00", '"scalar"', "42", '{"loop":"a","event":"answer"}']
        for _ in range(400):
            text = "\n".join(rng.choice(chunks) for _ in range(rng.randint(0, 8)))
            entries = ledger._parse_jsonl_lines(text.splitlines())  # never raises
            self.assertTrue(all(isinstance(e, dict) for e in entries))


if __name__ == "__main__":
    unittest.main()
