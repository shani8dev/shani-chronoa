"""The Devices surface, as a built widget.

This panel's one promise is that the four ways of there being no device list are
each reported as itself. "Not permitted", "no phone link installed", "the link did
not answer" and "nothing is paired" are four different facts about a machine, and
the failure mode this file is built against is the confident wrong answer: a
panel with no rows and no explanation, which reads as a bug until you know which
of the four it was.

Rows are read by walking the built widget tree and reading each row's own labels,
never off a list the module stashed on itself. A count read back out of the thing
being counted is the mistake that produced the visual-regression suite
AGENTS.md records as having been deleted: fifteen assertions that all passed
against a window which rendered nothing.

**The stubs stand in at `devices.ph` and `devices.desktop_sources`** - the names
`build()` reads - and not on the real `shani_chronoa.phone`. That is the rule the
package states about where to patch, and it also buys the assertion that matters
most here: the fake counts its calls, so "the gate is shut, so the link was never
asked" is checked against a real count rather than inferred from the text.

`common.adw_ready()` is branched on wherever the two row shapes differ, because
that is the condition `common.row()` itself uses to choose between an
`Adw.ActionRow` (whose `use-markup` is True on libadwaita 1.5) and a plain
`Gtk.Box` of `Gtk.Label`s (which take no markup). The two shapes want opposite
escaping, so a test asserting one behaviour unconditionally would be asserting a
difference that is not there.
"""

from __future__ import annotations

import ast
import signal
import threading
import types
from pathlib import Path
from typing import Any, Iterable, List, Optional

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

import shani_chronoa.gui.surfaces as registry  # noqa: E402
from shani_chronoa import markdown_lite  # noqa: E402
from shani_chronoa import phone as real_phone  # noqa: E402
from shani_chronoa.gui.surfaces import common, devices  # noqa: E402
from shani_chronoa.triggers.common import SIGNAL_OK, SIGNAL_UNAVAILABLE  # noqa: E402

REGISTRY_SOURCE = Path(registry.__file__).resolve()

#: A name carrying all three markup-significant characters. A bare `&` is already
#: enough to make Pango refuse the whole string, which is the point: unescaped,
#: the row renders as *nothing* rather than as a wrong character.
HOSTILE = "Ann & <b>Bob</b> phone"


# --- stubs -----------------------------------------------------------------


class _Granted:
    """A config with the phone gate on."""

    def get_bool(self, key: str, default: bool = False) -> bool:
        return True


class _Shut:
    """A config with the phone gate off - it keeps the caller's default."""

    def get_bool(self, key: str, default: bool = False) -> bool:
        return default


class _Exploding:
    """A config whose permission check cannot answer at all."""

    def get_bool(self, key: str, default: bool = False) -> bool:
        raise RuntimeError("dconf is not answering")


class _FakePhone:
    """`shani_chronoa.phone` at the three seams `build()` reads.

    `calls` records which of them were reached, so "the gate is shut, so nothing
    was asked of the link" is a measurement rather than an inference from text.
    `PhoneUnavailable` is the *real* exception class, because `_paired()` catches
    it by identity off this object's attribute.
    """

    PhoneUnavailable = real_phone.PhoneUnavailable

    def __init__(self, backend: Optional[str] = "kdeconnect",
                 found: Iterable[Any] = (), battery: Any = None,
                 raises: Optional[BaseException] = None,
                 battery_raises: Optional[BaseException] = None) -> None:
        self._backend = backend
        self._found = list(found)
        self._battery = battery
        self._raises = raises
        self._battery_raises = battery_raises
        self.calls: List[str] = []

    def backend(self) -> Optional[str]:
        self.calls.append("backend")
        return self._backend

    def devices(self) -> List[Any]:
        self.calls.append("devices")
        if self._raises is not None:
            raise self._raises
        return list(self._found)

    def battery(self, device: Any) -> Any:
        self.calls.append("battery")
        if self._battery_raises is not None:
            raise self._battery_raises
        return self._battery


class _Signal:
    """A stand-in for `EventSignal`, whose four fields this panel reads some of."""

    def __init__(self, status: str = SIGNAL_OK, detail: str = "") -> None:
        self.status = status
        self.detail = detail
        self.fingerprint = "fp" if status == SIGNAL_OK else None
        self.event = None
        self.payload: Optional[dict] = None


class _FakeSources:
    """`triggers.desktop_sources` at the one seam `build()` reads."""

    def __init__(self, signal: _Signal) -> None:
        self._signal = signal
        self.calls: List[str] = []

    def read_phone(self, source: str, now: Any = None) -> _Signal:
        self.calls.append(source)
        return self._signal


def _device(ident: str, name: str, reachable: bool = True, paired: bool = True) -> Any:
    """A real `phone.Device`, so the panel reads the real attribute names."""
    return real_phone.Device(id=ident, name=name, reachable=reachable, paired=paired)


def _app(config: Any = None) -> Any:
    return types.SimpleNamespace(config=config)


def _install(monkeypatch: pytest.MonkeyPatch, phone: _FakePhone,
             signal: Optional[_Signal] = None) -> _FakeSources:
    """Patch both seams where `build()` reads them, and hand back the sources stub."""
    sources = _FakeSources(signal if signal is not None else _Signal())
    monkeypatch.setattr(devices, "ph", phone)
    monkeypatch.setattr(devices, "desktop_sources", sources)
    return sources


# --- reading the built widget ----------------------------------------------


def _labels(widget: Gtk.Widget) -> List[str]:
    """Every `Gtk.Label`'s text in the built tree, in order."""
    found: List[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            found.append(child.get_text())
        found.extend(_labels(child))
        child = child.get_next_sibling()
    return found


def _marked(widget: Gtk.Widget, css: str) -> List[Gtk.Widget]:
    """Every widget carrying `css`, found by walking the tree."""
    found: List[Gtk.Widget] = []
    if css in widget.get_css_classes():
        found.append(widget)
    child = widget.get_first_child()
    while child is not None:
        found.extend(_marked(child, css))
        child = child.get_next_sibling()
    return found


def _row_title(row: Gtk.Widget) -> str:
    """The row's title as a reader sees it - the rendered label's text.

    Deliberately *not* `Adw.ActionRow.get_title()`: measured on libadwaita 1.5,
    that getter hands back the stored Pango markup (`Ann &amp; <b>Bob</b>`) while
    `get_subtitle()` hands back the parsed text. Comparing a rendered name against
    a stored-markup getter compares two different alphabets and finds nothing.
    Reading labels works for both row shapes and means the same thing in both.
    """
    for text in _labels(row):
        if text.strip():
            return text
    raise AssertionError(f"row has no title text at all: {_labels(row)!r}")


def _row_text(row: Gtk.Widget) -> str:
    """Everything the row shows, title first, one label per line."""
    return "\n".join(_labels(row))


def _device_rows(widget: Gtk.Widget) -> List[Gtk.Widget]:
    """Only the device rows, which carry their own css class."""
    return _marked(widget, devices.DEVICE_CSS)


def _device_row_for(widget: Gtk.Widget, name: str) -> Gtk.Widget:
    for row in _device_rows(widget):
        if _row_title(row) == name:
            return row
    raise AssertionError(
        f"no device row for {name!r}; the panel has "
        f"{[_row_title(r) for r in _device_rows(widget)]!r}"
    )


def _said(widget: Gtk.Widget) -> str:
    """Every piece of text on the page, joined."""
    return "\n".join(_labels(widget))


# --- the module's own contract --------------------------------------------


class TestModuleContract:
    def test_it_exports_what_the_registry_reads(self):
        assert devices.TITLE == "Devices"
        assert devices.ICON == "phone-symbolic"
        assert devices.SECTION == "This machine"
        assert callable(devices.build)

    def test_the_icon_is_a_real_icon_name_on_this_machine(self):
        """A symbolic name nothing ships renders as a blank gap the size of an
        icon, which no assertion on the string can see."""
        roots = [Path("/usr/share/icons"), Path("/usr/local/share/icons")]
        if not any(root.is_dir() for root in roots):
            pytest.skip("no icon theme installed on this machine")
        found = [
            svg
            for root in roots
            if root.is_dir()
            for svg in root.rglob(f"{devices.ICON}.svg")
        ]
        assert found, f"{devices.ICON} is not an icon this machine has"

    def test_the_registry_already_names_this_surface(self):
        """`SURFACE_IDS` is what `all_surfaces()` iterates, so a module nobody
        names there is a module nothing builds.

        Read with `ast` rather than by calling `all_surfaces()`, which imports
        every surface to answer - so one broken panel would turn this into a
        statement about the whole sidebar instead of about this module.
        """
        tree = ast.parse(REGISTRY_SOURCE.read_text(encoding="utf-8"))
        ids = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "SURFACE_IDS"
                for target in node.targets
            ):
                assert isinstance(node.value, ast.Tuple), "SURFACE_IDS must stay a tuple"
                ids = [el.value for el in node.value.elts if isinstance(el, ast.Constant)]
        assert ids is not None, "gui/surfaces/__init__.py no longer defines SURFACE_IDS"
        assert all(isinstance(name, str) for name in ids)
        assert "devices" in ids, (
            "gui/surfaces/__init__.py never names a 'devices' surface, so this "
            f"module is built by nothing that runs: {ids}"
        )
        assert ids.count("devices") == 1, (
            f"'devices' appears {ids.count('devices')} times in SURFACE_IDS; the "
            "sidebar offers one panel per id, so a second entry is a lie"
        )

    def test_its_section_is_one_the_sidebar_knows_a_home_for(self):
        assert devices.SECTION in registry.SECTION_ORDER

    def test_the_honest_states_are_four_and_all_four_are_spelled_out(self):
        """Declared rather than inferred from the wording, so "one of ours" is
        distinguishable from a state this panel invented - and no two states may
        share a headline or a body, which is how one message would end up
        answering for three."""
        expected = {devices.STATE_REFUSED, devices.STATE_NO_LINK,
                    devices.STATE_LINK_DOWN, devices.STATE_NONE_PAIRED}
        assert set(devices.HEADLINE) == expected
        assert set(devices.BODY) == expected
        assert set(devices.STATES) == expected | {devices.STATE_PERMITTED}
        assert len(set(devices.HEADLINE.values())) == len(devices.HEADLINE)
        assert len(set(devices.BODY.values())) == len(devices.BODY)

    def test_the_trigger_gate_is_a_different_permission_from_the_control_gate(self):
        assert devices.TRIGGER_KEY != devices.CONSENT_KEY
        assert devices.TRIGGER_KEY == "phone-sense-enabled"
        assert devices.CONSENT_KEY == "phone-control-enabled"


# --- build ----------------------------------------------------------------


class TestBuild:
    def test_build_returns_a_gtk_widget(self):
        assert isinstance(devices.build(_app(_Granted())), Gtk.Widget)

    def test_a_stub_app_with_nothing_on_it_still_builds(self):
        """No settings is a normal state. An exception here takes the window down
        rather than one panel."""
        assert isinstance(devices.build(types.SimpleNamespace()), Gtk.Widget)
        assert isinstance(devices.build(_app(None)), Gtk.Widget)

    def test_the_real_link_on_this_machine_lands_in_a_declared_state(self):
        """The real `phone.backend()` and the real trigger, nothing stubbed. The
        state this machine reaches has to be one the panel declares - a message it
        cannot account for would mean it rendered something it has no state for.

        Recorded on the dev box this was written on: `phone.backend()` is None
        (no kdeconnect-cli, and gjs present but no GSConnect extension), so the
        no-phone-link state is the one taken. The assertion below is written so
        it still holds on a machine that has a backend.
        """
        widget = devices.build(_app(_Granted()))
        said = _said(widget)
        if real_phone.backend() is None:
            assert devices.HEADLINE[devices.STATE_NO_LINK] in said, said
        else:
            assert said.strip(), "the panel said nothing at all"
        assert devices.HEADLINE[devices.STATE_REFUSED] not in said, (
            "a granted gate rendered the refusal state"
        )


# --- the gate -------------------------------------------------------------


class TestTheGate:
    def test_a_shut_gate_says_so_and_never_asks_the_link(self, monkeypatch):
        """The whole point of the gate: with it shut, `devices()`, `battery()`
        and the trigger are not called at all. That is `skills/phone.py`'s rule
        and the `machine` surface's, and a panel that asked anyway would be doing
        the thing the gate exists to prevent."""
        phone = _FakePhone(found=[_device("abc123", "Pixel 8")])
        sources = _install(monkeypatch, phone)
        widget = devices.build(_app(_Shut()))
        said = _said(widget)
        assert devices.HEADLINE[devices.STATE_REFUSED] in said, said
        assert devices.CONSENT_KEY in said, said
        assert phone.calls == [], f"the link was asked with the gate shut: {phone.calls}"
        assert sources.calls == [], f"the trigger ran with the gate shut: {sources.calls}"
        assert _device_rows(widget) == [], "a device row appeared with the gate shut"

    def test_an_app_with_no_settings_is_not_permitted(self, monkeypatch):
        """A permission that cannot be checked is not a granted one."""
        phone = _FakePhone(found=[_device("abc123", "Pixel 8")])
        _install(monkeypatch, phone)
        widget = devices.build(types.SimpleNamespace())
        assert devices.HEADLINE[devices.STATE_REFUSED] in _said(widget)
        assert phone.calls == [], f"the link was asked with no settings: {phone.calls}"

    def test_a_permission_check_that_raises_is_not_a_grant(self, monkeypatch):
        phone = _FakePhone(found=[_device("abc123", "Pixel 8")])
        _install(monkeypatch, phone)
        widget = devices.build(_app(_Exploding()))
        said = _said(widget)
        assert devices.HEADLINE[devices.STATE_REFUSED] in said, said
        assert "could not be checked" in said, said
        assert phone.calls == [], f"the link was asked after a failed check: {phone.calls}"

    def test_the_negative_control_grants_the_gate_and_the_refusal_goes_away(
        self, monkeypatch
    ):
        """The control for `test_a_shut_gate_says_so_and_never_asks_the_link`, and
        the reason that test can fail.

        `_permission` is swapped - on `devices`, where `build()` reads it - for one
        that grants unconditionally, so the same stubbed link that was refused a
        moment ago is now read for real. If the panel then shows the device rows,
        the refusal was the gate and not some other text that happens to sit on the
        page; if the refusal were still there afterwards, the test above would be
        asserting a string this panel always prints and would prove nothing.

        `monkeypatch.context()` restores the mutation on the way out, and the
        control checks it applied rather than assuming it - a control that cannot
        fail is not a control.
        """
        phone = _FakePhone(found=[_device("abc123", "Pixel 8")])
        _install(monkeypatch, phone)

        with monkeypatch.context() as control:
            control.setattr(
                devices, "_permission",
                lambda _config: devices.State(devices.STATE_PERMITTED),
            )
            widget = devices.build(_app(_Shut()))

            assert devices.HEADLINE[devices.STATE_REFUSED] not in _said(widget), (
                "the control granted the gate and the refusal message is still "
                "there, so the refusal is not coming from _permission and the "
                "assertions above are proving nothing"
            )
            assert [_row_title(r) for r in _device_rows(widget)] == ["Pixel 8"]
            assert "devices" in phone.calls, (
                f"the control granted the gate but never read the link: {phone.calls}"
            )


# --- the rows --------------------------------------------------------------


class TestTheRows:
    def test_a_stubbed_device_list_renders_one_row_per_device(self, monkeypatch):
        found = [
            _device("abc123", "Pixel 8"),
            _device("def456", "Galaxy Tab"),
            _device("ghi789", "Old Nook", reachable=False),
        ]
        _install(monkeypatch, _FakePhone(found=found, battery=(72, False)))
        widget = devices.build(_app(_Granted()))
        rows = _device_rows(widget)
        assert len(rows) == 3, [_row_title(r) for r in rows]
        assert [_row_title(r) for r in rows] == ["Pixel 8", "Galaxy Tab", "Old Nook"]

    def test_each_row_says_its_kind_its_battery_and_when_it_was_read(self, monkeypatch):
        _install(
            monkeypatch,
            _FakePhone(found=[_device("abc123", "Pixel 8")], battery=(72, True)),
        )
        row = _device_row_for(devices.build(_app(_Granted())), "Pixel 8")
        text = _row_text(row)
        assert "kind: paired, reachable right now" in text, text
        assert "battery: 72% and charging" in text, text
        assert "last known at " in text, text
        stamps = _marked(row, devices.WHEN_CSS)
        assert len(stamps) == 1 and stamps[0].get_text(), "the row carries no timestamp"

    def test_an_unreachable_device_is_never_given_a_battery_reading(self, monkeypatch):
        """`skills/phone.py` asks only of a reachable device, and for the same
        reason: an unreachable phone's last battery reading is not a current one.
        A battery shown beside a fresh timestamp makes the timestamp lie."""
        phone = _FakePhone(found=[_device("abc123", "Old Nook", reachable=False)],
                           battery=(9, False))
        _install(monkeypatch, phone)
        row = _device_row_for(devices.build(_app(_Granted())), "Old Nook")
        text = _row_text(row)
        assert "kind: paired, not reachable" in text, text
        assert "not read, the device is not reachable" in text, text
        assert "9%" not in text, text
        assert "battery" not in phone.calls, (
            f"an unreachable device's battery was read anyway: {phone.calls}"
        )

    def test_a_backend_that_reports_no_battery_says_that_rather_than_zero(
        self, monkeypatch
    ):
        """`phone.battery()` returns None on GSConnect and without busctl. No
        reading is not 0%."""
        _install(monkeypatch,
                 _FakePhone(found=[_device("abc123", "Pixel 8")], battery=None))
        row = _device_row_for(devices.build(_app(_Granted())), "Pixel 8")
        text = _row_text(row)
        assert "this backend does not report one" in text, text
        assert "0%" not in text, text

    def test_an_unpaired_device_is_not_listed(self, monkeypatch):
        """`phone.devices()` returns everything the link has ever seen. A row for a
        device the user has unpaired is a device Chronoa may not act on."""
        _install(monkeypatch, _FakePhone(found=[
            _device("abc123", "Pixel 8"),
            _device("def456", "Unpaired Tablet", paired=False),
        ]))
        rows = _device_rows(devices.build(_app(_Granted())))
        assert [_row_title(r) for r in rows] == ["Pixel 8"]

    def test_the_backend_is_named_to_a_person_not_by_the_modules_own_slug(
        self, monkeypatch
    ):
        _install(monkeypatch, _FakePhone(backend="gsconnect",
                                         found=[_device("abc123", "Pixel 8")]))
        said = _said(devices.build(_app(_Granted())))
        assert devices.BACKEND_TITLE["gsconnect"] in said, said
        assert "kdeconnect" not in said, said


# --- the honest states ----------------------------------------------------


class TestTheHonestStates:
    def test_no_backend_installed_is_its_own_message(self, monkeypatch):
        """`phone.backend()` returning None is this machine having no phone link.
        It is emphatically not this machine having no phone."""
        phone = _FakePhone(backend=None)
        _install(monkeypatch, phone)
        widget = devices.build(_app(_Granted()))
        said = _said(widget)
        assert devices.HEADLINE[devices.STATE_NO_LINK] in said, said
        assert "kdeconnect-cli" in said, said
        assert "GSConnect" in said, said
        assert "not a machine with no phone" in said, said
        assert "devices" not in phone.calls, (
            f"devices were asked for with no backend installed: {phone.calls}"
        )

    def test_a_link_that_will_not_answer_shows_the_error_it_gave(self, monkeypatch):
        """`PhoneUnavailable` is `phone.py`'s own "not no phone", and its message
        is the most specific thing available - so it is shown rather than replaced
        by a generic one."""
        _install(monkeypatch, _FakePhone(
            raises=real_phone.PhoneUnavailable(
                "GSConnect is not running (no session bus); it is a GNOME Shell "
                "extension that has to be switched on")))
        said = _said(devices.build(_app(_Granted())))
        assert devices.HEADLINE[devices.STATE_LINK_DOWN] in said, said
        assert "has to be switched on" in said, said

    def test_no_device_paired_is_its_own_message(self, monkeypatch):
        _install(monkeypatch, _FakePhone(found=[]))
        widget = devices.build(_app(_Granted()))
        said = _said(widget)
        assert devices.HEADLINE[devices.STATE_NONE_PAIRED] in said, said
        assert "has nothing paired to it" in said, said
        assert _device_rows(widget) == [], "a row appeared with nothing paired"

    def test_the_four_states_render_as_four_different_pages(self, monkeypatch):
        """The three the panel is judged on, plus the fourth, checked against each
        other. A panel that printed one message for all of them would pass every
        assertion above individually and still be useless, because a reader could
        not tell which case they were in."""
        cases = (
            ("refused", _Shut(), _FakePhone(found=[_device("abc123", "Pixel 8")])),
            ("no link", _Granted(), _FakePhone(backend=None)),
            ("link down", _Granted(), _FakePhone(
                raises=real_phone.PhoneUnavailable("KDE Connect did not answer"))),
            ("none paired", _Granted(), _FakePhone(found=[])),
        )
        rendered = {}
        for label, config, phone in cases:
            with monkeypatch.context() as one:
                _install(one, phone)
                text = _said(devices.build(_app(config)))
            assert text.strip(), f"{label} rendered nothing at all"
            rendered[label] = text
        for label, text in rendered.items():
            for other, other_text in rendered.items():
                if label != other:
                    assert text != other_text, f"{label} and {other} render identically"

    def test_the_trigger_reports_unavailable_in_its_own_words(self, monkeypatch):
        """`status != SIGNAL_OK` is the module's own unavailable contract: a `None`
        fingerprint must not be compared against a real one, because "unavailable"
        and "unchanged" then look alike. So the reason is shown, and never shown as
        "nothing happened"."""
        _install(
            monkeypatch,
            _FakePhone(found=[_device("abc123", "Pixel 8")]),
            _Signal(SIGNAL_UNAVAILABLE, "no phone is paired"),
        )
        said = _said(devices.build(_app(_Granted())))
        assert f"phone:{devices.TRIGGER_SOURCE}" in said, said
        assert "no phone is paired" in said, said

    def test_the_trigger_gate_is_named_on_the_page(self, monkeypatch):
        """`phone-sense-enabled` and `phone-control-enabled` are different
        permissions; a panel that treated one as the other would be reporting a
        capability the user never granted."""
        _install(monkeypatch, _FakePhone(found=[_device("abc123", "Pixel 8")]))
        said = _said(devices.build(_app(_Granted())))
        assert devices.TRIGGER_KEY in said, said


# --- escaping -------------------------------------------------------------


class TestEscaping:
    def test_a_hostile_device_name_is_rendered_as_itself_and_not_as_nothing(
        self, monkeypatch
    ):
        """The failure mode of getting this wrong is not a wrong character: with
        libadwaita, `use-markup` is True, so Pango refuses the whole string and the
        row renders *blank*, with only a Gtk-WARNING to say so. An empty page is
        exactly what this panel is not allowed to ship."""
        _install(monkeypatch, _FakePhone(found=[_device("abc123", HOSTILE)]))
        widget = devices.build(_app(_Granted()))
        assert len(_device_rows(widget)) == 1, _marked(widget, devices.ROW_CSS)
        assert HOSTILE in _labels(widget), _labels(widget)

    @pytest.mark.skipif(not common.adw_ready(),
                        reason="no libadwaita: the fallback row takes no markup")
    def test_with_adw_the_row_carries_entities_and_reports_use_markup(
        self, monkeypatch
    ):
        """`get_title()` returns the stored markup, so this is where the entities
        are visible; the label beside it is where they are shown to have worked.
        The second assertion is the one that would fail if the escaping were
        removed: the raw string renders as a *blank* row, not as a wrong one."""
        _install(monkeypatch, _FakePhone(found=[_device("abc123", HOSTILE)]))
        row = _device_row_for(devices.build(_app(_Granted())), HOSTILE)
        assert row.get_property("use-markup") is True, (
            "if use-markup is ever False the escaping stops being load-bearing and "
            "this test asserts a difference that is not there"
        )
        assert row.get_title() == markdown_lite.escape(HOSTILE), row.get_title()
        assert "&amp;" in row.get_title() and "&lt;b&gt;" in row.get_title()
        assert _labels(row)[0] == HOSTILE, _labels(row)

    @pytest.mark.skipif(not common.adw_ready(),
                        reason="no libadwaita: the Adw row is not built")
    def test_with_adw_the_subtitle_is_escaped_too(self, monkeypatch):
        """The battery line can carry a subprocess's own punctuation, and the
        subtitle is parsed as markup exactly like the title - so unescaped it is
        not rendered as `<failed>` but discarded whole. What must appear is the
        original text, with the angle brackets shown rather than acted on."""
        _install(monkeypatch, _FakePhone(
            found=[_device("abc123", "Pixel 8")],
            battery_raises=OSError("busctl: /org/kde/kdeconnect > & <failed>")))
        row = _device_row_for(devices.build(_app(_Granted())), "Pixel 8")
        shown = _labels(row)
        assert any("<failed>" in text for text in shown), shown
        assert any("&lt;failed&gt;" not in text and "&amp;" not in text
                   for text in shown), shown
        assert not any(text == "" for text in shown[1:2]), f"the subtitle is blank: {shown}"

    @pytest.mark.skipif(common.adw_ready(),
                        reason="libadwaita is present: the Adw path is the real one")
    def test_without_adw_the_text_is_not_escaped(self, monkeypatch):
        """`Gtk.Label(label=...)` takes no markup, so escaping there would put a
        literal `&amp;` on screen. The branch is on `common.adw_ready()` because
        that is the same condition `common.row()` uses to choose the shape."""
        _install(monkeypatch, _FakePhone(found=[_device("abc123", HOSTILE)]))
        widget = devices.build(_app(_Granted()))
        assert HOSTILE in _labels(widget), _labels(widget)
        assert not any("&amp;" in text for text in _labels(widget)), _labels(widget)


# --- what this panel must not do ------------------------------------------


class TestBoundaries:
    def test_it_adds_no_transport_of_its_own(self):
        """Read from the module's imports and calls rather than its prose, with
        `ast` - a text search for "subprocess" or "busctl" matches this panel's
        own *explanations* to the reader ("no kdeconnect-cli", "busctl is absent"),
        which is the opposite of the thing being asserted.

        A panel that grew a subprocess call of its own would still build fine in a
        test. The only subprocesses and D-Bus calls in Chronoa's phone path belong
        to `phone.py`, which this panel reads rather than reimplementing.
        """
        tree = ast.parse(Path(devices.__file__).read_text(encoding="utf-8"))
        imported: set = set()
        called: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Call):
                func = node.func
                called.add(func.attr if isinstance(func, ast.Attribute)
                           else func.id if isinstance(func, ast.Name) else "")

        for name in sorted(imported):
            root = name.split(".")[0]
            assert root not in ("subprocess", "shutil", "socket", "dbus", "asyncio"), (
                f"this module imports {name!r}; it must read phone.py and add no "
                "transport of its own"
            )
        for name in ("Popen", "check_output", "check_call", "bus_get_sync",
                     "call_sync", "spawn_sync"):
            assert name not in called, (
                f"this module calls {name}(); that is a transport of its own, and "
                "the phone link belongs to phone.py"
            )

    def test_the_only_phone_module_it_reaches_is_phone_py(self):
        """`devices.ph` is `shani_chronoa.phone`. If it were ever repointed at a
        second module, this panel would be reading a different phone path from the
        one the `phone` skill and trigger use - which is the thing this panel
        promises not to be."""
        assert devices.ph.__name__ == "shani_chronoa.phone"
        assert devices.ph is real_phone

    def test_it_starts_no_thread_and_arms_no_timer(self, monkeypatch):
        timer_before = signal.getitimer(signal.ITIMER_REAL)
        handler_before = signal.getsignal(signal.SIGALRM)
        threads_before = threading.active_count()
        _install(monkeypatch, _FakePhone(found=[_device("abc123", "Pixel 8")]))
        devices.build(_app(_Granted()))
        assert signal.getitimer(signal.ITIMER_REAL) == timer_before
        assert signal.getsignal(signal.SIGALRM) is handler_before
        assert threading.active_count() == threads_before

    def test_it_grants_no_permission_by_being_looked_at(self, monkeypatch):
        """Reading the gate must never write it. A panel that set the key as a side
        effect of opening would grant a permission by being rendered."""
        writes: List[str] = []

        class _Writes(_Shut):
            def set(self, key: str, value: Any) -> None:
                writes.append(f"{key}={value}")

        _install(monkeypatch, _FakePhone(found=[_device("abc123", "Pixel 8")]))
        devices.build(_app(_Writes()))
        assert writes == [], f"opening the panel wrote a permission: {writes}"