"""Test the ufc.com card digest, live result validation, and the live_event watcher."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tests.test_sync_upcoming import llm_reply, tool_call
from ufc_agent.agents.live_event import FETCH_FAILURES_BEFORE_SEARCH, LiveEventAgent, watch
from ufc_agent.fetch.http import FetchError
from ufc_agent.fetch.ufc_card import bout_line, card_fingerprint, has_result, parse_card
from ufc_agent.llm.client import QuotaExhausted
from ufc_agent.schemas.models import LiveResult, ufcstats_method
from ufc_agent.tools.db_tools import NameIndex
from ufc_agent.tools.registry import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures"
COMPLETED = (FIXTURES / "ufc_event_completed.html").read_text(encoding="utf-8")
UPCOMING = (FIXTURES / "ufc_event_upcoming.html").read_text(encoding="utf-8")
URL = "https://www.ufc.com/event/ufc-332"


def test_parse_completed_card():
    bouts = parse_card(COMPLETED)
    assert len(bouts) == 14 and all(has_result(b) for b in bouts)
    assert bout_line(bouts[1]) == ("Bantamweight Bout: Deiveson Figueiredo [Loss] vs Payton Talbott [Win] "
                                   "| KO/TKO | round 1 | 2:09")
    assert bouts[0]["method"] == "Decision - Unanimous" and bouts[0]["red_outcome"] == "Win"


def test_parse_upcoming_card_has_no_results():
    bouts = parse_card(UPCOMING)
    assert len(bouts) == 12 and not any(has_result(b) for b in bouts)
    assert bout_line(bouts[0]) == "Middleweight Bout: Brendan Allen vs Christian Leroy Duncan"


def test_fingerprint_changes_only_with_results():
    bouts = parse_card(COMPLETED)
    same = [dict(b) for b in bouts]
    assert card_fingerprint(bouts) == card_fingerprint(same)
    same[3]["method"] = "Submission"
    assert card_fingerprint(bouts) != card_fingerprint(same)


def test_ufcstats_method():
    assert [ufcstats_method(t) for t in ("Decision - Unanimous", "Decision - Split", "KO/TKO", "Submission",
                                         "No Contest", "Mystery")] == ["U-DEC", "S-DEC", "KO/TKO", "SUB",
                                                                       "Overturned", "Other"]


def live(**overrides):
    return {"event_id": "e1", "red_name": "Deiveson Figueiredo", "blue_name": "Payton Talbott",
            "winner": "Payton Talbott", "method": "KO/TKO", "round": 1, "time": "2:09", "source_url": URL,
            **overrides}


def test_live_result_validation():
    assert LiveResult(**live()).winner == "Payton Talbott"
    assert LiveResult(**live(winner="draw", method="Decision - Split", round=3, time="5:00"))
    with pytest.raises(ValidationError, match="winner"):
        LiveResult(**live(winner="Someone Else"))
    with pytest.raises(ValidationError, match="time"):
        LiveResult(**live(time="7:00"))


def test_name_matches_reordered_names():
    index = NameIndex([{"id": "w", "name": "Cong Wang", "aliases": None}])
    assert index.name_matches("w", "Wang Cong")
    assert not index.name_matches("w", "Natalia Silva")


def fake_agent(scheduled):
    db_tools = MagicMock()
    db_tools.recorded = []
    db_tools.live_bouts.side_effect = lambda event_id: [
        {"red": r, "blue": b, "status": "scheduled"} for r, b in scheduled]
    db_tools.submit_live_result.return_value = {"status": "ok"}
    llm = MagicMock()
    return LiveEventAgent(llm, ToolRegistry(db_tools, None, search=object())), llm, db_tools


def test_process_sends_only_finished_bouts_and_pending():
    agent, llm, db_tools = fake_agent([("Deiveson Figueiredo", "Payton Talbott")])
    llm.chat.side_effect = [llm_reply(tool_call("1", "submit_live_result", live())), llm_reply()]
    result = agent.process(MagicMock(), "e1", "UFC 332", URL, parse_card(COMPLETED))
    assert result == {"submitted": 1, "flagged": 0, "agent_ran": True}
    job_input = json.loads(llm.chat.call_args_list[0].args[0][1]["content"])
    assert len(job_input["card"]) == 14
    assert job_input["pending"] == ["Deiveson Figueiredo vs Payton Talbott"]
    assert llm.chat.call_args_list[0].kwargs["reasoning_effort"] == "none"


def test_process_skips_agent_without_results():
    agent, llm, _ = fake_agent([("Brendan Allen", "Christian Leroy Duncan")])
    assert agent.process(MagicMock(), "e1", "UFC FN", URL, parse_card(UPCOMING))["agent_ran"] is False
    llm.chat.assert_not_called()


def test_pending_excludes_bouts_recorded_in_shadow_mode():
    agent, _, db_tools = fake_agent([("A", "B"), ("C", "D")])
    db_tools.recorded.append({"live_result": {"bout": "A vs B"}})
    assert agent.pending_bouts("e1") == ["C vs D"]


class FakeAgent:
    def __init__(self, pending_after):
        self.calls, self.searches, self.pending_after = [], 0, pending_after

    def process(self, run_log, event_id, event_name, source_url, bouts):
        self.calls.append(sum(map(has_result, bouts)))
        return {"submitted": sum(map(has_result, bouts)), "flagged": 0, "agent_ran": any(map(has_result, bouts))}

    def search(self, run_log, event_id, event_name):
        self.searches += 1
        return {"submitted": 0, "flagged": 0, "agent_ran": True}

    def pending_bouts(self, event_id):
        return ["X vs Y"] if len(self.calls) < self.pending_after else []


def run_watch(agent, pages, **kwargs):
    pages = iter(pages)

    def fetch_html():
        page = next(pages)
        if isinstance(page, Exception):
            raise page
        return page

    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    return watch(agent, MagicMock(), "e1", "UFC 332", URL, fetch_html, poll_seconds=120, max_hours=1,
                 sleep=sleep, clock=lambda: clock["now"], **kwargs)


def test_watch_runs_agent_only_when_page_changes_and_stops_when_card_finished():
    agent = FakeAgent(pending_after=99)
    result = run_watch(agent, [UPCOMING, UPCOMING, COMPLETED, COMPLETED])
    assert agent.calls == [0, 14]  # unchanged second poll skipped
    assert result["stopped"] == "card_finished"
    assert result["polls"] == 3 and result["submitted"] == 14


def test_watch_stops_when_nothing_pending():
    agent = FakeAgent(pending_after=1)
    assert run_watch(agent, [COMPLETED])["stopped"] == "no_pending_bouts"


def test_watch_searches_after_repeated_fetch_failures():
    agent = FakeAgent(pending_after=99)
    failures = [FetchError("dns")] * (FETCH_FAILURES_BEFORE_SEARCH + 1) + [COMPLETED]
    result = run_watch(agent, failures)
    assert agent.searches == 1  # once at the third failure, not again within 10 minutes
    assert result["fetch_failures"] == FETCH_FAILURES_BEFORE_SEARCH + 1


def test_watch_survives_quota_exhaustion_and_retries_next_poll():
    agent = FakeAgent(pending_after=99)
    original = agent.process
    attempts = {"n": 0}

    def flaky(*args):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise QuotaExhausted("out", retry_in_seconds=600)
        return original(*args)

    agent.process = flaky
    result = run_watch(agent, [COMPLETED, COMPLETED])
    assert result["quota_waits"] == 1
    assert agent.calls == [14]  # same page processed on the next poll


def test_event_start_reads_earliest_section_timestamp():
    import datetime

    from ufc_agent.fetch.ufc_card import event_start

    html = """<body>
      <div class="c-event-fight-card-broadcaster__time tz-change-inner" data-timestamp="1791072000">8:00 PM</div>
      <div class="c-event-fight-card-broadcaster__time tz-change-inner" data-timestamp="1791057600">4:00 PM</div>
      <div class="c-listing-viewing-option__time" data-timestamp="1788368400">other event</div></body>"""
    assert event_start(html) == datetime.datetime(2026, 10, 3, 20, 0, tzinfo=datetime.timezone.utc)
    assert event_start("<body>no times</body>") is None


def test_pick_live_event_uses_watch_window():
    import datetime

    from ufc_agent.agents.live_event import pick_live_event

    start = datetime.datetime(2026, 10, 10, 21, 0, tzinfo=datetime.timezone.utc)
    events = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    starts = {"a": None, "b": start}
    assert pick_live_event(events, starts, start - datetime.timedelta(minutes=30)) is None
    assert pick_live_event(events, starts, start - datetime.timedelta(minutes=10))["id"] == "b"
    assert pick_live_event(events, starts, start + datetime.timedelta(hours=9))["id"] == "b"
    assert pick_live_event(events, starts, start + datetime.timedelta(hours=11)) is None


def test_find_live_event_stores_real_start_and_survives_fetch_errors():
    import datetime

    from ufc_agent.agents.live_event import find_live_event

    db_tools = MagicMock()
    db_tools.live_candidates.return_value = [{"id": "x", "name": "Gone", "slug": "missing"},
                                             {"id": "y", "name": "UFC FN", "slug": "ufc-fight-night-october-10-2026"}]
    page = '<div class="c-event-fight-card-broadcaster__time" data-timestamp="1791666000"></div>'
    fetcher = MagicMock()
    fetcher.get.side_effect = lambda url, use_cache: (_ for _ in ()).throw(FetchError("404")) if "missing" in url else page
    start = datetime.datetime.fromtimestamp(1791666000, datetime.timezone.utc)

    event, url = find_live_event(db_tools, fetcher, now=start)
    assert event["id"] == "y" and url == "https://www.ufc.com/event/ufc-fight-night-october-10-2026"
    db_tools.set_event_start.assert_called_once_with("y", start)

    event, report = find_live_event(db_tools, fetcher, now=start - datetime.timedelta(days=1))
    assert event is None and any("error" in r for r in report)
