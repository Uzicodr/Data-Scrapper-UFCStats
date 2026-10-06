"""Tool registry for agent jobs. Each job gets a subset of tools."""
import json
from typing import Callable, Optional

from pydantic import ValidationError

from ufc_agent.fetch.http import Fetcher
from ufc_agent.schemas.models import (
    DIVISIONS, METHODS, RANKED_PER_DIVISION, EventCard, EventResults, FighterProfile, LiveResult, Rankings,
)
from ufc_agent.search.grounded import GroundedSearch
from ufc_agent.tools.db_tools import DBTools

PAGE_CHARS = 8000


def validation_error(e: ValidationError) -> dict:
    """Tool result for a pydantic failure, one readable line per problem."""
    return {"status": "error", "message": "Validation failed",
            "errors": [" ".join([".".join(map(str, err["loc"])), err["msg"]]).strip() for err in e.errors()]}


class ToolResult:
    def __init__(self, name: str, value: any, is_error: bool = False):
        self.name = name
        self.value = value
        self.is_error = is_error

    def to_dict(self):
        return {"name": self.name, "value": self.value, "is_error": self.is_error}


class ToolRegistry:
    """Registry and dispatcher for agent tools."""

    def __init__(self, db_tools: DBTools, fetcher: Fetcher, search: GroundedSearch = None):
        self.db_tools = db_tools
        self.fetcher = fetcher
        self.search = search or GroundedSearch()
        self._tools = {
            "web_search": self._web_search,
            "fetch_page": self._fetch_page,
            "db_get_event": self._db_get_event,
            "db_list_upcoming": self._db_list_upcoming,
            "db_find_fighter": self._db_find_fighter,
            "submit_event_results": self._submit_event_results,
            "submit_live_result": self._submit_live_result,
            "submit_rankings": self._submit_rankings,
            "submit_event_card": self._submit_event_card,
            "submit_fighter_profile": self._submit_fighter_profile,
            "flag_issue": self._flag_issue,
        }

    def get_schemas(self, job: str) -> list[dict]:
        """Get tool schemas for a job."""
        job_tools = {
            "post_event_stats": [
                "fetch_page",
                "submit_event_results",
                "flag_issue",
            ],
            "sync_rankings": [
                "fetch_page",
                "web_search",
                "db_find_fighter",
                "submit_rankings",
                "flag_issue",
            ],
            "sync_upcoming": [
                "fetch_page",
                "db_list_upcoming",
                "db_find_fighter",
                "submit_event_card",
                "flag_issue",
            ],
            "live_event": [
                "submit_live_result",
                "flag_issue",
            ],
            # When the live page cannot be fetched: search instead.
            "live_event_search": [
                "web_search",
                "submit_live_result",
                "flag_issue",
            ],
            "refresh_fighters": [
                "fetch_page",
                "submit_fighter_profile",
                "flag_issue",
            ],
        }
        tools = job_tools.get(job, [])
        return [self._schema(name) for name in tools]

    def dispatch(self, tool_name: str, args: dict) -> dict:
        """Call a tool with JSON args. Returns {result, is_error}."""
        tool = self._tools.get(tool_name)
        if not tool:
            return {"result": f"Unknown tool: {tool_name}", "is_error": True}
        try:
            result = tool(**args)
            return {"result": result, "is_error": False}
        except Exception as e:
            return {"result": str(e), "is_error": True}

    # Implementations
    def _web_search(self, query: str) -> dict:
        """Grounded search. Returns answer + sources."""
        result = self.search.search(query)
        return result.to_dict()

    def _fetch_page(self, url: str, start: int = 0, include_links: bool = False) -> dict:
        """Fetch and clean page. Returns one PAGE_CHARS chunk from start, plus next_start when more text follows."""
        title, text, truncated = self.fetcher.fetch(url, max_chars=start + PAGE_CHARS, include_links=include_links)
        result = {
            "title": title or "",
            "text": text[start:],
            "truncated": truncated,
            "url": url,
        }
        if truncated:
            result["next_start"] = len(text)
        return result

    def _db_get_event(self, name_or_date: str) -> dict:
        """Look up event by name or date (YYYY-MM-DD)."""
        event = self.db_tools.get_event(name_or_date)
        if not event:
            return {"event": None, "error": f"Event not found: {name_or_date}"}
        return {"event": event}

    def _db_list_upcoming(self, days: int = 180) -> dict:
        """Scheduled events in the next N days, with their scheduled bouts."""
        return {"events": self.db_tools.upcoming_cards(days)}

    def _db_find_fighter(self, name: str) -> dict:
        """Fuzzy match fighter. Returns candidates with scores."""
        candidates = self.db_tools.find_fighter(name)
        if not candidates:
            return {"candidates": [], "error": f"No matches for: {name}"}
        return {"candidates": candidates[:5]}  # Top 5

    def _submit_rankings(self, division: str, ranked: list[dict], sources: list[str],
                         champion: Optional[dict] = None) -> dict:
        """Validate a full division list, then replace that division's rows."""
        try:
            rankings = Rankings(division=division, champion=champion, ranked=ranked, sources=sources)
        except ValidationError as e:
            return validation_error(e)
        return self.db_tools.submit_rankings(rankings)

    def _submit_event_results(self, **results) -> dict:
        """Validate every fight of a completed event, then record them and complete the event."""
        try:
            event_results = EventResults(**results)
        except ValidationError as e:
            return validation_error(e)
        return self.db_tools.submit_event_results(event_results)

    def _submit_live_result(self, **result) -> dict:
        """Validate one finished bout from a live source, then record it as provisional."""
        try:
            live_result = LiveResult(**result)
        except ValidationError as e:
            return validation_error(e)
        return self.db_tools.submit_live_result(live_result)

    def _submit_event_card(self, **card) -> dict:
        """Validate a full upcoming card, then create or update the event and its bouts."""
        try:
            event_card = EventCard(**card)
        except ValidationError as e:
            return validation_error(e)
        return self.db_tools.submit_event_card(event_card)

    def _submit_fighter_profile(self, **profile) -> dict:
        """Validate a fighter's profile as printed on ufcstats.com, then update the fighter."""
        try:
            fighter_profile = FighterProfile(**profile)
        except ValidationError as e:
            return validation_error(e)
        return self.db_tools.submit_fighter_profile(fighter_profile)

    def _flag_issue(self, entity: str, reason: str) -> dict:
        """Flag issue for human review."""
        return self.db_tools.flag_issue(entity, reason)

    def _schema(self, name: str) -> dict:
        """Return OpenAI tool schema for a tool."""
        function = self._function_schema(name)
        return {"type": "function", "function": function} if function else {}

    def _function_schema(self, name: str) -> dict:
        ranked_fighter = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name exactly as printed by the source"},
                "fighter_id": {"type": "string",
                               "description": "Only when the tool could not match the name: id from db_find_fighter"},
            },
            "required": ["name"],
        }
        schemas = {
            "web_search": {
                "name": "web_search",
                "description": "Search UFC data using Google Search with grounding",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"}
                    },
                    "required": ["query"]
                }
            },
            "fetch_page": {
                "name": "fetch_page",
                "description": "Fetch and clean a page. Allowed: ufcstats.com, ufc.com, espn.com, etc. "
                               "Long pages come in chunks: call again with start=next_start for the rest.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Page URL"},
                        "start": {"type": "integer", "minimum": 0,
                                  "description": "Character offset; use next_start from the previous chunk"},
                        "include_links": {"type": "boolean",
                                          "description": "Show links as 'text <url>', table-row links as a "
                                                         "trailing <url>, and images as [img:file.png]"}
                    },
                    "required": ["url"]
                }
            },
            "db_list_upcoming": {
                "name": "db_list_upcoming",
                "description": "List scheduled events already in the database for the next N days, with their bouts",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {"type": "integer", "minimum": 1, "description": "Look-ahead window, default 180"}
                    }
                }
            },
            "db_get_event": {
                "name": "db_get_event",
                "description": "Look up event by name substring or date",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name_or_date": {"type": "string", "description": "Event name or YYYY-MM-DD"}
                    },
                    "required": ["name_or_date"]
                }
            },
            "db_find_fighter": {
                "name": "db_find_fighter",
                "description": "Fuzzy match fighter by name. Returns top matches with similarity scores.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Fighter name"}
                    },
                    "required": ["name"]
                }
            },
            "submit_rankings": {
                "name": "submit_rankings",
                "description": "Replace one division's rankings with the full current list. "
                               "Names are matched to fighters automatically; unmatched names come back as errors.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "division": {"type": "string", "enum": list(DIVISIONS)},
                        "champion": {**ranked_fighter,
                                     "description": "Division champion. Omit for pound-for-pound or a vacant title."},
                        "ranked": {"type": "array", "items": ranked_fighter,
                                   "minItems": RANKED_PER_DIVISION, "maxItems": RANKED_PER_DIVISION,
                                   "description": "Ranked fighters in order; first item is rank 1"},
                        "sources": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                    "description": "URLs the list was read from"}
                    },
                    "required": ["division", "ranked", "sources"]
                }
            },
            "submit_event_results": {
                "name": "submit_event_results",
                "description": "Record every fight of one completed event and mark the event completed. "
                               "Fighters are matched by URL. Scheduled bouts missing from the results are "
                               "marked cancelled.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_url": {"type": "string", "description": "ufcstats.com/event-details/... URL"},
                        "fights": {
                            "type": "array", "minItems": 1,
                            "description": "Every fight row in page order",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "fight_url": {"type": "string", "description": "The row's fight-details URL"},
                                    "outcome": {"type": "string", "enum": ["win", "draw", "nc"],
                                                "description": "The W/L column"},
                                    "fighters": {
                                        "type": "array", "minItems": 2, "maxItems": 2,
                                        "description": "Both fighters in the order the row lists them",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"type": "string"},
                                                "url": {"type": "string", "description": "fighter-details URL"},
                                                "kd": {"type": "integer", "minimum": 0},
                                                "sig_str": {"type": "integer", "minimum": 0,
                                                            "description": "Str column"},
                                                "td": {"type": "integer", "minimum": 0},
                                                "sub": {"type": "integer", "minimum": 0},
                                            },
                                            "required": ["name", "url", "kd", "sig_str", "td", "sub"],
                                        },
                                    },
                                    "weight_class": {"type": "string",
                                                     "description": "Weight class text without [img:...] marks"},
                                    "is_title_fight": {"type": "boolean",
                                                       "description": "True only when the row shows [img:belt.png]"},
                                    "method": {"type": "string", "enum": list(METHODS)},
                                    "method_details": {"type": "string",
                                                       "description": "Rest of the Method column, e.g. 'Punch'"},
                                    "round": {"type": "integer", "minimum": 1, "maximum": 5},
                                    "time": {"type": "string", "description": "m:ss as printed"},
                                },
                                "required": ["fight_url", "outcome", "fighters", "method", "round", "time"],
                            },
                        },
                        "sources": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                    "description": "URLs the results were read from"},
                    },
                    "required": ["event_url", "fights", "sources"],
                },
            },
            "submit_live_result": {
                "name": "submit_live_result",
                "description": "Record one finished bout as a provisional result. The bout is matched to a "
                               "scheduled bout of the event by both fighters' names.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string", "description": "event_id from the job input"},
                        "red_name": {"type": "string", "description": "First fighter as the source lists the bout"},
                        "blue_name": {"type": "string", "description": "Second fighter as the source lists the bout"},
                        "winner": {"type": "string",
                                   "description": "Winner's name exactly as given for that fighter, or 'draw' "
                                                  "or 'no contest'"},
                        "method": {"type": "string", "description": "Method as printed, e.g. 'Decision - Unanimous'"},
                        "round": {"type": "integer", "minimum": 1, "maximum": 5},
                        "time": {"type": "string", "description": "m:ss as printed"},
                        "source_url": {"type": "string", "description": "URL the result was read from"},
                    },
                    "required": ["event_id", "red_name", "blue_name", "winner", "method", "round", "time",
                                 "source_url"],
                },
            },
            "submit_event_card": {
                "name": "submit_event_card",
                "description": "Create or update one upcoming event with its full card. Bouts missing from the "
                               "card are marked cancelled. Fighters new to the database are created from their URL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_url": {"type": "string", "description": "ufcstats.com/event-details/... URL"},
                        "name": {"type": "string", "description": "Event name exactly as printed"},
                        "date": {"type": "string", "description": "Event date as YYYY-MM-DD"},
                        "location": {"type": "string",
                                     "description": "Location as printed, e.g. 'Las Vegas, Nevada, USA'"},
                        "bouts": {
                            "type": "array", "minItems": 1,
                            "description": "Every bout in page order; the first bout is the main event",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "red_name": {"type": "string", "description": "First fighter listed in the row"},
                                    "red_url": {"type": "string", "description": "First fighter's fighter-details URL"},
                                    "blue_name": {"type": "string", "description": "Second fighter listed in the row"},
                                    "blue_url": {"type": "string",
                                                 "description": "Second fighter's fighter-details URL"},
                                    "weight_class": {"type": "string", "description": "Weight class as printed"},
                                    "is_title_fight": {"type": "boolean",
                                                       "description": "True only when the row shows [img:belt.png]"},
                                    "fight_url": {"type": "string", "description": "The row's fight-details URL"},
                                },
                                "required": ["red_name", "blue_name"],
                            },
                        },
                        "sources": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                    "description": "URLs the card was read from"},
                    },
                    "required": ["event_url", "name", "date", "bouts", "sources"],
                },
            },
            "submit_fighter_profile": {
                "name": "submit_fighter_profile",
                "description": "Update one fighter from their ufcstats.com profile. Copy every value exactly as "
                               "printed, including units and % signs; use '--' when the page shows '--' or nothing.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fighter_id": {"type": "string", "description": "fighter_id from the job input"},
                        "profile_url": {"type": "string", "description": "profile_url from the job input"},
                        "name_on_page": {"type": "string", "description": "Fighter name at the top of the page"},
                        "nickname": {"type": "string", "description": "Nickname under the name, if any"},
                        "record": {"type": "string", "description": "e.g. '27-7-0' or '27-7-0 (1 NC)'"},
                        "height": {"type": "string", "description": "e.g. 6' 2\""},
                        "weight": {"type": "string", "description": "e.g. '185 lbs.'"},
                        "reach": {"type": "string", "description": "e.g. '75\"'"},
                        "stance": {"type": "string", "description": "e.g. 'Orthodox'"},
                        "dob": {"type": "string", "description": "e.g. 'Dec 28, 1995'"},
                        "slpm": {"type": "string", "description": "SLpM, e.g. '3.75'"},
                        "str_acc": {"type": "string", "description": "Str. Acc., e.g. '52%'"},
                        "sapm": {"type": "string", "description": "SApM, e.g. '3.78'"},
                        "str_def": {"type": "string", "description": "Str. Def, e.g. '47%'"},
                        "td_avg": {"type": "string", "description": "TD Avg., e.g. '1.52'"},
                        "td_acc": {"type": "string", "description": "TD Acc., e.g. '41%'"},
                        "td_def": {"type": "string", "description": "TD Def., e.g. '57%'"},
                        "sub_avg": {"type": "string", "description": "Sub. Avg., e.g. '1.1'"},
                    },
                    "required": ["fighter_id", "profile_url", "name_on_page", "record"],
                },
            },
            "flag_issue": {
                "name": "flag_issue",
                "description": "Flag data issue for human review instead of guessing",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "description": "What entity (fighter, fight, event)"},
                        "reason": {"type": "string", "description": "Why it's flagged"}
                    },
                    "required": ["entity", "reason"]
                }
            }
        }
        return schemas.get(name, {})
