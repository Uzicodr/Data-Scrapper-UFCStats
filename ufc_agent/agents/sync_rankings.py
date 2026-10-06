"""Agent to keep the rankings table current. Runs daily.

Run once by hand with: python -m ufc_agent.agents.sync_rankings
"""
import json

from ufc_agent.agents.loop import run_agent
from ufc_agent.llm.client import LLMClient
from ufc_agent.runlog import RunLog
from ufc_agent.schemas.models import DIVISIONS
from ufc_agent.tools.registry import ToolRegistry

RANKINGS_URL = "https://www.ufc.com/rankings"

SYSTEM_PROMPT = f"""You keep the UFC rankings in the database current.

1. Call fetch_page on {RANKINGS_URL}. If the result has next_start, call fetch_page again with
   start=next_start until you have read every division in the job input.
2. For each division in the job input, call submit_rankings once:
   - champion: the fighter marked "Champion" in that division's header. Omit it when the title is vacant.
     Pound-for-pound lists have no champion.
   - ranked: the 15 ranked fighters in order, rank 1 first. Ignore notes such as "Rank increased by 1".
   - sources: the page URL.
3. If submit_rankings returns errors for names it could not match, call db_find_fighter for each one.
   Resubmit with that entry's fighter_id only when a candidate is clearly the same person.
   If no candidate matches, call flag_issue for that division with the unmatched names, and move on.
4. If the rankings page will not load, use web_search to find the current rankings and cite its source URLs.

Rules:
- Copy names exactly as the source prints them.
- Never invent or reorder fighters. If a division is missing or unclear, call flag_issue instead of guessing.
- Stop when every division has been saved or flagged.
"""


class SyncRankingsAgent:
    def __init__(self, llm_client: LLMClient, registry: ToolRegistry):
        self.llm = llm_client
        self.registry = registry

    def run(self, run_log: RunLog, divisions=DIVISIONS) -> dict:
        """Sync the given divisions. Returns counts of divisions saved and issues flagged."""
        job_input = {"task": "update rankings", "divisions": list(divisions)}
        # Copying ranked names off a page needs no reasoning; thinking only adds latency and tokens.
        outcome = run_agent(
            self.llm, self.registry, "sync_rankings", SYSTEM_PROMPT, job_input, run_log, reasoning_effort="none"
        )

        saved = outcome["ok_calls"].get("submit_rankings", 0)
        flagged = outcome["ok_calls"].get("flag_issue", 0)
        status = "completed" if saved >= len(divisions) else "partial"
        summary = {
            "divisions": len(divisions),
            "saved": saved,
            "issues_flagged": flagged,
            "failed_submits": outcome["failed_calls"].get("submit_rankings", 0),
            "hit_step_limit": outcome["hit_step_limit"],
        }
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
    run_log = RunLog(conn, "sync_rankings", {"divisions": list(DIVISIONS)})
    try:
        result = SyncRankingsAgent(LLMClient.from_env(), registry).run(run_log)
    except Exception as exc:
        run_log.finish(status="error", error=str(exc))
        raise
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
