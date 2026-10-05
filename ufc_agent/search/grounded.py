"""Web search through Gemini's Grounding with Google Search.

Runs as its own Gemini call, separate from the agent loop, so the agent can use any LLM
provider. Returns a short answer plus the source pages Google used.
"""
from dataclasses import asdict, dataclass

import httpx
from google import genai
from google.genai import types

from ufc_agent.config import optional_env, require_env

SEARCH_INSTRUCTIONS = (
    "You are a research assistant for UFC data. Answer the query using Google Search results only. "
    "Be factual and concise. Include exact names, dates, weight classes, results, methods, rounds "
    "and times when relevant. If the results do not answer the query, say so plainly. Never guess."
)
REDIRECT_PREFIX = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


@dataclass
class Source:
    title: str
    url: str


@dataclass
class SearchResult:
    query: str
    answer: str
    sources: list

    def to_dict(self):
        return {"query": self.query, "answer": self.answer, "sources": [asdict(s) for s in self.sources]}


def resolve_redirect(url, http_client):
    """Grounding links point at a Google redirect; return the real page URL when possible."""
    if not url.startswith(REDIRECT_PREFIX):
        return url
    try:
        response = http_client.head(url, follow_redirects=False, timeout=10)
        return response.headers.get("location") or url
    except httpx.HTTPError:
        return url


def parse_response(query, response, http_client=None):
    candidate = (response.candidates or [None])[0]
    metadata = getattr(candidate, "grounding_metadata", None)
    sources, seen = [], set()
    for chunk in getattr(metadata, "grounding_chunks", None) or []:
        web = getattr(chunk, "web", None)
        if not web or not web.uri:
            continue
        url = resolve_redirect(web.uri, http_client) if http_client else web.uri
        if url in seen:
            continue
        seen.add(url)
        sources.append(Source(title=web.title or "", url=url))
    return SearchResult(query=query, answer=(response.text or "").strip(), sources=sources)


class GroundedSearch:
    def __init__(self, client=None, model=None, http_client=None):
        self.client = client or genai.Client(api_key=require_env("GEMINI_API_KEY"))
        self.model = model or optional_env("SEARCH_MODEL", "gemini-2.5-flash")
        self.http_client = http_client or httpx.Client()

    def search(self, query):
        response = self.client.models.generate_content(
            model=self.model,
            contents=query,
            config=types.GenerateContentConfig(
                system_instruction=SEARCH_INSTRUCTIONS,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.0,
            ),
        )
        return parse_response(query, response, self.http_client)
