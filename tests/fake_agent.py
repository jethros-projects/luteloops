#!/usr/bin/env python3
"""fake_agent - a scripted stand-in for a real agent CLI.

Invoked exactly like a real engine: the runner pipes one prompt on stdin
and waits for the process to exit. Behavior is scripted per fixture in
./playbook.json (cwd = fixture repo root):

    { "<loop-id>": { "<run-number>": [ step, ... ] } }

Steps:
    {"write":  {"path": p, "content": c}}    overwrite a file
    {"append": {"path": p, "content": c}}    append to a file
    {"touch":  p}                            create empty file
    {"sleep":  seconds}                      stall (T6 crash window, T10 live window)
    {"print":  "text"}                       write a line to stdout (T10 streaming)
    {"journal": "line"}                      append a line to this loop's journal
    {"require": p}                           stop this run unless p exists (order proof)
    {"if_journal_contains": s,
     "then": [...], "else": [...]}           branch on persisted journal content (T3)

Run numbers are counted per loop in .fake_runs_<loop>; every prompt received
is saved to prompts/<loop>.run<n>.txt so the harness can inspect it.
"""
import json
import os
import re
import sys
import time

# T8: every child process of this agent carries the marker.
os.environ["LUTE_FAKE_AGENT"] = "1"

prompt = sys.stdin.read()
m = re.search(r"\.lute/journal/([A-Za-z0-9_-]+)\.md", prompt)
loop = m.group(1) if m else "unknown"

counter = ".fake_runs_%s" % loop
n = (int(open(counter).read()) if os.path.exists(counter) else 0) + 1
with open(counter, "w") as f:
    f.write(str(n))

os.makedirs("prompts", exist_ok=True)
with open("prompts/%s.run%d.txt" % (loop, n), "w") as f:
    f.write(prompt)

JOURNAL = ".lute/journal/%s.md" % loop


def run_steps(steps):
    for s in steps:
        if "write" in s:
            with open(s["write"]["path"], "w") as f:
                f.write(s["write"]["content"])
        elif "append" in s:
            with open(s["append"]["path"], "a") as f:
                f.write(s["append"]["content"])
        elif "touch" in s:
            open(s["touch"], "a").close()
        elif "sleep" in s:
            time.sleep(s["sleep"])
        elif "print" in s:
            print(s["print"], flush=True)
        elif "journal" in s:
            os.makedirs(".lute/journal", exist_ok=True)
            with open(JOURNAL, "a") as f:
                f.write(s["journal"] + "\n")
        elif "require" in s:
            if not os.path.exists(s["require"]):
                return
        elif "if_journal_contains" in s:
            text = open(JOURNAL).read() if os.path.exists(JOURNAL) else ""
            branch = "then" if s["if_journal_contains"] in text else "else"
            run_steps(s.get(branch, []))


book = json.load(open("playbook.json")) if os.path.exists("playbook.json") else {}
run_steps(book.get(loop, {}).get(str(n), []))
