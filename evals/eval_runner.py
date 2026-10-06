"""Evaluate the post_event_stats agent against seeded ground truth.

The agent runs on past completed events with DBTools(dry_run=True): every submission goes through
the same checks and writes, then is rolled back, so the ground truth is never touched. Run logs are
kept under the job name "eval_post_event_stats".

Run with: python -m evals.eval_runner [--events 30] [--skip 0] [--target 0.98]
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from evals.compare import aggregate, compare_event, ground_truth
from ufc_agent.agents.post_event_stats import PostEventStatsAgent
from ufc_agent.db import connect
from ufc_agent.fetch.http import Fetcher
from ufc_agent.llm.client import LLMClient, QuotaExhausted, format_duration
from ufc_agent.runlog import RunLog
from ufc_agent.tools.db_tools import DBTools
from ufc_agent.tools.registry import ToolRegistry

RESULTS_DIR = Path("evals/results")


def load_eval_events(conn, limit=30, skip=0) -> list[dict]:
    """Most recent completed ufcstats events whose fights carry seeded stats."""
    return [dict(row) for row in conn.execute(
        """
        SELECT e.id, e.name, e.starts_at::date AS date FROM events e
        WHERE e.source = 'ufcstats' AND e.status = 'completed'
          AND EXISTS (SELECT 1 FROM fights x WHERE x.event_id = e.id AND x.status = 'completed'
                      AND x.raw_payload ? 'kd')
        ORDER BY e.starts_at DESC OFFSET %s LIMIT %s
        """,
        (skip, limit),
    ).fetchall()]


def run_eval(events=30, skip=0, target=0.98) -> dict:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    db_tools = DBTools(conn, dry_run=True)
    agent = PostEventStatsAgent(LLMClient.from_env(), ToolRegistry(db_tools, Fetcher()))

    eval_events = load_eval_events(conn, limit=events, skip=skip)
    print(f"Evaluating {len(eval_events)} events (dry run, nothing is saved)", flush=True)

    per_event, stopped = [], None
    for number, event in enumerate(eval_events, start=1):
        truth = ground_truth(conn, event["id"])
        db_tools.recorded.clear()
        run_log = RunLog(conn, "eval_post_event_stats", {"event_id": str(event["id"])})
        try:
            run = agent.run(str(event["id"]), run_log)
        except QuotaExhausted as exc:
            run_log.finish(status="error", error=str(exc))
            stopped = f"Stopped before event {number}: {exc}. Retry in {format_duration(exc.retry_in_seconds)}."
            print(stopped)
            break
        except Exception as exc:
            run_log.finish(status="error", error=str(exc))
            run = {"status": "error", "error": str(exc)}

        submissions = [r for r in db_tools.recorded if "fights" in r]
        metrics = compare_event(truth, submissions[-1]["fights"] if submissions else [])
        flags = [r["flag_issue"] for r in db_tools.recorded if "flag_issue" in r]
        per_event.append({"event": event["name"], "date": str(event["date"]), "run_status": run["status"],
                          "tokens_input": run.get("tokens_input"), "flags": flags, **metrics})
        print(f"[{number:>2}/{len(eval_events)}] {event['date']} {event['name'][:45]:<45} "
              f"{run['status']:<9} fights {metrics['fights_submitted']}/{metrics['fights_expected']} "
              f"accuracy {metrics['accuracy']:.1%}", flush=True)

    summary = {**aggregate(per_event), "target": target, "events_planned": len(eval_events), "stopped": stopped}
    summary["passed"] = (not stopped and summary["field_accuracy"] >= target and summary["fights_invented"] == 0)
    output = RESULTS_DIR / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    output.write_text(json.dumps({"summary": summary, "events": per_event}, indent=2, default=str), encoding="utf-8")

    print("\n" + json.dumps(summary, indent=2))
    print(f"Details: {output}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Evaluate post_event_stats against seeded results")
    parser.add_argument("--events", type=int, default=30)
    parser.add_argument("--skip", type=int, default=0, help="skip the N most recent events")
    parser.add_argument("--target", type=float, default=0.98, help="field accuracy needed to pass")
    args = parser.parse_args()
    run_eval(events=args.events, skip=args.skip, target=args.target)


if __name__ == "__main__":
    main()
