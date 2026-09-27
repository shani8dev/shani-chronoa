"""The OCR sense drives the tesseract binary the way Shanios actually ships it.

tesseract is not installed on the dev host, so the binary under test is a real
executable shell script on PATH (written by these tests) that records its
argv and prints a known TSV. That exercises the whole real path: argument
construction, tessdata discovery, the subprocess call, TSV parsing, the
timeout, and the non-zero-exit branch. It does not exercise tesseract's own
recognition - see `test_ocr_sense.py`'s module docstring in `ocr.py` and the
verification notes in the repo's AUDIT-HISTORY for where that is covered.
"""

import pytest

# Row 0 is tesseract's TSV header. The rows below are hand-built to cover
# every branch of the parser: a level-1 page row, two words on one line, a
# word below the confidence floor, a word on a second line, a level-4
# container row (ignored: level 5 is a word), and a word whose confidence
# cell is not a number.
TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "1\t1\t0\t0\t0\t0\t0\t0\t200\t60\t-1\t\n"
    "5\t1\t1\t1\t1\t1\t10\t100\t40\t12\t96.5\tHello\n"
    "5\t1\t1\t1\t1\t2\t60\t100\t30\t12\t95.0\tworld\n"
    "5\t1\t1\t1\t1\t3\t95\t100\t30\t12\t4.0\tnoisy\n"
    "5\t1\t1\t1\t2\t1\t10\t140\t45\t12\t91.0\tsecond\n"
    "4\t1\t1\t1\t1\t0\t10\t100\t85\t12\t90.0\tHello world\n"
    "5\t1\t1\t1\t3\t1\t10\t180\t40\t12\tjunk\tgarbled\n"
)


def _fake_tesseract(tmp_path, monkeypatch, body=None):
    """Put a real executable named `tesseract` on PATH. Returns its argv log."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    argv_log = tmp_path / "argv.log"
    known_tsv = tmp_path / "known.tsv"
    known_tsv.write_text(TSV)
    if body is None:
        body = 'cat "{}"'.format(known_tsv)
    script = bindir / "tesseract"
    script.write_text(
        "#!/bin/sh\n"
        + 'printf \'%s\\n\' "$@" > "{}"\n'.format(argv_log)
        + body
        + "\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    return argv_log


def _tessdata(tmp_path, monkeypatch, *codes):
    """A tessdata directory holding the given language codes."""
    prefix = tmp_path / "share"
    tessdata = prefix / "tessdata"
    tessdata.mkdir(parents=True)
    for code in codes:
        (tessdata / f"{code}.traineddata").write_bytes(b"x")
    monkeypatch.setenv("TESSDATA_PREFIX", str(prefix))
    return tessdata


def _image(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    return image


@pytest.fixture
def ocr_enabled(chronoa_config):
    """Consent granted for this test only; ocr defaults to false."""
    chronoa_config.set("ocr-sense-enabled", "true")
    return chronoa_config


def _argv(argv_log):
    return argv_log.read_text().splitlines()


def test_argv_is_built_for_the_image_and_resolved_tessdata(tmp_path, monkeypatch, ocr_enabled):
    """Given consent and an installed tesseract, the argv is the ported one."""
    from shani_chronoa.senses import ocr as ocr_mod

    argv_log = _fake_tesseract(tmp_path, monkeypatch)
    tessdata = _tessdata(tmp_path, monkeypatch, "eng", "deu")
    image = _image(tmp_path)

    result = ocr_mod.run({"path": str(image)})

    assert not isinstance(result, str), result
    assert _argv(argv_log) == [
        str(image),
        "stdout",
        "-l",
        "eng",
        "--psm",
        "3",
        "--tessdata-dir",
        str(tessdata),
        "tsv",
    ]


def test_words_below_the_confidence_floor_are_dropped_and_lines_are_kept(
    tmp_path, monkeypatch, ocr_enabled
):
    from shani_chronoa.senses import ocr as ocr_mod

    _fake_tesseract(tmp_path, monkeypatch)
    _tessdata(tmp_path, monkeypatch, "eng")
    image = _image(tmp_path)

    result = ocr_mod.run({"path": str(image)})

    # 'noisy' (conf 4.0) and 'garbled' (non-numeric conf) are filtered;
    # the level-4 container row is not a word.
    assert result.content.endswith("Hello world\nsecond")
    assert [box["text"] for box in result.metadata["boxes"]] == ["Hello", "world", "second"]
    assert result.metadata["word_count"] == 3
    assert result.metadata["boxes"][0] == {
        "text": "Hello",
        "left": 10,
        "top": 100,
        "width": 40,
        "height": 12,
        "confidence": 96.5,
    }


def test_percept_carries_the_file_as_its_source_and_private_sensitivity(
    tmp_path, monkeypatch, ocr_enabled
):
    from shani_chronoa.senses import SENSITIVITY_PRIVATE, ocr as ocr_mod

    _fake_tesseract(tmp_path, monkeypatch)
    _tessdata(tmp_path, monkeypatch, "eng")
    image = _image(tmp_path)

    result = ocr_mod.run({"path": str(image)})

    assert result.source == str(image)
    assert result.sensitivity == SENSITIVITY_PRIVATE
    assert result.ttl_seconds == 120.0 and not result.is_expired()


def test_uninstalled_languages_fall_back_to_an_installed_one(tmp_path, monkeypatch, ocr_enabled):
    from shani_chronoa.senses import ocr as ocr_mod

    argv_log = _fake_tesseract(tmp_path, monkeypatch)
    _tessdata(tmp_path, monkeypatch, "eng", "deu")
    image = _image(tmp_path)

    result = ocr_mod.run({"path": str(image), "languages": ["fra", "deu", "deu"]})

    assert _argv(argv_log)[3] == "deu"
    assert result.metadata["languages"] == ["deu"]


def test_a_language_string_cannot_become_a_shell_option(tmp_path, monkeypatch, ocr_enabled):
    """A requested code is filtered against `[A-Za-z0-9_]+` before it is used."""
    from shani_chronoa.senses import ocr as ocr_mod

    argv_log = _fake_tesseract(tmp_path, monkeypatch)
    _tessdata(tmp_path, monkeypatch, "eng")
    image = _image(tmp_path)

    ocr_mod.run({"path": str(image), "languages": ["eng; rm -rf ~", "-l"]})

    argv = _argv(argv_log)
    assert argv[3] == "eng"
    assert "rm" not in " ".join(argv)


def test_missing_tesseract_is_refused_rather_than_raised(tmp_path, monkeypatch, ocr_enabled):
    from shani_chronoa.senses import ocr as ocr_mod

    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(ocr_mod, "FALLBACK_TESSDATA_DIR", str(tmp_path / "no-tessdata"))

    result = ocr_mod.run({"path": str(_image(tmp_path))})

    assert isinstance(result, str)
    assert "tesseract is not installed" in result
    assert "tesseract-data-eng" in result  # names the Arch package


def test_a_binary_without_language_data_is_not_available(tmp_path, monkeypatch):
    """tesseract with no tessdata is present-but-broken; say so up front."""
    from shani_chronoa.senses import ocr as ocr_mod

    _fake_tesseract(tmp_path, monkeypatch)
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path / "empty-prefix"))
    monkeypatch.setattr(ocr_mod, "FALLBACK_TESSDATA_DIR", str(tmp_path / "no-tessdata"))

    assert ocr_mod.resolve_tesseract() is not None
    assert ocr_mod.find_tessdata_dir() is None
    assert ocr_mod.is_available() is False


def test_a_nonzero_exit_is_reported_with_tesseracts_own_message(
    tmp_path, monkeypatch, ocr_enabled
):
    from shani_chronoa.senses import ocr as ocr_mod

    _fake_tesseract(tmp_path, monkeypatch, body='echo "Error in pixRead: empty image" >&2\nexit 2')
    _tessdata(tmp_path, monkeypatch, "eng")

    result = ocr_mod.run({"path": str(_image(tmp_path))})

    assert isinstance(result, str)
    assert "Error in pixRead" in result


def test_a_hanging_tesseract_is_stopped_at_the_timeout(tmp_path, monkeypatch, ocr_enabled):
    from shani_chronoa.senses import ocr as ocr_mod

    _fake_tesseract(tmp_path, monkeypatch, body="sleep 30")
    _tessdata(tmp_path, monkeypatch, "eng")
    image = _image(tmp_path)

    request = ocr_mod.OcrRequest(image_path=str(image), timeout_seconds=0.3)
    with pytest.raises(ocr_mod.OcrError, match="longer than"):
        ocr_mod.run_tesseract(request, ocr_mod.resolve_tesseract())


def test_consent_is_required_before_anything_runs(tmp_path, monkeypatch, chronoa_config):
    """The gsetting defaults to false, so an out-of-the-box install refuses."""
    from shani_chronoa.senses import ocr as ocr_mod

    assert chronoa_config.get_bool("ocr-sense-enabled", False) is False
    argv_log = _fake_tesseract(tmp_path, monkeypatch)
    _tessdata(tmp_path, monkeypatch, "eng")

    result = ocr_mod.run({"path": str(_image(tmp_path))})

    assert isinstance(result, str)
    assert "ocr-sense-enabled" in result
    assert not argv_log.exists()  # the binary was never invoked


def test_the_loader_registers_the_sense(gsettings_env):
    from shani_chronoa.senses import _sense_problem, discover_senses

    senses = discover_senses()
    assert "ocr" in senses
    assert _sense_problem(senses["ocr"]) == ""
    assert senses["ocr"].is_ambient() is False
