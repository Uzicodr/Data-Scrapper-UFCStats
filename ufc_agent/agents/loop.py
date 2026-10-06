"""Tool-calling loop shared by every agent job."""
import json
import time
from collections import Counter

MAX_STEPS = 25
# Free models sometimes return an empty reply (no text, no tool call) mid-job. That is not the model
# finishing, so the same request is retried before the run is stopped as incomplete.
EMPTY_REPLY_RETRIES = 2


def is_error_result(result):
    """Tools report failures by raising, or by returning {"status": "error"} / {"error": ...}."""
    return isinstance(result, dict) and (result.get("status") == "error" or bool(result.get("error")))


def run_agent(llm, registry, job, system_prompt, job_input, run_log, max_steps=MAX_STEPS, reasoning_effort=None):
    """Run one job until the model stops calling tools or max_steps is reached.

    Returns {steps, hit_step_limit, empty_reply, ok_calls, failed_calls}. ok_calls and failed_calls
    count tool calls by tool name. empty_reply is True when the run stopped because the model kept
    returning nothing. reasoning_effort overrides the LLM client default for this job.
    """
    tools = registry.get_schemas(job)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(job_input, default=str)},
    ]
    ok_calls, failed_calls = Counter(), Counter()
    hit_step_limit, empty_reply = True, False

    for step in range(1, max_steps + 1):
        for _ in range(EMPTY_REPLY_RETRIES + 1):
            response = llm.chat(messages, tools=tools, reasoning_effort=reasoning_effort)
            run_log.llm_step(response)
            tool_calls = response.message.tool_calls or []
            if tool_calls or (response.message.content or "").strip():
                break

        if not tool_calls:
            hit_step_limit = False
            empty_reply = not (response.message.content or "").strip()
            break

        # One assistant message carrying every tool call, then one tool message per call.
        # model_dump keeps provider extras such as Gemini's thought signatures.
        messages.append({
            "role": "assistant",
            "content": response.message.content,
            "tool_calls": [call.model_dump(exclude_none=True) for call in tool_calls],
        })
        for call in tool_calls:
            name = call.function.name
            started = time.monotonic()
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                args = {"raw": call.function.arguments}
                result, is_error = {"error": f"Invalid JSON args: {exc}"}, True
            else:
                dispatched = registry.dispatch(name, args)
                result = dispatched["result"]
                is_error = dispatched["is_error"] or is_error_result(result)

            duration_ms = int((time.monotonic() - started) * 1000)
            run_log.tool_step(name, args, result, is_error=is_error, duration_ms=duration_ms)
            (failed_calls if is_error else ok_calls)[name] += 1
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, default=str),
            })

    return {
        "steps": step,
        "hit_step_limit": hit_step_limit,
        "empty_reply": empty_reply,
        "ok_calls": dict(ok_calls),
        "failed_calls": dict(failed_calls),
    }
