"""The egress panel has to say *why* a request left the machine.

"Recent network activity" in `gui/surfaces/privacy.py` rendered

    {method} {url} - {bytes_out} bytes

which is accurate about bytes and **silent about the only part that matters**.
`egress.record` has carried a `purpose` since model downloads (`model-download`,
so an audit could tell a consented fetch from something else), and `cloud_voice`
writes `speech-recognition` and `speech-synthesis` - so an upload of a recording
read as

    POST https://api.openai.com/v1/audio/transcriptions - 53312 bytes

and that looks like a data POST, not like the voice of the person sitting at the
machine. The person opening this panel is asking one question - *what left?* -
and for the newest kind of egress the panel answered with a URL and a byte count.

**The byte count is not enough, and it is worth being precise about why.** It is
the kind of number that reads as complete. 53,312 bytes of anything looks like a
request; 53,312 bytes of *16 kHz mono PCM* is fourteen seconds of somebody
speaking. The panel cannot tell the difference, because `payload_size` is a
length and a length does not say what was measured. Only the purpose does.

Two things this deliberately does **not** do:

- it does not invent a duration. Deriving seconds from a byte count needs a
  sample rate and a channel count, and guessing either would be exactly the
  confident-wrong-answer this panel exists to avoid. The panel says what the
  request was *for* and leaves the arithmetic to whoever wants it.
- it does not fail when `purpose` is absent. Events written before this field
  existed, and by callers that have no purpose to give, render exactly as before -
  an addition that made old rows unreadable would be a worse regression than the
  one it fixes.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import egress  # noqa: E402
from shani_chronoa.gui.surfaces import privacy as surface  # noqa: E402


def test_the_egress_panel_shows_why_a_request_left_the_machine():
    """**The audit panel is where a person checks what left, and it dropped the reason.**

    `gui/surfaces/privacy.py`'s "Recent network activity" group rendered
    `{method} {url} - {bytes_out} bytes`. `egress.record` has carried a `purpose`
    since model downloads, and `cloud_voice` writes `speech-recognition` and
    `speech-synthesis` - so an upload of a recording read as

        POST https://api.openai.com/v1/audio/transcriptions - 53312 bytes

    which looks like a data POST and not like the voice of the person sitting at
    the machine. The row was accurate about bytes and silent about the only part
    that matters. Asserted on the built panel, with the real `egress.record`
    call, because a source-level check would pass on the old code.
    """

    recorded = []
    original = egress.record
    egress.record = lambda *a, **k: recorded.append((a, k))
    try:
        egress.record("cloud_stt:openai", "https://api.openai.com/v1/audio/transcriptions",
                      method="POST", status=200, bytes_out=53312,
                      privacy_mode=False, purpose="speech-recognition")
    finally:
        egress.record = original
    assert recorded and recorded[0][1]["purpose"] == "speech-recognition"

    # Now the panel: a fake `_recent_events` returning what was just recorded.
    events = [dict(zip(
        ("component", "url", "host", "method", "status", "bytes_out",
         "privacy_mode", "purpose"),
        ("cloud_stt:openai", "https://api.openai.com/v1/audio/transcriptions",
         "api.openai.com", "POST", 200, 53312, False, "speech-recognition")))]


    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(surface, "_recent_events", lambda: events)
        ctx.setattr(egress, "summary", lambda: {"log": "/tmp/egress.jsonl"})
        ctx.setattr(egress, "privacy_mode_enabled", lambda: False)
        texts = _rendered_rows(surface)

    assert _egress_row(texts, "api.openai.com") == (
        "speech-recognition: POST "
        "https://api.openai.com/v1/audio/transcriptions - 53312 bytes"), texts


class _Surface:
    """The minimum an `Adw.PreferencesPage` is, for walking the built rows."""
    def __init__(self):
        gi.require_version("Adw", "1")
        self._box = Gtk.Box()
        self._rows = []

    def add(self, widget):
        self._rows.append(widget)
        self._box.append(widget)


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _egress_row(rows, host_fragment):
    """The one row describing the request to `host_fragment`.

    The panel renders privacy mode, the switches and every consent row too, so
    "the rendered text contains X" is the wrong shape of assertion here - and it
    is the shape that let a prefix mutation through.
    """
    found = [r for r in rows if host_fragment in r]
    assert len(found) == 1, (
        f"expected exactly one row about {host_fragment!r}, found {len(found)} "
        f"in {rows!r}")
    return found[0]


def _rendered_rows(surface_module):
    """Every row subtitle the privacy surface actually renders.

    Through the **real** `surface.build(app)` - it takes one argument and returns
    the widget itself - because a source-level assertion would pass on the code
    this file exists to fail: the old renderer had no `purpose` in it at all, and
    a grep for the word would have found nothing.

    **No skip.** A first version stubbed the page object and skipped when the
    build raised, which is the failure this repository documents twice: a skip
    reads as coverage and is not. `build()` needs only `app.config`, so it is
    given a real `Adw` surface and a real config stub.
    """
    import types

    class _Config:
        """The three things the privacy surface reads off a config.

        `sense_allowed` is here because the per-sense consent rows ask it, and a
        stub without it fails the build rather than skipping - which is the
        behaviour this file wants.
        """

        privacy_mode = False
        cloud_stt_enabled = False
        cloud_tts_enabled = False
        input_control_enabled = False

        def get_bool(self, key, default=False):
            return getattr(self, key, default)

        def sense_allowed(self, name):
            return False

    app = types.SimpleNamespace(config=_Config())
    widget = surface_module.build(app)
    return [w.get_subtitle() or "" for w in _walk(widget)
            if isinstance(w, Gtk.ListBoxRow)]


def test_an_event_with_no_purpose_renders_exactly_as_before():
    """An addition that made old rows unreadable would be the worse regression.

    Events written before `purpose` existed, and callers that have none to give,
    must render unchanged.

    **Equality on the row, not a substring of the joined text.** A first version
    asserted `"GET ... - 12 bytes" in texts`, and a mutation that prefixed every
    row with `request: ` still satisfied it - because the prefix goes *before* the
    string being looked for. The mutation ran green, which is the only reason this
    line reads the way it does.
    """
    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(egress, "summary", lambda: {"log": "/tmp/egress.jsonl"})
        ctx.setattr(egress, "privacy_mode_enabled", lambda: True)
        ctx.setattr(surface, "_recent_events", lambda: [{
            "url": "https://example.invalid/thing", "host": "example.invalid",
            "method": "GET", "status": 200, "bytes_out": 12, "purpose": ""}])
        texts = _rendered_rows(surface)
    assert _egress_row(texts, "example.invalid") == (
        "GET https://example.invalid/thing - 12 bytes"), texts


def test_a_model_download_names_its_purpose_too():
    """`purpose` predates this change, so it was already being recorded unwatched."""
    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(egress, "summary", lambda: {"log": "/tmp/egress.jsonl"})
        ctx.setattr(egress, "privacy_mode_enabled", lambda: True)
        ctx.setattr(surface, "_recent_events", lambda: [{
            "url": "https://huggingface.co/model.gguf", "host": "huggingface.co",
            "method": "GET", "status": 200, "bytes_out": 1105926934,
            "purpose": "model-download"}])
        rows = _rendered_rows(surface)
    assert _egress_row(rows, "huggingface.co") == (
        "model-download: GET https://huggingface.co/model.gguf "
        "- 1105926934 bytes"), rows



def test_a_browser_navigation_is_recorded_with_a_purpose():
    """The other egress site, and the reason the panel change was worth making.

    `gui/browser.py`'s `_record_navigation` wrote an event with no `purpose`, so a
    page the person opened rendered in the audit panel as a bare
    `GET https://example.com/` - indistinguishable, in the one panel that answers
    *what left this machine?*, from a speech upload or a model fetch.

    Driven through the real method, because `browser.py` needs a live WebKit view
    and the function itself is the unit that matters.
    """
    import types

    from shani_chronoa.gui import browser as browser_mod

    recorded = []
    original = egress.record
    egress.record = lambda *a, **k: recorded.append((a, k))
    try:
        view = types.SimpleNamespace()
        browser_mod.BrowserWindow._record_navigation(view, "https://example.com/page")
    finally:
        egress.record = original

    assert recorded, "the navigation was not recorded at all"
    args, kwargs = recorded[0]
    assert args[0] == "browser:navigate", args
    assert kwargs.get("purpose") == "page-navigation", (
        f"a navigation is recorded without a purpose, so the audit panel cannot "
        f"tell it from anything else: {kwargs}")
    assert kwargs["privacy_mode"] is True, (
        "privacy mode must be read at the call site - omitted, it computes "
        "violation=False structurally, which is how webtext.retrieve was "
        "uninstrumented while looking instrumented")


def test_a_local_file_is_still_not_recorded_at_all():
    """Unchanged on purpose: an alarm that fires for local files is one nobody reads."""
    import types

    from shani_chronoa.gui import browser as browser_mod

    recorded = []
    original = egress.record
    egress.record = lambda *a, **k: recorded.append((a, k))
    try:
        view = types.SimpleNamespace()
        browser_mod.BrowserWindow._record_navigation(view, "file:///home/me/page.html")
    finally:
        egress.record = original
    assert recorded == [], (
        f"a file:// page was recorded as a remote request: {recorded}")
