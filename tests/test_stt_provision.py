"""Provisioning an STT model must never install bytes it has not verified.

These tests drive the real `provision()` through a stubbed httpx transport.
Nothing here touches the network, and the pinned table is replaced with a
handful of bytes whose digest the test computes itself, so the happy path
exercises exactly the same verification code as a real 57 MB download.
"""

import hashlib
import os
import stat

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