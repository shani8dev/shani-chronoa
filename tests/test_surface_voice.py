"""The voice surface as a built widget, branched on whether libadwaita is there.

**`common.adw_ready()` decides the branch, and both branches must pass.** The
helper returns False on a headless builder with no libadwaita, and this panel's
whole reason for existing next to `model.py` is that `common.py` gives every
helper a plain-GTK answer. So nothing here names `Adw`, nothing here assumes a
label class, and the assertions read the panel through its forwarded accessors
plus the label text found by walking the tree - which works for
`Adw.NavigationPage` and for a plain `Gtk.Box` alike.

What is pinned, and why each is one a reader cannot get right from the diff:

- **the module contract** - `TITLE`, `ICON`, `SECTION`, `build`; that the
  registry's own `SURFACE_IDS` names `"voice"` exactly once (read as source
  with `ast`, because `all_surfaces()` imports every sibling panel and would
  report another module's problem as this one's); that `SECTION` is one
  `SECTION_ORDER` knows, or `sections()` files the panel under "Everything else";
  and that the icon name is one the icon theme actually ships, which a widget
  that constructed says nothing about.

- **the current-engine row names an engine** - and it names the one the real
  chain in `tts.py` picks, checked by asking that chain rather than by trusting
  the row. The fixture makes the answer deterministic on any machine: no
  `piper-tts` on PATH, no RHVoice, and `espeak-ng` resolvable, so the floor is
  the only possible answer.

- **an engine probe that raises says "cannot be determined", and names no
  engine.** This is the point of the panel. `PiperTTS.engine()` is the answer
  every reply goes through, and a page that turned a failed probe into "the
  floor" would describe a voice nobody is speaking with - and send someone to
  install a package whose absence was never established. Asserted from both
  sides: the words are there, *and* none of the engine names in
  anywhere in the row's title or detail.

- **the sox-skipped reason appears when sox is absent** - `sox` is an optdepend
  and the machine without it is the ordinary case, so a timbre row that quietly
  described a pitch shift that never happened would be the expensive kind of
  wrong. The reason comes from `PiperTTS.timbre_problem()`, the function
  `apply_timbre` itself branches on, and the row also has to say the reply still
  speaks: `AGENTS.md` records that a user's speech must never stop over a
  preference.

- **markup in a voice name is escaped** - a voice id comes from a gsetting, and
  `<b>` and a bare `&` are ordinary content in one. `Adw.ActionRow` renders both
  title and subtitle through Pango markup and a string the parser refuses leaves
  the label *empty* (measured on libadwaita 1.5), so the assertion is on the
  rendered label text being exactly the name that was read, and on the label's
  markup being the escaped form rather than the name.

Plus one negative control at the bottom: a voice genuinely installed on disk,
made to report itself absent, with the assertion meant to catch that shown to
fail while the files are still there, and the state restored afterwards.

Everything this file touches is isolated by the autouse fixtures:
`XDG_DATA_HOME` (where `voices.voice_dir()`, `sherpa.install_dir()` and the
whisper model search resolve, per call), `HOME`, `XDG_CONFIG_HOME` and the
compiled-schema GSettings backend. The `config` on the stub app is a stub, so no
real `ChronoaConfig` and no real dconf is involved.
"""

from __future__ import annotations

import ast
import os
import pathlib
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

Gtk.init()

from shani_chronoa import cloud_voice, sherpa, stt, tts, voices  # noqa: E402
from shani_chronoa.gui.surfaces import common, voice as surface  # noqa: E402

REGISTRY = pathlib.Path(surface.__file__).with_name("__init__.py")

#: The real constructor, captured at import. `floor` patches
#: `WhisperSTT.__init__` and `_pin_stt` still needs the original - a closure over
#: a name defined inside the fixture would not be in `_pin_stt`'s scope, and the
#: `NameError` would be swallowed by `_speech_input`'s `except`, turning a test
#: wiring mistake into a silent "could not determine".
_REAL_STT_INIT = stt.WhisperSTT.__init__

#: The names `tts.PiperTTS._engine` can return. Asserted absent from a row that
#: could not be established, so they are a set rather than prose.
#:
#: **It was four and is now five.** `_engine` grew a `cloud` link on 2026-10-06
#: (`cloud_voice.CloudTTS`), and this list - used to prove a failed probe names
#: *no* engine - would have kept passing while omitting the fifth, because a
#: "cannot be determined" row mentioning no engine still mentions none of these.
#: An omission in the guard is invisible to the guard.
ENGINE_NAMES = ("kokoro", "piper", "rhvoice", "espeak-ng", "cloud")


class _StubConfig:
    """A `ChronoaConfig` reduced to the five things this panel reads.

    `piper_voice`, `whisper_model`, `stt_backend` and `language` are properties
    on the real class and plain attributes here; the surface accepts either, and
    `get_bool`/`get_double` cover the timbre keys and the Kokoro switch.
    """

    def __init__(self, piper_voice="en_US-lessac-medium", whisper_model="", stt_backend="whisper",
                 language="en", **values):
        self.piper_voice = piper_voice
        self.whisper_model = whisper_model
        self.stt_backend = stt_backend
        self.language = language
        self.values = {"kokoro-tts-enabled": False, **values}

    def get(self, key, default=""):
        return self.values.get(key, default)

    def get_bool(self, key, default=False):
        return bool(self.values.get(key, default))

    def get_double(self, key, default=0.0):
        return float(self.values.get(key, default))


class _StubApp:
    """The least an app can be: a config, and no live dispatcher or STT.

    `tts` and `stt` are `None` rather than absent so the panel takes the same
    branch a real app takes before `_init_components` has run, and `hardware` is
    absent so the speech-input model resolution is the documented `base`
    fallback rather than a probe of this machine.
    """

    def __init__(self, config=None):
        self.config = config if config is not None else _StubConfig()
        self.tts = None
        self.stt = None


def _pin_stt(engine, model: str, binary: str, language: str) -> None:
    """Build a real `WhisperSTT`, then point only its binary at a missing path.

    Everything else - the model name, `model_path`, `use_server` - comes from the
    real constructor, so the panel is described by the same object it would be in
    the app and only the one host fact this file cannot control is pinned. The
    model path stays inside the test's own data home, which the autouse
    `XDG_DATA_HOME` fixture makes empty, so "the model file is not on disk" is
    true here for the reason it is true on a fresh machine rather than by
    accident.
    """
    _REAL_STT_INIT(engine, model=model, whisper_path=binary, language=language)
    # Belt and braces: the constructor honours `whisper_path`, and this makes the
    # pin visible to anything that re-reads the attribute.
    engine.whisper_path = binary


@pytest.fixture
def floor(monkeypatch):
    """An engine chain with only the floor in it: espeak-ng, and nothing above.

    `shutil.which` is patched rather than relied on, because the answer this
    file needs is "espeak-ng is reachable and nothing better is", which is true
    on this machine by accident and would stop being true on one that had Piper
    installed. Patching `tts.shutil.which` patches the attribute on the `shutil`
    module itself, so the panel's own `which` calls see it too - the one
    arrangement in which the panel and the chain cannot disagree.

    **The speech-input binary is pinned too, and that was the missing half.**
    `WhisperSTT` falls back to the literal `/usr/bin/whisper-cli` when `which`
    finds nothing, and the panel then asks `os.path.exists` about that path - so
    the engine row said *"run by /usr/bin/whisper-cli"* on this machine and
    *"whisper.cpp is not installed"* on a machine without it. The test asserted
    the second, so it was asserting a fact about the host: it passed on a bare
    container and failed on a developer machine that had whisper-cpp installed
    (which is where it was found). `_stt_for` is pointed at a path inside the
    test's own data home, which cannot exist, so the "no program there" branch is
    the one taken everywhere.
    """
    absent = str(pathlib.Path(os.devnull).parent
                 / "no-such-whisper-cli-in-this-test")
    monkeypatch.setattr(stt.WhisperSTT, "__init__",
                        lambda self, model="base", whisper_path=None, language="en":
                        _pin_stt(self, model, absent, language))
    monkeypatch.setattr(tts.shutil, "which",
                        lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)
    monkeypatch.setattr(tts.PiperTTS, "_rhvoice_has_voice", staticmethod(lambda: False))
    monkeypatch.setattr(voices, "piper_binary", lambda: "")
    return _StubApp()


def _row_title(row):
    return row._row_title.get_text()


def _row_detail(row):
    return row._row_detail.get_text()


def _squash(text):
    """A label's rendered text with Pango's wrap breaks removed.

    A `Gtk.Label` that has never been allocated wraps at every legal break
    point - measured here, one character at a time - so its `get_text()` is the
    string it was given with newlines and nothing else. Dropping all whitespace
    from both sides of a comparison removes exactly those inserted breaks and
    nothing a label was set with, so `"espeak-ng"` still matches.
    """
    return "".join(str(text).split())


def _joined(widget):
    """Every label's rendered text, in tree order, with wrap breaks removed.

    GTK4 dropped `Gtk.Container`, so `get_first_child()` is the only way down,
    and an `Adw.NavigationPage` is not even a container in the old sense. This is
    what a person reads on the page, whatever it was built from.
    """
    out = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            out.append(_squash(child.get_text()))
        part = _joined(child)
        if part:
            out.append(part)
        child = child.get_next_sibling()
    return " ".join(out)


def _labels(widget):
    """(rendered text, markup) for every label, in tree order.

    The markup is unwrapped - `get_label()` is the string the label was set with
    - so it is what has to be compared against an escaped value, while the
    rendered text is what has to compare equal to the name that was read.
    """
    out = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            out.append((_squash(child.get_text()), child.get_label()))
        out.extend(_labels(child))
        child = child.get_next_sibling()
    return out


# -- the module contract ----------------------------------------------------


class TestModuleContract:
    def test_it_exports_title_icon_section_and_build(self):
        assert isinstance(surface.TITLE, str) and surface.TITLE
        assert isinstance(surface.ICON, str) and surface.ICON
        assert isinstance(surface.SECTION, str) and surface.SECTION
        assert callable(surface.build)

    def test_it_is_the_module_the_registry_resolves(self):
        """`surfaces/__init__.py` imports `shani_chronoa.gui.surfaces.<name>` for
        each name in `SURFACE_IDS`; a module that does not answer to that name is
        a hole in the sidebar only a lookup notices."""
        assert sys.modules["shani_chronoa.gui.surfaces.voice"] is surface
        assert surface.__name__ == "shani_chronoa.gui.surfaces.voice"

    def test_the_registry_names_this_surface_exactly_once(self):
        """Read as source, not through `all_surfaces()`: that imports every
        sibling panel, so a failure there is not this panel's - and
        `all_surfaces()` builds a dict, so a name listed twice is invisible there
        and shows up in the sidebar as two entries for one panel."""
        tree = ast.parse(REGISTRY.read_text(encoding="utf-8"))
        ids = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple)
                    and any(isinstance(t, ast.Name) and t.id == "SURFACE_IDS" for t in node.targets)):
                ids = [e.value for e in node.value.elts]
        assert ids is not None, "SURFACE_IDS is gone from the registry"
        assert ids.count("voice") == 1, f"SURFACE_IDS names 'voice' {ids.count('voice')} times: {ids}"
        assert isinstance(ids, list) and len(ids) == len(set(ids)), "SURFACE_IDS has a duplicate"

    def test_its_section_is_one_the_sidebar_knows(self):
        """`sections()` files a panel by the module's own `SECTION`; a value
        outside `SECTION_ORDER` puts it in "Everything else" instead."""
        from shani_chronoa.gui import surfaces

        assert surface.SECTION in surfaces.SECTION_ORDER
        assert surfaces.sections().get("voice") == surface.SECTION

    def test_the_icon_name_is_one_the_theme_actually_has(self):
        """An icon name nothing ships renders as a blank space in the sidebar,
        and a widget that constructed says nothing about that. Neither a missing
        display nor a missing icon directory is a skip: a check that looked at
        nothing is worse than no check."""
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        if display is not None:
            assert Gtk.IconTheme.get_for_display(display).has_icon(surface.ICON), (
                f"{surface.ICON} is not in the icon theme this display uses")
            return
        roots = [pathlib.Path(p) for p in
                 (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")]
        found = [p for root in roots for p in root.glob(f"icons/*/*/{surface.ICON}.*")]
        assert found, f"no display and no installed icon theme carries {surface.ICON}"


# -- build ------------------------------------------------------------------


class TestBuild:
    def test_it_builds_a_widget_for_a_stub_app(self, floor):
        widget = surface.build(floor)
        assert isinstance(widget, Gtk.Widget)

    def test_it_builds_without_a_config_at_all(self, monkeypatch):
        """A missing settings object is a real state, not a crash.

        The engine chain reads the filesystem and PATH, not settings, so the page
        still has an answer here - and saying so is the point: a panel that
        reported "unknown" for everything would be no more honest than one that
        invents an engine, and the two are separated by whether the *engine*
        probe succeeded.
        """
        monkeypatch.setattr(tts.shutil, "which",
                            lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)
        monkeypatch.setattr(tts.PiperTTS, "_rhvoice_has_voice", staticmethod(lambda: False))
        monkeypatch.setattr(voices, "piper_binary", lambda: "")
        app = _StubApp()
        del app.config
        widget = surface.build(app)
        assert isinstance(widget, Gtk.Widget)
        assert widget.speaking_title() == "speaking with espeak-ng", widget.speaking_title()
        states = [r._engine_state for r in widget.engine_rows()]
        # **Exactly one engine is present, and it is the floor.** Stated as a
        # property rather than as a list, because the list was the thing that had
        # to be edited twice today: once when `cloud` was added to the chain, and
        # again because the cloud engine sits *after* espeak-ng and is False, so
        # "all False then a True" was never going to be the right shape.
        present = [key for key, state in zip(surface.CHAIN, states) if state]
        assert present == ["espeak-ng"], (
            f"the chain is {surface.CHAIN}, the states were {states}, so the "
            f"engines in use read as {present} - expected only the floor, since "
            "kokoro, piper and rhvoice are all stubbed absent and the cloud "
            "engine is opt-in")
        # Kokoro's row names the half that is missing, and asks the switch only
        # after that - `tts.py:_kokoro_reason` checks in the same order, because
        # the first answer is the one a person can act on.
        kokoro = [r for r in widget.engine_rows() if r._engine_key == "kokoro"][0]
        assert "sherpa-onnx program that runs Kokoro is not installed" in _row_detail(kokoro), \
            _row_detail(kokoro)

    def test_every_row_has_a_tooltip_naming_its_source(self, floor):
        widget = surface.build(floor)
        for row in (widget.engine_rows() + widget.speech_rows() + widget.catalogue_rows()):
            assert row.get_tooltip_text(), row.get_style_class()
        for row in (widget.engine_rows() + widget.speech_rows()):
            assert "Read from" in row.get_tooltip_text(), row.get_tooltip_text()

    def test_the_reload_button_is_labelled_for_a_screen_reader(self, floor):
        button = surface.build(floor).reload_button()
        assert isinstance(button, Gtk.Button)
        assert "again" in button.get_tooltip_text()

    def test_reload_rereads_the_disk_without_accumulating_rows(self, monkeypatch):
        """The sidebar builds a panel once and keeps it, so every reading here is
        a claim about the moment it was asked. Reload has to make it true again,
        and must not leave a second copy of anything behind."""
        monkeypatch.setattr(tts.shutil, "which",
                            lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)
        monkeypatch.setattr(tts.PiperTTS, "_rhvoice_has_voice", staticmethod(lambda: False))
        monkeypatch.setattr(voices, "piper_binary", lambda: "")
        app = _StubApp()
        widget = surface.build(app)
        before = (len(widget.engine_rows()), len(widget.speech_rows()), len(widget.catalogue_rows()))
        widget.reload_button().emit("clicked")
        widget.reload_button().emit("clicked")
        after = (len(widget.engine_rows()), len(widget.speech_rows()), len(widget.catalogue_rows()))
        assert after == before, f"rows accumulated across reloads: {before} -> {after}"
        assert "speaking with" in widget.speaking_title(), widget.speaking_title()


# -- the engine that would speak --------------------------------------------


class TestTheCurrentEngine:
    def test_the_row_names_the_engine_the_real_chain_picks(self, floor):
        """Asserted against `tts.PiperTTS.engine()` itself, not against a literal:
        a page that agreed with itself rather than with the chain is the failure
        this check exists to catch."""
        dispatcher = surface._VoiceSurface(floor)._dispatcher(floor, floor.config)
        expected = dispatcher.engine()
        assert expected == "espeak-ng", (
            f"the fixture was meant to leave only the floor in the chain, and the chain says {expected!r}; "
            "this test then proves nothing about which engine the row names"
        )
        widget = surface.build(floor)
        assert widget.speaking_title() == "speaking with espeak-ng", widget.speaking_title()
        # And on the page itself: the title is rendered as words, so this is read
        # off the labels rather than off the accessor.
        assert "espeak-ng" in _joined(widget), _joined(widget)[:400]

    def test_it_says_why_the_better_engines_are_not_in_use(self, floor):
        detail = surface.build(floor).speaking_detail()
        assert "Kokoro" in detail and "Piper" in detail, detail
        assert "not in use" in detail, detail

    def test_a_probe_that_raises_says_cannot_be_determined_and_names_no_engine(self, floor,
                                                                                monkeypatch):
        """The point of the panel. A failed probe is not a negative answer, and a
        page that named the floor here would describe a voice nobody speaks with
        - then send somebody to install a package whose absence was never
        established. Both halves are asserted: the words, and the absence of every
        engine name in `ENGINE_NAMES`."""
        def _explode(*_a, **_k):
            raise OSError("the settings store went away")

        monkeypatch.setattr(tts.PiperTTS, "engine", _explode)
        widget = surface.build(floor)
        title, detail = widget.speaking_title(), widget.speaking_detail()
        assert "cannot be determined" in title, title
        assert "cannot be determined" in detail, detail
        assert "the settings store went away" in detail, detail
        said = f"{title} {detail}".lower()
        for name in ENGINE_NAMES:
            assert name not in said, f"the row named {name!r} after the probe failed: {said}"

    def test_a_probe_that_returns_nothing_says_no_engine_not_an_unknown(self, floor, monkeypatch):
        """`engine()` returns None when nothing can speak, which is an answer and
        not a failure: collapsing it into "cannot be determined" would be as wrong
        as naming one."""
        monkeypatch.setattr(tts.PiperTTS, "engine", lambda *a, **k: None)
        widget = surface.build(floor)
        assert widget.speaking_title() == "no engine at all", widget.speaking_title()
        assert "not spoken aloud" in widget.speaking_detail(), widget.speaking_detail()

    def test_every_engine_has_a_row_in_the_chain_order(self, floor):
        rows = surface.build(floor).engine_rows()
        assert [r._engine_key for r in rows] == list(surface.CHAIN), [r._engine_key for r in rows]

    def test_the_floor_is_shown_as_a_dependency_not_a_preference(self, floor):
        row = [r for r in surface.build(floor).engine_rows() if r._engine_key == "espeak-ng"][0]
        assert row._engine_state is True
        assert "hard dependency" in _row_detail(row), _row_detail(row)

    def test_a_probe_that_raises_is_unknown_not_absent(self, floor, monkeypatch):
        """Same rule as the current-engine row, per engine: one unreadable engine
        does not make the others unreadable, and it does not become 'not
        installed'."""
        monkeypatch.setattr(tts.shutil, "which",
                            lambda name: (_ for _ in ()).throw(OSError("EIO")) if name == "RHVoice-test"
                            else "/usr/bin/espeak-ng")
        widget = surface.build(floor)
        assert widget.unknown_engine_keys() == ["rhvoice"], widget.unknown_engine_keys()
        row = [r for r in widget.engine_rows() if r._engine_key == "rhvoice"][0]
        assert "unknown" in _row_title(row), _row_title(row)
        assert "EIO" in _row_detail(row), _row_detail(row)
        assert len(widget.engine_rows()) == len(surface.CHAIN) == 5, (
            f"the chain is {surface.CHAIN}, so the panel must render a row for each; "
            f"it rendered {len(widget.engine_rows())}")


# -- the voice and the timbre pass ------------------------------------------


class TestTheVoice:
    def test_it_names_the_configured_piper_voice_and_whether_its_file_is_there(self, floor):
        row = surface.build(floor).voice_row()
        assert row._voice_name.get_text() == "en_US-lessac-medium"
        assert "Lessac" in _row_detail(row), _row_detail(row)
        assert row._voice_on_disk is False, "nothing is on disk in an isolated data home"
        assert "not downloaded" in _row_detail(row), _row_detail(row)

    def test_it_says_a_voice_for_another_engine_is_not_what_is_audible(self, floor):
        """With the floor speaking, the configured Piper voice is named but must
        not be presented as the voice being heard."""
        detail = surface.build(floor).voice_row()._row_detail.get_text()
        assert "Piper is not the engine right now" in detail, detail

    def test_the_sox_skipped_reason_appears_when_sox_is_absent(self, floor, monkeypatch):
        """The ordinary case on a machine without the optdepend, and the one the
        panel has to get right: a row describing a pitch shift that never
        happened is the expensive kind of wrong. The reason is the one
        `apply_timbre` itself would return."""
        monkeypatch.setattr(tts.PiperTTS, "sox_path", staticmethod(lambda: ""))
        floor.config.values["tts-pitch"] = 3.0
        dispatcher = tts.PiperTTS(config=floor.config)
        expected = dispatcher.timbre_problem()
        assert expected, "this test is about a transform that cannot run; with no problem it proves nothing"
        row = surface.build(floor).timbre_row()
        title, detail = _row_title(row), _row_detail(row)
        assert "NOT applied" in title, title
        assert "pitch +3 semitones" in detail, detail
        assert "sox" in detail and "pacman" in detail, detail
        assert "The reply still speaks" in detail, (
            "a skipped preference must not read as a lost reply - that is how three of them would "
            f"degrade speech: {detail}"
        )

    def test_with_sox_present_a_configured_transform_is_shown_applied(self, floor, monkeypatch):
        monkeypatch.setattr(tts.PiperTTS, "sox_path", staticmethod(lambda: "/usr/bin/sox"))
        floor.config.values["tts-tempo"] = 1.3
        detail = surface.build(floor).timbre_row()._row_detail.get_text()
        assert "tempo 1.3x" in detail and "tempo 1.300" in detail, detail
        assert "NOT applied" not in detail, detail

    def test_the_identity_settings_are_a_third_state_not_a_skip(self, floor, monkeypatch):
        """No transform set means SoX is never started. Reporting that as either
        "applied" or "skipped" would be wrong in both directions."""
        monkeypatch.setattr(tts.PiperTTS, "sox_path", staticmethod(lambda: ""))
        detail = surface.build(floor).timbre_row()._row_detail.get_text()
        assert "exactly as the engine wrote it" in detail, detail
        assert "NOT applied" not in detail, detail


# -- speech input -----------------------------------------------------------


class TestSpeechInput:
    def test_it_names_the_backend_and_the_model(self, floor):
        engine_row = [r for r in surface.build(floor).speech_rows() if r._speech_kind == "engine"][0]
        assert engine_row._speech_backend == "whisper"
        assert engine_row._speech_model == "base", engine_row._speech_model
        assert "Whisper.cpp" in _row_title(engine_row), _row_title(engine_row)

    def test_the_model_files_own_state_is_asked_separately_from_the_binary(self, floor):
        """`is_available()` is one boolean over two causes. The row that says the
        model file is missing when it is the *program* that is missing would send
        somebody to download a model they already have."""
        rows = {r._speech_kind: r for r in surface.build(floor).speech_rows()}
        assert set(rows) == {"engine", "model-file"}, sorted(rows)
        engine, model_file = rows["engine"], rows["model-file"]
        assert engine._speech_state is False
        assert "whisper.cpp is not installed" in _row_detail(engine), _row_detail(engine)
        assert model_file._speech_model_on_disk is False, (
            "no ggml model file exists in this test's data home, so the model-file row must not claim "
            "one is there"
        )
        assert "not on disk" in _row_title(model_file), _row_title(model_file)
        assert "ggml-base.bin" in _row_detail(model_file), _row_detail(model_file)

    def test_a_model_file_on_disk_is_reported_as_present(self, floor):
        """A real file at the real path `WhisperSTT._get_model_path` searches, so
        the row comes from the same on-disk state the engine itself reads."""
        directories = (voices.voice_dir(), voices.voice_dir().parents[1] / "whisper" / "models")
        model = directories[1] / "ggml-base.bin"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"gguf")
        row = [r for r in surface.build(floor).speech_rows() if r._speech_kind == "model-file"][0]
        assert row._speech_model_on_disk is True
        assert "on disk" in _row_title(row) and "NOT" not in _row_title(row), _row_title(row)

    def test_the_parakeet_backend_is_named_from_the_setting(self, floor):
        floor.config.stt_backend = "parakeet"
        row = [r for r in surface.build(floor).speech_rows() if r._speech_kind == "engine"][0]
        assert row._speech_backend == "parakeet"
        assert "Parakeet" in _row_title(row), _row_title(row)


# -- the catalogue and the cost ---------------------------------------------


class TestEverythingElse:
    def test_every_voice_this_build_knows_is_listed(self, floor):
        rows = surface.build(floor).catalogue_rows()
        assert len(rows) == len(voices.VOICES) + len(voices.KOKORO_VOICES), len(rows)
        assert {r._voice_key for r in rows} == set(voices.VOICES) | set(voices.KOKORO_VOICES)

    def test_the_catalogue_says_what_downloading_one_would_cost(self, floor):
        detail = " ".join(_row_detail(r) for r in surface.build(floor).catalogue_rows())
        assert "MB" in detail, detail
        assert f"{voices.VOICES['en_US-lessac-medium'].onnx_size / 1e6:.0f} MB" in detail, detail

    def test_the_cost_row_states_the_one_measured_rtf_and_says_so(self, floor):
        detail = surface.build(floor).cost_row()._row_detail.get_text()
        assert "real-time factor of about 1.2" in detail, detail
        assert "opt-in" in detail, detail
        # And nothing is claimed for the engines nobody timed.
        assert "not measured in this repository" in detail, detail

    def test_the_cost_of_the_engine_in_use_is_the_one_quoted(self, floor):
        detail = surface.build(floor).cost_row()._row_detail.get_text()
        assert detail.startswith("The engine in use, espeak-ng,"), detail
        assert "about 0.005" in detail, detail


# -- markup -----------------------------------------------------------------


class TestMarkup:
    def test_markup_in_a_voice_name_is_escaped_not_rendered(self, floor):
        """A voice id comes from a gsetting, so `<b>` and a bare `&` are ordinary
        content in one. With libadwaita the row is an `Adw.ActionRow`, which sets
        title *and* subtitle through Pango markup and refuses a string the parser
        cannot read - leaving the label *empty* (measured on libadwaita 1.5). So
        both halves are asserted: the rendered text is exactly the name that was
        read, and the label's markup is the escaped form rather than the name."""
        hostile = '<b>x</b> & "y"'
        floor.config.piper_voice = hostile
        widget = surface.build(floor)
        rendered = [text for text, _markup in _labels(widget)]
        assert any(_squash(hostile) in text for text in rendered), (
            f"the voice name was not rendered as words; the labels were {rendered}")
        labels = [markup for text, markup in _labels(widget) if _squash(hostile) in text]
        assert labels, "no label carries the name at all"
        for markup in labels:
            assert markup != hostile, "the label holds the raw name as markup, which is a tag waiting to parse"
            assert "&lt;b&gt;x&lt;/b&gt;" in markup, markup
            assert "&amp;" in markup and "&amp;amp;" not in markup, markup
        # The row's own accessor reports what was displayed, and the row says so.
        detail = widget.voice_row()._voice_detail.get_text()
        assert "not downloaded" in detail, detail

    def test_the_plain_gtk_branch_shows_the_name_too(self, floor, monkeypatch):
        """`common.adw_ready()` False is the other half of this file: the same
        page has to build without libadwaita, and the name must still be on it.

        In this branch `common.row()` builds `Gtk.Label(label=...)`, which takes
        no markup, so the string must *not* be escaped - escaping would put
        `&lt;b&gt;` on screen. Both arrangements were measured on this machine; a
        panel that only ever handled one of them would show an entity or an empty
        row on the other.
        """
        monkeypatch.setattr(common, "adw_ready", lambda: False)
        hostile = '<b>x</b> & "y"'
        floor.config.piper_voice = hostile
        widget = surface.build(floor)
        assert isinstance(widget, Gtk.Widget)
        assert _squash(hostile) in _joined(widget), _joined(widget)[:400]
        assert _squash(hostile) in _squash(widget.voice_row()._voice_detail.get_text())
        assert "&lt;" not in widget.voice_row()._voice_detail.get_text(), (
            "the plain-GTK label takes no markup, so an escaped name would be shown as an entity")


# -- read-only --------------------------------------------------------------


class TestReadOnly:
    def test_building_writes_nothing_to_the_data_home(self, floor, tmp_path):
        before = sorted(str(p) for p in tmp_path.rglob("*"))
        surface.build(floor)
        assert sorted(str(p) for p in tmp_path.rglob("*")) == before

    def test_it_downloads_and_installs_nothing(self, floor, monkeypatch):
        for module, name in ((voices, "install_kokoro"), (voices, "install_piper"),
                             (voices, "install_voice"), (sherpa, "install"),
                             (sherpa, "install_archive")):
            monkeypatch.setattr(module, name,
                                (lambda m, n: lambda *a, **k: pytest.fail(f"{m}.{n} was called"))(
                                    module.__name__, name),
                                raising=False)
        surface.build(floor)

    def test_it_never_synthesises_anything(self, floor, monkeypatch):
        """A panel that produced a WAV to describe the voice would be an
        actuator, not a read-only page - and it would be audible."""
        monkeypatch.setattr(tts.PiperTTS, "synthesize",
                            lambda *a, **k: pytest.fail("synthesize() was called"))
        monkeypatch.setattr(tts.PiperTTS, "synthesize_to_bytes",
                            lambda *a, **k: pytest.fail("synthesize_to_bytes() was called"))
        surface.build(floor)


# -- the negative control ---------------------------------------------------


def test_a_voice_present_on_disk_that_reports_absent_breaks_the_catalogue_row(
        floor, tmp_path_factory):
    """A voice genuinely installed, made to report itself absent.

    A check that cannot come out wrong proves nothing, so this writes the two
    files `voices.voice_installed` requires at the real path it reads, confirms
    the catalogue says the voice is downloaded, then patches the one function
    that row reads - `voices.voice_installed`, where it is read - to report
    nothing. It asserts the patch took effect *while the files are still on disk*
    (so the failure is the rendering's, not an emptied disk's), then asserts the
    assertion meant to catch that fails. Then it restores and confirms the row is
    back: an unrestored control is a change left behind for the next reader.
    """
    voice = "en_US-lessac-medium"
    directory = voices.voice_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for suffix in (".onnx", ".onnx.json"):
        (directory / f"{voice}{suffix}").write_bytes(b"voice")
    assert voices.voice_installed(voice), "the fixture did not put a voice on disk"
    # The basetemp, not this test's `tmp_path`: the autouse XDG isolation hands
    # every test its own data home under the session's base temp directory, and
    # this is what proves the fixture bytes cannot land in a real home.
    basetemp = str(tmp_path_factory.getbasetemp())
    assert str(directory).startswith(basetemp), (
        f"the voice went to {directory}, which is outside the test's own temporary directory; "
        "the isolation is what makes the rest of this file safe to run"
    )

    def _downloaded_row_state():
        row = [r for r in surface.build(floor).catalogue_rows() if r._voice_key == voice][0]
        detail = _row_detail(row)
        assert "downloaded" in detail, detail
        assert "MB" not in detail, detail
        return detail

    kept = _downloaded_row_state()
    assert "downloaded" in kept

    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(voices, "voice_installed", lambda name: False)
        assert voices.voice_installed(voice) is False, "the control did not take effect"
        assert (directory / f"{voice}.onnx").is_file(), "the on-disk state was removed, so this proves nothing"
        assert (directory / f"{voice}.onnx.json").is_file()
        with pytest.raises(AssertionError):
            _downloaded_row_state()

    assert _downloaded_row_state() == kept, "the control was not restored"

def test_the_chain_and_the_real_cascade_cannot_drift_apart():
    """**A fifth link appeared and this table was not updated.**

    `tts.PiperTTS._engine` grew `"cloud"` on 2026-10-06 (`cloud_voice.CloudTTS`,
    opt-in, reached only when none of the four local engines (all of which are local) can speak). This
    module's `CHAIN` still listed four, so a machine whose chosen engine *was*
    `cloud` would have found no entry at or above its own and been told it was
    using nothing - the panel-silently-incomplete defect this file's own docstring
    records for the Eyes list.

    Asserted by **asking the real cascade for every outcome it can produce**
    rather than by reading `_engine`'s body: the three ways it can reach its last
    link are stubbed in turn, and each result must be a name this panel knows
    about. A new engine therefore fails here, on a test, instead of on a screen.
    """
    from shani_chronoa.gui.surfaces import voice as surface

    piper = tts.PiperTTS.__new__(tts.PiperTTS)
    piper.piper_path = "/nonexistent/piper"
    piper.voice_path = "/nonexistent/voice"
    piper.piper_trial = False
    piper.kokoro_trial_voice = ""
    piper.rate = 1.0

    # Kokoro off, so the cascade starts from the Piper link and walks down.
    found = set()
    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(surface.os.path, "exists", lambda p: False)
        ctx.setattr(surface.shutil, "which", lambda name: None)
        found.add(piper._engine("kokoro is off"))
        # With every local engine absent and the cloud reachable, the last link.
        from shani_chronoa import cloud_voice
        ctx.setattr(cloud_voice.CloudTTS, "is_available", lambda self: True)
        found.add(piper._engine("kokoro is off"))
        # And with the cloud unavailable too, nothing at all.
        ctx.setattr(cloud_voice.CloudTTS, "is_available", lambda self: False)
        found.add(piper._engine("kokoro is off"))

    assert found == {None, "cloud"}, (
        f"the cascade produced {sorted(str(f) for f in found)}, which does not "
        "match the stubbed conditions - so this test is not exercising it")
    assert "cloud" in surface.CHAIN, (
        "the cascade can choose 'cloud' but the panel's chain does not know the "
        "name, so the current-engine row would have nothing above it to say and "
        "would read as 'using nothing'")
    assert "cloud" in surface.COST, (
        "the cloud engine has no row in COST, so its detail falls back to the "
        "unmeasured wording and loses the only thing that matters about it - "
        "that the reply text leaves the machine")


def test_the_cloud_row_says_the_reply_leaves_the_machine():
    """The disclosure, not a latency figure, is this engine's cost."""
    from shani_chronoa.gui.surfaces import voice as surface
    cost = surface.COST["cloud"].lower()
    assert "leaves the machine" in cost or "sent to a cloud provider" in cost, cost
    assert "off by default" in cost, cost


# -- cloud speech input: the panel must describe the engine that is running --


class TestCloudSpeechInput:
    """**By symmetry with the output side, which was fixed first.**

    `PiperTTS.engine()` grew a `cloud` link and `_chain()` was not extended, so a
    machine speaking through a provider was told it was using nothing. The input
    side had the same defect and I fixed only the output side: `_stt_for`'s guard
    was `hasattr(live, "model_path") and callable(live.is_available)`, which
    identifies a speech engine by a field only the two *local* backends have.

    Measured on the real functions with a running `CloudSTT`: the guard rejected
    the running object, the panel built a local `WhisperSTT` instead, and the row
    read **"Whisper.cpp (whisper-cli)"** with
    *"the whisper.cpp model ggml-base.bin is not downloaded"* - on a machine that
    was transcribing in the cloud and had no whisper.cpp at all.

    `is_available` plus `transcribe` is the contract `stt.build_stt` says its own
    two backends share, so that is what the panel asks for now.
    """

    @staticmethod
    def _cloud_app(wires):
        from shani_chronoa import cloud_voice
        from shani_chronoa.config import ChronoaConfig
        wires["stt"] = wires["tts"] = True
        monkey = pytest.MonkeyPatch()
        monkey.setattr(cloud_voice, "_read_switch",
                       lambda name, config=None: True)
        monkey.setattr(cloud_voice, "_provider_key",
                       lambda pid, api_keys=None, config=None: "sk-test")
        monkey.setattr(cloud_voice.egress, "privacy_mode_enabled", lambda: False)
        config = ChronoaConfig()
        app = type("A", (), {"config": config,
                             "stt": cloud_voice.CloudSTT(),
                             "dispatcher": None})()
        return app, config, monkey

    def test_the_panel_describes_the_engine_that_is_actually_running(self):
        app, _config, monkey = self._cloud_app({})
        try:
            engine, where = surface._stt_for(app, app.config)
        finally:
            monkey.undo()
        assert isinstance(engine, cloud_voice.CloudSTT), (
            f"the panel rejected the running object ({engine!r}, via {where}) and "
            "described a locally built engine instead - so it reports a whisper "
            "binary and model as missing on a machine using neither")

    def test_the_row_names_the_cloud_and_not_whisper(self):
        app, _config, monkey = self._cloud_app({})
        try:
            speech = surface._speech_input(app, app.config)
        finally:
            monkey.undo()
        assert speech.label == "Cloud provider", (
            f"the row reads {speech.label!r} while a cloud provider is "
            "transcribing, naming a program that was never invoked")
        assert "hissper" not in speech.label, speech.label

    def test_a_working_cloud_engine_reports_no_problem(self):
        """`stt_problem` used to raise on it, and the caller showed the traceback."""
        app, _config, monkey = self._cloud_app({})
        try:
            speech = surface._speech_input(app, app.config)
        finally:
            monkey.undo()
        assert speech.state is True
        assert speech.problem == "", (
            f"an engine that can listen is reported as {speech.problem!r}, which "
            "would send somebody to install whisper-cpp on a machine that does "
            "not use it")
        assert "unknown" not in speech.problem.lower(), speech.problem

    def test_an_unavailable_cloud_engine_names_its_own_reason(self, monkeypatch):
        """Not `whisper-cpp is not installed` - that is the wrong program.

        **Through `monkeypatch`, not by assigning and restoring by hand.** The
        first version did `cloud_voice._read_switch = ...` in a `try/finally`,
        which leaves the module globally patched for every later test whenever
        the assertion fails before the restore - and a failure is exactly when
        you least want to also corrupt the rest of the file.
        """
        from shani_chronoa import cloud_voice
        from shani_chronoa.senses import hearing

        monkeypatch.setattr(cloud_voice, "_read_switch",
                            lambda name, config=None: False)
        problem = hearing.stt_problem(cloud_voice.CloudSTT())
        assert "cloud-stt-enabled" in problem, (
            f"the reason given is {problem!r}, which does not name the switch "
            "that has to be turned on")
        assert "hissper" not in problem, problem

    def test_a_local_engine_is_unchanged(self):
        """The fix must not move the two local backends' answers."""
        from shani_chronoa.config import ChronoaConfig
        app = type("A", (), {"config": ChronoaConfig(), "stt": None,
                             "dispatcher": None})()
        speech = surface._speech_input(app, app.config)
        assert speech.label == "Whisper.cpp (whisper-cli)", speech.label
        assert "model" in speech.problem or "installed" in speech.problem, speech.problem


def test_stt_problem_does_not_raise_on_an_engine_without_whisper_paths():
    """The AttributeError this guarded, asserted as a property rather than a shape."""
    from shani_chronoa.senses import hearing

    class Engine:
        """No `whisper_path`, no `model_path`, and no `refusal()` either."""

        def is_available(self):
            return False

        def transcribe(self, path):
            return ""

    problem = hearing.stt_problem(Engine())
    assert isinstance(problem, str) and problem, (
        "an engine with nothing to check must still be given an answer, not an "
        "AttributeError")
    assert "hissper" not in problem, problem
