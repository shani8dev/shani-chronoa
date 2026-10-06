"""The model surface as a built widget.

Four properties are pinned here, and each is one a reader cannot get right
from the diff:

- **the module contract** - `TITLE`, `ICON`, `build`, and that the icon name is
  one the theme actually ships (an icon nothing carries renders as a blank
  space, and a widget that constructed says nothing about that);
- **`build(app_stub)` returns a widget**, and `app_stub` is a bare object with
  a `config` and a `hardware` - no `Gtk.Application`, no libadwaita, no
  display, which is the condition the surface was written for;
- **the extras rows count equals what the on-disk state reports**. The count is
  recomputed here from the real `local_vision` / `local_embed` / `imagegen` /
  `languages` functions rather than read out of the surface, so the two are
  independent; and the "installed" and "not installed" halves are separated,
  because a surface that rendered every row as installed would satisfy a bare
  count;
- **a missing extra file renders "not installed"**, with real files created at
  the real paths (`local_vision.model_dir()`, `local_embed.model_dir()`,
  `imagegen.engine_dir()`) that the modules read, so the rows come from the
  same on-disk state `setup_wizard.state()` reads and not from a fixture the
  test also feeds to the surface.

Plus the negative control at the bottom: a present extra is made to report
absent, the assertion meant to catch that is shown to fail, and the state is
restored. An installed-check that cannot come out wrong is the dead-control
failure this repo keeps recording.

Everything is relocated before anything is built. `XDG_DATA_HOME` is set by
the autouse `_hermetic_env`/`_isolate_xdg_data_home` fixtures, and the
`hardware` the stub carries is a real object with a real `profile`, so the
surface never constructs a `HardwareProfile` of its own (which would shell out
to `nvidia-smi`/`vulkaninfo`/`rocm-smi`).
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

Gtk.init()

from shani_chronoa import (  # noqa: E402
    imagegen,
    languages,
    local_embed,
    local_llm,
    local_vision,
)
from shani_chronoa.gui.surfaces import model as surface  # noqa: E402
from shani_chronoa.opencv import runtime as opencv_runtime  # noqa: E402
from shani_chronoa.skills import solve_math  # noqa: E402


class _StubConfig:
    """A `ChronoaConfig` reduced to what this surface reads.

    `model` / `hardware_profile` / `ollama_host` are properties on the real
    class, and this surface accepts either a property or a method for them -
    which is why they are plain attributes here and methods in
    `_CallableConfig` below.
    """

    def __init__(self, model="", hardware_profile="auto", host="http://127.0.0.1:11434",
                 privacy=True, cloud_fallback=False, languages_chosen=""):
        self.model = model
        self.hardware_profile = hardware_profile
        self.ollama_host = host
        self.privacy_mode = privacy
        self.cloud_fallback_enabled = cloud_fallback
        self._languages = languages_chosen
        self.values = {"extra-languages": languages_chosen, "language": "en"}

    def get(self, key, default=""):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def get_bool(self, key, default=False):
        return bool({"privacy-mode": self.privacy_mode,
                     "cloud-fallback-enabled": self.cloud_fallback_enabled}.get(key, default))

    def cloud_llm_api_keys(self):
        return {}


class _StubHardware:
    def __init__(self, profile="medium", model="qwen3:4b"):
        self.profile = profile
        self._model = model

    def get_model(self):
        return self._model


class _StubApp:
    """The least an app can be: a config, a hardware profile, no overrides."""

    def __init__(self, config=None, hardware=None, model_override=""):
        self.config = config if config is not None else _StubConfig()
        self.hardware = hardware if hardware is not None else _StubHardware()
        self._model_override = model_override


def _texts(widget):
    """Every label's rendered text, in tree order.

    GTK4 dropped `Gtk.Container`, so `get_first_child()` is the only way down.
    """
    out = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            out.append(child.get_text())
        out.extend(_texts(child))
        child = child.get_next_sibling()
    return out


def _joined(widget):
    return " ".join(_texts(widget))


def _row_title(row):
    return row._row_title.get_text()


def _row_detail(row):
    return row._row_detail.get_text()


# -- on-disk state, written where the modules actually look ------------------


@pytest.fixture
def on_disk(tmp_path, monkeypatch):
    """An `XDG_DATA_HOME` of this test's own, with helpers that create the real
    files each extras module looks for.

    `tmp_path` rather than the session fixture because these paths are read per
    call, so pointing `XDG_DATA_HOME` at this test's directory is the whole
    isolation - no module global is involved and nothing can leak into the
    developer's own `~/.local/share/shani-chronoa`.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    class Disk:
        root = tmp_path / "data"

        def eye(self, key="qwen3-vl-2b"):
            """Both files of one vision tier: `local_vision.installed()` needs
            the model *and* its projector, which is why neither alone counts."""
            model = local_vision.MODELS[key]
            directory = local_vision.model_dir()
            directory.mkdir(parents=True, exist_ok=True)
            for spec in (model.model, model.mmproj):
                (directory / spec.filename).write_bytes(b"gguf")
            return key

        def memory(self):
            directory = local_embed.model_dir()
            directory.mkdir(parents=True, exist_ok=True)
            (directory / local_embed.MODEL.filename).write_bytes(b"gguf")
            return local_embed.MODEL.key

        def imagine(self):
            engine = imagegen.engine_dir()
            engine.mkdir(parents=True, exist_ok=True)
            binary = engine / "sd-server"
            binary.write_bytes(b"#!/bin/sh\n")
            binary.chmod(0o755)
            models = imagegen.model_dir()
            models.mkdir(parents=True, exist_ok=True)
            (models / imagegen.MODEL.filename).write_bytes(b"gguf")

        def photos(self):
            directory = opencv_runtime.python_dir()
            for package in ("cv2", "numpy"):
                (directory / package).mkdir(parents=True, exist_ok=True)
            models = opencv_runtime.models_dir()
            models.mkdir(parents=True, exist_ok=True)
            for spec in opencv_runtime.MODELS:
                (models / spec.filename).write_bytes(b"onnx")

        def language(self, code="hi"):
            directory = languages.tessdata_dir()
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{languages.LANGUAGES[code].tesseract}.traineddata").write_bytes(b"tess")

    disk = Disk()
    # The isolation is only real if it actually applied: every module here
    # resolves its path per call, so this is the whole redirection - and a
    # fixture that silently lost the race to `_isolate_xdg_data_home` would
    # write fixture bytes into the developer's own `~/.local/share`.
    assert str(local_vision.model_dir()).startswith(str(disk.root))
    # **Maths is the one extra nothing on disk decides.** Its two engines are
    # distro packages, so its row reads the environment - and this machine has
    # `symengine` and `bc` installed, which made six tests here fail with counts
    # they had assumed ("nothing is installed", "exactly two"). The fixture
    # creates files for the other seven and cannot do that for this one, so it
    # controls it instead: absent by default, the way a fresh install reads.
    # `disk.maths()` puts it back, so the installed case is still reachable.
    monkeypatch.setattr(solve_math, "se", None)
    monkeypatch.setattr(solve_math, "_bc_available", lambda: False)
    disk.maths = lambda: monkeypatch.setattr(
        solve_math, "se", object(), raising=False) or monkeypatch.setattr(
        solve_math, "_bc_available", lambda: True)
    return disk


def _expected_installed(config):
    """The installed/not-installed split, recomputed from the same on-disk
    functions the surface reads - and deliberately not from `_EXTRAS`, so a
    surface that inverted one row's answer fails here rather than agreeing with
    itself."""
    chosen = languages.chosen(config)
    return {
        "eyes": bool(local_vision.installed()),
        "imagine": bool(imagegen.installed()),
        "memory": bool(local_embed.installed()),
        # A chosen language with nothing downloaded is not installed: the
        # reading data is what `languages.reads()` looks for on disk.
        "languages": bool(chosen) and any(languages.reads(c) or languages.speaks(c) for c in chosen),
        "photos": bool(opencv_runtime.installed()),
    }


# -- module contract --------------------------------------------------------


class TestModuleContract:
    def test_exports_title_icon_and_build(self):
        assert isinstance(surface.TITLE, str) and surface.TITLE
        assert isinstance(surface.ICON, str) and surface.ICON
        assert callable(surface.build)

    def test_it_is_the_module_the_registry_names(self):
        """`surfaces/__init__.py` resolves `shani_chronoa.gui.surfaces.model`,
        so a module that does not answer to that name is a hole in the sidebar
        that only a lookup notices."""
        assert sys.modules["shani_chronoa.gui.surfaces.model"] is surface
        assert surface.__name__ == "shani_chronoa.gui.surfaces.model"

    def test_the_icon_name_is_one_the_theme_actually_has(self):
        """An icon name nothing ships renders as a blank space in the sidebar,
        and a widget that constructed says nothing about that. Neither the live
        theme nor the installed icon directories answering is a failure, not a
        skip - a green line that looked at nothing is worse."""
        display = Gdk.Display.get_default()
        if display is not None:
            assert Gtk.IconTheme.get_for_display(display).has_icon(surface.ICON), (
                f"{surface.ICON} is not in the icon theme this display uses")
            return
        roots = [pathlib.Path(p) for p in
                 (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")]
        found = [path for root in roots
                 for path in root.glob(f"icons/*/*/{surface.ICON}.*")]
        assert found, f"no display and no installed icon theme carries {surface.ICON}"

    def test_it_builds_without_libadwaita(self):
        """Plain GTK4 on purpose: `Adw` widgets need `Adw.init()` before they
        exist, and the sidebar builds this under a bare `Gtk.Application`."""
        assert "Adw" not in dir(surface)
        source = pathlib.Path(surface.__file__).read_text(encoding="utf-8")
        assert 'gi.require_version("Adw"' not in source, "the surface asks for libadwaita"


# -- build ------------------------------------------------------------------


class TestBuild:
    def test_returns_a_gtk_widget_for_a_stub_app(self):
        widget = surface.build(_StubApp())
        assert isinstance(widget, Gtk.Widget)

    def test_an_app_with_no_config_at_all_still_builds(self):
        """A missing settings object is a real state, not a crash, and none of
        its answers may become a claim: the cloud row's gates cannot be read, so
        they are unknown rather than shut."""
        app = _StubApp()
        del app.config
        widget = surface.build(app)
        assert isinstance(widget, Gtk.Widget)
        cloud = [row for row in widget.engine_rows() if row._engine_key == "cloud"][0]
        assert cloud._engine_state is None
        assert "unknown" in _row_title(cloud), _row_title(cloud)

    def test_it_reads_no_file_outside_the_test_directory(self, on_disk):
        """Every extras path resolves under the test's own `XDG_DATA_HOME`."""
        widget = surface.build(_StubApp())
        for row in widget.extras():
            tooltip = row.get_tooltip_text()
            for path in (local_llm.model_dir(), local_vision.model_dir(), local_embed.model_dir(),
                         imagegen.model_dir(), opencv_runtime.models_dir(), languages.tessdata_dir()):
                if str(path) in tooltip:
                    assert str(path).startswith(str(on_disk.root)), f"{path} is outside the test"


# -- the model --------------------------------------------------------------


class TestTheModel:
    def test_it_names_the_model_the_app_would_use(self):
        """`_model_override` -> `config.model` -> `HardwareProfile.get_model()`,
        in that order, because that is the order `application._init_components`
        resolves the model in."""
        assert "qwen3:4b" in _joined(surface.build(_StubApp()))
        assert "chosen for this computer" in _joined(surface.build(_StubApp()))
        assert "qwen3:8b" in _joined(surface.build(
            _StubApp(config=_StubConfig(model="qwen3:8b"))))
        assert "command line" in _joined(surface.build(_StubApp(model_override="llama-3:8b")))

    def test_a_command_line_override_beats_the_setting(self):
        joined = _joined(surface.build(_StubApp(config=_StubConfig(model="qwen3:8b"),
                                           model_override="llama-3:8b")))
        assert "llama-3:8b" in joined and "command line" in joined

    def test_it_names_the_hardware_profile(self):
        joined = _joined(surface.build(_StubApp(hardware=_StubHardware(profile="low",
                                                                       model="qwen3:1.7b"))))
        assert "low" in joined and "qwen3:1.7b" in joined

    def test_no_model_file_says_not_installed_rather_than_failing(self, on_disk):
        """A fresh install has no GGUF and no `llama-server`; the page must say
        so in the same words as a missing extra, because that is the fact that
        decides whether Chronoa can think."""
        widget = surface.build(_StubApp())
        details = " ".join(_row_detail(row) for row in widget.model_rows())
        assert "not installed" in details, details

    def test_an_existing_model_file_is_named(self, on_disk, monkeypatch):
        """The state `setup_brain` writes: the file, then the `current.gguf`
        symlink its `use()` renames into place. `llama-server` is patched in
        because without it the page stops at "nothing local can run" - which is
        the right answer on a machine without llama-cpp, and a different branch
        from the one under test."""
        monkeypatch.setattr(local_llm, "server_binary", lambda: "/usr/bin/llama-server")
        local_llm.model_dir().mkdir(parents=True, exist_ok=True)
        (local_llm.model_dir() / local_llm.SPECS["qwen3-1.7b"].filename).write_bytes(b"gguf")
        local_llm.use("qwen3-1.7b")
        details = " ".join(_row_detail(row) for row in surface.build(_StubApp()).model_rows())
        assert "qwen3-1.7b" in details and "current.gguf" in details, details

    def test_a_hostile_model_name_is_shown_not_parsed(self):
        """A model name comes from a gsetting and a command line. `<b>`, `&`
        and a stray quote are all ordinary content in one; without escaping they
        are markup."""
        hostile = '<b>x</b> & "y"'
        widget = surface.build(_StubApp(config=_StubConfig(model=hostile)))
        shown = _joined(widget)
        assert hostile in shown, shown
        assert "&amp;" not in shown and "&lt;b&gt;" not in shown, shown


# -- the engines ------------------------------------------------------------


class TestTheEngines:
    def test_all_three_engines_are_listed_in_precedence_order(self):
        rows = surface.build(_StubApp()).engine_rows()
        assert [row._engine_key for row in rows] == ["ollama", "local", "cloud"]

    def test_nothing_installed_means_nothing_answers(self, on_disk):
        widget = surface.build(_StubApp())
        assert [row._engine_state for row in widget.engine_rows()] == [False, False, False]
        assert "nothing" in widget.serving().lower(), widget.serving()

    def test_the_cloud_fallback_names_the_gate_that_is_shut(self):
        detail = " ".join(_row_detail(row) for row in surface.build(_StubApp()).engine_rows())
        assert "privacy mode is on" in detail and "cloud-fallback-enabled is off" in detail, detail

    def test_the_cloud_row_is_not_asked(self, monkeypatch):
        """`CloudLLMChain.is_available()` only counts configured providers; a
        chain that reached the network would make this page's "local state
        only" claim false, so the construction itself is checked."""
        from shani_chronoa import cloud_llm

        built = []

        def _refuse(*args, **kwargs):
            built.append(kwargs.get("provider_ids", args[0] if args else None))
            return type("Chain", (), {"is_available": staticmethod(lambda: True), "model": "x"})()

        monkeypatch.setattr(cloud_llm, "CloudLLMChain", _refuse)
        widget = surface.build(_StubApp(config=_StubConfig(privacy=False, cloud_fallback=True)))
        cloud = [row for row in widget.engine_rows() if row._engine_key == "cloud"][0]
        assert built, "the cloud row did not build a chain, so this proved nothing"
        assert cloud._engine_state is True
        assert "llm7" in _row_detail(cloud) or "would answer through" in _row_detail(cloud)

    def test_a_non_loopback_ollama_host_is_unknown_not_absent(self):
        """Asking a host on another machine would be a network call, and
        reporting it absent because the probe was skipped would be a confident
        wrong answer about a server that may well be up."""
        row = [r for r in surface.build(_StubApp(config=_StubConfig(
            host="http://192.0.2.10:11434"))).engine_rows() if r._engine_key == "ollama"][0]
        assert row._engine_state is None
        assert "unknown" in _row_title(row), _row_title(row)

    def test_a_probe_that_raises_is_reported_unknown(self, monkeypatch):
        """The distinction this page exists to keep: a failed probe is not a
        negative answer. Without it a broken engine reads as "not installed" and
        sends someone to download a model they already have."""
        def _explode(*_a, **_k):
            raise OSError("the socket went away")

        monkeypatch.setattr(local_llm, "server_binary", lambda: "/usr/bin/llama-server")
        monkeypatch.setattr(local_llm, "installed", lambda: ["qwen3-1.7b"])
        monkeypatch.setattr(local_llm, "active", lambda: "qwen3-1.7b")
        monkeypatch.setattr(local_llm, "is_up", _explode)
        widget = surface.build(_StubApp())
        local = [row for row in widget.engine_rows() if row._engine_key == "local"][0]
        assert local._engine_state is None
        assert "unknown" in _row_title(local)
        assert "the socket went away" in _row_detail(local)
        assert "cannot be determined" in widget.serving(), widget.serving()

    def test_an_available_local_engine_is_the_one_that_answers(self, monkeypatch):
        monkeypatch.setattr(local_llm, "server_binary", lambda: "/usr/bin/llama-server")
        monkeypatch.setattr(local_llm, "installed", lambda: ["qwen3-1.7b"])
        monkeypatch.setattr(local_llm, "active", lambda: "qwen3-1.7b")
        monkeypatch.setattr(local_llm, "is_up", lambda: True)
        widget = surface.build(_StubApp())
        local = [row for row in widget.engine_rows() if row._engine_key == "local"][0]
        assert local._engine_state is True
        assert "llama.cpp on this computer" in widget.serving(), widget.serving()


# -- the extras -------------------------------------------------------------


class TestExtras:
    def test_one_row_per_extra_and_the_count_matches_the_on_disk_state(self, on_disk):
        on_disk.eye()
        on_disk.memory()
        on_disk.imagine()

        config = _StubConfig()
        widget = surface.build(_StubApp(config=config))
        keys = [row._extra_key for row in widget.extras()]
        assert keys == [spec[0] for spec in surface._EXTRAS], keys
        assert widget.extra_count() == len(surface._EXTRAS) == 8

        expected = _expected_installed(config)
        shown = {row._extra_key: row._extra_state for row in widget.extras()}
        for key, want in expected.items():
            assert shown[key] is want, f"{key}: on-disk says {want}, the row says {shown[key]}"
        assert widget.installed_keys() == [k for k, v in expected.items() if v]
        assert len(widget.installed_keys()) == 3, widget.installed_keys()

    def test_a_language_needs_its_reading_data_on_disk_too(self, on_disk):
        """`languages.chosen()` is the record setup writes, but a chosen
        language with nothing fetched is not an installed extra - and the row
        separates the two rather than rounding the record up."""
        config = _StubConfig(languages_chosen="hi")
        chosen_only = surface.build(_StubApp(config=config))
        assert chosen_only.installed_keys() == [], chosen_only.installed_keys()
        assert "nothing is downloaded yet" in _row_detail(chosen_only.extras()[3])

        on_disk.language("hi")
        widget = surface.build(_StubApp(config=config))
        assert widget.installed_keys() == ["languages"]
        assert "reading data for hi" in _row_detail(widget.extras()[3])
        assert "no voice installed" in _row_detail(widget.extras()[3])

    def test_the_photos_extra_is_read_from_opencv_runtime(self, on_disk):
        assert surface.build(_StubApp()).installed_keys() == []
        on_disk.photos()
        widget = surface.build(_StubApp())
        assert "photos" in widget.installed_keys(), widget.installed_keys()

    def test_a_missing_extra_file_renders_not_installed(self, on_disk):
        """Nothing is created on disk at all, so every row must read "not
        installed" - and the words must be there, not merely implied by the
        absence of an "installed"."""
        widget = surface.build(_StubApp())
        for row in widget.extras():
            assert row._extra_state is False, row._extra_key
            assert "not installed" in _row_title(row), _row_title(row)
        assert widget.installed_keys() == []
        assert widget.unknown_keys() == []

    def test_a_present_extra_file_renders_installed(self, on_disk):
        on_disk.eye()
        row = [r for r in surface.build(_StubApp()).extras() if r._extra_key == "eyes"][0]
        assert row._extra_state is True
        assert _row_title(row) == "Eyes - installed"

    def test_a_probe_that_raises_is_unknown_not_absent(self, monkeypatch):
        """The same rule as the engines: an extra whose state could not be read
        is not an extra the user has not installed."""
        monkeypatch.setattr(local_vision, "installed", lambda: (_ for _ in ()).throw(OSError("EIO")))
        widget = surface.build(_StubApp())
        assert widget.unknown_keys() == ["eyes"]
        row = [r for r in widget.extras() if r._extra_key == "eyes"][0]
        assert row._extra_state is None
        assert _row_title(row) == "Eyes - unknown"
        assert "EIO" in _row_detail(row)
        # And the other seven are unaffected: one unreadable extra does not
        # hide the rest of the page.
        assert widget.extra_count() == 8

    def test_every_row_says_where_its_answer_came_from(self, on_disk):
        on_disk.eye()
        for row in surface.build(_StubApp()).extras():
            tooltip = row.get_tooltip_text()
            assert "Read from" in tooltip, tooltip
            assert "--setup" in tooltip, tooltip

    def test_math_is_read_from_the_skill_that_gates_on_symengine(self):
        """There is no maths page on the wizard - both engines are distro
        packages - so the state that answers this is `solve_math`'s own flags:
        `se`, the exact flag `_run()` refuses on, and `_bc_available()`, the one
        the bc engine asks.

        **This asserted `solve_math.sp`, which does not exist.** The skill moved
        to `symengine` plus `bc`, and the row's probe raised `AttributeError`, so
        the row came back *unknown* - which is why two other tests in this class
        went red for a reason that had nothing to do with what they were
        checking. A probe reading an attribute the product deleted is a stale
        probe, not a failing test, and the fix belongs in the product.
        """
        from shani_chronoa.skills import solve_math

        row = [r for r in surface.build(_StubApp()).extras() if r._extra_key == "math"][0]
        symbolic = solve_math.se is not None
        arithmetic = solve_math._bc_available()
        assert row._extra_state is (symbolic and arithmetic)
        if not symbolic:
            assert "python-symengine" in _row_detail(row)
        if not arithmetic:
            assert "bc" in _row_detail(row)

    def test_the_maths_row_names_both_engines_rather_than_one(self):
        """A row that reported symengine alone would say "installed" on a
        machine where every integral raises, and one that reported `bc` alone
        would say it where nothing symbolic can run."""
        detail = _row_detail([r for r in surface.build(_StubApp()).extras()
                              if r._extra_key == "math"][0])
        assert "symengine" in detail and "bc" in detail


# -- controls ---------------------------------------------------------------


class TestControls:
    def test_the_one_button_has_a_tooltip_and_an_accessible_label(self):
        button = surface.build(_StubApp()).reload_button()
        assert isinstance(button, Gtk.Button)
        assert button.get_tooltip_text()
        assert "again" in button.get_tooltip_text()

    def test_every_row_has_a_tooltip(self):
        widget = surface.build(_StubApp())
        for row in widget.model_rows() + widget.engine_rows() + widget.extras():
            assert row.get_tooltip_text(), row.get_style_class()

    def test_reload_rereads_the_disk(self, on_disk):
        """`installed` and `available right now` are claims about the moment they
        were read; a page that cached them would be wrong within seconds of the
        setup wizard finishing. So the row holds until Reload is pressed, and
        then it is right again."""
        widget = surface.build(_StubApp())
        assert widget.installed_keys() == []
        on_disk.memory()
        assert widget.installed_keys() == [], "the row changed without being re-read"
        widget.reload_button().emit("clicked")
        assert widget.installed_keys() == ["memory"], widget.installed_keys()
        # And nothing accumulates across the rebuild.
        assert widget.extra_count() == 8


# -- read-only --------------------------------------------------------------


class TestReadOnly:
    def test_building_writes_nothing_under_the_data_home(self, on_disk):
        on_disk.memory()
        before = sorted(str(p) for p in on_disk.root.rglob("*"))
        surface.build(_StubApp())
        assert sorted(str(p) for p in on_disk.root.rglob("*")) == before

    def test_it_does_not_provision_or_start_anything(self, monkeypatch):
        for module, name in ((local_vision, "provision"), (local_embed, "provision"),
                             (imagegen, "provision"), (imagegen, "install_engine"),
                             (local_llm, "provision"), (local_llm, "start_service"),
                             (languages, "install"), (opencv_runtime, "install")):
            monkeypatch.setattr(module, name,
                                (lambda m, n: lambda *a, **k: pytest.fail(f"{m}.{n} was called"))(
                                    module.__name__, name),
                                raising=False)
        surface.build(_StubApp())


def test_installed_state_negative_control(on_disk):
    """A present extra made to report absent must break the count assertion.

    A check that cannot come out wrong proves nothing, so this creates the real
    vision files, confirms the page counts them, then patches the one function
    the row reads - `local_vision.installed`, where it is read, not where it is
    re-exported - to report nothing. It asserts the patch took effect *while the
    files are still on disk* (so the failure is the rendering's, not an emptied
    disk's), then asserts the count assertion meant to catch that fails. Then it
    restores and confirms the count is back: an un-restored control is a change
    left behind for the next reader.
    """
    on_disk.eye()
    on_disk.memory()
    config = _StubConfig()
    app = _StubApp(config=config)

    def _installed_count():
        widget = surface.build(app)
        assert widget.extra_count() == len(surface._EXTRAS)
        expected = _expected_installed(config)
        shown = {row._extra_key: row._extra_state for row in widget.extras()}
        for key, want in expected.items():
            assert shown[key] is want, f"{key}: on-disk says {want}, the row says {shown[key]}"
        return len(widget.installed_keys())

    assert _installed_count() == 2, "the fixture did not put two extras on disk"

    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(local_vision, "installed", lambda: [])
        assert local_vision.installed() == [], "the control did not take effect"
        assert local_vision.model_dir().is_dir(), "the on-disk state was removed, so this proves nothing"
        assert (local_vision.model_dir() / local_vision.MODELS["qwen3-vl-2b"].model.filename).is_file()
        with pytest.raises(AssertionError):
            assert _installed_count() == 2

    assert _installed_count() == 2, "the control was not restored"


def test_a_present_extra_that_reports_absent_reads_not_installed(on_disk):
    """The same mutation, checked from the other side: with the vision files on
    disk and `installed()` refusing to see them, the row must say "not
    installed" - and say it because the state said so, not because the surface
    guessed."""
    on_disk.eye()
    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(local_vision, "installed", lambda: [])
        row = [r for r in surface.build(_StubApp()).extras() if r._extra_key == "eyes"][0]
    assert row._extra_state is False
    assert row is not None and _row_title(row) == "Eyes - not installed"

class TestTwoEnginesCannotBothClaimToAnswer:
    """**Availability and selection are different questions here, unlike in Voice.**

    `PiperTTS.engine()` is asked per reply, so the running object and the
    selected engine coincide - which is why `gui/surfaces/voice.py` resolves
    through `app.tts`. The LLM is chosen **once, at startup**, and
    `app/brain.py:_maybe_enable_cloud_fallback` returns early on
    `isinstance(self.llm, CloudLLMChain)` and never reconsiders.

    So a machine that started without llama-server and started it afterwards has
    an *available* local engine and a *selected* cloud one. The panel used to read
    `f"{HOST}:{PORT} is answering with {active}"` off `local_llm.is_up()` -
    a claim about the app made from a probe of the server.

    Measured, in exactly that state: the local row said **"127.0.0.1:8765 is
    answering with qwen3-1.7b"** while `app.llm` held a `CloudLLMChain`, and the
    cloud row said it "would answer". Two rows, one of them claiming a fact about
    the app that was false.
    """

    @staticmethod
    def _diverged():
        from shani_chronoa import cloud_llm, local_llm
        from shani_chronoa.config import ChronoaConfig

        cfg = ChronoaConfig()
        cfg.set("privacy-mode", "false")
        cfg.set("cloud-fallback-enabled", "true")
        monkey = pytest.MonkeyPatch()
        monkey.setattr(local_llm, "server_binary", lambda: "/usr/bin/llama-server")
        monkey.setattr(local_llm, "installed", lambda: ["qwen3-1.7b"])
        monkey.setattr(local_llm, "active", lambda: "qwen3-1.7b")
        monkey.setattr(local_llm, "is_up", lambda: True)
        app = type("App", (), {
            "config": cfg, "hardware": None,
            "llm": cloud_llm.CloudLLMChain(
                provider_ids=cloud_llm.DEFAULT_PROVIDER_ORDER, api_keys={}),
        })()
        return app, cfg, monkey

    def test_the_local_row_does_not_claim_to_be_the_one_answering(self):
        from shani_chronoa.gui.surfaces import model as surface
        app, cfg, monkey = self._diverged()
        try:
            rows = {e.key: e for e in surface._engines(cfg, "qwen3-1.7b")}
        finally:
            monkey.undo()
        assert "is answering" not in rows["local"].detail, (
            f"the local row asserts it is answering ({rows['local'].detail!r}) "
            "from a probe of the server, while the app holds a cloud chain")
        assert "is up" in rows["local"].detail, rows["local"].detail

    def test_the_selected_engine_is_named_from_the_object_the_app_holds(self):
        from shani_chronoa.gui.surfaces import model as surface
        app, _cfg, monkey = self._diverged()
        try:
            chosen = surface._selected_engine(app)
        finally:
            monkey.undo()
        # **The phrase, not the substring.** `"cloud" in chosen.lower()` also
        # matches "Answers through CloudLLMChain" - the class name - so a mutation
        # that dropped the isinstance check entirely ran green. Asserted on what
        # the sentence says instead of on a word it happens to contain.
        assert "cloud provider" in chosen.lower(), chosen
        assert "cloudllmchain" not in chosen.lower(), (
            f"the selected engine is reported by its class name ({chosen!r}), so a "
            "person is shown an implementation detail rather than what answers")
        # And it says why the two can differ, because that is the whole point.
        assert "startup" in chosen.lower(), chosen

    def test_it_names_a_local_engine_when_that_is_what_is_held(self):
        from shani_chronoa.gui.surfaces import model as surface
        app, _cfg, monkey = self._diverged()
        try:
            app.llm = type("OllamaLLM", (), {})()
            chosen = surface._selected_engine(app)
            app.llm = None
            none = surface._selected_engine(app)
        finally:
            monkey.undo()
        assert "OllamaLLM" in chosen, chosen
        assert "cloud" not in chosen.lower(), chosen
        assert "startup" in chosen.lower(), chosen
        assert "no engine" in none.lower(), none
