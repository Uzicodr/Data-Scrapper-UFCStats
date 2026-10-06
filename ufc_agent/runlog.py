"""Records agent runs, steps and review items in the agent_runs / agent_steps / review_queue tables."""
import json
import uuid

from psycopg.types.json import Jsonb

MAX_RESULT_CHARS = 4000


def _json(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = {"text": value}
    # Serialize to JSON first to handle non-serializable types like UUID
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > MAX_RESULT_CHARS:
        return Jsonb({"truncated": True, "preview": text[:MAX_RESULT_CHARS]})
    return Jsonb(json.loads(text))


class RunLog:
    def __init__(self, conn, job, run_input=None):
        self.conn = conn
        self.job = job
        self.run_id = uuid.uuid4()
        self.step = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.provider = None
        self.model = None
        conn.execute(
            "insert into agent_runs (id, job, status, input) values (%s, %s, 'running', %s)",
            (self.run_id, job, _json(run_input or {})),
        )
        conn.commit()

    def llm_step(self, response):
        self.step += 1
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.provider, self.model = response.provider, response.model
        tool_calls = [
            {"name": call.function.name, "arguments": call.function.arguments}
            for call in (response.message.tool_calls or [])
        ]
        self.conn.execute(
            "insert into agent_steps (id, run_id, step, kind, name, args, result, is_error, provider, model, "
            "input_tokens, output_tokens, duration_ms) values (%s, %s, %s, 'llm', null, %s, %s, false, %s, %s, %s, %s, %s)",
            (
                uuid.uuid4(), self.run_id, self.step,
                _json({"fallback_errors": response.errors}) if response.errors else None,
                _json({"content": response.message.content, "tool_calls": tool_calls,
                       "finish_reason": getattr(response, "finish_reason", None)}),
                response.provider, response.model, response.input_tokens, response.output_tokens,
                response.duration_ms,
            ),
        )
        self.conn.commit()

    def tool_step(self, name, args, result, is_error=False, duration_ms=None):
        self.step += 1
        self.conn.execute(
            "insert into agent_steps (id, run_id, step, kind, name, args, result, is_error, duration_ms) "
            "values (%s, %s, %s, 'tool', %s, %s, %s, %s, %s)",
            (uuid.uuid4(), self.run_id, self.step, name, _json(args), _json(result), is_error, duration_ms),
        )
        self.conn.commit()

    def flag(self, entity_type, reason, entity_id=None, payload=None):
        self.conn.execute(
            "insert into review_queue (id, run_id, entity_type, entity_id, reason, payload) "
            "values (%s, %s, %s, %s, %s, %s)",
            (uuid.uuid4(), self.run_id, entity_type, entity_id, reason, _json(payload)),
        )
        self.conn.commit()

    def finish(self, status, summary=None, error=None):
        self.conn.execute(
            "update agent_runs set status = %s, summary = %s, error = %s, provider = %s, model = %s, "
            "input_tokens = %s, output_tokens = %s, finished_at = now() where id = %s",
            (status, _json(summary), error, self.provider, self.model, self.input_tokens, self.output_tokens,
             self.run_id),
        )
        self.conn.commit()
