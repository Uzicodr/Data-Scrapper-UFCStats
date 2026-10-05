"""Polite page fetching: domain allowlist, per-host throttle, retries, disk cache.

Also solves the ufcstats.com proof-of-work browser check (moved from ufcstats_client.py).
"""
import hashlib
import re
import threading
import time
from urllib.parse import urljoin, urlparse

import httpx

from ufc_agent.config import CACHE_DIR

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

DEFAULT_ALLOWED_DOMAINS = (
    "ufcstats.com",
    "ufc.com",
    "espn.com",
    "wikipedia.org",
    "sherdog.com",
    "tapology.com",
    "mmajunkie.usatoday.com",
    "mmafighting.com",
    "bloodyelbow.com",
)

CHALLENGE_RE = re.compile(
    r'var nonce="(?P<nonce>[^"]+)".*?target=new Array\((?P<difficulty>\d+)\+1\)',
    re.S,
)
RETRY_STATUSES = {429, 500, 502, 503, 504}


class FetchError(Exception):
    pass


class DomainNotAllowed(FetchError):
    pass


class RequestBudgetExceeded(FetchError):
    pass


def host_allowed(url, allowed_domains):
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in allowed_domains)


def solve_challenge(html):
    """Return (nonce, n) for the ufcstats.com browser check, or None if there is no challenge."""
    match = CHALLENGE_RE.search(html)
    if not match:
        return None
    nonce = match.group("nonce")
    prefix = "0" * int(match.group("difficulty"))
    n = 0
    while not hashlib.sha256(f"{nonce}:{n}".encode()).hexdigest().startswith(prefix):
        n += 1
    return nonce, n


class Fetcher:
    def __init__(
        self,
        allowed_domains=DEFAULT_ALLOWED_DOMAINS,
        min_interval=2.0,
        max_requests=200,
        max_retries=3,
        cache_dir=CACHE_DIR,
        cache_ttl=6 * 3600,
        client=None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.allowed_domains = tuple(allowed_domains)
        self.min_interval = min_interval
        self.max_requests = max_requests
        self.max_retries = max_retries
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True
        )
        self.sleep = sleep
        self.clock = clock
        self.requests_made = 0
        self._last_request = {}
        self._lock = threading.Lock()

    # -- cache -------------------------------------------------------------
    def _cache_path(self, url):
        return self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.html" if self.cache_dir else None

    def _read_cache(self, url):
        path = self._cache_path(url)
        if path and path.exists() and time.time() - path.stat().st_mtime < self.cache_ttl:
            return path.read_text(encoding="utf-8")
        return None

    def _write_cache(self, url, html):
        path = self._cache_path(url)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")

    # -- requests ----------------------------------------------------------
    def _throttle(self, host):
        with self._lock:
            if self.requests_made >= self.max_requests:
                raise RequestBudgetExceeded(f"Request budget of {self.max_requests} used up")
            wait = self._last_request.get(host, float("-inf")) + self.min_interval - self.clock()
            if wait > 0:
                self.sleep(wait)
            self._last_request[host] = self.clock()
            self.requests_made += 1

    def _request(self, method, url, **kwargs):
        host = urlparse(url).hostname or ""
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise FetchError(f"{method} {url} failed: {exc}") from exc
                self.sleep(2 ** attempt)
                continue
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                retry_after = response.headers.get("retry-after", "")
                self.sleep(float(retry_after) if retry_after.isdigit() else 2 ** (attempt + 1))
                continue
            if response.status_code >= 400:
                raise FetchError(f"{method} {url} returned HTTP {response.status_code}")
            return response
        raise FetchError(f"{method} {url} failed after {self.max_retries} retries")

    def get(self, url, use_cache=True):
        """Return the page HTML."""
        if not host_allowed(url, self.allowed_domains):
            raise DomainNotAllowed(f"{urlparse(url).hostname} is not in the allowed domains")
        if use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        html = self._request("GET", url).text
        challenge = solve_challenge(html) if "Checking your browser" in html else None
        if challenge:
            nonce, n = challenge
            self._request("POST", urljoin(url, "/__c"), data={"nonce": nonce, "n": n})
            html = self._request("GET", url).text

        self._write_cache(url, html)
        return html

    def fetch(self, url, max_chars=8000):
        """Fetch page and extract clean text. Returns (title, text, truncated)."""
        from ufc_agent.fetch.extract import html_to_text
        html = self.get(url)
        return html_to_text(html, max_chars=max_chars)
