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
- **OpenCode Zen** (`opencode.ai/zen/v1`) - the 401 "Invalid API key" seen
  here on 2026-09-16 came from Chronoa's own placeholder bearer (see below):
  without an Authorization header Zen answers - and refuses, with
  `FreeTierError: "OpenCode's free tier can only be used from within
  OpenCode"` (2026-10-01; restricted since 2026-09-23). That is Zen's rule,
  so Zen is only a **keyed** provider here (`opencode-zen-api-key`); its
  free tier is not used, and not reached by imitating OpenCode.

**Re-tested 2026-10-01**, chat plus a tool call with `get_datetime`'s real
schema: LLM7 works (codestral-latest, tool call made; 1 s rate limits when
requests come back-to-back). Kilo works - nvidia/nemotron-3-ultra via
kilo-auto/free, tool call made - but only WITHOUT an Authorization header:
the "Bearer unused" this module used to send got 401 "Your authentication
token is invalid", so Kilo had been dead in the chain. BlockRun: still 429
"Free model capacity exhausted". Considered and left out: Pollinations
(gen.pollinations.ai now needs a key; the legacy text endpoint answers
chat but 402s a tool call), OVHcloud AI Endpoints' anonymous tier
(2 requests/minute per IP and model; only 429s from here, so its tool calls
are unverified), ch.at (keyless chat, no tool calling), Hack Club AI and
APIFreeLLM (need an account key), and aggregators that only bundle the
user's own keys.

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

from shani_chronoa import egress, usage as usage_mod
from shani_chronoa.redaction import redactor
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
    # Keyed only: Zen's free tier answers anonymous requests with FreeTierError
    # "OpenCode's free tier can only be used from within OpenCode" (2026-10-01),
    # and that is the provider's rule to make. A paid Zen key is the sanctioned
    # way in, and goes through the same OpenAI-compatible endpoint.
    "opencode-zen": CloudProvider("opencode-zen", "OpenCode Zen", "https://opencode.ai/zen/v1",
                                  "gpt-5.4-mini", requires_key=True),
    # Keyed (401 "No cookie auth credentials found" without one, 2026-10-01),
    # but free with a free account key: `openrouter/free` is OpenRouter's own
    # router over whichever ':free' models are up (price 0, tool calls
    # supported), so it outlives any one free model. `openrouter/auto` picks
    # the best model per prompt but bills at that model's price - not a
    # default to choose for someone.
    "openrouter": CloudProvider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
                                "openrouter/free", requires_key=True),
    "google": CloudProvider(
        "google", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.0-flash", requires_key=True,
    ),
}

DEFAULT_PROVIDER_ORDER = ("llm7", "kilo", "blockrun")

# BYOK-required providers, tried ahead of the free chain when a key is
# configured for them (see app.py's cloud-fallback wiring). Anthropic is
# handled separately (see AnthropicLLM) since it isn't OpenAI-compatible.
BYOK_PROVIDER_ORDER = ("anthropic", "openai", "google", "groq", "openrouter", "opencode-zen")

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

    #: Token counts from the most recent call, or None before the first
    #: one. See `usage.py` for why this is not part of the returned message.
    last_usage: "usage_mod.Usage | None" = None

    #: Hooks a subclass fills in (local_llm.LocalLLM does): extra request
    #: fields, a rewrite of the messages before sending, a rewrite of the reply,
    #: and the read timeout - a CPU model can take minutes where a cloud
    #: gateway takes seconds.
    extra_payload: dict = {}
    #: set by the assistant for one request when the person named the tool ('/timer ...')
    required_tool: str = ""
    read_timeout: float = 60.0

    def prepare_messages(self, messages: list[dict]) -> list[dict]:
        return messages

    def postprocess(self, message: dict, tools: Optional[list[dict]]) -> dict:
        return message

    def __init__(self, provider: CloudProvider, model: Optional[str] = None, api_key: str = "") -> None:
        self.provider = provider
        self.model = model or provider.default_model
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        # one client per event loop: see llm.OllamaLLM._get_client
        loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or getattr(self, "_client_loop", None) is not loop:
            self._client_loop = loop
            self._client = httpx.AsyncClient(
                base_url=self.provider.base_url,
                timeout=httpx.Timeout(self.read_timeout, connect=10.0),
                # Only a real key is sent. A placeholder ("Bearer unused") used
                # to be sent to the keyless gateways, and by 2026-10-01 it was
                # what broke them: Kilo answered 401 "Your authentication token
                # is invalid" and OVHcloud 403, while both served the same
                # request - tool call included - with no Authorization header
                # at all. LLM7 accepts either.
                headers=({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            )
        return self._client

    async def chat_message(self, messages: list[dict], tools: Optional[list[dict]] = None, stream: bool = False) -> dict:
        """Send one chat request. Raises CloudLLMError on any failure, including
        a 200 response whose body is actually an error (confirmed live for
        both Kilo and BlockRun - see module docstring)."""
        client = await self._get_client()
        sanitized_messages = [
            {**msg, "content": redactor.sanitize(msg.get("content", ""))}
            for msg in self.prepare_messages(messages)
        ]
        payload: dict = {"model": self.model, "messages": sanitized_messages, **self.extra_payload}
        if tools:
            payload["tools"] = _valid_tools(tools)
            if self.required_tool:
                payload["tool_choice"] = {"type": "function", "function": {"name": self.required_tool}}

        status = None
        try:
            response = await client.post("/chat/completions", json=payload)
            status = response.status_code
        except httpx.RequestError as e:
            raise CloudLLMError(f"{self.provider.name}: request failed: {type(e).__name__}: {e}") from e
        finally:
            # Metadata only - never the payload. This is one of only two paths
            # that deliberately send a conversation off the machine, so it is
            # one of only two places a user most needs to be able to check.
            egress.record(
                f"cloud_llm:{self.provider.name}",
                f"{self.provider.base_url}/chat/completions",
                method="POST",
                status=status,
                bytes_out=egress.payload_size(payload),
                privacy_mode=egress.privacy_mode_enabled(),
            )

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
        # Every provider here speaks the OpenAI shape, so `usage` is uniform.
        # Recorded beside the message, not inside it: a message is appended to
        # history and re-sent as context on later turns.
        self.last_usage = usage_mod.from_openai(data)
        return self.postprocess(choices[0].get("message", {}), tools)

    #: Whether `chat_message_stream` may be used (the local llama-server: yes;
    #: the free gateways were only ever verified non-streaming).
    stream_supported: bool = False

    async def chat_message_stream(self, messages: list[dict], tools: Optional[list[dict]] = None,
                                  on_text=None) -> dict:
        """Like `chat_message`, but streamed: `on_text(delta)` gets the reply's words as they arrive.

        Tool-call fragments are assembled as they stream (OpenAI's indexed
        `delta.tool_calls`). Text stops being passed on once a tool call
        appears - it is preamble, not the answer - and a leading `<think>`
        block is held back. Bounded per chunk, not per request: the first
        token may take long on a CPU, but a stalled stream fails (assistd's
        inactivity timeout).
        """
        import json as _json
        client = await self._get_client()
        payload: dict = {"model": self.model, "stream": True, **self.extra_payload, "messages": [
            {**msg, "content": redactor.sanitize(msg.get("content", ""))}
            for msg in self.prepare_messages(messages)]}
        if tools:
            payload["tools"] = _valid_tools(tools)
        content, calls, status = [], {}, None
        held, released = "", False
        try:
            async with client.stream("POST", "/chat/completions", json=payload) as response:
                status = response.status_code
                if status >= 400:
                    body = (await response.aread()).decode(errors="replace")[:300]
                    raise CloudLLMError(f"{self.provider.name}: HTTP {status}: {body}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = _json.loads(data)
                    except ValueError:
                        continue
                    if chunk.get("usage"):
                        self.last_usage = usage_mod.from_openai(chunk)
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0), {"id": "", "type": "function",
                                                                          "function": {"name": "", "arguments": ""}})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["function"]["name"] += fn.get("name") or ""
                            slot["function"]["arguments"] += fn.get("arguments") or ""
                        text = delta.get("content") or ""
                        if not text:
                            continue
                        content.append(text)
                        if calls or on_text is None:
                            continue
                        if not released:
                            held += text
                            stripped = held.lstrip()
                            if stripped.startswith("<think>") or "<think>".startswith(stripped):
                                if "</think>" not in held:
                                    continue
                                held = held.split("</think>", 1)[1]
                            released = True
                            text, held = held.lstrip(), ""
                            if not text:
                                continue
                        on_text(text)
        except httpx.RequestError as e:
            raise CloudLLMError(f"{self.provider.name}: request failed: {type(e).__name__}: {e}") from e
        finally:
            egress.record(
                f"cloud_llm:{self.provider.name}", f"{self.provider.base_url}/chat/completions",
                method="POST", status=status, bytes_out=egress.payload_size(payload),
                privacy_mode=egress.privacy_mode_enabled(),
            )
        message: dict = {"role": "assistant", "content": "".join(content)}
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        return self.postprocess(message, tools)

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

    #: Token counts from the most recent call, or None before the first
    #: one. See `usage.py` for why this is not part of the returned message.
    last_usage: "usage_mod.Usage | None" = None


    def __init__(self, api_key: str, model: str = _ANTHROPIC_DEFAULT_MODEL) -> None:
        # A CloudProvider-shaped attribute purely so CloudLLMChain's logging
        # (`backend.provider.name`) works the same for every backend type
        # without special-casing this one.
        self.provider = CloudProvider("anthropic", "Anthropic", "https://api.anthropic.com/v1", model, requires_key=True)
        self.api_key = api_key
        self.model = model
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        # one client per event loop: see llm.OllamaLLM._get_client
        loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or getattr(self, "_client_loop", None) is not loop:
            self._client_loop = loop
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
        if system_prompt:
            system_prompt = redactor.sanitize(system_prompt)
        sanitized_messages = [
            {**msg, "content": redactor.sanitize(msg.get("content", ""))}
            for msg in anthropic_messages
        ]
        payload: dict = {
            "model": self.model,
            "max_tokens": _ANTHROPIC_MAX_TOKENS,
            "messages": sanitized_messages,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = self._tools_to_anthropic(tools)

        status = None
        try:
            response = await client.post("/messages", json=payload)
            status = response.status_code
        except httpx.RequestError as e:
            raise CloudLLMError(f"Anthropic: request failed: {type(e).__name__}: {e}") from e
        finally:
            egress.record(
                f"cloud_llm:{self.provider.name}",
                f"{self.provider.base_url}/messages",
                method="POST",
                status=status,
                bytes_out=egress.payload_size(payload),
                privacy_mode=egress.privacy_mode_enabled(),
            )

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

        # Anthropic names the same two counts `input_tokens`/`output_tokens`
        # rather than `prompt_tokens`/`completion_tokens`, so it gets its own
        # extractor instead of being folded into the OpenAI-compatible one.
        self.last_usage = usage_mod.from_anthropic(data)
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
        #: Proxied from whichever backend answered the last call. `assistant.py`
        #: reads this with `getattr(self.llm, "last_usage", None)`, so without it
        #: every cloud-fallback turn reports zero tokens - indistinguishable from
        #: a provider that sent no `usage` block at all.
        self.last_usage: "usage_mod.Usage | None" = None

    _MAX_RETRY_WAIT = 10.0  # cap how long we'll wait on a provider's own retry_after hint

    async def chat_message(self, messages: list[dict], tools: Optional[list[dict]] = None, stream: bool = False) -> dict:
        sanitized_messages = [
            {**msg, "content": redactor.sanitize(msg.get("content", ""))}
            for msg in messages
        ]
        last_error: Optional[Exception] = None
        for backend in self._backends:
            for attempt in range(2):  # one retry after a provider's own retry_after hint, then move on
                try:
                    message = await backend.chat_message(sanitized_messages, tools=tools, stream=stream)
                    # Set unconditionally, including to None: a backend that sends
                    # no usage block must clear the previous call's numbers rather
                    # than let the assistant count them twice.
                    self.last_usage = getattr(backend, "last_usage", None)
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
