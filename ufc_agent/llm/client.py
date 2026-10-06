"""One chat interface over several OpenAI-compatible providers, tried in order.

Each provider has its own requests-per-minute limiter. When a provider is rate limited,
down, or returns an unusable response, the next provider in the chain is tried.

The chain comes from LLM_CHAIN in .env, e.g.
    LLM_CHAIN=gemini:gemini-2.5-flash,openrouter:nvidia/nemotron-3-super-120b-a12b:free

A 429 benches the provider until the reset time the error names (free tiers also have daily caps:
gemini-2.5-flash allows 20 requests a day, OpenRouter free models 50 without credits), or for
cooldown_seconds when it names none. When every provider is benched for a short while, chat()
waits; when the wait would be long, it raises QuotaExhausted.
"""
import collections
import re
import threading
import time
from dataclasses import dataclass, field

import openai

from ufc_agent.config import optional_env, require_env

PROVIDER_ENDPOINTS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    # OpenCode Zen. Its free models refuse API use outside the OpenCode app (403 FreeTierError);
    # only space-bunny-free answered when this was written. Check their terms before relying on it.
    "opencode": ("https://opencode.ai/zen/v1", "OPENCODE_API_KEY"),
}
# Free-tier request limits per minute. Check the provider docs; they change.
DEFAULT_RPM = {"gemini": 8, "openrouter": 15, "opencode": 10}
DEFAULT_CHAIN = "gemini:gemini-2.5-flash,openrouter:nvidia/nemotron-3-super-120b-a12b:free"
REASONING_EFFORTS = ("none", "low", "medium", "high")


class AllProvidersFailed(Exception):
    pass


class QuotaExhausted(AllProvidersFailed):
    """Every provider is rate limited for longer than the client is willing to wait."""

    def __init__(self, message, retry_in_seconds):
        super().__init__(message)
        self.retry_in_seconds = retry_in_seconds


def quota_reset_seconds(exc, now_epoch=None):
    """Seconds until a 429 says the quota resets, or None when the error does not say.

    Gemini writes "Please retry in 9h10m26.49s"; OpenRouter sends X-RateLimit-Reset as epoch milliseconds.
    """
    text = f"{exc.message} {exc.body}"
    match = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", text)
    if match and any(match.groups()):
        hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    match = re.search(r"X-RateLimit-Reset['\"]?\s*:\s*['\"]?(\d{10,13})", text)
    if match:
        reset = int(match.group(1))
        reset = reset / 1000 if reset > 10 ** 11 else reset
        return max(0.0, reset - (now_epoch if now_epoch is not None else time.time()))
    retry_after = exc.response.headers.get("retry-after", "") if exc.response is not None else ""
    return float(retry_after) if retry_after.replace(".", "", 1).isdigit() else None


def format_duration(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}h{rest // 60:02d}m" if hours else f"{rest // 60}m{rest % 60:02d}s"


class RateLimiter:
    """Sliding one-minute window."""

    def __init__(self, rpm, clock=time.monotonic, sleep=time.sleep):
        self.rpm = rpm
        self.clock = clock
        self.sleep = sleep
        self.calls = collections.deque()
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = self.clock()
            while self.calls and now - self.calls[0] >= 60:
                self.calls.popleft()
            if len(self.calls) >= self.rpm:
                self.sleep(60 - (now - self.calls[0]))
                self.calls.popleft()
            self.calls.append(self.clock())


@dataclass
class Provider:
    name: str
    model: str
    client: openai.OpenAI
    limiter: RateLimiter
    cooldown_until: float = 0.0


@dataclass
class LLMResponse:
    message: object  # openai ChatCompletionMessage
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int
    errors: list = field(default_factory=list)  # failures from providers tried before this one
    finish_reason: str = None


def build_provider(spec, http_client=None):
    name, _, model = spec.partition(":")
    if name not in PROVIDER_ENDPOINTS or not model:
        raise ValueError(f"Bad LLM_CHAIN entry {spec!r}; expected '<gemini|openrouter|opencode>:<model>'")
    base_url, key_env = PROVIDER_ENDPOINTS[name]
    client = openai.OpenAI(
        api_key=require_env(key_env), base_url=base_url, max_retries=0, timeout=120, http_client=http_client
    )
    rpm = int(optional_env(f"{name.upper()}_RPM", DEFAULT_RPM[name]))
    return Provider(name=name, model=model, client=client, limiter=RateLimiter(rpm))


def reasoning_params(provider_name, effort):
    if effort is None:
        return {}
    if effort not in REASONING_EFFORTS:
        raise ValueError(f"reasoning_effort must be one of {REASONING_EFFORTS}, got {effort!r}")
    if provider_name == "gemini":
        return {"reasoning_effort": effort}
    if provider_name == "openrouter":
        reasoning = {"enabled": False} if effort == "none" else {"effort": effort}
        return {"extra_body": {"reasoning": reasoning}}
    return {}


class LLMClient:
    def __init__(self, providers, clock=time.monotonic, cooldown_seconds=60, reasoning_effort=None,
                 max_wait_seconds=120, sleep=time.sleep):
        if not providers:
            raise ValueError("At least one provider is required")
        self.providers = providers
        self.clock = clock
        self.cooldown_seconds = cooldown_seconds
        self.reasoning_effort = reasoning_effort
        self.max_wait_seconds = max_wait_seconds
        self.sleep = sleep

    def _wait_if_all_benched(self):
        """Sleep through a short shared cooldown; raise QuotaExhausted for a long one."""
        now = self.clock()
        if not all(p.cooldown_until > now for p in self.providers):
            return
        wait = min(p.cooldown_until for p in self.providers) - now
        if wait > self.max_wait_seconds:
            names = ", ".join(f"{p.name} for {format_duration(p.cooldown_until - now)}" for p in self.providers)
            raise QuotaExhausted(f"All providers are out of quota ({names})", retry_in_seconds=wait)
        self.sleep(wait)

    @classmethod
    def from_env(cls):
        chain = optional_env("LLM_CHAIN", DEFAULT_CHAIN)
        return cls(
            [build_provider(spec.strip()) for spec in chain.split(",") if spec.strip()],
            reasoning_effort=optional_env("LLM_REASONING_EFFORT", "low"),
        )

    def chat(self, messages, tools=None, temperature=0.0, reasoning_effort=None):
        effort = reasoning_effort or self.reasoning_effort
        self._wait_if_all_benched()
        errors = []
        for provider in self.providers:
            if provider.cooldown_until > self.clock():
                errors.append(f"{provider.name}: cooling down after rate limit")
                continue
            provider.limiter.wait()
            started = time.monotonic()
            try:
                response = provider.client.chat.completions.create(
                    model=provider.model,
                    messages=messages,
                    tools=tools or openai.NOT_GIVEN,
                    temperature=temperature,
                    **reasoning_params(provider.name, effort),
                )
            except openai.RateLimitError as exc:
                reset = quota_reset_seconds(exc)
                provider.cooldown_until = self.clock() + (reset if reset is not None else self.cooldown_seconds)
                errors.append(f"{provider.name}: rate limited ({exc.status_code})"
                              + (f", resets in {format_duration(reset)}" if reset else ""))
                continue
            except openai.APIStatusError as exc:
                # Server errors and provider-specific 4xx (e.g. an unsupported parameter) both
                # move on to the next provider; the reason is kept for the run log.
                errors.append(f"{provider.name}: HTTP {exc.status_code} {exc.message[:200]}")
                continue
            except (openai.APIConnectionError, openai.APITimeoutError) as exc:
                errors.append(f"{provider.name}: {type(exc).__name__}")
                continue

            if not response.choices:
                errors.append(f"{provider.name}: empty response")
                continue
            usage = response.usage
            return LLMResponse(
                message=response.choices[0].message,
                provider=provider.name,
                model=provider.model,
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                duration_ms=int((time.monotonic() - started) * 1000),
                errors=errors,
                finish_reason=response.choices[0].finish_reason,
            )
        now = self.clock()
        if all(p.cooldown_until > now for p in self.providers):
            # Every provider just hit its limit: report how long until the first one is back.
            wait = min(p.cooldown_until for p in self.providers) - now
            if wait > self.max_wait_seconds:
                raise QuotaExhausted("; ".join(errors), retry_in_seconds=wait)
        raise AllProvidersFailed("; ".join(errors))
