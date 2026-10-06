"""Test rankings validation, name matching and the sync_rankings tool."""
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from ufc_agent.agents.sync_rankings import SYSTEM_PROMPT
from ufc_agent.schemas.models import Rankings
from ufc_agent.tools.db_tools import DBTools, NameIndex
from ufc_agent.tools.registry import ToolRegistry

FIGHTERS = [
    {"id": "f-van", "name": "Joshua Van", "aliases": None},
    {"id": "f-proch", "name": "Jiri Prochazka", "aliases": None},
    {"id": "f-sumu", "name": "Su Mudaerji", "aliases": ["Sumudaerji"]},
    {"id": "f-silva-1", "name": "Bruno Silva", "aliases": None},
    {"id": "f-silva-2", "name": "Bruno Gustavo Silva", "aliases": None},
] + [{"id": f"f-{i}", "name": f"Fighter Number{i}", "aliases": None} for i in range(1, 16)]


def ranked(names):
    return [{"name": name} for name in names]


FIFTEEN = [f"Fighter Number{i}" for i in range(1, 16)]


def test_rankings_accepts_full_division():
    rankings = Rankings(division="Flyweight", champion={"name": "Joshua Van"}, ranked=ranked(FIFTEEN),
                        sources=["https://www.ufc.com/rankings"])
    assert len(rankings.ranked) == 15


@pytest.mark.parametrize("kwargs, message", [
    ({"division": "Cruiserweight"}, "Unknown division"),
    ({"ranked": ranked(FIFTEEN[:14])}, "Expected 15"),
    ({"division": "Men's Pound-for-Pound", "champion": {"name": "Joshua Van"}}, "no champion"),
    ({"champion": {"name": "Fighter Number1"}}, "more than once"),
    ({"sources": ["ufc.com"]}, "must be URLs"),
])
def test_rankings_rejects_bad_lists(kwargs, message):
    args = {"division": "Flyweight", "ranked": ranked(FIFTEEN), "sources": ["https://www.ufc.com/rankings"],
            **kwargs}
    with pytest.raises(ValidationError, match=message):
        Rankings(**args)


def test_name_index_resolves_accents_aliases_and_ambiguity():
    index = NameIndex(FIGHTERS)
    assert index.resolve("Jiří Procházka") == "f-proch"
    assert index.resolve("Sumudaerji") == "f-sumu"
    assert index.resolve("Bruno Silva") == "f-silva-1"  # exact name wins
    assert index.resolve("B. Silva") is None
    assert index.resolve("Nobody Known") is None


def test_name_index_checks_fighter_id_against_name():
    index = NameIndex(FIGHTERS)
    assert index.name_matches("f-proch", "Jiří Procházka")
    assert not index.name_matches("f-van", "Jiří Procházka")
    assert not index.name_matches("missing", "Joshua Van")


def make_db_tools():
    db_tools = DBTools(MagicMock())
    db_tools._name_index = NameIndex(FIGHTERS)
    return db_tools


def test_submit_rankings_replaces_division():
    db_tools = make_db_tools()
    result = db_tools.submit_rankings(Rankings(
        division="Flyweight", champion={"name": "Joshua Van"}, ranked=ranked(FIFTEEN),
        sources=["https://www.ufc.com/rankings"]))

    assert result["status"] == "ok"
    conn = db_tools.conn
    conn.execute.assert_called_once_with("DELETE FROM rankings WHERE division = %s", ("Flyweight",))
    rows = conn.cursor.return_value.executemany.call_args.args[1]
    assert [(r[2], r[3], r[4]) for r in rows[:2]] == [(None, "f-van", True), (1, "f-1", False)]
    conn.commit.assert_called_once()


def test_submit_rankings_reports_unmatched_names_without_writing():
    db_tools = make_db_tools()
    names = FIFTEEN[:14] + ["Unknown Debutant"]
    result = db_tools.submit_rankings(Rankings(division="Flyweight", ranked=ranked(names),
                                               sources=["https://www.ufc.com/rankings"]))

    assert result["status"] == "error"
    assert "#15 Unknown Debutant" in result["errors"][0]
    db_tools.conn.execute.assert_not_called()


def test_submit_rankings_accepts_matching_fighter_id_override():
    db_tools = make_db_tools()
    entries = ranked(FIFTEEN[:14]) + [{"name": "Prochazka", "fighter_id": "f-proch"}]
    result = db_tools.submit_rankings(Rankings(division="Flyweight", ranked=entries,
                                               sources=["https://www.ufc.com/rankings"]))
    assert result["status"] == "ok"


def test_registry_submit_rankings_returns_validation_errors():
    registry = ToolRegistry(MagicMock(), None)
    result = registry.dispatch("submit_rankings", {
        "division": "Flyweight", "ranked": ranked(FIFTEEN[:3]), "sources": ["https://www.ufc.com/rankings"]})
    assert result["result"]["status"] == "error"
    assert "Expected 15" in result["result"]["errors"][0]
    registry.db_tools.submit_rankings.assert_not_called()


def test_sync_rankings_tools_and_prompt():
    schemas = ToolRegistry(None, None).get_schemas("sync_rankings")
    assert {s["function"]["name"] for s in schemas} == {
        "fetch_page", "web_search", "db_find_fighter", "submit_rankings", "flag_issue"}
    assert "https://www.ufc.com/rankings" in SYSTEM_PROMPT
