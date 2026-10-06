"""Test the eval comparison against ground truth."""
from evals.compare import aggregate, compare_event

TRUTH = {
    "f1": {"winner_fighter_id": "aaaaaaaa-1", "method": "KO/TKO", "round": 2, "time": "1:48", "is_title_fight": False,
           "stats": {"aaaaaaaa-1": {"kd": 1, "sig_str": 11, "td": 0, "sub": 1},
                     "bbbbbbbb-2": {"kd": 0, "sig_str": 13, "td": 1, "sub": 0}}},
    "f2": {"winner_fighter_id": None, "method": "S-DEC", "round": 3, "time": "5:00", "is_title_fight": False,
           "stats": {"cccccccc-3": {"kd": 0, "sig_str": 40, "td": 2, "sub": 0}}},
}


def submitted(fight_id, **overrides):
    fight = {"source_id": fight_id, **{k: v for k, v in TRUTH[fight_id].items()}}
    fight["stats"] = {fid: dict(stats) for fid, stats in TRUTH[fight_id]["stats"].items()}
    fight.update(overrides)
    return fight


def test_perfect_submission_scores_every_field():
    metrics = compare_event(TRUTH, [submitted("f1"), submitted("f2")])
    assert metrics["fields_total"] == 5 + 8 + 5 + 4
    assert metrics["accuracy"] == 1.0
    assert metrics["mismatches"] == []


def test_wrong_stat_and_missing_fight_are_counted():
    wrong = submitted("f1")
    wrong["stats"]["bbbbbbbb-2"]["sig_str"] = 31
    metrics = compare_event(TRUTH, [wrong])
    assert metrics["fights_missing"] == 1
    assert metrics["fields_correct"] == 12
    assert {"fight": "f1", "field": "bbbbbbbb.sig_str", "expected": 13, "got": 31} in metrics["mismatches"]


def test_invented_fight_is_reported():
    metrics = compare_event(TRUTH, [submitted("f1"), submitted("f2"), {"source_id": "f9", "stats": {}}])
    assert metrics["fights_invented"] == 1
    assert metrics["invented_fight_ids"] == ["f9"]


def test_aggregate():
    a = compare_event(TRUTH, [submitted("f1"), submitted("f2")])
    b = compare_event(TRUTH, [submitted("f1")])
    totals = aggregate([a, b])
    assert totals["fields_total"] == 44
    assert totals["fields_correct"] == 22 + 13
    assert totals["field_accuracy"] == round(35 / 44, 4)
