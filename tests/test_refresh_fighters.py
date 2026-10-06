"""Test fighter-profile validation, the submit_fighter_profile tool and the refresh_fighters agent."""
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tests.test_sync_upcoming import llm_reply, tool_call
from ufc_agent.agents.refresh_fighters import RefreshFightersAgent
from ufc_agent.schemas.models import FighterProfile
from ufc_agent.tools.registry import ToolRegistry

URL = "http://ufcstats.com/fighter-details/2f181c0467965b98"


def profile(**overrides):
    return {
        "fighter_id": "b0e4a02b-c184-5ca1-b79b-64ecd4035dd0", "profile_url": URL, "name_on_page": "Brendan Allen",
        "nickname": "All In", "record": "27-7-0", "height": "6' 2\"", "weight": "185 lbs.", "reach": '75"',
        "stance": "Orthodox", "dob": "Dec 28, 1995", "slpm": "3.75", "str_acc": "52%", "sapm": "3.78",
        "str_def": "47%", "td_avg": "1.52", "td_acc": "41%", "td_def": "57%", "sub_avg": "1.1",
        **overrides,
    }


def test_fighter_profile_accepts_page_values():
    fighter = FighterProfile(**profile())
    assert fighter.record_counts() == (27, 7, 0, 0)
    assert FighterProfile(**profile(record="20-5-1 (2 NC)")).record_counts() == (20, 5, 1, 2)


def test_fighter_profile_accepts_blanks():
    fighter = FighterProfile(**profile(height="--", reach="--", stance="--", dob="--", str_acc="--", slpm="--"))
    assert fighter.height == "--"


@pytest.mark.parametrize("overrides, message", [
    ({"profile_url": "https://www.ufc.com/athlete/brendan-allen"}, "profile_url"),
    ({"record": "27 wins"}, "record"),
    ({"height": "74"}, "height"),
    ({"reach": "750"}, "reach"),
    ({"stance": "Boxer"}, "stance"),
    ({"dob": "yesterday"}, "dob"),
    ({"str_acc": "0.52"}, "str_acc"),
    ({"td_def": "157%"}, "td_def"),
    ({"slpm": "fast"}, "slpm"),
])
def test_fighter_profile_rejects_bad_values(overrides, message):
    with pytest.raises(ValidationError, match=message):
        FighterProfile(**profile(**overrides))


def test_submit_fighter_profile_tool_validates_before_db():
    db_tools = MagicMock()
    registry = ToolRegistry(db_tools, None, search=object())
    result = registry.dispatch("submit_fighter_profile", profile(record="unknown"))["result"]
    assert result["status"] == "error"
    db_tools.submit_fighter_profile.assert_not_called()


def test_agent_batches_fighters_and_counts_updates():
    fighters = [{"fighter_id": f"id-{i}", "name": f"F{i}", "profile_url": URL} for i in range(7)]
    replies = []
    for batch in (fighters[:5], fighters[5:]):
        replies.append(llm_reply(*[tool_call(f["fighter_id"], "submit_fighter_profile",
                                             profile(fighter_id=f["fighter_id"])) for f in batch]))
        replies.append(llm_reply())
    llm = MagicMock()
    llm.chat.side_effect = replies
    db_tools = MagicMock()
    db_tools.submit_fighter_profile.return_value = {"status": "ok"}
    run_log = MagicMock(input_tokens=0, output_tokens=0)

    result = RefreshFightersAgent(llm, ToolRegistry(db_tools, None, search=object())).run(run_log, fighters)

    assert result["updated"] == 7
    assert result["status"] == "completed"
    assert llm.chat.call_count == 4  # two batches, two turns each
    assert db_tools.submit_fighter_profile.call_count == 7
