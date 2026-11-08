"""Setup offers every speech backend, dynamically from the catalogues.

Setup used to build the Ears choice from `stt_provision.MODELS` alone, so
Parakeet - a shipped, selectable backend (`stt-backend`) - was invisible at
first boot for its whole life, and `setup_ears("parakeet:...")` would have
looked up a whisper model under a Parakeet key. Now the choice is
`stt_catalogue()`: both backends, one spelling, nothing hardcoded.
"""

from shani_chronoa import engines, local_llm, setup_wizard, stt_provision


def _fake_engines(monkeypatch):
    seen = {}
    monkeypatch.setattr(engines, "ensure",
                        lambda program, **kw: seen.setdefault("engine", program))
    return seen


def test_the_catalogue_offers_both_backends_dynamically():
    catalogue = setup_wizard.stt_catalogue()
    keys = [k for k, _l, _s, _p in catalogue]
    assert set(stt_provision.MODELS) <= set(keys), "a whisper tier is missing from setup"
    prefixed = {f"{stt_provision.PARAKEET_KEY_PREFIX}{k}" for k in stt_provision.PARAKEET_MODELS}
    assert prefixed <= set(keys), "a Parakeet model is missing from setup"
    assert any(program == "parakeet-cli" for _k, _l, _s, program in catalogue)


def test_setup_ears_parakeet_selects_that_backend(monkeypatch):
    seen = _fake_engines(monkeypatch)
    provisioned = {}
    monkeypatch.setattr(stt_provision, "provision_parakeet",
                        lambda key, **kw: provisioned.setdefault("key", key))
    calls = {}
    monkeypatch.setattr(setup_wizard.ChronoaConfig, "set",
                        lambda self, k, v: calls.__setitem__(k, v))
    out = setup_wizard.setup_ears("parakeet:parakeet-q4_0", report=lambda *a: None)
    assert out == "Chronoa can now hear you."
    assert seen["engine"] == "parakeet-cli"
    assert provisioned["key"] == "parakeet-q4_0"
    assert calls.get("stt-backend") == "parakeet"


def test_setup_ears_whisper_selects_that_backend(monkeypatch):
    seen = _fake_engines(monkeypatch)
    provisioned = {}
    monkeypatch.setattr(stt_provision, "provision",
                        lambda key, **kw: provisioned.setdefault("key", key))
    calls = {}
    monkeypatch.setattr(setup_wizard.ChronoaConfig, "set",
                        lambda self, k, v: calls.__setitem__(k, v))
    setup_wizard.setup_ears("base-q5_1", report=lambda *a: None)
    assert seen["engine"] == "whisper-cli"
    assert calls.get("stt-backend") == "whisper"
    assert calls.get("whisper-model") == "base"


def test_ears_state_counts_a_packaged_parakeet_model(monkeypatch):
    monkeypatch.setattr(stt_provision, "is_provisioned", lambda key: False)
    monkeypatch.setattr(stt_provision, "is_parakeet_provisioned", lambda key: True)
    monkeypatch.setattr(local_llm, "server_binary", lambda: "")
    state = setup_wizard.state()
    assert state["ears"]["model"] is True
