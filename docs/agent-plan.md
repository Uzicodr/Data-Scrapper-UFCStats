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
| API | FastAPI |
| Scheduler | APScheduler inside the service. Live polling needs an always-on process. |
| Deploy | Docker on an always-on host (to be decided). GitHub Actions cannot poll every 2 minutes reliably. |

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
| `ask` | API call | Yes | Answer ad-hoc questions using read-only DB tools. |

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

- Java keeps reading data directly from Supabase.
- The Python service exposes:
  - `POST /jobs/{job}`: start a job, returns a `run_id`
  - `GET /runs/{id}`: run status and summary
  - `POST /ask`: ad-hoc question
  - `GET /health`
- All endpoints require an API key header.
- For live updates, Java either polls `events` and `fights`, or subscribes to Supabase Realtime on `fights`.

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
  api/                   FastAPI server
  scheduler.py
scripts/
  mongo_json_to_sql.py   one-off Mongo export to SQL seed (done)
evals/                   eval set built from seeded data, runner, reports
tests/                   unit tests per tool, saved fixtures
docs/                    this plan
Dockerfile
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
2. **Infrastructure.** LLM wrapper with fallback, grounded search, fetch layer, and the
   `agent_runs` / `agent_steps` / `review_queue` migration in the backend.
3. **First agent: `post_event_stats`.** Chosen first because the seeded data is ground truth for it.
   Build an eval on 30 past events. Measure field accuracy and invented-data rate. Tune prompts until
   results are at least 98% correct.
4. **More agents.** `sync_upcoming`, `sync_rankings`, then `refresh_fighters`.
5. **`live_event`.** Run on a real event night in shadow mode, writing to a staging schema, before
   going live.
6. **Service.** FastAPI, scheduler, Docker deploy.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Model invents numbers | Source URLs required, two sources for results, schema validation, eval gate in phase 3, `flag_issue` instead of guessing. |
| Free-tier caps hit during an event | Change-detection polling, fallback provider chain, run logs that show usage. |
| Model retired or free model removed | Model names in config; eval suite re-checks any new model before use. |
| Fighter name mismatch creates duplicates | Alias matching, fuzzy name search, LLM confirms only ambiguous matches, unsure cases go to the review queue. |
| Stats page layout changes | `fetch_page` returns cleaned text, so the LLM reads it without fixed selectors. |
| Overlap with Cito data later | Rows are separated by `source`. Decide on a single source of truth per table before re-enabling Cito. |

## 13. Open decisions

1. Hosting for the always-on Python service (small VM, Render, Fly.io or Railway).
2. How pick settlement is triggered when the agent completes a fight.
3. Whether Cito stays as a source, and if so, which source wins per table.
