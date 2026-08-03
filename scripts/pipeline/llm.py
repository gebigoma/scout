"""Shared bits for the two stages that call an LLM (classify, score)."""
import shutil
import os


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
