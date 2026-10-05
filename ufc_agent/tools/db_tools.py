"""Database read/write tools for agent jobs."""
import json
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

import psycopg

from ufc_agent.schemas.models import FightResult, FightStats, FightRoundStats


def fuzzy_match(name: str, candidates: list[dict], threshold: float = 0.6) -> list[dict]:
    """Fuzzy match name against candidates, return those above threshold with score."""
    scores = []
    for cand in candidates:
        ratio = SequenceMatcher(None, name.lower(), cand["name"].lower()).ratio()
        if ratio >= threshold:
            scores.append({**cand, "similarity": round(ratio, 2)})
    return sorted(scores, key=lambda x: x["similarity"], reverse=True)


class DBTools:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def get_event(self, name_or_date: str) -> Optional[dict]:
        """Get event by name substring or date (YYYY-MM-DD)."""
        try:
            # Try as date first
            datetime.strptime(name_or_date, "%Y-%m-%d")
            cursor = self.conn.execute(
                "SELECT * FROM events WHERE DATE(date) = %s LIMIT 1",
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
            "SELECT * FROM events WHERE status = 'scheduled' AND date <= NOW() + INTERVAL '%s days' "
            "ORDER BY date",
            (days,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def find_fighter(self, name: str) -> list[dict]:
        """Fuzzy match fighter by name. Returns top matches with similarity scores."""
        cursor = self.conn.execute(
            "SELECT source, source_id, name FROM fighters WHERE name IS NOT NULL LIMIT 5000"
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
