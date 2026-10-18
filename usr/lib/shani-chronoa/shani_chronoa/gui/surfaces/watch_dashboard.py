"""A MoYoung watch as a dashboard: what it counted, how you slept, how you are.

**Why this replaced a row of buttons.** The Wearables card was nine buttons
("Activity & sleep", "Heart rate", ...) that each printed a sentence into one
shared label, so reading the watch meant pressing a button and reading prose,
and the second press overwrote the first. Everything the watch keeps already
comes back *structured* from `moyoung.health_summary` - steps, distance and
calories for three days, sleep stages with times, half-hourly stress,
workouts - so this draws it the way a fitness app does: one number per card,
a ring for the step goal, a bar for each night's stages, a line for the day's
stress, and the three on-demand measurements as tiles that fill in when taken.

One connection per refresh, off the main thread (`health_summary` reads
everything in one hold). Measurements are separate because each needs the
watch worn and still for up to a minute.
"""
from __future__ import annotations

import logging
import math
import threading
from datetime import datetime
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
# **Drawn with GTK's own snapshot API, not cairo.** A cairo draw function needs
# PyGObject's cairo converter (`python3-gi-cairo`), which a machine can lack:
# measured on the dev box, every chart came out blank with "Couldn't find
# foreign struct converter for 'cairo.Context'". `Gsk.PathBuilder` (GTK 4.14+)
# draws the same shapes with nothing extra, and the text is real labels.
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

logger = logging.getLogger(__name__)

#: The goal the ring is drawn against when the watch's own is not known.
DEFAULT_STEP_GOAL = 8000
#: Sleep stage colours, deep to awake (GNOME palette).
STAGE_COLOURS = {"deep": (0.21, 0.33, 0.66), "light": (0.38, 0.62, 0.92),
                 "awake": (0.96, 0.76, 0.27)}
STEP_COLOUR = (0.15, 0.64, 0.41)
STRESS_COLOUR = (0.88, 0.44, 0.26)


def _off_thread(work: Callable[[], Any], done: Callable[[Any], None]) -> None:
    def run() -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - shown on the card
            logger.info("watch dashboard: %s", exc, exc_info=True)
            result = exc
        GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=run, daemon=True, name="chronoa-watch").start()


def hm(minutes: Optional[int]) -> str:
    if minutes is None:
        return "-"
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def stress_word(value: Optional[int]) -> str:
    """MoYoung's 0-100 stress reading as the band the watch itself shows."""
    if value is None:
        return ""
    return ("Relaxed" if value < 30 else "Normal" if value < 60
            else "Medium" if value < 80 else "High")


def _label(text: str = "", *classes: str, xalign: float = 0.0) -> Gtk.Label:
    # Capped, or a wrapped label reports its whole sentence as natural width
    # and widens the page past the window (measured: a sideways scrollbar).
    label = Gtk.Label(label=text, xalign=xalign, wrap=True, max_width_chars=30)
    for css in classes:
        label.add_css_class(css)
    return label


def _card(title: str, icon: str) -> "tuple[Gtk.Box, Gtk.Box]":
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True,
                   valign=Gtk.Align.START)
    card.add_css_class("card")
    card.add_css_class("watch-card")
    # Padding as margins on the card's children, not CSS, so the card reads
    # right wherever it is shown - the panel, a render, a test window.
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                    margin_top=12, margin_bottom=14, margin_start=14, margin_end=14)
    card.append(inner)
    head = Gtk.Box(spacing=6)
    head.append(Gtk.Image(icon_name=icon))
    head.append(_label(title, "heading"))
    inner.append(head)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    inner.append(body)
    return card, body


from shani_chronoa.gui.charts import Bars, Ring, Swatch  # noqa: E402
from shani_chronoa.gui import charts  # noqa: E402


class StageBar(charts.StackedBar):
    """One night as a strip of stages - `charts.StackedBar` fed stage names."""

    def set(self, segments) -> None:  # type: ignore[override]
        super().set([(STAGE_COLOURS.get(stage, (0.5, 0.5, 0.5)), minutes)
                     for stage, minutes in segments])


class Spark(charts.Line):
    """The day's stress over the watch's own bands (30 / 60 / 80).

    **Feed it with `set_times`, not `set`** - `Line.set` takes `(x, y)` points
    and the readings carry `(datetime, value)`. It carried its own `set`
    override that did that conversion inline, because routing through
    `charts.Line.set_times` recursed: `set_times` calls `self.set`, which *was*
    this override, and the dashboard died on its first stress reading (found by
    the README render, 2026-10-15). Dropping the override is what makes
    `set_times` usable here, and it leaves one copy of the conversion rather
    than the two it had.
    """

    def __init__(self) -> None:
        super().__init__(height=70, colour=STRESS_COLOUR, y_range=(0, 100),
                         x_range=(0, 24), bands=(30, 60, 80))
        self.fill = False


class WatchDashboard(Gtk.Box):
    """Everything one MoYoung watch keeps, and the three measurements."""

    def __init__(self, mac: str, name: str,
                 actions: "Optional[list[tuple[str, str, Callable[[], None]]]]" = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.mac, self.name = mac, name
        self.add_css_class("watch-dashboard")

        # Header: name, battery, refresh.
        head = Gtk.Box(spacing=12)
        head.append(Gtk.Image(icon_name="alarm-symbolic", pixel_size=40))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        titles.append(_label(name, "title-3"))
        self.synced = _label("Not read yet", "dim-label", "caption")
        titles.append(self.synced)
        head.append(titles)
        battery = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        self.battery_label = _label("", "caption", xalign=1.0)
        self.battery = Gtk.LevelBar(min_value=0, max_value=100, width_request=64)
        # In value units: the defaults (0.25 / 0.75 / 1.0) are fractions, so on
        # a 0-100 bar every reading was "full" and 64% drew as a warning.
        for name, at in (("low", 20), ("high", 50), ("full", 100)):
            self.battery.add_offset_value(name, at)
        battery.append(self.battery_label)
        battery.append(self.battery)
        head.append(battery)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                                         tooltip_text="Read the watch again")
        self.refresh_button.add_css_class("circular")
        self.refresh_button.connect("clicked", lambda _b: self.refresh())
        head.append(self.refresh_button)
        self.append(head)
        self.note = _label("", "dim-label")
        self.note.set_visible(False)
        self.append(self.note)

        # Two columns, each card its own height (a homogeneous flow box made
        # every card as tall as Activity and left half of Sleep empty).
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        self.append(grid)
        self._grid_at = 0

        activity, body = _card("Activity", "starred-symbolic")
        self.ring = Ring()
        body.append(self.ring)
        self.activity_detail = _label("", "dim-label", xalign=0.5)
        body.append(self.activity_detail)
        self.bars = Bars()
        body.append(self.bars)
        self._place(grid, activity)

        sleep, body = _card("Sleep", "weather-clear-night-symbolic")
        self.sleep_total = _label("-", "title-1")
        body.append(self.sleep_total)
        self.sleep_window = _label("", "dim-label")
        body.append(self.sleep_window)
        self.stages = StageBar()
        body.append(self.stages)
        legend = Gtk.Box(spacing=12)
        self.legend = {}
        for stage in ("deep", "light", "awake"):
            legend.append(Swatch(STAGE_COLOURS[stage]))
            self.legend[stage] = _label(stage.capitalize(), "caption")
            self.legend[stage].set_wrap(False)
            legend.append(self.legend[stage])
        body.append(legend)
        self._place(grid, sleep)

        stress, body = _card("Stress today", "face-plain-symbolic")
        self.stress_now = _label("-", "title-1")
        body.append(self.stress_now)
        self.stress_word = _label("", "dim-label")
        body.append(self.stress_word)
        self.spark = Spark()
        body.append(self.spark)
        self._place(grid, stress)

        vitals, body = _card("Measure now", "emote-love-symbolic")
        body.append(_label("Wear the watch and keep still - each takes up to a minute.",
                           "dim-label", "caption"))
        tiles = Gtk.Box(spacing=8, homogeneous=True)
        self.tiles = {}
        for key, title, unit in (("heart_rate", "Heart rate", "bpm"),
                                 ("blood_oxygen", "Blood oxygen", "%"),
                                 ("blood_pressure", "Blood pressure", "mmHg")):
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for side in ("top", "bottom", "start", "end"):
                getattr(tile, f"set_margin_{side}")(0)
            tile.add_css_class("card")
            tile.add_css_class("watch-tile")
            value = _label("-", "title-2", xalign=0.5)
            caption = _label(title, "caption", xalign=0.5)
            caption.set_margin_top(8)
            tile.append(caption)
            tile.append(value)
            tile.append(_label(unit, "dim-label", "caption", xalign=0.5))
            button = Gtk.Button(label="Measure", margin_start=8, margin_end=8,
                                margin_bottom=8, margin_top=4)
            button.add_css_class("pill")
            button.connect("clicked", lambda _b, k=key: self.measure(k))
            tile.append(button)
            self.tiles[key] = (value, button)
            tiles.append(tile)
        body.append(tiles)
        vitals_card = vitals

        workouts, body = _card("Workouts", "media-playlist-repeat-symbolic")
        self._place(grid, workouts)
        # Full width: three tiles side by side need more than half the
        # window, and in an equal-column grid that widened every card.
        grid.attach(vitals_card, 0, (self._grid_at + 1) // 2, 2, 1)
        self.workouts = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.workouts.add_css_class("boxed-list")
        body.append(self.workouts)
        self.no_workouts = _label("None recorded on the watch.", "dim-label")
        body.append(self.no_workouts)

        if actions:
            row = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            for label, icon, callback in actions:
                button = Gtk.Button(child=Adw.ButtonContent(icon_name=icon, label=label))
                button.add_css_class("pill")
                button.connect("clicked", lambda _b, c=callback: c())
                row.append(button)
            self.append(row)

    def _place(self, grid: Gtk.Grid, card: Gtk.Widget) -> None:
        grid.attach(card, self._grid_at % 2, self._grid_at // 2, 1, 1)
        self._grid_at += 1

    # -- reading ------------------------------------------------------------
    def say(self, text: str) -> None:
        self.note.set_text(text)
        self.note.set_visible(bool(text))

    def refresh(self) -> None:
        from shani_chronoa import moyoung
        self.refresh_button.set_sensitive(False)
        self.say("Reading the watch...")
        _off_thread(lambda: moyoung.health_summary(self.mac), self.show)

    def show(self, summary: Any) -> None:
        """Fill every card from a `moyoung.health_summary` dict (or an error)."""
        self.refresh_button.set_sensitive(True)
        if isinstance(summary, Exception):
            self.say(f"Could not read the watch: {summary}")
            return
        self.say("")
        device = summary.get("device") or {}
        about = " · ".join(v for v in (device.get("manufacturer"), device.get("software")) if v)
        self.synced.set_text(f"Read at {datetime.now():%H:%M}" + (f"  ·  {about}" if about else ""))
        if device:
            self.synced.set_tooltip_text("\n".join(f"{k.capitalize()}: {v}" for k, v in device.items()))
        level = summary.get("battery")
        if level is not None:
            self.battery.set_value(level)
            self.battery_label.set_text(f"{level}%")

        today = summary.get("steps_today") or {}
        steps = int(today.get("steps") or 0)
        goal = int(summary.get("step_goal") or DEFAULT_STEP_GOAL)
        self.ring.set(steps / goal if goal else 0, f"{steps:,}", f"of {goal:,} steps")
        self.activity_detail.set_text(
            f"{(today.get('distance_m') or 0) / 1000:.2f} km  ·  {today.get('calories') or 0} kcal"
            if today else "No reply for today")
        days = [("2 days ago", summary.get("steps_2_days_ago")),
                ("Yesterday", summary.get("steps_yesterday")), ("Today", today)]
        self.bars.set([(label, int((v or {}).get("steps") or 0)) for label, v in days])
        counted = any(int((v or {}).get("steps") or 0) for _l, v in days)
        self.bars.set_visible(counted)
        if not counted:
            # Measured on the FB BGS002: zeros, not silence, when nothing is
            # stored - and a phone's Da Fit sync empties the watch too.
            self.activity_detail.set_text(
                "Nothing counted in three days - not worn, or the Da Fit app "
                "already synced it off the watch.")

        night = summary.get("sleep_summary")
        if night:
            self.sleep_total.set_text(hm(night["asleep_minutes"]))
            start, end = night.get("from"), night.get("to")
            self.sleep_window.set_text(f"{start:%H:%M} - {end:%H:%M}" if start and end else "")
            for stage in ("deep", "light", "awake"):
                self.legend[stage].set_text(f"{stage.capitalize()} {hm(night[f'{stage}_minutes'])}")
            self.stages.set([(s["stage"], s["minutes"] or 0) for s in summary.get("sleep") or []])
        else:
            self.sleep_total.set_text("-")
            self.sleep_window.set_text("No sleep recorded last night")

        stress = summary.get("stress_today") or []
        if stress:
            latest = stress[-1][1]
            self.stress_now.set_text(str(latest))
            self.stress_word.set_text(f"{stress_word(latest)} at {stress[-1][0]:%H:%M}")
        else:
            self.stress_now.set_text("-")
            self.stress_word.set_text("No readings yet today")
        self.spark.set_times(stress)

        child = self.workouts.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.workouts.remove(child)
            child = nxt
        training = summary.get("training") or []
        for t in training:
            minutes = (t.get("active_seconds") or 0) // 60
            row = Adw.ActionRow(
                title=t["type"].replace("_", " ").capitalize(),
                subtitle=f"{t['start']:%a %d %b, %H:%M} · {minutes} min · {t['steps']:,} steps"
                         f" · {t['calories']} kcal" + (f" · avg {t['avg_hr']} bpm" if t.get("avg_hr") else ""))
            self.workouts.append(row)
        self.workouts.set_visible(bool(training))
        self.no_workouts.set_visible(not training)

    def measure(self, what: str) -> None:
        from shani_chronoa import moyoung
        value, button = self.tiles[what]
        button.set_sensitive(False)
        button.set_label("Measuring...")
        value.set_text("…")

        def done(result: Any) -> None:
            button.set_sensitive(True)
            button.set_label("Measure")
            if isinstance(result, Exception) or not result:
                value.set_text("-")
                self.say(f"No {what.replace('_', ' ')} reading: "
                         f"{result if isinstance(result, Exception) else 'the watch gave none - is it worn?'}")
                return
            value.set_text("/".join(str(v) for v in result))
        _off_thread(lambda: moyoung.measure(self.mac, what), done)


def find(mac: str, done: Callable[[str], None]) -> None:
    """Ring the watch; `done` gets the sentence to show."""
    from shani_chronoa import moyoung
    _off_thread(lambda: moyoung.find_watch(mac),
                lambda r: done(f"Could not reach the watch: {r}" if isinstance(r, Exception)
                               else "The watch is ringing."))
