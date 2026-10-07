"""The organ strip: what Chronoa is doing, visible while it does it.

Eight lights, one per organ (`body.py`), always present rather than appearing
when something happens. The difference matters: an indicator that only exists
while the camera is on cannot answer the question people actually have, which is
"is it on *now*", and a strip that hides its lights makes an idle state
indistinguishable from a broken one.

Each light says three things at once, because one of them is not enough:

- **an icon**, so it is readable without reading;
- **a word** ("listening", "acting"), so it is readable without recognising the
  icon;
- **a tooltip naming the exact activity**, so a person who wants to know *what*
  it is doing can find out without opening a log.

**Sound is available and off by default.** The setting exists because the
requirement is real - an assistant you cannot hear is an assistant that surprises
you - but the default is silence, and the reason is that a machine that makes a
noise every time it looks at a camera is a machine people mute, and a muted
indicator is the same as no indicator. Tones are short, quiet, and one per organ
class, not one per event: the sound says *which organ*, the tooltip says what.
"""

from __future__ import annotations

import logging
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # type: ignore

from shani_chronoa import body as body_module

logger = logging.getLogger(__name__)

#: organ -> (icon, label). The icons are the GNOME ones this repo already uses
#: for the same meanings elsewhere, so a person who learned one has learned both.
#:
#: The eye and the brain are here because the names that were here first did not
#: resolve, and an icon that does not resolve is not a subtle wrong - it is an
#: empty box in the organ strip, which is on screen in every conversation.
#: `eye-open-negative-symbolic` reads as "not looking" in intent and is absent
#: from the theme under that spelling or the plain one; the theme's eye is
#: `preferences-desktop-screensaver-symbolic`, which is also what this repo
#: already uses for the Senses panel, so the two now agree. For "thinking",
#: there is no brain icon in Adwaita at any spelling - `brain-augemnted-symbolic`
#: (which is also misspelled) and the corrected `brain-augmented-symbolic` were
#: both measured absent, as was `brain-symbolic` - so "thinking" takes
#: `dialog-question-symbolic`, the repo's existing glyph for Chronoa working
#: something out.
ORGAN_ICONS = {
    "ears": ("audio-input-microphone-symbolic", "listening"),
    "eyes": ("preferences-desktop-screensaver-symbolic", "looking"),
    "mouth": ("audio-volume-high-symbolic", "speaking"),
    "skin": ("network-wireless-symbolic", "network"),
    "nose": ("weather-clear-symbolic", "sensing"),
    "memory": ("emblem-documents-symbolic", "remembering"),
    "hands": ("system-run-symbolic", "acting"),
    "brain": ("dialog-question-symbolic", "thinking"),
}

#: Which sound an organ makes, and at what pitch. Groups rather than eight
#: separate noises: the useful thing to hear is "it is doing something to me"
#: (senses, speech) versus "it did something" (actions, network), and a person
#: learns two cues in a day and eight in a month.
ORGAN_TONE = {
    "ears": ("senses", 660.0),
    "eyes": ("senses", 660.0),
    "nose": ("senses", 660.0),
    "mouth": ("speech", 520.0),
    "skin": ("acted", 440.0),
    "hands": ("acted", 440.0),
    "memory": ("acted", 440.0),
    "brain": ("acted", 440.0),
}


def tone_wav(path: Path, frequency: float, milliseconds: int = 140) -> bool:
    """Write a short sine tone. Returns whether it worked.

    A tone rather than a sample file, so there is nothing to ship and nothing to
    license: 140 ms of a sine at a given pitch, written as 16-bit mono PCM. A
    soft attack and release are *not* decoration - a click at the edges of a
    140 ms tone is audible as a click, which is the thing people find startling.
    """
    try:
        rate = 22050
        frames = int(rate * milliseconds / 1000)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            fade = max(1, frames // 8)
            data = bytearray()
            for index in range(frames):
                envelope = min(1.0, min(index, frames - 1 - index) / fade)
                value = int(12000 * envelope * math.sin(2 * math.pi * frequency * index / rate))
                data += struct.pack("<h", value)
            handle.writeframes(bytes(data))
        return True
    except Exception:                                   # noqa: BLE001
        logger.debug("could not write the indicator tone", exc_info=True)
        return False


class OrganStrip(Gtk.FlowBox):
    """The row of organ lights. Subscribes to the body and redraws on change.

    **A `Gtk.FlowBox`, not a `Gtk.Box`, and that is the whole point of the
    change.** Measured: as a horizontal box this strip asked for **470px** of
    its own, which made the conversation column 498px minimum, which made the
    `Adw.NavigationSplitView` need 762px - so a 600px window could never fit
    chat plus sidebar, and the sidebar could therefore never collapse into a
    drawer. One decorative row of eight status lights was the single reason the
    narrow layout was broken.

    A flow box wraps onto a second row instead, so the strip's minimum is one
    cell wide and the conversation keeps whatever width it has. Every light is
    still drawn at every width; none is hidden to make room.
    """

    def __init__(self, config=None) -> None:
        super().__init__()
        self.add_css_class("organ-strip")
        self.set_selection_mode(Gtk.SelectionMode.NONE)
        # Centring was tried and reverted: `halign` is honoured by nothing
        # between here and the window (see the note at the append site in
        # `gui/window.py`, with the measurement).
        # Not homogeneous: with equal-width cells the strip wrapped to two rows
        # even at 1280px (measured) because six wide cells plus two is a line
        # break, not because the words needed it. Cells size to their label, so
        # a wide window keeps the single row it always had and a narrow one
        # wraps as late as it genuinely must.
        self.set_homogeneous(False)
        # 6px, and it is the *only* gap between the cells. Zeroing the
        # `flowboxchild` padding in the stylesheet recovered ~230px of width -
        # and left nothing between the lights, so the eight labels rendered as
        # "listening looking speaking network sensing remembering acting
        # thinking": one run-on string, not eight named lights. The first fix
        # put the margin in CSS, which left the gap split across two places -
        # 2px of spacing here and 4px of margin there - so no test could
        # measure it and removing either half silently re-broke the reading.
        # One property, one number, and
        # `tests/test_now_rail.py::test_the_organ_lights_are_separated_by_a_gap`
        # asserts it.
        self.set_column_spacing(6)
        self.set_row_spacing(2)
        self.set_min_children_per_line(0)
        # Set explicitly rather than relying on the default: rendered at 1280px
        # with the default, the strip still broke "thinking" onto a second row
        # when all eight lights fitted (measured: eight cells need ~470px of a
        # ~660px column). Naming the number removes the guess - and there is no
        # `Gtk.FLOW_MAX_CHILDREN_PER_LINE` constant in this PyGObject build to
        # name "unlimited" with.
        self.set_max_children_per_line(64)
        self.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.set_margin_top(4)
        self._config = config
        self._labels: dict = {}
        self._rows: dict = {}
        self._unsubscribe = None
        self._build()
        self._unsubscribe = body_module.body.subscribe(lambda _b: self.refresh())

    def _build(self) -> None:
        for organ in body_module.ORGANS:
            icon_name, word = ORGAN_ICONS[organ]
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            row.set_tooltip_text(f"{word.capitalize()} - idle")
            row.update_property([Gtk.AccessibleProperty.LABEL], [f"{word}: idle"])

            image = Gtk.Image.new_from_icon_name(icon_name)
            image.set_pixel_size(16)
            image.add_css_class("organ-icon")
            row.append(image)

            label = Gtk.Label(label=word)
            label.add_css_class("organ-label")
            row.append(label)

            row.add_css_class("organ")
            row.add_css_class("organ-idle")
            self.insert(row, -1)
            self._rows[organ] = row
            self._labels[organ] = label
        self.refresh()

    def refresh(self) -> None:
        """Redraw from the register. Cheap, and called only when it changed."""
        busy = {a.organ: a for a in body_module.body.snapshot()}
        for organ in body_module.ORGANS:
            row = self._rows[organ]
            label = self._labels[organ]
            activity = busy.get(organ)
            if activity is None:
                row.remove_css_class("organ-active")
                row.add_css_class("organ-idle")
                word = ORGAN_ICONS[organ][1]
                label.set_text(word)
                row.set_tooltip_text(f"{word.capitalize()} - idle")
                row.update_property([Gtk.AccessibleProperty.LABEL], [f"{word}: idle"])
            else:
                row.remove_css_class("organ-idle")
                row.add_css_class("organ-active")
                label.set_text(ORGAN_ICONS[organ][1])
                detail = body_module.body.describe(organ)
                row.set_tooltip_text(detail.capitalize())
                row.update_property([Gtk.AccessibleProperty.LABEL], [detail])

    def dispose(self) -> None:
        """Unsubscribe.

        Called by name, not on teardown: **GTK4 removed `Gtk.Widget.destroy()`**
        (`super().destroy()` raises `AttributeError: 'super' object has no
        attribute 'destroy'`, which is what the first version did). And it has to
        be called at all, because `body` holds a strong reference to the listener
        - a strip that forgets to unsubscribe is a leak *and* a redraw loop, not
        a stale widget.
        """
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    # -- sound ----------------------------------------------------------

    def audible_enabled(self) -> bool:
        if self._config is None:
            return False
        try:
            return bool(self._config.get_bool("organ-audible-enabled", False))
        except Exception:                               # noqa: BLE001
            return False

    def play(self, organ: str) -> None:
        """Make the organ's tone, if sound is on and something can play it.

        Never raises into the caller: this runs from whatever triggered the
        activity, and an indicator that takes down the thing it is watching is
        worse than silence.
        """
        if not self.audible_enabled():
            return
        entry = ORGAN_TONE.get(organ)
        if entry is None:
            return
        _group, frequency = entry
        with tempfile.TemporaryDirectory(prefix="chronoa-tone-") as directory:
            path = Path(directory) / "tone.wav"
            if not tone_wav(path, frequency):
                return
            for player in ("pw-play", "paplay", "aplay"):
                if _which(player) is None:
                    continue
                try:
                    subprocess.Popen([player, str(path)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    continue
            logger.debug("no audio player available for the organ tone")


def _which(program: str) -> Optional[str]:
    import shutil

    return shutil.which(program)


class OrganPanel(Adw.NavigationPage):
    """A full page for the body register, for when the strip is not enough.

    The strip answers "what is it doing now". This answers "what has it done
    today", in the same rows the diagnostics panel uses for availability: one
    organ, its state, and the last thing it did.
    """

    def __init__(self) -> None:
        super().__init__(title="Body")
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Body"))
        refresh = Gtk.Button()
        refresh.set_icon_name("view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Refresh")
        refresh.update_property([Gtk.AccessibleProperty.LABEL], ["Refresh"])
        refresh.connect("clicked", lambda _b: self.refresh())
        header.pack_end(refresh)
        toolbar.add_top_bar(header)

        self._group = Adw.PreferencesGroup(title="What Chronoa is doing")
        body_scroll = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body_scroll.set_margin_top(12)
        body_scroll.set_margin_start(12)
        body_scroll.set_margin_end(12)
        body_scroll.append(self._group)
        toolbar.set_content(body_scroll)
        self.set_child(toolbar)
        self._unsubscribe = body_module.body.subscribe(lambda _b: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        child = self._group.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._group.remove(child)
            child = following
        for organ in body_module.ORGANS:
            noun, source = body_module.ORGAN_NOUNS[organ], body_module.ORGAN_SOURCE[organ]
            busy = [a for a in body_module.body.snapshot() if a.organ == organ]
            if busy:
                subtitle = body_module.body.describe(organ).split(": ", 1)[-1]
            else:
                subtitle = f"idle - {source}"
            row = Adw.ActionRow(title=noun.capitalize(), subtitle=subtitle)
            row.update_property([Gtk.AccessibleProperty.LABEL],
                                [f"{noun.capitalize()}: {subtitle}"])
            self._group.add(row)

    def dispose(self) -> None:
        """Unsubscribe; see `OrganStrip.dispose`."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


#: The idle/active colours. Written here rather than in `style.py` because they
#: are the one thing on this strip that must not follow the theme: an indicator
#: that changes colour with the desktop theme is an indicator whose meaning
#: changes with the desktop theme.
ORGAN_IDLE_CSS = """
.organ-strip { padding: 2px 4px; }
.organ { padding: 2px 6px; border-radius: 8px; }
.organ-icon { opacity: 0.45; }
.organ-label { font-size: 10px; opacity: 0.55; }
.organ-idle { background-color: alpha(currentColor, 0.05); }
.organ-active { background-color: rgba(239, 68, 68, 0.18); }
.organ-active .organ-icon, .organ-active .organ-label { opacity: 1.0; color: #b91c1c; }
"""