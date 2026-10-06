"""Watch one event while it happens and record each result as it is posted.

The watcher is code: every poll it fetches the ufc.com event page, builds the bout digest
(ufc_agent/fetch/ufc_card.py) and hashes it. Only when the hash changes does the agent run, with the
finished bouts and the bouts the database still has scheduled. Results are saved as provisional;
post_event_stats overwrites them with ufcstats' verified results and stats the next day.

If the page cannot be fetched several polls in a row, the agent searches instead, at most every
10 minutes, because grounded search has a small daily quota.

Run by hand:
    python -m ufc_agent.agents.live_event --event-id <uuid> [--url <ufc.com event URL>] [--shadow]
Replay a saved page (always shadow):
    python -m ufc_agent.agents.live_event --event-id <uuid> --html-file page.html
Scheduled (GitHub Actions, hourly): find the event whose window includes now, store its real start
time from ufc.com, and watch it; exits at once when no event is on:
    python -m ufc_agent.agents.live_event --auto [--max-hours 5.8]
"""
import argparse
import datetime
import json
import time
from pathlib import Path

from ufc_agent.agents.loop import run_agent
from ufc_agent.config import PROJECT_ROOT
from ufc_agent.fetch.http import FetchError
from ufc_agent.fetch.ufc_card import bout_line, card_fingerprint, event_start, has_result, parse_card
from ufc_agent.llm.client import LLMClient, QuotaExhausted
from ufc_agent.runlog import RunLog
from ufc_agent.tools.registry import ToolRegistry

UFC_EVENT_URL = "https://www.ufc.com/event/{slug}"
POLL_SECONDS = 120
MAX_HOURS = 8
FETCH_FAILURES_BEFORE_SEARCH = 3
SEARCH_EVERY_SECONDS = 600
SHADOW_DIR = PROJECT_ROOT / "data" / "live_shadow"
# --auto watches an event from this long before its first card section starts ...
LEAD_MINUTES = 15
# ... until this long after it, unless every bout is finished sooner.
WATCH_WINDOW_HOURS = 10

SYSTEM_PROMPT = """You record UFC results while an event is happening.

The job input has:
- card: one line per finished bout, e.g. "Bantamweight Bout: A [Loss] vs B [Win] | KO/TKO | round 1 | 2:09".
- pending: bouts the database still has scheduled.

For each card line whose bout is in pending, call submit_live_result with event_id and source_url from
the job input, red_name = the first fighter in the line, blue_name = the second fighter, winner = the
fighter marked [Win] (or "draw" when marked [Draw], "no contest" when marked [NC] or the method says No
Contest), and method, round and time exactly as printed. You may submit several bouts in one turn.

Skip card lines whose bout is not in pending, and lines where neither fighter is marked. If
submit_live_result says no bout matches, call flag_issue (entity "fight") once with the card line.
Never guess a winner, method, round or time.
"""

SEARCH_PROMPT = """You record UFC results while an event is happening. The live results page cannot be
loaded right now.

Call web_search once for the latest results of the event in the job input. For each bout in pending that
the search answer reports as finished, with its winner, method, round and time, call submit_live_result
with event_id from the job input and a source_url from the search sources. Skip any bout the answer does
not report completely. Never guess.
"""


class LiveEventAgent:
    def __init__(self, llm_client: LLMClient, registry: ToolRegistry):
        self.llm = llm_client
        self.registry = registry
        self.db_tools = registry.db_tools

    def pending_bouts(self, event_id):
        """Scheduled bouts, minus any this watch already recorded in shadow mode (where the database never changes)."""
        recorded = {r["live_result"]["bout"] for r in self.db_tools.recorded if "live_result" in r}
        return [f"{b['red']} vs {b['blue']}" for b in self.db_tools.live_bouts(event_id)
                if b["status"] == "scheduled" and f"{b['red']} vs {b['blue']}" not in recorded]

    def process(self, run_log, event_id, event_name, source_url, bouts) -> dict:
        """Run the agent on the finished bouts of one page. Returns tool-call counts."""
        lines = [bout_line(b) for b in bouts if has_result(b)]
        pending = self.pending_bouts(event_id)
        if not lines or not pending:
            return {"submitted": 0, "flagged": 0, "agent_ran": False}
        job_input = {"event_id": event_id, "event": event_name, "source_url": source_url,
                     "card": lines, "pending": pending}
        outcome = run_agent(self.llm, self.registry, "live_event", SYSTEM_PROMPT, job_input, run_log,
                            max_steps=len(lines) + 4, reasoning_effort="none")
        return {"submitted": outcome["ok_calls"].get("submit_live_result", 0),
                "flagged": outcome["ok_calls"].get("flag_issue", 0), "agent_ran": True}

    def search(self, run_log, event_id, event_name) -> dict:
        pending = self.pending_bouts(event_id)
        if not pending:
            return {"submitted": 0, "flagged": 0, "agent_ran": False}
        job_input = {"event_id": event_id, "event": event_name, "pending": pending}
        outcome = run_agent(self.llm, self.registry, "live_event_search", SEARCH_PROMPT, job_input, run_log,
                            max_steps=len(pending) + 4, reasoning_effort="low")
        return {"submitted": outcome["ok_calls"].get("submit_live_result", 0),
                "flagged": outcome["ok_calls"].get("flag_issue", 0), "agent_ran": True}


def watch(agent, run_log, event_id, event_name, source_url, fetch_html, poll_seconds=POLL_SECONDS,
          max_hours=MAX_HOURS, sleep=time.sleep, clock=time.monotonic, single_pass=False) -> dict:
    """Poll until no bout is pending, the page shows every bout finished, or max_hours pass."""
    stats = {"polls": 0, "page_changes": 0, "agent_runs": 0, "searches": 0, "submitted": 0, "flagged": 0,
             "fetch_failures": 0, "quota_waits": 0}
    deadline = clock() + max_hours * 3600
    last_fingerprint, failures, last_search = None, 0, float("-inf")
    stopped = "max_hours"

    while clock() < deadline:
        stats["polls"] += 1
        bouts = None
        try:
            bouts = parse_card(fetch_html())
            failures = 0
        except FetchError:
            failures += 1
            stats["fetch_failures"] += 1

        try:
            if bouts is not None:
                fingerprint = card_fingerprint(bouts)
                if fingerprint != last_fingerprint:
                    stats["page_changes"] += 1
                    result = agent.process(run_log, event_id, event_name, source_url, bouts)
                    last_fingerprint = fingerprint
                    stats["agent_runs"] += result["agent_ran"]
                    stats["submitted"] += result["submitted"]
                    stats["flagged"] += result["flagged"]
            elif failures >= FETCH_FAILURES_BEFORE_SEARCH and clock() - last_search >= SEARCH_EVERY_SECONDS:
                last_search = clock()
                result = agent.search(run_log, event_id, event_name)
                stats["searches"] += 1
                stats["submitted"] += result["submitted"]
                stats["flagged"] += result["flagged"]
        except QuotaExhausted:
            # Keep watching: the page is re-read next poll and processed once a provider is back.
            stats["quota_waits"] += 1

        if not agent.pending_bouts(event_id):
            stopped = "no_pending_bouts"
            break
        if bouts and all(has_result(b) for b in bouts) and last_fingerprint == card_fingerprint(bouts):
            stopped = "card_finished"
            break
        if single_pass:
            stopped = "single_pass"
            break
        sleep(poll_seconds)

    return {**stats, "stopped": stopped}


def pick_live_event(candidates, starts, now):
    """The first candidate whose watch window contains now. starts maps event id -> real start or None."""
    for event in candidates:
        start = starts.get(str(event["id"]))
        if start and start - datetime.timedelta(minutes=LEAD_MINUTES) <= now <= start + datetime.timedelta(
                hours=WATCH_WINDOW_HOURS):
            return event
    return None


def find_live_event(db_tools, fetcher, now=None):
    """Read each nearby event's real start from ufc.com, store it, and return (event, url) to watch or (None, report)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    candidates, starts, report = db_tools.live_candidates(), {}, []
    for event in candidates:
        url = UFC_EVENT_URL.format(slug=event["slug"])
        try:
            start = event_start(fetcher.get(url, use_cache=False))
        except FetchError as exc:
            start = None
            report.append({"event": event["name"], "url": url, "error": str(exc)})
        if start:
            db_tools.set_event_start(str(event["id"]), start)
            report.append({"event": event["name"], "url": url, "starts_at": start.isoformat()})
        starts[str(event["id"])] = start
    event = pick_live_event(candidates, starts, now)
    if event is None:
        return None, report
    return event, UFC_EVENT_URL.format(slug=event["slug"])


def main():
    from ufc_agent.db import connect
    from ufc_agent.fetch.http import Fetcher
    from ufc_agent.tools.db_tools import DBTools

    parser = argparse.ArgumentParser(description="Record results of one event while it happens")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--event-id")
    target.add_argument("--auto", action="store_true", help="watch whichever event is on now, if any")
    parser.add_argument("--url", help="ufc.com event URL; default https://www.ufc.com/event/<event slug>")
    parser.add_argument("--shadow", action="store_true", help="record what would be saved, save nothing")
    parser.add_argument("--html-file", help="replay a saved page once instead of fetching (implies --shadow)")
    parser.add_argument("--poll", type=int, default=POLL_SECONDS, help="seconds between polls")
    parser.add_argument("--max-hours", type=float, default=MAX_HOURS)
    args = parser.parse_args()
    shadow = args.shadow or bool(args.html_file)

    conn = connect()
    db_tools = DBTools(conn, dry_run=shadow)
    fetcher = Fetcher()
    if args.auto:
        event, found = find_live_event(db_tools, fetcher)
        if event is None:
            print(json.dumps({"status": "idle", "message": "no event is on now", "checked": found}, indent=2))
            return
        args.event_id, source_url = str(event["id"]), args.url or found
    else:
        event = conn.execute("SELECT id, name, slug, status FROM events WHERE id = %s", (args.event_id,)).fetchone()
        if event is None:
            raise SystemExit(f"Unknown event {args.event_id}")
        source_url = args.url or UFC_EVENT_URL.format(slug=event["slug"])

    if args.html_file:
        def fetch_html():
            return Path(args.html_file).read_text(encoding="utf-8")
    else:
        def fetch_html():
            return fetcher.get(source_url, use_cache=False)

    agent = LiveEventAgent(LLMClient.from_env(), ToolRegistry(db_tools, fetcher))
    db_tools.set_event_live(args.event_id)
    run_log = RunLog(conn, "live_event", {"event_id": args.event_id, "url": source_url, "shadow": shadow})
    try:
        result = watch(agent, run_log, args.event_id, event["name"], source_url, fetch_html,
                       poll_seconds=args.poll, max_hours=args.max_hours, single_pass=bool(args.html_file))
    except Exception as exc:
        run_log.finish(status="error", error=str(exc))
        raise
    run_log.finish(status="completed", summary=result)

    if shadow:
        SHADOW_DIR.mkdir(parents=True, exist_ok=True)
        out = SHADOW_DIR / f"{event['slug']}-{datetime.datetime.now():%Y%m%d_%H%M%S}.jsonl"
        out.write_text("".join(json.dumps(r, default=str) + "\n" for r in db_tools.recorded), encoding="utf-8")
        result["shadow_file"] = str(out)
    print(json.dumps({"event": event["name"], "url": source_url, "shadow": shadow, **result}, indent=2))


if __name__ == "__main__":
    main()
