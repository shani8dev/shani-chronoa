"""Free, no-API-key cloud LLM fallback for when Ollama isn't available.

Chronoa is local-first by design (see config.py's `PrivacyManager`) - this
module exists only as an explicit, opt-in fallback for machines without
Ollama installed, NOT a silent default. `app.py` only ever reaches for it
when privacy mode has been turned OFF (an explicit user action, see
`_toggle_privacy`) AND Ollama is unavailable AND the user has opted in via
the `cloud-fallback-enabled` gsetting. Talking to any of these means your
prompts and tool-call arguments leave the machine.

The providers below are the free, keyless OpenAI-compatible gateways
documented in shani-docs' AI-Assisted Development page. None of this is
theoretical: every provider here was live-tested from this machine on
2026-09-16 with a real Chronoa-shaped tool-call request (the `get_datetime`
skill's actual schema), not just read about:

- **LLM7** (`api.llm7.io/v1`) - worked cleanly for an isolated single-turn
  tool call and an isolated second turn with a tool result fed back in, but
  running it through Chronoa's actual `Assistant.handle()` loop surfaced two
  more real issues: it re-called `get_datetime` a second time instead of
  concluding after getting the result (harmless here only because
  `assistant.py`'s `MAX_TOOL_ROUNDS` caps this and forces a final answer
  afterward - a model that doesn't reliably recognize "I have enough
  information now" will waste rounds on any harness), and separately
  returned a raw non-JSON HTTP 502 body on a later request (transient
  upstream error, correctly caught as a failure and routed to the next
  provider - but would have crashed outright if `chat_message()` assumed
  every response was parseable JSON). Also rate-limits concurrent requests
  per client (`concurrent_request_limit_exceeded`, with a `retry_after`
  hint) - hit repeatedly just from this module's own back-to-back testing.
  shani-builder's `ai-ci-fixer.yml` separately documented this gateway
  breaking outright on a second turn for a different harness/model
  combination. None of this makes LLM7 unusable - it answered correctly
  every time it wasn't rate-limited or erroring - but "usually works,
  several distinct rough edges under real multi-turn use" is the honest
  characterization, not "reliable."
- **Kilo Gateway** (`api.kilo.ai/api/gateway`) and **BlockRun**
  (`blockrun.ai/api/v1`) - both returned **HTTP 200 with an `"error"` key in
  the JSON body** (rate-limited: Kilo's upstream Poolside model, BlockRun's
  entire free tier - "Free model capacity exhausted") rather than a non-2xx
  status. `raise_for_status()` alone does NOT catch this - checking for an
  `"error"` key in the parsed body is mandatory before touching
  `choices[0]`, confirmed by hitting this exact shape live. BlockRun's
  `cohere/north-mini-code` is the one model `ai-ci-fixer.yml` verified
  end-to-end on a real multi-step agent task, but its free quota is
  shared/global and can be (and was, when tested here) already exhausted
  for everyone.
- **OpenCode Zen** (`opencode.ai/zen/v1`) - returned a real 401 "Invalid API
  key" on an unauthenticated request, contradicting shani-docs' claim of
  "no key of your own required." Excluded from the default provider chain
  until that's resolved - either the docs are stale or Zen's free tier
  needs a registered (if free) key now, not truly anonymous access.

Because free-tier availability is this volatile - two of four providers
were already rate-limited/exhausted at first test, one contradicted its own
documentation - this is implemented as a fallback **chain**, not a single
hardcoded "best" provider: `CloudLLMChain` tries each configured provider in
order and only fails if all of them do.

## BYOK providers (OpenAI, Anthropic, Google Gemini, Groq)

Unlike the three above, these four are NOT keyless - confirmed live on
2026-09-16 with an unauthenticated request to each: Anthropic and Groq both
returned a real `401`; Google's OpenAI-compatible endpoint returned `400
"Missing or invalid Authorization header"`. All four require the user to
actually configure an API key (`config.cloud_llm_api_keys()`); `CloudLLMChain`
skips constructing a backend for any of these that has no key configured,
rather than building one that's guaranteed to fail on the first real
request. When a key *is* configured, `app.py` puts these ahead of the free
chain, since they're presumably far more capable/reliable than an anonymous
free tier.

OpenAI, Groq, and Google's OpenAI-compatible endpoint all reuse
`OpenAICompatibleLLM` directly - same wire format. **Anthropic does not**:
its native Messages API uses a different shape entirely (a top-level
`system` field instead of a system-role message, `x-api-key`/
`anthropic-version` headers instead of `Authorization: Bearer`, tool
results sent as user-role `tool_result` content blocks instead of a `tool`
role, and a `content` block list in the response instead of `choices`).
`AnthropicLLM` below translates Chronoa's shared OpenAI/Ollama-shaped
`_history` into Anthropic's format and translates the response back, so
`assistant.py` never needs to know which wire format is actually in use.
Verified live only as far as *reachability and request shape* go (a
request with no key correctly reaches Anthropic's real endpoint and gets a
401 rather than some earlier network or format-related failure) - there is
no real Anthropic API key in this dev environment to verify an actual
authenticated round trip, so the message/tool translation logic itself is
only unit-tested against synthetic conversations here, not proven against
the live API. Re-verify with a real key before fully trusting it.
"""

import asyncio
import json
import logging
from typing import NamedTuple, Optional

import httpx

from shani_chronoa.skills import is_valid_schema

logger = logging.getLogger(__name__)


class CloudProvider(NamedTuple):
    id: str
    name: str
    base_url: str
    default_model: str
    requires_key: bool = False


# Order matters: this is the default fallback sequence, chosen by the live
# test above, not alphabetically or from the source config. OpenCode Zen is
# deliberately omitted - see module docstring.
PROVIDERS: dict[str, CloudProvider] = {
    "llm7": CloudProvider("llm7", "LLM7", "https://api.llm7.io/v1", "default"),
    "kilo": CloudProvider("kilo", "Kilo Gateway", "https://api.kilo.ai/api/gateway", "kilo-auto/free"),
    "blockrun": CloudProvider("blockrun", "BlockRun", "https://blockrun.ai/api/v1", "cohere/north-mini-code"),
    "openai": CloudProvider("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o-mini", requires_key=True),
    "groq": CloudProvider("groq", "Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", requires_key=True),
    "google": CloudProvider(
        "google", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.0-flash", requires_key=True,
    ),
}

DEFAULT_PROVIDER_ORDER = ("llm7", "kilo", "blockrun")

# BYOK-required providers, tried ahead of the free chain when a key is
# configured for them (see app.py's cloud-fallback wiring). Anthropic is
# handled separately (see AnthropicLLM) since it isn't OpenAI-compatible.
BYOK_PROVIDER_ORDER = ("anthropic", "openai", "google", "groq")

_ANTHROPIC_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_ANTHROPIC_MAX_TOKENS = 1024
_ANTHROPIC_API_VERSION = "2023-06-01"


class CloudLLMError(Exception):
    """Raised when a single provider's request fails or returns an error body."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _valid_tools(tools: Optional[list]) -> list:
    """Filter tool schemas down to well-formed function schemas.

    Malformed entries (non-dicts, missing function objects, bad names) are
    skipped with a warning instead of being sent to a provider or crashing
    translation - a malformed user skill must never take down the cloud
    fallback path.
    """
    valid = []
    for tool in tools or []:
        if is_valid_schema(tool):
            valid.append(tool)
        else:
            logger.warning(f"Skipping malformed tool schema in cloud request: {tool!r}")
    return valid


class OpenAICompatibleLLM:
    """One OpenAI-compatible /chat/completions endpoint (a single provider).

    `api_key`, if given, is a user-supplied key for this specific gateway
    (BYOK) - Kilo's own rate-limit error message during live testing
    explicitly suggested this ("add your own key to accumulate your rate
    limits"), so it's a real, evidence-backed way to work around the
    anonymous-access rate limits documented in this module's docstring, not
    a speculative feature. Falls back to the placeholder "unused" bearer
    token when no key is configured, same as before.
    """

    def __init__(self, provider: CloudProvider, model: Optional[str] = None, api_key: str = "") -> None:
        self.provider = provider
        self.model = model or provider.default_model
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.provider.base_url,
                timeout=httpx.Timeout(60.0, connect=10.0),
                # Some of these gateways 401 on a totally absent Authorization
                # header even when the key value itself is never checked -
                # matches ai-ci-fixer.yml's "OPENAI_API_KEY: unused" note.
                headers={"Authorization": f"Bearer {self.api_key or 'unused'}"},
            )
        return self._client

    async def chat_message(self, messages: list[dict], tools: Optional[list[dict]] = None, stream: bool = False) -> dict:
        """Send one chat request. Raises CloudLLMError on any failure, including
        a 200 response whose body is actually an error (confirmed live for
        both Kilo and BlockRun - see module docstring)."""
        client = await self._get_client()
        payload: dict = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = _valid_tools(tools)

        try:
            response = await client.post("/chat/completions", json=payload)
        except httpx.RequestError as e:
            raise CloudLLMError(f"{self.provider.name}: request failed: {e}") from e

        try:
            data = response.json()
        except ValueError as e:
            raise CloudLLMError(f"{self.provider.name}: non-JSON response (status {response.status_code})") from e

        # Confirmed live: Google's OpenAI-compatible endpoint wraps an error
        # body in a JSON array (`[{"error": {...}}]`) instead of a plain
        # object like every other provider tested - `data.get(...)` would
        # crash with AttributeError on a list. Unwrap defensively.
        if isinstance(data, list):
            data = data[0] if data else {}

        if response.status_code >= 400 or (isinstance(data, dict) and "error" in data):
            detail = data.get("error", data) if isinstance(data, dict) else data
            # Some gateways (confirmed live: LLM7) put a numeric-seconds
            # "retry_after" at the top level of the error body on a
            # concurrent-request rate limit rather than an HTTP Retry-After
            # header - worth honoring with a short bounded wait before
            # falling through to the next provider, since the whole point
            # of the chain is to prefer "wait a beat" over "give up".
            retry_after = detail.get("retry_after") if isinstance(detail, dict) else None
            raise CloudLLMError(f"{self.provider.name}: {detail}", retry_after=retry_after)

        choices = data.get("choices") or []
        if not choices:
            raise CloudLLMError(f"{self.provider.name}: response had no choices")
        return choices[0].get("message", {})

    async def check_health(self) -> bool:
        """Best-effort reachability check - lists models, doesn't spend a real request."""
        client = await self._get_client()
        try:
            response = await client.get("/models")
            return response.status_code == 200
        except Exception:
            return False


class AnthropicLLM:
    """Anthropic's native Messages API - see module docstring for why this
    isn't `OpenAICompatibleLLM` and what's actually been verified.
    """

    def __init__(self, api_key: str, model: str = _ANTHROPIC_DEFAULT_MODEL) -> None:
        # A CloudProvider-shaped attribute purely so CloudLLMChain's logging
        # (`backend.provider.name`) works the same for every backend type
        # without special-casing this one.
        self.provider = CloudProvider("anthropic", "Anthropic", "https://api.anthropic.com/v1", model, requires_key=True)
        self.api_key = api_key
        self.model = model
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.provider.base_url,
                timeout=httpx.Timeout(60.0, connect=10.0),
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": _ANTHROPIC_API_VERSION,
                },
            )
        return self._client

    @staticmethod
    def _tools_to_anthropic(tools: list[dict]) -> list[dict]:
        converted = []
        for t in _valid_tools(tools):
            fn = t["function"]
            converted.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return converted

    @staticmethod
    def _history_to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
        """Returns (system_prompt, anthropic_messages).

        Anthropic has no system-role message (a dedicated top-level field
        instead) and no tool-role message (a tool result is a user-role
        message with a `tool_result` content block, correlated to the
        preceding `tool_use` block by id - which is why `assistant.py` now
        carries `tool_call_id` on its tool-role messages; without it this
        would send an empty `tool_use_id` that can't match anything).
        """
        system_prompt = ""
        out: list[dict] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                system_prompt = msg.get("content", "") or system_prompt
            elif role == "user":
                out.append({"role": "user", "content": msg.get("content", "") or ""})
            elif role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    out.append({"role": "assistant", "content": msg.get("content") or ""})
                    continue
                blocks = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": msg["content"]})
                for i, call in enumerate(tool_calls):
                    fn = call.get("function", {})
                    arguments = fn.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": call.get("id") or f"{fn.get('name', 'tool')}_{i}",
                        "name": fn.get("name", ""),
                        "input": arguments,
                    })
                out.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": msg.get("content", ""),
                    }],
                })
        return system_prompt, out

    async def chat_message(self, messages: list[dict], tools: Optional[list[dict]] = None, stream: bool = False) -> dict:
        client = await self._get_client()
        system_prompt, anthropic_messages = self._history_to_anthropic(messages)
        payload: dict = {
            "model": self.model,
            "max_tokens": _ANTHROPIC_MAX_TOKENS,
            "messages": anthropic_messages,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = self._tools_to_anthropic(tools)

        try:
            response = await client.post("/messages", json=payload)
        except httpx.RequestError as e:
            raise CloudLLMError(f"Anthropic: request failed: {e}") from e

        try:
            data = response.json()
        except ValueError as e:
            raise CloudLLMError(f"Anthropic: non-JSON response (status {response.status_code})") from e

        if response.status_code >= 400 or "error" in data:
            raise CloudLLMError(f"Anthropic: {data.get('error', data)}")

        # Translate Anthropic's content-block response back into the
        # OpenAI/Ollama-shaped message dict the rest of Chronoa expects, so
        # assistant.py never needs to know which backend answered.
        text_parts = []
        tool_calls = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })

        message: dict = {"role": "assistant", "content": "".join(text_parts)}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message


class CloudLLMChain:
    """Tries a sequence of providers in order, falling through on failure.

    Presents the same `chat_message()` interface as `OllamaLLM` so `app.py`
    can swap it in as a drop-in replacement for `self.llm`. A provider that
    `requires_key` (the BYOK ones) and has no key configured is skipped
    entirely rather than built and left to fail its first real request -
    we already know from live testing that it would just 401/400.
    """

    def __init__(self, provider_ids: tuple = DEFAULT_PROVIDER_ORDER, api_keys: Optional[dict] = None) -> None:
        api_keys = api_keys or {}
        backends: list = []
        for p in provider_ids:
            key = api_keys.get(p, "")
            if p == "anthropic":
                if key:
                    backends.append(AnthropicLLM(api_key=key, model=api_keys.get("anthropic_model") or _ANTHROPIC_DEFAULT_MODEL))
                continue
            provider = PROVIDERS.get(p)
            if provider is None:
                logger.warning(f"Unknown cloud LLM provider ignored: {p}")
                continue
            if provider.requires_key and not key:
                continue
            backends.append(OpenAICompatibleLLM(provider, api_key=key))
        self._backends = backends
        self.model = ", ".join(b.model for b in self._backends) or "(none)"

    _MAX_RETRY_WAIT = 10.0  # cap how long we'll wait on a provider's own retry_after hint

    async def chat_message(self, messages: list[dict], tools: Optional[list[dict]] = None, stream: bool = False) -> dict:
        last_error: Optional[Exception] = None
        for backend in self._backends:
            for attempt in range(2):  # one retry after a provider's own retry_after hint, then move on
                try:
                    message = await backend.chat_message(messages, tools=tools, stream=stream)
                    logger.info(f"Cloud LLM fallback answered via {backend.provider.name} ({backend.model})")
                    return message
                except CloudLLMError as e:
                    last_error = e
                    if attempt == 0 and e.retry_after:
                        wait = min(float(e.retry_after), self._MAX_RETRY_WAIT)
                        logger.warning(f"{backend.provider.name} rate-limited, retrying in {wait:.0f}s: {e}")
                        await asyncio.sleep(wait)
                        continue
                    logger.warning(f"Cloud LLM provider failed, trying next: {e}")
                    break
        raise ConnectionError(f"All cloud LLM providers failed. Last error: {last_error}")

    def is_available(self) -> bool:
        """True if at least one configured provider is present (not a live check -
        availability is checked per-request since free-tier quotas fluctuate
        minute to minute, confirmed live: see module docstring)."""
        return len(self._backends) > 0
