j# UFC Data Agent: Plan

Status as of 2026-10-06. Phase 1 (data migration) is complete.

## 1. Goal

Replace the daily ufcstats.com scraper with an LLM agent that keeps UFC data current:
upcoming cards, live results, completed results with per-fight stats, fighter profiles and
career stats, and rankings. The data lives in the Octapulse backend's Supabase Postgres
database. The Java backend (`Octapulse-Backend`) reads it from there.

## 2. Why a hybrid, not "agent for everything"

| Problem with the old scraper | What actually fixes it |
|---|---|
| Rate limiting | Incremental sync. The old job re-fetched about 4,400 fighter profiles and 750 event pages every day. Completed events never change, so only new events and the fighters in them need fetching. That is roughly 15 to 40 page fetches per event week. |
| Not realtime | An event-window job that polls during live events. ufcstats.com only updates after an event ends. |
| Brittle parsers | The LLM reads cleaned page text, so a layout change does not break extraction. |
| Legal exposure | Grounded search moves the fetching to Google. Republishing compiled stats is still a gray area; check before going commercial. |

Where search works and where it does not:

- Search works well for upcoming cards, bout changes, results (winner, method, round, time) and rankings.
- Search does not return per-fight stats (knockdowns, significant strikes, takedowns, submission
  attempts) or career stats (SLpM, accuracy, defense). Those exist only on stats pages
  (ufcstats.com, ufc.com, ESPN). The agent fetches those pages directly, at low volume, after each event.
- Historical data is imported once and frozen. It never goes through the agent.

## 3. Principles

1. **One small agent per job.** Each job has a narrow prompt, 4 to 6 tools and one clear goal.
   Free models such as Gemini Flash perform much better this way than with one large agent.
2. **The LLM finds and extracts; code validates and writes.** The model never writes SQL. It calls
   `submit_*` tools, and Python validates every record before the upsert.
3. **Every fact carries its source.** Submitted records include source URLs. A result needs two
   agreeing sources before it is treated as verified.
4. **Never guess.** When data is missing or sources disagree, the agent calls `flag_issue` instead of
   inventing a value.
5. **Measure before trusting.** The seeded historical data is ground truth for an eval set. The agent
   must reach a target accuracy on past events before it writes to production.

## 4. Stack

| Part | Choice |
|---|---|
| Language | Python 3.11+ |
| LLM client | `openai` SDK pointed at Gemini's OpenAI-compatible endpoint, with OpenRouter free models as fallback. One interface, so switching providers is a config change. |
| Search | `web_search` tool, implemented as a separate `google-genai` call with Grounding with Google Search. Returns a summary plus source URLs. Kept outside the agent loop so it works with any LLM provider. |
| Page fetch | `httpx` with a per-host throttle (2 s minimum), retries with backoff, a URL cache, the existing ufcstats proof-of-work solver, `trafilatura`/`selectolax` to turn HTML into compact text, and a domain allowlist. |
| Validation | Pydantic v2 |
| Database | Supabase Postgres via `psycopg` 3. Schema is owned by the backend's Flyway migrations. |
| Scheduler | GitHub Actions scheduled workflows (`.github/workflows/`). No API or server in this repo: the Spring Boot backend is the API. |

### Free-tier constraints

- Gemini and OpenRouter free tiers have per-minute and per-day request caps. Search grounding has its own
  daily cap. Check current limits; they change.
- Google may use free-tier Gemini prompts and outputs to improve its products.
- Free models get retired or removed without notice. Model names live in config, and the eval suite
  re-checks any new model before use.
- Plan to move to a paid tier once real users depend on the data.

## 5. Jobs

| Job | Trigger | Uses LLM | What it does |
|---|---|---|---|
| `sync_upcoming` | Daily | Yes | Search for announced cards. Add, change or cancel bouts. Create profiles for debuting fighters. |
| `sync_rankings` | Daily | Yes | Get current rankings and link names to existing fighters. |
| `live_event` | From event start until the main event ends | Only on change | Fetch the results page every 2 minutes and hash it. Run the agent only when the hash changes. Fallback: a search every 10 minutes. |
| `post_event_stats` | Event day +1, again at +2 | Yes | Fill per-fight stats, mark the event `completed`, confirm results. |
| `refresh_fighters` | After `post_event_stats` | Yes | Update record and career stats, only for fighters who just fought (about 28 per event). |
| `sync_news` | Hourly | No | Read the ESPN, UFC.com, Sherdog, MMA Weekly, BBC Sport and Guardian RSS feeds into `news_items`. Tag fighters by name and sort stories into kinds with keyword rules. Store headlines and summaries unchanged, and keep 30 days. Stories without a feed photo get a free Wikipedia photo of a tagged fighter (cached in `fighter_photos`) or an Unsplash cage photo, with credits. |
| `ask` | API call | Yes | Answer ad-hoc questions using read-only DB tools. |

After `live_event` saves results and after `sync_news` saves new stories, the job calls the backend's `POST /internal/notify` (`ufc_agent/backend.py`) so push notifications go out at once. It needs the `BACKEND_URL` variable and `INTERNAL_TOKEN` secret; without them the call is skipped.

The live job keeps LLM calls to roughly 14 to 30 per event instead of about 360.

## 6. Tools

Shared library. Each job gets only the subset it needs.

| Tool | Notes |
|---|---|
| `web_search(query)` | Grounded search. Returns `{summary, sources[]}`. |
| `fetch_page(url)` | Allowlisted domains only. Returns cleaned text, at most about 8,000 characters. |
| `db_get_event(name_or_date)` | Read-only. |
| `db_list_upcoming()` | Read-only. |
| `db_find_fighter(name)` | Read-only. Fuzzy match on name and known aliases; returns candidates with a similarity score. |
| `submit_event(event, sources)` | Validates, then upserts. |
| `submit_fight_result(fight, sources)` | Two agreeing sources are required to mark a result verified. |
| `submit_fight_stats(stats, source)` | Checks both corners are present and all values are 0 or more. |
| `submit_fighter(fighter, sources)` | Never overwrites a row with `manual_override = true`. |
| `submit_rankings(division, list, sources)` | Checks length (champion plus 15) and that names are unique. |
| `flag_issue(entity, reason)` | Logs a problem for human review instead of guessing. |

When validation fails, the tool returns the error to the model so it can correct the data and resubmit.
Each job has a maximum step count of about 25.

## 7. Agent loop

A manual loop, about 80 lines, which is easier to learn from and debug than a framework:

```
messages = [system(job_prompt), user(job_input)]
for step in range(MAX_STEPS):
    resp = llm.chat(messages, tools=job_tools)      # provider wrapper, rate-limited
    log_step(run_id, resp)
    if no tool_calls: break
    for call in resp.tool_calls:
        result = dispatch(call)                     # validate args with Pydantic, run, catch errors
        messages.append(tool_result(call.id, result))
finalize_run(run_id)
```

The provider wrapper:

- applies a rate limiter per provider, set to the free-tier limits;
- on a 429 response, backs off and then falls back to the next provider in the chain;
- logs tokens per call, so you can see what a paid tier would cost.

## 8. Database

The schema is owned by the backend: `Octapulse-Backend/src/main/resources/db/migration`
(`V1__init.sql`, `V2__bout_round_stats.sql`). The agent writes to these tables:

| Table | How the agent uses it |
|---|---|
| `fighters` | Upsert by `(source, source_id)`. Fields without a column (date of birth, career stats, aliases) go in `raw_payload`. |
| `events` | Upsert by `(source, source_id)`. Status values: `scheduled`, `live`, `completed`. |
| `fights` | Upsert by `(source, source_id)`. Status values match events. Per-fight totals go in `raw_payload`. |
| `bout_round_stats` | Per-round stats when a source provides them. |
| `rankings` | Replaced per sync. Champion has `rank = NULL` and `is_champion = true`. |

Conventions, matching the existing Cito sync:

- Slugs are kebab-case: `islam-makhachev`, `ufc-322`, `ufc-fight-night-october-11-2026`.
- Rows with `manual_override = true` are never changed by a sync.
- When a fight becomes `completed` with a winner, picks must be settled. Either the agent calls a backend
  endpoint that runs `SettlementService`, or the backend settles picks on its own schedule. To be decided.

### New tables needed (add as a Flyway migration in the backend)

- `agent_runs`: job, status, input, summary, error, provider, model, token counts, start and finish times.
- `agent_steps`: every LLM call and tool call in a run, with arguments, a truncated result and errors.
  This is the main debugging tool.
- `review_queue`: everything raised by `flag_issue`, with status `open`, `resolved` or `dismissed`.
- Optionally `fighter_aliases`, to replace the aliases currently stored in `fighters.raw_payload`.

## 9. Java integration

There is no API in this repo. The jobs write straight to the Supabase tables the Spring Boot backend
already maps (`fighters`, `events`, `fights`, `rankings`), so its endpoints serve new data as soon as a
job commits; there is no cache in between.

- Status values match what `EventController` queries: `scheduled`, `live`, `completed`. Bouts that do
  not happen are `cancelled`.
- Per-fight stats and career stats are in `raw_payload`; `FightDto` and `FighterDto` do not expose them
  yet.
- `events.starts_at` holds the real start time once the live check has read it from ufc.com, so pick
  locking (`PickService`) uses the actual start.
- Pick settlement does not happen yet: `SettlementService` only runs inside the Java Cito sync.
- Disable the backend's Cito sync workflow (`sync.yml`) while ufcstats is the source, or it adds
  `source = 'cito'` duplicates of the same events and fighters.

## 10. Project layout (this repo)

```
ufc_agent/
  config.py              environment variables
  normalize.py           text-to-value parsers (done)
  db.py                  Postgres connection (done)
  llm/                   OpenAI-compatible client, fallback chain, rate limiter
  search/                Gemini grounded search
  fetch/                 throttled HTTP, cache, PoW solver, HTML-to-text
  tools/                 tool implementations and registry
  agents/                loop, prompts, one module per job
  schemas/               Pydantic models (tool contracts)
  scheduler.py
scripts/
  mongo_json_to_sql.py   one-off Mongo export to SQL seed (done)
evals/                   eval set built from seeded data, runner, reports
tests/                   unit tests per tool, saved fixtures
docs/                    this plan
.github/workflows/       scheduled jobs (see phase 6)
```

## 11. Phases

1. **Data migration. Done.**
   - Exported all four Mongo collections (`fighterlogs`, `pastevents`, `upcomingevents`, `rankings`)
     to `data/mongo_export/`.
   - Generated `data/seed.sql` with `scripts/mongo_json_to_sql.py` and loaded it into Supabase:
     4,605 fighters, 795 events, 8,935 fights, 206 ranking rows.
   - Known gaps: event start times are midnight UTC (date only); 161 completed fights have no winner
     (draws, no-contests, and 14 fights with a fighter missing from the source list); `card_section`,
     `venue` and fighter `country` are empty.
   - Removed MongoDB: the legacy scraper scripts, `pymongo`, the Mongo credentials and the daily
     scraper GitHub workflow. Kept `ufcstats_client.py` (proof-of-work solver) for the fetch layer.
2. **Infrastructure. Done (pending the backend migration deploy).**
   - `ufc_agent/llm/client.py`: OpenAI-compatible client over a provider chain set by `LLM_CHAIN`
     (default `gemini:gemini-2.5-flash`, then `openrouter:nvidia/nemotron-3-super-120b-a12b:free`),
     with a per-provider requests-per-minute limiter and a 60 s cooldown after a 429.
   - `ufc_agent/search/grounded.py`: Gemini Grounding with Google Search; returns an answer plus
     resolved source URLs.
   - `ufc_agent/fetch/`: domain allowlist, 2 s per-host throttle, retries honoring `Retry-After`,
     6-hour disk cache, request budget, ufcstats proof-of-work solver, HTML-to-text with tables kept
     as `a | b | c` rows.
   - `ufc_agent/runlog.py`: writes `agent_runs`, `agent_steps` and `review_queue`.
   - Backend migration `V3__agent_runs.sql` adds those three tables. Flyway applies it on the next
     backend start or deploy.
   - Live checks passed: ufcstats page fetch (challenge solved), grounded search, both LLM providers,
     Gemini tool calling.
   - Findings for phase 3: grounded search answers headline facts (main event, date, winner) but not
     full fight cards, so card and stats work must fetch pages (ufc.com, ufcstats.com). The
     ufcstats "completed" list shows the next upcoming event as its first row; skip events dated in
     the future.
3. **`post_event_stats` and eval. Done.**
   - The agent reads the completed event page once (`fetch_page(include_links=true)`) and calls
     `submit_event_results` with every fight: outcome, method, round, time, title fight, and each
     fighter's KD, Str, Td and Sub. Fighters are matched by URL, not corner (ufcstats lists the winner
     first). Fights and the event are marked `completed`; bouts missing from the results are marked
     `cancelled`. Pick settlement is deferred.
   - Run: `python -m ufc_agent.agents.post_event_stats [--event-id <uuid>]`. Default targets are
     ufcstats events still `scheduled`/`live` 6+ hours after their start.
   - Eval: `python -m evals.eval_runner [--events 30] [--skip N]`. Uses `DBTools(dry_run=True)`: every
     check and write runs, then rolls back, so the seeded ground truth is never changed (verified by
     table checksums before and after). Scores 13 fields per fight against the seeded data.
   - Eval passed: 30 events, 377 fights, 4,893 of 4,893 fields correct (100%), no missing or invented
     fights (`evals/results/eval_20261006_194907.json` for the first 6 events,
     `eval_20261007_001128.json` for the other 24). The first attempt stopped after 6 events when the
     Gemini and OpenRouter daily caps ran out; the rest ran with OpenCode `space-bunny-free` first.
   - Production run completed the 6 past events that were still scheduled (Aug 29 to Oct 3): 78 fights
     completed and verified, 3 bouts that did not happen marked cancelled, 5 debuting fighters created as
     stubs for `refresh_fighters`.
4. **More agents. Done.**
   - `sync_rankings` (`python -m ufc_agent.agents.sync_rankings`): reads ufc.com/rankings and replaces
     each division's rows. Live run saved all 13 divisions; every linked fighter appears on the page.
   - `sync_upcoming` (`python -m ufc_agent.agents.sync_upcoming`): reads the ufcstats upcoming list and
     each event page with `fetch_page(include_links=true)`, then calls `submit_event_card` per event.
     Bouts are matched by ufcstats fight id; bouts dropped from a card are marked `cancelled`, never
     deleted, because picks reference them. Fighters new to the database are created as stubs
     (`raw_payload.stub = true`). Rows with `manual_override` and non-scheduled events are never touched.
     Live run saved all 8 upcoming cards (82 bouts, correct main events and title fights).
   - `refresh_fighters` (`python -m ufc_agent.agents.refresh_fighters [--days 14] [--limit 40]
     [--fighter-id <uuid>]`): targets stubs first, then fighters whose profile is older than a fight
     they had in the last N days. Works in batches of 5 so each conversation stays short. The model
     copies values as printed; code parses them and never lets a blank (`--`) erase a stored value.
     Live run on 6 fighters: all 90 copied values matched the previously scraped data.
   - Shared fixes made along the way: `reasoning_effort` per job (`none` for the copy jobs, default
     `low`), empty model replies are retried and never counted as a finished job, and
     `db_list_upcoming` hides past events still marked scheduled (completing them is phase 3's job).
   - Known cost: `sync_upcoming` sends about 600k input tokens per run because every fetched event
     page stays in the conversation. If free-tier token limits bite, run one short conversation per
     event, as `refresh_fighters` does with batches.
5. **`live_event`. Built; first real run pending an event night.**
   - Source: the ufc.com event page (`https://www.ufc.com/event/<event slug>`), which posts each result
     as it happens; ufcstats only updates after the event. `ufc_agent/fetch/ufc_card.py` reads the
     fight listing markup into one line per bout ("A [Loss] vs B [Win] | KO/TKO | round 1 | 2:09"),
     because the flattened page text separates the Win/Loss labels from the fighters.
   - The watcher is code: it polls every 2 minutes and hashes that digest (odds and timers do not
     change it). The agent runs only when the hash changes, with the finished bouts and the bouts the
     database still has scheduled, and calls `submit_live_result` per bout. About one LLM call per
     posted result instead of ~360 per event.
   - Results are saved as provisional (`raw_payload.result_status = 'provisional'`, method mapped to
     the ufcstats codes, event status `live`). `post_event_stats` overwrites them the next day and sets
     `result_status = 'verified'`.
   - After 3 failed fetches in a row it falls back to grounded search, at most every 10 minutes. An
     exhausted LLM quota pauses processing; the same page is retried on the next poll.
   - Run: `python -m ufc_agent.agents.live_event --event-id <uuid> [--url ...] [--shadow]`.
     `--shadow` saves nothing and writes what it would have saved to `data/live_shadow/*.jsonl`;
     `--html-file page.html` replays a saved page.
   - Shadow replay of UFC 332 against the real database: 7 of 7 scheduled bouts matched, all winners,
     methods, rounds and times correct, table checksums unchanged.
   - Remaining: shadow-run a real event night (next: UFC Fight Night: Allen vs. Duncan, Oct 10), then
     run without `--shadow`. Starting the watcher at the event's start time is phase 6's scheduler.
   - Local network note: this machine's router DNS fails to resolve `www.ufc.com` (public DNS
     resolves it). Fix the DNS setting, or the live job and `sync_rankings` cannot reach ufc.com.
6. **Scheduling on GitHub Actions. Done (needs repository secrets).**
   - `_run-job.yml` is the shared setup (Python 3.12, `pip install -r requirements.txt`, secrets); each
     job is a small scheduled workflow that calls it, and every one can also be started by hand.

     | Workflow | Schedule (UTC) | Command |
     |---|---|---|
     | `sync-upcoming.yml` | daily 14:00 | `ufc_agent.agents.sync_upcoming` |
     | `sync-rankings.yml` | Wednesday 15:00 | `ufc_agent.agents.sync_rankings` |
     | `post-event-stats.yml` | 04:00, 10:00, 16:00, 22:00 | `ufc_agent.agents.post_event_stats` |
     | `refresh-fighters.yml` | daily 11:30 | `ufc_agent.agents.refresh_fighters` |
     | `live-event.yml` | hourly at :05 | `ufc_agent.agents.live_event --auto --max-hours 5.8` |
     | `sync-news.yml` | hourly at :17 | `ufc_agent.agents.sync_news` |

   - Live: `--auto` reads each nearby event's real start from ufc.com
     (`.c-event-fight-card-broadcaster__time[data-timestamp]`), stores it in `events.starts_at`, and
     watches the event whose window (15 minutes before its first section to 10 hours after) contains
     now. Each run stays under GitHub's 6-hour job limit; a concurrency group keeps one watcher at a
     time, and the next hourly run continues a long card. Manual runs can choose shadow mode, whose
     output is uploaded as an artifact.
   - Required repository secrets: `SUPABASE_DB_URL`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`,
     `OPENCODE_API_KEY`; repository variable `LLM_CHAIN`.
   - Limits: scheduled runs can start 5 to 30 minutes late; scheduled workflows are disabled after 60
     days without a commit; a private repository gets 2,000 free minutes a month (this setup uses
     roughly 900 to 1,200; public repositories are free).

## 12. Daily LLM budget

The free tiers allow about 70 LLM calls a day in total, and one day of jobs needs roughly:
`sync_rankings` ~18, `sync_upcoming` ~30, `post_event_stats` ~3 per event, `refresh_fighters` ~3 per
5 fighters. That fits only on quiet days and leaves nothing for evals. Adding 10 credits to
OpenRouter raises its free-model cap to 1,000 requests a day, which covers everything. The client
benches a provider until the reset time a 429 names, waits out short cooldowns, and raises
`QuotaExhausted` when every provider is out for longer than 2 minutes.

The chain now starts with OpenCode Zen (`LLM_CHAIN=opencode:space-bunny-free,gemini:...,openrouter:...`).
OpenCode's free models refuse API use outside the OpenCode app ("OpenCode's free tier can only be used
from within OpenCode"); `space-bunny-free` answered anyway when this was set up. Treat it as likely
against their terms and liable to stop working without notice.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Model invents numbers | Source URLs required, two sources for results, schema validation, eval gate in phase 3, `flag_issue` instead of guessing. |
| Free-tier caps hit during an event | Change-detection polling, fallback provider chain, run logs that show usage. |
| Model retired or free model removed | Model names in config; eval suite re-checks any new model before use. |
| Fighter name mismatch creates duplicates | Alias matching, fuzzy name search, LLM confirms only ambiguous matches, unsure cases go to the review queue. |
| Stats page layout changes | `fetch_page` returns cleaned text, so the LLM reads it without fixed selectors. |
| Overlap with Cito data later | Rows are separated by `source`. Decide on a single source of truth per table before re-enabling Cito. |

## 14. Open decisions

2. How pick settlement is triggered when the agent completes a fight.
3. Whether Cito stays as a source, and if so, which source wins per table.
