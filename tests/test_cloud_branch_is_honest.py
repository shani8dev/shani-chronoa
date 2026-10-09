"""Choosing "In the cloud" in setup must not quietly cost 1.3 GB of downloads.

Three defects, all in the wizard's cloud branch, all found by walking the real
window and reading what it said rather than by reading the code.

**One: the question was asked and the answer discarded.** The Mode page is the
one page whose destination *is* the answer to its own question, and `navigate()`
took the destination as a string bound into the button's handler when the page
was built - before anyone could toggle a radio on it. Measured: choose "In the
cloud", press Next, land on `brain`, the page offering a 1.1 GB local download.
The comment three lines above the wiring says the whole point of asking first is
that "offering 1.1 GB of Qwen to someone who intends to use Claude was asking
them to pay for the answer to a question they had not been asked". The question
was asked; the answer was written to `setup-mode` and ignored.
(`test_mode_choice_routes_the_flow.py` covers that one; it is here because this
is what it cost.)

**Two: the cloud branch skipped Ears and Voice entirely.** `cloud-keys` navigated
to `done`, so the flow was `welcome -> mode -> cloud-keys -> done` and then
`on_finish` wrote `setup-complete=true` unconditionally - after which
`needs_setup()` returns False forever. So the person who deliberately chose the
cloud got a Chronoa that could think and **could not hear**, and was never asked.
"nothing to download" was literally true and practically misleading: it was true
because the wizard had stopped asking.

**Three: the Ears page showed nothing and still charged 60 MB.** In cloud mode
the model list was built as `[]`, so the choice group rendered no rows - and then
the same `else` branch ran anyway and queued the recommended model's download.
Measured with `setup-mode=cloud`: the Ears page listed **zero rows**, and the
Review page then read **"Download 3 thing(s) ... 1.3 GB across 3 download(s)"**,
one of which was a 60 MB whisper model there was no row to see, choose or decline.

Three is the one that earns the file. A charge with no row in front of it is not
a choice, and the Review page is the page whose own subtitle promises the
opposite - *"Everything you picked, and nothing you did not."*
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

from shani_chronoa import cloud_voice, setup_wizard  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402

_SETTLE_MS = 3000


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


def _open(config, after, ready=None):
    """Build the real wizard on `config`, then hand it to `after(window, done)`."""
    app = Gtk.Application(application_id="test.chronoa.cloudbranch",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    box = {}

    def settled(window):
        return _view(window) is not None

    def build(a):
        box["window"] = setup_wizard.build_window(a, config)
        box["window"].present()
        GLib.timeout_add(_SETTLE_MS, poll)
        return False

    def poll():
        if _view(box.get("window")) is not None and (ready or settled)(box["window"]):
            try:
                after(box["window"], done)
            except BaseException as exc:                # noqa: BLE001
                done(exc)
            return False
        GLib.timeout_add(100, poll)
        return False

    def done(result=None):
        if isinstance(result, BaseException):            # noqa: BLE001
            box["error"] = result
        else:
            box["result"] = result
        app.quit()
        return False

    GLib.timeout_add(50, build, app)
    GLib.timeout_add(_SETTLE_MS + 12000,
                     lambda: (box.setdefault("error", AssertionError("timed out")),
                              app.quit(), False)[2])
    app.hold()
    try:
        app.run(["cloudbranch-probe"])
    finally:
        app.release()
    if "error" in box:
        raise box["error"]
    return box["result"]


@pytest.fixture
def cloud_config():
    """A config whose `setup-mode` really reads `cloud`.

    Stated explicitly rather than left to whatever the machine's dconf holds: a
    test that reads `local` here is testing the local branch and calling it the
    cloud one, which is a green suite over the wrong question.
    """
    config = ChronoaConfig()
    config.set("setup-mode", "cloud")
    assert config.get("setup-mode", "") == "cloud", (
        "the schema's `setup-mode` could not be written, so this test would be "
        "exercising the local branch and reporting it as the cloud one. "
        "(A stale *compiled* schema is the usual cause: `setup-mode` is in the "
        "XML but not in an installed gschemas.compiled.)")
    return config


def _cloud_stt_gate(enabled: bool):
    """Make `CloudSTT.is_available()` a known answer, without touching the network."""
    original = cloud_voice._read_switch, cloud_voice._provider_key
    cloud_voice._read_switch = lambda name, config=None: enabled
    cloud_voice._provider_key = lambda pid, api_keys=None, config=None: "sk-test"
    return original


def _page_report(window, tag):
    view = _view(window)
    view.push_by_tag(tag)
    page = view.get_visible_page()
    rows = [w for w in _walk(page) if isinstance(w, Adw.ActionRow)]
    return {
        "tag": page.get_tag(),
        "rows": [w.get_title() for w in rows],
        # **Subtitles too, not just labels.** A first version of this collected
        # only `Gtk.Label`s and reported that the model rows "do not state their
        # size" - when the size is the row's *subtitle*, which is where
        # `choice_group` puts it. A check that only looks in one place is a check
        # that has already decided what the answer is.
        "text": " ".join(
            [w.get_label() for w in _walk(page)
             if isinstance(w, Gtk.Label) and w.get_label()]
            + [w.get_subtitle() or "" for w in rows]),
        "buttons": [w.get_label() for w in _walk(page)
                    if isinstance(w, Gtk.Button) and w.get_label()],
    }


def test_the_ears_page_shows_a_model_before_it_charges_for_one(cloud_config):
    """The silent 60 MB. Every queued download must have a row in front of it."""
    def after(window, done):
        page = _page_report(window, "ears")
        GLib.timeout_add(1200, lambda: done(page))

    original = _cloud_stt_gate(False)
    try:
        page = _open(cloud_config, after)
    finally:
        cloud_voice._read_switch, cloud_voice._provider_key = original

    assert page["rows"], (
        "the Ears page lists no models in cloud mode, yet it still queues a "
        "download - a charge with no row in front of it is not a choice, and "
        "the Review page promises 'everything you picked, and nothing you did not'")
    # And the row carries the size that will be charged.
    assert "MB" in page["text"], (
        f"the model rows do not state the size they will be charged: "
        f"{page['rows']}")
    assert any((b or "").startswith("Next") for b in page["buttons"]), (
        f"the Ears page has no way forward: {page['buttons']}")


def test_the_ears_page_says_plainly_that_cloud_voice_is_not_on(cloud_config):
    """Silence is not the same answer as "no", and this page used to give silence."""
    def after(window, done):
        page = _page_report(window, "ears")
        GLib.timeout_add(1200, lambda: done(page))

    original = _cloud_stt_gate(False)
    try:
        page = _open(cloud_config, after)
    finally:
        cloud_voice._read_switch, cloud_voice._provider_key = original

    lowered = page["text"].lower()
    assert "cloud" in lowered and "no cloud speech recognition" in lowered, (
        "a person who chose the cloud branch is told nothing about cloud speech "
        "recognition being off, so 'nothing to download' and '60 MB' both go "
        f"unmentioned. The page said: {page['text'][:220]!r}")


def _review_text(window):
    view = _view(window)
    view.push_by_tag("review")
    page = view.get_visible_page()
    return [w.get_label() for w in _walk(page)
            if isinstance(w, Gtk.Label) and w.get_label()]


def test_the_cloud_branch_still_asks_about_hearing_and_speaking(cloud_config):
    """**Walk the real route, because pushing pages bypasses exactly this.**

    `cloud-keys` used to navigate to `done`, so the flow was
    `welcome -> mode -> cloud-keys -> done`, and `on_finish` then wrote
    `setup-complete=true` - after which `needs_setup()` returns False forever.
    The result was a Chronoa that could think and **could not hear**, from
    somebody who had deliberately chosen the cloud.

    The two tests above jump straight to `ears` and `review` with
    `push_by_tag`, which is why they did not catch it: **a test that navigates
    around a route is not a test of the route.** This one presses Next on the
    page the cloud branch actually lands on, and reads where it goes.
    """
    def after(window, done):
        view = _view(window)
        view.push_by_tag("cloud-keys")

        def press_next():
            page = view.get_visible_page()
            buttons = [w for w in _walk(page)
                       if isinstance(w, Gtk.Button)
                       and (w.get_label() or "").startswith("Next")]
            assert len(buttons) == 1, (
                f"the Cloud keys page has {len(buttons)} buttons reading 'Next'")
            landed = {}
            buttons[0].emit("clicked")

            def read_back():
                landed["tag"] = view.get_visible_page().get_tag()
                done(landed["tag"])

            GLib.timeout_add(1500, read_back)

        GLib.timeout_add(1200, press_next)

    tag = _open(cloud_config, after)
    assert tag == "ears", (
        f"choosing the cloud and finishing the keys page landed on {tag!r}. "
        "Routing to 'done' here means Ears and Voice are never asked about, so "
        "setup completes on a machine that cannot hear and has only the basic "
        "voice - and `setup-complete` then stops it ever being offered again.")


def test_completing_the_cloud_branch_leaves_setup_reopenable_when_hearing_is_absent():
    """The consequence of the route above, asserted where it actually bites.

    `needs_setup()` is what reopens the wizard, and it asks whether brain, ears
    **and** voice can all work. A cloud brain counts as a brain - that half was
    fixed earlier - but ears and voice are local-only questions, so the wizard
    must keep offering itself until they are answered. This pins that the cloud
    branch did not quietly make the "am I finished?" answer a yes.
    """
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa import setup_wizard as sw
    config = ChronoaConfig()
    config.set("setup-complete", "false")
    config.set("setup-dismissed", "false")
    report = sw.state(config)
    assert report["ears"]["ready"] is False, (
        "the fixture machine appears able to hear, so this test would prove "
        "nothing about a cloud-mode machine that cannot")
    assert sw.needs_setup(config) is True, (
        "a machine that can think in the cloud but cannot hear is reported as "
        "needing no setup, so the wizard never opens for it again")


def test_turning_on_cloud_recognition_removes_the_ears_download(cloud_config):
    """Opting into the cloud must actually stop charging for a local model.

    **Both gates are opened explicitly**, because `privacy-mode` defaults to
    **true** in the schema - which is the correct default and the reason a first
    version of this test saw no difference at all: with privacy mode on, cloud
    speech is unavailable by design, so both branches of the comparison were the
    same branch. Stating the precondition is what turns that from a passing test
    of nothing into a test.
    """
    assert cloud_config.get_bool("privacy-mode", True) is True, (
        "privacy-mode no longer defaults to true; if that changed on purpose, "
        "this test's premise - that both gates must be opened explicitly - "
        "needs revisiting")

    def collect(enabled, privacy):
        cloud_config.set("privacy-mode", "true" if privacy else "false")
        original = _cloud_stt_gate(enabled)
        try:
            def after(window, done):
                GLib.timeout_add(1500, lambda: done(_review_text(window)))
            return _open(cloud_config, after)
        finally:
            cloud_voice._read_switch, cloud_voice._provider_key = original
            cloud_config.set("privacy-mode", "true")

    cloud_on = collect(True, False)
    cloud_off = collect(False, False)

    assert any("Listening - whisper.cpp" in line for line in cloud_off), (
        f"with cloud recognition off the local model should be offered; the "
        f"Review page said: {cloud_off}")
    assert not any("Listening - whisper.cpp" in line for line in cloud_on), (
        "enabling cloud speech recognition still charges for a 60 MB local "
        f"whisper model. The Review page said: {cloud_on}")

def test_the_cloud_path_names_who_can_listen_and_speak(cloud_config):
    """The pages said cloud speech "needs a switch in Settings and an API key"
    without naming a provider or offering the switch (2026-10-08)."""
    def after(window, done):
        pages = {tag: _page_report(window, tag)["text"] for tag in ("cloud-keys", "ears", "voice")}
        GLib.timeout_add(1200, lambda: done(pages))

    pages = _open(cloud_config, after)
    keys, ears, voice = pages["cloud-keys"], pages["ears"], pages["voice"]
    assert "chat, listening, speaking" in keys, keys[:400]            # e.g. OpenAI
    assert "needs a key - chat" in keys or "saved - chat" in keys      # a chat-only one
    assert "chat only" in keys, "the free gateways are not said to be chat only"
    assert "Listen in the cloud" in ears and "Groq" in ears and "OpenAI" in ears, ears[-500:]
    assert "Speak from the cloud" in voice and "OpenRouter" in voice, voice[-500:]


def test_the_listen_switch_on_the_ears_page_writes_the_setting(cloud_config):
    def after(window, done):
        view = _view(window)
        view.push_by_tag("ears")
        rows = [w for w in _walk(view.get_visible_page())
                if isinstance(w, Adw.SwitchRow) and w.get_title() == "Listen in the cloud"]
        assert rows, "no Listen in the cloud switch"
        rows[0].set_active(True)
        GLib.timeout_add(300, lambda: done(cloud_config.get_bool(cloud_voice.STT_SWITCH, False)))

    assert _open(cloud_config, after) is True


def test_abilities_follow_the_measured_routes():
    assert cloud_voice.abilities("openai") == ["chat", "listening", "speaking"]
    assert cloud_voice.abilities("anthropic") == ["chat"]
    assert "Kilo" in " ".join(cloud_voice.provider_names(cloud_voice.STT_ROUTES)) or \
        "kilo" in " ".join(cloud_voice.provider_names(cloud_voice.STT_ROUTES)).lower()
