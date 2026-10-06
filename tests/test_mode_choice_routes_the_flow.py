"""Choosing "In the cloud" on the Mode page must take the wizard to Cloud keys.

**The Mode page asked the question and threw the answer away.** Measured on the
real wizard - build it, press Start, flip the radio to "In the cloud", press
Next:

    RESULT radios on mode page: 2
    RESULT flipped the inactive radio
    RESULT pressing: Next: Brain          <- label unchanged
    RESULT after Next: brain              <- the 1.1 GB local download page

The cause is one line. `navigate()` took the destination as a string and bound
it into the button's handler when the page was built:

    navigate(box, "cloud-keys" if chosen_mode() == "cloud" else "brain", ...)
    ...
    nxt.connect("clicked", lambda *_: goto(next_tag))

`build_window` constructs the Mode page before anyone can toggle a radio on it,
so `chosen_mode()` is read once, at build time, and is never read again. Every
other page's destination is fixed by the page, so a captured string is right for
all of them; the Mode page is the one page whose destination *is* the answer to
its own question.

That makes this the specific failure the page's own comment says it exists to
prevent, three lines above the wiring that caused it:

> **This question comes before the model list, not after it.** The whole point of
> a cloud model is that there is nothing to download, so offering "1.1 GB of
> Qwen" to someone who intends to use Claude was asking them to pay for the
> answer to a question they had not been asked.

The question was asked. The answer was recorded in `setup-mode`, the button said
"Next: Brain", and the person who said *In the cloud* was sent to the local model
download anyway. The cloud branch was reachable only by choosing cloud *before*
opening the window - i.e. by not being asked.

`navigate` now takes a callable and resolves it on the press, and the Mode page
re-titles the button on `toggled` so the label cannot disagree with where the
press goes. Both halves are asserted here, because a button that goes to
`cloud-keys` while still reading "Next: Brain" is the same defect wearing a
different hat: this file has shipped a fix that would have satisfied a test
checking only the destination.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import setup_wizard  # noqa: E402

_SETTLE_MS = 3000
_MOVE_MS = 1500


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _view(window):
    for widget in _walk(window):
        if isinstance(widget, Adw.NavigationView):
            return widget
    return None


def _tag(window):
    view = _view(window)
    return None if view is None else view.get_visible_page().get_tag()


def _buttons(page, prefix=""):
    return [w for w in _walk(page)
            if isinstance(w, Gtk.Button)
            and (w.get_label() or "").startswith(prefix)]


def _radios(page):
    return [w for w in _walk(page) if isinstance(w, Gtk.CheckButton)]


def _run(steps):
    """Drive the real wizard through `steps`, one dict per main-loop turn.

    Each step gets `(window, page)` and may schedule more work with
    `GLib.timeout_add`; the run ends when the last one has been applied.
    """
    app = Gtk.Application(
        application_id="test.chronoa.modechoice",
        flags=Gio.ApplicationFlags.NON_UNIQUE)
    box = {}

    def finish(value=None):
        box["result"] = value
        app.quit()
        return False

    def step(index):
        page = _view(box["window"]).get_visible_page()
        steps[index](box["window"], page, finish)
        return False

    def build(a):
        box["window"] = setup_wizard.build_window(a)
        box["window"].present()
        GLib.timeout_add(_SETTLE_MS, step, 0)
        return False

    def timeout():
        finish("TIMED OUT")
        return False

    GLib.timeout_add(50, build, app)
    GLib.timeout_add(_SETTLE_MS + 12000, timeout)
    app.hold()
    try:
        app.run(["modechoice-probe"])
    finally:
        app.release()
    assert box.get("result") != "TIMED OUT", (
        "the wizard harness never finished - a step did not call `finish`")
    return box["result"]


def _to_mode(window, page, done):
    """Press Start, then flip to the second radio, then press Next."""
    assert _tag(window) == "welcome", (
        f"the wizard opened on {_tag(window)!r}, not the welcome page")

    def press_start():
        starts = _buttons(page, "Start")
        assert len(starts) == 1, (
            f"the welcome page has {len(starts)} buttons reading 'Start'")
        starts[0].emit("clicked")

        def pick_cloud():
            assert _tag(window) == "mode", (
                f"Start led to {_tag(window)!r}, not the mode page")
            radios = _radios(_view(window).get_visible_page())
            assert len(radios) == 2, (
                f"the mode page offers {len(radios)} choices, not the two its "
                "own text describes ('On this computer', 'In the cloud')")
            for radio in radios:
                if not radio.get_active():
                    radio.set_active(True)
                    break
            else:
                raise AssertionError("both mode radios are already active")

            def press_next():
                page2 = _view(window).get_visible_page()
                assert _tag(window) == "mode", (
                    f"choosing a mode left the wizard on {_tag(window)!r}")
                nxt = _buttons(page2, "Next")
                assert len(nxt) == 1, (
                    f"the mode page has {len(nxt)} buttons reading 'Next...'")
                _LABEL["next"] = nxt[0].get_label()
                nxt[0].emit("clicked")

                def read_landing():
                    done(_tag(window))

                GLib.timeout_add(_MOVE_MS, read_landing)

            GLib.timeout_add(600, press_next)

        GLib.timeout_add(1200, pick_cloud)

    GLib.timeout_add(300, press_start)


#: What the Next button called itself at the moment it was pressed. Module-level
#: because the press happens inside a main-loop callback and cannot return it.
_LABEL = {}


def test_choosing_the_cloud_routes_to_cloud_keys():
    """The destination, measured by pressing Next after choosing the cloud."""
    _LABEL.clear()
    landed = _run([_to_mode])
    assert landed == "cloud-keys", (
        "choosing 'In the cloud' and pressing Next led to "
        f"{landed!r}, not the Cloud keys page. The mode page's destination was "
        "decided when the page was built, before the question could be answered.")


def test_the_next_button_names_where_the_cloud_choice_goes():
    """The label and the destination must not be able to disagree.

    A button that reads "Next: Brain" and goes to Cloud keys is the same defect
    as the one being fixed: it answers the reader's question wrongly, and
    `test_choosing_the_cloud_routes_to_cloud_keys` alone would pass on it. The
    reverse - a label that says Cloud keys while the press goes to `brain` - is
    the original bug with the symptom moved, so both are asserted together here.
    """
    _LABEL.clear()
    _run([_to_mode])
    label = _LABEL.get("next")
    assert label == "Next: Cloud keys", (
        f"after choosing 'In the cloud' the button still read {label!r}. A "
        "person reading 'Next: Brain' is being told the opposite of where they "
        "are about to go - which is the sentence the page exists to get right.")