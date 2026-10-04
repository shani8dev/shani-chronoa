"""The privacy-mode alarm has to be reachable from the code that actually runs.

`egress.record()` has taken `privacy_mode` and computed
`violation = privacy_mode and not local` since it was written, and
`test_egress.py` has asserted that arithmetic directly for as long as it has
existed. That test is real, and it is also insufficient: it calls
`egress.record(..., privacy_mode=True)` itself, so it proves the *log line*
works and says nothing about whether any *caller* ever supplies the flag.

Which is what was wrong. All five instrumented call sites -
`ollama_llm.py`, `cloud_llm.py` twice, `senses/vision.py`, `webtext.py` - omitted
`privacy_mode`, so the parameter kept its `False` default, `violation` was
structurally always `False`, and the `logger.error` alarm at the end of
`record()` was unreachable. For an application whose entire product claim is
"nothing leaves this machine unless you say so", the one property that makes
the log an *audit* rather than a *log* was dead code, and every test still
passed.

So the property pinned here is not "record() computes violation correctly".
It is: **with privacy mode genuinely ON, a real request to a real remote host
is recorded as a violation, through the real call site, on the real code
path.** Driving the call sites is the only thing that can catch the flag being
dropped again, and these tests are written so that dropping it at any single
one of the five sites turns exactly that site's test red.
"""

import ast
import asyncio
import logging
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import egress  # noqa: E402

# Every file that records egress, mapped to the call site inside it. Used by the
# static "no call site may drop the flag again" test at the bottom of this file.
INSTRUMENTED = {
    "stt_provision.py": ":provision",  # f"{label}:provision" - stt, llm and voice files
    "ollama_llm.py": "llm:ollama",
    "cloud_llm.py": "cloud_llm:",
    "webtext.py": "web:retrieve",
    "senses/vision.py": "senses:vision",
}


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    monkeypatch.setattr(egress, "EGRESS_DIR", tmp_path / "egress")
    monkeypatch.setattr(egress, "EGRESS_LOG", tmp_path / "egress" / "egress.jsonl")
    yield


@pytest.fixture
def privacy_on(gsettings_env):
    """Privacy mode genuinely ON in the real GSettings store, not a stub.

    Deliberately not a monkeypatched `privacy_mode_enabled()`. A stub would
    still be defeated by the bug under test - if a call site stopped asking the
    question, a stubbed answer would go unread just as easily as a real one -
    and it would not prove the *setting* is the thing the alarm is derived
    from. The schema default is already `true`, so this makes the failure mode
    (an unreachable schema) a separate, explicit test instead of the baseline.
    """
    from shani_chronoa.config import ChronoaConfig

    config = ChronoaConfig()
    config.set("privacy-mode", "true")
    assert config.privacy_mode is True
    return config


def _mocked_async(monkeypatch, module, handler):
    """Point `module.httpx.AsyncClient` at a MockTransport. No network."""
    real_async_client = httpx.AsyncClient

    def _factory(**kwargs):
        return real_async_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(module.httpx, "AsyncClient", _factory)


def _ok_openai(request):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})


def _ok_anthropic(request):
    return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})


def _ok_ollama(request):
    return httpx.Response(200, json={"message": {"role": "assistant", "content": "ok"}})


def _html(request):
    return httpx.Response(200, html="<html><title>T</title><body><p>hello there</p></body></html>")


# --- the alarm, per call site -------------------------------------------------


class TestCloudProvidersAlarm:
    """The two cloud paths are the ones that can send a whole conversation to a
    third party, and `app.py` gates them on privacy mode being off - so a
    violation here means a gate was bypassed, which is the alarm's whole job."""

    def test_the_openai_compatible_path_alarms(self, privacy_on, monkeypatch):
        import shani_chronoa.cloud_llm as cloud_mod
        from shani_chronoa.cloud_llm import OpenAICompatibleLLM, PROVIDERS

        _mocked_async(monkeypatch, cloud_mod, _ok_openai)
        asyncio.run(OpenAICompatibleLLM(PROVIDERS["llm7"]).chat_message([{"role": "user", "content": "hi"}]))

        event = egress.read_events()[0]
        assert event["host"] == "api.llm7.io"
        assert event["local"] is False
        assert event["privacy_mode"] is True, "the call site did not pass the real setting"
        assert event["violation"] is True, (
            "a remote call went out while privacy mode was ON and the egress log "
            "does not say so"
        )
        assert egress.summary()["violations"] == 1

    def test_the_anthropic_path_alarms(self, privacy_on, monkeypatch):
        import shani_chronoa.cloud_llm as cloud_mod
        from shani_chronoa.cloud_llm import AnthropicLLM

        _mocked_async(monkeypatch, cloud_mod, _ok_anthropic)
        asyncio.run(AnthropicLLM(api_key="test-key").chat_message([{"role": "user", "content": "hi"}]))

        event = egress.read_events()[0]
        assert event["host"] == "api.anthropic.com"
        assert event["violation"] is True

    def test_a_local_ollama_call_does_not_alarm(self, privacy_on, monkeypatch):
        """The control. Without it, "pass privacy_mode everywhere" and "always
        alarm" are indistinguishable."""
        import shani_chronoa.cloud_llm as cloud_mod
        from shani_chronoa.cloud_llm import OpenAICompatibleLLM, PROVIDERS
        from shani_chronoa.cloud_llm import CloudProvider

        loopback = CloudProvider("local", "Local", "http://127.0.0.1:11434", "qwen3:4b")
        _mocked_async(monkeypatch, cloud_mod, _ok_openai)
        asyncio.run(OpenAICompatibleLLM(loopback).chat_message([{"role": "user", "content": "hi"}]))

        event = egress.read_events()[0]
        assert event["local"] is True
        assert event["privacy_mode"] is True
        assert event["violation"] is False
        assert egress.summary()["violations"] == 0


class TestOllamaPathAlarms:
    def test_a_remote_ollama_host_alarms(self, privacy_on, monkeypatch):
        """`config.ollama_host` forces loopback while privacy mode is on, so
        reaching this means either that gate was bypassed or `ollama_llm.py` was
        pointed somewhere else by hand. Both are worth a log line."""
        import shani_chronoa.ollama_llm as llm_mod

        _mocked_async(monkeypatch, llm_mod, _ok_ollama)
        remote = llm_mod.OllamaLLM(host="http://ollama.example.com:11434")
        asyncio.run(remote.chat_message([{"role": "user", "content": "hi"}]))

        event = egress.read_events()[0]
        assert event["host"] == "ollama.example.com"
        assert event["violation"] is True

    def test_the_default_loopback_host_does_not_alarm(self, privacy_on, monkeypatch):
        import shani_chronoa.ollama_llm as llm_mod

        _mocked_async(monkeypatch, llm_mod, _ok_ollama)
        asyncio.run(llm_mod.OllamaLLM().chat_message([{"role": "user", "content": "hi"}]))

        event = egress.read_events()[0]
        assert event["local"] is True
        assert event["violation"] is False


class TestWebPathAlarms:
    def test_a_web_fetch_alarms(self, privacy_on):
        from shani_chronoa import webtext

        webtext.retrieve("https://example.com/page", transport=httpx.MockTransport(_html))

        event = egress.read_events()[0]
        assert event["host"] == "example.com"
        assert event["violation"] is True


class TestVisionPathAlarms:
    def test_a_screenshot_sent_off_machine_alarms(self, privacy_on):
        """`local_endpoint()` refuses a non-loopback `ollama-host` before this
        runs, so the only way a screenshot reaches a remote host is a bypass -
        and a screenshot is the most sensitive payload in the app."""
        from shani_chronoa.senses import vision

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"message": {"content": "a terminal"}})
        )
        vision.describe_image(
            b"\x89PNG-not-really", "qwen3-vl", "http://vision.example.com:11434", transport=transport
        )

        event = egress.read_events()[0]
        assert event["host"] == "vision.example.com"
        assert event["violation"] is True

    def test_a_screenshot_staying_local_does_not_alarm(self, privacy_on):
        from shani_chronoa.senses import vision

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"message": {"content": "a terminal"}})
        )
        vision.describe_image(
            b"\x89PNG-not-really", "qwen3-vl", "http://127.0.0.1:11434", transport=transport
        )

        event = egress.read_events()[0]
        assert event["local"] is True
        assert event["violation"] is False


# --- the alarm is an alarm, not a debug line ---------------------------------


class TestTheAlarmIsLoud:
    def test_a_violation_is_logged_at_error(self, privacy_on, monkeypatch, caplog):
        import shani_chronoa.cloud_llm as cloud_mod
        from shani_chronoa.cloud_llm import OpenAICompatibleLLM, PROVIDERS

        _mocked_async(monkeypatch, cloud_mod, _ok_openai)
        with caplog.at_level(logging.ERROR, logger=egress.logger.name):
            asyncio.run(OpenAICompatibleLLM(PROVIDERS["llm7"]).chat_message([{"role": "user", "content": "hi"}]))

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "a privacy-mode violation was recorded but nothing was logged at ERROR"
        assert "api.llm7.io" in errors[0].getMessage()


# --- privacy mode OFF is not a violation --------------------------------------


class TestPrivacyOffIsNotAViolation:
    """The other control. A fix that stamped every event as a violation would
    satisfy every test above and be worse than no alarm at all."""

    def test_a_remote_call_with_privacy_off_is_recorded_but_not_flagged(self, gsettings_env, monkeypatch):
        from shani_chronoa.config import ChronoaConfig
        import shani_chronoa.cloud_llm as cloud_mod
        from shani_chronoa.cloud_llm import OpenAICompatibleLLM, PROVIDERS

        config = ChronoaConfig()
        config.set("privacy-mode", "false")
        assert config.privacy_mode is False

        _mocked_async(monkeypatch, cloud_mod, _ok_openai)
        asyncio.run(OpenAICompatibleLLM(PROVIDERS["llm7"]).chat_message([{"role": "user", "content": "hi"}]))

        event = egress.read_events()[0]
        assert event["privacy_mode"] is False
        assert event["local"] is False
        assert event["violation"] is False
        assert egress.summary()["remote"] == 1


# --- fail-safe reading of the setting -----------------------------------------


class TestReadingTheSettingFailsSafe:
    def test_an_unreadable_setting_reads_as_privacy_mode_on(self, monkeypatch):
        """The direction matters and is the whole point.

        `egress.record()` must never raise, and a GSettings read can fail in
        plenty of ways (no schema compiled, no dconf, a permissions error). If
        that degraded to "privacy mode is off" then a broken settings store
        would silently disarm the invariant the alarm exists to defend - the
        failure would look exactly like a clean, well-behaved run.
        """
        import shani_chronoa.config as config_mod

        def _explode(*args, **kwargs):
            raise RuntimeError("no settings backend on this machine")

        monkeypatch.setattr(config_mod, "ChronoaConfig", _explode)
        assert egress.privacy_mode_enabled() is True

    def test_a_remote_call_still_alarms_when_the_setting_cannot_be_read(self, monkeypatch):
        import shani_chronoa.config as config_mod
        import shani_chronoa.cloud_llm as cloud_mod
        from shani_chronoa.cloud_llm import OpenAICompatibleLLM, PROVIDERS

        def _explode(*args, **kwargs):
            raise RuntimeError("no settings backend on this machine")

        monkeypatch.setattr(config_mod, "ChronoaConfig", _explode)
        _mocked_async(monkeypatch, cloud_mod, _ok_openai)
        asyncio.run(OpenAICompatibleLLM(PROVIDERS["llm7"]).chat_message([{"role": "user", "content": "hi"}]))

        event = egress.read_events()[0]
        assert event["privacy_mode"] is True
        assert event["violation"] is True

    def test_the_default_with_no_schema_at_all_is_still_privacy_mode_on(self, monkeypatch):
        """A missing schema is not an exception: `ChronoaConfig` logs a warning
        and falls back to Python defaults. `get_bool("privacy-mode", True)`
        must therefore default to ON, which is a property of `config.py` and
        worth pinning here because this module now depends on it."""
        from shani_chronoa.config import ChronoaConfig

        monkeypatch.delenv("GSETTINGS_SCHEMA_DIR", raising=False)
        config = ChronoaConfig()
        config._settings = None  # noqa: SLF001 - simulating an uninstalled schema
        assert config.privacy_mode is True
        assert egress.privacy_mode_enabled() is True


# --- no call site may silently drop the flag again ---------------------------


class TestNoCallSiteCanDropTheFlagAgain:
    """A static check, and it is the right tool *here* for the same reason
    `test_egress_covers_local_paths.py` gives: the five sites build their
    requests five different ways, and the thing that must hold uniformly is a
    property of the source text. Five behavioural tests that each cover one
    site would also catch this, but a *new* call site added later would not be
    covered by any of them - this one is."""

    @pytest.mark.parametrize("name", sorted(INSTRUMENTED))
    def test_every_record_call_site_passes_privacy_mode(self, name):
        path = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa") / name
        tree = ast.parse(path.read_text())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "record"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "egress"
        ]
        assert calls, f"{name} records nothing at all"
        for call in calls:
            keywords = {kw.arg for kw in call.keywords}
            assert "privacy_mode" in keywords, (
                f"{name}:{call.lineno} calls egress.record without privacy_mode, so a "
                f"remote call made while privacy mode is ON is recorded as an "
                f"ordinary line and the alarm can never fire"
            )

    def test_the_only_way_to_ask_is_the_shared_helper(self):
        """One mechanism, not two. A second way of reading the setting would
        drift from the first and the two would disagree - which is precisely
        the failure this whole change exists to remove.

        Checked on the AST rather than the source text, because several of
        these files *discuss* `PrivacyManager` and `privacy-mode` in their
        docstrings and a substring search would flag the prose documenting the
        very mechanism being enforced.

        Constructing a `ChronoaConfig` is deliberately *not* forbidden:
        `senses/vision.py`'s `run()` builds one to ask `sense_allowed("vision")`,
        which is a different question asked for a different reason and is
        correct. What is forbidden is reading the privacy setting itself.
        """
        for name in sorted(INSTRUMENTED):
            tree = ast.parse((pathlib.Path("usr/lib/shani-chronoa/shani_chronoa") / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("get", "get_bool"):
                    assert not (node.args and node.args[0].value == "privacy-mode"), (
                        f"{name}:{node.lineno} reads the privacy setting directly; the call "
                        f"sites must go through egress.privacy_mode_enabled() so there is "
                        f"one answer, not several"
                    )
                if isinstance(node, ast.Attribute) and node.attr in ("privacy_mode", "is_local_only"):
                    assert False, (
                        f"{name}:{node.lineno} reads {node.attr!r} directly; that is a "
                        f"second mechanism for the same question"
                    )
