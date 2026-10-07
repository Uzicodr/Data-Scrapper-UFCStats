"""Nudges the Octapulse backend after the agent saves something users should hear about.

The backend's notification jobs only run while its free Render instance is awake. POST
/internal/notify wakes it and runs them straight away, so live results and news pushes go out in
minutes instead of whenever someone next opens the app. Needs BACKEND_URL and INTERNAL_TOKEN; without
them this does nothing. Never raises: a missed nudge is caught up by the backend's own timer.
"""
import httpx

from ufc_agent.config import optional_env

# A sleeping Render instance takes up to a minute to start.
TIMEOUT_SECONDS = 90


def notify_backend(reason, client=None):
    """Returns the backend's counts of notifications sent, or {"skipped"/"error": ...}."""
    url, token = optional_env("BACKEND_URL"), optional_env("INTERNAL_TOKEN")
    if not url or not token:
        return {"skipped": "BACKEND_URL or INTERNAL_TOKEN not set"}
    try:
        response = (client or httpx).post(
            f"{url.rstrip('/')}/internal/notify",
            headers={"X-Internal-Token": token, "User-Agent": f"octapulse-agent ({reason})"},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": f"{reason}: {exc}"}
