import hashlib

import httpx
import pytest

from ufc_agent.fetch.http import (
    DomainNotAllowed,
    Fetcher,
    FetchError,
    RequestBudgetExceeded,
    host_allowed,
    solve_challenge,
)


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_fetcher(handler, tmp_path, **kwargs):
    fake = FakeTime()
    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache_dir=tmp_path,
        sleep=fake.sleep,
        clock=fake.clock,
        **kwargs,
    )
    return fetcher, fake


def test_host_allowed():
    domains = ("ufcstats.com", "ufc.com")
    assert host_allowed("http://ufcstats.com/statistics/events", domains)
    assert host_allowed("https://www.ufc.com/rankings", domains)
    assert not host_allowed("https://notufc.com/x", domains)
    assert not host_allowed("https://example.com/ufc.com", domains)


def test_rejects_disallowed_domain(tmp_path):
    fetcher, _ = make_fetcher(lambda request: httpx.Response(200, text="x"), tmp_path)
    with pytest.raises(DomainNotAllowed):
        fetcher.get("https://example.com/page")


def test_throttles_requests_to_same_host(tmp_path):
    fetcher, fake = make_fetcher(lambda request: httpx.Response(200, text="ok"), tmp_path, min_interval=2.0)
    fetcher.get("http://ufcstats.com/a", use_cache=False)
    fetcher.get("http://ufcstats.com/b", use_cache=False)
    assert fake.sleeps == [2.0]


def test_uses_cache(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, text="<p>page</p>")

    fetcher, _ = make_fetcher(handler, tmp_path)
    assert fetcher.get("http://ufcstats.com/a") == "<p>page</p>"
    assert fetcher.get("http://ufcstats.com/a") == "<p>page</p>"
    assert len(calls) == 1


def test_retries_on_429_then_succeeds(tmp_path):
    responses = iter([httpx.Response(429, headers={"retry-after": "5"}), httpx.Response(200, text="ok")])
    fetcher, fake = make_fetcher(lambda request: next(responses), tmp_path)
    assert fetcher.get("http://ufcstats.com/a", use_cache=False) == "ok"
    assert 5.0 in fake.sleeps


def test_raises_on_404(tmp_path):
    fetcher, _ = make_fetcher(lambda request: httpx.Response(404), tmp_path)
    with pytest.raises(FetchError):
        fetcher.get("http://ufcstats.com/missing", use_cache=False)


def test_request_budget(tmp_path):
    fetcher, _ = make_fetcher(lambda request: httpx.Response(200, text="ok"), tmp_path, max_requests=1)
    fetcher.get("http://ufcstats.com/a", use_cache=False)
    with pytest.raises(RequestBudgetExceeded):
        fetcher.get("http://ufcstats.com/b", use_cache=False)


def test_solves_browser_challenge(tmp_path):
    challenge_page = 'Checking your browser <script>var nonce="abc"; target=new Array(1+1)</script>'
    state = {"solved": False}

    def handler(request):
        if request.method == "POST":
            assert request.url.path == "/__c"
            state["solved"] = True
            return httpx.Response(200)
        return httpx.Response(200, text="real page" if state["solved"] else challenge_page)

    fetcher, _ = make_fetcher(handler, tmp_path)
    assert fetcher.get("http://ufcstats.com/a", use_cache=False) == "real page"


def test_solve_challenge_finds_valid_nonce():
    nonce, n = solve_challenge('var nonce="xyz"; target=new Array(2+1)')
    assert hashlib.sha256(f"{nonce}:{n}".encode()).hexdigest().startswith("00")
    assert solve_challenge("<p>no challenge</p>") is None
