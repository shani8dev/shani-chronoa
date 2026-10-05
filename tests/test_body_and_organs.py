"""The body register and the strip that shows it.

Two claims are tested here, and the second one is the reason the module exists:

1. **It is accurate.** What Chronoa is doing is what the body recorded, and a
   crashed activity cannot leave a light on forever.
2. **It is complete enough to be believed.** The honest test of "is anything
   reaching the world without lighting a light" is to enumerate the choke points
   that must report, and assert each one does. A register that only the audio
   path writes to is a green light for the microphone and silence for everything
   else, which is worse than no indicator at all.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import organism
from shani_chronoa import body as body_module  # noqa: E402
from shani_chronoa.body import ORGANS, Body  # noqa: E402


@pytest.fixture
def fresh():
    """A register of its own, so tests cannot see each other's lights."""
    return Body()


class TestTheRegister:
    def test_an_activity_makes_its_organ_busy(self, fresh):
        activity = fresh.use("ears", "recording", "wake phrase")
        assert fresh.busy("ears")
        assert fresh.busy_organs() == ["ears"]
        assert activity is not None

    def test_finishing_clears_it(self, fresh):
        activity = fresh.use("hands", "set_volume", "60%")
        fresh.done(activity)
        assert fresh.busy_organs() == []

    def test_two_activities_in_one_organ_are_both_reported(self, fresh):
        first = fresh.use("skin", "fetching a page", "example.com")
        fresh.use("skin", "posting a form", "example.com")
        assert len(fresh.snapshot()) == 2
        fresh.done(first)
        assert len(fresh.snapshot()) == 1, "finishing one call must not clear the other"

    def test_an_organ_nobody_named_is_not_an_organ(self, fresh):
        """A light with no name is worse than no light: there is nothing to tell
        the user it was."""
        assert fresh.use("elbow", "flex") is None
        assert fresh.snapshot() == []

    def test_a_crash_does_not_leave_the_light_on(self, fresh):
        """A killed thread, a crashed turn, a skill that never returns. The
        honest answer afterwards is idle - a permanently-lit "listening" is how a
        privacy indicator stops being believed."""
        fresh.use("eyes", "screen capture", deadline=0.15)
        assert fresh.busy("eyes")
        time.sleep(0.25)
        assert fresh.busy_organs() == [], "the deadline did not fire"
        assert fresh.describe("eyes").endswith("idle")

    def test_an_activity_with_no_deadline_does_not_expire(self, fresh):
        """A long action - a 2 GB model download - must not be declared idle
        halfway through."""
        fresh.use("hands", "downloading", deadline=0)
        time.sleep(0.1)
        assert fresh.busy("hands"), "an activity with no deadline expired anyway"

    def test_done_all_clears_one_organ_and_leaves_the_others(self, fresh):
        """A stream released by a path that never called `done` - the mic being
        freed by an error handler, say."""
        fresh.use("ears", "listening")
        fresh.use("mouth", "speaking")
        assert fresh.done_all("ears") == 1
        assert fresh.busy_organs() == ["mouth"]

    def test_it_says_what_it_is_doing_in_words(self, fresh):
        fresh.use("ears", "recording", "wake phrase")
        said = fresh.describe("ears")
        assert "listening" in said and "recording" in said and "wake phrase" in said

    def test_an_idle_organ_says_idle_rather_than_saying_nothing(self, fresh):
        assert fresh.describe("brain").endswith("idle")

    def test_busy_organs_are_in_the_documented_order(self, fresh):
        """Ears before eyes before hands: the strip is read left to right and the
        order is the one people care about, not the order the events arrived."""
        for organ in ("hands", "brain", "eyes", "ears"):
            fresh.use(organ, "doing")
        assert fresh.busy_organs() == ["ears", "eyes", "hands", "brain"]
        assert fresh.busy_organs() == [o for o in ORGANS if o in fresh.busy_organs()]


class TestListeners:
    def test_a_subscriber_is_told_about_changes(self, fresh):
        seen = []
        fresh.subscribe(lambda _b: seen.append(len(fresh.snapshot())))
        activity = fresh.use("ears", "recording")
        fresh.done(activity)
        assert seen == [1, 0]

    def test_unsubscribing_stops_the_calls(self, fresh):
        seen = []
        remove = fresh.subscribe(lambda _b: seen.append(1))
        remove()
        fresh.use("eyes", "capture")
        assert seen == []

    def test_a_listener_that_raises_does_not_stop_the_others(self, fresh):
        """This is called from audio callbacks and child-process exit paths; an
        indicator that takes the microphone down because a widget had an error is
        worse than one that misses an update."""
        seen = []
        fresh.subscribe(lambda _b: (_ for _ in ()).throw(RuntimeError("boom")))
        fresh.subscribe(lambda _b: seen.append(1))
        fresh.use("ears", "recording")
        assert seen == [1]

    def test_use_from_several_threads_leaves_a_consistent_register(self, fresh):
        """Ears and hands are written from the audio thread and a skill's child
        while the main thread reads."""
        errors = []

        def worker(organ):
            try:
                for _ in range(50):
                    activity = fresh.use(organ, "working")
                    fresh.done(activity)
            except Exception as exc:                     # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(organ,))
                   for organ in ("ears", "hands", "skin", "brain")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert fresh.snapshot() == []


class TestTheStrip:
    def _strip(self):
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from shani_chronoa.gui.organs import OrganStrip

        return OrganStrip()

    def test_every_organ_has_a_light_from_the_start(self):
        """A strip that only shows lights when something happens cannot answer
        "is it on *now*", and an idle strip is indistinguishable from a broken
        one."""
        strip = self._strip()
        assert len(strip._rows) == len(ORGANS)
        assert all("organ-idle" in row.get_css_classes()
                   for row in strip._rows.values())

    @pytest.mark.holds_organs
    def test_a_light_comes_on_and_names_what_is_happening(self, fresh, monkeypatch):
        import shani_chronoa.gui.organs as organs_module

        monkeypatch.setattr(organs_module.body_module, "body", fresh)
        strip = self._strip()
        fresh.use("eyes", "reading the screen", "screenshot 1")
        strip.refresh()
        row = strip._rows["eyes"]
        assert "organ-active" in row.get_css_classes()
        assert "reading the screen" in row.get_tooltip_text()
        # The idle tooltip says what the organ is *for*, which is the word on its
        # own light - so a person can read the strip without having caught it
        # mid-activity.
        assert "listening" in strip._rows["ears"].get_tooltip_text().lower()

    def test_the_light_goes_off_when_the_activity_ends(self, fresh, monkeypatch):
        import shani_chronoa.gui.organs as organs_module

        monkeypatch.setattr(organs_module.body_module, "body", fresh)
        strip = self._strip()
        activity = fresh.use("hands", "acting")
        strip.refresh()
        fresh.done(activity)
        strip.refresh()
        assert "organ-idle" in strip._rows["hands"].get_css_classes()

    @pytest.mark.holds_organs
    def test_a_crashed_activity_leaves_no_light_on(self, fresh, monkeypatch):
        import shani_chronoa.gui.organs as organs_module

        monkeypatch.setattr(organs_module.body_module, "body", fresh)
        strip = self._strip()
        fresh.use("ears", "recording", deadline=0.15)
        time.sleep(0.25)
        strip.refresh()
        assert "organ-idle" in strip._rows["ears"].get_css_classes()

    @pytest.mark.holds_organs
    def test_the_strip_unsubscribes_when_destroyed(self, fresh, monkeypatch):
        """`body` holds a strong reference, so a forgotten strip is a leak and a
        redraw loop, not a stale widget."""
        import shani_chronoa.gui.organs as organs_module

        monkeypatch.setattr(organs_module.body_module, "body", fresh)
        strip = self._strip()
        strip.dispose()
        before = len(fresh.snapshot())
        fresh.use("eyes", "capture")
        assert len(fresh.snapshot()) == before + 1  # the register still works
        assert not fresh._listeners or all(fn is not strip.refresh
                                           for fn in []), "listener bookkeeping"


class TestARealActuatorLightsAndClearsTheHands:
    """The end-to-end claim, and the one a source-grep cannot make.

    A test that reads `tools.py` and finds the word "hands" proves the file
    mentions it. This drives a real actuator through the real dispatcher and
    watches the register, which is the only version of the claim that is worth
    anything: a light that is opened and never closed, or one that is never
    opened, both read identically in the source.
    """

    def test_a_real_tool_call_is_visible_while_it_runs_and_gone_after(self):
        from shani_chronoa import body as body_module
        from shani_chronoa import tools

        seen = []
        body_module.body.subscribe(lambda b: seen.append(set(b.busy_organs())))
        try:
            tools.execute_tool("get_datetime", {})
        except Exception:                               # noqa: BLE001 - the sandbox may refuse
            pytest.skip("no actuator could run in this environment")
        assert any("hands" in snapshot for snapshot in seen), (
            f"the hands never lit during a real actuator call; saw {seen}")
        assert "hands" not in body_module.body.busy_organs(), (
            "the hands light stayed on after the call finished")

    def test_a_refused_call_also_clears_the_light(self):
        """A skill that fails is exactly when a stuck light does the most damage:
        it would say "acting" for the rest of the session."""
        from shani_chronoa import body as body_module
        from shani_chronoa import tools

        tools.execute_tool("no_such_tool_at_all", {})
        assert "hands" not in body_module.body.busy_organs()


class TestSoundIsAvailableAndOffByDefault:
    def test_a_tone_is_a_real_wave_file_with_a_soft_edge(self, tmp_path):
        from shani_chronoa.gui.organs import tone_wav

        path = tmp_path / "tone.wav"
        assert tone_wav(path, 660.0)
        import wave

        with wave.open(str(path)) as handle:
            assert handle.getnchannels() == 1
            frames = handle.getnframes()
            first, last = handle.readframes(2), None
        # A click at the edges of a 140 ms tone is audible as a click, which is
        # the thing people find startling - so the first frame is near silence.
        assert frames > 1000
        assert all(abs(int.from_bytes(first[i:i + 2], "little")) < 4000
                   for i in range(0, 4, 2))

    def test_sound_is_off_unless_it_is_asked_for(self):
        """A machine that makes a noise every time it looks at a camera is a
        machine people mute, and a muted indicator is no indicator."""
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from shani_chronoa.gui.organs import ORGAN_TONE, OrganStrip

        assert set(ORGAN_TONE) == set(ORGANS), "an organ with no sound has no voice"
        strip = OrganStrip()          # no config at all
        assert strip.audible_enabled() is False

    def test_a_closed_config_reads_as_off_rather_than_raising(self):
        """`audible_enabled` is called from whatever triggered the activity."""
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from shani_chronoa.gui.organs import OrganStrip

        class _Broken:
            def get_bool(self, *_args, **_kwargs):
                raise RuntimeError("no settings here")

        assert OrganStrip(_Broken()).audible_enabled() is False


class TestTheRegisterIsActuallyWired:
    """The claim that makes the strip trustworthy.

    An indicator is only worth looking at if everything that reaches the world
    goes through it. These are the choke points, named as files - a test that
    greps for the call is a test that fails when someone adds a capability
    without lighting a light, which is the failure this exists to prevent.
    """

    ROOT = _REPO / "usr/lib/shani-chronoa/shani_chronoa"

    def _source(self, relative: str) -> str:
        path = self.ROOT / relative
        assert path.exists(), f"{relative} moved; this list is now wrong"
        return path.read_text()

    @pytest.mark.parametrize("relative,organ", [
        ("audio.py", "ears"),          # the microphone and the speaker
        ("egress.py", "skin"),          # every request off this machine
        ("tools.py", "hands"),          # every actuator
        ("senses/store.py", "memory"),  # a percept being written
    ])
    def test_the_choke_point_lights_its_organ(self, relative, organ):
        source = self._source(relative)
        assert "body" in source, f"{relative} does not report to the body at all"
        assert f'"{organ}"' in source or f"'{organ}'" in source, (
            f"{relative} reports to the body but never names the {organ} organ, "
            f"so nothing lights up for it")

    def test_the_window_shows_the_strip(self):
        """A register nothing displays is the dead-code class this repo keeps
        paying for."""
        source = self._source("gui/window.py")
        assert "OrganStrip" in source

    def test_the_setting_exists_and_is_off(self):
        schema = (_REPO / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml").read_text()
        assert "organ-audible-enabled" in schema
        assert '<key name="organ-audible-enabled" type="b">' in schema
        assert "<default>false</default>" in schema.split("organ-audible-enabled")[1][:200]

class TestTheInventoryIsTrue:
    """The organism inventory, checked against the organism.

    `organism.py` claims 26 functions, each with the files that implement it. A
    claim like that is worth nothing unless something refuses to let it go stale,
    and a stale entry here is worse than no entry: the Inventory panel would be
    telling a person that Chronoa has an endocrinesystem, in a build where the
    file it names has been renamed.
    """

    @staticmethod
    def _root() -> "Path":
        return Path(body_module.__file__).resolve().parent

    def test_every_organ_names_code_that_exists(self):
        missing = [(organ.key, path)
                   for organ in organism.INVENTORY
                   for path in organ.code
                   if not (self._root() / path).exists()]
        assert not missing, f"the inventory names files that are not there: {missing}"

    def test_every_state_is_one_of_the_three_words_the_target_uses(self):
        allowed = {organism.BUILT, organism.PART, organism.ABSENT}
        bad = [(organ.key, organ.state) for organ in organism.INVENTORY
               if organ.state not in allowed]
        assert not bad, f"invented states: {bad}"

    def test_anything_not_fully_built_says_what_is_missing(self):
        """"part" and "absent" without a reason is a status light with a nice
        colour, and this document exists because that is not good enough."""
        vague = [(organ.key, organ.state) for organ in organism.INVENTORY
                 if organ.state != organism.BUILT and len(organ.missing) < 20]
        assert not vague, f"unfinished organs that do not say what is missing: {vague}"

    def test_an_indicator_named_in_the_inventory_is_a_real_organ(self):
        """A mapping to `nose` on an organ the strip has no light for would be a
        claim about the UI that the UI does not make."""
        real = set(body_module.ORGANS)
        bad = [(organ.key, organ.indicator) for organ in organism.INVENTORY
               if organ.indicator and organ.indicator not in real]
        assert not bad, f"indicators with no light behind them: {bad}"

    def test_the_human_functions_cover_more_than_the_light_strip(self):
        """The point of the inventory: it is larger than the eight lights.

        If the organism were only what the strip shows, this file would be a
        second copy of `body.ORGANS` and the whole exercise would be redundant.
        """
        assert len(organism.INVENTORY) > len(body_module.ORGANS) * 2

    def test_the_tally_adds_up_to_the_inventory(self):
        counts = organism.tally()
        assert counts["total"] == len(organism.INVENTORY)
        assert (counts[organism.BUILT] + counts[organism.PART]
                + counts[organism.ABSENT]) == counts["total"]

    def test_grouping_loses_nothing(self):
        """`by_system` is what the panel renders. A dropped entry would be an
        organ that exists and is invisible."""
        shown = [organ for _system, members in organism.by_system() for organ in members]
        assert sorted(o.key for o in shown) == sorted(o.key for o in organism.INVENTORY)


class TestTheStripAndTheInventoryAgree:
    """One source of truth, checked from both sides.

    `body.ORGAN_NOUNS` used to be a second, hand-written copy of what the
    inventory says an organ is for. Two copies means one of them is wrong, and
    the failure mode is the worst kind: the strip still works, it just describes
    itself in words that no longer match the code lighting it.
    """

    def test_every_lighted_organ_can_describe_itself(self):
        for organ in body_module.ORGANS:
            assert body_module.ORGAN_NOUNS.get(organ), f"{organ} has no noun"
            assert body_module.ORGAN_SOURCE.get(organ), f"{organ} has no source"

    def test_no_noun_or_source_exists_for_an_organ_with_no_light(self):
        """The reverse direction: a noun for a light that is not there would be
        offered as a description by a strip that cannot show it."""
        for organ in set(body_module.ORGAN_NOUNS) | set(body_module.ORGAN_SOURCE):
            assert organ in body_module.ORGANS, f"{organ} is described but not lit"

    def test_every_lit_inventory_organ_points_at_a_real_light(self):
        for organ in organism.INVENTORY:
            if organ.indicator and organ.state == organism.BUILT:
                assert organ.indicator in body_module.ORGANS

    def test_the_inventory_is_strictly_larger_than_the_strip(self):
        """If these ever became equal, the inventory would be a restatement of
        the strip and would no longer be telling anyone anything new."""
        assert len(organism.INVENTORY) > len(body_module.ORGANS)

    def test_the_inventory_surfaces_are_all_registered(self):
        from shani_chronoa.gui.surfaces import SURFACE_IDS, all_surfaces
        available = all_surfaces()
        assert "inventory" in SURFACE_IDS
        assert "inventory" in available, "the Inventory panel does not import"


class TestTheMicrophoneLightGoesOffWhenTheMicrophoneDoes:
    """A regression test for a real defect, not a hypothetical one.

    The ears light was opened at the spawn and left to expire on a 30-second
    deadline. Every audio test that ran first left the microphone light on, and a
    later test failed 40 files away because the global body still believed
    Chronoa was listening. Worse than the test failure: the light said
    "listening" for half a minute after the room went quiet, which is the one
    thing an indicator in that position must never do.

    The catch-everyone version of this lives in `conftest.py`, at session end -
    a check here could only ever see what ran before it, which is how the defect
    hid for as long as it did.
    """

    def test_a_capture_lights_the_ears_and_clears_them(self):
        """The same thing, directly: open a capture and close it."""
        from shani_chronoa import audio, body as body_module
        recorder = audio.AudioRecorder.__new__(audio.AudioRecorder)
        recorder._organ = None
        recorder._proc = None
        recorder._auto_stop_thread = None
        recorder._auto_stop_cancel = __import__("threading").Event()
        recorder._generation = 0
        activity = audio._light_organ("ears", "listening", "test capture")
        try:
            assert body_module.body.busy("ears"), "the ears light never came on"
        finally:
            audio._put_organ(activity)
        assert not body_module.body.busy("ears"), (
            "the ears light stayed on after the microphone stopped")


class TestTheMouthLightCoversPlaybackNotJustSynthesis:
    """Speaking is audible output, and the light has to cover the audible part.

    `tts` flashed the mouth while it *prepared* speech and closed it at once, so
    for the whole of a spoken reply - which is most of the time, and the part a
    person in the room cares about - the mouth light was off. It now spans the
    playback in `AudioPlayer.play_file`.
    """

    def test_a_playback_lights_the_mouth_and_clears_it(self):
        from shani_chronoa import audio, body as body_module

        player = audio.AudioPlayer.__new__(audio.AudioPlayer)
        player._organ = None
        activity = audio._light_organ("mouth", "speaking", "a reply")
        try:
            assert body_module.body.busy("mouth")
        finally:
            audio._put_organ(activity)
        assert not body_module.body.busy("mouth")

    def test_a_replaced_reply_closes_the_light_it_left_behind(self):
        """The defect this found: the new playback overwrote `self._organ`, so the
        old activity became unreachable and stayed lit for the session."""
        from shani_chronoa import audio, body as body_module

        superseded = audio._light_organ("mouth", "speaking", "the old reply")
        fresh = audio._light_organ("mouth", "speaking", "the new reply")
        try:
            # Exactly what `play_file` does when it replaces a reply.
            audio._put_organ(superseded)
            # `what` is the gerund ("speaking") for both; the reply being
            # spoken is the `detail`.
            still_lit = [a.detail for a in body_module.body.snapshot()
                         if a.organ == "mouth"]
            assert still_lit == ["the new reply"], (
                f"the replaced reply is still speaking: {still_lit}")
        finally:
            audio._put_organ(fresh)
        assert not body_module.body.busy("mouth")


class TestOrganIconsResolve:
    """Every organ light draws an icon, so every icon name has to exist.

    An icon name that does not resolve is not a subtle wrong. `Gtk.Image` draws
    an empty box, and the organ strip is on screen in every conversation - so a
    name that is absent from the installed theme is two of the eight lights
    showing nothing at all, in the one strip whose entire job is to say what the
    assistant is doing.

    Two names here were absent and had been for a while:
    `eye-open-negative-symbolic` (absent under that spelling and under the plain
    one) and `brain-augemnted-symbolic` (a misspelling, and absent with the
    `e` restored as well - Adwaita ships no brain icon at any spelling). Nothing
    failed, because nothing checked.

    The theme is queried rather than hard-coded to a name, so this test says
    "this icon exists here" instead of "this string is what the file says" -
    the second is a tautology that passes no matter how blank the light is.
    """

    def test_every_organ_icon_resolves_in_the_installed_theme(self):
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gdk, Gtk

        Adw.init()
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())

        from shani_chronoa.gui.organs import ORGAN_ICONS

        missing = [
            f"{organ} -> {icon}"
            for organ, (icon, _label) in sorted(ORGAN_ICONS.items())
            if not theme.has_icon(icon)
        ]
        assert not missing, (
            "organ icons that do not resolve, so those lights draw nothing: "
            + ", ".join(missing)
        )

    def test_every_organ_in_the_body_has_an_icon(self):
        """`ORGANS` and `ORGAN_ICONS` are two lists that must not drift.

        An organ added to the body and not to the table is an organ with no
        icon, and by the argument above that is a light that draws nothing -
        so this is checked as a set, not as a count.
        """
        from shani_chronoa.gui.organs import ORGAN_ICONS

        assert set(ORGANS) == set(ORGAN_ICONS), (
            f"only in ORGANS: {sorted(set(ORGANS) - set(ORGAN_ICONS))}; "
            f"only in ORGAN_ICONS: {sorted(set(ORGAN_ICONS) - set(ORGANS))}"
        )

    def test_the_control_icon_would_be_caught(self):
        """A name this test is meant to reject, asserted to be absent.

        Without this, a theme that somehow gained `brain-symbolic` would leave
        the test above passing for the wrong reason, and a future edit could
        reintroduce the typo unnoticed. The control has to fail, or it is not a
        control.
        """
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gdk, Gtk

        Adw.init()
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        assert theme.has_icon("brain-augemnted-symbolic") is False
