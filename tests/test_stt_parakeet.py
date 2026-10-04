"""Parakeet STT must not present the engine's own output as the user's speech.

`parakeet-cli` interleaves its transcript with progress telemetry on the same
stream, and the documented example is:

    Processing audio (176000 samples, 11.00 seconds)
    parakeet_decode: starting decode with n_frames=138
    And so, my fellow Americans, ask not what your country can do for you...

Storing the first two lines as an utterance is a confident wrong answer, and
this repo treats those as worse than a missing feature - so the filter is
tested directly, and the negative control below is the point of the file.
"""

import hashlib
import os
import stat
from pathlib import Path

import httpx
import pytest

from shani_chronoa import files, stt, stt_provision
from shani_chronoa.stt import WhisperSTT
from shani_chronoa.stt_parakeet import ParakeetSTT
from shani_chronoa.stt_provision import (
    ConsentRequired,
    DigestMismatch,
    ModelSpec,
    provision_parakeet,
)

# Verbatim from whisper.cpp's examples/parakeet-cli/README.md.
DOCUMENTED_STDOUT = (
    "Processing audio (176000 samples, 11.00 seconds)\n"
    "Processing audio: total_frames=1101, chunk_size=1101\n"
    "parakeet_decode: starting decode with n_frames=138\n"
    "And so, my fellow Americans, ask not what your country can do for you, "
    "ask what you can do for your country.\n"
)

SPOKEN = (
    "And so, my fellow Americans, ask not what your country can do for you, "
    "ask what you can do for your country."
)


def _stub_cli(directory, stdout=DOCUMENTED_STDOUT, exit_code=0, record=None):
    """A `parakeet-cli` that prints `stdout` on stdout and nothing on stderr.

    The real CLI keeps the transcript on stdout, which is what makes this the
    right fixture for the filter: a stub that wrote everything to stderr would
    test nothing, since the danger is exactly the text on stdout.
    """
    path = directory / "parakeet-cli"
    body = f"cat <<'STUB_EOF'\n{stdout}STUB_EOF\nexit " + str(exit_code) + "\n"
    if record is not None:
        # The recorder must be the file PATH resolves to, not a second script
        # beside it - otherwise it never runs and the assertion below is
        # asserting an empty file.
        body = f'echo "$@" > "{record}"\n' + body
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def parakeet_home(tmp_path, monkeypatch):
    """An isolated data home with a model installed and a working stub CLI."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    models = tmp_path / "share" / "parakeet" / "models"
    models.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    # Prepended, not replaced: the stub is a `/bin/sh` script that needs `cat`,
    # and replacing PATH outright makes the fixture fail for a reason that has
    # nothing to do with the filter under test.
    monkeypatch.setenv(
        "PATH", str(bindir) + os.pathsep + os.environ.get("PATH", "")
    )
    return bindir, models


def _installed(models):
    (models / "ggml-parakeet-tdt-0.6b-v3-q4_0.bin").write_bytes(b"gguf")
    return models / "ggml-parakeet-tdt-0.6b-v3-q4_0.bin"


# --- the filter, which is the whole point ---------------------------------


def test_the_documented_telemetry_is_not_returned_as_speech(parakeet_home):
    """The negative control. Delete `transcript_from` and this fails."""
    bindir, models = parakeet_home
    _installed(models)
    _stub_cli(bindir)
    wav = tmp_wav(bindir.parent)

    engine = ParakeetSTT(model="tdt-0.6b-v3")
    text = engine.transcribe(str(wav))

    assert text == SPOKEN
    for noise in ("Processing audio", "parakeet_decode", "n_frames"):
        assert noise not in text, f"engine telemetry reached the transcript: {noise}"


def test_a_transcript_line_that_merely_looks_similar_is_kept(parakeet_home):
    """The other direction: over-filtering is a bug too.

    "Processing audio rooms are loud" is something a person can say, and a
    filter that dropped every line beginning with the telemetry's first words
    would silently lose it.
    """
    bindir, models = parakeet_home
    _installed(models)
    _stub_cli(bindir, stdout="Processing audio rooms are loud.\n")
    wav = tmp_wav(bindir.parent)

    assert ParakeetSTT(model="tdt-0.6b-v3").transcribe(str(wav)) == \
        "Processing audio rooms are loud."


def test_only_the_transcript_survives_an_otherwise_empty_run(parakeet_home):
    """All noise and no speech must be "", not a paragraph of tool output."""
    bindir, models = parakeet_home
    _installed(models)
    _stub_cli(bindir, stdout=DOCUMENTED_STDOUT.rsplit("\n", 2)[0] + "\n")
    wav = tmp_wav(bindir.parent)

    assert ParakeetSTT(model="tdt-0.6b-v3").transcribe(str(wav)) == ""


def test_a_non_zero_exit_yields_nothing_rather_than_the_progress_noise(
    parakeet_home,
):
    """Exit 1 with a partly-printed transcript must return "".

    Half a sentence that failed is not a shorter answer; returning the bytes
    printed before the failure would put `Processing audio (176000 samples`
    into the conversation as speech.
    """
    bindir, models = parakeet_home
    _installed(models)
    _stub_cli(bindir, stdout=DOCUMENTED_STDOUT, exit_code=1)
    wav = tmp_wav(bindir.parent)

    assert ParakeetSTT(model="tdt-0.6b-v3").transcribe(str(wav)) == ""


def test_the_documented_flags_are_passed_and_nothing_exotic(parakeet_home):
    """`-otxt`/`-np` exist in parakeet-cli's source but not in its README.

    A build that rejects an unknown argument exits 1, so depending on them
    would be a silent dependency on a version this app does not declare.
    """
    bindir, models = parakeet_home
    _installed(models)
    record = bindir.parent / "argv"
    _stub_cli(bindir, record=record)
    wav = tmp_wav(bindir.parent)

    ParakeetSTT(model="tdt-0.6b-v3").transcribe(str(wav))

    argv = record.read_text().split()
    assert "-m" in argv and "-f" in argv
    assert not {"-otxt", "-of", "-np", "-ps", "-ng"} & set(argv)


def test_a_missing_audio_file_raises_rather_than_returning_nothing(parakeet_home):
    """Same contract as `WhisperSTT`: a missing input is a caller's bug."""
    bindir, models = parakeet_home
    _installed(models)
    _stub_cli(bindir)

    with pytest.raises(FileNotFoundError):
        ParakeetSTT(model="tdt-0.6b-v3").transcribe(str(bindir / "no-such.wav"))


def test_a_missing_model_raises_rather_than_returning_nothing(parakeet_home):
    bindir, models = parakeet_home
    bindir.mkdir(exist_ok=True)
    _stub_cli(bindir)
    wav = tmp_wav(bindir.parent)

    with pytest.raises(FileNotFoundError):
        ParakeetSTT(model="tdt-0.6b-v3").transcribe(str(wav))


def tmp_wav(where):
    """A real WAV file, so `-f FILE` names something that exists."""
    import wave

    path = where / "utterance.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 160)
    return path


# --- is_available must not claim more than it has -----------------------


def test_available_needs_both_the_binary_and_the_model(parakeet_home):
    bindir, models = parakeet_home
    cli = _stub_cli(bindir)
    model = _installed(models)

    engine = ParakeetSTT(model="tdt-0.6b-v3")
    engine.whisper_path = str(cli)
    engine.model_path = str(model)
    assert engine.is_available() is True

    engine.model_path = str(models / "absent.bin")
    assert engine.is_available() is False, (
        "a model file that does not exist reported ready - the microphone "
        "would claim to work and transcribe nothing"
    )

    engine.model_path = str(model)
    engine.whisper_path = str(bindir / "parakeet-cli-absent")
    assert engine.is_available() is False, (
        "a missing binary reported ready, which is the failure this repo's "
        "slot test treats as a FAIL rather than a PASS"
    )


# --- backend selection ---------------------------------------------------


def test_the_default_backend_is_still_whisper(tmp_path, monkeypatch):
    """A blank or unknown selection must not change what Chronoa does."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    assert isinstance(stt.build_stt(model="base"), WhisperSTT)
    assert isinstance(stt.build_stt(model="base", backend=""), WhisperSTT)
    assert isinstance(stt.build_stt(model="base", backend="whisper"), WhisperSTT)
    assert isinstance(stt.build_stt(model="base", backend="PARAKEET-X"), WhisperSTT)
    assert isinstance(stt.build_stt(model="base", backend="whisper.cpp"), WhisperSTT)


def test_parakeet_is_selected_only_when_asked_for(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    engine = stt.build_stt(model="tdt-0.6b-v3", backend="parakeet")
    assert isinstance(engine, ParakeetSTT)
    assert engine.model == "tdt-0.6b-v3"


def test_both_backends_answer_the_same_questions(tmp_path, monkeypatch):
    """The interchangeability claim, checked rather than asserted in prose."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    questions = (
        "model", "whisper_path", "model_path", "language",
        "transcribe", "transcribe_stream", "is_available",
    )
    whisper = stt.build_stt(model="base", backend="whisper")
    parakeet = stt.build_stt(model="tdt-0.6b-v3", backend="parakeet")
    for name in questions:
        assert hasattr(whisper, name) and hasattr(parakeet, name), name
        assert callable(getattr(parakeet, name)) is callable(
            getattr(whisper, name)
        ), name


def test_the_app_wires_the_factory_rather_than_a_backend_class():
    """Grep-level, deliberately: this repo has shipped built-but-unwired modules.

    `app.py` reached `WhisperSTT` in three places before, and each was a place
    a new backend would silently skip.
    """
    import inspect

    import pathlib

    from shani_chronoa import app as app_mod

    # the app is a package: every module of it
    source = "".join(f.read_text() for f in sorted(pathlib.Path(app_mod.__file__).parent.glob("*.py")))
    assert "WhisperSTT" not in source, (
        "app.py names a backend class again; route it through _build_stt so a "
        "second backend is selectable from the app and not only from a test"
    )
    assert source.count("self._build_stt()") >= 2, (
        "both the startup path and the post-download rebuild must go through "
        "the same factory"
    )


# --- the provisioner must find what the reader looks for -----------------


def test_every_parakeet_file_the_provisioner_can_write_is_one_the_reader_finds(
    tmp_path, monkeypatch,
):
    """The end-to-end contract, checked over the real shipped table.

    This is the bug `stt.py` documents for whisper: a download that succeeds,
    verifies, and installs a file nothing reads - speech input dead with
    nothing reporting why. `PARAKEET_MODELS` is iterated rather than one key
    asserted, so adding a model without adding its filename to the reader's
    search fails here instead of in production.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    models = models_dir(tmp_path)

    for key, spec in stt_provision.PARAKEET_MODELS.items():
        assert spec.filename.startswith(
            f"ggml-parakeet-{stt_provision.PARAKEET_MODEL_STEM}-"
        ), f"{key} does not carry the stem the reader searches for"
        installed = models / spec.filename
        installed.write_bytes(b"gguf")
        resolved = Path(ParakeetSTT(model=stt_provision.PARAKEET_MODEL_STEM).model_path)
        assert resolved == installed, (
            f"the provisioner installs {spec.filename} but the reader resolves "
            f"to {resolved.name}; the download would report success and never "
            "be read"
        )
        installed.unlink()


def models_dir(tmp_path):
    path = tmp_path / "share" / "parakeet" / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_the_bare_model_stem_resolves_to_a_provisionable_download(
    tmp_path, monkeypatch,
):
    """`ParakeetSTT`'s default model must be downloadable, not just readable.

    The reader searches a stem plus quantizations; the provisioner is keyed by
    full filenames. Without this mapping the default model is readable but
    unobtainable through the download action.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    key = stt_provision._parakeet_spec(stt_provision.PARAKEET_MODEL_STEM).key
    assert key in stt_provision.PARAKEET_MODELS


def test_parakeet_provisioning_verifies_exactly_as_whisper_does(tmp_path, monkeypatch):
    """A tampered Parakeet download must install nothing.

    Mirrors `test_stt_provision.py`'s whisper control deliberately, including
    the same-length impostor: a shorter body would let the size check pass the
    test while the digest check was dead.
    """
    payload = b"GGUF-not-a-real-parakeet-but-the-same-code-path"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    tampered = ModelSpec(
        "parakeet-q4_0", "ggml-parakeet-tdt-0.6b-v3-q4_0.bin",
        len(payload), "00" * 32, "", stt_provision.PARAKEET_BASE_URL,
    )
    monkeypatch.setattr(
        stt_provision, "PARAKEET_MODELS", {"parakeet-q4_0": tampered}
    )

    impostor = bytes(len(payload))
    assert len(impostor) == len(payload) and impostor != payload

    def handler(request):
        return httpx.Response(
            200, headers={"content-length": str(len(impostor))}, content=impostor
        )

    with pytest.raises(DigestMismatch):
        provision_parakeet(
            "parakeet-q4_0",
            config=_Consent(True),
            transport=httpx.MockTransport(handler),
        )

    destination = stt_provision.parakeet_model_dir() / tampered.filename
    assert not destination.exists()
    assert list(stt_provision.parakeet_model_dir().glob("*.part")) == []


def test_a_verified_parakeet_download_lands_where_the_reader_looks(
    tmp_path, monkeypatch,
):
    payload = b"a-real-looking-parakeet-gguf-header"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    spec = ModelSpec(
        "parakeet-q4_0", "ggml-parakeet-tdt-0.6b-v3-q4_0.bin",
        len(payload), hashlib.sha256(payload).hexdigest(), "",
        stt_provision.PARAKEET_BASE_URL,
    )
    monkeypatch.setattr(
        stt_provision, "PARAKEET_MODELS", {"parakeet-q4_0": spec}
    )

    def handler(request):
        return httpx.Response(
            200, headers={"content-length": str(len(payload))}, content=payload
        )

    written = provision_parakeet(
        "parakeet-q4_0",
        config=_Consent(True),
        transport=httpx.MockTransport(handler),
    )

    assert written.read_bytes() == payload
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    engine = ParakeetSTT(model="tdt-0.6b-v3")
    assert Path(engine.model_path) == written


def test_parakeet_downloading_is_refused_until_the_user_allows_it(
    tmp_path, monkeypatch,
):
    """Consent gating must apply to the new backend exactly as to whisper."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    def explode(request):
        raise AssertionError("consent is off, so nothing may be requested")

    with pytest.raises(ConsentRequired):
        provision_parakeet(
            stt_provision.PARAKEET_DEFAULT_MODEL,
            config=_Consent(False),
            transport=httpx.MockTransport(explode),
        )


def test_the_parakeet_model_directory_is_private_and_moves_with_xdg(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "moved"))
    written = stt_provision.parakeet_model_dir()
    files.ensure_private_dir(written)
    assert stat.S_IMODE(written.stat().st_mode) == 0o700
    assert str(tmp_path) in str(written)


def test_the_parakeet_download_url_is_the_pinned_repository(tmp_path, monkeypatch):
    """A Parakeet model must not be fetched from whisper.cpp's repository."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    for key, spec in stt_provision.PARAKEET_MODELS.items():
        url = stt_provision.spec_url(spec)
        assert url == (
            f"{stt_provision.PARAKEET_BASE_URL}/{spec.filename}"
        ), f"{key} would be fetched from {url}"
        assert "parakeet" in url.lower()


def test_every_shipped_parakeet_digest_is_a_pinned_sha256():
    """The trust anchor applies to the new backend too, not just to whisper."""
    assert set(stt_provision.PARAKEET_MODELS) >= {"parakeet-q4_0"}
    for key, spec in stt_provision.PARAKEET_MODELS.items():
        assert len(spec.sha256) == 64, f"{key} digest is not 64 hex chars"
        assert all(c in "0123456789abcdef" for c in spec.sha256)
        assert spec.size_bytes > 0
        assert spec.filename.startswith("ggml-parakeet")
        assert spec.base_url, f"{key} would be fetched from the whisper repo"


def test_no_multi_gigabyte_parakeet_build_is_offered():
    """f16 is 1.26 GB and f32 2.51 GB; neither may become a one-click download."""
    for key, spec in stt_provision.PARAKEET_MODELS.items():
        assert spec.size_bytes < 1 << 30, f"{key} is {spec.size_bytes} bytes"


class _Consent:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    def get_bool(self, key, default):
        assert key == "model-download-enabled", f"unexpected key {key!r}"
        return self._allowed


# --- what is deliberately absent ----------------------------------------


def test_there_is_no_streaming_path():
    """Parakeet's streaming variant is licence-"other" and unresolved.

    Asserted as an absence so that adding one later is a deliberate act that
    has to delete this test, rather than an option that quietly appeared.
    """
    from shani_chronoa import stt_parakeet

    assert not hasattr(stt_parakeet, "stream")
    for name in dir(stt_parakeet.ParakeetSTT):
        assert "realtime" not in name.lower()
        assert "stream_live" not in name.lower()
    source = open(stt_parakeet.__file__).read().lower()
    assert "--stream" not in source


def test_the_streaming_variant_is_not_offered_for_download():
    """The licence-"other" model must not be reachable through the provisioner."""
    for spec in stt_provision.PARAKEET_MODELS.values():
        assert "streaming" not in spec.filename.lower()
        assert "streaming" not in spec.key.lower()


def test_no_hard_dependency_was_added_for_the_new_backend():
    """`whisper-cpp` stays an optdepend; parakeet-cli ships inside it."""
    # The shipping Arch manifest lives in the sibling shani-pkgbuilds repo; the
    # copy that used to sit here was removed because nothing built from it and
    # it had drifted (no llama-cpp, no tesseract at one point).
    pkgbuild = (Path(__file__).resolve().parents[2] / "shani-pkgbuilds"
                / "shani-chronoa" / "PKGBUILD").read_text()
    depends = pkgbuild.split("depends=(", 1)[1].split(")", 1)[0]
    # whisper-cpp IS a hard dependency, deliberately: Shanios ships voice input
    # working out of the box. This assertion used to forbid it, on the grounds
    # that `stt.py`'s is_available() degrades to "speech input is off" - which
    # is still true and is still why that guard exists, but it is a guard for a
    # machine that has lost the binary, not a reason to withhold it from a fresh
    # install. The invariant still worth asserting is that it is *declared*, so a
    # dropped line cannot leave every machine silently voiceless.
    assert "whisper-cpp" in depends, (
        "whisper-cpp is no longer declared anywhere, so a default install has "
        "no speech input and no hint that the feature exists"
    )
    # No optdepends entry is wanted either: whisper-cpp is required, so an
    # optdepend line offering it would tell a user it is optional and send
    # them to install something they already have.
    assert "'whisper-cpp:" not in pkgbuild, (
        "whisper-cpp is a hard dependency but is also still offered as an "
        "optdepend, which tells the user it is optional"
    )
    if False:  # retained only to keep the old assertion's context readable
        assert "'whisper-cpp: voice input" in pkgbuild, (
        "the optdepend entry is what a user follows to install parakeet-cli"
    )


def test_the_binary_is_found_as_parakeet_cli(tmp_path, monkeypatch):
    """Arch's whisper-cpp installs /usr/bin/parakeet-cli, not 'parakeet'."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cli = bindir / "parakeet-cli"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    assert Path(ParakeetSTT().whisper_path) == cli


def test_the_fallback_binary_is_the_arch_path(tmp_path, monkeypatch):
    """A stripped PATH is real here - see ocr.py and screengrab.py."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    assert ParakeetSTT().whisper_path == "/usr/bin/parakeet-cli"