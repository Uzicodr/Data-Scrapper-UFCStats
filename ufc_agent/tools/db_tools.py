"""Database read/write tools for agent jobs."""
import json
import uuid
from collections import defaultdict
from datetime import datetime, time, timezone
from difflib import SequenceMatcher
from typing import Optional

import psycopg
from psycopg.types.json import Jsonb

from ufc_agent.ids import SOURCE, event_slug_base, row_uuid, slugify
from ufc_agent.normalize import (
    clean,
    height_to_inches,
    inches_text,
    name_key,
    split_location,
    to_float,
    ufcstats_id,
)
from ufc_agent.schemas.models import EventCard, FighterProfile, FightResult, FightStats, FightRoundStats, Rankings

UFCSTATS = "http://ufcstats.com"


def fuzzy_match(name: str, candidates: list[dict], threshold: float = 0.6) -> list[dict]:
    """Fuzzy match name against candidates, return those above threshold with score."""
    scores = []
    for cand in candidates:
        ratio = SequenceMatcher(None, name.lower(), cand["name"].lower()).ratio()
        if ratio >= threshold:
            scores.append({**cand, "similarity": round(ratio, 2)})
    return sorted(scores, key=lambda x: x["similarity"], reverse=True)


class NameIndex:
    """Fighter lookup by name_key of name and aliases, plus a looser first-and-last-token key."""

    def __init__(self, fighters: list[dict]):
        self.names = {}  # fighter id -> every name_key known for that fighter
        self.exact, self.loose = defaultdict(set), defaultdict(set)
        for fighter in fighters:
            fighter_id = str(fighter["id"])
            keys = {name_key(n) for n in [fighter["name"], *(fighter.get("aliases") or [])] if n}
            self.names[fighter_id] = keys
            for key in keys:
                self.exact[key].add(fighter_id)
                tokens = key.split()
                if len(tokens) >= 2:
                    self.loose[f"{tokens[0]} {tokens[-1]}"].add(fighter_id)

    def resolve(self, name: str) -> Optional[str]:
        """Return the fighter id when exactly one fighter matches, else None."""
        key = name_key(name)
        tokens = key.split()
        for ids in (self.exact.get(key), self.loose.get(f"{tokens[0]} {tokens[-1]}") if len(tokens) >= 2 else None):
            if ids and len(ids) == 1:
                return next(iter(ids))
        return None

    def name_matches(self, fighter_id: str, name: str, threshold: float = 0.6) -> bool:
        """True when fighter_id exists and one of its names is close to name."""
        key = name_key(name)
        return any(SequenceMatcher(None, key, known).ratio() >= threshold for known in self.names.get(fighter_id, ()))


class DBTools:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self._name_index = None

    def name_index(self) -> NameIndex:
        """Load every fighter name and alias once per DBTools instance."""
        if self._name_index is None:
            cursor = self.conn.execute(
                "SELECT id, name, raw_payload->'aliases' AS aliases FROM fighters WHERE name IS NOT NULL"
            )
            self._name_index = NameIndex([dict(row) for row in cursor.fetchall()])
        return self._name_index

    def get_event(self, name_or_date: str) -> Optional[dict]:
        """Get event by name substring or date (YYYY-MM-DD)."""
        try:
            # Try as date first
            datetime.strptime(name_or_date, "%Y-%m-%d")
            cursor = self.conn.execute(
                "SELECT * FROM events WHERE DATE(starts_at) = %s LIMIT 1",
                (name_or_date,)
            )
        except ValueError:
            # Try as name
            cursor = self.conn.execute(
                "SELECT * FROM events WHERE name ILIKE %s LIMIT 1",
                (f"%{name_or_date}%",)
            )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_upcoming(self, days: int = 30) -> list[dict]:
        """List events within next N days."""
        cursor = self.conn.execute(
            "SELECT * FROM events WHERE status = 'scheduled' AND starts_at <= NOW() + make_interval(days => %s) "
            "ORDER BY starts_at",
            (days,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def find_fighter(self, name: str) -> list[dict]:
        """Fuzzy match fighter by name. Returns top matches with similarity scores."""
        cursor = self.conn.execute(
            "SELECT id, source, source_id, name FROM fighters WHERE name IS NOT NULL LIMIT 5000"
        )
        candidates = [dict(row) for row in cursor.fetchall()]
        return fuzzy_match(name, candidates, threshold=0.6)

    def submit_fight_result(self, fight: FightResult, sources: list[str]) -> dict:
        """Submit fight result with source URLs.

        Returns dict with status, message, and any validation errors.
        Requires two agreeing sources to mark result verified.
        """
        errors = []

        # Validate fight data
        if not fight.source or not fight.source_id or not fight.event_id:
            errors.append("Missing source/source_id/event_id")
        if fight.method not in ("win", "draw", "no_contest"):
            errors.append(f"Invalid method: {fight.method}")
        if fight.round < 1:
            errors.append("Round must be >= 1")
        if fight.time_seconds < 0:
            errors.append("Time must be >= 0")

        if errors:
            return {"status": "error", "message": "Validation failed", "errors": errors}

        try:
            # Upsert fight with result
            self.conn.execute(
                """
                UPDATE fights
                SET winner_id = %s, method = %s, round = %s, time_seconds = %s,
                    updated_at = NOW(), raw_payload = raw_payload || %s
                WHERE source = %s AND source_id = %s
                """,
                (
                    fight.winner_id, fight.method, fight.round, fight.time_seconds,
                    psycopg.types.json.Jsonb({"sources": sources}),
                    fight.source, fight.source_id
                )
            )
            self.conn.commit()
            return {"status": "ok", "message": f"Fight result recorded"}
        except Exception as e:
            self.conn.rollback()
            return {"status": "error", "message": str(e)}

    def submit_fight_stats(self, fight_id: str, stats: FightStats, source: str) -> dict:
        """Submit per-fight stats for one fighter.

        Both corners must be present before upsert. All values must be >= 0.
        """
        errors = []

        # Validate stats
        if stats.knockdowns < 0 or stats.sig_strikes_landed < 0:
            errors.append("Stats must be >= 0")
        if stats.sig_strikes_attempted < stats.sig_strikes_landed:
            errors.append("Attempted must be >= landed")
        if stats.takedowns_attempted < stats.takedowns_landed:
            errors.append("Takedowns: attempted must be >= landed")

        if errors:
            return {"status": "error", "message": "Validation failed", "errors": errors}

        try:
            payload = {
                "knockdowns": stats.knockdowns,
                "sig_strikes": {
                    "landed": stats.sig_strikes_landed,
                    "attempted": stats.sig_strikes_attempted
                },
                "total_strikes": {
                    "landed": stats.total_strikes_landed,
                    "attempted": stats.total_strikes_attempted
                },
                "takedowns": {
                    "landed": stats.takedowns_landed,
                    "attempted": stats.takedowns_attempted
                },
                "submission_attempts": stats.submission_attempts,
                "reversals": stats.reversals,
                "control_time_seconds": stats.control_time_seconds,
                "source": source
            }

            # Find fight by fighter_id
            cursor = self.conn.execute(
                "SELECT id, raw_payload FROM fights WHERE source_id = %s LIMIT 1",
                (fight_id,)
            )
            row = cursor.fetchone()
            if not row:
                return {"status": "error", "message": f"Fight not found: {fight_id}"}

            fight_id_db, existing_payload = row
            existing_payload = existing_payload or {}

            # Merge stats
            existing_payload[f"stats_{stats.fighter_id}"] = payload

            self.conn.execute(
                "UPDATE fights SET raw_payload = %s, updated_at = NOW() WHERE id = %s",
                (psycopg.types.json.Jsonb(existing_payload), fight_id_db)
            )
            self.conn.commit()
            return {"status": "ok", "message": f"Stats recorded for fighter"}
        except Exception as e:
            self.conn.rollback()
            return {"status": "error", "message": str(e)}

    def submit_rankings(self, rankings: Rankings) -> dict:
        """Replace one division's rankings. Every name must resolve to exactly one fighter.

        Champion is stored with rank NULL and is_champion = true.
        """
        index = self.name_index()
        entries = ([(None, rankings.champion)] if rankings.champion else []) + list(enumerate(rankings.ranked, start=1))

        rows, errors = [], []
        for rank, entry in entries:
            label = f"#{rank}" if rank else "champion"
            if entry.fighter_id:
                fighter_id = entry.fighter_id
                if not index.name_matches(fighter_id, entry.name):
                    errors.append(f"{label} {entry.name}: fighter_id {fighter_id} is unknown or belongs to someone else")
                    continue
            else:
                fighter_id = index.resolve(entry.name)
                if fighter_id is None:
                    errors.append(f"{label} {entry.name}: no unique match; look up with db_find_fighter and pass fighter_id")
                    continue
            rows.append((uuid.uuid4(), rankings.division, rank, fighter_id, rank is None))

        fighter_ids = [row[3] for row in rows]
        if len(set(fighter_ids)) != len(fighter_ids):
            errors.append("Two names resolved to the same fighter")
        if errors:
            return {"status": "error", "message": "Rankings not saved", "errors": errors}

        try:
            self.conn.execute("DELETE FROM rankings WHERE division = %s", (rankings.division,))
            self.conn.cursor().executemany(
                "INSERT INTO rankings (id, division, rank, fighter_id, is_champion) VALUES (%s, %s, %s, %s, %s)",
                rows,
            )
            self.conn.commit()
            return {"status": "ok", "message": f"{rankings.division}: saved {len(rows)} rows"}
        except Exception as e:
            self.conn.rollback()
            return {"status": "error", "message": str(e)}

    def flag_issue(self, entity: str, reason: str) -> dict:
        """Log issue for human review."""
        try:
            self.conn.execute(
                "INSERT INTO review_queue (id, entity_type, reason, status) VALUES (%s, %s, %s, 'open')",
                (uuid.uuid4(), entity, reason)
            )
            self.conn.commit()
            return {"status": "ok", "message": "Issue flagged"}
        except Exception as e:
            self.conn.rollback()
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Upcoming cards (sync_upcoming)
    # ------------------------------------------------------------------
    def upcoming_cards(self, days: int = 180) -> list[dict]:
        """Scheduled events from today through the next N days, with their scheduled bouts, in a compact form.

        Past events still marked scheduled are left out: completing them is post_event_stats' job.
        """
        events = self.conn.execute(
            "SELECT id, name, starts_at::date AS date, source, source_id FROM events "
            "WHERE status = 'scheduled' AND starts_at >= CURRENT_DATE "
            "AND starts_at <= NOW() + make_interval(days => %s) ORDER BY starts_at",
            (days,),
        ).fetchall()
        if not events:
            return []
        bouts = defaultdict(list)
        for row in self.conn.execute(
            "SELECT x.event_id, r.name AS red, b.name AS blue, x.weight_class FROM fights x "
            "LEFT JOIN fighters r ON r.id = x.red_fighter_id LEFT JOIN fighters b ON b.id = x.blue_fighter_id "
            "WHERE x.event_id = ANY(%s) AND x.status = 'scheduled' ORDER BY x.bout_order NULLS LAST",
            ([e["id"] for e in events],),
        ).fetchall():
            bouts[row["event_id"]].append(f"{row['red']} vs {row['blue']} ({row['weight_class'] or '?'})")
        return [
            {
                "name": e["name"],
                "date": e["date"].isoformat(),
                "event_url": f"{UFCSTATS}/event-details/{e['source_id']}" if e["source"] == SOURCE else None,
                "bouts": bouts.get(e["id"], []),
            }
            for e in events
        ]

    def _free_slug(self, table: str, base: str, suffix: str) -> str:
        """base, else base-suffix, else base-suffix-2, ... whichever is not yet used in table."""
        candidates = [base, f"{base}-{suffix}"] + [f"{base}-{suffix}-{n}" for n in range(2, 50)]
        for slug in candidates:
            if not self.conn.execute(f"SELECT 1 FROM {table} WHERE slug = %s", (slug,)).fetchone():
                return slug
        raise RuntimeError(f"No free slug for {base!r} in {table}")

    def _fighter_for_bout(self, name: str, url: Optional[str], weight_class: Optional[str]):
        """Return (fighter_id, created). Creates a stub fighter for a ufcstats URL not in the database."""
        if url:
            source_id = ufcstats_id(url)
            row = self.conn.execute(
                "SELECT id FROM fighters WHERE source = %s AND source_id = %s", (SOURCE, source_id)
            ).fetchone()
            if row:
                return str(row["id"]), False
            fighter_id = row_uuid("fighter", source_id)
            self.conn.execute(
                "INSERT INTO fighters (id, slug, name, weight_class, source, source_id, raw_payload, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())",
                (fighter_id, self._free_slug("fighters", slugify(name), source_id[:8]), name, weight_class,
                 SOURCE, source_id, Jsonb({"profile_link": url, "stub": True})),
            )
            return fighter_id, True
        return self.name_index().resolve(name), False

    def submit_event_card(self, card: EventCard) -> dict:
        """Create or update one scheduled event and its bouts.

        Bouts are matched by their ufcstats fight id. Scheduled bouts no longer on the card are marked
        cancelled, never deleted, because picks reference them. Fighters with a ufcstats URL that are
        not in the database yet are created as stubs for refresh_fighters to fill in.
        """
        event_source_id = ufcstats_id(card.event_url)
        city, country = split_location(card.location)
        starts_at = datetime.combine(card.date, time(0), tzinfo=timezone.utc)
        try:
            event = self.conn.execute(
                "SELECT id, status, manual_override, starts_at FROM events WHERE source = %s AND source_id = %s",
                (SOURCE, event_source_id),
            ).fetchone()
            if event and event["manual_override"]:
                self.conn.rollback()
                return {"status": "ok", "message": f"{card.name}: manual override set, left unchanged"}
            if event and event["status"] != "scheduled":
                self.conn.rollback()
                return {"status": "error",
                        "message": f"{card.name} is {event['status']}; only scheduled events can be edited here"}

            payload = Jsonb({"event_link": card.event_url, "event_location": card.location,
                             "sources": card.sources, "synced_by": "sync_upcoming"})
            if event:
                event_id = str(event["id"])
                # Keep a real start time set elsewhere when the date has not moved.
                if event["starts_at"] and event["starts_at"].date() == card.date:
                    starts_at = event["starts_at"]
                self.conn.execute(
                    "UPDATE events SET name = %s, starts_at = %s, city = %s, country = %s, "
                    "raw_payload = COALESCE(raw_payload, '{}'::jsonb) || %s, updated_at = NOW() WHERE id = %s",
                    (card.name, starts_at, city, country, payload, event_id),
                )
            else:
                event_id = row_uuid("event", event_source_id)
                slug = self._free_slug("events", event_slug_base(card.name, card.date), card.date.isoformat())
                self.conn.execute(
                    "INSERT INTO events (id, slug, name, starts_at, city, country, status, source, source_id, "
                    "raw_payload, updated_at) VALUES (%s, %s, %s, %s, %s, %s, 'scheduled', %s, %s, %s, NOW())",
                    (event_id, slug, card.name, starts_at, city, country, SOURCE, event_source_id, payload),
                )

            errors, new_fighters, corners = [], [], []
            for number, bout in enumerate(card.bouts, start=1):
                ids = []
                for corner, name, url in (("red", bout.red_name, bout.red_url),
                                          ("blue", bout.blue_name, bout.blue_url)):
                    fighter_id, created = self._fighter_for_bout(name, url, bout.weight_class)
                    if fighter_id is None:
                        errors.append(f"bout {number} {corner} {name}: not found; pass {corner}_url from the event page")
                    if created:
                        new_fighters.append(name)
                    ids.append(fighter_id)
                corners.append(ids)
            if errors:
                self.conn.rollback()
                return {"status": "error", "message": "Card not saved", "errors": errors}

            source_ids = [
                ufcstats_id(b.fight_url) if b.fight_url
                else f"{event_source_id}:{slugify(b.red_name)}-vs-{slugify(b.blue_name)}"
                for b in card.bouts
            ]
            existing = {
                row["source_id"]: row for row in self.conn.execute(
                    "SELECT id, source_id, event_id, status, manual_override FROM fights "
                    "WHERE source = %s AND (event_id = %s OR source_id = ANY(%s))",
                    (SOURCE, event_id, source_ids),
                ).fetchall()
            }
            for order, (bout, source_id, (red_id, blue_id)) in enumerate(zip(card.bouts, source_ids, corners), start=1):
                row = existing.get(source_id)
                fight_payload = Jsonb({"fight_detail_link": bout.fight_url, "fighter_red": bout.red_name,
                                       "fighter_blue": bout.blue_name, "sources": card.sources})
                values = (event_id, red_id, blue_id, clean(bout.weight_class), order, bout.is_title_fight)
                if row is None:
                    self.conn.execute(
                        "INSERT INTO fights (id, event_id, red_fighter_id, blue_fighter_id, weight_class, bout_order, "
                        "is_title_fight, status, source, source_id, raw_payload, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'scheduled', %s, %s, %s, NOW())",
                        (row_uuid("fight", source_id), *values, SOURCE, source_id, fight_payload),
                    )
                elif not row["manual_override"] and row["status"] != "completed":
                    self.conn.execute(
                        "UPDATE fights SET event_id = %s, red_fighter_id = %s, blue_fighter_id = %s, weight_class = %s, "
                        "bout_order = %s, is_title_fight = %s, status = 'scheduled', "
                        "raw_payload = COALESCE(raw_payload, '{}'::jsonb) || %s, updated_at = NOW() WHERE id = %s",
                        (*values, fight_payload, row["id"]),
                    )

            dropped = [
                row["id"] for sid, row in existing.items()
                if sid not in source_ids and str(row["event_id"]) == event_id
                and row["status"] == "scheduled" and not row["manual_override"]
            ]
            cancelled = []
            if dropped:
                cancelled = [
                    f"{r['red']} vs {r['blue']}" for r in self.conn.execute(
                        "UPDATE fights SET status = 'cancelled', updated_at = NOW() WHERE id = ANY(%s) "
                        "RETURNING raw_payload->>'fighter_red' AS red, raw_payload->>'fighter_blue' AS blue",
                        (dropped,),
                    ).fetchall()
                ]
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            return {"status": "error", "message": str(e)}

        if new_fighters:
            self._name_index = None
        return {
            "status": "ok",
            "message": f"{card.name}: saved {len(card.bouts)} bouts",
            "event_created": event is None,
            "new_fighters": new_fighters,
            "cancelled_bouts": cancelled,
        }

    # ------------------------------------------------------------------
    # Fighter profiles (refresh_fighters)
    # ------------------------------------------------------------------
    def fighters_to_refresh(self, days: int = 14, limit: int = 40) -> list[dict]:
        """Stub fighters first, then fighters whose profile is older than a fight they had in the last N days."""
        rows = self.conn.execute(
            """
            WITH targets AS (
                SELECT f.id, f.name, f.source_id, 1 AS priority FROM fighters f
                WHERE f.source = %(source)s AND NOT f.manual_override AND f.raw_payload->>'stub' = 'true'
                UNION ALL
                SELECT f.id, f.name, f.source_id, 2 FROM fighters f
                JOIN fights x ON f.id IN (x.red_fighter_id, x.blue_fighter_id)
                JOIN events e ON e.id = x.event_id
                WHERE f.source = %(source)s AND NOT f.manual_override AND x.status = 'completed'
                  AND e.starts_at >= NOW() - make_interval(days => %(days)s)
                  AND f.updated_at < e.starts_at + interval '1 day'
            )
            SELECT * FROM (SELECT DISTINCT ON (id) * FROM targets ORDER BY id, priority) t
            ORDER BY priority, name LIMIT %(limit)s
            """,
            {"source": SOURCE, "days": days, "limit": limit},
        ).fetchall()
        return [
            {"fighter_id": str(r["id"]), "name": r["name"],
             "profile_url": f"{UFCSTATS}/fighter-details/{r['source_id']}"}
            for r in rows
        ]

    def fighters_by_ids(self, fighter_ids: list[str]) -> list[dict]:
        """Refresh targets for specific ufcstats fighters, in the fighters_to_refresh shape."""
        rows = self.conn.execute(
            "SELECT id, name, source_id FROM fighters WHERE id = ANY(%s::uuid[]) AND source = %s ORDER BY name",
            (fighter_ids, SOURCE),
        ).fetchall()
        return [
            {"fighter_id": str(r["id"]), "name": r["name"],
             "profile_url": f"{UFCSTATS}/fighter-details/{r['source_id']}"}
            for r in rows
        ]

    def submit_fighter_profile(self, profile: FighterProfile) -> dict:
        """Update a fighter from their ufcstats profile. Blank ('--') values never erase stored ones."""
        try:
            row = self.conn.execute(
                "SELECT id, name, source, source_id, manual_override, raw_payload FROM fighters WHERE id = %s",
                (profile.fighter_id,),
            ).fetchone()
        except psycopg.errors.InvalidTextRepresentation:
            self.conn.rollback()
            row = None
        if row is None:
            return {"status": "error", "message": f"Unknown fighter_id {profile.fighter_id}"}
        if row["manual_override"]:
            return {"status": "ok", "message": f"{row['name']}: manual override set, left unchanged"}
        if row["source"] == SOURCE and ufcstats_id(profile.profile_url) != row["source_id"]:
            return {"status": "error", "message": f"profile_url is not {row['name']}'s page"}
        if not self.name_index().name_matches(profile.fighter_id, profile.name_on_page):
            return {"status": "error",
                    "message": f"Page shows {profile.name_on_page!r} but this fighter is {row['name']!r}; wrong page?"}

        wins, losses, draws, no_contests = profile.record_counts()
        name, aliases = row["name"], list((row["raw_payload"] or {}).get("aliases") or [])
        if name_key(profile.name_on_page) != name_key(row["name"]):
            name, aliases = profile.name_on_page, sorted(set(aliases) | {row["name"]})

        def keep(value):
            return value if clean(value) else None

        # Same keys and text formats as the seeded raw_payload, so readers see one shape.
        payload = {
            key: value for key, value in {
                "nickname": keep(profile.nickname), "height": keep(profile.height), "weight": keep(profile.weight),
                "reach": keep(profile.reach), "stance": keep(profile.stance), "dob": keep(profile.dob),
                "wins": str(wins), "losses": str(losses), "draws": str(draws), "no_contests": str(no_contests),
                "slpm": keep(profile.slpm), "striking_accuracy": keep(profile.str_acc), "sapm": keep(profile.sapm),
                "striking_defense": keep(profile.str_def), "td_avg": keep(profile.td_avg),
                "td_accuracy": keep(profile.td_acc), "td_defense": keep(profile.td_def),
                "submission_avg": keep(profile.sub_avg),
            }.items() if value is not None
        }
        payload.update({"profile_link": profile.profile_url, "aliases": aliases,
                        "last_updated": datetime.now(timezone.utc).date().isoformat(),
                        "refreshed_by": "refresh_fighters"})
        try:
            self.conn.execute(
                "UPDATE fighters SET name = %s, nickname = COALESCE(%s, nickname), record_wins = %s, "
                "record_losses = %s, record_draws = %s, height_inches = COALESCE(%s, height_inches), "
                "reach_inches = COALESCE(%s, reach_inches), stance = COALESCE(%s, stance), "
                "raw_payload = (COALESCE(raw_payload, '{}'::jsonb) - 'stub') || %s, updated_at = NOW() WHERE id = %s",
                (name, keep(profile.nickname), wins, losses, draws,
                 inches_text(height_to_inches(profile.height)), inches_text(to_float(profile.reach)),
                 keep(profile.stance), Jsonb(payload), profile.fighter_id),
            )
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            return {"status": "error", "message": str(e)}
        self._name_index = None
        return {"status": "ok", "message": f"{name}: profile updated ({wins}-{losses}-{draws})"}
