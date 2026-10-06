"""Agent to record a finished event's results and per-fight stats, and mark it completed.

Runs on event day +1 and +2. Targets, chosen in code: ufcstats events still scheduled or live that
started at least 6 hours ago.

Run once by hand with: python -m ufc_agent.agents.post_event_stats [--event-id <uuid> ...]
"""
import argparse
import json

from ufc_agent.agents.loop import run_agent
from ufc_agent.llm.client import LLMClient, QuotaExhausted, format_duration
from ufc_agent.runlog import RunLog
from ufc_agent.tools.registry import ToolRegistry

MAX_STEPS = 8

SYSTEM_PROMPT = """You record the results of one finished UFC event from ufcstats.com.

1. Call fetch_page on event.event_url from the job input with include_links=true.
2. If the fight table has no results yet (the W/L column has no "win", "draw" or "nc"), reply
   "not ready" and stop.
3. Otherwise call submit_event_results once with event_url, sources=[event_url] and every fight row in
   page order. For each row:
   - fight_url: the fight-details URL at the end of the row.
   - outcome: the W/L value, "win", "draw" or "nc".
   - fighters: both fighters in the order the row lists them, each with name, fighter-details url, and
     their numbers from the Kd, Str, Td and Sub columns. Each of those columns shows two numbers: the
     first belongs to the first fighter, the second to the second fighter.
   - weight_class: the weight class text without any [img:...] marks.
   - is_title_fight: true only when the row shows [img:belt.png].
   - method: the first part of the Method column (KO/TKO, SUB, U-DEC, S-DEC, M-DEC, DQ, CNC, Overturned
     or Other). method_details: the rest of that column, if any, e.g. "Punch" or "Rear Naked Choke".
   - round and time: the Round and Time columns.
4. If submit_event_results returns errors, fix exactly what they name and resubmit once. If it still
   fails, call flag_issue (entity "event") with the event name and the errors.

Rules:
- Copy every number exactly as printed. Never compute, guess, swap or reorder values.
- Use only URLs shown on the page.
"""


class PostEventStatsAgent:
    def __init__(self, llm_client: LLMClient, registry: ToolRegistry):
        self.llm = llm_client
        self.registry = registry

    def run(self, event_id: str, run_log: RunLog) -> dict:
        """Record one event's results. Status is completed, not_ready (no results on the page yet),
        partial, or error (the event is unknown or not a ufcstats event)."""
        job_input = self.registry.db_tools.event_for_results(event_id)
        if job_input is None:
            summary = {"event_id": event_id, "error": "event not found or not a ufcstats event"}
            run_log.finish(status="error", summary=summary)
            return {**summary, "status": "error"}

        # Copying a results table needs no reasoning; thinking only adds latency and tokens.
        outcome = run_agent(
            self.llm, self.registry, "post_event_stats", SYSTEM_PROMPT, job_input, run_log,
            max_steps=MAX_STEPS, reasoning_effort="none",
        )

        saved = outcome["ok_calls"].get("submit_event_results", 0)
        summary = {
            "event": job_input["event"]["name"],
            "results_saved": saved > 0,
            "issues_flagged": outcome["ok_calls"].get("flag_issue", 0),
            "failed_submits": outcome["failed_calls"].get("submit_event_results", 0),
            "hit_step_limit": outcome["hit_step_limit"],
            "empty_reply": outcome["empty_reply"],
        }
        if saved:
            status = "completed"
        elif not (outcome["hit_step_limit"] or outcome["empty_reply"] or summary["failed_submits"]
                  or summary["issues_flagged"]):
            status = "not_ready"
        else:
            status = "partial"
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

    parser = argparse.ArgumentParser(description="Record finished events' results from ufcstats.com")
    parser.add_argument("--event-id", action="append", default=[],
                        help="process this event instead of the automatic targets; repeat for several")
    args = parser.parse_args()

    conn = connect()
    db_tools = DBTools(conn)
    event_ids = args.event_id or db_tools.events_awaiting_results()
    if not event_ids:
        print(json.dumps({"status": "completed", "events": 0, "message": "no events awaiting results"}))
        return

    agent = PostEventStatsAgent(LLMClient.from_env(), ToolRegistry(db_tools, Fetcher()))
    results = []
    for event_id in event_ids:
        run_log = RunLog(conn, "post_event_stats", {"event_id": event_id})
        try:
            results.append(agent.run(event_id, run_log))
        except QuotaExhausted as exc:
            # The remaining events stay scheduled and are picked up by the next run.
            run_log.finish(status="error", error=str(exc))
            results.append({"event_id": event_id, "status": "error", "error": str(exc),
                            "retry_in": format_duration(exc.retry_in_seconds)})
            break
        except Exception as exc:
            run_log.finish(status="error", error=str(exc))
            results.append({"event_id": event_id, "status": "error", "error": str(exc)})
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
