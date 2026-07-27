"""
notify.py — phone push notifications via ntfy.

A research run takes minutes and the watchdog is headless, so the only way to
know a report landed was to go and look. ntfy is the smallest thing that fixes
that: one HTTP POST to a topic, no account, and the phone app subscribes to it.

Unset NTFY_TOPIC disables notifications entirely — everything here becomes a
no-op, so this is opt-in and the pipeline behaves identically without it.

Notification policy lives in `notify_run`: only the long, interesting runs
(research / concept / callout) notify on success, but *any* kind notifies on
failure — a clip that silently failed is exactly what you'd want to hear about.
"""

import requests

from config import NTFY_TOPIC, NTFY_SERVER, NTFY_KINDS

# ntfy priorities: 1 min, 3 default, 4 high.
_PRIORITY = {"done": "3", "failed": "4", "interrupted": "4"}
_EMOJI = {"done": "white_check_mark", "failed": "rotating_light",
          "interrupted": "warning"}


def push(title: str, message: str, *, priority: str = "3", tags: str = "") -> bool:
    """
    Send one notification. Returns True if it went out.

    Never raises: a notification is a courtesy, and an ntfy outage must not fail
    the run that was trying to report success.
    """
    if not NTFY_TOPIC:
        return False
    try:
        headers = {"Title": title, "Priority": priority}
        if tags:
            headers["Tags"] = tags
        resp = requests.post(
            f"{NTFY_SERVER.rstrip('/')}/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=8,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[notify] push failed ({e})")
        return False


def _duration(secs: float) -> str:
    secs = int(secs or 0)
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m{secs % 60:02d}s"


def notify_run(record: dict) -> None:
    """
    Push a finished run, following the policy above. Called by telemetry._finish.
    """
    if not NTFY_TOPIC:
        return

    kind = record.get("kind", "")
    status = record.get("status", "done")
    if status == "done" and kind not in NTFY_KINDS:
        return

    title = record.get("title") or kind
    cost = record.get("cost_usd") or 0.0
    took = _duration(record.get("secs", 0))

    if status == "done":
        heading = f"{kind.capitalize()} done — {title}"
        body = f"{took} · ${cost:.3f} · {record.get('calls', 0)} model calls"
    else:
        heading = f"{kind.capitalize()} {status} — {title}"
        body = f"{record.get('error') or 'no error recorded'}\n{took} · ${cost:.3f}"

    push(heading, body,
         priority=_PRIORITY.get(status, "3"),
         tags=_EMOJI.get(status, "information_source"))
