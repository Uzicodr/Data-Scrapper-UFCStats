"""Test upcoming-card validation, the submit_event_card tool and the sync_upcoming agent."""
import datetime
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from ufc_agent.agents.sync_upcoming import SYSTEM_PROMPT, SyncUpcomingAgent
from ufc_agent.schemas.models import EventCard
from ufc_agent.tools.registry import ToolRegistry

FUTURE = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()


def bout(red="Brendan Allen", blue="Christian Leroy Duncan", **extra):
    return {
        "red_name": red, "blue_name": blue,
        "red_url": "http://ufcstats.com/fighter-details/2f181c0467965b98",
        "blue_url": "http://ufcstats.com/fighter-details/a93f94c923c3a9cb",
        "weight_class": "Middleweight", "fight_url": "http://ufcstats.com/fight-details/7db1a3dac7e343e7",
        **extra,
    }


def card(**overrides):
    return {
        "event_url": "http://ufcstats.com/event-details/7f98d9d5a10fa25c",
        "name": "UFC Fight Night: Allen vs. Duncan",
        "date": FUTURE,
        "location": "Las Vegas, Nevada, USA",
        "bouts": [bout()],
        "sources": ["http://ufcstats.com/event-details/7f98d9d5a10fa25c"],
        **overrides,
    }


def test_event_card_accepts_valid_card():
    event = EventCard(**card())
    assert event.date.isoformat() == FUTURE
    assert event.bouts[0].red_name == "Brendan Allen"
    assert not event.bouts[0].is_title_fight


@pytest.mark.parametrize("overrides, message", [
    ({"event_url": "https://www.ufc.com/event/ufc-fight-night"}, "event_url must be"),
    ({"date": "2020-01-01"}, "in the past"),
    ({"bouts": []}, "at least 1"),
    ({"bouts": [bout(blue="Brendan Allen")]}, "both corners"),
    ({"bouts": [bout(), bout(red="Brendan Allen", blue="Someone Else")]}, "more than one bout"),
    ({"bouts": [bout(fight_url="http://ufcstats.com/fighter-details/abc")]}, "fight_url"),
    ({"sources": ["ufcstats"]}, "must be URLs"),
])
def test_event_card_rejects_bad_cards(overrides, message):
    with pytest.raises(ValidationError, match=message):
        EventCard(**card(**overrides))


def test_submit_event_card_tool_returns_validation_errors_without_touching_db():
    db_tools = MagicMock()
    registry = ToolRegistry(db_tools, None, search=object())
    result = registry.dispatch("submit_event_card", card(date="2020-01-01"))
    assert result["result"]["status"] == "error"
    assert any("in the past" in e for e in result["result"]["errors"])
    db_tools.submit_event_card.assert_not_called()


def test_submit_event_card_tool_passes_valid_card_to_db():
    db_tools = MagicMock()
    db_tools.submit_event_card.return_value = {"status": "ok"}
    registry = ToolRegistry(db_tools, None, search=object())
    assert registry.dispatch("submit_event_card", card())["result"] == {"status": "ok"}
    assert db_tools.submit_event_card.call_args.args[0].name == "UFC Fight Night: Allen vs. Duncan"


def test_prompt_covers_links_and_cancellations():
    assert "include_links=true" in SYSTEM_PROMPT
    assert "[img:belt.png]" in SYSTEM_PROMPT
    assert "flag_issue" in SYSTEM_PROMPT


def tool_call(call_id, name, args):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)),
        model_dump=lambda **_: {"id": call_id, "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)}},
    )


def llm_reply(*calls):
    return SimpleNamespace(
        message=SimpleNamespace(content=None if calls else "done", tool_calls=list(calls)),
        provider="fake", model="fake", input_tokens=10, output_tokens=2, duration_ms=1, errors=[],
    )


def test_agent_runs_until_model_stops_and_reports_counts():
    llm = MagicMock()
    llm.chat.side_effect = [
        llm_reply(tool_call("1", "submit_event_card", card())),
        llm_reply(tool_call("2", "submit_event_card", card(date="2020-01-01"))),
        llm_reply(),
    ]
    db_tools = MagicMock()
    db_tools.submit_event_card.return_value = {"status": "ok", "message": "saved"}
    run_log = MagicMock(input_tokens=30, output_tokens=6)

    result = SyncUpcomingAgent(llm, ToolRegistry(db_tools, None, search=object())).run(
        run_log, today=datetime.date(2026, 10, 6))

    assert result["cards_saved"] == 1
    assert result["failed_submits"] == 1
    assert result["status"] == "completed"
    assert json.loads(llm.chat.call_args_list[0].args[0][1]["content"])["today"] == "2026-10-06"
    assert llm.chat.call_args_list[0].kwargs["reasoning_effort"] == "none"
    run_log.finish.assert_called_once()


def empty_reply():
    reply = llm_reply()
    reply.message.content = None
    return reply


def test_loop_retries_empty_reply_and_continues():
    from ufc_agent.agents.loop import run_agent

    llm = MagicMock()
    llm.chat.side_effect = [empty_reply(), llm_reply(tool_call("1", "flag_issue", {"entity": "event", "reason": "x"})),
                            llm_reply()]
    db_tools = MagicMock()
    db_tools.flag_issue.return_value = {"status": "ok"}
    outcome = run_agent(llm, ToolRegistry(db_tools, None, search=object()), "sync_upcoming", "p", {}, MagicMock())
    assert outcome["ok_calls"] == {"flag_issue": 1}
    assert outcome["empty_reply"] is False


def test_loop_stops_as_incomplete_after_repeated_empty_replies():
    from ufc_agent.agents.loop import EMPTY_REPLY_RETRIES, run_agent

    llm = MagicMock()
    llm.chat.side_effect = [empty_reply() for _ in range(EMPTY_REPLY_RETRIES + 1)]
    outcome = run_agent(llm, ToolRegistry(MagicMock(), None, search=object()), "sync_upcoming", "p", {}, MagicMock())
    assert outcome["empty_reply"] is True
    assert outcome["hit_step_limit"] is False
    assert llm.chat.call_count == EMPTY_REPLY_RETRIES + 1
