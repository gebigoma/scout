"""Text-formatting helpers with no dependency on any pipeline stage, so an
LLM stage (llm.py) and a non-LLM stage (digest.py) can both use them without
either importing the other - digest is not an LLM stage and that coupling
would be wrong.
"""

_MARKER = "\n...[elided]...\n"


def elide_middle(text: str, limit: int = 500) -> str:
    """Cap `text` at `limit` characters, dropping the MIDDLE rather than the
    tail.

    Diagnostic text does not put its cause in a predictable place. git's own
    `fatal:` line comes first; a Python traceback puts the exception type and
    message on its LAST line. Head-only truncation therefore loses the cause
    for whichever shape of output it happens to get - which is exactly what
    hid the cause of the 2026-09-28 push failure: the pre-push hook died with
    an uncaught `CalledProcessError` from `git rev-list` on a range whose left
    side wasn't in the local object store, and `GitError.__str__`'s
    `detail[:500]` kept the traceback's opening frames while cutting the
    final "subprocess.CalledProcessError: ... exit status 128" line - the
    only line that named what actually happened. Keeping both ends survives
    either shape of output, whichever end the cause lands on.
    """
    if len(text) <= limit:
        return text
    budget = limit - len(_MARKER)
    if budget < 2:
        return text[:limit]
    head_len = (budget + 1) // 2
    tail_len = budget - head_len
    return text[:head_len] + _MARKER + text[-tail_len:]
