"""One chat interface over several OpenAI-compatible providers, tried in order.

Each provider has its own requests-per-minute limiter. When a provider is rate limited,
down, or returns an unusable response, the next provider in the chain is tried.

The chain comes from LLM_CHAIN in .env, e.g.
    LLM_CHAIN=gemini:gemini-2.5-flash,openrouter:nvidia/nemotron-3-super-120b-a12b:free
"""
import collections
import threading
import time
from dataclasses import dataclass, field

import openai

from ufc_agent.config import optional_env, require_env

PROVIDER_ENDPOINTS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}
# Free-tier request limits per minute. Check the provider docs; they change.
DEFAULT_RPM = {"gemini": 8, "openrouter": 15}
DEFAULT_CHAIN = "gemini:gemini-2.5-flash,openrouter:nvidia/nemotron-3-super-120b-a12b:free"
REASONING_EFFORTS = ("none", "low", "medium", "high")


class AllProvidersFailed(Exception):
    pass


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
        raise ValueError(f"Bad LLM_CHAIN entry {spec!r}; expected '<gemini|openrouter>:<model>'")
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
    def __init__(self, providers, clock=time.monotonic, cooldown_seconds=60, reasoning_effort=None):
        if not providers:
            raise ValueError("At least one provider is required")
        self.providers = providers
        self.clock = clock
        self.cooldown_seconds = cooldown_seconds
        self.reasoning_effort = reasoning_effort

    @classmethod
    def from_env(cls):
        chain = optional_env("LLM_CHAIN", DEFAULT_CHAIN)
        return cls(
            [build_provider(spec.strip()) for spec in chain.split(",") if spec.strip()],
            reasoning_effort=optional_env("LLM_REASONING_EFFORT", "low"),
        )

    def chat(self, messages, tools=None, temperature=0.0, reasoning_effort=None):
        effort = reasoning_effort or self.reasoning_effort
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
                provider.cooldown_until = self.clock() + self.cooldown_seconds
                errors.append(f"{provider.name}: rate limited ({exc.status_code})")
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
        raise AllProvidersFailed("; ".join(errors))
