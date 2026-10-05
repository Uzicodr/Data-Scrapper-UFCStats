"""Convert the MongoDB JSON export into one SQL seed file for the Octapulse backend schema.

Usage:
    python scripts/mongo_json_to_sql.py [export_dir] [output.sql]

Defaults: data/mongo_export -> data/seed.sql. Every row uses source = 'ufcstats' and a
deterministic UUID, and every insert is ON CONFLICT DO NOTHING, so the file is safe to re-run.
"""
import datetime
import json
import re
import sys
import uuid
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ufc_agent.normalize import (  # noqa: E402
    clean,
    fight_round,
    fight_time,
    height_to_inches,
    name_key,
    parse_date,
    to_float,
    to_int,
    ufcstats_id,
)

SOURCE = "ufcstats"
UUID_NAMESPACE = uuid.UUID("6f1d3c52-6a8e-4d55-9a57-3b8f4c0e2a11")
BATCH = 500
NON_DIVISION_WEIGHT_CLASSES = {"Open Weight", "Catch Weight", "Super Heavyweight"}


def row_uuid(kind, source_id):
    return str(uuid.uuid5(UUID_NAMESPACE, f"{SOURCE}:{kind}:{source_id}"))


def slugify(text):
    return "-".join(name_key(text).split())


def strip_mongo_id(doc):
    return {key: value for key, value in doc.items() if key != "_id"}


def inches_text(value):
    """71.0 -> '71', 71.5 -> '71.5' (backend stores inches as text)."""
    if value is None:
        return None
    return str(int(value)) if float(value).is_integer() else str(value)


def unique_slug(base, suffix, taken):
    slug = base or suffix
    if slug in taken:
        slug = f"{base}-{suffix}"
    taken.add(slug)
    return slug


def event_slug_base(name, event_date):
    """Matches Cito's style: 'ufc-332', 'ufc-fight-night-october-11-2026'."""
    numbered = re.match(r"UFC (\d+)\b", name)
    if numbered:
        return f"ufc-{numbered.group(1)}"
    if name.startswith("UFC Fight Night"):
        return f"ufc-fight-night-{event_date.strftime('%B-%d-%Y').lower()}"
    return slugify(name)


def split_location(location):
    parts = [part.strip() for part in (location or "").split(",") if part.strip()]
    city = parts[0] if parts else None
    country = parts[-1] if len(parts) > 1 else None
    return city, country


# ---------------------------------------------------------------------------
# Build rows
# ---------------------------------------------------------------------------
def build_fighters(fighter_docs, event_docs):
    # Most recent weight class each fighter fought at, from the completed events.
    latest_class = {}
    for event in sorted(event_docs, key=lambda e: parse_date(e.get("event_date")) or datetime.date.min):
        for fight in event.get("fights") or []:
            weight_class = clean(fight.get("weight_class"))
            if not weight_class or weight_class in NON_DIVISION_WEIGHT_CLASSES:
                continue
            for link in fight.get("fighter_profile_links") or []:
                latest_class[ufcstats_id(link)] = weight_class

    # The old scraper keyed fighters by name, so a renamed fighter left a stale copy
    # with the same profile link. Keep the newest copy and record the old names.
    newest, names_seen = {}, defaultdict(set)
    for doc in sorted(fighter_docs, key=lambda d: d.get("last_updated") or ""):
        source_id = ufcstats_id(doc.get("profile_link"))
        if not source_id:
            continue
        newest[source_id] = doc
        names_seen[source_id].add(f"{clean(doc.get('first_name')) or ''} {clean(doc.get('last_name')) or ''}".strip())

    taken_slugs, rows = set(), []
    for source_id, doc in sorted(newest.items(), key=lambda item: item[1].get("last_updated") or "", reverse=True):
        name = f"{clean(doc.get('first_name')) or ''} {clean(doc.get('last_name')) or ''}".strip()
        aliases = sorted(n for n in names_seen[source_id] if name_key(n) != name_key(name))
        payload = strip_mongo_id(doc)
        if aliases:
            payload["aliases"] = aliases
        rows.append({
            "id": row_uuid("fighter", source_id),
            "slug": unique_slug(slugify(name), source_id[:8], taken_slugs),
            "name": name or source_id,
            "nickname": clean(doc.get("nickname")),
            "record_wins": to_int(doc.get("wins")),
            "record_losses": to_int(doc.get("losses")),
            "record_draws": to_int(doc.get("draws")),
            "weight_class": latest_class.get(source_id),
            "height_inches": inches_text(height_to_inches(doc.get("height"))),
            "reach_inches": inches_text(to_float(doc.get("reach"))),
            "stance": clean(doc.get("stance")),
            "country": None,
            "source": SOURCE,
            "source_id": source_id,
            "raw_payload": payload,
            "updated_at": doc.get("last_updated") or datetime.date.today().isoformat(),
            "_aliases": aliases,
        })
    return rows


def build_events_and_fights(past_docs, upcoming_docs, fighter_ids):
    by_source_id = {}
    for status, docs in (("scheduled", upcoming_docs), ("completed", past_docs)):  # completed wins
        for doc in docs:
            source_id = ufcstats_id(doc.get("event_link"))
            event_date = parse_date(doc.get("event_date"))
            if source_id and event_date and clean(doc.get("event_name")):
                by_source_id[source_id] = (status, event_date, doc)

    taken_slugs, events, fights = set(), [], []
    for source_id, (status, event_date, doc) in sorted(by_source_id.items(), key=lambda item: item[1][1]):
        name = clean(doc["event_name"])
        city, country = split_location(doc.get("event_location"))
        event_id = row_uuid("event", source_id)
        events.append({
            "id": event_id,
            "slug": unique_slug(event_slug_base(name, event_date), event_date.isoformat(), taken_slugs),
            "name": name,
            "starts_at": f"{event_date.isoformat()}T00:00:00Z",
            "venue": None,
            "city": city,
            "country": country,
            "status": status,
            "source": SOURCE,
            "source_id": source_id,
            "raw_payload": {k: v for k, v in strip_mongo_id(doc).items() if k != "fights"},
            "updated_at": doc.get("last_updated") or datetime.date.today().isoformat(),
        })

        for fight in doc.get("fights") or []:
            fight_source_id = ufcstats_id(fight.get("fight_detail_link"))
            links = fight.get("fighter_profile_links") or []
            if not fight_source_id or len(links) < 2:
                continue
            red_id = fighter_ids.get(ufcstats_id(links[0]))
            blue_id = fighter_ids.get(ufcstats_id(links[1]))
            winner = clean(fight.get("winner"))
            winner_id = None
            if status == "completed" and winner:
                if name_key(winner) == name_key(fight.get("fighter_red")):
                    winner_id = red_id
                elif name_key(winner) == name_key(fight.get("fighter_blue")):
                    winner_id = blue_id
            fights.append({
                "id": row_uuid("fight", fight_source_id),
                "event_id": event_id,
                "red_fighter_id": red_id,
                "blue_fighter_id": blue_id,
                "weight_class": clean(fight.get("weight_class")),
                "card_section": None,
                "bout_order": to_int(fight.get("fight_order")),
                "is_title_fight": bool(fight.get("is_championship_fight")),
                "status": status,
                "winner_fighter_id": winner_id,
                "method": clean(fight.get("method")) if status == "completed" else None,
                "result_round": fight_round(fight.get("round")) if status == "completed" else None,
                "result_time": fight_time(fight.get("time")) if status == "completed" else None,
                "source": SOURCE,
                "source_id": fight_source_id,
                "raw_payload": fight,
                "updated_at": doc.get("last_updated") or datetime.date.today().isoformat(),
            })
    return events, fights


def build_rankings(ranking_docs, fighters):
    exact, loose = defaultdict(set), defaultdict(set)
    for fighter in fighters:
        for name in [fighter["name"], *fighter["_aliases"]]:
            key = name_key(name)
            exact[key].add(fighter["id"])
            tokens = key.split()
            if len(tokens) >= 2:
                loose[f"{tokens[0]} {tokens[-1]}"].add(fighter["id"])

    def resolve(name):
        key = name_key(name)
        tokens = key.split()
        for ids in (exact.get(key), loose.get(f"{tokens[0]} {tokens[-1]}") if len(tokens) >= 2 else None):
            if ids and len(ids) == 1:
                return next(iter(ids))
        return None

    rows, unresolved = [], []
    for doc in ranking_docs:
        gender = (clean(doc.get("gender")) or "").lower()
        category = clean(doc.get("category"))
        if not category or gender not in ("men", "women"):
            continue
        if category == "Pound for Pound":
            division = "Men's Pound-for-Pound" if gender == "men" else "Women's Pound-for-Pound"
        else:
            division = category if gender == "men" else f"Women's {category}"

        names = [n for n in (clean(x) for x in doc.get("fighters") or []) if n]
        champion = clean(doc.get("champion"))
        if champion and names and name_key(names[0]) == name_key(champion):
            names = names[1:]
        # Cito style: champion has rank NULL and is_champion = true.
        ranked = ([(None, champion, True)] if champion else []) + [(i, n, False) for i, n in enumerate(names, start=1)]

        fetched_at = doc.get("last_updated") or datetime.date.today().isoformat()
        for rank, name, is_champion in ranked:
            fighter_id = resolve(name)
            if fighter_id is None:
                unresolved.append(f"{division} #{rank or 'C'} {name}")
                continue
            rows.append({
                "id": row_uuid("ranking", f"{division}:{rank or 'C'}:{fetched_at}"),
                "division": division,
                "rank": rank,
                "fighter_id": fighter_id,
                "is_champion": is_champion,
                "fetched_at": fetched_at,
            })
    return rows, unresolved


# ---------------------------------------------------------------------------
# SQL rendering
# ---------------------------------------------------------------------------
def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return sql_literal(json.dumps(value, ensure_ascii=False, sort_keys=True)) + "::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


def insert_statements(table, rows, columns):
    statements = []
    for start in range(0, len(rows), BATCH):
        values = ",\n".join(
            "(" + ", ".join(sql_literal(row[c]) for c in columns) + ")" for row in rows[start:start + BATCH]
        )
        statements.append(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES\n{values}\nON CONFLICT DO NOTHING;"
        )
    return statements


FIGHTER_COLUMNS = [
    "id", "slug", "name", "nickname", "record_wins", "record_losses", "record_draws", "weight_class",
    "height_inches", "reach_inches", "stance", "country", "source", "source_id", "raw_payload", "updated_at",
]
EVENT_COLUMNS = [
    "id", "slug", "name", "starts_at", "venue", "city", "country", "status", "source", "source_id",
    "raw_payload", "updated_at",
]
FIGHT_COLUMNS = [
    "id", "event_id", "red_fighter_id", "blue_fighter_id", "weight_class", "card_section", "bout_order",
    "is_title_fight", "status", "winner_fighter_id", "method", "result_round", "result_time", "source",
    "source_id", "raw_payload", "updated_at",
]
RANKING_COLUMNS = ["id", "division", "rank", "fighter_id", "is_champion", "fetched_at"]


def build_statements(export_dir):
    load = lambda name: json.loads((export_dir / f"{name}.json").read_text(encoding="utf-8"))  # noqa: E731
    fighter_docs, past, upcoming, ranking_docs = (
        load("fighterlogs"), load("pastevents"), load("upcomingevents"), load("rankings")
    )

    fighters = build_fighters(fighter_docs, past)
    fighter_ids = {f["source_id"]: f["id"] for f in fighters}
    events, fights = build_events_and_fights(past, upcoming, fighter_ids)
    rankings, unresolved = build_rankings(ranking_docs, fighters)

    statements = (
        insert_statements("fighters", fighters, FIGHTER_COLUMNS)
        + insert_statements("events", events, EVENT_COLUMNS)
        + insert_statements("fights", fights, FIGHT_COLUMNS)
        + insert_statements("rankings", rankings, RANKING_COLUMNS)
    )
    summary = {
        "fighters": len(fighters),
        "events": len(events),
        "events_scheduled": sum(e["status"] == "scheduled" for e in events),
        "fights": len(fights),
        "fights_missing_fighter": sum(f["red_fighter_id"] is None or f["blue_fighter_id"] is None for f in fights),
        "rankings": len(rankings),
        "rankings_unresolved": unresolved,
    }
    return statements, summary


def main():
    export_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/mongo_export")
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/seed.sql")
    statements, summary = build_statements(export_dir)
    output.write_text("BEGIN;\n\n" + "\n\n".join(statements) + "\n\nCOMMIT;\n", encoding="utf-8")
    print(f"Wrote {output} ({output.stat().st_size // 1024} KB, {len(statements)} statements)")
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
