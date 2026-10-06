import json

import httpx
import openai
import pytest

from ufc_agent.llm.client import AllProvidersFailed, LLMClient, Provider, RateLimiter


def completion(content="ok", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "c1", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }


def provider(name, handler, rpm=100):
    client = openai.OpenAI(
        api_key="test", base_url="http://test.local/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return Provider(name=name, model=f"{name}-model", client=client, limiter=RateLimiter(rpm, sleep=lambda s: None))


def test_returns_first_provider_response():
    llm = LLMClient([provider("a", lambda r: httpx.Response(200, json=completion("hello")))])
    response = llm.chat([{"role": "user", "content": "hi"}])
    assert response.message.content == "hello"
    assert (response.provider, response.input_tokens, response.output_tokens) == ("a", 10, 3)
    assert response.errors == []


def test_falls_back_on_rate_limit_and_cools_down_provider():
    calls = {"a": 0}

    def rate_limited(request):
        calls["a"] += 1
        return httpx.Response(429, json={"error": {"message": "quota"}})

    now = [0.0]
    llm = LLMClient(
        [provider("a", rate_limited), provider("b", lambda r: httpx.Response(200, json=completion("from b")))],
        clock=lambda: now[0],
    )
    first = llm.chat([{"role": "user", "content": "hi"}])
    assert first.provider == "b"
    assert "a: rate limited" in first.errors[0]

    second = llm.chat([{"role": "user", "content": "hi"}])
    assert second.provider == "b"
    assert calls["a"] == 1  # still cooling down, not retried

    now[0] = 61
    llm.chat([{"role": "user", "content": "hi"}])
    assert calls["a"] == 2


def test_falls_back_on_server_error():
    llm = LLMClient([
        provider("a", lambda r: httpx.Response(503, json={"error": {"message": "down"}})),
        provider("b", lambda r: httpx.Response(200, json=completion())),
    ])
    assert llm.chat([{"role": "user", "content": "hi"}]).provider == "b"


def test_raises_when_all_fail():
    llm = LLMClient([provider("a", lambda r: httpx.Response(500, json={"error": {"message": "x"}}))])
    with pytest.raises(AllProvidersFailed, match="a: HTTP 500"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_passes_tools_and_parses_tool_calls():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=completion(None, [{
            "id": "t1", "type": "function", "function": {"name": "get_event", "arguments": '{"name": "UFC 322"}'},
        }]))

    tools = [{"type": "function", "function": {"name": "get_event", "parameters": {"type": "object"}}}]
    response = LLMClient([provider("a", handler)]).chat([{"role": "user", "content": "hi"}], tools=tools)
    assert seen["body"]["tools"] == tools
    assert response.message.tool_calls[0].function.name == "get_event"


def test_rate_limiter_waits_when_window_full():
    now, sleeps = [0.0], []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(2, clock=lambda: now[0], sleep=sleep)
    limiter.wait()
    now[0] = 10
    limiter.wait()
    limiter.wait()
    assert sleeps == [50.0]


def test_reasoning_effort_per_provider():
    bodies = {}

    def capture(name):
        def handler(request):
            bodies[name] = json.loads(request.content)
            return httpx.Response(200, json=completion())
        return handler

    gemini = provider("gemini", capture("gemini"))
    openrouter = provider("openrouter", capture("openrouter"))
    LLMClient([gemini], reasoning_effort="low").chat([{"role": "user", "content": "hi"}])
    LLMClient([openrouter]).chat([{"role": "user", "content": "hi"}], reasoning_effort="none")
    assert bodies["gemini"]["reasoning_effort"] == "low"
    assert bodies["openrouter"]["reasoning"] == {"enabled": False}
    assert "reasoning_effort" not in bodies["openrouter"]


def test_no_reasoning_params_when_unset():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=completion())

    LLMClient([provider("gemini", handler)]).chat([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in seen["body"]


def test_rejects_unknown_reasoning_effort():
    llm = LLMClient([provider("gemini", lambda r: httpx.Response(200, json=completion()))])
    with pytest.raises(ValueError):
        llm.chat([{"role": "user", "content": "hi"}], reasoning_effort="max")


def test_quota_reset_seconds_reads_gemini_and_openrouter_errors():
    from ufc_agent.llm.client import quota_reset_seconds

    def error(body):
        return openai.RateLimitError("Rate limited", response=httpx.Response(429, request=httpx.Request("POST", "http://x")),
                                     body=body)

    gemini = error([{"error": {"message": "Quota exceeded, limit: 20. Please retry in 9h10m26.49s."}}])
    assert round(quota_reset_seconds(gemini)) == 9 * 3600 + 10 * 60 + 26
    openrouter = error({"message": "free-models-per-day", "metadata": {"headers": {"X-RateLimit-Reset": "1791331200000"}}})
    assert quota_reset_seconds(openrouter, now_epoch=1791331200 - 3600) == 3600
    assert quota_reset_seconds(error({"message": "slow down"})) is None


def test_daily_quota_benches_provider_and_raises_quota_exhausted():
    from ufc_agent.llm.client import QuotaExhausted

    body = {"error": {"message": "Quota exceeded. Please retry in 2h0m0s."}}
    llm = LLMClient([provider("a", lambda r: httpx.Response(429, json=body))], clock=lambda: 0.0)
    with pytest.raises(QuotaExhausted) as raised:
        llm.chat([{"role": "user", "content": "hi"}])
    assert raised.value.retry_in_seconds == 7200
    with pytest.raises(QuotaExhausted):  # still benched: fails without calling the provider
        llm.chat([{"role": "user", "content": "hi"}])


def test_short_shared_cooldown_is_waited_out():
    now, sleeps, calls = [0.0], [], {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=completion("back"))

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    llm = LLMClient([provider("a", handler)], clock=lambda: now[0], cooldown_seconds=30, sleep=sleep)
    with pytest.raises(AllProvidersFailed):
        llm.chat([{"role": "user", "content": "hi"}])
    assert llm.chat([{"role": "user", "content": "hi"}]).message.content == "back"
    assert sleeps == [30.0]


def test_opencode_provider_is_built_from_chain(monkeypatch):
    from ufc_agent.llm.client import build_provider, reasoning_params

    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    provider_ = build_provider("opencode:space-bunny-free")
    assert (provider_.name, provider_.model) == ("opencode", "space-bunny-free")
    assert str(provider_.client.base_url).startswith("https://opencode.ai/zen/v1")
    assert reasoning_params("opencode", "none") == {}  # unknown support: send nothing
