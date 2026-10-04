"""The on-machine language model without Ollama: llama.cpp's `llama-server` and a pinned GGUF file.

Neither image ships Ollama (the CLI matrix finds no `ollama` on GNOME or
Plasma), and its Linux release is a 1.4 GB tarball of CUDA libraries a machine
with no GPU never uses. Arch packages llama.cpp itself (`extra/llama-cpp`,
17 MiB, CPU backends in `ggml`), so Chronoa depends on it and only the model
file is downloaded - one of the three below, chosen by RAM, verified against
the digest pinned here exactly as `stt_provision` verifies a speech model
(the same `_install`, so the same temp-file, size and sha256 rules).

`llama-server` runs as a user service (`shani-chronoa-llm.service`) on
127.0.0.1 only, with the model's own chat template (`--jinja`) so tool calls
work, and speaks the OpenAI API, so the client is `cloud_llm`'s
`OpenAICompatibleLLM` pointed at localhost - nothing leaves the machine.
Ollama, when someone has it, still comes first.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

from shani_chronoa import files
from shani_chronoa.stt_provision import ModelSpec, _install

logger = logging.getLogger(__name__)

HOST, PORT = "127.0.0.1", 8765
BASE_URL = f"http://{HOST}:{PORT}/v1"
UNIT = "shani-chronoa-llm.service"

# (spec, label, smallest RAM it is the default for, in GB). Sizes and digests
# are each file's Hugging Face LFS `size` and `oid` (sha256) at the pinned
# revision, read from the HF API on 2026-10-02 - not from a download.
_QWEN = "https://huggingface.co/Qwen/{repo}/resolve/{rev}"
TIERS = (
    (ModelSpec("qwen3-0.6b", "Qwen3-0.6B-Q8_0.gguf", 639_446_688,
               "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031",
               "tiny and quick; simple requests",
               _QWEN.format(repo="Qwen3-0.6B-GGUF", rev="23749fefcc72300e3a2ad315e1317431b06b590a")),
     "Small (0.6 GB) - quick on any computer, for simple requests", 0),
    (ModelSpec("qwen3-1.7b", "Qwen3-1.7B-Q4_K_M.gguf", 1_107_409_472,
               "b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897",
               "the default; reliable tool calls on a CPU",
               "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/d7f544eead698dbd1f15126ef60b45a1e1933222"),
     "Medium (1.1 GB) - the best fit for most computers", 6),
    (ModelSpec("qwen3-4b", "Qwen3-4B-Q4_K_M.gguf", 2_497_280_256,
               "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
               "smarter, slower on a CPU",
               _QWEN.format(repo="Qwen3-4B-GGUF", rev="bc640142c66e1fdd12af0bd68f40445458f3869b")),
     "Large (2.5 GB) - smarter, noticeably slower without a graphics card", 999),
)
SPECS = {spec.key: spec for spec, _label, _ram in TIERS}


def model_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "llm"


def current_link() -> Path:
    """The file the service loads; a symlink to the chosen model, so switching models is one rename."""
    return model_dir() / "current.gguf"


def ram_gb() -> float:
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return 8.0


def recommended(ram: Optional[float] = None) -> str:
    """The default for this much RAM: 0.6B under 6 GB, 1.7B otherwise (4B is offered, never defaulted)."""
    ram = ram_gb() if ram is None else ram
    pick = [spec.key for spec, _label, need in TIERS if ram >= need]
    return pick[-1]


def installed() -> "list[str]":
    return [key for key, spec in SPECS.items() if (model_dir() / spec.filename).is_file()]


def active() -> str:
    target = current_link()
    try:
        name = os.readlink(target)
    except OSError:
        return ""
    return next((k for k, s in SPECS.items() if s.filename == Path(name).name), "")


def server_binary() -> str:
    return shutil.which("llama-server") or ""


def verify(key: str) -> bool:
    """Whether the downloaded file for `key` still has its pinned size and sha256 (about a second per GB)."""
    from shani_chronoa.stt_provision import file_matches
    return file_matches(model_dir() / SPECS[key].filename, SPECS[key])


def provision(key: str, progress: Optional[Callable[[int, int], None]] = None, config=None, transport=None) -> Path:
    """Download and verify one model, then make it the one the service loads.

    A file already in place is re-hashed first, not trusted for being there: a
    copied-in, truncated or altered model is removed and fetched again.
    """
    spec = SPECS[key]
    present = model_dir() / spec.filename
    if present.is_file() and not verify(key):
        logger.warning("%s does not match its pinned digest; fetching it again", present)
        present.unlink()
    path = _install(spec, user_dir=model_dir(), system_dir=Path("/usr/share/shani-chronoa/llm"),
                    existing=present if present.is_file() else None,
                    config=config, progress=progress, transport=transport, label="llm")
    use(key)
    return path


def use(key: str) -> None:
    spec = SPECS[key]
    link = current_link()
    tmp = link.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(spec.filename)
    os.replace(tmp, link)


def _systemctl(*args: str) -> "subprocess.CompletedProcess | None":
    if shutil.which("systemctl") is None:
        return None
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return None


def start_service() -> str:
    """Enable and (re)start the user service; '' on success, else why not."""
    if not server_binary():
        return "llama-server is not installed (the llama-cpp package)"
    if not current_link().exists():
        return "no model is downloaded yet"
    write_env()
    proc = _systemctl("enable", "--now", UNIT)
    if proc is None or proc.returncode != 0:
        return (proc.stderr.strip() if proc else "systemctl is not available")[:200]
    _systemctl("restart", UNIT)
    return ""


def is_up(timeout: float = 1.5) -> bool:
    import httpx
    try:
        return httpx.get(f"http://{HOST}:{PORT}/health", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def gpu_devices() -> "list[str]":
    """Accelerators llama.cpp can use here (Vulkan via ggml-vulkan), from `llama-server --list-devices`.

    Empty on a CPU-only machine - or with no Vulkan driver - and that is not an
    error: ggml then runs on the CPU, so the same package works on both.
    """
    binary = server_binary()
    if not binary:
        return []
    try:
        proc = subprocess.run([binary, "--list-devices"], capture_output=True, text=True, timeout=30, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return []
    out = []
    for line in (proc.stdout + proc.stderr).splitlines():
        line = line.strip()
        # "Vulkan0: Intel(R) Iris(R) Xe Graphics (12345 MiB, 12000 MiB free)"
        if ":" in line and not line.lower().startswith(("available devices", "load_backend", "ggml_", "warning")):
            name = line.split(":", 1)[0]
            if name and name[-1].isdigit() and not name.lower().startswith("cpu"):
                out.append(line)
    return out


def server_args(devices: "Optional[list[str]]" = None) -> "list[str]":
    """The llama-server command line for this machine: GPU offload only when a GPU is listed."""
    devices = gpu_devices() if devices is None else devices
    args = ["-m", str(current_link()), "--host", HOST, "--port", str(PORT), "--jinja",
            "-c", "8192", "--no-webui"]
    args += ["-ngl", "99"] if devices else ["-ngl", "0", "-t", str(max(1, (os.cpu_count() or 2) - 1))]
    return args


def write_env(devices: "Optional[list[str]]" = None) -> Path:
    """~/.config/shani-chronoa/llm.env, which the unit reads - so a GPU added later is a restart away."""
    path = files.config_home() / "shani-chronoa" / "llm.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("LLAMA_ARGS=" + " ".join(server_args(devices)) + "\n", encoding="utf-8")
    return path


def recommended_for_machine() -> str:
    """By RAM on a CPU; with a GPU listed, the 4B model is the default from 8 GB."""
    key = recommended()
    if gpu_devices() and ram_gb() >= 8:
        return "qwen3-4b"
    return key


_RULES_PREFIX = "The user's standing rules for you"
_THINK = re.compile(r"<think>.*?</think>\s*", re.S)
_TOOL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _redact(messages: "list[dict]") -> "list[dict]":
    """Every message's content, with registered secrets replaced.

    Applied to the whole list rather than to the system prompt alone, because
    the dangerous half is a secret in a *tool result* or a *file read* - those
    arrive as `tool` and `user` messages, and they are exactly the messages a
    reader would assume were safe because they are not the system prompt.
    """
    try:
        from shani_chronoa.redaction import redactor
    except Exception:  # noqa: BLE001 - redaction must never break a reply
        return messages
    out = []
    for message in messages:
        if not isinstance(message, dict):
            out.append(message)
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content:
            out.append(message)
            continue
        try:
            cleaned = redactor.sanitize(content)
        except Exception:  # noqa: BLE001
            cleaned = content
        out.append({**message, "content": cleaned})
    return out

def normalize_messages(messages: "list[dict]") -> "list[dict]":
    """One system message at the head, holding only what stays the same; changing context folded into the last user turn.

    Two reasons, both from harness-study: llama.cpp reuses its prompt cache
    only for an unchanged prefix (assistd folds per-turn context into the user
    turn for this), and several models' chat templates reject a system
    message anywhere but first (sayri merges them for this). Percepts change
    every turn, so they go into the newest user message; the rules file
    rarely changes, so it joins the system prompt. Nothing is dropped, and
    the caller's list is not modified.

    **Every registered secret is redacted here, and this is the only place in
    this module that could do it.** `ollama_llm.py` and all three cloud paths
    call `redactor.sanitize` on every message before sending, and this file
    called it nowhere - so on the *default* backend, the one `AGENTS.md` says is
    now the default brain, a registered API key that had been read back by a
    tool, quoted in a file, or pasted into a turn went to the model verbatim.
    Every other backend redacted it. `normalize_messages` is the single
    function every request here passes through (see the `prepare_messages`
    below), so this is the chokepoint rather than a belt-and-braces extra.

    Fail-soft: a redactor that raises must not stop a reply, so an exception
    here degrades to the unsanitized text rather than to no answer - which is
    the one direction that is wrong, and it is stated rather than hidden. The
    registered-secret case is narrow (they are the keys Chronoa itself holds)
    and losing a turn would be worse than losing the redaction.
    """
    if not messages:
        return messages
    messages = _redact(messages)
    head = dict(messages[0]) if messages[0].get("role") == "system" else None
    rest = messages[1:] if head else list(messages)
    stable, moving, body = [], [], []
    for m in rest:
        if m.get("role") == "system":
            text = str(m.get("content") or "")
            (stable if text.startswith(_RULES_PREFIX) else moving).append(text)
        else:
            body.append(dict(m))
    if head is not None and stable:
        head["content"] = "\n\n".join([str(head.get("content") or "")] + stable)
    if moving:
        last_user = max((i for i, m in enumerate(body) if m.get("role") == "user"), default=None)
        context = "\n\n".join(moving)
        if last_user is None:
            body.insert(0, {"role": "user", "content": context})
        else:
            body[last_user]["content"] = f"{context}\n\n---\n{body[last_user].get('content') or ''}"
    return ([head] if head is not None else []) + body


def recover_tool_calls(message: dict, tools) -> dict:
    """A tool call a small model wrote as text (`<tool_call>{...}</tool_call>`, a JSON block) turned into a real one.

    Only for a tool that was offered, with an object of arguments; anything
    else is left as the text it was. Also removes a leaked `<think>` block,
    which would otherwise be shown and spoken.
    """
    content = message.get("content") or ""
    if isinstance(content, str) and "<think>" in content:
        content = _THINK.sub("", content).strip()
        message = {**message, "content": content}
    if message.get("tool_calls") or not tools or not isinstance(content, str):
        return message
    offered = {(t.get("function") or {}).get("name") for t in tools}
    calls = []
    for match in list(_TOOL_TAG.finditer(content)) or list(_JSON_FENCE.finditer(content)):
        try:
            obj = json.loads(match.group(1))
        except ValueError:
            continue
        name = obj.get("name") if isinstance(obj, dict) else None
        args = obj.get("arguments", obj.get("parameters", {})) if isinstance(obj, dict) else None
        if name in offered and isinstance(args, dict):
            calls.append({"id": f"recovered-{len(calls)}", "type": "function",
                          "function": {"name": name, "arguments": json.dumps(args)}})
    if not calls:
        return message
    logger.info("Recovered %d tool call(s) the model wrote as text", len(calls))
    return {**message, "content": "", "tool_calls": calls}


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    cut = re.search(r"(?<=[.!?])\s", text)
    text = text[:cut.start()] if cut and cut.start() > 20 else text
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def compact_tools(tools, limit: int = 160):
    """The same tools with each description cut to its first sentence (and `limit` characters).

    Names, parameter names, types, enums and `required` are untouched, so a
    call is exactly as valid; only the prose shrinks. On a CPU the schemas are
    most of the prompt the model reads before its first word.
    """
    if not tools:
        return tools
    out = []
    for tool in tools:
        fn = dict(tool.get("function") or {})
        fn["description"] = _short(fn.get("description", ""), limit)
        params = fn.get("parameters")
        if isinstance(params, dict) and isinstance(params.get("properties"), dict):
            props = {}
            for name, prop in params["properties"].items():
                prop = dict(prop) if isinstance(prop, dict) else prop
                if isinstance(prop, dict) and "description" in prop:
                    prop["description"] = _short(prop["description"], 80)
                props[name] = prop
            fn["parameters"] = {**params, "properties": props}
        out.append({**tool, "function": fn})
    return out


#: Whether the local client sends compacted schemas. Off, by measurement: on
#: both images (Qwen3-0.6B, CPU, 2026-10-02) compact was 30% faster (28.2 s vs
#: 40.2 s for 8 calls) but chose the right tool 6/8 times against 7/8, and a
#: wrong action costs more than a slower right one. Re-measure with
#: shani-testbed's chronoa-setup (setup-compact-tools) for a larger model.
COMPACT_TOOLS = False

#: Sampling temperature for the local model. None is llama-server's own
#: default (0.8), which for a 0.6B model choosing among a dozen tools is close
#: to dice. Decided by tools/task_eval.py (shani-testbed chronoa-eval).
TEMPERATURE = None

#: A second, narrower chance when the model answered in words to what was
#: plainly a command (tool_select.confident): the same request with only those
#: few tools, ask_user, and an explicit "answer without a tool" choice, under
#: tool_choice=required. A destructive tool is never forced, and not acting
#: stays a choice the model can make. Decided by tools/task_eval.py.
FORCE_RETRY = False

#: Two constrained steps instead of free-form tool calling (adopted from Alpaca's
#: schema-constrained title generation, applied to the tool call itself): first
#: the tool's *name*, under a JSON schema whose only allowed values are the
#: offered tools and "none"; then its *arguments*, under that tool's own
#: parameter schema. llama-server turns each schema into a grammar, so an
#: unknown tool or malformed arguments cannot be generated at all. Decided by
#: tools/task_eval.py.
CONSTRAINED = False

NO_TOOL_NAME = "answer_without_a_tool"
NO_TOOL = {"type": "function", "function": {
    "name": NO_TOOL_NAME,
    "description": "Choose this when none of the other tools is needed: the request is a question to answer, "
                   "or chat. Put your answer in text.",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}}


def _first_sentence(text: str, limit: int = 140) -> str:
    return _short(text, limit)


def tool_menu(tools: "list[dict]") -> str:
    return "\n".join(f"- {t['function']['name']}: {_first_sentence(t['function'].get('description', ''))}"
                     for t in tools)


def pick_schema(tools: "list[dict]") -> dict:
    names = [t["function"]["name"] for t in tools]
    return {"type": "object", "properties": {"tool": {"type": "string", "enum": names + ["none"]}},
            "required": ["tool"]}


def argument_schema(tool: dict) -> dict:
    params = (tool.get("function") or {}).get("parameters") or {}
    props = params.get("properties") or {}
    return {"type": "object", "properties": props, "required": [r for r in params.get("required", []) if r in props]}


def _json_format(schema: dict) -> dict:
    return {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}


def _never_forced(name: str) -> bool:
    from shani_chronoa import capabilities
    return capabilities.GATED.get(name) in capabilities.DESTRUCTIVE_CONSENT_KEYS


def forced_tools(messages: "list[dict]") -> "list[dict]":
    """The narrowed tool list for a forced retry, or [] when the request is not clearly a command."""
    from shani_chronoa.tool_select import confident
    from shani_chronoa.tools import TOOLS
    asked = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
    names = [n for n in confident(asked, TOOLS) if not _never_forced(n)]
    if not names:
        return []
    keep = set(names) | {"ask_user"}
    return [tool for tool in TOOLS if tool["function"]["name"] in keep] + [NO_TOOL]


#: Characters per token, conservatively low for English + JSON (so the estimate errs towards "too long").
CHARS_PER_TOKEN = 3.0
#: Tokens kept free for the reply itself.
REPLY_RESERVE = 1024
_N_CTX = {}


def context_tokens(default: int = 8192) -> int:
    """llama-server's real context window (GET /props), cached; `default` when it cannot be read."""
    if "n" not in _N_CTX:
        import httpx
        try:
            props = httpx.get(f"http://{HOST}:{PORT}/props", timeout=2.0).json()
            _N_CTX["n"] = int((props.get("default_generation_settings") or {}).get("n_ctx") or props.get("n_ctx") or default)
        except (httpx.HTTPError, ValueError, TypeError):
            return default
    return _N_CTX["n"]


def fit_to_context(messages: "list[dict]", n_ctx: int, reserved_chars: int = 0) -> "list[dict]":
    """Drop the oldest whole turns until the request fits the model's window (assistd's effective_budget).

    A turn is a user message and everything after it up to the next user
    message, so a tool call is never separated from its result. The head
    system message and the newest turn are always kept; a note says how many
    messages were left out, so the model does not mistake a gap for nothing.
    """
    budget = int((n_ctx - REPLY_RESERVE) * CHARS_PER_TOKEN) - reserved_chars
    size = lambda ms: sum(len(str(m.get("content") or "")) + len(json.dumps(m.get("tool_calls") or "")) for m in ms)
    if size(messages) <= budget or len(messages) < 3:
        return messages
    head, body = (messages[:1], messages[1:]) if messages[0].get("role") == "system" else ([], list(messages))
    starts = [i for i, m in enumerate(body) if m.get("role") == "user"] or [0]
    turns = [body[a:b] for a, b in zip(starts, starts[1:] + [len(body)])]
    lead = body[:starts[0]]
    dropped = 0
    while len(turns) > 1 and size(head + lead + [m for t in turns for m in t]) > budget:
        dropped += len(turns.pop(0))
    kept = [m for t in turns for m in t]
    if dropped:
        note = {"role": "user", "content": f"[{dropped} earlier message(s) of this conversation were left out "
                                            f"to fit the model's memory.]"}
        if kept and kept[0].get("role") == "user":
            kept[0] = {**kept[0], "content": f"{note['content']}\n\n{kept[0].get('content') or ''}"}
        else:
            kept.insert(0, note)
        logger.info("Left out %d old message(s) to fit a %d-token context", dropped, n_ctx)
    return head + lead + kept


class LocalLLM:
    """Constructed lazily below so importing this module never imports httpx/cloud_llm."""

    def __new__(cls, model_key: str = ""):
        from shani_chronoa.cloud_llm import CloudProvider, OpenAICompatibleLLM

        class _Local(OpenAICompatibleLLM):
            local = True
            # Qwen3 "thinks" before answering unless told not to; on a CPU that
            # is most of the wait, and the thought is never shown (assistd sends
            # the same flag). llama-server passes it to the chat template.
            extra_payload = {"chat_template_kwargs": {"enable_thinking": False}}
            read_timeout = 300.0
            stream_supported = True

            def is_available(self) -> bool:
                return is_up()

            def prepare_messages(self, messages):
                return fit_to_context(normalize_messages(messages), context_tokens(),
                                      reserved_chars=getattr(self, "_tools_chars", 0))

            def _payload_extra(self, **more):
                extra = {"chat_template_kwargs": {"enable_thinking": False}}
                if TEMPERATURE is not None:
                    extra["temperature"] = TEMPERATURE
                extra.update(more)
                return extra

            async def _json(self, messages, schema, temperature=None):
                more = {"temperature": temperature} if temperature is not None else {}
                self.extra_payload = self._payload_extra(response_format=_json_format(schema), **more)
                try:
                    reply = await super().chat_message(messages, tools=None)
                finally:
                    self.extra_payload = self._payload_extra()
                try:
                    return json.loads(reply.get("content") or "{}")
                except ValueError:
                    return {}

            async def suggest_title(self, asked: str, answered: str) -> str:
                """A few-word title for a conversation, under a JSON schema (Alpaca's titles), at low temperature."""
                schema = {"type": "object", "properties": {"title": {"type": "string", "maxLength": 48}},
                          "required": ["title"]}
                got = await self._json([{"role": "user", "content": (
                    "Write a short title, 2 to 6 words, for a conversation that began like this. No quotes.\n\n"
                    f"User: {asked[:600]}\nAssistant: {answered[:600]}")}], schema, temperature=0.2)
                return str(got.get("title") or "").strip()[:48] if isinstance(got, dict) else ""

            async def _constrained(self, messages, tools):
                """The tool's name, then its arguments, each generated under a schema."""
                conversation = [m for m in messages if m.get("role") != "system"]
                head = next((str(m.get("content") or "") for m in messages if m.get("role") == "system"), "")
                pick = await self._json([{"role": "system", "content": head + (
                    "\n\nDecide what to do with the user's last message. If one of these tools does it, choose that "
                    "tool. If it is a question you can answer yourself, or chat, choose none.\nTools:\n"
                    + tool_menu(tools))}] + conversation, pick_schema(tools))
                name = pick.get("tool")
                chosen = next((t for t in tools if t["function"]["name"] == name), None)
                if chosen is None:
                    return None  # "none", or nothing usable: answer in words
                args = await self._json([{"role": "system", "content": head + (
                    f"\n\nCall {name}: {chosen['function'].get('description', '')}\nGive its arguments for the "
                    "user's last message.")}] + conversation, argument_schema(chosen))
                return {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call_0", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args if isinstance(args, dict) else {})}}]}

            required_tool = ""

            async def chat_message(self, messages, tools=None, stream=False):
                tools = compact_tools(tools) if COMPACT_TOOLS else tools
                self._tools_chars = len(json.dumps(tools)) if tools else 0
                self.extra_payload = self._payload_extra()
                named = next((t for t in tools or [] if t["function"]["name"] == self.required_tool), None)
                if named is not None:
                    # the person named the tool: only its arguments are left to decide
                    conversation = [m for m in messages if m.get("role") != "system"]
                    head = next((str(m.get("content") or "") for m in messages if m.get("role") == "system"), "")
                    args = await self._json([{"role": "system", "content": head + (
                        f"\n\nCall {named['function']['name']}: {named['function'].get('description', '')}\n"
                        "Give its arguments for the user's last message.")}] + conversation, argument_schema(named))
                    return {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "call_0", "type": "function", "function": {
                            "name": named["function"]["name"],
                            "arguments": json.dumps(args if isinstance(args, dict) else {})}}]}
                if CONSTRAINED and tools and messages and messages[-1].get("role") == "user":
                    called = await self._constrained(messages, tools)
                    if called is not None:
                        return called
                    return await super().chat_message(messages, tools=None, stream=stream)
                reply = await super().chat_message(messages, tools=tools, stream=stream)
                if FORCE_RETRY and tools and not reply.get("tool_calls"):
                    narrowed = forced_tools(messages)
                    if narrowed:
                        self.extra_payload = self._payload_extra(tool_choice="required")
                        try:
                            retry = await super().chat_message(messages, tools=narrowed, stream=stream)
                        finally:
                            self.extra_payload = self._payload_extra()
                        calls = retry.get("tool_calls") or []
                        if calls and (calls[0].get("function") or {}).get("name") != NO_TOOL_NAME:
                            logger.info("Answered in words to a clear command; the narrowed retry called %s",
                                        calls[0]["function"].get("name"))
                            return retry
                return reply

            async def chat_message_stream(self, messages, tools=None, on_text=None):
                tools = compact_tools(tools) if COMPACT_TOOLS else tools
                self._tools_chars = len(json.dumps(tools)) if tools else 0
                self.extra_payload = self._payload_extra()
                return await super().chat_message_stream(messages, tools=tools, on_text=on_text)

            def postprocess(self, message, tools):
                return recover_tool_calls(message, tools)

        provider = CloudProvider("llama.cpp", "llama.cpp (this computer)", BASE_URL, model_key or active() or "local")
        return _Local(provider)
