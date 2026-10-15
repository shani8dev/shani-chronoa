"""The Listening surface: that it says the right things, and how it got there.

**This file exists because the verification was throwaway.** The surface was
rendered and read through a heredoc in `/tmp`, which is lost when the session
ends - and `AGENTS.md` says a check that does not exist is a check no later agent
runs. Everything proved by that render is re-proved here, plus the two states it
could not reach.

**A page is not verified by building it.** The four rows below are the answers;
their *wording* is the product, and a row reading "not checked yet" is the same
page reading nothing at all.
"""

import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# The reading half, which needs no display at all
# ---------------------------------------------------------------------------


class _Stream:
    def __init__(self, name, label, recordable=True):
        self.name, self.label, self.recordable = name, label, recordable

    def __repr__(self):
        return "<%s %s recordable=%s>" % (type(self).__name__, self.name,
                                          self.recordable)


def test_no_pipewire_is_a_named_absence_not_an_empty_list(monkeypatch):
    """`list_streams()` returning [] because there is no graph is a *broken*
    machine; [] because nothing is playing is a *quiet* one. They read the same
    and must be told apart."""
    from shani_chronoa.gui.surfaces import listening

    monkeypatch.setattr("shani_chronoa.pipewire.is_available", lambda: False)
    streams, problem = listening._what_is_playing()

    assert streams == [] and "pw-dump" in problem, (streams, problem)


def test_a_broken_graph_is_not_a_quiet_machine(monkeypatch):
    from shani_chronoa.gui.surfaces import listening

    def explodes():
        raise RuntimeError("the daemon died")

    monkeypatch.setattr("shani_chronoa.pipewire.is_available", lambda: True)
    monkeypatch.setattr("shani_chronoa.pipewire.list_streams", explodes)

    streams, problem = listening._what_is_playing()

    assert problem and "RuntimeError" in problem, problem
    assert streams == []


def test_consent_off_names_the_switch_not_a_failure(monkeypatch):
    """A person reading "not allowed" cannot act; the ones who can are told
    which setting it is."""
    from shani_chronoa.gui.surfaces import listening

    class _Cfg:
        def sense_allowed(self, sense):
            return False

    monkeypatch.setattr("shani_chronoa.config.ChronoaConfig", lambda: _Cfg())
    said, ok = listening._consent()

    assert ok is False
    assert "heard-sound" in said or "Chronoa name a sound" in said, said


def test_the_sandbox_caveat_is_derived_not_asserted(monkeypatch):
    """**The clause that nearly shipped a false reassurance.**

    The page would otherwise show `Transcription: whisper.cpp, model ...` with a
    green tick on a machine where `recording action=capture` segfaults: whisper
    cannot mmap its model under the skill sandbox's address-space ceiling,
    measured both ways - no limit, `brown fox jump over the lazy dog` rc=0;
    `ulimit -v 524288`, segfault.

    The number is read off the profile, so a hardcoded "512 MB" is a mutation
    this test catches.
    """
    from shani_chronoa.gui.surfaces import listening

    caveat = listening._sandbox_caveat()

    assert caveat, "no caveat at all, so the page reassures unconditionally"
    assert "address-space" in caveat, caveat
    assert "MB" in caveat, caveat
    assert "app itself is not under it" in caveat, (
        "the sentence must say which of the two it is talking about: %r" % caveat)


def test_the_caveat_follows_a_change_to_the_profile(monkeypatch):
    """The control for the above: a different ceiling must change the sentence,
    or the number is decoration."""
    from shani_chronoa.gui.surfaces import listening
    from shani_chronoa.sandbox import profiles

    class _Profile:
        def ceiling(self, _t):
            return type("C", (), {"memory_bytes": 64 * 1024 * 1024})()

    monkeypatch.setattr(profiles, "profile_for_origin", lambda origin: _Profile())
    assert "64 MB" in listening._sandbox_caveat()


def test_an_unreadable_profile_says_nothing_rather_than_inventing_one(monkeypatch):
    from shani_chronoa.gui.surfaces import listening
    from shani_chronoa.sandbox import profiles

    def explodes(_o):
        raise RuntimeError("no profile")

    monkeypatch.setattr(profiles, "profile_for_origin", explodes)
    assert listening._sandbox_caveat() == "", (
        "an unreadable ceiling produced a sentence with a number in it")


def test_transcription_names_the_two_failures_apart(monkeypatch):
    """`whisper_model()` raises one type for two very different problems - no
    binary, and no downloaded model - and sending somebody to the wrong one is
    how they reinstall a package they already have."""
    from shani_chronoa.gui.surfaces import listening

    class _NoModel(Exception):
        pass

    def raises(*_a, **_k):
        raise _NoModel("no speech model is installed (Chronoa's setup, Ears)")

    monkeypatch.setattr("shani_chronoa.recordings.whisper_model", raises)
    said, ok = listening._can_transcribe()

    assert ok is False
    assert "no speech model" in said, said


def test_the_model_found_is_named_with_its_file(monkeypatch):
    from shani_chronoa.gui.surfaces import listening

    monkeypatch.setattr(
        "shani_chronoa.recordings.whisper_model",
        lambda: ("/usr/bin/whisper-cli",
                 "/home/u/.local/share/whisper/models/ggml-base-q5_1.bin"))

    said, ok = listening._can_transcribe()

    assert ok is True
    assert "whisper-cli" in said and "ggml-base-q5_1.bin" in said, said


# ---------------------------------------------------------------------------
# The page itself, where a window can be built
# ---------------------------------------------------------------------------


def _labels(widget, acc=None):
    acc = [] if acc is None else acc
    if isinstance(widget, _Gtk.Label) and widget.get_label():
        acc.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        _labels(child, acc)
        child = child.get_next_sibling()
    return acc


try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gdk, Gtk as _Gtk

    _can_build = Gdk.Display.get_default() is not None
except Exception:  # noqa: BLE001 - no GTK is not a broken page
    _can_build = False
    _Gtk = None


def _settled(page):
    """The labels **after** the page's own thread has answered.

    `build()` starts a `threading.Thread` and updates from `GLib.idle_add`, so
    reading the labels straight after `build()` returns reads "Reading the audio
    graph…" - the page *asking* rather than the page *answering*. Measured: the
    first version of this test asserted on exactly that and failed.
    """
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.05)
    return " | ".join(_labels(page))


@pytest.mark.skipif(not _can_build, reason="no display to build a widget tree in")
def test_the_page_builds_and_asks_for_everything_it_needs():
    """**Constructing a widget is not the same as it saying anything.**"""
    from shani_chronoa.gui.surfaces import listening

    page = listening.build(None)
    said = _settled(page)
    for expected in ("Transcribe what's playing", "Playing now", "Listen for",
                     "Start transcribing", "Transcribe a recording file", "Details"):
        assert expected in said, "%r missing from the built page: %r" % (expected, said)


def _quiet_prereqs(monkeypatch, listening):
    monkeypatch.setattr(listening, "_can_transcribe", lambda: ("whisper.cpp", True))
    monkeypatch.setattr(listening, "_consent", lambda: ("Allowed", True))


@pytest.mark.skipif(not _can_build, reason="no display to build a widget tree in")
def test_a_quiet_machine_is_reported_as_quiet(monkeypatch):
    from shani_chronoa.gui.surfaces import listening

    monkeypatch.setattr("shani_chronoa.pipewire.list_streams", lambda: [])
    _quiet_prereqs(monkeypatch, listening)
    said = _settled(listening.build(None))
    assert "Nothing is playing right now" in said, said
    assert "quiet machine" in said, said


@pytest.mark.skipif(not _can_build, reason="no display to build a widget tree in")
def test_a_capturable_stream_is_offered_with_its_capture_name(monkeypatch):
    """`media.name` is what a person recognises and `node.name` is what
    `pw-record --target` takes - the row shows both."""
    from shani_chronoa.gui.surfaces import listening

    monkeypatch.setattr("shani_chronoa.pipewire.list_streams",
                        lambda: [_Stream("Google Chrome", "Playback", True)])
    _quiet_prereqs(monkeypatch, listening)
    page = listening.build(None)
    said = _settled(page)
    titles = _row_texts(page)
    assert "Playback" in titles and "Google Chrome" in titles, titles
    assert "Pick what to transcribe" not in said, "one stream is chosen without asking"


@pytest.mark.skipif(not _can_build, reason="no display to build a widget tree in")
def test_an_applications_microphone_is_not_offered_as_a_target(monkeypatch):
    from shani_chronoa.gui.surfaces import listening

    monkeypatch.setattr("shani_chronoa.pipewire.list_streams",
                        lambda: [_Stream("chromium", "Audio input", False)])
    _quiet_prereqs(monkeypatch, listening)
    page = listening.build(None)
    said = _settled(page)
    assert "own microphone cannot be captured" in said, said
    assert "chromium" not in _row_texts(page), "a microphone was offered as a target"


@pytest.mark.skipif(not _can_build, reason="no display to build a widget tree in")
def test_start_names_the_stream_it_was_given(monkeypatch):
    """The old button sent only `seconds`, so with two things playing the
    skill refused every time ("say which one with stream=")."""
    from shani_chronoa.gui.surfaces import listening
    from shani_chronoa import tools

    monkeypatch.setattr("shani_chronoa.pipewire.list_streams",
                        lambda: [_Stream("Google Chrome", "Playback", True),
                                 _Stream("Firefox", "Playback", True)])
    _quiet_prereqs(monkeypatch, listening)
    sent = []
    monkeypatch.setattr(tools, "execute_tool", lambda name, args, **k: sent.append(args) or "said")
    page = listening.build(None)
    _settled(page)
    radios = _widgets(page, _Gtk.CheckButton)
    assert len(radios) == 2
    radios[1].set_active(True)
    start = [b for b in _widgets(page, _Gtk.Button) if b.get_label() == "Start transcribing"][0]
    assert start.get_sensitive()
    start.emit("clicked")
    _settled(page)
    assert sent and sent[0]["stream"] == "Firefox", sent


def _widgets(widget, kind, acc=None):
    acc = [] if acc is None else acc
    if isinstance(widget, kind):
        acc.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _widgets(child, kind, acc)
        child = child.get_next_sibling()
    return acc


def _row_texts(page):
    from gi.repository import Adw
    return [t for r in _widgets(page, Adw.ActionRow) for t in (r.get_title(), r.get_subtitle()) if t]
