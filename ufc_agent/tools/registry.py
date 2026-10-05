"""Tool registry for agent jobs. Each job gets a subset of tools."""
import json
from typing import Callable, Optional

from ufc_agent.fetch.http import Fetcher
from ufc_agent.search.grounded import GroundedSearch
from ufc_agent.tools.db_tools import DBTools


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
            "db_find_fighter": self._db_find_fighter,
            "submit_fight_result": self._submit_fight_result,
            "submit_fight_stats": self._submit_fight_stats,
            "flag_issue": self._flag_issue,
        }

    def get_schemas(self, job: str) -> list[dict]:
        """Get tool schemas for a job."""
        job_tools = {
            "post_event_stats": [
                "fetch_page",
                "db_get_event",
                "db_find_fighter",
                "submit_fight_result",
                "submit_fight_stats",
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

    def _fetch_page(self, url: str) -> dict:
        """Fetch and clean page. Returns title, text, truncated flag."""
        title, text, truncated = self.fetcher.fetch(url)
        return {
            "title": title or "",
            "text": text,
            "truncated": truncated,
            "url": url,
        }

    def _db_get_event(self, name_or_date: str) -> dict:
        """Look up event by name or date (YYYY-MM-DD)."""
        event = self.db_tools.get_event(name_or_date)
        if not event:
            return {"event": None, "error": f"Event not found: {name_or_date}"}
        return {"event": event}

    def _db_find_fighter(self, name: str) -> dict:
        """Fuzzy match fighter. Returns candidates with scores."""
        candidates = self.db_tools.find_fighter(name)
        if not candidates:
            return {"candidates": [], "error": f"No matches for: {name}"}
        return {"candidates": candidates[:5]}  # Top 5

    def _submit_fight_result(self, event_name: str, winner: str, method: str,
                            round: int, time_seconds: int, sources: list[str]) -> dict:
        """Submit fight result for an event. Expects LLM to provide cleaned data."""
        return {"status": "pending", "message": "submit_fight_result: define full schema"}

    def _submit_fight_stats(self, fighter_name: str, event_name: str, stats: dict,
                           source: str) -> dict:
        """Submit per-fight stats. Expects LLM to provide stats dict."""
        return {"status": "pending", "message": "submit_fight_stats: define full schema"}

    def _flag_issue(self, entity: str, reason: str) -> dict:
        """Flag issue for human review."""
        return self.db_tools.flag_issue(entity, reason)

    def _schema(self, name: str) -> dict:
        """Return OpenAI tool schema for a tool."""
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
                "description": "Fetch and clean a page. Allowed: ufcstats.com, ufc.com, espn.com, etc.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Page URL"}
                    },
                    "required": ["url"]
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
            "submit_fight_result": {
                "name": "submit_fight_result",
                "description": "Submit fight result (winner, method, round, time) with source URLs",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_name": {"type": "string"},
                        "winner": {"type": "string"},
                        "method": {"type": "string", "enum": ["win", "draw", "no_contest"]},
                        "round": {"type": "integer", "minimum": 1},
                        "time_seconds": {"type": "integer", "minimum": 0},
                        "sources": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["event_name", "method", "round", "time_seconds", "sources"]
                }
            },
            "submit_fight_stats": {
                "name": "submit_fight_stats",
                "description": "Submit per-fight stats (strikes, takedowns, etc.)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fighter_name": {"type": "string"},
                        "event_name": {"type": "string"},
                        "stats": {
                            "type": "object",
                            "properties": {
                                "knockdowns": {"type": "integer", "minimum": 0},
                                "sig_strikes_landed": {"type": "integer", "minimum": 0},
                                "sig_strikes_attempted": {"type": "integer", "minimum": 0},
                                "takedowns_landed": {"type": "integer", "minimum": 0},
                                "takedowns_attempted": {"type": "integer", "minimum": 0},
                                "submission_attempts": {"type": "integer", "minimum": 0},
                                "control_time_seconds": {"type": "integer", "minimum": 0},
                            }
                        },
                        "source": {"type": "string"}
                    },
                    "required": ["fighter_name", "event_name", "stats", "source"]
                }
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
