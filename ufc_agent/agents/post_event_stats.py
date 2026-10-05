"""Agent to fill per-fight stats and mark event completed. Runs day +1 and +2 post-event."""
import json
import re

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

MAX_STEPS = 25


class PostEventStatsAgent:
    def __init__(self, llm_client: LLMClient, registry: ToolRegistry):
        self.llm = llm_client
        self.registry = registry
        self.tools = registry.get_schemas("post_event_stats")

    def run(self, event_id: str, run_log: RunLog) -> dict:
        """Run agent on event. Event must be marked 'completed' in DB before calling this.

        Returns dict with summary, stats_submitted, errors.
        """
        job_input = {"event_id": event_id, "task": "extract per-fight stats"}

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(job_input)},
        ]

        stats_submitted = 0
        issues_flagged = 0

        for step in range(MAX_STEPS):
            # Call LLM
            response = self.llm.chat(messages, tools=self.tools)
            run_log.llm_step(response)

            # Check for tool calls
            if not response.message.tool_calls:
                break

            # Process each tool call
            for call in response.message.tool_calls:
                tool_name = call.function.name
                try:
                    args = json.loads(call.function.arguments)
                except json.JSONDecodeError as e:
                    result = {"error": f"Invalid JSON args: {e}"}
                    is_error = True
                else:
                    dispatch_result = self.registry.dispatch(tool_name, args)
                    result = dispatch_result["result"]
                    is_error = dispatch_result["is_error"]

                    # Count submissions
                    if tool_name == "submit_fight_stats" and not is_error:
                        stats_submitted += 1
                    elif tool_name == "flag_issue":
                        issues_flagged += 1

                run_log.tool_step(tool_name, args, result, is_error=is_error)

                # Add result to messages for next LLM call
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [call]  # OpenAI format
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result)
                })

        summary = f"Event {event_id}: submitted {stats_submitted} stat sets, flagged {issues_flagged} issues"
        run_log.finish(status="completed", summary=summary)

        return {
            "event_id": event_id,
            "summary": summary,
            "stats_submitted": stats_submitted,
            "issues_flagged": issues_flagged,
            "steps": step + 1,
            "tokens_input": run_log.input_tokens,
            "tokens_output": run_log.output_tokens,
        }
