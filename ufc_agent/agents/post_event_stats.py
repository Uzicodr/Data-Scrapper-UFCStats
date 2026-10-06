"""Agent to fill per-fight stats and mark event completed. Runs day +1 and +2 post-event."""
from ufc_agent.agents.loop import run_agent
from ufc_agent.llm.client import LLMClient
from ufc_agent.runlog import RunLog
from ufc_agent.tools.registry import ToolRegistry

SYSTEM_PROMPT = """You are UFC data extraction assistant. Your job is to fetch fight stats pages and extract per-fight statistics.

For each fight in the specified event:
1. Use fetch_page to get ufcstats.com stats pages (e.g., ufcstats.com/match/123)
2. Extract stats from the page: knockdowns, significant strikes, takedowns, submission attempts, control time
3. Use submit_fight_stats to record stats with the page URL as source
4. If data is missing or ambiguous, use flag_issue instead of guessing

Important:
- All numeric stats must be >= 0
- Attempted must be >= Landed for strikes and takedowns
- Never invent numbers; if you can't read them, flag the issue
- Include the source URL when submitting
- Only submit stats you can verify from the page
"""

class PostEventStatsAgent:
    def __init__(self, llm_client: LLMClient, registry: ToolRegistry):
        self.llm = llm_client
        self.registry = registry

    def run(self, event_id: str, run_log: RunLog) -> dict:
        """Run agent on event. Event must be marked 'completed' in DB before calling this.

        Returns dict with summary, stats_submitted, errors.
        """
        job_input = {"event_id": event_id, "task": "extract per-fight stats"}
        outcome = run_agent(self.llm, self.registry, "post_event_stats", SYSTEM_PROMPT, job_input, run_log)

        stats_submitted = outcome["ok_calls"].get("submit_fight_stats", 0)
        issues_flagged = outcome["ok_calls"].get("flag_issue", 0)

        summary = f"Event {event_id}: submitted {stats_submitted} stat sets, flagged {issues_flagged} issues"
        run_log.finish(status="completed", summary=summary)

        return {
            "event_id": event_id,
            "summary": summary,
            "stats_submitted": stats_submitted,
            "issues_flagged": issues_flagged,
            "steps": outcome["steps"],
            "tokens_input": run_log.input_tokens,
            "tokens_output": run_log.output_tokens,
        }
