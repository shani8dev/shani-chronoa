"""A person can choose which cloud model answers, and the choice actually runs.

Two things this file covers, and the second is the one that was broken.

**The capability was here and unreachable.** `CloudLLMChain.__init__` has read an
`anthropic_model` override since it was written, and read nothing for the other
eight providers - so a model could be chosen for one provider and not for the
seven, and nothing in the tree ever let anyone choose one at all. Meanwhile
`check_health` was already fetching `GET /models` from every provider and throwing
the body away, so the one live, per-provider model list in reach was being read
and discarded on every probe.

**Measured 2026-10-12, unauthenticated, over this machine's own network:**
llm7 64 models, BlockRun 115, OpenCode Zen 83, Kilo 390, OpenRouter 458 - and
OpenAI/Groq answer 401 without a key while Google answers 404 because its API is
not OpenAI-shaped. Five of eight give a real list with no key at all, including
all three free gateways. So the list is not theoretical and the 401 providers are
a different failure, not the same one.

`TestTheKeyShape` is the regression test for the bug the first version of this
work shipped: `cloud_llm_models()` returned the provider's plain id as the dict
key, so the choice was saved and read by nothing. The probe that found it is in
the docstring too, because it was a five-line check against real objects and a
reading of the code had said it worked.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import cloud_llm  # noqa: E402


class _Reply:
    """What `urlopen` hands back, so no test touches the network."""

    def __init__(self, body):
        self._body = body

    def read(self, *args):
        return json.dumps(self._body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _serve(monkeypatch, body, fail=None):
    """Answer every `/models` request with `body`, or raise `fail`."""
    def urlopen(request, timeout=0):
        if fail is not None:
            raise fail
        monkeypatch._served_url = request.full_url
        return _Reply(body)

    import shani_chronoa.cloud_llm as module
    monkeypatch.setattr(module, "list_models", module.list_models.__wrapped__
                        if hasattr(module.list_models, "__wrapped__")
                        else module.list_models)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return urlopen


class TestListingModels:
    def test_the_openai_shape_is_parsed(self, monkeypatch):
        _serve(monkeypatch, {"data": [{"id": "alpha"}, {"id": "beta"}]})
        ids, why = cloud_llm.list_models("kilo")
        assert why == ""
        assert ids == ["alpha", "beta"]

    def test_a_bare_list_is_parsed_too(self, monkeypatch):
        """Some gateways answer `[{"id": ...}]` rather than an object.

        Parsed because it is real, not because it is tidy - a reader that
        understood only `{"data": [...]}` would report "no model list" about a
        provider that gave one.
        """
        _serve(monkeypatch, [{"id": "alpha"}, {"id": "beta"}])
        assert cloud_llm.list_models("kilo") == (["alpha", "beta"], "")

    def test_a_401_is_reported_not_returned_empty(self, monkeypatch):
        """OpenAI and Groq answer 401 without a key, and that is not "no
        models"."""
        import urllib.error
        _serve(monkeypatch, {}, fail=urllib.error.HTTPError(
            "https://api.openai.com/v1/models", 401, "Unauthorized", {}, None))
        ids, why = cloud_llm.list_models("openai")
        assert ids == []
        assert "401" in why
        assert "Unauthorized" in why

    def test_an_unreachable_host_is_reported(self, monkeypatch):
        _serve(monkeypatch, {}, fail=OSError("no route to host"))
        ids, why = cloud_llm.list_models("kilo")
        assert ids == [] and "no route to host" in why

    def test_a_body_with_no_list_is_refused(self, monkeypatch):
        _serve(monkeypatch, {"unexpected": "shape"})
        ids, why = cloud_llm.list_models("kilo")
        assert ids == []
        assert "no model list" in why

    def test_an_empty_list_is_a_refusal_not_a_silence(self, monkeypatch):
        """`([], "")` is never returned: "there are none" and "could not ask"
        are different answers and this is the line that keeps them apart.
        """
        _serve(monkeypatch, {"data": []})
        ids, why = cloud_llm.list_models("kilo")
        assert ids == [] and why

    def test_duplicates_are_dropped(self, monkeypatch):
        """OpenRouter repeats a model once per pricing variant, and a menu with
        the same name twice is a menu nobody can choose from."""
        _serve(monkeypatch, {"data": [{"id": "a"}, {"id": "b"},
                                      {"id": "a:batch"}, {"id": "a"}]})
        ids, _why = cloud_llm.list_models("openrouter")
        assert ids.count("a") == 1

    def test_an_unknown_provider_is_refused_by_name(self, monkeypatch):
        ids, why = cloud_llm.list_models("no-such-provider")
        assert ids == []
        assert "no-such-provider" in why

    def test_the_url_is_the_providers_own(self, monkeypatch):
        _serve(monkeypatch, {"data": [{"id": "a"}]})
        cloud_llm.list_models("kilo")
        expected = cloud_llm.PROVIDERS["kilo"].base_url.rstrip("/") + "/models"
        assert monkeypatch._served_url == expected


class TestWhatAPersonMayPick:
    """The bounded view the setup page actually shows."""

    def _models(self, monkeypatch, ids):
        monkeypatch.setattr(cloud_llm, "list_models", lambda pid, **kw: (ids, ""))

    def test_the_default_is_first(self, monkeypatch):
        self._models(monkeypatch, ["zzz", "kilo-auto/free", "aaa"])
        offered, why = cloud_llm.offerable_models("kilo")
        assert why == ""
        assert offered[0] == cloud_llm.PROVIDERS["kilo"].default_model

    def test_free_models_come_before_the_rest(self, monkeypatch):
        """Measured: OpenRouter's raw list leads with `stepfun/step-5-preview`
        and `anthropic/claude-haiku-5.5` - both paid - and its `:free` variants
        are pages further down. Truncating would have offered only the paid ones
        on the provider whose whole point is being free.
        """
        self._models(monkeypatch, ["openrouter/free", "stepfun/step-5-preview",
                                   "anthropic/claude-haiku-5.5",
                                   "apodex/apodex-1.1-mini:free"])
        offered, _why = cloud_llm.offerable_models("openrouter")
        assert offered == ["openrouter/free", "apodex/apodex-1.1-mini:free",
                           "stepfun/step-5-preview",
                           "anthropic/claude-haiku-5.5"]

    def test_the_list_is_bounded(self, monkeypatch):
        """458 models is not a menu, and the docstring says the cap is ours."""
        self._models(monkeypatch, [f"model-{i}" for i in range(458)])
        offered, _why = cloud_llm.offerable_models("openrouter")
        assert len(offered) == cloud_llm._MENU_LIMIT

    def test_an_unreadable_list_falls_back_to_the_default(self, monkeypatch):
        """A chooser with nothing in it reads as "this provider has no
        models", which nobody has claimed.
        """
        monkeypatch.setattr(cloud_llm, "list_models",
                            lambda pid, **kw: ([], "401 Unauthorized"))
        offered, why = cloud_llm.offerable_models("openai")
        assert offered == []
        assert "401" in why
        assert cloud_llm.PROVIDERS["openai"].default_model == "gpt-4o-mini"


class TestTheKeyShape:
    """The bug the first version of this work shipped.

    `cloud_llm_models()` returned `{"kilo": "..."}` while `CloudLLMChain` reads
    `api_keys["kilo_model"]` - the shape it has always used for Anthropic. So the
    model was saved, the settings row showed it, and the backend used its
    default. Everything looked wired.
    """

    def test_the_dict_key_is_the_one_the_chain_reads(self, tmp_path, monkeypatch):
        from shani_chronoa.config import ChronoaConfig

        home = tmp_path / "home"
        os.makedirs(home / "glib-2.0" / "settings", exist_ok=True)
        with open(home / "glib-2.0" / "settings" / "keyfile", "w") as handle:
            handle.write("[org/shani/chronoa]\n")
        monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
        monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", "usr/share/glib-2.0/schemas")

        config = ChronoaConfig()
        config.set_cloud_model("kilo", "stepfun/step-5-preview-free")
        models = config.cloud_llm_models()
        assert "kilo_model" in models, (
            f"the key is {sorted(models)!r}, and the chain reads "
            f"'<pid>_model' - so this choice is saved and read by nothing")
        assert "kilo" not in models

    def test_blank_means_the_providers_default(self, tmp_path, monkeypatch):
        from shani_chronoa.config import ChronoaConfig

        home = tmp_path / "home"
        os.makedirs(home / "glib-2.0" / "settings", exist_ok=True)
        with open(home / "glib-2.0" / "settings" / "keyfile", "w") as handle:
            handle.write("[org/shani/chronoa]\n")
        monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
        monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", "usr/share/glib-2.0/schemas")

        config = ChronoaConfig()
        assert config.cloud_llm_models() == {}
        config.set_cloud_model("kilo", "anything")
        config.set_cloud_model("kilo", "")
        assert config.cloud_llm_models() == {}

    def test_an_unknown_provider_is_ignored_not_stored(self, tmp_path,
                                                       monkeypatch):
        from shani_chronoa.config import ChronoaConfig

        home = tmp_path / "home"
        os.makedirs(home / "glib-2.0" / "settings", exist_ok=True)
        with open(home / "glib-2.0" / "settings" / "keyfile", "w") as handle:
            handle.write("[org/shani/chronoa]\n")
        monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
        monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", "usr/share/glib-2.0/schemas")
        ChronoaConfig().set_cloud_model("no-such-provider", "x")
        assert ChronoaConfig().cloud_llm_models() == {}


class TestTheChainHonoursIt:
    """The override is read for every provider, not only Anthropic."""

    def _chain(self, monkeypatch, provider, model):
        chain = cloud_llm.CloudLLMChain(
            provider_ids=(provider,),
            api_keys={"openai": "sk-x", "openai_model": model})
        return chain

    def test_the_chosen_model_reaches_the_backend(self, monkeypatch):
        chain = self._chain(monkeypatch, "openai", "gpt-4o")
        assert [b.model for b in chain._backends] == ["gpt-4o"]

    def test_no_choice_means_the_providers_default(self, monkeypatch):
        chain = cloud_llm.CloudLLMChain(provider_ids=("openai",),
                                        api_keys={"openai": "sk-x"})
        assert [b.model for b in chain._backends] == ["gpt-4o-mini"]

    def test_anthropic_still_reads_its_own_key(self, monkeypatch):
        """The one that always worked, kept working."""
        chain = cloud_llm.CloudLLMChain(
            provider_ids=("anthropic",),
            api_keys={"anthropic": "sk-a", "anthropic_model": "claude-haiku-4-5"})
        from shani_chronoa.cloud_llm import AnthropicLLM
        assert any(isinstance(b, AnthropicLLM) and b.model == "claude-haiku-4-5"
                   for b in chain._backends)

    def test_a_free_provider_takes_one_too(self, monkeypatch):
        """The free gateways are where the list is longest and needed most."""
        chain = cloud_llm.CloudLLMChain(
            provider_ids=("kilo",), api_keys={"kilo": "k",
                                              "kilo_model": "stepfun/step-5"})
        assert [b.model for b in chain._backends] == ["stepfun/step-5"]


class TestEveryProviderHasAModelKey:
    """One table per axis, or a provider with no row is a provider nobody can
    choose a model for - the same shape as a consent key with no switch."""

    def test_every_provider_has_a_model_setting(self):
        from shani_chronoa.config import MODEL_SETTINGS
        from shani_chronoa.cloud_llm import PROVIDERS
        missing = sorted(set(PROVIDERS) - set(MODEL_SETTINGS))
        assert not missing, f"no model setting for: {missing}"

    def test_every_model_key_is_in_the_schema(self):
        from shani_chronoa.config import MODEL_SETTINGS
        import glob
        import re
        keys = set()
        for path in glob.glob("usr/share/glib-2.0/schemas/*.xml"):
            for match in re.finditer(r'<key[^>]*name="([^"]+)"',
                                     pathlib.Path(path).read_text()):
                keys.add(match.group(1))
        missing = sorted(v for v in MODEL_SETTINGS.values() if v not in keys)
        assert not missing, (
            f"these settings are read by config.py but declared nowhere, so "
            f"every read falls back to a hardcoded default: {missing}")

