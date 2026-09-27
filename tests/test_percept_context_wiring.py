"""RED/GREEN tests: a stored percept must actually reach the LLM turn.

The senses layer (`senses/`) was fully built, consent-gated, unit-tested and
exposed through the headless CLI - and completely invisible to the assistant.
`app.py`, `assistant.py`, `tools.py` and `mcp.py` imported none of it, so a
percept produced by `shani-chronoa-sense` died in that one-shot process and
never appeared in a single LLM request. The CLI was the layer's only consumer,
which is exactly the shape of hazard this repo has demonstrable history with
(`ipc.py`, `tool_tracking.py`, `gateway_supervisor.py`,
`sandbox/profiles.py`; see `test_sense_manifest.py`).

These tests pin the two halves of the fix:

- `Assistant.build_messages()` injects live percept context on every request,
  and leaves `_history` untouched. That second half is not incidental. A
  percept appended to `_history` would be silently deleted by the next
  `_trim_history()` (which preserves `_history[0]` and drops the rest), would
  consume a share of the `MAX_HISTORY_MESSAGES` budget that belongs to the
  conversation, and would outlive its own TTL. Ollama drops rather than errors
  on context overflow, so that failure mode would be invisible.
- With nothing stored, the integration is a strict no-op: byte-identical
  prompts. A feature that silently changes every prompt for every user who has
  never enabled a sense is a regression, not a default.

Everything here runs the real objects: the real `PerceptStore`, the real
`ContextBuilder`, a real `ChronoaApplication()` (a genuine `Gtk.Application`,
not `__new__`) through the real `_init_components()`, and the real
`usr/bin/shani-chronoa-sense` launcher as a subprocess for the cross-process
case - because a store round trip proven only in one interpreter is exactly
the assumption that has not held before in this repo.

Paths, constants and the loader/registry helpers live in
`sense_manifest_support.py`; hermeticity (per-test `HOME`, `XDG_CONFIG_HOME`,
keyfile GSettings backend, temp compiled schema, no real keyring) is
`tests/conftest.py`'s job.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sense_manifest_support import PKG_DIR, SENSE_CLI, USER_SITE_PACKAGES

# A phrase chosen to be unmistakably not conversation: if it turns up in
# `_history` it can only have come from the percept, never from a user turn.
FACT = "the user's standing espresso order is a flat white"

SENSED_BLOCK_HEADER = "What you currently perceive"


class RecordingLLM:
    """Stands in for `OllamaLLM`, recording the exact prompt of every request.

    Snapshots with a JSON round trip so what is compared is what would have
    gone over the wire, not aliased dicts from `_history` that a later turn
    could mutate underneath the assertion.
    """

    def __init__(self, reply: str = "A flat white.") -> None:
        self.prompts: "list[list[dict]]" = []
        self.reply = reply

    async def chat_message(self, messages, tools=None, stream=False):
        self.prompts.append(json.loads(json.dumps(messages)))
        return {"role": "assistant", "content": self.reply}


@pytest.fixture
def secrets():
    """The real secrets manager, with its process-global cache restored after.

    `register_runtime_secret()` mutates a module-level singleton; without this
    a fake key registered by one test would still be redacted by the next.
    """
    from shani_chronoa.secrets_manager import secrets_manager

    snapshot = dict(secrets_manager._cache)
    try:
        yield secrets_manager
    finally:
        secrets_manager._cache.clear()
        secrets_manager._cache.update(snapshot)


def make_percept(content=FACT, ttl=300.0, created_at=None, **overrides):
    """A real `Percept` as a sense would build it, defaults filled in."""
    from shani_chronoa.senses import SENSITIVITY_PERSONAL, Percept

    fields = {
        "sense": "memory",
        "kind": "fact",
        "content": content,
        "created_at": time.time() if created_at is None else created_at,
        "ttl_seconds": ttl,
        "source": "user-stated",
        "sensitivity": SENSITIVITY_PERSONAL,
        "metadata": None,
    }
    fields.update(overrides)
    return Percept(**fields)


@pytest.fixture
def real_app(gsettings_env, monkeypatch, tmp_path):
    """A genuine `Gtk.Application` run through the real `_init_components()`.

    Not `ChronoaApplication.__new__` (which is what `stubbed_app` does, and
    which cannot exercise `__init__` at all). Constructing the real subclass is
    what catches the class of bug `AGENTS.md` records twice: a removed GTK4
    API, and a bad annotation that only explodes when it is really evaluated.

    `PerceptStore.DURABLE_FILE` is redirected at a temp path because it is
    resolved from `$HOME` at *import* time - which for the test process was
    the real home, long before `conftest`'s per-test `HOME` override applied.
    Leaving it alone would make this test read the developer's own stored
    memories, and the no-op assertion in
    `TestNoPerceptsMeansNoChange` genuinely hermetic only for in-memory
    percepts.
    """
    try:
        import gi

        gi.require_version("Gtk", "4.0")
    except (ImportError, ValueError) as e:  # pragma: no cover - env without GTK4
        pytest.skip(f"GTK4 typelib unavailable, cannot construct the real app: {e}")

    import shani_chronoa.senses.store as store_mod

    monkeypatch.setattr(store_mod, "DURABLE_FILE", tmp_path / "percepts" / "memory.jsonl")

    from shani_chronoa.app import ChronoaApplication

    app = ChronoaApplication()
    app._init_components()
    return app


class TestAppWiresTheSensesLayerIn:
    """The GUI path must be a real second consumer, not just a capable one."""

    def test_init_components_hands_the_assistant_a_store_and_a_builder(self, real_app):
        from shani_chronoa.senses.context import ContextBuilder
        from shani_chronoa.senses.store import PerceptStore

        assert isinstance(real_app.percept_store, PerceptStore)
        assert isinstance(real_app.percept_context, ContextBuilder)
        assert real_app.assistant.percept_store is real_app.percept_store
        assert real_app.assistant.context_builder is real_app.percept_context

    def test_a_percept_stored_through_the_apps_store_reaches_its_next_turn(self, real_app):
        # Given: a percept produced by a sense and recorded in the app's own
        # store - the shape `shani-chronoa-sense run` writes, and the shape a
        # future in-GUI producer would write
        real_app.percept_store.add(make_percept())
        llm = RecordingLLM()
        real_app.assistant.llm = llm

        # When: the user speaks
        asyncio.run(real_app.assistant.handle("what is my usual order?"))

        # Then: the percept is in the prompt the LLM was actually given
        assert llm.prompts, "the assistant sent no request at all"
        prompt = llm.prompts[0]
        assert FACT in json.dumps(prompt)
        block = prompt[1]
        assert block["role"] == "system"
        assert SENSED_BLOCK_HEADER in block["content"]
        # ... and only in the prompt: never in the conversation transcript
        assert all(FACT not in m.get("content", "") for m in real_app.assistant._history)


class TestNoPerceptsMeansNoChange:
    """The integration must be a strict no-op until a sense produces something."""

    def test_an_empty_store_leaves_every_prompt_byte_identical(self, tmp_path):
        from shani_chronoa.assistant import Assistant
        from shani_chronoa.senses.context import ContextBuilder
        from shani_chronoa.senses.store import PerceptStore

        plain_llm = RecordingLLM()
        sensed_llm = RecordingLLM()
        plain = Assistant(plain_llm)
        sensed = Assistant(
            sensed_llm,
            percept_store=PerceptStore(durable_path=tmp_path / "never-written.jsonl"),
            context_builder=ContextBuilder(),
        )

        for turn in ("what time is it?", "and the weather?"):
            asyncio.run(plain.handle(turn))
            asyncio.run(sensed.handle(turn))

        assert sensed_llm.prompts == plain_llm.prompts
        # Spelled out, not just implied by the equality: exactly the system
        # prompt and the user's own words, no extra system turn, no placeholder.
        assert [m["role"] for m in sensed_llm.prompts[0]] == ["system", "user"]
        assert sensed_llm.prompts[0][1]["content"] == "what time is it?"

    def test_an_expired_percept_is_not_sent(self, tmp_path):
        from shani_chronoa.assistant import Assistant
        from shani_chronoa.senses.context import ContextBuilder
        from shani_chronoa.senses.store import PerceptStore

        store = PerceptStore(durable_path=tmp_path / "expired.jsonl")
        store.add(make_percept(ttl=1.0, created_at=time.time() - 60))
        llm = RecordingLLM()
        assistant = Assistant(llm, percept_store=store, context_builder=ContextBuilder())

        asyncio.run(assistant.handle("what is my usual order?"))

        assert FACT not in json.dumps(llm.prompts[0])


class TestPerceptsNeverEnterHistory:
    """The bug this design exists to prevent, asserted as a regression test."""

    def test_history_holds_no_percept_and_survives_trimming(self, tmp_path):
        from shani_chronoa.assistant import MAX_HISTORY_MESSAGES, Assistant
        from shani_chronoa.senses.context import ContextBuilder
        from shani_chronoa.senses.store import PerceptStore

        store = PerceptStore(durable_path=tmp_path / "durable.jsonl")
        store.add(make_percept())
        llm = RecordingLLM()
        assistant = Assistant(llm, percept_store=store, context_builder=ContextBuilder())

        # Enough turns that `_trim_history()` has to fire several times
        for i in range(MAX_HISTORY_MESSAGES):
            asyncio.run(assistant.handle(f"unrelated chatter number {i}"))

        # The trimmer ran (otherwise this proves nothing about survival) ...
        # It runs *before* the reply is appended, so a turn at rest can sit one
        # message over the cap; that is the pre-existing trimmer's arithmetic,
        # not this change's. What matters is that whole turns went away.
        assert len(assistant._history) <= MAX_HISTORY_MESSAGES + 2
        # ... yet the percept is still delivered, because it was never stored
        assert FACT not in json.dumps(assistant._history)
        assert FACT in json.dumps(assistant.build_messages())
        # and the trimmed-out turns are the ones that actually went missing
        assert "unrelated chatter number 0" not in json.dumps(assistant._history)

    def test_a_fresh_percept_appears_without_touching_the_transcript(self, tmp_path):
        from shani_chronoa.assistant import Assistant
        from shani_chronoa.senses.context import ContextBuilder
        from shani_chronoa.senses.store import PerceptStore

        store = PerceptStore(durable_path=tmp_path / "durable.jsonl")
        llm = RecordingLLM()
        assistant = Assistant(llm, percept_store=store, context_builder=ContextBuilder())
        asyncio.run(assistant.handle("hello"))
        before = json.dumps(assistant._history)

        store.add(make_percept(content="the office wifi password rotates on Mondays"))
        asyncio.run(assistant.handle("and the wifi?"))

        assert "rotates on Mondays" in json.dumps(llm.prompts[-1])
        assert json.dumps(assistant._history) != before
        assert all(
            "rotates on Mondays" not in m.get("content", "") for m in assistant._history
        )


class TestPerceptContentIsSanitized:
    """A percept is a prompt input, so redaction applies to it like any other."""

    def test_a_registered_secret_in_a_percept_is_redacted_in_the_prompt(
        self, tmp_path, secrets
    ):
        from shani_chronoa.assistant import Assistant
        from shani_chronoa.senses.context import ContextBuilder, sanitize_percepts
        from shani_chronoa.senses.store import PerceptStore

        secret = "sk-live-0123456789abcdef-do-not-leak"
        secrets.register_runtime_secret("CLOUD_LLM_ANTHROPIC", secret)

        # The real write path the memory sense uses: redact, then store.
        store = PerceptStore(durable_path=tmp_path / "redacted.jsonl")
        store.extend(
            sanitize_percepts(
                [make_percept(content=f"the api key is {secret}")],
                secrets.sanitize_text_for_llm,
            )
        )
        llm = RecordingLLM()
        assistant = Assistant(llm, percept_store=store, context_builder=ContextBuilder())

        asyncio.run(assistant.handle("what is the api key?"))

        sent = json.dumps(llm.prompts[0])
        assert "do-not-leak" not in sent
        assert "$SECRET:CLOUD_LLM_ANTHROPIC" in sent


class TestTheCliAndTheAssistantShareOneStore:
    """A percept written by the launcher is live in the very next turn."""

    def _run_cli(self, *args):
        completed = subprocess.run(
            [sys.executable, str(SENSE_CLI), *args],
            capture_output=True,
            text=True,
            timeout=60,
            env=dict(os.environ),
        )
        assert completed.returncode == 0, (
            f"shani-chronoa-sense {' '.join(args)} failed "
            f"(exit {completed.returncode}):\n{completed.stderr}"
        )
        return json.loads(completed.stdout)

    def test_a_durable_percept_written_by_the_cli_reaches_the_assistant(
        self, gsettings_env, monkeypatch
    ):
        from shani_chronoa.assistant import Assistant
        from shani_chronoa.senses.context import ContextBuilder
        from shani_chronoa.senses.store import PerceptStore
        import shani_chronoa.senses.store as store_mod

        # The launcher resolves `~` from $HOME in a fresh interpreter;
        # `store.DURABLE_FILE` was frozen at *this* process's import, so point
        # it at the same place the child will write. Same file, both sides.
        monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
        monkeypatch.setenv(
            "PYTHONPATH", os.pathsep.join([str(PKG_DIR), USER_SITE_PACKAGES])
        )
        durable = Path(os.environ["HOME"]) / ".local/share/shani-chronoa/percepts/memory.jsonl"
        monkeypatch.setattr(store_mod, "DURABLE_FILE", durable)

        # When: the user remembers something out-of-band
        reported = self._run_cli(
            "run", "memory", "operation=remember", f"fact={FACT}", "--json"
        )
        # The percept `run` hands back is a transient confirmation - real,
        # observed behaviour: it lives only inside the process that just exited.
        assert reported["data"]["percept"]["ttl_seconds"] is not None
        assert reported["data"]["stored"] is False
        # What crossed the process boundary is the durable fact on disk.
        assert durable.is_file(), f"{durable} was not written by the real launcher"
        persisted = PerceptStore().durable()
        assert [p.content for p in persisted] == [FACT]

        # Then: the assistant, on its next turn, sends exactly that text
        llm = RecordingLLM()
        assistant = Assistant(llm, percept_store=PerceptStore(), context_builder=ContextBuilder())
        asyncio.run(assistant.handle("what do you remember about coffee?"))

        assert SENSED_BLOCK_HEADER in llm.prompts[0][1]["content"]
        assert FACT in llm.prompts[0][1]["content"]
        assert all(FACT not in m.get("content", "") for m in assistant._history)
