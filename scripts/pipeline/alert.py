"""Best-effort failure notification via ntfy.sh - a free push notification
service with no account/API key needed (POST to a topic URL, subscribe to
the same topic in the ntfy app). Topic name comes from the NTFY_TOPIC env
var (set in the launchd plist, not committed to the repo)."""
import os
import urllib.request


def send_failure_alert(run_date: str, error_message: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    message = f"scout run {run_date} failed: {error_message}"
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": "scout weekly run failed", "Priority": "high"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # best-effort - don't let alert delivery failure mask the real error
