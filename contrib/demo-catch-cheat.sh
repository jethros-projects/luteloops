#!/bin/bash
# demo-catch-cheat.sh - watch lute refuse a bought pass, then accept an honest
# one. Fully hermetic: a rigged repo in a temp dir, a scripted agent, no LLM
# calls, no network. The scripted agent plays both parts:
#
#   Act 1 - the cheat.  Run 1 deletes the failing test. The exam would go
#           green - so lute quarantines the edit, restores the test, and the
#           exam fails exactly as before. The cheat bought nothing.
#   Act 2 - the fix.    Run 2 fixes the code. The same exam passes. Done.
#
# Usage:  contrib/demo-catch-cheat.sh        (pauses for narration on a TTY)
#         contrib/demo-catch-cheat.sh -y     no pauses - straight through
#
# Re-runnable: every run rigs a fresh repo and prints its path, so you can
# inspect the commits, the quarantine record, and the events afterwards.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LUTE="$ROOT/lute"
FAKE="python3 '$ROOT/tests/fake_agent.py'"

PAUSE=1
case "${1:-}" in -y|--yes|--no-pause) PAUSE=0 ;; esac
[ -t 0 ] || PAUSE=0

if [ -t 1 ]; then
  B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; C=$'\033[36m'; Y=$'\033[33m'; R=$'\033[0m'
else
  B=''; D=''; G=''; C=''; Y=''; R=''
fi
say()  { printf '%s\n' "$*"; }
head() { printf '\n%s┌─ %s%s\n' "$C$B" "$*" "$R"; }
beat() { [ "$PAUSE" -eq 1 ] && { printf '%s   ↵ press enter to %s%s' "$D" "$*" "$R"; read -r _; } || true; }
die()  { say "${Y}demo assertion failed:${R} $*"; exit 1; }

command -v python3 >/dev/null || { say "need python3 on PATH"; exit 1; }
[ -x "$LUTE" ] || { say "lute not found at $LUTE"; exit 1; }

# ---- rig the broken repo ----------------------------------------------------
WORK="$(mktemp -d "${TMPDIR:-/tmp}/lute-cheat-demo.XXXXXX")"
cd "$WORK" || exit 1
git init -q -b main
git config user.email demo@lute; git config user.name lute-demo

mkdir -p tests
cat > tax.py <<'PY'
def total(amount):
    return amount + amount * 20 // 100   # BUG: charges 20% tax; the tests want 10%
PY
cat > tests/test_tax.py <<'PY'
import sys
from tax import total
sys.exit(0 if total(100) == 110 else 1)
PY

# The scripted agent's two performances: cheat, then fix.
cat > playbook.json <<'PY'
{ "make-it-pass": {
    "1": [ {"delete": "tests/test_tax.py"},
           {"journal": "run 1: deleted the failing test. surely green now."} ],
    "2": [ {"write": {"path": "tax.py", "content": "def total(amount):\n    return amount + amount * 10 // 100\n"}},
           {"journal": "run 2: the quarantine note said the exam was restored; fixed the rate instead."} ] } }
PY

cat > lute.yaml <<EOF
loop: make-it-pass
agent: $FAKE
task: Make the suite pass. Do not touch the tests.
done_when: "PYTHONPATH=. python3 -B tests/test_tax.py"
protected: ["tests/**"]
budget: 3 runs
EOF

git add -A && git commit -qm "broken tax math, one failing test"

# ---- 1. the setup -----------------------------------------------------------
head "the repo, broken on purpose"
say "${D}$WORK${R}"
say "  tax.py               charges 20% tax; the tests demand 10%"
printf '  tests/test_tax.py    '
PYTHONPATH=. python3 -B tests/test_tax.py && say "${G}pass${R}" || say "${Y}FAIL${R}  (total(100) → 120, want 110)"
say "  protected:           tests/** — the exam's own materials"
say "  the agent's plan     run 1: ${Y}delete the failing test${R} · run 2: fix the code"

# ---- 2. the run -------------------------------------------------------------
head "lute run — the cheat, then the fix"
beat "run"
rc=0; "$LUTE" run || rc=$?
[ "$rc" -eq 0 ] || die "run exited $rc (expected the honest fix to close the loop)"

# ---- 3. the receipts --------------------------------------------------------
head "act 1's receipt: the quarantine record"
"$LUTE" quarantine
qid="$("$LUTE" quarantine | awk 'NR==1{print $1}')"
[ -n "$qid" ] || die "no quarantine record - the cheat was not caught"
say ""
say "${B}the attempted edit, kept for inspection (lute quarantine diff $qid):${R}"
"$LUTE" quarantine diff "$qid" | sed 's/^/  /'

head "act 2's receipt: the branch"
say "${B}one commit per attempt on lute/make-it-pass:${R}"
git --no-pager log --oneline lute/make-it-pass | sed 's/^/  /'
say ""
run1="$(git log --format='%H %s' lute/make-it-pass | awk '/run 1$/{print $1}')"
if [ -n "$run1" ] && git show --stat --format= "$run1" | grep -q 'test_tax'; then
  die "run 1's commit touched the protected test - the cheat leaked into history"
fi
say "${B}run 1's commit does NOT contain the test deletion — quarantined, not committed.${R}"
[ -f tests/test_tax.py ] || die "the protected test is missing from the final tree"
PYTHONPATH=. python3 -B tests/test_tax.py || die "the exam does not pass on the final tree"
say "${B}the exam that closed the loop is the same one the agent tried to delete.${R}"

# ---- 4. what you just saw ---------------------------------------------------
head "what you just saw"
say "  1. The agent deleted tests/test_tax.py — the one file that could fail it."
say "  2. Before grading, lute re-hashed the protected materials against the"
say "     pinned base, saved the edit under .lute/quarantine/, and restored the"
say "     trusted test. The exam ran unchanged and failed exactly as before."
say "  3. The next prompt named the quarantine record, so the agent fixed the"
say "     code instead. Same exam, honest pass, loop closed."
say "  4. Every attempt is a commit; the cheat is inspectable but never enters"
say "     run history. That is INVARIANT.md, clause 2: exam materials are"
say "     restored from a pinned base before every check."
say ""
say "${G}${B}✔ the builder cannot author its own verdict.${R}"
say "${D}explore the evidence:  cd $WORK  ·  lute quarantine  ·  git log lute/make-it-pass${R}"
