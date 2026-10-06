"""Test agent components."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ufc_agent.agents.loop import run_agent
from ufc_agent.agents.post_event_stats import PostEventStatsAgent, SYSTEM_PROMPT
from ufc_agent.tools.registry import ToolRegistry


def test_system_prompt_exists():
    assert "UFC data extraction" in SYSTEM_PROMPT
    assert "fetch_page" in SYSTEM_PROMPT


def test_agent_initialization():
    llm_client = MagicMock()
    registry = MagicMock()
    registry.get_schemas.return_value = []

    agent = PostEventStatsAgent(llm_client, registry)
    assert agent.llm == llm_client
    assert agent.registry == registry


def test_tool_registry_schemas():
    registry = ToolRegistry(None, None)
    schemas = registry.get_schemas("post_event_stats")

    # Should return schemas for all post_event_stats tools
    assert all(s["type"] == "function" for s in schemas)
    tool_names = {s["function"]["name"] for s in schemas}
    expected = {
        "fetch_page",
        "db_get_event",
        "db_find_fighter",
        "submit_fight_result",
        "submit_fight_stats",
        "flag_issue",
    }
    assert tool_names == expected


def test_tool_registry_dispatch_unknown_tool():
    registry = ToolRegistry(None, None)
    result = registry.dispatch("unknown_tool", {})
    assert result["is_error"] is True
    assert "Unknown tool" in result["result"]


def test_fetch_page_returns_chunks():
    fetcher = MagicMock()
    fetcher.fetch.side_effect = lambda url, max_chars, include_links=False: (
        "T", "x" * min(max_chars, 10000), max_chars < 10000)
    registry = ToolRegistry(None, fetcher)

    first = registry.dispatch("fetch_page", {"url": "https://www.ufc.com/rankings"})["result"]
    assert len(first["text"]) == 8000 and first["next_start"] == 8000
    second = registry.dispatch("fetch_page", {"url": "https://www.ufc.com/rankings", "start": 8000})["result"]
    assert len(second["text"]) == 2000 and "next_start" not in second


def tool_call(call_id, name, arguments):
    call = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))
    call.model_dump = lambda exclude_none: {"id": call_id, "type": "function",
                                            "function": {"name": name, "arguments": arguments}}
    return call


def llm_reply(*tool_calls):
    # A final reply carries text; an empty one is retried by the loop.
    content = None if tool_calls else "done"
    return SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=list(tool_calls)))


def test_run_agent_sends_tool_results_and_counts_calls():
    llm = MagicMock()
    llm.chat.side_effect = [
        llm_reply(tool_call("c1", "flag_issue", '{"entity": "fight", "reason": "x"}'),
                  tool_call("c2", "flag_issue", "not json")),
        llm_reply(),
    ]
    registry = MagicMock()
    registry.dispatch.return_value = {"result": {"status": "ok"}, "is_error": False}
    run_log = MagicMock()

    outcome = run_agent(llm, registry, "post_event_stats", "prompt", {"event_id": "e1"}, run_log)

    assert outcome == {"steps": 2, "hit_step_limit": False, "empty_reply": False,
                       "ok_calls": {"flag_issue": 1}, "failed_calls": {"flag_issue": 1}}
    messages = llm.chat.call_args_list[1].args[0]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "tool"]
    assert [c["id"] for c in messages[2]["tool_calls"]] == ["c1", "c2"]
    assert run_log.tool_step.call_count == 2


def test_run_agent_treats_error_results_as_failures():
    llm = MagicMock()
    llm.chat.side_effect = [llm_reply(tool_call("c1", "submit_rankings", "{}")), llm_reply()]
    registry = MagicMock()
    registry.dispatch.return_value = {"result": {"status": "error", "errors": ["bad"]}, "is_error": False}

    outcome = run_agent(llm, registry, "sync_rankings", "prompt", {}, MagicMock())
    assert outcome["failed_calls"] == {"submit_rankings": 1}
    assert outcome["ok_calls"] == {}
