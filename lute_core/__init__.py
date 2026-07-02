"""Internal primitives for the lute executable.

The public entry point is the `lute` CLI.  This package holds small, testable
pieces that should not depend on terminal UI, git subprocesses, or process
supervision unless a caller passes those concerns in explicitly.
"""

# The one version source: pyproject.toml reads it at build time and the CLI
# prints it, so the number cannot fork across surfaces (pinned by t29).
__version__ = "0.2.0"
