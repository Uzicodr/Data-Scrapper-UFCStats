"""Test agent components."""
from unittest.mock import MagicMock, patch

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
    tool_names = {s["name"] for s in schemas}
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
