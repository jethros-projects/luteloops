#!/usr/bin/env python3
"""fake_judge - a scripted stand-in for a judge CLI (spec §6).

Reads the judge payload (rubric + diff + instruction) on stdin and replies
deterministically, PASS/FAIL on the first line as the protocol demands:

    fake_judge.py --pass-if SUBSTR    PASS iff SUBSTR occurs in the payload
    fake_judge.py --verdict V         fixed first line V (use GARBAGE to
                                      exercise the malformed-reply = fail path)
"""
import sys

payload = sys.stdin.read()
args = sys.argv[1:]

if args[:1] == ["--pass-if"] and len(args) > 1:
    ok = args[1] in payload
    print("PASS" if ok else "FAIL")
    print("- rubric item 1: %s `%s` in the diff (cite: diff body line 1)"
          % ("found" if ok else "did not find", args[1]))
elif args[:1] == ["--verdict"] and len(args) > 1:
    print("Well, let me think about it..." if args[1] == "GARBAGE" else args[1])
    print("- reasoning, citing file:1")
else:
    print("FAIL")
    print("- no instruction given to fake_judge")
