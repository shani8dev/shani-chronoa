"""The egress log: making "local-first" checkable rather than merely claimed.

The distinction that matters is not local-vs-remote in the abstract, it is
whether a request that a user believed stayed on their machine actually did.
So the properties pinned here are that classification is conservative, that a
remote call made while privacy mode is on is recorded as a violation rather
than a log line, and that the log never captures a payload - a log that
captured bodies would become the very leak it exists to prevent.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import egress  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    """Never write to the developer's real egress log from a test."""
    monkeypatch.setattr(egress, "EGRESS_DIR", tmp_path / "egress")
    monkeypatch.setattr(egress, "EGRESS_LOG", tmp_path / "egress" / "egress.jsonl")
    yield


class TestClassification:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:11434/api/chat",
            "http://localhost:11434/",
            "http://[::1]:8080/x",
            "http://printer.local/status",
            "http://db.internal/query",
            "https://api.host.localdomain/v1",
        ],
    )
    def test_loopback_and_local_namespaces_are_local(self, url):
        assert egress.is_local(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.llm7.io/v1/chat/completions",
            "https://api.anthropic.com/v1/messages",
            "https://api.openai.com/v1/responses",
            "https://generativelanguage.googleapis.com/v1beta/models",
            "http://example.com/",
            # A public host that merely *looks* local must not pass.
            "https://evil-local.attacker.com/",
            "https://notlocalhost.example/",
        ],
    )
    def test_real_destinations_are_remote(self, url):
        assert egress.is_local(url) is False

    @pytest.mark.parametrize("url", ["", "not a url", "://", "http://"])
    def test_unparseable_targets_fail_closed_as_remote(self, url):
        """Guessing wrong here is the dangerous direction, so it must fail remote."""
        assert egress.is_local(url) is False


class TestRecording:
    def test_a_local_call_is_recorded_as_local(self):
        egress.record("ollama", "http://127.0.0.1:11434/api/chat", method="POST", status=200, bytes_out=12)
        events = egress.read_events()
        assert len(events) == 1
        assert events[0]["local"] is True
        assert events[0]["bytes_out"] == 12
        assert events[0]["status"] == 200

    def test_a_remote_call_is_recorded_as_remote(self):
        egress.record("cloud_llm:llm7", "https://api.llm7.io/v1/chat/completions", method="POST")
        events = egress.read_events()
        assert events[0]["local"] is False
        assert events[0]["host"] == "api.llm7.io"

    def test_a_remote_call_under_privacy_mode_is_flagged_as_a_violation(self):
        """Chronoa's documented invariant is that privacy mode keeps data local."""
        egress.record("cloud_llm:llm7", "https://api.llm7.io/v1/chat/completions", privacy_mode=True)
        assert egress.read_events()[0]["violation"] is True
        assert egress.summary()["violations"] == 1

    def test_a_local_call_under_privacy_mode_is_not_a_violation(self):
        egress.record("ollama", "http://127.0.0.1:11434/api/chat", privacy_mode=True)
        assert egress.read_events()[0]["violation"] is False
        assert egress.summary()["violations"] == 0


class TestTheModelDownloadException:
    """The one exception to "privacy mode means nothing leaves", and its edges.

    A model download *is* egress, and it was logged as a breach every time - so
    a log that cries wolf for every consented setup is a log nobody reads, and a
    real breach is one line among hundreds that look identical. The exception is
    deliberately hard to reach, and these are the ways it must not be reachable.
    """

    def test_an_ordinary_remote_request_is_still_a_violation(self):
        event = egress.record("web_search", "https://duckduckgo.com/", privacy_mode=True)
        assert event.violation is True

    def test_a_consented_model_download_to_a_pinned_host_is_not(self):
        event = egress.record("llm:provision", "https://huggingface.co/x/y.gguf",
                              privacy_mode=True, purpose="model-download",
                              consented=True)
        assert event.violation is False
        assert event.purpose == "model-download"

    def test_consent_alone_does_not_help(self):
        """Without the purpose, `consented=True` is just a claim."""
        event = egress.record("web_search", "https://huggingface.co/",
                              privacy_mode=True, consented=True)
        assert event.violation is True

    def test_the_purpose_alone_does_not_help(self):
        event = egress.record("web_search", "https://huggingface.co/",
                              privacy_mode=True, purpose="model-download")
        assert event.violation is True

    def test_a_host_that_merely_looks_like_a_model_host_is_still_a_violation(self):
        """`not-huggingface.co` ends with `huggingface.co` as a string but is not
        that host, which is the whole reason the match is on a dot boundary."""
        assert egress.is_model_host("huggingface.co")
        assert not egress.is_model_host("evil-huggingface.co")
        assert egress.is_model_host("cdn-lfs.huggingface.co")
        assert not egress.is_model_host("huggingface.co.evil.net")
        event = egress.record("cloud_llm:x", "https://evil-huggingface.co/v1",
                              privacy_mode=True, purpose="model-download",
                              consented=True)
        assert event.violation is True

    def test_a_local_request_is_never_a_violation(self):
        event = egress.record("ollama", "http://127.0.0.1:11434/", privacy_mode=True)
        assert event.violation is False


class TestTheLogItself:
    def test_the_log_is_owner_only(self):
        egress.record("ollama", "http://127.0.0.1:11434/")
        assert egress.EGRESS_LOG.stat().st_mode & 0o077 == 0, "log readable by other users"

    def test_the_log_never_stores_a_payload(self):
        """A body in the log would be the leak this exists to prevent."""
        secret = "sk-live-DO-NOT-LOG-THIS"
        egress.record("cloud_llm:x", "https://api.example.com/v1", bytes_out=len(secret))
        raw = egress.EGRESS_LOG.read_text(encoding="utf-8")
        assert secret not in raw
        # Only the documented keys are present. `purpose` joined them when a
        # consented model download was allowed to stop counting as a breach -
        # it is a short label for what the request was for, never content, and
        # the test that keeps it honest is the one below that asserts the value
        # is a name and not a body.
        assert set(json.loads(raw.strip())) == {
            "at", "component", "url", "host", "local", "method",
            "status", "bytes_out", "privacy_mode", "violation", "purpose",
        }
        assert json.loads(raw.strip())["purpose"] == "", (
            "an ordinary request must not claim a purpose")

    def test_a_corrupt_line_is_skipped_rather_than_poisoning_the_log(self):
        egress.record("ollama", "http://127.0.0.1:1/")
        with open(egress.EGRESS_LOG, "a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        assert len(egress.read_events()) == 1

    def test_a_missing_log_reads_as_empty_not_an_error(self, monkeypatch):
        monkeypatch.setattr(egress, "EGRESS_LOG", egress.EGRESS_DIR / "nope.jsonl")
        assert egress.read_events() == []
        assert egress.summary()["total"] == 0

    def test_the_log_survives_a_directory_that_cannot_be_created(self, monkeypatch):
        """A telemetry log that crashes the request it describes is worse than none."""
        monkeypatch.setattr(egress, "EGRESS_DIR", egress.EGRESS_DIR / "proc" / "x" / "y")
        event = egress.record("ollama", "http://127.0.0.1:1/")
        assert event.local is True  # returned, not raised


class TestWhereTheLogIsWritten:
    """The log's location is part of the audit trail's trustworthiness.

    It used to be a module constant built from a hardcoded `~/.local/share`, so
    the only way to relocate it was to change the source. A test run that
    redirected `XDG_STATE_HOME` therefore wrote every fixture record into the
    real user's `~/.local/share/shani-chronoa/egress/egress.jsonl` - 4,320 lines
    of fixture data in a file whose whole purpose is to be a truthful record of
    what actually left the machine. A polluted audit log is worse than an
    absent one, because it is believed.
    """

    @staticmethod
    def _real_log() -> Path:
        """The pre-fix destination: home-based, and never under `XDG_DATA_HOME`.

        Computed per call so a run that redirects `HOME` is compared against its
        own home rather than the developer's.
        """
        return Path.home() / ".local" / "share" / "shani-chronoa" / "egress" / "egress.jsonl"

    @staticmethod
    def _unpin(monkeypatch):
        """Drop the autouse fixture's redirect so the environment decides.

        That fixture exists so a test cannot write into the developer's real
        log, and it does its job - but it pins the very values these tests are
        about, so leaving it in place would let a regression pass by proving only
        that the pin works.
        """
        monkeypatch.setattr(egress, "EGRESS_DIR", egress._DEFAULT_EGRESS_DIR)
        monkeypatch.setattr(
            egress, "EGRESS_LOG", egress._DEFAULT_EGRESS_DIR / "egress.jsonl"
        )

    @staticmethod
    def _fingerprint(path: Path):
        """`(size, mtime_ns)`, or None if the file is not there at all.

        None round-trips, so a machine that has never run Chronoa is asserted to
        still have no log afterwards instead of being skipped. `mtime_ns` as well
        as size, because a rewrite of identical length would satisfy size alone.
        """
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns)

    def test_recording_follows_xdg_data_home_and_leaves_the_real_log_untouched(
        self, tmp_path, monkeypatch
    ):
        xdg = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
        self._unpin(monkeypatch)

        real_log = self._real_log()
        real_dir = real_log.parent
        before = self._fingerprint(real_log)
        before_listing = sorted(p.name for p in real_dir.iterdir()) if real_dir.is_dir() else None

        egress.record("ollama", "http://127.0.0.1:11434/api/chat", method="POST", status=200)

        written = xdg / "shani-chronoa" / "egress" / "egress.jsonl"
        assert written.is_file(), (
            f"the record did not land under XDG_DATA_HOME; resolved to {egress._egress_log()}"
        )
        assert json.loads(written.read_text(encoding="utf-8").strip())["host"] == "127.0.0.1"
        assert egress.summary()["log"] == str(written), "summary reports a path other than the one written"

        assert self._fingerprint(real_log) == before, (
            "the record was also appended to the home-based log that XDG_DATA_HOME was "
            "meant to replace"
        )
        after_listing = sorted(p.name for p in real_dir.iterdir()) if real_dir.is_dir() else None
        assert after_listing == before_listing, "something was created under the real home"

    @pytest.mark.parametrize(
        "case, expect_xdg",
        [
            ("set", True),
            ("unset", False),
            ("empty", False),
            ("relative", False),
        ],
        ids=["XDG_DATA_HOME set", "XDG_DATA_HOME unset", "XDG_DATA_HOME empty", "XDG_DATA_HOME relative"],
    )
    def test_the_home_default_applies_to_every_unusable_xdg_data_home(
        self, tmp_path, monkeypatch, case, expect_xdg
    ):
        """Resolution only - never `record()`: three of the four cases resolve to
        the real home, and asserting on the answer is the safe way to pin it."""
        self._unpin(monkeypatch)
        configured = str(tmp_path / "xyz") if case == "set" else "" if case == "empty" else case

        if case == "unset":
            monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        else:
            monkeypatch.setenv("XDG_DATA_HOME", configured)
        assert ("XDG_DATA_HOME" in os.environ) is (case != "unset"), "fixture did not set the case up"

        base = Path(configured) if expect_xdg else Path.home() / ".local" / "share"
        expected = base / "shani-chronoa" / "egress"

        resolved = egress._egress_dir()
        assert resolved == expected
        assert resolved.is_absolute(), (
            f"XDG_DATA_HOME={configured!r} produced a relative log path, so the audit log "
            "would be written against the process's working directory"
        )
        assert egress._egress_log() == expected / "egress.jsonl"


class TestSummary:
    def test_summary_separates_local_from_remote_and_names_the_hosts(self):
        egress.record("ollama", "http://127.0.0.1:11434/api/chat", bytes_out=10)
        egress.record("cloud:a", "https://api.llm7.io/v1", bytes_out=100)
        egress.record("cloud:b", "https://api.anthropic.com/v1", bytes_out=50)
        stats = egress.summary()
        assert stats["total"] == 3
        assert stats["local"] == 1
        assert stats["remote"] == 2
        assert stats["bytes_out"] == 160
        assert stats["remote_hosts"] == ["api.anthropic.com", "api.llm7.io"]


class TestCallSitesActuallyRecord:
    """The unit tests above prove `record` works. These prove the instrumented
    call site fires, which is the part that can silently stop being called."""

    def test_a_real_chat_dispatch_is_recorded(self, monkeypatch):
        import asyncio
        import httpx
        import shani_chronoa.cloud_llm as cloud_mod

        real_async_client = httpx.AsyncClient

        def _factory(**kwargs):
            return real_async_client(**kwargs, transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
                )
            ))

        monkeypatch.setattr(cloud_mod.httpx, "AsyncClient", _factory)
        from shani_chronoa.cloud_llm import OpenAICompatibleLLM, PROVIDERS

        asyncio.run(OpenAICompatibleLLM(PROVIDERS["llm7"]).chat_message(
            [{"role": "user", "content": "hi"}]
        ))

        events = egress.read_events()
        assert len(events) == 1, "a real dispatch recorded nothing - the instrumentation is dead"
        assert events[0]["method"] == "POST"
        assert events[0]["host"] == "api.llm7.io"
        assert events[0]["local"] is False
        assert events[0]["bytes_out"] > 0

    def test_a_failed_dispatch_is_still_recorded(self, monkeypatch):
        """A request that left the machine counts as leaving, even if it failed."""
        import asyncio
        import httpx
        import shani_chronoa.cloud_llm as cloud_mod

        real_async_client = httpx.AsyncClient

        def _factory(**kwargs):
            return real_async_client(**kwargs, transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))
            ))

        monkeypatch.setattr(cloud_mod.httpx, "AsyncClient", _factory)
        from shani_chronoa.cloud_llm import CloudLLMError, OpenAICompatibleLLM, PROVIDERS

        with pytest.raises(CloudLLMError):
            asyncio.run(OpenAICompatibleLLM(PROVIDERS["llm7"]).chat_message(
                [{"role": "user", "content": "hi"}]
            ))

        events = egress.read_events()
        assert len(events) == 1, "a request that was sent and failed was not recorded"
        assert events[0]["local"] is False

    def test_the_anthropic_path_is_also_recorded(self, monkeypatch):
        """Both cloud providers leave the machine, so both must be recorded.
        An unrecorded Anthropic call is the gap a user is most likely to hit."""
        import asyncio
        import httpx
        import shani_chronoa.cloud_llm as cloud_mod

        real_async_client = httpx.AsyncClient

        def _factory(**kwargs):
            return real_async_client(**kwargs, transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})
            ))

        monkeypatch.setattr(cloud_mod.httpx, "AsyncClient", _factory)
        from shani_chronoa.cloud_llm import AnthropicLLM

        asyncio.run(AnthropicLLM(api_key="test-key").chat_message(
            [{"role": "user", "content": "hi"}]
        ))

        events = egress.read_events()
        assert len(events) == 1, "the Anthropic call site is not instrumented"
        assert events[0]["local"] is False
        assert events[0]["host"] == "api.anthropic.com"


class TestPayloadSizeNeverRaises:
    """Computed from a `finally` block on the error path, so it must not raise
    or it would mask the CloudLLMError the caller is trying to surface."""

    def test_ordinary_payloads_measure_correctly(self):
        assert egress.payload_size({"a": 1}) == len('{"a": 1}')

    @pytest.mark.parametrize(
        "unserializable",
        [
            {"fn": object()},
            {"s": {1, 2, 3}},
            {"bytes": b"raw"},
        ],
    )
    def test_unserializable_payloads_degrade_to_zero_rather_than_raising(self, unserializable):
        assert egress.payload_size(unserializable) == 0

    def test_a_recursive_payload_degrades_to_zero(self):
        recursive = {}
        recursive["self"] = recursive
        assert egress.payload_size(recursive) == 0

    def test_a_failing_dispatch_still_raises_its_own_error(self, monkeypatch):
        """The bug this guards: a raise in the finally would replace CloudLLMError."""
        import asyncio
        import httpx
        import shani_chronoa.cloud_llm as cloud_mod

        real_async_client = httpx.AsyncClient

        def _factory(**kwargs):
            return real_async_client(**kwargs, transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))
            ))

        monkeypatch.setattr(cloud_mod.httpx, "AsyncClient", _factory)
        from shani_chronoa.cloud_llm import CloudLLMError, OpenAICompatibleLLM, PROVIDERS

        with pytest.raises(CloudLLMError, match="request failed"):
            asyncio.run(OpenAICompatibleLLM(PROVIDERS["llm7"]).chat_message(
                [{"role": "user", "content": "hi"}]
            ))
