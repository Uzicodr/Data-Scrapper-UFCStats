"""Agent to refresh fighter profiles and career stats from ufcstats.com.

Targets, chosen in code: stub fighters created by sync_upcoming, then fighters who fought in an event
completed in the last N days. Runs after post_event_stats.

Run once by hand with: python -m ufc_agent.agents.refresh_fighters [--days 14] [--limit 40]
Refresh specific fighters with: python -m ufc_agent.agents.refresh_fighters --fighter-id <uuid> [--fighter-id ...]
"""
import argparse
import json
from collections import Counter

from ufc_agent.agents.loop import run_agent
from ufc_agent.llm.client import LLMClient
from ufc_agent.runlog import RunLog
from ufc_agent.tools.registry import ToolRegistry

# Small batches keep each conversation short: every fetched page stays in the context until the batch ends.
BATCH_SIZE = 5

SYSTEM_PROMPT = """You update fighter profiles in the database from ufcstats.com.

For each fighter in the job input:
1. Call fetch_page on its profile_url. You may fetch several fighters' pages in one turn.
2. Call submit_fighter_profile with fighter_id and profile_url from the job input, and every value
   copied exactly as the page prints it:
   - name_on_page: the name at the top of the page.
   - record: the value after "Record:", e.g. "27-7-0" or "27-7-0 (1 NC)".
   - nickname: the line right after the record, when there is one.
   - height, weight, reach, stance, dob: the values after "Height:", "Weight:", "Reach:", "STANCE:", "DOB:".
   - slpm, str_acc, sapm, str_def, td_avg, td_acc, td_def, sub_avg: the values after "SLpM:",
     "Str. Acc.:", "SApM:", "Str. Def:", "TD Avg.:", "TD Acc.:", "TD Def.:", "Sub. Avg.:".
   Use "--" for any value the page shows as "--" or leaves blank.
3. If submit_fighter_profile returns errors, fix exactly what they name and resubmit once. If it still
   fails, call flag_issue (entity "fighter") with the fighter's name and the errors.
4. If a page will not load or shows a different fighter, call flag_issue instead of submitting.

Rules:
- Never compute, convert or round a value. Copy it.
- Stop when every fighter in the job input has been submitted or flagged.
"""


class RefreshFightersAgent:
    def __init__(self, llm_client: LLMClient, registry: ToolRegistry):
        self.llm = llm_client
        self.registry = registry

    def run(self, run_log: RunLog, fighters: list[dict], batch_size=BATCH_SIZE) -> dict:
        """Refresh the given fighters ({fighter_id, name, profile_url}) in batches."""
        ok_calls, failed_calls = Counter(), Counter()
        steps, hit_step_limit, empty_reply = 0, False, False
        for start in range(0, len(fighters), batch_size):
            batch = fighters[start:start + batch_size]
            outcome = run_agent(
                self.llm, self.registry, "refresh_fighters", SYSTEM_PROMPT,
                {"task": "refresh fighter profiles", "fighters": batch}, run_log,
                max_steps=2 * len(batch) + 4, reasoning_effort="none",
            )
            ok_calls.update(outcome["ok_calls"])
            failed_calls.update(outcome["failed_calls"])
            steps += outcome["steps"]
            hit_step_limit = hit_step_limit or outcome["hit_step_limit"]
            empty_reply = empty_reply or outcome["empty_reply"]

        updated = ok_calls.get("submit_fighter_profile", 0)
        summary = {
            "fighters": len(fighters),
            "updated": updated,
            "issues_flagged": ok_calls.get("flag_issue", 0),
            "failed_submits": failed_calls.get("submit_fighter_profile", 0),
            "hit_step_limit": hit_step_limit,
            "empty_reply": empty_reply,
        }
        status = "completed" if updated >= len(fighters) else "partial"
        run_log.finish(status=status, summary=summary)

        return {
            **summary,
            "status": status,
            "steps": steps,
            "tokens_input": run_log.input_tokens,
            "tokens_output": run_log.output_tokens,
        }


def main():
    from ufc_agent.db import connect
    from ufc_agent.fetch.http import Fetcher
    from ufc_agent.tools.db_tools import DBTools

    parser = argparse.ArgumentParser(description="Refresh fighter profiles from ufcstats.com")
    parser.add_argument("--days", type=int, default=14, help="fighters who fought in the last N days")
    parser.add_argument("--limit", type=int, default=40, help="maximum fighters per run")
    parser.add_argument("--fighter-id", action="append", default=[],
                        help="refresh this fighter instead of the automatic targets; repeat for several")
    args = parser.parse_args()

    conn = connect()
    db_tools = DBTools(conn)
    if args.fighter_id:
        fighters = db_tools.fighters_by_ids(args.fighter_id)
    else:
        fighters = db_tools.fighters_to_refresh(days=args.days, limit=args.limit)
    if not fighters:
        print(json.dumps({"status": "completed", "fighters": 0, "message": "nothing to refresh"}))
        return

    registry = ToolRegistry(db_tools, Fetcher())
    run_log = RunLog(conn, "refresh_fighters", {"days": args.days, "limit": args.limit, "fighters": len(fighters)})
    try:
        result = RefreshFightersAgent(LLMClient.from_env(), registry).run(run_log, fighters)
    except Exception as exc:
        run_log.finish(status="error", error=str(exc))
        raise
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
