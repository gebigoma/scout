"""Shared bits for the two stages that call an LLM (classify, score)."""
import shutil
import os
import subprocess


# Pinned deliberately. Without it these calls inherit whatever interactive
# default the user last set via /model, so changing your editor preference
# would silently change what every scheduled run costs. Override for a one-off
# with SCOUT_MODEL=opus.
MODEL = os.environ.get("SCOUT_MODEL", "sonnet")


def failure_detail(proc: "subprocess.CompletedProcess") -> str:
    """Diagnostic text for a failed CLI call, from whichever stream carried it.

    Reporting stderr alone is not enough: the CLI prints some fatal conditions
    to *stdout* and exits non-zero with stderr empty. An expired login is one
    of them ("Not logged in - Please run /login"), which is how the 2026-08-31
    run logged `claude classify call failed (exit 1): ` with nothing after the
    colon and lost 397 already-fetched listings to a cause the log could not
    name. stderr comes first because that is where a real crash lands.
    """
    parts = [s.strip() for s in (proc.stderr, proc.stdout) if s and s.strip()]
    return " | ".join(parts)[:500] if parts else "no output on stdout or stderr"


def claude_bin() -> str:
    """Resolve the Claude Code CLI. Checked in this order so the pipeline
    runs on any machine (a hardcoded path would make the repo useless to
    anyone else, including a reviewer trying it out)."""
    explicit = os.environ.get("CLAUDE_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    raise RuntimeError(
        "Could not find the 'claude' CLI. Install Claude Code, or set "
        "CLAUDE_BIN to its full path (the launchd job sets PATH explicitly)."
    )
