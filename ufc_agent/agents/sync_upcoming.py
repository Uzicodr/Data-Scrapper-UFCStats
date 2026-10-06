"""Agent to keep upcoming events and their fight cards current. Runs daily.

Run once by hand with: python -m ufc_agent.agents.sync_upcoming
"""
import datetime
import json

from ufc_agent.agents.loop import run_agent
from ufc_agent.llm.client import LLMClient
from ufc_agent.runlog import RunLog
from ufc_agent.tools.registry import ToolRegistry

UPCOMING_URL = "http://ufcstats.com/statistics/events/upcoming"
MAX_STEPS = 40

SYSTEM_PROMPT = f"""You keep upcoming UFC events and their fight cards in the database current.

1. Call fetch_page on {UPCOMING_URL} with include_links=true. Each event row shows the event name
   with its event-details URL, the date and the location.
2. Call db_list_upcoming to see what the database already has.
3. For each event on the page dated on or after "today" from the job input:
   a. Call fetch_page on its event-details URL with include_links=true.
   b. If the page lists no bouts yet, skip the event.
   c. Otherwise call submit_event_card once with:
      - event_url, name and location exactly as printed, and date as YYYY-MM-DD.
      - bouts: every bout row in page order; the first row is the main event. In each row the first
        fighter is red and the second is blue. Give each fighter's name and fighter-details URL, the
        weight class, the fight-details URL at the end of the row, and is_title_fight=true only when the
        row shows [img:belt.png].
      - sources: the event page URL.
4. If submit_event_card returns errors, fix exactly what they name and resubmit once. If it still
   fails, call flag_issue with the event name and the errors, and move on.
5. For each event from db_list_upcoming that has an event_url, is dated after today, and is no longer on
   the upcoming page, call flag_issue (entity "event") naming it: it may have been cancelled or renamed.
   Do not change it.

Rules:
- Copy names exactly as the page prints them. Never invent, merge or reorder bouts.
- Never guess a URL. Use only URLs shown on the pages.
- Stop when every upcoming event has been submitted, skipped or flagged.
"""


class SyncUpcomingAgent:
    def __init__(self, llm_client: LLMClient, registry: ToolRegistry):
        self.llm = llm_client
        self.registry = registry

    def run(self, run_log: RunLog, today=None) -> dict:
        """Sync every upcoming event. Returns counts of cards saved and issues flagged."""
        today = today or datetime.date.today()
        job_input = {"task": "update upcoming events and fight cards", "today": today.isoformat()}
        # Copying bouts off a page needs no reasoning; thinking only adds latency and tokens.
        outcome = run_agent(
            self.llm, self.registry, "sync_upcoming", SYSTEM_PROMPT, job_input, run_log,
            max_steps=MAX_STEPS, reasoning_effort="none",
        )

        summary = {
            "cards_saved": outcome["ok_calls"].get("submit_event_card", 0),
            "issues_flagged": outcome["ok_calls"].get("flag_issue", 0),
            "failed_submits": outcome["failed_calls"].get("submit_event_card", 0),
            "hit_step_limit": outcome["hit_step_limit"],
            "empty_reply": outcome["empty_reply"],
        }
        status = "partial" if outcome["hit_step_limit"] or outcome["empty_reply"] else "completed"
        run_log.finish(status=status, summary=summary)

        return {
            **summary,
            "status": status,
            "steps": outcome["steps"],
            "tokens_input": run_log.input_tokens,
            "tokens_output": run_log.output_tokens,
        }


def main():
    from ufc_agent.db import connect
    from ufc_agent.fetch.http import Fetcher
    from ufc_agent.tools.db_tools import DBTools

    conn = connect()
    registry = ToolRegistry(DBTools(conn), Fetcher())
    run_log = RunLog(conn, "sync_upcoming", {"url": UPCOMING_URL})
    try:
        result = SyncUpcomingAgent(LLMClient.from_env(), registry).run(run_log)
    except Exception as exc:
        run_log.finish(status="error", error=str(exc))
        raise
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
