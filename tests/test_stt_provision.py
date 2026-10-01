"""Provisioning an STT model must never install bytes it has not verified.

These tests drive the real `provision()` through a stubbed httpx transport.
Nothing here touches the network, and the pinned table is replaced with a
handful of bytes whose digest the test computes itself, so the happy path
exercises exactly the same verification code as a real 57 MB download.
"""

import hashlib
import os
import stat
from pathlib import Path

import httpx
import pytest

from shani_chronoa import files, stt_provision
from shani_chronoa.stt_provision import (
    ConsentRequired,
    DigestMismatch,
    ModelSpec,
    ProvisionError,
    provision,
)

PAYLOAD = b"GGUF-not-a-real-model-but-the-same-code-path"
PINNED = hashlib.sha256(PAYLOAD).hexdigest()


class _Config:
    """Stands in for ChronoaConfig without touching GSettings."""

    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    def get_bool(self, key, default):
        assert key == "model-download-enabled", f"unexpected key {key!r}"
        return self._allowed


@pytest.fixture
def pinned(monkeypatch):
    """Replace the shipped table with one tiny entry we can hash ourselves."""
    spec = ModelSpec("tiny-q5_1", "ggml-tiny-q5_1.bin", len(PAYLOAD), PINNED)
    monkeypatch.setattr(stt_provision, "MODELS", {"tiny-q5_1": spec})
    return spec


def _transport(body=PAYLOAD, status=200, location=None, requests=None):
    """A transport that answers one canned response and records requests."""
    calls = requests if requests is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if location is not None:
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(
            status,
            headers={"content-length": str(len(body))},
            content=body,
        )

    return httpx.MockTransport(handler), calls


# --- the happy path -------------------------------------------------------

def test_verified_bytes_land_where_stt_looks_for_them(pinned):
    transport, _ = _transport()
    path = provision(
        "tiny-q5_1", config=_Config(True), transport=transport
    )
    assert path.read_bytes() == PAYLOAD
    assert path.parent == stt_provision.model_dir()
    assert path.name == pinned.filename


def test_the_installed_model_is_not_world_readable(pinned):
    """Both ends are restricted, because os.replace preserves the temp's mode."""
    transport, _ = _transport()
    path = provision("tiny-q5_1", config=_Config(True), transport=transport)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_model_directory_is_private(pinned):
    transport, _ = _transport()
    provision("tiny-q5_1", config=_Config(True), transport=transport)
    assert stat.S_IMODE(stt_provision.model_dir().stat().st_mode) == 0o700


def test_an_already_present_model_is_not_fetched_again(pinned, tmp_path):
    existing = stt_provision.model_dir() / pinned.filename
    files.ensure_private_dir(existing.parent)
    existing.write_bytes(b"already here")

    def explode(request):
        raise AssertionError("must not fetch a model that is already installed")

    result = provision(
        "tiny-q5_1",
        config=_Config(False),
        transport=httpx.MockTransport(explode),
    )
    assert result == existing


# --- the controls that matter --------------------------------------------

def test_bytes_that_do_not_match_the_pinned_digest_are_not_installed(
    pinned, monkeypatch
):
    """The whole point. A tampered download must leave nothing behind.

    The substituted body is the SAME LENGTH as the pinned one on purpose. An
    earlier draft used a shorter body, and the suite stayed green with the
    digest comparison deleted entirely - the size check fired first and the
    test passed for the wrong reason. A control that cannot tell verification
    from a length check is not a control.
    """
    tampered = ModelSpec(
        "tiny-q5_1", pinned.filename, len(PAYLOAD), "00" * 32
    )
    monkeypatch.setattr(stt_provision, "MODELS", {"tiny-q5_1": tampered})

    impostor = bytes(len(PAYLOAD))  # same size, different bytes
    assert len(impostor) == len(PAYLOAD) and impostor != PAYLOAD

    transport, _ = _transport(body=impostor)
    with pytest.raises(DigestMismatch):
        provision("tiny-q5_1", config=_Config(True), transport=transport)

    assert not (stt_provision.model_dir() / pinned.filename).exists()
    assert list(stt_provision.model_dir().glob("*.part")) == []


def test_a_truncated_download_is_rejected_even_when_the_prefix_hashes_right(
    pinned,
):
    """Size is checked as well as digest, so a short file cannot slip past."""
    truncated = ModelSpec(
        "tiny-q5_1", pinned.filename, len(PAYLOAD) + 4096, PINNED
    )
    original = stt_provision.MODELS
    stt_provision.MODELS = {"tiny-q5_1": truncated}
    try:
        transport, _ = _transport(body=PAYLOAD)
        with pytest.raises(DigestMismatch):
            provision("tiny-q5_1", config=_Config(True), transport=transport)
    finally:
        stt_provision.MODELS = original
    assert not (stt_provision.model_dir() / pinned.filename).exists()


def test_downloading_is_refused_until_the_user_allows_it(pinned):
    def explode(request):
        raise AssertionError("consent is off, so nothing may be requested")

    with pytest.raises(ConsentRequired):
        provision(
            "tiny-q5_1",
            config=_Config(False),
            transport=httpx.MockTransport(explode),
        )


def test_a_redirect_into_the_cloud_metadata_address_is_refused(pinned):
    """check_destination fails closed, so the second hop is never requested."""
    seen = []
    transport, _ = _transport(
        location="http://169.254.169.254/latest/meta-data/", requests=seen
    )
    with pytest.raises(ProvisionError):
        provision("tiny-q5_1", config=_Config(True), transport=transport)
    assert seen == [stt_provision.spec_url(pinned)]


def test_a_redirect_loop_is_bounded_rather_than_followed_forever(pinned):
    def handler(request):
        return httpx.Response(302, headers={"location": str(request.url)})

    with pytest.raises(ProvisionError):
        provision(
            "tiny-q5_1",
            config=_Config(True),
            transport=httpx.MockTransport(handler),
        )


def test_an_unknown_model_name_is_refused_before_any_request(pinned):
    def explode(request):
        raise AssertionError("an unknown name must not reach the network")

    with pytest.raises(ProvisionError):
        provision(
            "nope", config=_Config(True), transport=httpx.MockTransport(explode)
        )


def test_an_oversized_content_length_is_refused_before_the_bytes(pinned):
    def handler(request):
        return httpx.Response(
            200, headers={"content-length": str(1 << 31)}, content=b""
        )

    with pytest.raises(ProvisionError):
        provision(
            "tiny-q5_1",
            config=_Config(True),
            transport=httpx.MockTransport(handler),
        )


def test_a_server_error_does_not_install_anything(pinned):
    transport, _ = _transport(status=503)
    with pytest.raises(ProvisionError):
        provision("tiny-q5_1", config=_Config(True), transport=transport)
    assert not (stt_provision.model_dir() / pinned.filename).exists()


# --- the digests are the trust anchor ------------------------------------

def test_every_shipped_digest_is_a_plausible_sha256():
    for key, spec in stt_provision.MODELS.items():
        assert len(spec.sha256) == 64, f"{key} digest is not 64 hex chars"
        assert spec.sha256 == spec.sha256.lower(), f"{key} digest has uppercase"
        assert all(c in "0123456789abcdef" for c in spec.sha256)
        assert spec.size_bytes > 0
        assert spec.filename.startswith("ggml-")


def test_no_model_is_shipped_without_a_digest_to_check_it_against():
    """The pin is the trust anchor, so a missing one is a hard error."""
    assert set(stt_provision.MODELS) >= {"tiny-q5_1", "base-q5_1"}
    for key, spec in stt_provision.MODELS.items():
        assert spec.sha256, f"{key} would install unverified bytes"


def test_the_default_model_is_one_we_can_actually_provision():
    assert stt_provision.DEFAULT_MODEL in stt_provision.MODELS

# --- the integration that actually matters -------------------------------
# Provisioning can be perfect and still useless: if the file it writes is not a
# name `stt.py` looks for, the download "succeeds" and speech input stays dead
# with nothing reporting why. The two halves shipped in different commits and
# disagreed on the filename (`ggml-base.bin` vs `ggml-base-q5_1.bin`), so this
# is pinned deliberately.


def test_a_provisioned_file_is_one_the_stt_lookup_actually_finds(pinned):
    """The end-to-end contract: provision, then resolve, and they must meet."""
    from shani_chronoa.stt import WhisperSTT

    transport, _ = _transport()
    written = provision("tiny-q5_1", config=_Config(True), transport=transport)

    resolved = Path(WhisperSTT(model="tiny").model_path)
    assert resolved == written, (
        f"provisioned {written.name} but WhisperSTT resolves to {resolved.name}; "
        "the download would report success and never be read"
    )
    assert resolved.is_file()


def test_a_hardware_tier_name_resolves_to_a_provisionable_key():
    """`HardwareProfile.get_whisper_model()` returns 'tiny'/'base', not 'base-q5_1'."""
    from shani_chronoa import stt_provision as sp
    for tier in ("tiny", "base", "small"):
        assert sp.resolve_key(tier) in sp.MODELS
        assert sp.MODELS[sp.resolve_key(tier)].filename.startswith("ggml-")


def test_an_explicit_model_key_is_left_alone():
    from shani_chronoa import stt_provision as sp
    assert sp.resolve_key("base-q5_1") == "base-q5_1"


def test_the_quantized_name_is_found_after_the_exact_one():
    """Ordering is a contract: `ggml-<model>.bin` stays first.

    A test elsewhere pins the first name, and the full-precision file is what a
    distro would package, so the quantized spellings must only ever be a
    fallback.
    """
    from shani_chronoa.stt import WhisperSTT

    exact = WhisperSTT(model="base")._get_model_path
    assert exact("base").endswith("ggml-base.bin")


# --- the action the settings button fires -------------------------------


def test_the_download_action_starts_a_worker_and_installs_a_verified_model(monkeypatch):
    """End to end from the action: click -> thread -> verified file on disk.

    `provision` is driven through a stubbed transport, so this proves the
    wiring and the download together without touching the network.
    """
    import threading

    from shani_chronoa import app as app_mod

    class _Cfg:
        whisper_model = "base"
        language = "en"
        model_download_enabled = True

        def get_bool(self, key, default):
            return key == "model-download-enabled"

    monkeypatch.setattr(app_mod.stt_provision, "MODELS", {
        "base-q5_1": ModelSpec("base-q5_1", "ggml-base-q5_1.bin", len(PAYLOAD), PINNED),
    })

    real_provision = app_mod.stt_provision.provision
    called = {}

    def fake_provision(key, **kwargs):
        called["key"] = key
        kwargs.pop("transport", None)
        return real_provision(key, transport=_transport()[0], **kwargs)

    monkeypatch.setattr(app_mod.stt_provision, "provision", fake_provision)

    application = app_mod.ChronoaApplication.__new__(app_mod.ChronoaApplication)
    application.config = _Cfg()
    application.window = None
    application._model_download_thread = None
    application.hardware = type("H", (), {"get_whisper_model": staticmethod(lambda: "base")})()
    application._set_status = lambda m: None

    application._download_speech_model()
    thread = application._model_download_thread
    assert isinstance(thread, threading.Thread), "the download must not run on this thread"
    thread.join(timeout=10)

    written = stt_provision.model_dir() / "ggml-base-q5_1.bin"
    assert written.is_file(), "the worker did not install a verified model"
    assert written.read_bytes() == PAYLOAD
    assert called["key"] == "base-q5_1", "the tier name must resolve to the small build"


def test_the_download_action_refuses_when_consent_is_off(monkeypatch):
    """No network request may happen before the user has granted it."""
    from shani_chronoa import app as app_mod

    class _Cfg:
        whisper_model = "base"
        language = "en"
        model_download_enabled = False

        def get_bool(self, key, default):
            return False

    application = app_mod.ChronoaApplication.__new__(app_mod.ChronoaApplication)
    application.config = _Cfg()
    application.window = None
    application._model_download_thread = None
    application.hardware = type("H", (), {"get_whisper_model": staticmethod(lambda: "base")})()
    seen = []
    application._set_status = seen.append

    def explode():
        raise AssertionError("a download started with consent off")

    monkeypatch.setattr(app_mod.stt_provision, "provision", explode)
    application._download_speech_model()
    assert application._model_download_thread is None, "it must not have started a thread"
    assert any("off" in m.lower() for m in seen), f"the refusal must say why: {seen}"


def test_an_untiered_model_name_is_reported_not_attempted(monkeypatch):
    """An unknown tier gets a message, never a request."""
    from shani_chronoa import app as app_mod

    class _Cfg:
        whisper_model = "enormous"
        language = "en"
        model_download_enabled = True

        def get_bool(self, key, default):
            return True

    application = app_mod.ChronoaApplication.__new__(app_mod.ChronoaApplication)
    application.config = _Cfg()
    application.window = None
    application._model_download_thread = None
    application.hardware = type("H", (), {"get_whisper_model": staticmethod(lambda: "enormous")})()
    seen = []
    application._set_status = seen.append
    monkeypatch.setattr(
        app_mod.stt_provision, "provision",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not attempt")),
    )
    application._download_speech_model()
    assert application._model_download_thread is None
    assert any("enormous" in m for m in seen), f"the message must name the model: {seen}"


def test_the_action_is_actually_registered_not_just_defined():
    """A button wired to an unregistered action name does nothing, silently.

    The tests above call the handler directly, so none of them notice if
    `_create_actions` stops registering it - the settings row would still be
    there, still look right, and clicking it would do nothing at all.

    `list_actions()` is no help: it reports the action map only for a registered
    GApplication, and there is no session bus here. So this records what
    `_create_actions` actually adds.
    """
    from shani_chronoa.app import ChronoaApplication

    # The real constructor, not __new__: an uninitialised GObject raises from
    # add_action, which would fail this test for the wrong reason.
    application = ChronoaApplication()
    added = []
    application.add_action = lambda action, *a, **k: added.append(action.get_name())
    application._create_actions()

    assert "download-speech-model" in added, (
        "the settings button fires 'download-speech-model' but _create_actions "
        f"never adds it; added: {sorted(added)}"
    )
    assert "toggle-model-download" in added, (
        "the consent switch beside it fires a name that is never added"
    )
