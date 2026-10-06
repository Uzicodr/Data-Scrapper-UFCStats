"""Test completed-event result validation (post_event_stats)."""
import pytest
from pydantic import ValidationError

from ufc_agent.schemas.models import EventResults

EVENT_URL = "http://ufcstats.com/event-details/9d61d8cb1c354867"


def fighter(name, fid, kd=0, sig_str=10, td=0, sub=0):
    return {"name": name, "url": f"http://ufcstats.com/fighter-details/{fid}",
            "kd": kd, "sig_str": sig_str, "td": td, "sub": sub}


def fight(fight_id="228fefc6923c40a5", a="efb96bf3e9ada36f", b="2b6fc1c02736833d", **overrides):
    return {
        "fight_url": f"http://ufcstats.com/fight-details/{fight_id}", "outcome": "win",
        "fighters": [fighter("Song Yadong", a, kd=1, sig_str=11, sub=1), fighter("Umar Nurmagomedov", b, sig_str=13, td=1)],
        "weight_class": "Bantamweight", "method": "KO/TKO", "method_details": "Punch", "round": 2, "time": "1:48",
        **overrides,
    }


def results(fights=None, **overrides):
    return {"event_url": EVENT_URL, "fights": fights or [fight()], "sources": [EVENT_URL], **overrides}


def test_event_results_accepts_valid_results():
    parsed = EventResults(**results())
    assert parsed.fights[0].fighters[0].kd == 1
    assert parsed.fights[0].outcome == "win"


def test_event_results_accepts_draw_and_decision():
    parsed = EventResults(**results([fight(outcome="draw", method="S-DEC", method_details=None, round=3, time="5:00")]))
    assert parsed.fights[0].outcome == "draw"


@pytest.mark.parametrize("overrides, message", [
    ({"method": "KO/TKO Punch"}, "method"),
    ({"method": "U-DEC", "time": "4:59"}, "decision ends at 5:00"),
    ({"time": "6:10"}, "at most 5:00"),
    ({"round": 6}, "less than or equal to 5"),
    ({"outcome": "loss"}, "outcome"),
    ({"weight_class": "Bantamweight [img:perf.png]"}, "without \\[img"),
    ({"fight_url": "http://ufcstats.com/event-details/x1"}, "fight-details URL"),
    ({"fighters": [fighter("A", "a1")]}, "at least 2"),
])
def test_event_results_rejects_bad_fights(overrides, message):
    with pytest.raises(ValidationError, match=message):
        EventResults(**results([fight(**overrides)]))


def test_event_results_rejects_negative_stats():
    bad = fight()
    bad["fighters"][0]["sig_str"] = -1
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        EventResults(**results([bad]))


def test_event_results_rejects_fighter_in_two_fights():
    with pytest.raises(ValidationError, match="more than one fight"):
        EventResults(**results([fight(), fight(fight_id="73cb6c4e060c4ee9", b="c41d426cc7326d1b")]))


def test_event_results_rejects_bad_event_url():
    with pytest.raises(ValidationError, match="event_url"):
        EventResults(**results(event_url="https://www.ufc.com/event/ufc-322"))
