#!/bin/bash
# demo.sh - a live showcase. lute drives a REAL codex agent to fix a broken
# repo while nested loops close bottom-up in the cockpit. Nothing is faked:
# codex reads each prompt, edits the file, exits; lute runs the exams.
#
# Turtles all the way down: a calculator ships with two bugs - add subtracts,
# mul multiplies wrong. Two child loops each own one bug and one exam; the root
# seals only after both children close AND its own full-suite check passes. The
# outermost loop cannot lie.
#
# Usage:  contrib/demo.sh            run the showcase (pauses for narration on a TTY)
#         contrib/demo.sh -y         no pauses - straight through
#         LUTE_DEMO_AGENT='claude -p' contrib/demo.sh    swap the engine
#
# Re-runnable: each run rigs a fresh repo in its own temp dir and prints the
# path, so you can poke at the commits and diffs codex made afterwards.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LUTE="$ROOT/lute"
AGENT="${LUTE_DEMO_AGENT:-codex exec --sandbox workspace-write}"

PAUSE=1
case "${1:-}" in -y|--yes|--no-pause) PAUSE=0 ;; esac
[ -t 0 ] || PAUSE=0   # non-interactive stdin (piped/CI): never pause

# ---- cosmetics -------------------------------------------------------------
if [ -t 1 ]; then
  B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; C=$'\033[36m'; Y=$'\033[33m'; R=$'\033[0m'
else
  B=''; D=''; G=''; C=''; Y=''; R=''
fi
say()  { printf '%s\n' "$*"; }
head() { printf '\n%s┌─ %s%s\n' "$C$B" "$*" "$R"; }
beat() { [ "$PAUSE" -eq 1 ] && { printf '%s   ↵ press enter to %s%s' "$D" "$*" "$R"; read -r _; } || true; }

command -v python3 >/dev/null || { say "need python3 on PATH"; exit 1; }
[ -x "$LUTE" ] || { say "lute not found at $LUTE"; exit 1; }

# ---- rig the broken repo ---------------------------------------------------
WORK="$(mktemp -d "${TMPDIR:-/tmp}/lute-demo.XXXXXX")"
cd "$WORK" || exit 1
git init -q -b main
git config user.email demo@lute; git config user.name lute-demo

cat > calc.py <<'PY'
def add(a, b):
    return a - b   # BUG: subtracts instead of adding


def mul(a, b):
    return a + b   # BUG: adds instead of multiplying
PY

cat > test_add.py <<'PY'
import sys
from calc import add
sys.exit(0 if add(2, 3) == 5 and add(10, 5) == 15 else 1)
PY

cat > test_mul.py <<'PY'
import sys
from calc import mul
sys.exit(0 if mul(2, 3) == 6 and mul(4, 5) == 20 else 1)
PY

# The luteloops file: a root whose exam is the whole suite, over two children
# that each own one bug and one machine-checkable exam. codex never grades
# itself - it edits calc.py; lute runs the checks. (-B defeats __pycache__.)
cat > lute.yaml <<YAML
loop: calculator
agent: $AGENT
budget: 20m
done_when: "python3 -B test_add.py && python3 -B test_mul.py"
loops:
  - loop: fix-add
    task: >
      calc.py has a bug: add(a, b) subtracts instead of adding.
      Fix ONLY the add function so it returns a + b. Leave mul untouched.
    done_when: "python3 -B test_add.py"
    budget: 4 runs
  - loop: fix-mul
    task: >
      calc.py has a bug: mul(a, b) adds instead of multiplying.
      Fix ONLY the mul function so it returns a * b. Leave add untouched.
    done_when: "python3 -B test_mul.py"
    budget: 4 runs
YAML

git add -A && git commit -qm "broken calculator (two bugs)"

# ---- 1. the broken state ---------------------------------------------------
head "the repo, broken on purpose"
say "${D}$WORK${R}"
say "  calc.py    add() subtracts · mul() adds"
printf '  test_add   '; python3 -B test_add.py && say "${G}pass${R}" || say "${Y}FAIL${R}  (add(2,3) → -1, want 5)"
printf '  test_mul   '; python3 -B test_mul.py && say "${G}pass${R}" || say "${Y}FAIL${R}  (mul(2,3) → 5, want 6)"
say "  engine     ${B}$AGENT${R}"

# ---- 2. lint: are the exams even administrable? ----------------------------
head "lute lint - execute every exam once before any work"
beat "lint"
"$LUTE" lint
say "${D}  red exams are fine; lint fails only if an exam can't be run at all.${R}"

# ---- 3. the main event -----------------------------------------------------
head "lute run - codex grinds until the exams pass"
if [ -t 1 ]; then
  say "${D}  the cockpit renders below: a loop hierarchy + a live tail of codex.${R}"
  say "${D}  ✔ closed · ↻ in progress · ◌ untouched   ·   q detaches, run continues.${R}"
else
  say "${D}  no TTY → plain mode: one compact line per event, log paths inline.${R}"
fi
beat "start the run"
rc=0; "$LUTE" run || rc=$?

# ---- 4. the verdict --------------------------------------------------------
head "sealed"
"$LUTE" status || true
say ""
say "${B}each iteration is a commit on branch lute/calculator:${R}"
git --no-pager log --oneline lute/calculator 2>/dev/null | sed 's/^/  /' || \
  git --no-pager log --oneline | sed 's/^/  /'
say ""
say "${B}what codex actually changed - calc.py, exactly the two bugs:${R}"
base="$(git rev-list --max-parents=0 HEAD 2>/dev/null)"
git --no-pager diff "$base"..HEAD -- calc.py 2>/dev/null | sed 's/^/  /' || true
say ""
if [ "$rc" -eq 0 ]; then
  say "${G}${B}✔ root sealed green - the outermost loop cannot lie.${R}"
else
  say "${Y}run exited $rc${R} - inspect: ${D}$WORK  ·  $LUTE watch --snapshot --file $WORK/lute.yaml${R}"
fi
say ""
say "${D}explore the result:  cd $WORK${R}"
exit "$rc"
