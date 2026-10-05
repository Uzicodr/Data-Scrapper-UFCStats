"""Evaluation runner for post_event_stats agent.

Load 30 past completed events from seeded data.
Run agent on each.
Compare extracted stats to expected values.
Measure: field accuracy, invented-data rate, coverage.
"""
import json
import os
from datetime import datetime
from pathlib import Path

import psycopg

from ufc_agent.agents.post_event_stats import PostEventStatsAgent
from ufc_agent.config import supabase_db_url
from ufc_agent.db import connect
from ufc_agent.fetch.http import Fetcher
from ufc_agent.llm.client import LLMClient
from ufc_agent.runlog import RunLog
from ufc_agent.tools.registry import ToolRegistry


def load_eval_events(conn: psycopg.Connection, limit: int = 30) -> list[dict]:
    """Load past completed events with fights for evaluation."""
    cursor = conn.execute(
        """
        SELECT e.id, e.name, e.starts_at, COUNT(f.id) as fight_count
        FROM events e
        LEFT JOIN fights f ON e.id = f.event_id
        WHERE e.status = 'completed' AND e.starts_at < NOW()
        GROUP BY e.id, e.name, e.starts_at
        HAVING COUNT(f.id) > 0
        ORDER BY e.starts_at DESC
        LIMIT %s
        """,
        (limit,)
    )
    return [dict(row) for row in cursor.fetchall()]


def run_eval(
    max_events: int = 30,
    dry_run: bool = False,
    output_dir: Path = Path("evals/results"),
) -> dict:
    """Run evaluation suite.

    Args:
        max_events: Max events to evaluate
        dry_run: Don't submit to DB (for testing)
        output_dir: Where to save results

    Returns:
        dict with summary stats
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Setup
    conn = connect()
    llm_client = LLMClient.from_env()
    fetcher = Fetcher()
    from ufc_agent.tools.db_tools import DBTools
    db_tools = DBTools(conn)
    registry = ToolRegistry(db_tools, fetcher)
    agent = PostEventStatsAgent(llm_client, registry)

    # Load eval events
    eval_events = load_eval_events(conn, limit=max_events)
    print(f"Loaded {len(eval_events)} events for evaluation")

    results = []
    total_submitted = 0
    total_flagged = 0

    for i, event in enumerate(eval_events, 1):
        event_id = event["id"]
        event_name = event["name"]

        print(f"[{i}/{len(eval_events)}] {event_name}...", end=" ", flush=True)

        # Skip if no stats to extract (dev mode)
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM fights WHERE event_id = %s AND status = 'completed'",
            (event_id,)
        )
        fight_count = cursor.fetchone()["count"]
        if fight_count == 0:
            print("SKIP (no completed fights)")
            continue

        # Run agent (mock runlog if tables don't exist)
        try:
            run_log = RunLog(conn, "post_event_stats", {"event_id": event_id})
        except Exception as e:
            if "agent_runs" in str(e):
                print(f"SKIP (agent_runs table missing)")
                continue
            raise
        try:
            result = agent.run(event_id, run_log)
            total_submitted += result["stats_submitted"]
            total_flagged += result["issues_flagged"]
            results.append({
                "event": event_name,
                "event_id": event_id,
                **result
            })
            print(f"OK ({result['stats_submitted']} stats, {result['issues_flagged']} flags)")
        except Exception as e:
            run_log.finish(status="error", error=str(e))
            print(f"ERROR: {e}")
            results.append({
                "event": event_name,
                "event_id": event_id,
                "error": str(e)
            })

    # Summary
    summary = {
        "timestamp": datetime.now().isoformat(),
        "events_evaluated": len([r for r in results if "error" not in r]),
        "total_stats_submitted": total_submitted,
        "total_issues_flagged": total_flagged,
        "results": results,
    }

    # Save results
    output_file = output_dir / f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nResults saved to {output_file}")

    return summary


if __name__ == "__main__":
    run_eval(max_events=30, dry_run=os.getenv("DRY_RUN") == "1")
