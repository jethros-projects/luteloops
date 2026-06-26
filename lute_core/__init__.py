"""Internal primitives for the lute executable.

The public entry point is the `lute` CLI.  This package holds small, testable
pieces that should not depend on terminal UI, git subprocesses, or process
supervision unless a caller passes those concerns in explicitly.
"""
