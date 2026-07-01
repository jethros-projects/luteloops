"""Card parsing and summaries."""

from __future__ import annotations

import re
import os
import hashlib

from .config import AnswerAuthority
from .context import AppContext
from .domain import Card, LoopSpec
from .errors import Blocked, Gated, UsageError
from .events import EventBus, now_iso
from .formatting import human, tail
from .git_repo import GitRepo
from .ledger import runs_since_authenticated_answer
from .schema import ID_RE
from .state_store import StateStore


def answer_basis(prefix: str, answer: str) -> str:
    return hashlib.sha256((prefix + "\nANSWER: " + answer).encode()).hexdigest()


def summarize_card(lid: str, text: str) -> Card:
    gated = bool(re.search(r"^READY", text, re.M))
    kind = "ready" if gated else "blocked"
    next_command = f"lute answer {lid} approve" if gated else f'lute answer {lid} "..."'
    return Card(
        loop=lid,
        kind=kind,
        answered="\nANSWER: " in text,
        summary=(text.splitlines() or [""])[0],
        next_command=next_command,
    )


class CardService:
    def __init__(
        self,
        ctx: AppContext,
        store: StateStore,
        git: GitRepo,
        events: EventBus,
        authority: AnswerAuthority,
        ledger_entries,
        ledger_append,
        fire_halt,
    ):
        self.ctx = ctx
        self.store = store
        self.git = git
        self.events = events
        self.authority = authority
        self.ledger_entries = ledger_entries
        self.ledger_append = ledger_append
        self.fire_halt = fire_halt

    def path(self, loop_id: str) -> str:
        if not ID_RE.fullmatch(str(loop_id)):
            raise UsageError(f"loop id must be kebab-case, got {loop_id!r}")
        return self.store.child_path(self.ctx.paths.inbox, f"{loop_id}.md")

    def open_cards(self) -> list[dict[str, object]]:
        self.store.ensure_dir(self.ctx.paths.inbox)
        cards: list[dict[str, object]] = []
        for name in sorted(os.listdir(self.ctx.paths.inbox)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(self.ctx.paths.inbox, name)
            if not self.store.is_regular_file(path):
                continue
            with open(path, encoding="utf-8") as f:
                card = summarize_card(name[:-3], f.read())
            cards.append({
                "lid": card.loop,
                "gated": card.kind == "ready",
                "answered": card.answered,
                "summary": card.summary,
                "next": card.next_command,
            })
        return cards

    def consume_answer(self, loop: LoopSpec) -> str | None:
        lid = str(loop.id)
        path = self.path(lid)
        if not self.store.is_regular_file(path):
            return None
        text = self.store.read_text(path)
        marker = text.rfind("\nANSWER: ")
        if marker < 0:
            return None
        body = text[marker + len("\nANSWER: "):]
        match = re.search(r"\nANSWER-AUTH: (\S+)\s*$", body)
        answer_raw = body[:match.start()] if match else body
        answer = answer_raw.strip()
        basis = answer_basis(text[:marker], answer_raw)
        genuine = bool(match) and self.authority.valid(lid, basis, match.group(1))
        self.store.remove_runner_file(path)
        if not genuine:
            if self.git.ok("ls-files", "--error-unmatch", "--", path, cwd=self.ctx.shared_root):
                self.git.shared_text(self.ctx.shared_root, "add", "-A", "--", path)
                self.git.shared_text(self.ctx.shared_root, "commit", "-q", "--allow-empty", "-m", f"lute({lid}): card cleared")
            return None
        nonce = basis
        self.ledger_append({"ts": now_iso(), "loop": lid, "event": "answer", "n": nonce, "auth": self.authority.token(lid, nonce)})
        # The ledger line commits to THIS branch (a child commits to its own
        # worktree branch); the card lives in the shared INBOX and clears there.
        self.git.text("add", "--", self.ctx.paths.ledger)
        self.git.commit(f"lute({lid}): answer consumed", allow_empty=True)
        if self.git.ok("ls-files", "--error-unmatch", "--", path, cwd=self.ctx.shared_root):
            self.git.shared_text(self.ctx.shared_root, "add", "-A", "--", path)
            self.git.shared_text(self.ctx.shared_root, "commit", "-q", "--allow-empty", "-m", f"lute({lid}): card cleared")
        return answer

    def answer_card(self, loop_id: str, text: str) -> str | None:
        path = self.path(loop_id)
        if not self.store.is_regular_file(path):
            have = sorted(f[:-3] for f in os.listdir(self.ctx.paths.inbox)) if os.path.isdir(self.ctx.paths.inbox) else []
            return f"no escalation card at {path}" + (f"; open cards: {', '.join(have)}" if have else "; no open cards")
        basis = answer_basis(self.store.read_text(path), text)
        token = self.authority.token(loop_id, basis)
        self.store.append_text(path, f"\nANSWER: {text}\nANSWER-AUTH: {token}\n")
        self.git.shared_text(self.ctx.shared_root, "add", path)
        self.git.shared_text(self.ctx.shared_root, "commit", "-q", "-m", f"lute({loop_id}): answer")
        return None

    def write_card(self, lid: str, text: str, commit_msg: str) -> str:
        path = self.path(lid)
        self.store.safe_write_regular(path, text)
        self.git.shared_text(self.ctx.shared_root, "add", path)
        self.git.shared_text(self.ctx.shared_root, "commit", "-q", "--allow-empty", "-m", commit_msg)
        return path

    def raise_block(self, lid: str, text: str, commit_msg: str, *, message: str = "blocked", **event_fields) -> None:
        path = self.write_card(lid, text, commit_msg)
        self.events.emit("escalated", lid, card=f"INBOX/{lid}.md", **event_fields)
        self.fire_halt(lid, "blocked", path)
        raise Blocked(message)

    def raise_gate(self, lid: str, text: str | None = None, commit_msg: str | None = None) -> None:
        path = self.path(lid)
        if text is not None and commit_msg is not None:
            path = self.write_card(lid, text, commit_msg)
        self.events.emit("gated", lid, card=f"INBOX/{lid}.md")
        self.fire_halt(lid, "gated", path)
        raise Gated()

    def escalate(self, loop: LoopSpec, tail_text: str, note: str = "") -> None:
        lid = str(loop.id)
        runs, secs = runs_since_authenticated_answer(self.ledger_entries(), lid, self.authority.token)
        journal = os.path.join(self.ctx.paths.journal, f"{lid}.md")
        jtail = tail(self.store.read_text(journal), 5) if self.store.is_regular_file(journal) else "(no journal yet)"
        text = (
            f"BLOCKED: needs input after {runs} run{'s' if runs != 1 else ''} · {human(secs)}\n"
            + (note + "\n" if note else "")
            + f"Check: {loop.done_when.command}\n"
            f"Last failure (tail):\n{tail(tail_text, 10)}\n"
            f"Journal (last 5 lines):\n{jtail}\n"
            f"→ One question, stated by the runner: what should change?\n"
            f'Answer with: lute answer {lid} "..."\n'
        )
        self.raise_block(lid, text, f"lute({lid}): escalate")

    def supersede(self, loop_id: str, approved: bool) -> None:
        path = self.path(loop_id)
        text = self.store.read_text(path)
        if (approved or "READY" in text) and "SUPERSEDED" not in text:
            self.store.safe_write_regular(path, text + "SUPERSEDED: exam no longer passes\n")

    def gate_halt(self, loop: LoopSpec) -> None:
        lid = str(loop.id)
        path = self.path(lid)
        text = self.store.read_text(path)
        if not re.search(r"^READY", text, re.M):
            base = self.ctx.trusted_base or self.git.branch_base()
            diffstat = self.git.text("diff", "--stat", base + "...HEAD")
            self.raise_gate(
                lid,
                text + f"READY: exam passing, awaiting your approval\n"
                f"Check: {loop.done_when.command}\n"
                f"Diff:\n{diffstat}"
                f"Approve: lute answer {lid} approve   (only this exact answer seals this state)\n"
                f"Reject: lute answer {lid} \"...\" records your note but does not seal; "
                f"change files and re-run if needed\n",
                f"lute({lid}): gated",
            )
        self.raise_gate(lid)
