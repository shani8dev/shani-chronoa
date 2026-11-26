"""Setup engine ops: delegation, the confirm gate, and stable outcomes."""

from pathlib import Path

import pytest

from shani_chronoa.engine_ops import setup
from shani_chronoa.ui_gtk import setup_wizard


@pytest.fixture(autouse=True)
def _throwaway_xdg(tmp_path, monkeypatch):
    data = tmp_path / "xdg-data"
    config = tmp_path / "xdg-config"
    data.mkdir()
    config.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))


@pytest.fixture
def cfg():
    return object()


def _spy(seen, attr, result="wizard said yes"):
    def fake(*args, **kwargs):
        seen.append((attr, args, kwargs))
        return result

    return fake


def test_state_bundles_the_wizard_data_and_uses_one_config(monkeypatch, cfg):
    seen = []
    monkeypatch.setattr(
        setup_wizard, "state", _spy(seen, "state", {"brain": {"ready": True}})
    )
    monkeypatch.setattr(setup_wizard, "needs_setup", _spy(seen, "needs_setup", True))
    catalogue = [("base", "Whisper", "150 MB", "whisper-cli")]
    monkeypatch.setattr(
        setup_wizard,
        "stt_catalogue",
        lambda: seen.append(("stt_catalogue", (), {})) or catalogue,
    )

    out = setup.state(cfg)

    assert out == {
        "state": {"brain": {"ready": True}},
        "needs_setup": True,
        "stt_catalogue": catalogue,
    }
    assert [name for name, _, _ in seen] == ["state", "needs_setup", "stt_catalogue"]
    assert seen[0][1] == (cfg,)
    assert seen[1][1] == (cfg,)


@pytest.mark.parametrize(
    "name,value",
    [
        ("brain", "qwen3:4b"),
        ("ears", "base"),
        ("voice", "en_US-lessac-medium"),
        ("eyes", "vision-model"),
        ("memory", ""),
        ("languages", "hi"),
        ("kokoro", "af_heart"),
    ],
)
def test_every_download_step_refuses_without_confirm(monkeypatch, cfg, name, value):
    seen = []
    for attr in (
        "setup_brain",
        "setup_ears",
        "setup_voice",
        "setup_eyes",
        "setup_memory",
        "setup_languages",
        "setup_kokoro",
    ):
        monkeypatch.setattr(setup_wizard, attr, _spy(seen, attr))

    out = setup.step(name, value, config=cfg)

    assert "error" in out
    assert "confirmation required" in out["error"]
    assert seen == []


@pytest.mark.parametrize(
    "name,value,attr,args",
    [
        ("brain", "qwen3:4b", "setup_brain", ("qwen3:4b",)),
        ("ears", "base", "setup_ears", ("base",)),
        ("voice", "en_US-lessac-medium", "setup_voice", ("en_US-lessac-medium",)),
        ("eyes", "vision-model", "setup_eyes", ("vision-model",)),
        ("memory", "", "setup_memory", ()),
        ("kokoro", "af_heart", "setup_kokoro", ("af_heart",)),
    ],
)
def test_step_delegates_to_the_matching_wizard_function(
    monkeypatch, cfg, name, value, attr, args
):
    seen = []
    report = lambda *_: None
    monkeypatch.setattr(setup_wizard, attr, _spy(seen, attr))

    out = setup.step(name, value, confirm=True, config=cfg, report=report)

    assert out == {"step": name, "result": "done", "message": "wizard said yes"}
    assert seen == [(attr, args, {"report": report, "config": cfg})]


def test_languages_splits_codes_and_the_listen_token(monkeypatch, cfg):
    seen = []
    monkeypatch.setattr(setup_wizard, "setup_languages", _spy(seen, "setup_languages"))

    out = setup.step("languages", " hi, en , listen ", confirm=True, config=cfg)

    assert out["result"] == "done"
    assert seen[0][1] == (["hi", "en"], True)


def test_languages_without_listen_keeps_listening_off(monkeypatch, cfg):
    seen = []
    monkeypatch.setattr(setup_wizard, "setup_languages", _spy(seen, "setup_languages"))

    setup.step("languages", "hi,en", confirm=True, config=cfg)

    assert seen[0][1] == (["hi", "en"], False)


def test_an_unfinished_wizard_answer_is_not_reported_as_done(monkeypatch, cfg):
    monkeypatch.setattr(
        setup_wizard,
        "setup_brain",
        _spy([], "setup_brain", "llama-server is not installed. Install it and retry."),
    )

    out = setup.step("brain", "qwen3:4b", confirm=True, config=cfg)

    assert out["result"] == "blocked"
    assert out["message"] == "llama-server is not installed. Install it and retry."


def test_unknown_step_and_missing_value_are_errors(monkeypatch, cfg):
    seen = []
    monkeypatch.setattr(setup_wizard, "setup_brain", _spy(seen, "setup_brain"))

    unknown = setup.step("nope", confirm=True, config=cfg)
    missing = setup.step("brain", "", confirm=True, config=cfg)

    assert "choose one of" in unknown["error"]
    assert "needs a value" in missing["error"]
    assert seen == []


def test_a_wizard_exception_becomes_an_error(monkeypatch, cfg):
    def boom(*args, **kwargs):
        raise RuntimeError("the model file was corrupt")

    monkeypatch.setattr(setup_wizard, "setup_memory", boom)

    out = setup.step("memory", confirm=True, config=cfg)

    assert out["error"] == "memory could not finish: the model file was corrupt"


def test_the_three_new_engine_ops_modules_import_no_gui_toolkit():
    base = Path(setup.__file__).parent
    for name in ("setup.py", "conversations.py", "memory.py"):
        text = (base / name).read_text(encoding="utf-8")
        assert "gi.repository" not in text
        assert "from gi" not in text
        assert "import Gtk" not in text
        assert "import Adw" not in text
        assert "import Gdk" not in text
