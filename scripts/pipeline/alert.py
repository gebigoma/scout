"""Best-effort push notification via ntfy.sh - a free service with no
account/API key needed (POST to a topic URL, subscribe to the same topic in
the ntfy app). Topic name comes from the NTFY_TOPIC env var (set in the
launchd plist, not committed to the repo)."""
import os
import urllib.request


def _notify(message: str, title: str, priority: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # best-effort - don't let alert delivery failure mask the real error


def send_failure_alert(run_date: str, error_message: str) -> None:
    _notify(f"scout run {run_date} failed: {error_message}",
            title="scout weekly run failed", priority="high")


def send_success_heartbeat(run_date: str, manifest: dict) -> None:
    """Notify on success too, quietly.

    This is a dead-man's-switch: the most likely failure for a job scheduled
    on a laptop is that it never ran at all (lid closed, machine off), and
    failure-only alerting cannot detect that - silence is ambiguous. Sending
    on success makes silence meaningful.

    Including the per-source counts also surfaces the other silent failure:
    a source that starts returning zero results still "succeeds", and would
    otherwise publish an empty digest that looks like an honest quiet week.
    """
    stages = manifest.get("stages", {})
    per_source = stages.get("fetch", {}).get("per_source", {})
    counts = ", ".join(f"{name} {info.get('count', 0)}"
                       for name, info in sorted(per_source.items()))
    candidates = stages.get("dedupe", {}).get("deduped_count", "?")
    matches = stages.get("digest", {}).get("matches", "?")
    rejected = stages.get("digest", {}).get("classify_disagreements", 0)

    message = (f"{matches} matches from {candidates} candidates"
               f"{f' ({rejected} rejected on scoring)' if rejected else ''}\n"
               f"sources: {counts}")
    _notify(message, title=f"scout {run_date} ok", priority="low")
