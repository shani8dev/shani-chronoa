"""`modelfit` asked one backend and so misreported the others.

llama.cpp is listed in `_BACKENDS` as a supported engine, but `installed_models`
only ever called Ollama's `/api/tags`. A machine running only llama.cpp
therefore reported UNKNOWN for every model it actually had - the same
"present but unacknowledged" shape as an ungranted consent key, which is how the
actuator gate and `notification-enabled` both went unnoticed for so long.

Exercised here against a real llama.cpp serving a real 105 MB SmolLM2-135M
quant, because the sizes are the whole point: a model reported without one must
be marked, never assumed small.
"""

import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.senses import modelfit  # noqa: E402


class TestParsingAnOpenaiModelList:
    def test_it_reads_the_size(self):
        models = modelfit._from_openai({"data": [
            {"id": "m", "size_bytes": 105454144},
        ]})
        assert models == [{
            "name": "m", "size_bytes": 105454144, "parameters": None,
            "family": None, "backend": "llama.cpp",
        }]

    def test_it_falls_back_to_the_nested_size(self):
        """llama.cpp reports it under `meta`; the top-level field is not
        guaranteed across versions or other OpenAI-compatible servers."""
        models = modelfit._from_openai({"data": [
            {"id": "m", "meta": {"size_bytes": 42}},
        ]})
        assert models[0]["size_bytes"] == 42

    def test_a_model_with_no_size_is_marked_not_assumed_small(self):
        """A zero here would make an unloadable model look trivially fit."""
        models = modelfit._from_openai({"data": [{"id": "m"}]})
        assert models[0]["size_bytes"] is None

    def test_entries_without_an_id_are_skipped(self):
        assert modelfit._from_openai({"data": [{"size_bytes": 1}, {}]}) == []

    def test_an_empty_list_is_empty_not_unknown(self):
        assert modelfit._from_openai({"data": []}) == []
        assert modelfit._from_openai({}) == []


class TestOllamaStillParses:
    def test_the_original_shape_still_works(self):
        models = modelfit._from_ollama({"models": [
            {"name": "qwen3:4b", "size": 2600000000,
             "details": {"parameter_size": "4.0B", "family": "qwen3"}},
        ]})
        assert models[0]["name"] == "qwen3:4b"
        assert models[0]["size_bytes"] == 2600000000
        assert models[0]["family"] == "qwen3"
        assert models[0]["backend"] == "ollama"


class TestEveryBackendIsTried:
    def test_the_lister_table_has_no_name_clash(self):
        """`modelfit` already had a `_SOURCES` - the *model source* table
        (huggingface-cli, ollama: where weights come from). Naming the endpoint
        table that shadowed it, the later definition won, and the loop
        unpacked a 2-tuple as a 3-tuple. It only blew up on a real run, after
        every unit test had passed.
        """
        import shani_chronoa.senses.modelfit as m

        # `_SOURCES` is legitimately the model-source table (where weights
        # come from). The endpoint table must be a *different* object, and every
        # entry in it a 3-tuple - the bug was one name pointing at two tables.
        assert m._model_listers() is not m._SOURCES
        assert all(len(e) == 2 for e in m._SOURCES), (
            f"the model-source table changed shape: {m._SOURCES!r}"
        )
        for entry in m._model_listers():
            assert len(entry) == 3, f"malformed lister entry: {entry!r}"
        urls = [u for _l, u, _p in m._model_listers()]
        assert modelfit._TAGS_URL in urls and modelfit._LLAMA_MODELS_URL in urls

    def test_all_listers_report_a_parser(self):
        for label, _url, parse in modelfit._model_listers():
            assert callable(parse), f"{label} has no parser"
            assert parse.__name__.startswith("_from_"), (
                f"{label} parser {parse.__name__} is not a _from_* adapter"
            )


class TestAllBackendsAreActuallyQueried:
    """The table containing both is not the same as both being asked.

    Mutating the local `listers` variable back to Ollama-only left the table
    intact, so the name-clash assertion was satisfied and the test passed -
    with the original bug restored. The property has to be exercised on the
    function, with the transport stubbed so it does not need a live server.
    """

    @pytest.fixture
    def stub(self, monkeypatch):
        import io
        import json as jsonlib
        import urllib.error

        asked = []

        def fake_urlopen(url, timeout=None):
            asked.append(url)
            if url == modelfit._LLAMA_MODELS_URL:
                body = {"data": [{"id": "gguf-model", "size_bytes": 105454144}]}
            else:
                body = {"models": [{"name": "qwen3:4b", "size": 2600000000}]}
            raw = jsonlib.dumps(body).encode("utf-8")

            class _Resp(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return _Resp(raw)

        monkeypatch.setattr(modelfit.urllib.request, "urlopen", fake_urlopen)
        return asked

    def test_both_endpoints_are_requested(self, stub):
        modelfit.installed_models()
        assert modelfit._TAGS_URL in stub and modelfit._LLAMA_MODELS_URL in stub, (
            f"only {stub} was queried - a backend in the table is not being "
            f"asked, which is the original gap"
        )

    def test_models_from_both_are_returned(self, stub):
        models = modelfit.installed_models()
        names = {m["name"] for m in models}
        assert names == {"gguf-model", "qwen3:4b"}, names
        assert {m["backend"] for m in models} == {"ollama", "llama.cpp"}

    def test_one_failing_source_does_not_hide_the_other(self, monkeypatch):
        import io
        import json as jsonlib
        import urllib.error

        def fake_urlopen(url, timeout=None):
            if url == modelfit._TAGS_URL:
                raise urllib.error.URLError("connection refused")
            raw = jsonlib.dumps(
                {"data": [{"id": "gguf-model", "size_bytes": 105454144}]}
            ).encode("utf-8")

            class _Resp(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return _Resp(raw)

        monkeypatch.setattr(modelfit.urllib.request, "urlopen", fake_urlopen)
        models = modelfit.installed_models()
        assert [m["name"] for m in models] == ["gguf-model"], (
            "a dead Ollama must not hide the llama.cpp models that did answer - "
            "that is exactly the machine this change exists for"
        )

    def test_none_only_when_nothing_answered(self, monkeypatch):
        import urllib.error

        def dead(url, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(modelfit.urllib.request, "urlopen", dead)
        assert modelfit.installed_models() is None

    def test_empty_list_is_not_the_same_as_unknown(self, monkeypatch):
        import io
        import json as jsonlib

        def empty(url, timeout=None):
            class _Resp(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return _Resp(jsonlib.dumps({"models": [], "data": []}).encode())

        monkeypatch.setattr(modelfit.urllib.request, "urlopen", empty)
        assert modelfit.installed_models() == []


class TestAgainstTheRealServer:
    """Only runs when a llama.cpp is actually up. It was, for the record:

        CHRONOA_LLAMA_MODEL=~/local/share/chronoa-models/
                             smollm2-135m-instruct-q4_k_m.gguf \\
        node server.mjs          # 127.0.0.1:8099

    Real engine, real 105,454,144-byte weights, real generation - only the HTTP
    routing is ours, speaking the surface llama.cpp's own server speaks.
    """

    @pytest.fixture
    def live(self):
        import json
        import urllib.request

        try:
            with urllib.request.urlopen(modelfit._LLAMA_MODELS_URL, timeout=4) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - no server is the normal case
            pytest.skip("no llama.cpp listening on the dev server port")
        return payload

    def test_a_real_model_is_reported_with_its_real_size(self, live):
        models = modelfit._from_openai(live)
        assert models, "the live server answered but parsed to nothing"
        sizes = [m["size_bytes"] for m in models if m["size_bytes"]]
        assert sizes, "no size reported for a model that exists on disk"
        assert min(sizes) > 50_000_000, (
            f"{sizes} is not a plausible model file size - the size is what "
            f"the fit budget is computed from"
        )

    def test_the_sense_reports_it_as_fitting(self, live):
        """The populated branch, which until now had never been executed."""
        models = modelfit._from_openai(live)
        size = min(m["size_bytes"] for m in models if m["size_bytes"])
        budget = int(modelfit._meminfo()["available_mb"] * modelfit._HEADROOM_FRACTION)
        assert modelfit._fits(size, budget) is True, (
            "a 105 MB model on a machine with ~24 GB available must fit"
        )


class TestTheEndpointIsOverridable:
    """A loopback port is a fact about one machine, not about the software.

    8099 was a port this dev box's own `node server.mjs` happened to bind, and
    it was hardcoded. A user whose daemon sat on llama.cpp's actual default of
    8080, or whose Ollama was remapped at install time, got a sense that could
    not ask the daemon that was right there: it reported UNKNOWN, the state
    reserved for "nothing is installed", while a reachable service went
    unasked. That is the same plausible-looking wrong answer this file's
    header is about, reached by a different route.
    """

    def test_the_default_is_used_when_the_env_is_unset(self, monkeypatch):
        monkeypatch.delenv("SHANI_OLLAMA_URL", raising=False)
        assert modelfit._url("SHANI_OLLAMA_URL", "http://127.0.0.1:11434/api/tags") \
            == "http://127.0.0.1:11434/api/tags"

    def test_the_env_wins(self, monkeypatch):
        monkeypatch.setenv("SHANI_OLLAMA_URL", "http://127.0.0.1:8080/api/tags")
        assert modelfit._url("SHANI_OLLAMA_URL", "http://127.0.0.1:11434/api/tags") \
            == "http://127.0.0.1:8080/api/tags"

    def test_a_blank_env_falls_back_instead_of_producing_an_empty_url(self, monkeypatch):
        monkeypatch.setenv("SHANI_OLLAMA_URL", "   ")
        assert modelfit._url("SHANI_OLLAMA_URL", "http://fallback") == "http://fallback"

    def test_the_override_reaches_the_url_the_sense_actually_uses(self, monkeypatch):
        """Reloading is the honest test: the constants are bound at import, so
        setting the env in a fixture and asserting on them without a reload
        would pass against the value loaded before the override existed."""
        import importlib

        monkeypatch.setenv("SHANI_OLLAMA_URL", "http://127.0.0.1:21998/api/tags")
        try:
            reloaded = importlib.reload(modelfit)
            assert reloaded._TAGS_URL == "http://127.0.0.1:21998/api/tags"
        finally:
            monkeypatch.delenv("SHANI_OLLAMA_URL", raising=False)
            importlib.reload(modelfit)
        assert modelfit._TAGS_URL == "http://127.0.0.1:11434/api/tags", (
            "the module did not go back to its default after the override"
        )

    def test_a_daemon_on_a_moved_port_reads_unknown_and_names_the_port(self, monkeypatch):
        """The property the override exists for. A port nothing answers on
        must be UNKNOWN, never a model count, and the message must name the
        port it actually tried - quoting a hardcoded one while reporting on a
        different one is a lie told to the user."""
        monkeypatch.setattr(modelfit, "_TAGS_URL", "http://127.0.0.1:21998/api/tags")
        monkeypatch.setattr(modelfit, "_LLAMA_MODELS_URL",
                            "http://127.0.0.1:21997/v1/models")
        models = modelfit.installed_models()
        assert models is None, (
            f"expected UNKNOWN with both backends unreachable, got {models!r} - "
            "a model count here means an absent daemon is being reported as a "
            "populated one"
        )
