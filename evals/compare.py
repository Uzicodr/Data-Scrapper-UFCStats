"""Compare post_event_stats submissions with the seeded ground truth.

Ground truth is the completed fights already in the database (seeded from the old ufcstats scraper).
Each fight is scored on its result fields (winner, method, round, time, title fight) and on each
fighter's kd, sig_str, td and sub. Fights are matched by ufcstats fight id, fighters by fighter id.
"""
from ufc_agent.normalize import to_int

RESULT_FIELDS = ("winner_fighter_id", "method", "round", "time", "is_title_fight")
STAT_FIELDS = ("kd", "sig_str", "td", "sub")
# Seeded raw_payload keys for each stat, stored as {"red": "1", "blue": "0"}.
PAYLOAD_KEYS = {"kd": "kd", "sig_str": "str", "td": "td", "sub": "sub"}


def ground_truth(conn, event_id) -> dict:
    """{fight source_id: expected values} for the event's completed fights."""
    rows = conn.execute(
        "SELECT source_id, red_fighter_id, blue_fighter_id, winner_fighter_id, method, result_round, result_time, "
        "is_title_fight, raw_payload FROM fights WHERE event_id = %s AND status = 'completed'",
        (event_id,),
    ).fetchall()
    truth = {}
    for row in rows:
        payload = row["raw_payload"] or {}
        stats = {}
        for corner in ("red", "blue"):
            fighter_id = row[f"{corner}_fighter_id"]
            if fighter_id is None:
                continue  # fighter missing from the seeded fighter list; nothing to compare
            stats[str(fighter_id)] = {
                field: to_int((payload.get(key) or {}).get(corner)) for field, key in PAYLOAD_KEYS.items()
            }
        truth[row["source_id"]] = {
            "winner_fighter_id": str(row["winner_fighter_id"]) if row["winner_fighter_id"] else None,
            "method": row["method"],
            "round": row["result_round"],
            "time": row["result_time"],
            "is_title_fight": row["is_title_fight"],
            "stats": stats,
        }
    return truth


def compare_event(truth: dict, submitted_fights: list[dict]) -> dict:
    """Score one event. Values missing from the ground truth (None) are not scored, except the winner."""
    submitted = {fight["source_id"]: fight for fight in submitted_fights}
    fields_total = fields_correct = 0
    mismatches = []

    for source_id, expected in truth.items():
        got = submitted.get(source_id)
        checks = [(field, expected[field], got and got[field]) for field in RESULT_FIELDS]
        for fighter_id, expected_stats in expected["stats"].items():
            got_stats = (got or {}).get("stats", {}).get(fighter_id, {})
            checks += [(f"{fighter_id[:8]}.{field}", expected_stats[field], got_stats.get(field))
                       for field in STAT_FIELDS]
        for field, want, have in checks:
            # A missing winner is a real answer (draw or no contest); other missing values are unknown.
            if want is None and field != "winner_fighter_id":
                continue
            fields_total += 1
            if got is not None and want == have:
                fields_correct += 1
            else:
                mismatches.append({"fight": source_id, "field": field, "expected": want,
                                   "got": have if got is not None else "fight not submitted"})

    invented = sorted(set(submitted) - set(truth))
    return {
        "fights_expected": len(truth),
        "fights_submitted": len(submitted),
        "fights_missing": len(set(truth) - set(submitted)),
        "fights_invented": len(invented),
        "invented_fight_ids": invented,
        "fields_total": fields_total,
        "fields_correct": fields_correct,
        "accuracy": round(fields_correct / fields_total, 4) if fields_total else 0.0,
        "mismatches": mismatches,
    }


def aggregate(event_metrics: list[dict]) -> dict:
    fields_total = sum(m["fields_total"] for m in event_metrics)
    fields_correct = sum(m["fields_correct"] for m in event_metrics)
    fights_expected = sum(m["fights_expected"] for m in event_metrics)
    return {
        "events": len(event_metrics),
        "fights_expected": fights_expected,
        "fights_missing": sum(m["fights_missing"] for m in event_metrics),
        "fights_invented": sum(m["fights_invented"] for m in event_metrics),
        "fields_total": fields_total,
        "fields_correct": fields_correct,
        "field_accuracy": round(fields_correct / fields_total, 4) if fields_total else 0.0,
    }
