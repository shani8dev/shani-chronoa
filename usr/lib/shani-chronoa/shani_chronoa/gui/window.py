"""The main window: layout, state, the transcript area and text input; styling, questions, attachments and the conversations menu are mixed in."""

import logging
import os
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Gdk, Gio, GLib, GObject, Adw, Pango

# libadwaita has to be initialised before a single Adw widget exists, and the
# failure mode is silent: the widgets render nothing, no exception, no warning.
# That is exactly how this repo's own settings window once fell back to unstyled
# GTK in a way nobody could see. It is idempotent, and `do_startup` calls it
# again, so doing it here covers a surface or window built before the
# application is started - which is what a test does.
Adw.init()

from shani_chronoa import capabilities
from shani_chronoa import local_llm
from shani_chronoa import planmode

from .minimap import TurnMinimap
from .widgets import (  # noqa: F401
    AssistantState,
    ChronoaOrbWidget,
    HelpWindow,
    TranscriptView,
    _LEGACY_STATE_ALIASES,
    reading_width,
)
from .style import StyleMixin
from .asking import AskingMixin
from .attaching import AttachingMixin
from .conversations_menu import ConversationsMenuMixin
from .sidebar import SidebarBreakpoint, SidebarPage
from . import rail as rail_module
from . import task_card as task_card
from .organs import OrganPanel, OrganStrip, ORGAN_IDLE_CSS

logger = logging.getLogger(__name__)

#: The tag on the conversation page inside the content `Adw.NavigationView`.
#:
#: It exists because the way back to the conversation is `pop_to_tag`, and
#: `pop_to_tag` needs a tag. `Adw.NavigationView` has no `set_visible_page` -
#: measured on the installed libadwaita 1.5, `hasattr` is False - and the code
#: that went back to the chat called it anyway, so every click on the
#: "Conversation" row raised `AttributeError` and did nothing at all. From the
#: outside that reads as a dead row: the sidebar's own chat entry, on the state
#: the window opens in, silently broken, and with it every panel's back arrow.
CHAT_TAG = "chronoa-chat"

#: How many lines the detail line may wrap to, with the last one ellipsised.
#:
#: It exists because the alternative was unbounded. The detail line had no wrap
#: and no ellipsize at all, so a realistic 150-character refusal wanted **854px**
#: of a **455px** chat column at the window's own minimum - 399px clipped, and
#: the clipped part is the end, which is where a refusal names the file and the
#: limit. Wrapping unbounded is not the answer either: a long reason then costs
#: five lines.
#:
#: Measured on the real window at 380, 460 and 1024 wide, with one line being
#: **17px** and the chat column **455px**:
#:
#: | aside | at the bound below | at 5 |
#: |---|---|---|
#: | 150 characters (854px) | 33px | 33px |
#: | 300 characters | **33px** | 66px |
#: | 1200 characters | **33px** | 82px |
#:
#: So two lines is not a compromise between two costs - it costs the same 33px
#: whether the refusal is 150 characters or 1200, and the extra 16px arrives
#: **only** for text that needs it. That matters because the window's chrome has
#: **zero slack**: `set_size_request(380, 571)` is 451 of chrome plus exactly
#: 120 of transcript, so a permanent extra line would have to be paid for out of
#: the conversation. A bound that only costs when it wraps is not that.
DETAIL_MAX_LINES = 2


def _surface_title(name: str) -> str:
    """A page's title from its own registry entry, not a hand-kept list.

    `available_surfaces()` yields `(TITLE, ICON, build)` - index 0 is the title
    and index 1 is the icon. A first version read index 1, so every wrapped page
    was titled with its own icon name (`audio-input-microphone-symbolic` as a
    window title), and the test caught it on the first run.
    """
    from shani_chronoa.gui.surfaces import available_surfaces

    fallback = name.replace("_", " ").capitalize()
    try:
        entry = available_surfaces().get(name)
        return str(entry[0]) if entry else fallback
    except Exception:  # noqa: BLE001 - a title is not worth failing over
        return fallback


def _broken_page(name: str, exc: BaseException) -> "Adw.NavigationPage":
    """The page a panel shows when it could not be built.

    A panel that raises on construction must not take the window with it: the
    conversation is the product, and the panel is one of ten ways to look at it.
    So the failure is a page that says what failed, with the exception in the
    detail, and every other panel still works.
    """
    from shani_chronoa.gui.surfaces import common

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    body.set_margin_top(18)
    body.set_margin_start(18)
    body.set_margin_end(18)
    heading = Gtk.Label(label="This panel could not be opened")
    heading.add_css_class("title-4")
    body.append(heading)
    detail = Gtk.Label(label=f"{type(exc).__name__}: {exc}")
    detail.set_wrap(True)
    detail.set_selectable(True)
    detail.add_css_class("dim-label")
    body.append(detail)
    if common.adw_ready():
        return Adw.NavigationPage(child=body, title=name)
    return body


def _panel_status(page: object) -> "str | None":
    """What a panel says about itself, or None if it has nothing to say.

    A panel may expose `status()` returning one of `common.STATUS_*`. This is
    deliberately duck-typed and deliberately optional: twenty panels build
    through several different shapes (`Adw.NavigationPage`, `Gtk.Box`, and the
    ones that forward attributes through a loop), and requiring every one of
    them to answer would be a large change for a cosmetic gain. A panel that
    answers gets a dot; a panel that does not simply keeps none.

    The one thing that *is* checked is the vocabulary. A panel returning a fourth
    word would leave the dot uncoloured - the exact failure the closed vocabulary
    exists to prevent - so it is treated as no answer at all rather than passed
    through to a grey square. A `status()` that raises is likewise no answer: a
    sidebar dot is not worth taking the window down for.
    """
    from shani_chronoa.gui.surfaces import common

    status = getattr(page, "status", None)
    if not callable(status):
        return None
    try:
        answer = status()
    except Exception:
        logger.warning("A panel's status() raised", exc_info=True)
        return None
    if answer not in common.STATUS_CLASSES:
        return None
    return answer


#: The mode strip's rules, kept out of `style.py` for the reason
#: `organs.ORGAN_IDLE_CSS` is: these chips are *indicators*, and an indicator
#: whose "on" colour follows the desktop theme changes what it means when the
#: theme changes. It inherits `currentColor` rather than naming a hue, so a chip
#: reads as "on" by weight and background rather than by a second colour
#: vocabulary nobody would remember by the time they needed it.
MODE_STRIP_CSS = """
.mode-strip { padding: 2px 4px; }
.mode-chip { padding: 1px 2px; border-radius: 9px; }
.mode-chip-label { font-size: 10px; opacity: 0.6; }
.mode-chip-on { background-color: alpha(currentColor, 0.14); }
.mode-chip-on .mode-chip-label { opacity: 1.0; font-weight: 600; }
"""


def _tier_word(key: str) -> str:
    """The short word a tier key is shown as - `qwen3-1.7b` -> `1.7B`.

    **One function, because the second branch below wanted it too.** The chip's
    installed row and its answering-model row are the same question, and when
    the answering row carried the bare key while the installed row beside it
    carried `1.7B`, the chip said two names for one model - the defect this file
    records most often, introduced by that row rather than left by it.
    """
    return key.split("-")[-1].upper() if "-" in key else key


class _ModeStrip(Gtk.Box):
    """What Chronoa *is* right now: three switches and one button.

    The organ strip beside it answers "what is Chronoa doing"; this answers "what
    is Chronoa allowed to be". Those are different questions and they were both
    answered only in Settings, so a user who had turned plan mode on had no way
    to see it from the window they were talking to - and a mode that removes
    capabilities without saying so reads as a bug the first time the assistant
    declines something that was explicitly permitted.

    **Each chip is the control, not a label for one.** It is a
    `Gtk.ToggleButton` wired to the app's own action, which is the same path the
    keyboard accelerator and the Settings window's switch take. A label that
    reads "Plan mode: on" beside a switch in another window is two widgets that
    can disagree; this is one widget, and the only way for it to be wrong is for
    the action not to have run.

    **The state is read back, not tracked.** `_syncing` guards `set_active()`
    from re-entering the handler, and every read goes to the thing that owns the
    state - `planmode.is_enabled()` for plan mode, the persisted
    `wake-word-enabled` for the wake phrase, `privacy_mode` for local-only. A
    chip that remembered its own last click would be right until something else
    changed the setting, and then confidently wrong, which is the specific
    failure `set_status()` already documents for state lines.

    **Dictation is a button, not a fourth switch**, because it has no state to
    hold: it is a way of starting one long turn, and a chip that stayed pressed
    afterwards would claim a turn is still running when it ended on silence two
    hundred seconds later.
    """

    def __init__(self, application, config=None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.add_css_class("mode-strip")
        self.set_halign(Gtk.Align.CENTER)
        #: The application is passed in rather than read from
        # `Gtk.Application.get_default()`. That global is process-wide, so a
        # strip built while *any* other application happens to be the default
        # activates its actions on the wrong one - measured: `tests/
        # test_mode_strip.py` passed alone and failed every activation when run
        # beside `test_window_ux.py`, whose fixture is the default by then.
        self._app = application
        self._config = config
        #: True while `refresh()` writes. A `Gtk.MenuButton` has no pressed
        # state to fight this, so the guard is kept only for re-entry from a
        # widget repainting mid-write.
        self._syncing = False
        # **Four toggles became two dropdowns, and the two loose buttons moved.**
        # The strip had eight controls in a row - Local only, Plan mode, Wake
        # word, Talk over, Reply style, Answering, Dictate, Listening - and three
        # of them answered the same question. Plan mode and Local only are both
        # "what is this allowed to do"; Wake word and Talk over are both "how is
        # it listening". Four independent booleans also mean sixteen combinations
        # and no way to say "the usual one" in a glance.
        #
        # A dropdown names the state in words, which is what a mode strip is for:
        # the chip's *word* changes, so it is visible at a glance and legible in
        # a screenshot, and at the compact width the icon carries it. Nothing is
        # hidden - every old control is still an app action, still in the menu
        # that owns it, still in Settings.
        #
        # Dictate and the Listening page are not modes and are not here: Dictate
        # is one action and stays with the mic button that shares its subject,
        # and the Listening panel is navigation, which belongs to the sidebar.
        # **Three chips, one question each** (2026-10-14 redesign). The strip had
        # four chips answering two questions twice - "what may it do" was split
        # across General/Build, Ask first and a plan toggle beside Send, "where
        # do answers come from" across a presence chip, an egress chip and the
        # model row - and "how do I talk to it" had no chip at all, so wake word
        # and talking over a reply had no visible state anywhere on the window.
        #
        #   Work         what it works on + whether it plans first
        #   Permissions  risky actions + whether it acts on its own
        #   Voice        mic button / wake word / talking over a reply
        #
        # "Where answers come from" and "is the model in memory" moved into the
        # model button under the composer, beside the model they describe, and
        # the reply style into the Thinking button. The single-setting chips
        # below are still **built** - `refresh()` draws them and older tests
        # reach them - but none is on screen.
        self._rules_chip = self._capability_chip()
        self._auto_chip = self._auto_chip()
        # **Built, not appended - it moved to the composer.** Measured: this
        # strip is 480px of a 496px column, so it was already full, and the
        # chip the newcomer most needs (which model answers) had been pushed off
        # it entirely. `answer_button` carries plan mode and the reply style
        # together, beside Send, where the width is; the model has its own
        # "Answers with" row under the composer (`ChronoaWindow._model_row`).
        #
        # Still constructed and still drawn by `refresh()`: `test_mode_strip`
        # asserts the *css class* on this chip, and a test reaching past the
        # row to ask about a widget is still a test worth keeping.
        self._plan_chip = self._plan_chip()
        self._presence_chip = self._presence_chip()
        self._answer_chip = self._egress_chip()

        # **Built, not appended.** Four controls moved off the strip to the
        # palette (Ctrl+K) because they are set once per task rather than
        # changed mid-conversation: when it may act unprompted, how it listens,
        # how it replies, and which model answers. Nine chips in a row is nine
        # words to read past the one you came to change, and the strip is read
        # from across the room.
        #
        # They are still **built**, because `refresh()` draws them and
        # `activate_mode` opens them from the palette - a chip that is never
        # appended but is still constructed and still drawn is one keystroke
        # from usable, and no reader or test has to know it moved.
        self._ambient_chip = self._ambient_chip()
        self._hands_chip = self._hands_free_chip()
        self._style_chip = self._reply_style_chip()
        self._model_chip = self._model_chip()
        self._work_chip = self._work_chip()
        self.append(self._work_chip)
        self._permissions_chip = self._permissions_chip()
        self.append(self._permissions_chip)
        self.append(self._hands_chip)
        self.refresh()

    #: The plan rows as a person picks them; `_PLAN`'s words are the chip's.
    _PLAN_MENU = {"working": "Just do it", "plan": "Plan first"}

    def _work_chip(self) -> Gtk.MenuButton:
        """What it works on, and whether it plans before acting. One dropdown.

        Both answer "what kind of work is this": Build is the file tools for
        working on a project, and Plan first makes it say what it would do
        without changing anything - which a coder reaches for *with* Build, so
        the two were a pair split across the strip and the button beside Send.
        Plan is bound to the stateful `app.answer-plan`, so GTK draws it as a
        radio pair rather than two rows that both toggle.
        """
        button, label, image = self._menu_chip("system-run-symbolic")
        self._work_label = label
        self._work_image = image
        menu = Gio.Menu()
        works_on = Gio.Menu()
        for key, word, _line, _icon in self._CAPABILITY:
            works_on.append(word, f"app.answer-rules::{key}")
        menu.append_section("Works on", works_on)
        before = Gio.Menu()
        for key, _word, _line, _icon in self._PLAN:
            before.append(self._PLAN_MENU[key], f"app.answer-plan::{key}")
        menu.append_section("Before acting", before)
        # The project Build mode works in. Choosing one turns Build on.
        project = Gio.Menu()
        project.append("Choose project folder…", "app.choose-project")
        project.append("No project", "app.clear-project")
        menu.append_section("Project", project)
        button.set_menu_model(menu)
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["What it works on, whether it plans first, and which project"])
        return button

    def _permissions_chip(self) -> Gtk.MenuButton:
        """What it may do without you: risky calls, and acting unprompted.

        Both are "how much does it do on its own" - one per request, one in the
        background - and the second had no place on screen at all.
        """
        button, label, image = self._menu_chip("security-medium-symbolic")
        self._permissions_label = label
        self._permissions_image = image
        menu = Gio.Menu()
        risky = Gio.Menu()
        for key, word, _line, _icon in self._AUTO:
            risky.append(word, f"app.answer-permission::{key}")
        menu.append_section("Risky actions", risky)
        alone = Gio.Menu()
        for key, word, _line, _icon in self._AMBIENT:
            alone.append(word, f"app.answer-ambient::{key}")
        menu.append_section("On its own", alone)
        button.set_menu_model(menu)
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["What it may do without asking you"])
        return button

    def _menu_chip(self, icon: str):
        """The shell every dropdown shares: a button, a label, an image.

        **One shell, so the four dropdowns cannot drift apart.** The first
        version built each by hand and the reply-style chip had a second label
        and its own box construction, which is how a row ends up with two
        different paddings nobody chose.

        The image comes back because **the icon follows the state**: "This
        # computer" and "Cloud" are different pictures, and icons that say what
        # they mean are legible when the words are hidden.
        """
        button = Gtk.MenuButton()
        button.add_css_class("mode-chip")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        image = Gtk.Image.new_from_icon_name(icon)
        image.set_pixel_size(13)
        box.append(image)
        label = Gtk.Label(label="")
        label.add_css_class("mode-chip-label")
        box.append(label)
        button.set_child(box)
        return button, label, image


    #: style -> (chip word, menu line). Words a person picks by, not the keys.
    _STYLES = (
        # Under 30 characters each: the layout contract fails any label longer
        # than that which can neither wrap nor ellipsise, and menu items do
        # neither.
        ("ordinary", "Ordinary", "Ordinary: the usual answer",
         "format-justify-left-symbolic"),
        ("brief", "Brief", "Brief: just the answer",
         "format-justify-right-symbolic"),
        ("explanatory", "Explanatory", "Explanatory: adds the why",
         "format-justify-fill-symbolic"),
    )

    def _reply_style_chip(self) -> Gtk.MenuButton:
        """How replies are written, as a chip showing the current style.

        Three values, so a menu rather than a toggle, bound to the stateful
        `app.reply-style` action - GTK draws the menu's items as radio buttons
        and marks the current one from the action's own state. The style was
        reachable only in Settings although it changes the very next reply,
        which is the definition of a mode this strip exists for.
        """
        button, label, image = self._menu_chip("format-justify-left-symbolic")
        self._style_label = label
        self._style_image = image
        menu = Gio.Menu()
        for key, word, _line, _icon in self._STYLES:
            menu.append(word, f"app.reply-style::{key}")
        button.set_menu_model(menu)
        return button

    #: What it may **change**, as one choice.
    #:
    #: **The privacy half of this chip moved to "Where it may go".** This was one
    #: four-way menu crossing *what it may change* with *what may leave the
    #: machine*, and those are unrelated questions: "Read only" forbids changing
    #: things and permits sending them away, "Local only" does the opposite. **So
    #: the four options could not be ordered against each other** - a person
    #: choosing between "Read only" and "Local only" cannot tell which is
    #: stricter, because neither is - and a menu of modes that is not a total
    #: order is not a choice, it is a puzzle.
    #:
    #: Worse, "Local only" decided where answers came from, which the *next* chip
    #: also decided. The two chips could contradict each other and did: measured,
    #: `privacy=True` with `answer-mode=cloud` left the neighbour chip reading
    #: "Cloud" while `_apply_answer_mode` dropped the cloud chain and answered
    #: locally. One question per chip, and neither can contradict the other.
    #: How a risky call is treated. **Read live from `permissions`**, so the
    #: chip and the Settings combo row cannot disagree - Settings writes the
    #: posture through `permissions.set_mode` without going through an action,
    #: and `refresh()` runs on window focus precisely so the chip catches up.
    #:
    #: `default` is the quiet one. `dont_ask` is the important row: it does not
    #: mean "act freely", it means **refuse rather than prompt**, and the word
    #: says so. `explore` is plan mode's permission-layer twin.
    #: **Auto: it stops asking.** Two rows, because the third permission value -
    #: `explore`, read-only - is deliberately not here; it is Settings > Privacy,
    #: where its stricter meaning is written down, and `buildmode.read_only_note`
    #: says so for anyone who remembers it on the strip.
    _AUTO = (
        ("ask", "Ask first",
         "It asks before anything that needs permission",
         "dialog-question-symbolic"),
        # **"No questions", not "Auto".** The tooltip already said the mode
        # *refuses* rather than asks, and it already disclaimed acting freely -
        # and the word on the chip said the opposite of both. A mode strip is
        # read from across the room and the word is what survives; "Auto" on a
        # strip means *it may act freely*, which is precisely what
        # `permissions.DONT_ASK` refuses to be.
        #
        # This regressed in the strip redesign, which renamed the table to
        # `_AUTO` and carried the old word with it. `test_permission_posture_chip`
        # was already red at the time - it looks for `_PERMISSION` - so nothing
        # objected, and the word that names the mode backwards shipped. **A test
        # that was failing when the change was made cannot catch the change**,
        # which is why `test_the_word_does_not_say_auto` is in the file that
        # goes with this change and not in the one that was red.
        ("auto", "No questions",
         "It does not interrupt you: anything needing permission is refused "
         "rather than asked about. This is not permission to act freely, and it "
         "lasts until Chronoa restarts",
         "mail-unread-symbolic"),
    )

    #: The **workflow** axis: work out what it would do, without doing it.
    #: Separate from "what it may change", which is a permission and lives on
    #: the capability chip. They were one control until "Read only" was
    #: corrected to mean `explore` rather than plan mode; now they can be
    #: combined deliberately - read-only *and* planning, or read-only and
    #: acting - which one control could never do.
    _PLAN = (
        # **`emblem-default-symbolic` is not in Adwaita** - it is a Yaru name
        # and renders as an empty box on plain GNOME. `dialog-question-symbolic`
        # is wrong for this row too, so this uses the one name in the theme that
        # means "nothing is happening here".
        ("working", "Working", "It does what you ask",
         "media-playback-start-symbolic"),
        ("plan", "Plan",
         "It works out what it would do and says so, changing nothing",
         "document-edit-symbolic"),
    )

    #: **`explore` is deliberately not a row here**, and the first version of
    #: this table had it. Measured across all 256 tools, with both modes on:
    #:
    #: | | blocks | read-only? |
    #: |---|---|---|
    #: | plan mode (`gated_by`, the consent-keyed set) | 92 | yes |
    #: | explore (`READ_ONLY_TOOLS`) | 168 | yes |
    #: | both | 77 | |
    #: | neither | 73 | |
    #:
    #: Two read-only controls side by side, differing on 168 tools with no
    #: visible way to tell which is which, is worse than one. And explore
    #: refuses **`ask_user`** - so in explore mode the app cannot ask you a
    #: question - along with `backup_status`, `accessibility` and `alarm`,
    #: which cannot change the machine either. That is a defect in explore's
    #: calibration, not a reason to advertise it: it stays in Settings, where
    #: its stricter meaning is written down, and the measurement is recorded
    #: rather than the mode quietly duplicated onto a strip.

    #: **Build, not read-only.** This chip used to say "Anything" / "Read only"
    #: and to mean plan mode, which conflated a permission with a workflow. It
    #: now says what the turn is *working on*, and read-only has gone back to
    #: being what it always was underneath: `permissions.EXPLORE`, in Settings.
    _CAPABILITY = (
        ("general", "General",
         "It answers anything, using whatever tools fit the question",
         "security-medium-symbolic"),
        # **"Build", not "Code".** The files a person brings this app are often
        # not source - a .docx, a .csv, a systemd unit - and calling that "code"
        # promised a programmer's workflow it does not have. "Build" says what
        # it is for and matches the app's own idiom.
        #
        # **`document-edit-symbolic`, because Adwaita ships no build icon at
        # all.** Measured over every symbolic name in the theme: nothing matches
        # build, tool, hammer, wrench, construction or gear except
        # `tools-check-spelling-symbolic`. `system-build-symbolic` and
        # `applications-development-symbolic` are Yaru names and render as an
        # empty box on plain GNOME.
        ("build", "Build",
         "It is working on your files: reads, searches, edits, and shows what "
         "changed. It may still use anything else it needs",
         "document-edit-symbolic"),
    )

    #: What it does **when you are not asking**, as one choice.
    #:
    #: Surfaced by scanning all 202 schema keys: `background-mode-enabled` gates
    #: the daemon surface - "Keep Chronoa's automatic rules running in the
    #: background" - and it decides whether Chronoa acts at all while you are
    #: away, which is a bigger trust decision than either of the other two chips
    #: and was not on the strip at all. A mode strip answers "what is this right
    #: now", and "on-ask" versus "in the background" is the difference between a
    #: tool and an agent.
    _AMBIENT = (
        ("on-ask", "Only when asked",
         "It acts only in answer to something you asked for",
         "dialog-question-symbolic"),
        ("background", "In the background",
         "Its automatic rules keep running when you are not talking to it",
         "preferences-system-symbolic"),
    )

    #: How it listens, as one choice rather than two booleans.
    #:
    #: **Wake word and Talk over are both "how is it listening".** One opens the
    #: hands-free phrase; the other lets a reply be interrupted. They were two
    #: toggles and they interact - talking over a reply only matters when the mic
    #: is already open - so the pair is one menu with the combinations that mean
    #: something.
    _HANDS = (
        # Press the orb, not hold it: `_toggle_listening` starts a turn that
        # ends on silence or on a second press. "Hold" described a gesture that
        # does nothing different.
        # **`audio-headset-symbolic`, not the microphone.** The composer's
        # Dictate button is a microphone - the convention for "speak into this
        # field" - and the two sat side by side at the narrow width as two
        # identical glyphs meaning different things.
        ("off", "Mic button", "Mic button: press it to speak",
         "audio-headset-symbolic"),
        # **`audio-volume-high-symbolic`, not `audio-input-microphone-high-symbolic`.**
        # The `-high` mic is a Yaru name - Adwaita ships only the plain
        # `audio-input-microphone-symbolic`, which the row above already uses for
        # push-to-talk, so taking it would erase the distinction between "hold
        # the button" and "always listening" that these two rows exist to draw.
        # `audio-volume-high-symbolic` is present in **both** themes and reads as
        # the always-on state it is standing for.
        #
        # The blank was invisible here because GTK falls back to the system
        # theme; on plain GNOME the wake-word row showed nothing.
        ("wake", "Wake word",
         "Wake word: hands-free, but it will not talk over a reply",
         "audio-volume-high-symbolic"),
        ("wake-over", "Wake word, talk over",
         "Wake word and talk over: hands-free, and talking interrupts a reply",
         "audio-volume-high-symbolic"),
        ("over", "Talk over",
         "Talk over only: the mic is not always open, but talking interrupts a "
         "reply", "media-playback-pause-symbolic"),
    )

    #: Whether it is here at all, as one choice.
    #:
    #: **This is the largest question the window can answer, and it was only in
    #: Settings.** Everything else on this strip governs how Chronoa behaves *for
    #: the person*; this governs whether it is *there*. `presence.Presence` has
    #: three states, the third of which is a decision rather than an observation:
    #: `detect()` answers "is the model resident" and so can only ever return
    #: ACTIVE or DROWSY, while SLEEPING is something a person chose.
    #:
    #: The word is the **reconciled** state, for the reason `reconcile()` exists:
    #: a stale DROWSY on a machine that is up would otherwise show a word that
    #: disagrees with what is actually loaded, which is the two-widgets failure
    #: this strip keeps hitting.
    _PRESENCE = (
        ("active", "Here",
         "Awake and answering", "user-busy-symbolic"),
        ("drowsy", "Drowsy",
         "Loaded, but idle - it will let go of the model", "user-idle-symbolic"),
        ("sleeping", "Asleep",
         "It has let go of the model and will not answer until woken",
         "user-offline-symbolic"),
    )

    #: Where it may **go**, as one choice: the answer engine and the egress lock
    #: together.
    #:
    #: **These are one decision, not two.** "Nothing leaves this machine" and
    #: "answer from here" are the same thing a person means by "keep it local" -
    #: a question that goes to a provider has left the machine. So the lock and
    #: the engine are one menu of three, which makes the contradictory pair
    #: ("Local only" and "Cloud") unrepresentable rather than merely discouraged.
    #:
    #: `answer-mode` is untouched underneath: it is a stored setting with three
    #: real values, and this chip *writes* it alongside `privacy-mode` rather than
    #: replacing it.
    _EGRESS = (
        ("this-machine", "Nothing leaves",
         "Nothing leaves this machine, not even a question",
         "security-high-symbolic"),
        ("local-answers", "This machine answers",
         "Questions are answered here; it may still look things up",
         "computer-symbolic"),
        ("local-then-cloud", "This machine, then cloud",
         "Answer here when you can, reach a provider when you cannot",
         "network-transmit-receive-symbolic"),
        ("cloud", "Cloud",
         "Answer from a provider even when this machine could",
         "network-server-symbolic"),
    )

    def _auto_chip(self) -> Gtk.MenuButton:
        """Auto: whether it stops interrupting you to ask.

        **The word is not "auto" and the tooltip says what auto means here.**
        Auto mode *refuses* anything it cannot ask about rather than doing it -
        so a reader who takes "Auto" as "it will act freely" is wrong by the
        whole safety model. The chip says what happens instead.

        **It reads the permission module, not the action's state**, because
        Settings writes `permissions.set_mode` directly and `refresh()` runs on
        window focus for exactly that.
        """
        button, label, image = self._menu_chip("dialog-question-symbolic")
        self._auto_label = label
        self._auto_image = image
        menu = Gio.Menu()
        for key, word, _line, _icon in self._AUTO:
            menu.append(word, f"app.answer-permission::{key}")
        button.set_menu_model(menu)
        button.set_tooltip_text("Whether it stops you to ask before acting")
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Whether Chronoa interrupts you to ask"])
        return button

    def _plan_chip(self) -> Gtk.MenuButton:
        """How it treats a risky call - ask, refuse, or look only.

        **Placed first, before "what may it change", because it is the question
        that decides whether you are interrupted.** The capability chip answers
        *how much* it may do; this one answers *whether it will ask*. A person
        who has set "No questions" and is then surprised by a refusal should
        have been able to see that from the window, not only from Settings.

        **The word is the claim.** "No questions" says what happens - a call
        that needs permission is refused - and the tooltip spells out that this
        is *not* permission to act freely, because "auto" on a mode strip
        invites exactly that reading.
        """
        button, label, image = self._menu_chip("document-edit-symbolic")
        self._plan_label = label
        self._plan_image = image
        menu = Gio.Menu()
        for _key, word, _line, _icon in self._PLAN:
            # **Both rows activate the same toggle action.** It carries no
            # value, so there is nothing to set - the chip redraws from
            # `planmode.is_enabled()` instead. A dropdown whose two items both
            # say the same action is the honest shape for a toggle.
            menu.append(word, "app.toggle-plan-mode")
        button.set_menu_model(menu)
        button.set_tooltip_text("Whether it works out what it would do first")
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Whether Chronoa plans before acting"])
        return button

    def _capability_chip(self) -> Gtk.MenuButton:
        """What it may change, in one dropdown of two.

        Two options, which is a toggle wearing a dropdown's clothes - but the
        alternative is a bare switch whose state is invisible in a screenshot,
        and this strip's whole argument is that the current mode should be legible
        at a glance. It is bound to the stateful `app.answer-rules` action, so GTK
        marks the current one and the chip and the menu cannot disagree.
        """
        button, label, image = self._menu_chip("security-medium-symbolic")
        self._rules_label = label
        self._rules_image = image
        menu = Gio.Menu()
        for key, word, _line, _icon in self._CAPABILITY:
            menu.append(word, f"app.answer-rules::{key}")
        button.set_menu_model(menu)
        button.set_tooltip_text("Whether Chronoa may change things, not just read them")
        return button

    def _ambient_chip(self) -> Gtk.MenuButton:
        """Whether it acts when you are not asking, in one dropdown of two."""
        button, label, image = self._menu_chip("preferences-system-symbolic")
        self._ambient_label = label
        self._ambient_image = image
        menu = Gio.Menu()
        for key, word, _line, _icon in self._AMBIENT:
            menu.append(word, f"app.answer-ambient::{key}")
        button.set_menu_model(menu)
        button.set_tooltip_text(
            "Whether Chronoa's automatic rules keep running when you are not "
            "talking to it")
        return button

    def _presence_chip(self) -> Gtk.MenuButton:
        """Whether Chronoa is here at all, in a dropdown of three.

        **A menu rather than the button it replaces.** The Settings row cycles the
        states with a press, and its label is the *next* action rather than the
        current state - which is right for a row whose subtitle already says the
        state, and wrong for a strip, where the word on the chip is the only thing
        legible in a screenshot or from across the room.

        Applying a state stops or starts a service, so the work happens in
        `_on_answer_presence` rather than here.
        """
        button, label, image = self._menu_chip("user-busy-symbolic")
        self._presence_label = label
        self._presence_image = image
        menu = Gio.Menu()
        for key, word, _line, _icon in self._PRESENCE:
            menu.append(word, f"app.answer-presence::{key}")
        button.set_menu_model(menu)
        button.set_tooltip_text(
            "Whether Chronoa is awake, letting go of the model, or asleep")
        return button

    def _read_presence(self) -> str:
        """The presence choice, reconciled against what the machine is doing.

        `reconcile()` and not `detect()`: the word a person reads is built from
        the reconciled value, so reading it must come from the same value - and
        because `detect()` cannot see SLEEPING at all, reading through it would
        report a state that is not the one chosen.
        """
        from shani_chronoa import local_llm, presence

        chosen = getattr(self._app, "_presence", None)
        try:
            if chosen is None:
                chosen = presence.detect(local_llm.is_up)
            return presence.reconcile(chosen, local_llm.is_up).value
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the presence state", exc_info=True)
            return "drowsy"

    def _dream_sentence(self) -> str:
        """When Chronoa last reflected on its own day, in one sentence.

        **Dreaming is an activity, not a fourth presence state**, so it is a
        sentence on the chip rather than a value in `_PRESENCE`: a pass over the
        tool-call log can run at any of the three. What it does *not* do is let
        the chip claim something untrue - so the one thing it adds is the moment
        it happened, which is otherwise invisible on every surface but a
        Settings row.
        """
        try:
            from shani_chronoa import dream
            return dream.describe_last_dream()
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the last dream", exc_info=True)
            return "Its last dream could not be read"

    def _read_auto(self) -> str:
        """Whether auto is on, read from the permission layer that owns it."""
        try:
            from shani_chronoa import permissions

            return ("auto" if permissions.get_mode() == permissions.Mode.DONT_ASK
                    else "ask")
        except Exception:                               # noqa: BLE001
            logger.debug("mode strip: the posture is unreadable", exc_info=True)
            return "ask"

    def _read_plan(self) -> str:
        """Whether plan mode is on. Read from the module, not the toggle's own
        state - `toggle-plan-mode` carries no value, so there is nothing on the
        action to disagree with."""
        try:
            return "plan" if planmode.is_enabled() else "working"
        except Exception:                               # noqa: BLE001
            logger.debug("mode strip: plan mode is unreadable", exc_info=True)
            return "working"

    def _read_capability(self) -> str:
        """The capability choice, read from the permission layer that owns it.

        **"Read only" means `explore`.** It used to read `planmode.is_enabled()`,
        so the chip showed Read only whenever the *workflow* mode was on - and
        the permission layer's own read-only posture, `explore`, was on no chip
        at all. The two ideas were sharing one control, and neither could be
        used without the other.
        """
        try:
            from shani_chronoa import buildmode

            return "build" if buildmode.is_enabled() else "general"
        except Exception:                               # noqa: BLE001
            logger.debug("mode strip: build mode is unreadable", exc_info=True)
            return "general"

    def _read_ambient(self) -> str:
        """Whether the background rules run, read from the setting that owns it.

        Defaulting to `on-ask` when the read fails is the safe direction: this
        setting gates a daemon, and guessing "background" because a read raised
        would start acting on a machine whose owner never asked it to.
        """
        try:
            running = bool(self._config.get_bool("background-mode-enabled", False))
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the background-mode setting",
                           exc_info=True)
            return "on-ask"
        return "background" if running else "on-ask"

    def _hands_free_chip(self) -> Gtk.MenuButton:
        """How it hears you, as one dropdown.

        The two toggles it replaces are both about the microphone, and a person
        choosing between them is really saying "I want to talk to it without my
        hands" - which the four-word menu says outright instead of asking them to
        compose it.
        """
        button, label, image = self._menu_chip("audio-input-microphone-symbolic")
        self._hands_label = label
        self._hands_image = image
        menu = Gio.Menu()
        for key, word, _line, _icon in self._HANDS:
            menu.append(word, f"app.hands-free::{key}")
        button.set_menu_model(menu)
        button.set_tooltip_text("How Chronoa hears you")
        return button

    def _model_chip(self) -> Gtk.MenuButton:
        """Which model answers, in one dropdown.

        **The fifth question, and the one this strip could not answer at all.**
        "Where answers come from" says *local* or *cloud*; it never says *which
        model* - and the model is the most consequential thing about what Chronoa
        is right now, because `local_llm.TIERS` is picked by measured bandwidth
        and the harness is what makes a small model work at all. It lived only in
        the Models panel and Settings, which is the gap this strip's own docstring
        says it exists to close.

        **Only installed tiers are offered.** `TIERS` holds six and a box with one
        model has one choice, so an uninstalled tier is a download and not a mode -
        offering it would put a dead item in a menu whose whole job is to say what
        is true right now.

        **And it is the one control here with a real effect**, which is why its
        action restarts the service: `use()` points `current.gguf`, but the running
        `llama-server` keeps the weights it already loaded, so a chip that only
        renamed the symlink would read "8B" while the 1.7B was answering - the
        two-widgets-that-disagree failure this file records repeatedly. A restart
        is slow, so it runs off the main loop and reports what happened rather
        than appearing to do nothing.
        """
        button, label, image = self._menu_chip("object-select-symbolic")
        self._model_label = label
        self._model_image = image
        button.set_tooltip_text(
            "Which model answers. Switching loads it and restarts the local "
            "server, so the next reply uses it")
        return button

    def _default_model_key(self) -> str:
        """The tier this machine would pick by itself, for the quiet highlight.

        Not a hardcoded name: it is whatever `recommended()` says, so the strip
        flags a *departure* from the machine's own default rather than from a
        word this file chose. Falls back to the tier `TIERS` marks as the
        default, and to nothing at all if neither answers - a chip with no quiet
        state highlights everything, which is the same as highlighting nothing.
        """
        for name in ("recommended", "default_tier"):
            getter = getattr(local_llm, name, None)
            if callable(getter):
                try:
                    got = getter()
                except Exception:                       # noqa: BLE001
                    continue
                if isinstance(got, str) and got:
                    return got
                if got:
                    return getattr(got, "key", "") or ""
        return ""

    def _model_rows(self) -> "list[tuple]":
        """`(key, word, line, icon)` for every installed tier.

        The word is the tier's size, under the 30-character ceiling
        `tests/test_ui_layout_contract.py` enforces on every chip label - a menu
        item can neither wrap nor ellipsise. The line is `TIERS`' own description,
        and it goes in the **chip's tooltip, not the menu**: this comment used to
        say the menu should say what the tier is for rather than only its size,
        and every one of the five dropdowns was built to do exactly that - with
        twelve sentences over the ceiling, measured on the built window. GTK has
        no per-item tooltip on a `Gio.Menu` (`GtkPopoverMenu` renders `label` and
        nothing beside it), so a sentence in that column is a sentence the
        dropdown cannot fit. `_draw` already composes `f"{what}: {word}. {line}"`
        onto the chip, so the explanation for the selection is a hover away and
        nothing is lost; `tests/test_menu_labels_are_words.py` holds the line.
        """
        rows = []
        for key, spec in getattr(local_llm, "SPECS", {}).items():
            if key not in local_llm.installed():
                continue
            word = _tier_word(key)
            line = getattr(spec, "note", "") or word
            rows.append((key, word, f"{word}: {line}", "object-select-symbolic"))
        if not rows:
            # **A machine with no model downloaded is a state, not a crash.**
            # `_draw` reads `table[0]` for the current row, and the four static
            # tables above never hit it because they are written down - this one
            # is built from what is on disk, so an empty disk has to answer or
            # the whole strip raises and the window has no mode strip at all.
            # Measured: exactly this, six tests red with
            # `IndexError: list index out of range` at `_draw`.
            #
            # **Unless something is active, in which case "No model" is a lie.**
            # The two can disagree: a model is pointed at by `current.gguf` and
            # the file it names has been deleted, or `SPECS` does not describe
            # the key in use. Rendered and measured - the chip said "No model"
            # while the picker row said the key, and the key was the truthful
            # one. Naming the active key here is what makes the chip, the row
            # and the setting file agree, which is three sources that are one
            # fact.
            #
            # **`answering_key()`, not `active()`, and the reason is a rendering.**
            # `active()` reads a symlink in *this process's own data home*, which
            # is empty in a throwaway profile pointed at a server another
            # session started - so the disk said nothing while the server was
            # answering every question, and the row said **"No model"** on a
            # window that was visibly answering. The server is what receives the
            # prompt, so it is what the row is about.
            active = local_llm.answering_key() or ""
            if active:
                rows.append((active, _tier_word(active),
                              f"{active}: the model in use, which this data "
                              "home does not hold",
                              "object-select-symbolic"))
            else:
                rows.append(("", "No model",
                             "No model is installed on this machine",
                             "object-select-symbolic"))
        return rows

    def _read_model(self) -> str:
        """The tier answering now, or "" - **the same reader `_model_word` and
        `_model_rows` use.**

        It was `local_llm.active()`, a symlink in this data home, while the
        rows it is drawn against are themselves about what is answering: two
        sources for one question, which is the shape this file records
        repeatedly. `tests/test_model_picker.py` reads it directly, so it is a
        named reader rather than an expression at the call site.
        """
        try:
            return local_llm.answering_key() or ""
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the model in use", exc_info=True)
            return ""

    def _read_hands_free(self) -> str:
        try:
            wake = bool(self._config.wake_word_enabled)
            barge = bool(self._config.barge_in_vad_enabled)
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the hands-free state", exc_info=True)
            return "off"
        if wake and barge:
            return "wake-over"
        if wake:
            return "wake"
        if barge:
            return "over"
        return "off"

    def _egress_chip(self) -> Gtk.MenuButton:
        """Where it may go: the engine and the egress lock, as one choice.

        **This chip is the honest one.** It used to be `answer-mode` alone, so
        that setting "Local only" overrode what it claimed: measured, with privacy
        on and `answer-mode=cloud` it read "Cloud" while `_apply_answer_mode`
        dropped the cloud chain and answered locally. A chip that reports a
        preference the app has overridden is worse than no chip, because it is
        read as a promise.

        So the chip derives its word from **both** settings, and offers the three
        states that are actually reachable rather than the four that are not.
        """
        button, label, image = self._menu_chip("network-transmit-receive-symbolic")
        self._mode_label = label
        self._mode_image = image
        menu = Gio.Menu()
        for key, word, _line, _icon in self._EGRESS:
            menu.append(word, f"app.answer-egress::{key}")
        button.set_menu_model(menu)
        return button

    def _read_egress(self) -> str:
        """The egress choice, derived from the two settings that together make it.

        **Derived, never stored twice.** The chip's word is a function of
        `privacy-mode` and `answer-mode` rather than a third setting that has to
        be kept in step with the two - which is how the strip came to claim a
        state the app had overridden in the first place.
        """
        try:
            if bool(self._config.privacy_mode):
                return "this-machine"
            mode = str(getattr(self._config, "answer_mode", "") or "")
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the egress settings", exc_info=True)
            return "local-then-cloud"
        # **Every reachable pair maps to exactly one word.** An earlier version
        # collapsed `local` and `local-cloud` together as "the same egress state",
        # which was wrong in the only way that matters here: with privacy off and
        # `answer-mode=local` it read "then cloud" while the app answered locally
        # and never reached one. Measured, six combinations, before and after.
        return {"cloud": "cloud",
                "local": "local-answers"}.get(mode, "local-then-cloud")
    #: Below this window width the chips show icons only. Measured: with words
    #: the strip needs 512px, the window can be 380px, and between the two the
    #: strip was clipped at the right - losing Dictate first.
    COMPACT_BELOW = 560

    def set_compact(self, compact: bool) -> None:
        """Icons only, or icons and words. Tooltips and accessible names are
        unchanged either way, so nothing a screen reader says is lost.

        """
        stack = [self]
        while stack:
            node = stack.pop()
            if isinstance(node, Gtk.Label) and "mode-chip-label" in node.get_css_classes():
                node.set_visible(not compact)
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()

    def _read_reply_style(self) -> str:
        try:
            style = str(self._config.reply_style or "ordinary")
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the reply style", exc_info=True)
            style = "ordinary"
        return style if style in {row[0] for row in self._STYLES} else "ordinary"



    # -- the state, read from whoever owns it ---------------------------





    # -- keeping the chips honest ---------------------------------------

    def refresh(self) -> None:
        """Redraw every dropdown from the state that owns it.

        Called on construction, after every choice, and when the window regains
        focus - because the Settings window flips the same settings through the
        same actions, and a dropdown that only refreshed on its own pick would go
        on saying "off" for a setting that is on.

        **Each dropdown is drawn from its own table, not from a shared loop.**
        The strip used to walk one `_CHIPS` table of `(attribute, label, icon,
        action, reader)`; that shape only works while every control is a toggle
        with a boolean, and it is the reason the merge took as long as it did.
        """
        self._syncing = True
        try:
            # **`quiet=("general",)` alongside the renamed table.** The keys
            # changed from anything/read-only to general/build and this tuple
            # did not, so `general` matched nothing and the chip was drawn as a
            # departure in the ordinary case - every window, every refresh.
            self._draw(self._rules_chip, self._rules_label,
                       self._read_capability(), self._CAPABILITY,
                       "What it works on", quiet=("general",),
                       image=self._rules_image)
            # **Read live, not from the action's state.** Settings writes the
            # posture straight through `permissions.set_mode`, so an action
            # state would be stale until the next click. `refresh()` runs when
            # the window regains focus, which is when that becomes visible.
            self._draw(self._auto_chip, self._auto_label,
                       self._read_auto(), self._AUTO,
                       "Whether it interrupts you", quiet=("ask",),
                       image=self._auto_image)
            self._draw(self._plan_chip, self._plan_label,
                       self._read_plan(), self._PLAN,
                       "Whether it plans first", quiet=("working",),
                       image=self._plan_image)
            # **The work chip carries two settings in one word.** `_draw` sets
            # word, icon and highlight from what it works on; plan mode then
            # appends itself and lights the chip, because a chip that stayed
            # dark while plan mode held every turn would say "nothing unusual"
            # about the one setting that changes nothing.
            plan = self._read_plan()
            capability = self._read_capability()
            self._draw(self._work_chip, self._work_label, capability,
                       self._CAPABILITY, "What it works on", quiet=("general",),
                       image=self._work_image)
            # The project's folder name rides on the chip in Build mode, so
            # "which code is it changing" is answered without opening anything.
            from shani_chronoa import buildmode
            folder = buildmode.project()
            if folder:
                self._work_label.set_text(
                    f"{self._work_label.get_text()} · {os.path.basename(folder.rstrip('/'))}")
            if plan != "working":
                self._work_label.set_text(self._work_label.get_text() + " · Plan")
                self._work_chip.add_css_class("mode-chip-on")
            self._work_chip.set_tooltip_text(
                f"{self._answer_tooltip('Works on', capability, self._CAPABILITY)}. "
                f"{self._answer_tooltip('Before acting', plan, self._PLAN)}."
                + (f" Project: {folder}." if folder else ""))
            ambient = self._read_ambient()
            auto = self._read_auto()
            self._draw(self._permissions_chip, self._permissions_label, auto,
                       self._AUTO, "Risky actions", quiet=("ask",),
                       image=self._permissions_image)
            if ambient != "on-ask":
                self._permissions_chip.add_css_class("mode-chip-on")
            self._permissions_chip.set_tooltip_text(
                f"{self._answer_tooltip('Risky actions', auto, self._AUTO)}. "
                f"{self._answer_tooltip('On its own', ambient, self._AMBIENT)}.")
            self._draw(self._ambient_chip, self._ambient_label,
                       self._read_ambient(), self._AMBIENT,
                       "When it may act", quiet=("on-ask",),
                       image=self._ambient_image)
            # Quiet is "here", not "asleep": an assistant that has gone to sleep
            # is a departure somebody should see at a glance.
            presence_state = self._read_presence()
            self._draw(self._presence_chip, self._presence_label,
                       presence_state, self._PRESENCE,
                       "Whether Chronoa is here", quiet=("active",),
                       image=self._presence_image)
            self._presence_chip.set_tooltip_text(
                f"{self._presence_chip.get_tooltip_text()}. "
                f"{self._dream_sentence()}")
            self._draw(self._hands_chip, self._hands_label,
                       self._read_hands_free(), self._HANDS,
                       "How Chronoa hears you", quiet=("off",),
                       image=self._hands_image)
            style = self._read_reply_style()
            self._draw(self._style_chip, self._style_label,
                       style, self._STYLES, "Reply style",
                       quiet=("ordinary",), image=self._style_image)
            self._draw(self._answer_chip, self._mode_label,
                       self._read_egress(), self._EGRESS, "Where it may go",
                       quiet=("this-machine", "local-answers"),
                       image=self._mode_image)
            self._note_engine_disagreement()
            rows = self._model_rows()
            # The menu is rebuilt here rather than once at construction: the
            # rows come from what is on disk, and a model downloaded while the
            # window is open is a new mode, not a new install somebody has to
            # restart to see. Without this the chip is a label with no menu -
            # measured, `get_menu_model()` returned None and the dropdown had
            # nothing to open.
            menu = Gio.Menu()
            for key, word, _line, _icon in rows:
                menu.append(word, f"app.answer-model::{key}")
            self._model_chip.set_menu_model(menu)
            # Only a tier that is actually installed can be the quiet one - on a
            # machine whose default is not downloaded, every installed tier is a
            # departure and the strip should say so rather than leave one quiet
            # by accident.
            offered = tuple(r[0] for r in rows)
            default = self._default_model_key()
            self._draw(self._model_chip, self._model_label,
                       self._read_model(), rows,
                       "Which model answers",
                       quiet=(default,) if default in offered else (),
                       image=self._model_image)
        finally:
            self._syncing = False

    def _note_engine_disagreement(self) -> None:
        """Say so when the chosen egress mode is not the mode in force.

        **The strip answers "what is it allowed to be", and the Models panel
        answers "what is answering".** That split is deliberate and right - but
        since `_apply_answer_mode` started *moving* the engine, the two can now
        disagree, and before anything moved they could not.

        Two ways, and the second is the one that matters:

        - chose **Cloud**, and this machine is answering. Usually a provider
          was unreachable; the applier's refusal is named rather than guessed.
        - chose **Nothing leaves** or **This machine answers**, and a **cloud
          provider is answering**. That is the privacy promise failing, and it
          is the case where saying nothing is worst.

        **Appended to the chip's own tooltip and marked with a class**, rather
        than added as a row: the strip is six words wide, and a seventh chip
        saying "answering: local" would be read as a seventh permission rather
        than as an observation about the one beside it.
        """
        reader = getattr(self._app, "current_llm_kind", None)
        if reader is None:
            return
        try:
            actual = str(reader() or "")
        except Exception:                               # noqa: BLE001
            # No engine to compare against. A strip that cannot check is a
            # strip that says nothing, which is the status quo and not worse.
            return
        if actual not in ("local", "cloud"):
            return

        chosen = self._read_egress()
        row = next((r for r in self._EGRESS if r[0] == chosen), None)
        word = row[1] if row else "this mode"
        self._answer_chip.remove_css_class("mode-disagrees")

        # **Which disagreements are failures, stated once.**
        #
        # | chosen | answering | a failure? |
        # |---|---|---|
        # | Cloud | this machine | **yes** - you asked for a provider |
        # | This machine, then cloud | this machine | no - that is the point |
        # | This machine, then cloud | a provider | no - "when you cannot" |
        # | Nothing leaves / This machine answers | a provider | **yes** - the privacy promise |
        #
        # The first version of this asked "did the user want cloud?" and
        # treated `local-then-cloud` as wanting it, which flagged a local engine
        # under the one mode whose entire description is *answer here*. And it
        # only cleared the mark on `local`/`local`, so a cloud engine that
        # caught up with a cloud choice stayed flagged forever.
        strict_local = chosen in ("this-machine", "local-answers")
        if actual == "cloud":
            # A cloud answer is only a failure under the two modes that
            # promised this machine would answer. Under "This machine, then
            # cloud" it is what the person asked for.
            if not strict_local:
                return
            message = ("You chose to answer on this machine, but a cloud "
                       "provider is answering - nothing you type is going "
                       "there. Open Models to switch back.")
        elif chosen != "cloud":
            # Local answering, and local was either required or preferred.
            return
        else:
            detail = str(getattr(self._app, "_answer_mode_problem", "") or "") \
                or "no provider was reachable"
            message = (f"You chose {word}, but this machine is answering: "
                       f"{detail}.")
        self._answer_chip.add_css_class("mode-disagrees")
        self._answer_chip.set_tooltip_text(
            f"{self._answer_chip.get_tooltip_text() or 'Where it may go'}. "
            f"{message}")
        logger.warning("mode strip: %s", message)

    def _answer_tooltip(self, what: str, current: str, table) -> str:
        """`"Whether it plans first: Plan"` - from the table, not by hand.

        A fourth hand-written copy of these words is how a control ends up
        saying something the menu beside it does not offer, which is the same
        defect `test_menu_labels_are_words.py` exists to catch on the menus.
        """
        row = next((r for r in table if r[0] == current), None)
        return f"{what}: {row[1]}" if row else what

    def _draw(self, chip, label, current, table, what, quiet=(),
              image=None) -> None:
        """One dropdown: the word, the icon, the highlight, from its table.

        **`quiet` names the states that are not flagged.** Highlighting every
        chip would highlight none - four alarms read as silence - so each
        dropdown says which state means "nothing unusual": local for the answer
        mode, ordinary for the reply style, anything for the rules, mic button
        for listening.

        A `Gtk.MenuButton` carries no pressed state of its own, so the highlight
        is the *only* thing saying this is not the usual one - and the word and
        the icon carry it too, which is what makes the strip readable from across
        the window and legible in a screenshot.

        **An empty table is a refused draw, not a crash.** A table with no rows
        is a widget built from a source that errored, and `table[0]` on it is an
        `IndexError` inside `refresh()` - which runs from `__init__`, so the
        window would not open at all. Every reader already returns a known key,
        so this is the belt to their braces.
        """
        if not table:
            return
        row = next((r for r in table if r[0] == current), table[0])
        word, line = row[1], row[2]
        label.set_text(word)
        if image is not None and len(row) > 3 and row[3]:
            image.set_from_icon_name(row[3])
        if current in quiet:
            chip.remove_css_class("mode-chip-on")
        else:
            chip.add_css_class("mode-chip-on")
        chip.set_tooltip_text(f"{what}: {word}. {line}")
        chip.update_property(
            [Gtk.AccessibleProperty.LABEL], [f"{what}, currently {word}"])

class ChronoaWindow(StyleMixin, AskingMixin, AttachingMixin, ConversationsMenuMixin,
                    Adw.ApplicationWindow):

    def _model_row(self) -> Gtk.Widget:



        """The model picker, on its own row under the composer.

        **A popover of `Adw.ActionRow`s, not a `Gio.Menu` of names.** A menu of
        names answers one question - what are the choices - and cannot say which
        is installed, which is answering, or which the quality gate would refuse.
        All three are things a person wants *before* clicking, and the tier list
        plus `local_llm.use()` already know all three. See `gui/model_picker.py`.

        **Rebuilt every time the row is shown**, so a model downloaded while the
        window is open is a new choice rather than a restart.
        """
        from shani_chronoa.gui import model_picker

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # **`composer-model-row` was applied here and styled by nothing** -
        # not this package's stylesheet, not GTK, not libadwaita (all three
        # checked by asking). Removed rather than given a rule, because what
        # the row should look like is a design decision and inventing one here
        # would be a second answer to a question nobody has asked yet.
        # `tests/test_the_chrome_carries_nothing_that_does_nothing.py` fails
        # if a class with no rule comes back.
        row.set_margin_top(2)
        row.set_margin_bottom(2)
        row.set_margin_start(4)

        button = Gtk.MenuButton()
        button.add_css_class("flat")
        button.set_tooltip_text(
            "Which model answers. Switching loads it and restarts the local "
            "server, so the next reply uses it")
        # Where answers may come from, and whether the model stays in memory,
        # ride in the same popover: both are about the engine the lists choose,
        # and both were chips of their own on the strip.
        button.set_popover(model_picker.ModelPicker(
            self._app,
            before=(("Where it answers", "answer-egress", _ModeStrip._EGRESS),),
            after=(("Model in memory", "answer-presence", self._PRESENCE_WORDS),)))
        # **Held, not inlined.** The palette fetches a provider's list into the
        # cache this picker reads, and an inline popover would keep showing the
        # rows it drew before the answer arrived until the next reopen. The
        # handle is what lets a fetch refresh it - and it is also the only
        # thing that makes "the picker and the palette cannot disagree" a
        # property rather than a hope.
        self._model_picker = button.get_popover()
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Which model answers. Choose a locally installed model."])

        lead = Gtk.Image.new_from_icon_name("object-select-symbolic")
        lead.add_css_class("dim-label")
        lead.set_pixel_size(14)
        label = Gtk.Label(label="Answers with")
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        self._model_picker_caption = label
        which = Gtk.Label(label=self._model_word())
        which.add_css_class("caption")
        # A model name is the one word here with no fixed length, so it is the
        # one allowed to give way: ellipsised, the toolbar's minimum stays
        # small enough for the 380px window floor.
        which.set_ellipsize(Pango.EllipsizeMode.END)
        which.set_max_width_chars(22)

        inside = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        inside.append(lead)
        inside.append(label)
        inside.append(which)
        button.set_child(inside)
        self._model_picker_button = button
        self._model_picker_word = which
        self._model_picker_lead = lead
        row.append(button)
        row.append(self._thinking_button())
        row.append(self._speak_toggle())
        self._refresh_model_word()
        return row

    def _speak_toggle(self) -> Gtk.ToggleButton:
        """Speak replies aloud, or not - one press, in the window.

        Bound to the stateful `app.speak-replies`, so it shows the setting and
        cannot disagree with it. Before this the only way to silence replies
        was Settings → Voice, on a switch that also silenced timers.
        """
        button = Gtk.ToggleButton(action_name="app.speak-replies")
        button.add_css_class("flat")

        def show(*_a) -> None:
            on = button.get_active()
            button.set_icon_name("audio-volume-high-symbolic" if on
                                 else "audio-volume-muted-symbolic")
            tip = "Replies are spoken - press to stop" if on else "Replies are not spoken - press to speak them"
            button.set_tooltip_text(tip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tip])
        button.connect("notify::active", show)
        show()
        return button

    #: `_ModeStrip._PRESENCE` with words for a model menu. The strip's
    #: "Here / Drowsy / Asleep" describe Chronoa; inside "Model in memory"
    #: they have to describe the model. Same keys, same action.
    _PRESENCE_WORDS = (
        ("active", "Kept loaded", "Loaded and answering straight away"),
        ("drowsy", "Freed when idle",
         "Loaded now; it lets go of the memory when nothing is asked"),
        ("sleeping", "Asleep",
         "Not loaded, and it will not load itself to answer until woken"),
    )

    #: level -> menu word. Keys are `cloud_llm.THINKING_LEVELS`.
    _THINKING_WORDS = {"off": "Off", "low": "Low", "medium": "Medium",
                       "high": "High", "xhigh": "Extra high", "max": "Max"}

    def _thinking_button(self) -> Gtk.MenuButton:
        """How hard the model thinks, beside the model it applies to.

        **Beside "Answers with" because it is a property of that answer** - the
        same engine, asked the same turn. Bound to the stateful `app.thinking`
        action, so GTK draws the current level as the selected radio and the
        menu cannot disagree with the setting.
        """
        from shani_chronoa.cloud_llm import THINKING_LEVELS

        button = Gtk.MenuButton()
        button.add_css_class("flat")
        label = Gtk.Label()
        label.add_css_class("caption")
        button.set_child(label)
        menu = Gio.Menu()
        levels = Gio.Menu()
        for level in THINKING_LEVELS:
            levels.append(self._THINKING_WORDS[level], f"app.thinking::{level}")
        menu.append_section("How hard it thinks", levels)
        # **The reply style lives here too**: thinking is how much work goes
        # into the answer, the style how much of it is written out, and both
        # are set per question. It used to share a button beside Send with plan
        # mode, which is about acting rather than answering.
        styles = Gio.Menu()
        for key, word, _line, _icon in _ModeStrip._STYLES:
            styles.append(word, f"app.reply-style::{key}")
        menu.append_section("How it replies", styles)
        button.set_menu_model(menu)
        self._thinking_picker_button = button
        self._thinking_picker_word = label
        return button

    def _thinking_word(self) -> str:
        from shani_chronoa.cloud_llm import thinking_level
        try:
            level = thinking_level(getattr(self._config, "thinking", "off"))
        except Exception:                               # noqa: BLE001
            level = "off"
        return level

    def _refresh_thinking_word(self) -> None:
        """Redraw the level, and say what it means for the engine answering now."""
        label = getattr(self, "_thinking_picker_word", None)
        button = getattr(self, "_thinking_picker_button", None)
        if label is None or button is None:
            return
        level = self._thinking_word()
        word = self._THINKING_WORDS[level]
        compact = getattr(self, "_composer_compact", False)
        style = "ordinary"
        strip = getattr(self, "_mode_strip", None)
        if strip is not None:
            try:
                style = strip._read_reply_style()
            except Exception:                           # noqa: BLE001
                style = "ordinary"
        style_word = next((r[1] for r in _ModeStrip._STYLES if r[0] == style), "")
        # The style is named only when it departs from ordinary, the same
        # "quiet" rule every chip follows.
        suffix = "" if style == "ordinary" else f" · {style_word}"
        label.set_label((f"Think: {word}" if compact else f"Thinking: {word}") + suffix)
        if level == "off":
            what = "It answers without thinking first - the fastest choice."
        elif self._answering_kind() == "cloud":
            what = ("The cloud provider is asked for this effort. One that "
                    "cannot is asked at High, then without thinking.")
        else:
            what = ("A local model has no levels, so this turns its thinking "
                    "on. On a CPU that is most of the wait.")
        button.set_tooltip_text(f"Thinking: {word}. {what} Replies: {style_word or 'Ordinary'}.")
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"How hard it thinks before answering, currently {word}"])

    def _refresh_model_word(self) -> None:
        """Redraw the chosen model's name under the composer.

        **Called from `_refresh_strip`, not from the handler, on purpose.** The
        strip and the picker are the same question asked twice - "what is
        Chronoa right now" - and the first version of this had the handler for
        the model chip call one refresh and the strip's own handler call another,
        which is two chances to forget and a way to have the two widgets
        disagree. One refresh, both widgets.

        **It re-reads the setting rather than being handed the new value.** The
        row's whole job is to survive `_refresh_model_word` being called before
        the label exists (during construction) and after the model changed (from
        the handler); a version taking an argument would have two callers and one
        of them would pass the old one.
        """
        self._refresh_thinking_word()
        label = getattr(self, "_model_picker_word", None)
        if label is None:
            return
        label.set_label(self._model_word())
        # **The icon is the half that cannot be a string.** The row leads with
        # `object-select-symbolic`, which is right for "choose" and wrong for
        # "this is what is answering". After a switch the glyph has to follow
        # the word, or the row says one thing in a symbol and another in a
        # label - the two-widgets-that-disagree failure this file records more
        # than any other.
        button = getattr(self, "_model_picker_button", None)
        if button is not None:
            where = self._egress_row()
            lead = getattr(self, "_model_picker_lead", None)
            if lead is not None and where is not None and len(where) > 3 and where[3]:
                # **The icon says where answers go**, so "Nothing leaves" is
                # still visible at a glance now that its chip is gone.
                lead.set_from_icon_name(where[3])
            button.set_tooltip_text(
                "Answers with %s%s. Click to change the model, where answers "
                "come from, or whether the model stays in memory" % (
                    self._model_word(),
                    "" if where is None else " - " + where[1]))

    def _egress_row(self):
        """The `_EGRESS` row in force, or None when the strip cannot say."""
        strip = getattr(self, "_mode_strip", None)
        if strip is None:
            return None
        try:
            current = strip._read_egress()
        except Exception:                               # noqa: BLE001
            return None
        return next((r for r in _ModeStrip._EGRESS if r[0] == current), None)

    def _answering_kind(self) -> str:
        """`"cloud"`, `"local"` or `""` - which engine is holding the turn.

        **A stand-in for `ChronoaApplication.current_llm_kind()`, and the
        stand-in is the point.** This window is built by the renderer with a
        bare `Gtk.Application` that has no `llm` at all, and by a dozen test
        fixtures that have no application; a reader which raised would put a row
        on screen that only the real app can produce - the
        `_note_engine_disagreement` shape this file already records: *"a strip
        that cannot check is a strip that says nothing, which is the status quo
        and not worse"*.

        **`""` rather than `"none"`**, because **"" means "nobody could say" and
        "none" means "there is no engine"**, and only the second is a fact about
        the machine. A window that cannot tell falls through to the local
        registry, which is the answer it gave before this existed.
        """
        reader = getattr(self._app, "current_llm_kind", None)
        if reader is None:
            return ""
        try:
            return str(reader() or "")
        except Exception:                               # noqa: BLE001
            logger.debug("could not read which engine is answering",
                         exc_info=True)
            return ""

    def _model_word(self) -> str:
        """The chosen model's word, **taken from the strip's own row table**.

        This used to walk `local_llm.TIERS` itself and pick the label, and the
        strip walks `SPECS`. Two tables, so two words for one state: on a
        machine with nothing downloaded the chip said **"No model"** and the row
        said **"the default model"** - two labels for the absence of a choice,
        one of which implies a choice was made. Rendered and measured, not
        inferred.

        **One source, read from the strip.** `_mode_strip._model_rows` already
        answers exactly this question, returns a placeholder row when the disk
        is empty, and is the thing the chip is drawn from - so asking it is both
        the shortest answer and the only one that cannot drift.
        """
        try:
            # **Ask the engine before the local registry.** This row answers
            # "what is answering me", and for a machine that configured a
            # provider the local registry's answer is always `""` - nothing is
            # downloaded - so it fell through to the placeholder row and the
            # window read **"Answers with  No model"** while a provider held
            # every turn. Rendered and measured, and it is the one line on the
            # main window a newcomer looks at when deciding whether to bother
            # asking: it says the app cannot answer.
            #
            # **`current_llm_kind()` reads the object holding the turn**, not a
            # setting, because the engine is selected and re-decided at startup
            # and by the fallback - `_maybe_enable_cloud_fallback` returns early
            # on an existing chain and never reconsideres. The same reader is
            # what `_note_engine_disagreement` uses, so the row and the warning
            # beside it cannot answer this from different sources.
            #
            # **"a cloud provider", not the provider's name.** `CloudLLMChain`
            # knows which one answered, but only *after* a turn - and a row that
            # is empty until somebody asks is a row that cannot answer the
            # question it exists for on a fresh window. The specific provider is
            # named by the egress chip and the Privacy panel.
            if self._answering_kind() == "cloud":
                return "a cloud provider"
            strip = getattr(self, "_mode_strip", None)
            rows = strip._model_rows() if strip is not None else ()
            active = local_llm.answering_key() or ""
            for key, word, _line, _icon in rows:
                if key == active:
                    return word
            # **An active key the rows do not offer** - a model downloaded
            # after the rows were built, or one `SPECS` does not describe. The
            # rows' own words would be a lie here, so the key is shown as it
            # stands rather than borrowed from a neighbouring tier.
            return active or rows[0][1]
        except Exception:  # noqa: BLE001 - an unreadable list is not a blank row
            logger.debug("the active tier is unreadable", exc_info=True)
            return "No model"

    """The main window: the conversation, and every panel beside it.

    A `Adw.ApplicationWindow` inside an `Adw.NavigationSplitView`: the sidebar
    lists the panels (`gui/sidebar.py`, built from the same registry the panels
    register in) and the content is a navigation view the conversation lives on,
    with each panel pushed onto it.

    The chat's own widgets are unchanged by any of that - the orb, the transcript,
    the composer, the question row all keep their names and their place - because
    the split view wraps a column that was already there rather than rebuilding
    it. That is deliberate: this window has tests that walk its tree, and a
    redesign that also moved everything would make them all fail at once, which
    is how a real regression hides in a large diff.
    """

    def __init__(self, application: Gtk.Application, config=None) -> None:
        super().__init__(application=application)
        # Panels are handed the application, not the window: they read config and
        # the assistant from it, and a panel that only knew the window would have
        # to guess where the model is.
        self._app = application
        self._state = AssistantState.IDLE
        self._config = config if config is not None else self._default_config()
        self._caps = self._load_capabilities()
        self._help_window: HelpWindow | None = None
        #: Set by the application through `set_regenerate_handler`; None means
        #: the transcript shows no "Ask that again" control at all.
        self._regenerate = None
        #: Built panels, kept so opening one twice does not re-run everything it
        #: measures. Empty until a panel is opened.
        self._surface_pages: dict = {}
        self._toasts = None
        self._setup_window()
        self._holding = False
        self._setup_ui()
        self.connect("close-request", lambda *_a: self._on_close_request())
        try:
            self._apply_organ_css()
        except Exception:                               # noqa: BLE001
            # No display is the only way this fails, and a window with no strip
            # colour is a window that still shows what Chronoa is doing.
            logger.debug("the organ strip stylesheet could not be applied",
                         exc_info=True)
        self._sync_from_state()
        # On an idle turn, not here: building twenty panels costs about two
        # seconds of subprocesses, and a window that is on screen but not
        # usable for two seconds is worse than one whose sidebar fills in.
        # `_seed_status_dots` installs its own idle source and returns `None`,
        # so it is *called* here rather than handed to `GLib.idle_add` - handing
        # it over would re-add a source that returns `None`, i.e. never
        # removing itself, and re-seed every panel forever.
        # See `_seed_status_dots` for why the dots are drawn at all.
        self.connect("map", lambda *_a: self._seed_status_dots())

    @staticmethod
    def _default_config():
        """Config for the window, degrading rather than refusing.

        The window has to be constructible without one - a test, or a caller
        that only wants the visual surface - so a missing config is replaced by
        one that reports every gate as closed. Closed is the honest answer when
        consent cannot be read, and it means the help window explains the
        switches instead of quietly claiming everything is available.
        """
        try:
            from shani_chronoa.config import ChronoaConfig
            return ChronoaConfig()
        except Exception:
            logger.debug("no ChronoaConfig available; gates read as closed",
                         exc_info=True)

            class _Closed:
                def sense_allowed(self, key):
                    return False
            return _Closed()

    @staticmethod
    def _load_capabilities() -> list:
        """Read the live skill registry, tolerating its absence entirely.

        A broken or missing registry must cost the suggestions and the help
        list, not the window. Both surfaces are additive, so the failure mode
        is a plainer window rather than a window that will not open.
        """
        try:
            from shani_chronoa.skills import discover_skills
            tools, _handlers = discover_skills()
        except Exception:
            logger.warning("skill registry unavailable; no suggestions or help",
                           exc_info=True)
            return []
        return capabilities.find_capabilities(tools)

    def _apply_organ_css(self) -> None:
        """The strip's colours, which are the one thing on it that must not
        follow the theme.

        An indicator whose colour changes with the desktop theme is an indicator
        whose *meaning* changes with the desktop theme: on a light theme a
        "listening" light that has gone pale is not a smaller claim, it is a
        different one. The idle/active rules are fixed for that reason and live
        in `organs.ORGAN_IDLE_CSS` rather than in this window's stylesheet.
        """
        # **One provider per sheet, because `load_from_data` REPLACES.**
        #
        # This was one provider and two loads, which reads as "add both" and is
        # not: the second call discards the first. Measured on the real window
        # by the one rule in `ORGAN_IDLE_CSS` that cannot already be satisfied
        # by chance - `.organ { padding: 2px 6px }` predicts a cell exactly 12px
        # wider than its label:
        #
        #     one provider, two loads   (this used to be)   cell - label = 0
        #     one provider per sheet                        cell - label = 12
        #     ORGAN_IDLE_CSS alone                          cell - label = 12
        #
        # So the organ sheet was loaded and thrown away, and what the window was
        # actually running was the mode strip's sheet alone. **What that cost
        # is the whole point of the organ strip**: `.organ-active` carries the
        # red pill and `opacity: 1.0`, `.organ-idle` the 0.05 background, and
        # with neither in effect an active organ rendered identically to an idle
        # one. `refresh()` still toggled the classes correctly; there was simply
        # no rule left that read them.
        #
        # **Not caught by the existing test, and it could not have been:**
        # `tests/test_mode_strip.py:741` loads `MODE_STRIP_CSS` into a provider
        # of its own, so it validates the constant and never reproduces the path
        # that drops the other one.
        for sheet in (ORGAN_IDLE_CSS, MODE_STRIP_CSS):
            provider = Gtk.CssProvider()
            provider.load_from_data(sheet.encode())
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _setup_window(self) -> None:
        self.set_title("Shani Chronoa")
        self.set_default_size(460, 640)
        # **The minimum is as tall as the conversation it promises, not as short
        # as the chrome allows.** Measured on the real window, the transcript is
        # an exact linear function of the window's height: `height - 451` while
        # the organ strip needs two rows, and `height - 415` once it does not.
        # The 480 this used to declare left `max(58, 32)` = **58px** - about two
        # lines - in the smallest window the app would occupy.
        #
        # **The two numbers are measured and re-measurable.** `451 + 120 = 571`,
        # and 120 is the smallest transcript worth calling a conversation
        # (roughly six lines; below that the transcript is a peephole rather than
        # a place a reply is read). The previous 570 was `448 + 120` with two
        # pixels of slack, and the strip's vertical padding - which the sheet
        # carrying it had never applied, because that sheet was being loaded and
        # discarded - grew the chrome by 3px, which left **119** rather than the
        # promised 120. Nothing complained: the test that checks the promise
        # computed it with the same typed constant it was checking.
        # `tests/test_the_window_minimum_is_not_below_the_layouts_floor.py` now
        # measures both chromes off real mapped windows.
        #
        # **This treats the symptom and the root cause is recorded in AGENTS.md:**
        # 451px of fixed chrome is the real number, and an orb at 88px is a fifth
        # of it. A minimum that is honest beats a minimum that promises a window
        # with nothing in it; the chrome is the next thing to shrink.
        self.set_size_request(380, 571)
        self.set_decorated(True)
        self._apply_css()
        self._apply_motion_preference()

    def _home_board(self) -> "Optional[Gtk.Widget]":
        """What an empty conversation shows under the suggestions.

        **Why (2026-10-15).** Measured on a 1100x760 window, the empty state
        was the orb, a hint, six chips and ~200px of nothing above the composer,
        while the useful things - a timer running, the conversation from an
        hour ago, the panel that transcribes a file - were a menu or a sidebar
        away. Each part reads one existing source and hides itself when it has
        nothing to say, so a fresh install sees quick actions and little else.
        """
        app = getattr(self, "_app", None)
        board = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                        margin_top=18, halign=Gtk.Align.FILL)
        board.set_size_request(-1, -1)

        def heading(text: str) -> Gtk.Label:
            label = Gtk.Label(label=text, xalign=0.0)
            label.add_css_class("heading")
            return label

        def activate(name: str, value=None):
            return lambda *_: app is not None and app.activate_action(name, value)

        # -- today ---------------------------------------------------------
        tiles = []
        try:
            from shani_chronoa.skills import timer as timer_skill
            pending = timer_skill.active_timers()
        except Exception:  # noqa: BLE001
            pending = []
        if pending:
            _ident, label, left = pending[0]
            minutes, seconds = divmod(int(left), 60)
            tiles.append(("Next timer", f"{minutes}:{seconds:02d}", label or "timer"))
        try:
            from shani_chronoa.gui.surfaces.machine import _battery
            battery = _battery()
        except Exception:  # noqa: BLE001
            battery = None
        if battery:
            tiles.append(("Battery", f"{battery['percent']}%", battery["status"].lower()))
        try:
            from shani_chronoa import model_status
            missing = model_status.missing()
        except Exception:  # noqa: BLE001
            missing = []
        if tiles or missing:
            from shani_chronoa.gui import charts
            board.append(heading("Today"))
            row = Gtk.Box(spacing=10, homogeneous=True)
            for title, value, detail in tiles:
                row.append(charts.stat(title, value, detail))
            if missing:
                tile = charts.stat("Extras", f"{len(missing)} to add", "vision, voice, memory…")
                add = Gtk.Button(label="Set up", halign=Gtk.Align.START, margin_start=12,
                                 margin_bottom=10)
                add.add_css_class("pill")
                add.connect("clicked", activate("setup-page", GLib.Variant("s", "extras")))
                tile.append(add)
                row.append(tile)
            board.append(row)

        # -- pick up where you left off ------------------------------------
        try:
            from shani_chronoa import conversation_store
            sessions = [x for x in conversation_store.list_sessions(conversation_store.session_dir())
                        if not x["active"] and x["messages"]][:3]
        except Exception:  # noqa: BLE001
            sessions = []
        if sessions:
            board.append(heading("Pick up where you left off"))
            listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listing.add_css_class("boxed-list")
            for session in sessions:
                row = Adw.ActionRow(title=GLib.markup_escape_text(session["title"]),
                                    subtitle=f"{session['messages']} messages",
                                    activatable=True)
                row.add_prefix(Gtk.Image(icon_name="chat-message-new-symbolic"))
                row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
                row.connect("activated", activate("switch-conversation",
                                                  GLib.Variant("s", session["id"])))
                listing.append(row)
            board.append(listing)

        # -- quick actions -------------------------------------------------
        wanted = [
            ("Transcribe a recording", "audio-x-generic-symbolic", "transcribe-file", None),
            ("Record a meeting", "media-record-symbolic", "", None),
            ("Connect a device", "bluetooth-symbolic", "connect-device", GLib.Variant("s", "")),
            ("Work on a project", "folder-open-symbolic", "choose-project", None),
            ("Dictate", "audio-input-microphone-symbolic", "dictate", None),
        ]
        actions = []
        for label, icon, name, value in wanted:
            if not name:
                actions.append((label, icon, lambda *_: self._show_surface("meetings")))
            elif app is None or app.lookup_action(name) is not None:
                # An action this app does not register is no button at all.
                actions.append((label, icon, activate(name, value)))
        board.append(heading("Do something"))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8,
                           row_spacing=8, max_children_per_line=3, homogeneous=True)
        for label, icon, callback in actions:
            button = Gtk.Button(child=Adw.ButtonContent(icon_name=icon, label=label))
            button.add_css_class("pill")
            button.connect("clicked", callback)
            flow.append(button)
        board.append(flow)
        return board

    def _on_suggestion_clicked(self, _button: Gtk.Button, suggestion: str) -> None:
        """Put a suggestion in the input, ready to send or edit."""
        self._set_input_text(suggestion)
        self._input_entry.grab_focus()

    def open_help(self) -> HelpWindow | None:
        """Open the capability list, or raise the one already open.

        Returns the window so a caller can wait on it; `None` when there is
        nothing to show, which is a real state - a build whose skill registry
        failed to load has no capabilities, and an empty help window would be
        worse than no button.
        """
        if not self._caps:
            logger.info("no capabilities to show; skill registry was empty")
            return None
        if self._help_window is not None:
            self._help_window.present()
            return self._help_window
        self._help_window = HelpWindow(
            self._caps, self._config, self._on_help_try, parent=self
        )
        self._help_window.connect("close-request", self._on_help_close)
        self._help_window.present()
        return self._help_window

    def _on_help_try(self, _button: Gtk.Button, prompt: str) -> None:
        self._on_suggestion_clicked(_button, prompt)
        if self._help_window is not None:
            self._help_window.close()

    def _on_help_close(self, _window) -> bool:
        self._help_window = None
        return False

    def _setup_ui(self) -> None:
        self._install_body_notices()

        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=14,
            margin_bottom=14,
            margin_start=14,
            margin_end=14,
        )
        self._chat_column = main_box
        # Forward declaration: the header bar is built further down in the same
        # method, and the page that holds it has to be assembled before either.
        header_bar_holder = Adw.Bin()
        self._split = Adw.NavigationSplitView()
        self._sidebar_page = SidebarPage(
            self._app, self._show_chat, self._show_surface,
            on_close=self._hide_panels)
        self._split.set_sidebar(self._sidebar_page)
        self._content_view = Adw.NavigationView()
        self._chat_page = Adw.NavigationPage(
            child=self._content_view, title="Conversation")
        self._split.set_content(self._chat_page)
        # Registered with the window, not merely constructed: an `Adw.Breakpoint`
        # that no window knows about is never evaluated. Verified by rendering
        # at 600px - before this, neither the sidebar nor the rail collapsed and
        # the conversation was left in a 150px column.
        self._sidebar_breakpoint = SidebarBreakpoint.apply(self._split)
        self.add_breakpoint(self._sidebar_breakpoint)
        #: Whether `_apply_narrow_layout` last found this window narrow. `None`
        #: until it has run once, so the first evaluation is always a
        #: transition - `False == False` would otherwise skip the very
        #: correction a window opened narrow needs.
        self._narrow_applied = None
        # The width-driven half of the same behaviour; see
        # `_apply_narrow_layout` for why the breakpoint alone was not enough.
        self.connect("notify::default-width", lambda *_a: self._apply_narrow_layout())
        self.connect("notify::maximum-width", lambda *_a: self._apply_narrow_layout())
        # ...and once when the window is first shown, because a window opened
        # already narrow emits no width change - it starts at that width.
        self.connect("map", lambda *_a: GLib.idle_add(self._apply_narrow_layout))
        # ...and before the first frame, from the default size (see the
        # `width <= 0` branch in `_apply_narrow_layout`).
        self.connect("realize", lambda *_a: self._apply_narrow_layout())
        # A toolbar view with the header bar on top of the column, all of it
        # under a toast overlay - so a notice is a `Adw.Toast` that floats and
        # disappears rather than a line of text that permanently steals the room
        # where the answer is being written.
        self._toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header_bar_holder)
        toolbar.set_content(self._toasts)
        self._content_view.add(Adw.NavigationPage(
            child=toolbar, title="Conversation", tag=CHAT_TAG))
        self.set_content(self._split)

        # Header: an Adw.HeaderBar, not a row of widgets in a box.
        #
        # The buttons themselves are unchanged - same names, same actions, same
        # tooltips - but an `Adw.HeaderBar` is what gives them the title bar's
        # own background, the correct button sizing for the title's height, and a
        # working window-controls area, none of which a `Gtk.Box` inside a plain
        # window had. It also means the sidebar toggle below has somewhere real
        # to live.
        header_bar = Adw.HeaderBar()
        # No automatic back arrow. When the split view collapses it adds one that
        # goes to the panel list - the same thing the sidebar toggle beside it
        # does - so a narrow window showed two buttons for one action, and the
        # arrow on the conversation's own page read as "back to somewhere".
        header_bar.set_show_back_button(False)
        self._sidebar_toggle = Gtk.ToggleButton()
        # `sidebar-show`, not the hamburger: `open-menu` promises a menu, and
        # this opens a column. The rail toggle is its mirror image on the right.
        self._sidebar_toggle.set_icon_name("sidebar-show-symbolic")
        self._sidebar_toggle.add_css_class("flat")
        self._sidebar_toggle.set_tooltip_text("Show or hide the panels (F9)")
        self._sidebar_toggle.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Show or hide the panels"])
        self._sidebar_toggle.connect("toggled", self._on_sidebar_toggled)
        # ...and the other direction: the split view collapsing on its own has to
        # move the button, or the button lies about what is on screen.
        self._split.connect("notify::collapsed", self._on_split_collapsed)
        # `show_content` is the half that decides what a person can actually see,
        # and nothing outside this window changes it - so it needs its own
        # notification, or the button drifts the moment the panels are hidden and
        # anything else looks.
        self._split.connect("notify::show-content", self._on_split_collapsed)
        header_bar.pack_start(self._sidebar_toggle)
        header = Gtk.Label(label="Shani Chronoa")
        header.add_css_class("cajita-header")
        # May shorten. Unellipsised, the title reserved its full width and the
        # header bar as a whole needed 632px - wider than the 600px narrow
        # layout - so every narrow window clipped the right of the chat.
        header.set_ellipsize(Pango.EllipsizeMode.END)
        header.set_hexpand(True)
        header.set_halign(Gtk.Align.START)

        self._mic_icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        self._mic_icon.set_pixel_size(16)
        self._mic_icon.set_tooltip_text("Microphone is in use")
        self._mic_icon.add_css_class("flat")
        # **There was a `set_visible(False)` here**, with a docstring saying the
        # icon is hidden until the microphone is in use - and `_sync_from_state`
        # runs from `__init__` and shows it unconditionally, so the hide never
        # survived the constructor it was written in. The header therefore
        # always carries a microphone, and the line claimed otherwise. The
        # tooltip below is the honest statement of when it means anything.

        # Quick Ask and the browser are actions with keyboard shortcuts, which is
        # the same as being invisible: a shortcut nobody knows about is not a
        # feature. Both are buttons for the same reason they are actions - the
        # actions are what a global shortcut and a second window reach.
        quick_button = Gtk.Button()
        # Compose, not "?": with a question mark here and an "i" on Help, the
        # header had Help and Quick Ask the wrong way round to every reader who
        # has used a GNOME app. Every name below exists in Adwaita and Yaru.
        quick_button.set_icon_name("mail-message-new-symbolic")
        quick_button.add_css_class("flat")
        quick_button.set_valign(Gtk.Align.CENTER)
        quick_button.set_tooltip_text("Ask one quick question (Ctrl+Shift+A)")
        quick_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Ask one quick question"]
        )
        quick_button.set_action_name("app.quick-ask")

        self._browser_button = Gtk.Button()
        # `emblem-web-symbolic` is not in the installed theme - measured
        # `has_icon` False - so this button showed a missing-glyph box on a
        # machine where the feature worked. `web-browser-symbolic` exists.
        self._browser_button.set_icon_name("web-browser-symbolic")
        self._browser_button.add_css_class("flat")
        self._browser_button.set_valign(Gtk.Align.CENTER)
        self._browser_button.set_tooltip_text("Browse the web (Ctrl+Shift+U)")
        self._browser_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Browse the web"]
        )
        self._browser_button.set_action_name("app.open-browser")

        help_button = Gtk.Button()
        help_button.set_icon_name("help-browser-symbolic")
        help_button.add_css_class("flat")
        help_button.set_valign(Gtk.Align.CENTER)
        help_button.set_tooltip_text("What can Chronoa do?")
        help_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["What can Chronoa do?"]
        )
        help_button.connect("clicked", lambda _b: self.open_help())

        settings_button = Gtk.Button()
        settings_button.set_icon_name("emblem-system-symbolic")
        settings_button.add_css_class("flat")
        settings_button.set_valign(Gtk.Align.CENTER)
        settings_button.set_tooltip_text("Settings")
        settings_button.set_action_name("app.open-settings")

        # Setup: the wizard that installs the model, the ears and the voice.
        #
        # `app.setup` was registered in `application.py` and mentioned in the
        # shortcuts window, with no control anywhere in the UI to reach it - and
        # five panels dead-end into "turn it on in Settings" with no button that
        # leads there. An action with no control is the same class of thing this
        # file keeps finding: built, registered, and not reachable. The shortcut
        # exists for the same reason as the other two: a feature nobody can
        # invoke is not a feature.
        setup_button = Gtk.Button()
        # `system-run` draws a gear in Yaru - the same glyph as Settings beside
        # it, so the header showed two identical gears. Setup installs things.
        setup_button.set_icon_name("system-software-install-symbolic")
        setup_button.add_css_class("flat")
        setup_button.set_valign(Gtk.Align.CENTER)
        setup_button.set_tooltip_text("Set Chronoa up: the model, listening and the voice")
        setup_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Set Chronoa up"])
        setup_button.set_action_name("app.setup")
        self._setup_button = setup_button

        # Conversations: every saved conversation, newest first, with New.
        self._conversations_button = Gtk.MenuButton()
        self._conversations_button.set_icon_name("view-list-symbolic")
        self._conversations_button.add_css_class("flat")
        self._conversations_button.set_valign(Gtk.Align.CENTER)
        self._conversations_button.set_tooltip_text("Conversations")
        self._conversations_button.update_property([Gtk.AccessibleProperty.LABEL], ["Conversations"])
        self._conversations_popover = Gtk.Popover()
        self._conversations_popover.connect("show", lambda _p: self._fill_conversations())
        self._conversations_button.set_popover(self._conversations_popover)

        # A widget goes into the header bar OR the row, never both: appending to
        # `header_row` first and then packing the same button tripped
        # `adw_header_bar_pack_end`'s "child already has a parent" assertion four
        # times over, and every one of those buttons silently ended up nowhere.
        header_bar.set_title_widget(header)
        header_bar.pack_start(self._conversations_button)
        header_bar.pack_start(self._mic_icon)
        self._quick_button = quick_button
        self._help_button = help_button
        self._browser_available = True
        self._overflow_button = self._build_header_overflow()
        for widget in (quick_button, self._browser_button, help_button,
                       self._overflow_button, setup_button, settings_button):
            header_bar.pack_end(widget)
        # The page was assembled before the bar existed (the column is built
        # top-down), so the bar goes into the holder now. An `Adw.Bin` exists
        # exactly for this: one slot, fillable later, no re-parenting.
        header_bar_holder.set_child(header_bar)

        # The right rail: what it is doing, what it changed that can be taken
        # back, and how it is currently armed. The conversation is a
        # transcript - it grows downward and the useful part scrolls off - so
        # these three answers had nowhere to live. `main_box` keeps every
        # child it already had; the rail is a sibling, never a parent of it,
        # so nothing in the conversation's own layout moved.
        #
        # The rail is built *after* the column is complete, because it reads
        # state the column's own widgets report (the orb state, the live tool)
        # and pointing it at half-built widgets would show "Idle" forever.
        self._rail = None                      # built at the end of this method
        #: What this session's skills declared they made, newest last.
        self._artifacts: list = []
        self._rail_row = None
        #: Draft persistence: a timeout id, and a flag meaning "we are restoring,
        #: do not save what we are reading".
        self._draft_timeout = None
        self._draft_source = False
        #: The draft is restored once per window, not on every focus.
        self._draft_restored = False
        #: Which conversation the composer currently belongs to, or "" before
        #: the application has said. Empty means the single global draft, so
        #: nothing changes for a window that is never told.
        self._draft_key = ""
        self._build_palette()
        self._rail_toggle = Gtk.ToggleButton()
        self._rail_toggle.set_icon_name("sidebar-show-right-symbolic")
        self._rail_toggle.add_css_class("flat")
        self._rail_toggle.set_valign(Gtk.Align.CENTER)
        self._rail_toggle.set_tooltip_text("Show or hide the Now rail (F10)")
        self._rail_toggle.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Show or hide the Now rail"])
        self._rail_toggle.set_active(True)
        self._rail_toggle.connect("toggled", self._on_rail_toggled)
        header_bar.pack_end(self._rail_toggle)
        # **The palette's door.** Ctrl+K reached every panel, mode and command,
        # and nothing on screen said so - an index nobody knew existed. A
        # visible button, first on the left beside the panels toggle.
        search = Gtk.Button(icon_name="system-search-symbolic")
        search.add_css_class("flat")
        search.set_valign(Gtk.Align.CENTER)
        search.set_tooltip_text("Find anything: panels, settings, actions (Ctrl+K)")
        search.update_property([Gtk.AccessibleProperty.LABEL],
                               ["Find a panel, setting or action"])
        search.connect("clicked", lambda _b: self._toggle_palette())
        self._palette_button = search
        header_bar.pack_start(search)

        main_box.set_hexpand(True)
        self._chat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._chat_row.set_vexpand(True)
        self._chat_row.append(main_box)
        self._toasts.set_child(self._chat_row)

        self._orb = ChronoaOrbWidget()
        self._orb.set_halign(Gtk.Align.CENTER)
        self._orb.set_action_name("app.toggle-listening")
        self._orb.set_tooltip_text("Start listening (Enter)")
        main_box.append(self._orb)

        # Two separate lines: the state (what the assistant is doing) and a
        # transient detail (the last thing that changed). They used to share
        # one label, so any toggle notice overwrote the state.
        self._state_label = Gtk.Label(label="Ready")
        #: Set by `set_can_answer`; None means "nothing has said yet".
        self._can_answer = None
        self._state_label.add_css_class("cajita-state")
        self._state_label.set_halign(Gtk.Align.CENTER)
        main_box.append(self._state_label)

        # **The way out of "no model", on the screen that says it.**
        #
        # The state line says "No model yet" and its tooltip explains why, and
        # that was the whole of it: the person asking a question on a fresh
        # machine was told what was wrong in a tooltip they have to hover to
        # read, with nothing to click. Every route to a fix was elsewhere - the
        # header's setup button, or the Models panel - and both are one to three
        # steps away from the one screen that reports the problem.
        #
        # So the button appears exactly when `can_answer` is False and goes away
        # when it is not, driven from `set_can_answer` so it cannot disagree
        # with the label beside it. It is `app.setup`, the same action the
        # header button and `Ctrl+Shift+S` use, so all three are one path.
        self._setup_cta = Gtk.Button(
            label="Set Chronoa up", css_classes=["suggested-action", "pill"],
            halign=Gtk.Align.CENTER, visible=False,
            tooltip_text="Install a language model, and the ears and voice")
        # Deliberately not `set_action_name("app.setup")`: the header's setup
        # button already declares that action, and a tree walk for "the setup
        # button" must find the one that is always on screen rather than this
        # one, which is hidden until there is nothing to answer with. See
        # `set_can_answer`. It still activates the same `GAction`, so the
        # shortcut, the header button and this reach one implementation.
        self._setup_cta.connect(
            "clicked",
            lambda _b: self._app.activate_action("setup", None)
            if self._app is not None else None)
        self._setup_cta.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Set Chronoa up: install a language model"])
        main_box.append(self._setup_cta)

        self._detail_label = Gtk.Label(label="")
        self._detail_label.add_css_class("cajita-detail")
        self._detail_label.set_halign(Gtk.Align.CENTER)
        self._detail_label.set_visible(False)
        # **It wraps, and is bounded to two lines.** Measured 2026-10-14: the
        # line had no wrap and no ellipsize, and a realistic 150-character
        # refusal wanted 854px of a 455px chat column at the window's own
        # minimum - 399px of it clipped, so the part that named the file and the
        # limit was the part that did not fit. Wrapping alone would make a long
        # reason cost 5 lines, so it is bounded: 2 lines, with the last one
        # ellipsised, which measures 33px against 17px - and **only** for text
        # that needs it, so the empty case keeps the chrome exactly as measured.
        self._detail_label.set_wrap(True)
        self._detail_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._detail_label.set_lines(DETAIL_MAX_LINES)
        main_box.append(self._detail_label)

        # The two lanes the line holds, in the order they are consulted. Both
        # are read by `set_progress` before any setter has run, so they are
        # declared here rather than relying on a first assignment - the same
        # `getattr(self, name, None)` discipline the mixins use for exactly this
        # reason.
        self._status_aside = ""
        self._status_progress = ""

        # **The empty state's answer to "what can I ask?" is inside the
        # transcript**, not here, and this is where that decision is recorded
        # because the row that used to live here was the wrong half of it.
        #
        # There were two of them. This one showed five chips built from
        # `available_surfaces()` - the panel titles. `TranscriptView`'s own
        # `SuggestionBar` shows six, built from `capabilities.startup_suggestions`
        # and gated on the live skill registry, so a skill that is not loaded is
        # never offered. Measured, side by side, on a real window:
        #
        # | | this row | the SuggestionBar |
        # |---|---|---|
        # | chips | 5 | 6 |
        # | words | `Conversations, Compare, My files, Meetings, Phone link` | `Battery left?, What time?, Set a timer, Which model?, Search the web, Screenshot` |
        # | gated on | which panels exist | which **skills** are installed |
        # | shown | **never** - see below | yes |
        # | fit measured | no | 149px of a 162px viewport |
        #
        # **The panel titles teach the wrong thing.** "What can I ask?" is
        # answered by questions, and a list of page names answers "what can I
        # look at". For an app whose pitch is 256 named skills, the chips that
        # name panels point a newcomer at the panels.
        #
        # **And this row could not have shown anyway**, which is why the two
        # coexisted rather than competing. Its gate was `TranscriptView.is_empty()`,
        # and that called `self._rows.get_children()` on a `Gtk.Box` - a GTK3
        # spelling that raises `AttributeError` on every call. The caller caught
        # it and failed closed, so the row was permanently hidden while its own
        # test file was green, because that file asserted on the *other* row.
        #
        # Fixing the emptiness check without removing this would have put two
        # rows of questions on screen. So both happened together, and
        # `tests/test_the_empty_state_is_one_answer.py` holds the result.
        #
        # `is_empty()` is still a public question and still answers it -
        # a new empty state has somewhere to ask. It has no caller here.

        self._transcript = TranscriptView(
            config=self._config,
            caps=self._caps,
            on_suggestion=self._on_suggestion_clicked,
            reduce_motion=self._motion_is_reduced(),
            # No regenerate control until the application attaches one: a button
            # that cannot re-ask anything is a dead control in every transcript.
            on_regenerate=None,
            # A file write's card links straight to the Diff panel, which was
            # otherwise reachable only from the rail or by typing /diff.
            on_show_diff=lambda: self._show_surface("diff"),
            # Today, recent conversations and quick actions under the
            # suggestions - the empty window was an orb and 200px of nothing.
            home_factory=self._home_board,
        )
        # Search this conversation (Ctrl+F). A search *field* in the window, not
        # a skill and not the desktop search provider: the two things a person
        # searches for are "what did I ask before" (a conversation) and "what is
        # on my screen" (the search provider), and neither is the other.
        self._search_bar = Gtk.SearchBar()
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_hexpand(True)
        self._search_entry.set_placeholder_text("Find in this conversation")
        self._search_entry.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Find in this conversation"])
        self._search_entry.connect("search-changed", lambda e: self._run_search(e.get_text()))
        self._search_entry.connect("activate", lambda _e: self._search_next())
        self._search_bar.set_child(self._search_entry)
        self._search_bar.connect_entry(self._search_entry)
        main_box.append(self._search_bar)

        # What each turn cost. `turn_stats()` was already computed on every turn
        # and read by nothing - so this is a number that existed since the
        # assistant was written and reached nobody. Time and tokens only; the
        # pricing is deliberately left to whoever knows it.
        self._transcript.set_turn_footer_handler(self._turn_stats_for_footer)
        self._transcript.set_revert_turn_handler(self._revert_turn)

        # **The minimap, beside the transcript rather than inside it.** The
        # transcript is a ScrolledWindow, and the ruler has to stay still while
        # the conversation moves under it - so it is a sibling, not an overlay,
        # which also means it cannot be scrolled out of view by accident.
        #
        # It jumps by calling the transcript's own `scroll_to_turn`, the same
        # one search uses. A second way to reach a turn is a second place for
        # "which turn is this" to be answered differently.
        self._minimap = TurnMinimap(on_jump=self._minimap_jump)
        transcript_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=4)
        # **Expands, always.** Its width used to come from its content: with
        # nothing but an approval drawn under it, measured 58px of a 722px row,
        # so the request the approval was about disappeared from view.
        self._transcript.set_hexpand(True)
        transcript_row.append(self._transcript)
        transcript_row.append(self._minimap)
        main_box.append(transcript_row)

        # The map follows the scroll rather than the other way round: a
        # `value-changed` handler that does a handful of arithmetic and a class
        # toggle, and which is checked for raising because it runs on every
        # pixel of every scroll in a long conversation.
        self._minimap_adjustment = None
        scroll = self._transcript.get_vadjustment()
        if scroll is not None:
            scroll.connect("value-changed", self._on_minimap_scroll)
            self._minimap_adjustment = scroll
        self._transcript.connect("turn-added", self._on_turn_added)

        # **Escape stops the turn.** The stop-*speaking* button beside the
        # composer has claimed "(Esc)" in its tooltip since before it was wired
        # to anything, so the shortcut was advertised and did not exist - and the
        # button it was on could never appear either. Now there is a key that
        # does what that tooltip says, on a control that does.
        #
        # Scoped to a turn in progress: with nothing running, Escape is left
        # alone, because a global Escape that stops nothing is the same kind of
        # claim as everything else this file keeps catching.
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(keys)

        # Where an `ask_user` question appears. Hidden until a question is
        # actually pending, so it costs nothing the rest of the time.
        self._question_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        # `cajita-question` is gone for the reason `composer-model-row` is -
        # applied, styled by nothing anywhere. This row is also the one surface
        # AGENTS.md records as having no render target at all, so it is the last
        # row that should be carrying an unfulfilled claim about itself.
        self._question_row.set_visible(False)
        main_box.append(self._question_row)

        # **The palette is parented here, and until this line it was never on
        # screen.** `_build_palette` built it, fed it 50 entries, set it
        # invisible and wired the key handler - and added it to **nothing**.
        # Measured on the built window before this change: before the press
        # `visible=False parent=None`; after the real handler returned `True`,
        # `visible=True parent=None`; 400 ms later `mapped=False` and **not in
        # the window's widget subtree** at all. A `Gtk.Box` cannot be a
        # toplevel, so there was never a surface for it to appear on.
        #
        # **Why it survived**: `tests/test_command_palette.py` builds the widget
        # standalone, where everything works, and
        # `test_shortcuts_are_discoverable.py` asserts the *text* "Ctrl+K for
        # anything else". Nothing ever asked whether it was in the window -
        # which is this repository's own "a test that calls the function is not
        # a test that the skill is reachable", in a widget.
        #
        # **Directly under the transcript, and inline rather than overlaid**,
        # because the transcript is `vexpand`: showing the palette takes its
        # height out of the conversation and hiding it gives the space back, so
        # nothing moves and the composer is never pushed off. Same idiom as
        # `_question_row` above.
        main_box.append(self._palette)
        self._pending_question = None
        self._question_widgets: list = []

        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        # The composer's own box, so the placeholder can sit *in* the field
        # rather than beside it - a label to the left of the text view reads as
        # a caption, not as the prompt GTK's entry used to draw.
        composer = Gtk.Overlay()
        composer.set_hexpand(True)
        # **A `Gtk.TextView`, which is the piece this comment used to call "a
        # separate piece of work".** Measured, because the first version of this
        # comment was simply wrong: a `Gtk.Entry` *can* hold a newline -
        # `Gtk.Entry().set_text("one\ntwo")` gives back `"one\ntwo"`. What it
        # cannot do is **show** it: one line, no wrapping, no vertical growth, so
        # a three-line request is three lines of text in a field one line tall and
        # the person cannot see or edit what they wrote. That is the limitation,
        # and it is about reading rather than storing.
        #
        # `Adw.Entry` is still not an option: measured, it does not exist on
        # libadwaita 1.5 (it arrived in 1.6).
        self._input_entry = Gtk.TextView()
        self._input_entry.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._input_entry.set_accepts_tab(False)
        # A `Gtk.TextView` has no placeholder, so the hint is a label sitting in
        # the same place GTK would have drawn one and hidden as soon as there is
        # a character. Losing the built-in placeholder outright would have been
        # the kind of quiet regression this file keeps catching.
        self._input_placeholder = Gtk.Label(label="Type a message…")
        self._input_placeholder.add_css_class("dim-label")
        self._input_placeholder.set_xalign(0.0)
        self._input_placeholder.set_valign(Gtk.Align.START)
        self._input_placeholder.set_margin_start(10)
        self._input_placeholder.set_margin_top(6)
        # A placeholder is not an accessible name: a screen reader announced
        # this field as just "text" (shani-testbed a11y-lint, chronoa-voice)
        self._input_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Message to Chronoa"])
        self._input_entry.add_css_class("cajita-input")
        self._input_entry.set_hexpand(True)
        # **A floor of one line.** A `Gtk.TextView` reports a minimum height of
        # almost nothing, so when the window is short of height - the empty
        # state, whose hint and suggestions do not scroll - the box hands it
        # that minimum and the placeholder is drawn over the composer's
        # toolbar. Measured at 460x640 and 1000x760 with nothing said yet.
        self._input_entry.set_size_request(-1, 32)
        # **A `Gtk.EventControllerKey`, not a `key-pressed` signal.**
        # `Gtk.TextView` has no such signal: `key-pressed` is the GTK 3 name and
        # GTK 4 widgets take an event controller instead. Connecting to it
        # raised `TypeError: unknown signal name: key-pressed` the moment the
        # window was built, and every test that constructs the window errored -
        # measured as 8 failures and 11 errors once anything did. The two suites
        # I ran before committing this never built the composer, and the test
        # written for it checks the source text, so both were blind to it.
        composer_keys = Gtk.EventControllerKey()
        composer_keys.connect("key-pressed", self._on_input_key_pressed)
        self._input_entry.add_controller(composer_keys)
        self._input_entry.get_buffer().connect("changed", self._on_input_changed)
        # The command menu: cline has a `SlashCommandMenu`, kimi-cli a
        # decorator registry, and here a list of names that each run something
        # real. `_sync_command_menu` decides when it is up - only for a leading
        # slash with no space yet.
        self._command_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._command_menu = Gtk.Popover()
        self._command_menu.set_child(self._command_list)
        self._command_menu.set_position(Gtk.PositionType.BOTTOM)
        GLib.idle_add(lambda: (self._parent_command_menu(), False)[1])
        # Typing must not imply "Enter submits" to everyone: it rules out
        # on-screen keyboards whose return key inserts a newline, and for a
        # motor-impaired user it is a gesture to get wrong repeatedly. The
        # button is the primary action, so it is a real suggested-action and
        # stays present; Stop still appears only while something is speaking.
        self._send_button = Gtk.Button()
        self._send_button.set_icon_name("go-next-symbolic")
        self._send_button.add_css_class("suggested-action")
        self._send_button.set_valign(Gtk.Align.CENTER)
        self._send_button.set_tooltip_text("Send (Enter)")
        self._send_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Send message"]
        )
        self._send_button.connect("clicked", self._on_send_clicked)

        # Stop is only meaningful while something is being said, so it appears
        # then and disappears otherwise rather than sitting there disabled.
        self._stop_button = Gtk.Button()
        self._stop_button.set_icon_name("media-playback-stop-symbolic")
        self._stop_button.add_css_class("flat")
        self._stop_button.set_valign(Gtk.Align.CENTER)
        self._stop_button.set_tooltip_text("Stop speaking (Esc)")
        self._stop_button.set_action_name("app.stop-speaking")
        self._stop_button.set_visible(False)

        # Attachments: files dropped on the window or added with the paperclip
        # go with the next message as paths (see attachments.py), shown as
        # chips until it is sent; a chip's click removes it.
        self._attachments: list = []
        self._attach_bar = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                       max_children_per_line=4, column_spacing=6, row_spacing=6)
        self._attach_bar.set_visible(False)
        self._attach_button = Gtk.Button()
        self._attach_button.set_icon_name("mail-attachment-symbolic")
        self._attach_button.add_css_class("flat")
        self._attach_button.set_valign(Gtk.Align.CENTER)
        self._attach_button.set_tooltip_text("Attach files (or drop them on this window)")
        self._attach_button.update_property([Gtk.AccessibleProperty.LABEL], ["Attach files"])
        self._attach_button.connect("clicked", lambda _b: self._pick_files())
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_files_dropped)
        self.add_controller(drop)

        input_row.append(self._attach_button)
        # **Dictate, beside the paperclip.** `app.dictate` existed with
        # Ctrl+Shift+D and nothing else - not a button, not the palette - and
        # the mode strip's own docstring promised it "stays with the mic button
        # that shares its subject", a button that was never built. It is one long
        # spoken turn (up to five minutes, ended by silence), so it sits with the
        # other ways of putting something into the message.
        self._dictate_button = Gtk.Button()
        self._dictate_button.set_icon_name("audio-input-microphone-symbolic")
        self._dictate_button.add_css_class("flat")
        self._dictate_button.set_valign(Gtk.Align.CENTER)
        self._dictate_button.set_action_name("app.dictate")
        self._dictate_button.set_tooltip_text(
            "Dictate: speak a long message, it ends when you stop talking "
            "(Ctrl+Shift+D)")
        self._dictate_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Dictate a message"])
        input_row.append(self._dictate_button)
        # **The overlay, not the text view.** The migration to a `Gtk.TextView`
        # built `composer = Gtk.Overlay()`, gave the entry a real placeholder-less
        # field - and then appended `self._input_entry` here, so the overlay was
        # never given either of its children and `self._input_placeholder` had
        # **no parent at all**. Measured on a built window: the hint reported
        # `get_visible() == True` while not being in the tree, so the composer
        # had **no placeholder** and the "Type a message…" prompt had been lost.
        #
        # `tests/test_composer_and_escape.py::test_the_placeholder_is_replaced_
        # not_dropped` has asserted against this since the migration was
        # committed, and was red from that commit - the two suites run before it
        # were committed never built a window, which is the third time this
        # session that "no test exercised it" turned out to mean "no test could
        # have failed either".
        composer.set_child(self._input_entry)
        composer.add_overlay(self._input_placeholder)
        # **The text field is the card's first line, and this row its second.**
        # See `composer_card` below: the field, the model it goes to, how hard
        # that model thinks and Send are one act - writing a message - so they
        # are one bordered unit rather than four rows stacked under the
        # conversation, which measured ~185px of a 640px window.
        #
        # **"Answers with" rides here, beside the attachment button**, because
        # the question it answers is asked at the moment of typing. It used to
        # be a row of its own between the conversation and the strips.
        input_row.append(self._model_row())
        toolbar_gap = Gtk.Box()
        toolbar_gap.set_hexpand(True)
        input_row.append(toolbar_gap)
        input_row.append(self._stop_button)
        # **Stopping the turn, which the button above does not do.** That one is
        # `app.stop-speaking` - it stops the *voice* after the answer is made,
        # and it is shown whenever the state is SPEAKING or THINKING
        # (`_sync_from_state`), so "Stop speaking (Esc)" is a tooltip on a button
        # that appears on its own. A turn
        # that is thinking, streaming or mid-skill had no control at all:
        # `Decision.CANCEL` existed and worked, but only from inside the approval
        # prompt, so a turn could be stopped at exactly one moment - the one where
        # it asked permission. This is that decision reachable from outside it.
        self._turn_stop_button = Gtk.Button()
        self._turn_stop_button.set_icon_name("process-stop-symbolic")
        self._turn_stop_button.add_css_class("flat")
        self._turn_stop_button.set_valign(Gtk.Align.CENTER)
        self._turn_stop_button.set_tooltip_text(
            "Stop this turn. It ends before the next action, so whatever is "
            "already running finishes first.")
        self._turn_stop_button.connect("clicked", self._on_turn_stop_clicked)
        self._turn_stop_button.set_visible(False)
        input_row.append(self._turn_stop_button)

        # **Undo, and it names what it will undo.** `undo_last_change` is
        # *global* - it undoes the last change, and its own docstring separates
        # that from "revert to some earlier state I happened to have in mind" -
        # so a button on an individual turn would say "undo this" and undo
        # something else. That is the defect this session keeps finding, so it
        # lives here instead: always in the composer, always about the most
        # recent change, and `recent_changes()` supplies the path so the label
        # is not a guess.
        #
        # **Hidden when there is nothing to undo.** A button that appears to
        # have nothing behind it is the same shape as the stop-speaking button
        # above, which is built, hidden and never revealed.
        self._undo_button = Gtk.Button()
        self._undo_button.set_icon_name("edit-undo-symbolic")
        self._undo_button.add_css_class("flat")
        self._undo_button.set_valign(Gtk.Align.CENTER)
        self._undo_button.connect("clicked", self._on_undo_clicked)
        self._undo_button.set_visible(False)
        input_row.append(self._undo_button)
        self._refresh_undo_button()

        input_row.append(self._send_button)

        # **The model picker, under the composer.** Below the field and beside
        # it rather than on the mode strip, because "which model answers" is
        # asked at the moment of typing and answered once - not glanced at while
        # reading a reply. The strip is five words wide and a sixth saying
        # "Medium (2.5 GB)" is a word nobody reads; down here there is room for
        # the sentence that goes with it.
        #
        # A row of its own rather than packed into `input_row` so the send
        # button keeps its place and the picker does not fight the composer for
        # width.
        main_box.append(self._attach_bar)
        # The organ strip sits directly above the composer, always present, so
        # "is it listening right now" is answerable without a menu. It is not
        # hidden when nothing is happening: an indicator that only exists while
        # the camera is on cannot answer "is it on now", and an empty strip looks
        # exactly like a broken one.
        self._organ_strip = OrganStrip(self._config)
        # Centred now - `OrganStrip.do_measure` reports the width the lights
        # need rather than FlowBox's widest-child-times-count. The history of
        # why the two earlier attempts did nothing is kept below.
        #
        # It was left-aligned in the column, 86px off the centre of the mode
        # chips below it. **Two attempts to centre it, both measured to do
        # nothing:**
        # `set_halign(Gtk.Align.CENTER)` on the strip sets the property and
        # changes no pixel: a vertical `Gtk.Box` hands its child the full
        # cross-axis width whatever the child's alignment is (measured: the
        # strip was allocated 680px of a 680px box with `halign=CENTER`).
        # Wrapping it in a horizontal row with two expanding spacers is the
        # usual trick and also did nothing, because there is no slack to
        # distribute: the strip *fills* its container. The reason is a number
        # nobody had looked at - `strip.measure(HORIZONTAL, -1)` reports a
        # natural width of **834px** against a 680px chat column, so the flow
        # box is always over-full and always takes the whole width. `Gtk.Center`
        # and `Gtk.Alignment` are both gone in GTK4 and `Gtk.FlowBox` has no
        # `justify`, so the next thing to measure is where those 834px come
        # from: the `.organ-strip > flowboxchild { padding: 0 }` rule is worth
        # 350px of it and its natural width suggests it is not matching.
        # Appended below, on one row with the mode strip.
        # The mode strip sits between the organ strip and the composer:
        # what Chronoa *is* (its modes) rather than what it is *doing*
        # (its organs). Wake word, plan mode, privacy and dictation are
        # four switches the user can flip, and a strip that shows which
        # are on is the answer to "what is Chronoa" without opening
        # Settings. Each chip is a toggle button wired to its own action,
        # so the state shown and the control that changes it are the same
        # widget - a label that says "on" and a switch elsewhere is two
        # widgets that can disagree.
        self._mode_strip = _ModeStrip(self._app, self._config)
        # The model button was drawn before the strip existed, so it could not
        # read where answers go; now it can.
        self._refresh_model_word()
        # **One row: what Chronoa is on the left, what it is doing on the
        # right.** The two strips were two centred rows of their own, and with
        # the organ lights captioned they wrapped onto a third at the default
        # width. Idle lights are icons only now (`OrganStrip.refresh`), so both
        # fit one line at 460px and the line is the conversation's width,
        # aligned with the composer card under it.
        # The lights take the spare width, not the chips: an expanding strip
        # would leave the chips at its start with empty width beside them.
        self._mode_strip.set_halign(Gtk.Align.START)
        self._organ_strip.set_hexpand(True)
        self._organ_strip.set_halign(Gtk.Align.END)
        self._organ_strip.set_valign(Gtk.Align.CENTER)
        self._organ_strip.set_margin_top(0)
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_row.set_margin_start(8)
        status_row.set_margin_end(8)
        status_row.append(self._mode_strip)
        status_row.append(self._organ_strip)
        self._status_row = status_row
        main_box.append(reading_width(status_row))

        # **Nothing rides beside Send any more.** The answer button that sat
        # here carried plan mode and the reply style; plan moved to the work
        # chip and the style to the Thinking button, each beside what it
        # qualifies, so the toolbar is the message's own controls only.

        # The task card, above the composer: OpenHands and cline both keep the
        # plan in the conversation rather than behind a panel, because a plan
        # you have to go and look for is not a plan you are following. Hidden
        # entirely when nothing is outstanding - an empty card is a permanent
        # tax on attention for no information.
        self._task_card = task_card.TaskCard()
        main_box.append(self._task_card)
        # Same reading width as the conversation above it, so the composer lines
        # up with the turns instead of running the full width of the window.
        composer_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        composer_card.add_css_class("composer-card")
        composer_card.append(composer)
        composer_card.append(input_row)
        self._composer_card = composer_card
        main_box.append(reading_width(composer_card))
        # ...and re-read whenever the window comes back, because the Settings
        # window flips these same settings through the same actions. A strip
        # that only refreshed on its own click would keep saying "off" for a
        # mode that is on, which is the one thing an indicator must not do.
        self.connect("notify::is-active", self._on_window_activated)

        # The rail, last, because it reads the column it sits beside: the state
        # word comes from `_state_label` and the tool name from
        # `_detail_label`. Built earlier it rendered "Idle" while the window
        # said "Ready" - not because either was wrong, but because the label it
        # reads did not exist yet. A rail that answers a question from
        # half-built widgets is worse than one built a moment later.
        self._rail = rail_module.NowRail(get_state=self._rail_state,
                                         get_tool=self._rail_tool,
                                         on_open=self._show_surface,
                                         get_context=self._rail_context,
                                         get_artifacts=self._rail_artifacts,
                                         get_events=self._rail_events,
                                         get_senses=self._rail_senses)
        self._chat_row.append(self._rail)
        # **Hidden, not shrunk, on a narrow window.** At a fraction of its width
        # the rail wraps every label into two and three lines and reads as a
        # column of broken words; the conversation is what the window is for.
        # Same reasoning as `SidebarBreakpoint` on the left, and the toggle stays
        # available either way, so nothing becomes unreachable.
        self._rail_breakpoint = rail_module.RailBreakpoint.apply(self._rail, 1040)
        self.add_breakpoint(self._rail_breakpoint)
        # Two seconds, because that is the smallest number that makes a countdown
        # read as a countdown rather than as a stuck label.
        self._rail_ticket = GLib.timeout_add_seconds(2, self._on_rail_tick)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    # -- narrow-window behaviour ------------------------------------------

    #: Below this width the sidebar cannot stay a column, and becomes a drawer
    #: behind the ☰ button instead.
    SIDEBAR_COLLAPSE_WIDTH = 720

    def _apply_narrow_layout(self) -> None:
        """Collapse the sidebar by hand when the window is narrow.

        **The breakpoint alone does not do it, and this is the measurement.**
        With `SidebarBreakpoint` registered and firing, `split.collapsed` was
        still `False` at 600px, while `split.set_collapsed(True)` called after
        allocation returned `True` every time. The setter therefore runs before
        the split view finishes setting itself up, and the split view then
        overwrites it - and a breakpoint only re-applies when its *condition*
        changes, so a window that never changes width is never corrected again.

        So the condition is evaluated here as well, on every width change. It
        goes through `_set_panels_visible`, not `set_collapsed` alone, because
        collapsing *and* leaving the drawer open covers the whole window with
        the panel list - measured at 640px: the render showed the panel list
        filling the screen with the conversation nowhere in it, which is the
        same failure `_set_panels_visible`'s own docstring records for F9. The
        pair (`collapsed` for the layout, `show_content` for which pane is on
        top) is the contract, and one half of it is a bug.

        Widening back does not re-open the panels if the person had closed them:
        it only stops the window from *forcing* them shut.

        **Which is why this runs on a transition and not on every notification.**
        The first version re-evaluated the condition every time the width
        changed, and at a wide width its "widening" branch was
        `if collapsed: set_collapsed(False)` - so *any* late width notification
        re-opened panels the person had just closed with the toggle. Measured as
        `tests/test_sidebar_toggle.py`'s two round-trip tests failing
        intermittently (2 of 5 runs with this branch, 0 of 5 without it), which
        is the shape of a race: the notification lands after the press about
        half the time.
        """
        width = self.get_width()
        if width <= 0:
            # **Before the first allocation, the size it is about to get.**
            # Returning here meant the first frame was drawn uncollapsed -
            # measured, a 665px split in a 460px window until the idle after
            # `map` ran: a visible flash of a clipped window on every start.
            width = self.get_default_size()[0]
        if width <= 0:
            return
        if getattr(self, "_mode_strip", None) is not None:
            self._mode_strip.set_compact(width < _ModeStrip.COMPACT_BELOW)
        self._set_composer_compact(width < _ModeStrip.COMPACT_BELOW)
        narrow = width <= self.SIDEBAR_COLLAPSE_WIDTH
        if narrow != self._narrow_applied:
            self._narrow_applied = narrow
            if narrow:
                self._set_panels_visible(False)
            elif self._split.get_collapsed():
                self._split.set_collapsed(False)
            self._fold_header(narrow)
        self._sync_sidebar_toggle()

    def _set_composer_compact(self, compact: bool) -> None:
        """Shorter words in the composer's toolbar on a narrow window.

        The same threshold as the mode strip's, so the two rows change shape
        together. Only words go: "Answers with" (the model's own name stays) and
        "Thinking" becomes "Think". Tooltips and accessible names are unchanged.
        """
        compact = bool(compact)
        if compact == getattr(self, "_composer_compact", None):
            return
        self._composer_compact = compact
        caption = getattr(self, "_model_picker_caption", None)
        if caption is not None:
            caption.set_visible(not compact)
        self._refresh_thinking_word()

    def _build_header_overflow(self) -> Gtk.MenuButton:
        """A "More" button holding Quick ask, Browse and Help on a narrow window.

        Setup and Settings stay in the bar at every width - an action reachable
        only from a closed menu is the dead end `test_setup_button_is_reachable`
        exists for. These three are the ones a narrow window can spare: each
        also has a shortcut, and each opens a second window rather than acting
        on this one. Labelled buttons, not a menu model, so each is a real
        control with its own tooltip and accessible name.
        """
        button = Gtk.MenuButton()
        button.set_icon_name("view-more-symbolic")
        button.add_css_class("flat")
        button.set_valign(Gtk.Align.CENTER)
        button.set_tooltip_text("More")
        button.update_property([Gtk.AccessibleProperty.LABEL], ["More actions"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover = Gtk.Popover()
        popover.set_child(box)

        def item(label, icon, action=None, handler=None):
            entry = Gtk.Button()
            entry.add_css_class("flat")
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            line.append(Gtk.Image.new_from_icon_name(icon))
            line.append(Gtk.Label(label=label, xalign=0.0))
            entry.set_child(line)
            entry.update_property([Gtk.AccessibleProperty.LABEL], [label])
            if action:
                entry.set_action_name(action)
            if handler:
                entry.connect("clicked", handler)
            entry.connect("clicked", lambda _b: popover.popdown())
            box.append(entry)
            return entry

        item("Ask one quick question", "mail-message-new-symbolic", action="app.quick-ask")
        self._overflow_browser = item("Browse the web", "web-browser-symbolic",
                                      action="app.open-browser")
        item("What can Chronoa do?", "help-browser-symbolic",
             handler=lambda _b: self.open_help())
        button.set_popover(popover)
        button.set_visible(False)
        return button

    def _fold_header(self, narrow: bool) -> None:
        """Move the three spare header buttons into "More", or back out."""
        self._quick_button.set_visible(not narrow)
        self._help_button.set_visible(not narrow)
        self._browser_button.set_visible(self._browser_available and not narrow)
        self._overflow_button.set_visible(narrow)

    def _on_window_activated(self, *_args) -> None:
        """The window came forward: re-read the modes from their owners.

        Settings flips plan mode, privacy and the wake word through the same
        actions this strip does, so the only way a chip learns about a change
        made elsewhere is to look again. Reading is cheap - three gsetting
        reads and one module flag - and it happens on focus, not on a timer.
        """
        self._note_what_happened_while_away()

        # **The draft, once per window and never again.** Restoring on *every*
        # focus would put back text someone had deliberately cleared, the moment
        # they clicked away and came back - which is worse than not having the
        # feature at all, because it looks like the app is ignoring them.
        if not self._draft_restored:
            self._draft_restored = True
            self.restore_draft()
        if self._mode_strip is not None:
            self._mode_strip.refresh()
        if getattr(self, "_task_card", None) is not None:
            self._task_card.refresh()

    #: Below this, the window was not really away and a catch-up line would be
    #: noise: alt-tabbing to a terminal for ten seconds is not "coming back".
    AWAY_MINIMUM_SECONDS = 60

    #: What the rail's sources held the last time the window was looked at.
    #: `None` until the first focus, because "nothing yet" and "nothing since
    #: you looked" are different sentences.
    _away_snapshot = None

    def _away_counts(self) -> tuple:
        """(files made, rules fired) as the rail's own sources report them.

        **Read through the rail's getters, not a second copy of the state.** A
        separate tally is one that can disagree with the panel beside it, which
        is the failure this window has produced before.
        """
        try:
            made = len(self._artifacts)
        except Exception:                                   # noqa: BLE001
            made = 0
        fired = 0
        source = getattr(self, "_event_source", None)
        if source is not None:
            try:
                fired = len(source.event_results())
            except Exception:                               # noqa: BLE001
                fired = 0
        return made, fired

    def _note_what_happened_while_away(self) -> None:
        """One line in the transcript saying what changed while you were not here.

        **A count of what grew, not a list of everything.** Nothing here is
        timestamped, so "since you left" is measured as a delta between two
        readings of the same sources - which is exact for counting and says
        nothing about *when*, so the sentence must not imply it did.

        **Silent on the first focus, and when nothing changed.** "0 files and 0
        rules fired" is worse than nothing: it makes the reader check whether
        they are looking at the right window.

        **Not shown while the window was barely away.** Alt-tab to a terminal
        and back should not produce a notice about nothing.
        """
        now = time.monotonic()
        counts = self._away_counts()
        previous, left_at = self._away_snapshot or (None, None)
        self._away_snapshot = (counts, now)
        if previous is None or left_at is None:
            # **Two conditions, one case.** `left_at is None` *is* the first
            # look - the snapshot carries both - so the second half of this
            # condition is the one that does the work. Both are stated because
            # removing the first does not change behaviour, which was measured:
            # a mutation deleting it passed all nine tests, and the reason is
            # recorded here rather than left as an un-caught mutation.
            return
        if now - left_at < self.AWAY_MINIMUM_SECONDS:
            return                       # barely away: alt-tab, not "back"
        new_files, new_rules = counts[0] - previous[0], counts[1] - previous[1]
        if not new_files and not new_rules:
            return
        parts = []
        if new_files:
            parts.append(f"{new_files} file(s) made")
        if new_rules:
            parts.append(f"{new_rules} rule(s) fired")
        away = int(now - left_at)
        if away >= 3600:
            span = f"{away // 3600}h away"
        else:
            span = f"{away // 60}m away"
        sentence = f"While you were {span}: " + " and ".join(parts) + "."
        self.add_notice_row(sentence, kind="away")

    def set_state(self, state: AssistantState) -> None:
        """Set the assistant state. The single entry point for it."""
        self._state = state
        self._sync_from_state()
        # Mirrored to `app.status`, which the top-bar indicator watches.
        app = self.get_application()
        status = app.lookup_action("status") if app is not None else None
        if status is not None:
            status.set_state(GLib.Variant("s", state.value))
        # A turn-stop is only meaningful while a turn exists, and `set_state` is
        # the one place that knows. Driven here rather than in `_setup_ui` so it
        # cannot end up built-but-never-shown, which is what happened to the
        # stop-*speaking* button above.
        stop = getattr(self, "_turn_stop_button", None)
        if stop is not None:
            stop.set_visible(state != AssistantState.IDLE)
        # The rail answers "what is it doing" from the state line, so it is
        # refreshed here as well as on its tick: a state change is the moment
        # the rail's answer must not lag behind the orb.
        if getattr(self, "_rail", None) is not None:
            self._on_rail_tick()
        for observer in getattr(self, "state_observers", ()):
            try:
                observer(state)
            except Exception as e:  # an observer must never break the UI
                logger.error(f"State observer failed: {e}")

    def get_state(self) -> AssistantState:
        return self._state

    def _sync_from_state(self) -> None:
        """Derive every state-dependent widget from `self._state`.

        Called from exactly one place, which is what makes it impossible for
        the orb and the status line to show different things.
        """
        self._orb.set_state(self._state)
        # The halo pulses only while the mic is hot or the reply is being heard;
        # the last level must not freeze on a thinking/idle halo.
        if self._state not in (AssistantState.LISTENING, AssistantState.SPEAKING, AssistantState.INTERRUPTING):
            self._orb.set_level(0.0)
        # `set_can_answer` wins over the state machine's own label, because "no
        # model" is true of every state at once while "Ready" is only true of
        # one - and on a machine with nothing installed the label was showing the
        # one.
        if self._can_answer is False:
            self._state_label.set_label("No model yet")
            self._state_label.add_css_class("dim-label")
        else:
            self._state_label.set_label(self._voice_aware_label())
            self._state_label.remove_css_class("dim-label")
        dictating = self._is_dictating()
        # Built later than the first sync, which runs during construction.
        dictate = getattr(self, "_dictate_button", None)
        if dictate is not None:
            dictate.set_tooltip_text(
                "Stop dictating (Ctrl+Shift+D)" if dictating else
                "Dictate: speak a long message, it ends when you stop talking "
                "(Ctrl+Shift+D)")
        # The mic is genuinely hot in exactly two states.
        hot = self._state in (AssistantState.LISTENING, AssistantState.INTERRUPTING)
        self._mic_icon.set_visible(True)
        self._mic_icon.set_from_icon_name(
            "audio-input-microphone-symbolic" if hot else "microphone-disabled-symbolic"
        )
        self._mic_icon.set_tooltip_text(
            "Microphone is in use" if hot else "Microphone idle"
        )
        self._mic_icon.remove_css_class("mic-off")
        if hot:
            self._mic_icon.add_css_class("mic-off")
        # Stop works while thinking too: it cancels the turn (app._stop_speaking)
        self._stop_button.set_visible(self._state in (AssistantState.SPEAKING, AssistantState.THINKING))
        self._stop_button.set_tooltip_text("Stop speaking (Esc)" if self._state is AssistantState.SPEAKING
                                           else "Stop thinking about this (Esc)")

    def _is_dictating(self) -> bool:
        return bool(getattr(self._app, "_dictating", False))

    def _voice_aware_label(self) -> str:
        """The state word, plus what the voice setting means right now.

        **Two voice states looked like others.** Dictation drew "Listening..."
        - the same as a twenty-second question, though it runs five minutes and
        ends on a longer silence - and an armed wake word drew "Ready", the
        same as a mic that only opens when pressed. The state machine is right
        about both; it just cannot say which kind of listening or which kind of
        ready, so the label adds it.
        """
        label = self._state.label
        if self._state is AssistantState.LISTENING and self._is_dictating():
            return "Dictating…"
        if self._state is AssistantState.IDLE:
            strip = getattr(self, "_mode_strip", None)
            try:
                hands = strip._read_hands_free() if strip is not None else "off"
            except Exception:                           # noqa: BLE001
                hands = "off"
            if hands in ("wake", "wake-over"):
                try:
                    phrase = str(self._config.wake_phrase or "").strip()
                except Exception:                       # noqa: BLE001
                    phrase = ""
                if phrase:
                    return f"Ready - say “{phrase}”"
        return label

    def set_input_level(self, level: float) -> None:
        """Feed the recorder's input level to the orb.

        Ignored unless listening, so a level that arrives after the turn ended
        cannot make the halo pulse at nothing.
        """
        if self._state is AssistantState.LISTENING:
            self._orb.set_level(level)

    def set_playback_level(self, level: float) -> None:
        """Feed the playback level to the orb (sayri's orb-follows-playback).

        Ignored unless speaking, so a level that arrives after the turn ended
        cannot make the halo pulse at nothing.
        """
        if self._state is AssistantState.SPEAKING:
            self._orb.set_level(level)

    def _on_close_request(self) -> bool:
        """A close with the wake word on hides, rather than quitting.

        The wake word is the only way a user can reach Chronoa without this
        window; quitting it over "close" would silence the hands-free path
        the user explicitly turned on. With the wake word off there is no
        hands-free path to keep alive, so the close proceeds normally and
        quits the app. Hold/release keeps the application alive while
        hidden; a real quit still ends it.
        """
        try:
            enabled = self._config.wake_word_enabled
        except Exception:            # noqa: BLE001 - never leave the user unable to close
            enabled = False
        if not enabled:
            # **Flushed before the close proceeds.** The pending save is ten
            # seconds of debounce, so a window closed within ten seconds of the
            # last keystroke would otherwise discard exactly the text it was
            # written to protect. Saving on close is not redundant with the
            # debounce - it is what makes the debounce safe.
            self._flush_draft_now()
            return False
        self._app.hold()
        self._holding = True
        self.set_visible(False)
        # Hidden rather than closed, but still "gone" from the person's point of
        # view, and still a moment they might not come back from.
        self._flush_draft_now()
        return True

    def _flush_draft_now(self) -> None:
        """Write any pending draft immediately, and disarm the timer."""
        if self._draft_timeout is not None:
            GLib.source_remove(self._draft_timeout)
            self._draft_timeout = None
            self._flush_draft()

    def show(self) -> None:
        """Bring the window back, releasing the hold the close-request took."""
        self.present()
        if getattr(self, "_holding", False):
            self._app.release()
            self._holding = False
        release_hidden = getattr(self._app, "_release_hidden_hold", None)
        if callable(release_hidden):
            release_hidden()

    # -- the right rail ----------------------------------------------------

    def _rail_state(self) -> str:
        """The live state word, read from the state line itself.

        `_state_label` is the single place "what is Chronoa doing" is spelled
        out - `set_state` derives it, and `set_can_answer` may override it for
        the no-model case. Reading the widget rather than keeping a third copy
        is the point: a rail that tracked state separately would be a second
        answer to that question, free to disagree with the orb and the line
        under it, which is the failure this window keeps finding in smaller
        forms.
        """
        label = getattr(self, "_state_label", None)
        return (label.get_label() if label is not None else "") or "Idle"

    def _rail_context(self):
        """The last request's context report, straight from the assistant.

        Through the application object, because that is where the assistant
        lives: `window.assistant` is the *window's* placeholder text helper in
        some builds, and a meter wired to the wrong `assistant` reads zero
        forever - which looks exactly like an assistant that uses no context.
        """
        app = getattr(self, "_app", None)
        assistant = getattr(app, "assistant", None)
        report = getattr(assistant, "context_report", None)
        return report() if callable(report) else None

    def _rail_tool(self) -> str:
        """The tool currently running - read from the **progress** lane.

        It used to read the detail label, which was correct only while the two
        lanes were one lane. Now an aside outranks a running reading on the
        label, so reading the label here would print *"Running Not done: that
        was outside what you allowed this time"* in the rail's Now section - the
        rail claiming a refusal was a tool, which is the shape this file records
        for two widgets answering one question.

        The progress lane is kept as its own field rather than parsed back out
        of the label, which is what makes it available here at all.
        """
        return (getattr(self, "_status_progress", "") or "").strip()

    def _on_rail_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._rail is not None:
            self._rail.set_visible(button.get_active())

    def _on_rail_tick(self) -> bool:
        """Refresh on a two-second tick, so a countdown is a countdown."""
        if self._rail is not None:
            try:
                self._rail.refresh()
            except Exception as exc:  # noqa: BLE001 - the tick must never die
                logger.warning("The Now rail failed to refresh: %s", exc)
        return True

    def toggle_rail(self) -> None:
        if self._rail_toggle is not None:
            self._rail_toggle.set_active(not self._rail_toggle.get_active())

    def set_orb_state(self, state: str) -> None:
        """Compatibility shim for the existing `app.py` call sites.

        Kept because the string form is what `app.py` already speaks, but it
        now goes through the same single state rather than poking the orb and
        the label separately.
        """
        resolved = _LEGACY_STATE_ALIASES.get(state)
        if resolved is None:
            try:
                resolved = AssistantState(state)
            except ValueError:
                resolved = AssistantState.IDLE
        self.set_state(resolved)

    # ------------------------------------------------------------------
    # Detail line (never touches state)
    # ------------------------------------------------------------------

    def set_can_answer(self, can: bool, why: str = "") -> None:
        """Whether anything can answer at all, which the state line must show.

        **The orb said "Ready" on a machine with no model**, and the line under
        it said "LLM unavailable" - two states, one screen, one of them false.
        "Ready" is the IDLE label, so it was true by construction on every
        machine: the state machine describes what the assistant is *doing*, and
        doing nothing is what idle means. What it must also answer is whether
        there is anything here to do it *with*.

        So this overrides the text when there is no model, and restores it when
        there is. Deliberately only the text and the dimming - the orb's colour
        still belongs to the state machine, because "nothing can answer" is not
        an error and painting it red would be the opposite kind of lie.
        """
        self._can_answer = bool(can)
        self._no_model_reason = why or ""
        # Shown only while there is nothing to answer with, so it cannot become
        # a second thing offering setup on a machine that is already set up.
        #
        # The header's own setup button is deliberately *not* hidden here. The
        # CTA answers "I cannot answer, fix it" on the screen that says so; the
        # header button answers "open the wizard", which is wanted on a working
        # machine too - to change the model, the voice, or to re-run a step. A
        # first version hid both and `tests/test_setup_button_is_reachable.py`
        # failed on it (`the setup button is not mapped`), which is the test
        # earning its keep: an action that is registered, named in the shortcuts
        # window and unreachable is the exact gap that file was written for.
        #
        # The CTA activates `app.setup` through a handler rather than through
        # `set_action_name`, and that is load-bearing rather than incidental.
        # Two widgets declaring the same action leaves "the setup button"
        # ambiguous, and a finder that walks the tree then picks whichever it
        # reaches first - which here is a control that is deliberately
        # invisible until there is no model. The header button stays the one
        # widget that declares the action; the CTA reaches the same
        # `GAction` through it, so there is still one implementation.
        self._setup_cta.set_visible(not can)
        if not can:
            self._state_label.set_tooltip_text(
                why or "Chronoa cannot answer until a language model is set up")
        else:
            self._state_label.remove_css_class("dim-label")
            self._state_label.set_tooltip_text("")

    def _install_body_notices(self) -> None:
        """Show a refusal on the detail line, the way a sound cannot.

        **The hook the body has always wanted.** `tools.py` imports no GUI and
        has no app reference, so the body is the one process-wide channel from a
        refusal inside the tool loop to something the person can see - and the
        organ strip cannot carry it, because a refusal is an *event* and that row
        has eight lights already.
        """
        try:
            from shani_chronoa import body
            body.body.set_notice(self.set_status)
        except Exception:                               # noqa: BLE001
            logger.warning("could not install the refusal notice",
                           exc_info=True)

    def set_status(self, status: str) -> None:
        """Record **what last happened**, and show it.

        Intentionally cannot change the state line. A notice is an aside; the
        state is the answer to "what is Chronoa doing", and one must not
        overwrite the other.

        **An aside outranks a running reading, and this is the whole rule.**
        Measured 2026-10-14 on the real window, through the real tool clock: one
        line, 95 writers across 11 modules, and three ways one message destroyed
        another - a refusal and a settings toggle were both gone within a second
        of a running tool, and anything said in the last second of a turn was
        erased before it was ever on screen. So the line holds two lanes and this
        is the precedence between them.

        The line therefore shows **the last thing that happened, until something
        else happens** - it is a notice, not a live reading, and it is not
        cleared by the turn loop (that is `set_progress("")`). The tool clock is
        the other lane: it says what is happening *while nothing else has*,
        which is why `Generate image... 12s` steps aside for a refusal and
        comes back when the next one arrives.
        """
        text = (status or "").strip()
        self._status_aside = text
        self._write_detail(self._status_aside or self._status_progress)
        if text and self._should_toast(text):
            self.toast(text)
        self._sync_sidebar_toggle()

    def set_progress(self, progress: str) -> None:
        """Record **what is happening right now**, without outliving the turn.

        The tool clock is the only writer, and the turn loop's three clears now
        come through here rather than `set_status("")`. That is what fixes the
        second collision: an end-of-turn clear used to erase whatever had just
        been said, so a refusal issued in the turn's last second was never shown
        at all - measured, `""` and hidden, with no window in which it existed.

        It writes nothing while an aside is showing, which is the first
        collision: a clock ticking once a second used to overwrite a refusal
        within a second of it being written.
        """
        self._status_progress = (progress or "").strip()
        if self._status_aside:
            return
        self._write_detail(self._status_progress)

    def _write_detail(self, text: str) -> None:
        """The one place the detail label is written, so the two lanes cannot
        disagree with each other or with the label.

        Two setters and two lanes with a precedence rule between them is three
        ways to answer "what does this line say"; funnelling the writes through
        one method is what keeps it one answer.
        """
        label = self._detail_label
        if label.get_label() != text:
            label.set_label(text)
        label.set_visible(bool(text))
        self._sync_sidebar_toggle()

    #: Notices worth interrupting for. A toast is for something a person would
    #: want to know about *now*; the rest are conversation-adjacent and stay in
    #: the detail line.
    _TOAST_PREFIXES = ("Asking again", "Attached ", "Kept in", "Saved to", "Copied")

    @staticmethod
    def _should_toast(text: str) -> bool:
        return text.startswith(ChronoaWindow._TOAST_PREFIXES)

    def toast(self, message: str, seconds: float = 2.5) -> None:
        """A real `Adw.Toast`, floating over the column and then leaving.

        `set_timeout` is a method, not a constructor argument - `Adw.Toast.new()`
        takes the message only, and passing `timeout=` raises `TypeError`. Two
        seconds is libadwaita's own default; a longer notice is set afterwards,
        because the constructor cannot be told.
        """
        if self._toasts is None:
            return
        notice = Adw.Toast.new(message)
        if seconds:
            notice.set_timeout(seconds)
        self._toasts.add_toast(notice)

    def clear_status(self) -> None:
        """Wipe the line entirely - **both** lanes.

        The full clear, and distinct from the turn loop's `set_progress("")`
        on purpose: this one is "forget everything you were told", which is what
        a caller asking for a blank line means. The turn's own clear is "the
        step finished", and must not take an aside with it.

        `tests/test_window_ux.py` asserts the detail label is hidden after this,
        so both lanes are cleared here rather than just the visible one.
        """
        self._status_aside = ""
        self._status_progress = ""
        self._write_detail("")

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------

    def set_response(self, text: str) -> None:
        """Record the assistant's reply.

        Empty text clears the transcript, which is what resetting the
        conversation needs; otherwise the current assistant turn is added or
        updated rather than the whole view being replaced.
        """
        if not (text or "").strip():
            self._transcript.show_placeholder()
            return
        self._transcript.add_assistant_turn(text)
        # A turn is when files get changed, so it is when the undo ring can have
        # gained something. Refreshed here rather than on a timer, so the button
        # appears with the change that caused it instead of up to a tick later.
        self._refresh_undo_button()

    # -- the panels -------------------------------------------------------

    # ── addressable pages ──────────────────────────────────────────────────
    #
    # The main window is one conversation view, so there is nothing here to
    # scroll to - but three of its states are real destinations someone else may
    # want to name: the conversation itself, the quick-question panel, and the
    # panel containing the conversation list. Each is a page id so a
    # notification or a keybinding can open one, and so a test does not have to
    # click a button whose position it guessed.
    def show_page(self, page_id: str) -> bool:
        from shani_chronoa import pages as page_registry
        from shani_chronoa.gui.surfaces import available_surfaces
        page_id = page_registry.resolve("main", page_id) or page_id
        if page_id == "conversation":
            self.present()
            return True
        if page_id == "quick-ask":
            action = self.lookup_action("app.quick-ask")
            if action is None:
                return False
            action.activate(None)
            return True
        if page_id in ("conversations", "panels"):
            self.toggle_sidebar()
            return True
        # **A surface id lands on that surface, not on the panel list.**
        # Without this branch a header button could only open the Panels and
        # leave the person at the list - measured, `pages.show("listening")`
        # returned False and logged `main refused 'listening'` with the page
        # registered and resolvable, because this method had no path to it.
        # Read from `available_surfaces()` rather than a kept list, so the next
        # surface works the day it is added and not the day someone remembers.
        if page_id in available_surfaces():
            # **Measured 2026-10-11, on a real `ChronoaWindow`:** this branch
            # used to open the sidebar and mark the row, and return True:
            #
            #     show_page("listening") -> True
            #     "listening" in _surface_pages -> **False**   (never built)
            #     show_content=False, collapsed=False          (chat still up)
            #
            # So the Listening button's `pages.show(...)` returned success and
            # left the person on the conversation with the sidebar open. The row
            # was highlighted, which is what made it read as *almost* working.
            #
            # The comment that used to sit here said calling `_show_surface` as
            # well would "run select twice and push a bare Gtk.Box". **Both
            # halves are wrong**: `SidebarPage.select` only adds and removes the
            # `selected` CSS class on rows, and pushes nothing - so there is no
            # double-select and nothing to push a bare box into. The real cost of
            # the deferral was that a header button and a sidebar row had two
            # different answers to "where this page is", and only one of them
            # arrived.
            #
            # `_show_surface` is now the single path: push, mark the row, put the
            # content on top. That is what a sidebar row's own `on_open` calls, so
            # a button and a click finally agree.
            self._show_surface(page_id)
            return True
        return False

    def toggle_sidebar(self) -> None:
        """F9, and anything else that wants to flip the panels."""
        if self._sidebar_toggle is not None:
            self._sidebar_toggle.set_active(not self._sidebar_toggle.get_active())

    def _set_panels_visible(self, visible: bool) -> None:
        """Show the panels as a column beside the chat, or take them away.

        **`Adw.NavigationSplitView` has two properties, and "hidden" is not
        either of them.** `collapsed` chooses the *layout* - a column beside the
        content, or a drawer over it - and `show_content` chooses which pane is
        on top while collapsed. Neither one alone hides the sidebar. Measured on
        libadwaita 1.5, in a 1100x700 window:

        | state | collapsed | show_content | sidebar | chat | toggle mapped |
        |---|---|---|---|---|---|
        | column | False | False | 275px | 825px at x=275 | yes |
        | **hidden** | True | **True** | behind | **1100px at x=0** | **yes** |
        | drawer | True | False | 1100px over everything | behind | **no** |

        The last row is the bug that was reported as "clicking expands the
        sidebar but there is no way to retract it". This used to do
        `set_collapsed(button.get_active())` alone, which is row three: pressing
        a button labelled *show or hide the panels* covered the entire window
        with the panel list, and because the drawer is drawn over the content it
        also covered the button that put it there - the header's own toggle went
        `get_mapped() == False`. F9 was then the only way back, and the sidebar's
        "Conversation" row did not close it either.

        So the pair is the contract: `show_content` decides what a person can
        see, `collapsed` decides whether it is a column or an overlay, and
        "hidden" is the one combination where the toggle stays on screen.

        **`show_content` is named for the *content*, not for the sidebar** -
        `True` means the chat is on top. A first version passed `visible`
        straight through and got the drawer instead of the hidden sidebar, so the
        first fix reproduced the very symptom it was written for; measured in
        both directions, which is the only reason this is stated rather than
        assumed.

        **The button is written last, and the notify handler stands down while
        this runs.** The two properties notify *between* them, and the handler's
        job is to set the button - which emits `toggled`, which calls straight
        back in here. Measured: without the guard, `set_show_content(True)`
        notified, the handler read the half-updated split (still `collapsed`,
        which means "a column", which means "the panels are showing"), put the
        button back to active, and that re-entered with `visible=True` and wrote
        `show_content=False` - so a hide ended in the drawer again, having been
        asked for the hidden sidebar. Two property writes are not a transaction
        and GTK does not batch them; this guard is what makes them one.
        """
        self._writing_panels = True
        try:
            self._split.set_show_content(not visible)
            self._split.set_collapsed(not visible)
        finally:
            self._writing_panels = False
        self._sidebar_toggle.set_active(visible)
        self._sidebar_page.set_drawer_mode(self._panels_as_drawer())

    def _panels_visible(self) -> bool:
        """Whether the panels are on screen, in whichever layout is in force.

        The single read the button's state is derived from, so the two cannot
        disagree. Not `get_collapsed()`: that is a layout decision the
        breakpoint makes on a narrow window, where the panels are *showing* as a
        drawer - which is why a button reading "collapsed" was showing "off" while
        the panel list was on screen.
        """
        if not self._split.get_collapsed():
            return True
        return not self._split.get_show_content()

    def _hide_panels(self) -> None:
        """The drawer's own close button: put the panel list away, change nothing else.

        Separate from `_show_chat` because the drawer can be sitting over a panel
        that is already open, and closing a drawer must not also pop the content
        stack out from under it.
        """
        self._set_panels_visible(False)
        self._sync_sidebar_toggle()

    def _panels_as_drawer(self) -> bool:
        """Whether the panels are an overlay covering the content.

        The one state where the window's own toggle is not on screen - measured,
        the overlay is allocated the whole window and covers the header - and so
        the one state where the sidebar has to carry a close control of its own.
        """
        return bool(self._split.get_collapsed()
                    and not self._split.get_show_content())

    def _on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        """Show or hide the panels - but only for a press, not for a sync.

        The button is active when the panels are on screen, so this is the only
        place the polarity is decided; every other route calls
        `_set_panels_visible` and lets `_sync_sidebar_toggle` follow.

        The `_writing_panels` guard is load-bearing and was found by a test, not
        by reading. `_sync_sidebar_toggle` writes this button to match the split
        view, and `set_active` emits `toggled`, which lands here - so a
        *notification* from the split view was being answered by *overwriting* the
        split view. Concretely: the breakpoint collapsed the sidebar, this
        handler put the button where it belonged, and the handler that fired
        because of that write immediately set both properties back to "panels
        hidden", undoing the collapse. `tests/test_sidebar_toggle.py` catches it
        as "the drawer state was never reached".
        """
        if getattr(self, "_writing_panels", False):
            return
        self._set_panels_visible(button.get_active())

    def _on_split_collapsed(self, _split: object, _param: object) -> None:
        """Keep the toggle honest when something *else* moves the sidebar.

        The split view changes both properties without touching the button: the
        `max-width: 720px` breakpoint on a narrow window, a drag of the sidebar's
        edge, and `set_collapsed` from any other code. Without this the button
        reads "panels hidden" while the panel list is on screen, which is the
        control-whose-state-is-a-lie failure.

        `_sync_sidebar_toggle` only sets the button when it disagrees, so this
        cannot loop: `set_active` emits `toggled`, which writes both properties
        the values they already hold, which emits nothing further.

        It does nothing at all while `_set_panels_visible` is mid-write; see
        there for the measurement that made that guard necessary.
        """
        if getattr(self, "_writing_panels", False):
            return
        self._sync_sidebar_toggle()

    def _sync_sidebar_toggle(self) -> None:
        """Follow the split view, not the other way round.

        Without this, collapsing the sidebar by dragging its edge, or by the
        breakpoint on a narrow window, leaves the button pressed while the panels
        are off screen - or unpressed while they are on it.
        """
        visible = self._panels_visible()
        # The same guard as `_set_panels_visible`, for the same reason and in the
        # other direction: writing the button emits `toggled`, and without this
        # the "follow the split view" path would answer its own notification by
        # driving the split view back to whatever the button had just been set
        # from. Written inside the guard so the button and the drawer's close bar
        # always agree with each other.
        writing = getattr(self, "_writing_panels", False)
        self._writing_panels = True
        try:
            if self._sidebar_toggle.get_active() != visible:
                self._sidebar_toggle.set_active(visible)
            # The drawer gets its own close control, because in that state this
            # button is under it. Hidden everywhere else, which is the whole
            # point: in the column layout the button above is reachable and a
            # second close arrow would be the duplicate this window already had
            # once.
            self._sidebar_page.set_drawer_mode(self._panels_as_drawer())
        finally:
            self._writing_panels = writing

    def open_body_page(self) -> None:
        """The full body register, for when the strip is not enough."""
        if "body" not in self._surface_pages:
            self._surface_pages["body"] = OrganPanel()
        page = self._surface_pages["body"]
        try:
            page._back = self._pop_panel
        except AttributeError:
            pass
        self._content_view.push(page)
        self._sidebar_page.select(None)

    def _show_chat(self) -> None:
        """Back to the conversation, from any panel or from the sidebar row.

        Uses `pop_to_tag(CHAT_TAG)`, which is the only way libadwaita 1.5 offers
        to go back to a known page - there is no `set_visible_page`, and calling
        it raised `AttributeError`, so clicking "Conversation" did nothing and
        every panel's back arrow died with it.

        Popping rather than setting the page also unwinds the stack, so going
        back to the chat and opening a panel again builds it fresh instead of
        pushing a second copy on top of the first.
        """
        view = self._content_view
        if view.get_visible_page() is not view.find_page(CHAT_TAG):
            view.pop_to_tag(CHAT_TAG)
        self._sidebar_page.select(None)
        # Coming back to the conversation from inside the panel list also closes
        # the list, and it has to. While the panels are showing as a drawer they
        # are drawn over everything - including the header's own toggle, which
        # goes `get_mapped() == False` - so a drawer with no way out of itself is
        # a dead end. "Conversation" is the one row in it that means "not the
        # panels", so this row is the drawer close button. `_pop_panel` below
        # deliberately does not do this: the back arrow inside a panel means "back
        # to the chat", not "and now hide the list I just used".
        self._set_panels_visible(False)
        self._sync_sidebar_toggle()

    def _pop_panel(self) -> None:
        """The back arrow inside a panel's own header bar.

        Same route as `_show_chat`, and for the same reason: these pages are
        built by a dozen modules that none of them tag, so the only page worth
        popping *to* is the conversation - which now has a tag for exactly this.
        """
        view = self._content_view
        if view.get_visible_page() is not view.find_page(CHAT_TAG):
            view.pop_to_tag(CHAT_TAG)
        self._sidebar_page.select(None)

    def _seed_status_dots(self) -> None:
        """Ask every panel how it is, one per idle turn, and draw the dots.

        **A dot that only appears once you have opened the panel is not a
        dashboard.** Measured on the rendered window: with all twenty surfaces
        exposing `status()`, a freshly opened window still showed twenty rows
        with no dot at all, because `_show_surface` was the only caller of
        `_panel_status` - so the sidebar could only ever tell you about a panel
        you had already been inside. That defeats the entire point of a health
        column: the question is "is anything wrong *now*", asked *before* opening
        anything.

        One panel per idle turn, not all twenty at once. Building them is real
        work - they shell out to `systemctl`, `busctl` and `gsettings`, and read
        the filesystem - and measured, twenty panels cost around two seconds.
        Doing that synchronously in `_setup_ui` would put two seconds between
        the window appearing and the window being usable. Idle turns cost
        nothing a person can perceive and the dots fill in over the first second.

        Failures are the existing contract: a panel that will not build gets no
        dot, which is the same answer `_panel_status` gives for a panel that
        cannot say. A panel that fails here is not pushed onto the content view,
        so opening it later still builds it properly and reports the failure on
        screen - this pass only asks for the summary.
        """
        from shani_chronoa.gui import surfaces

        names = [n for n in surfaces.SURFACE_IDS
                 if n in self._sidebar_page._surface_rows]
        if not names:
            return

        available = surfaces.available_surfaces()

        import sys as _sys

        def prebuilt(build) -> bool:
            # A panel whose build is slow by nature (Diagnostics runs real
            # probes - a screen capture among them) sets PREBUILD = False and is
            # built when opened, not here.
            module = _sys.modules.get(getattr(build[2], "__module__", ""), None)
            return getattr(module, "PREBUILD", True)

        def one() -> bool:
            # ONE panel per main-loop turn. This used to loop over all of them
            # inside a single callback, so every panel was built back to back
            # with the main loop held - measured 2026-10-08: ~8 s just after
            # startup, during which the mic button did nothing and a spoken
            # request was lost before recording began.
            while names:
                name = names.pop(0)
                page = self._surface_pages.get(name)
                if page is None:
                    build = available.get(name)
                    if build is None or not prebuilt(build):
                        continue
                    try:
                        page = build[2](getattr(self, "_app", None))
                    except Exception:                  # noqa: BLE001
                        logger.info("panel %r did not build; its row keeps no "
                                    "dot until it is opened", name,
                                    exc_info=True)
                        continue
                    # Kept, so opening it later is instant and the dot cannot
                    # change underneath the person who is looking at it.
                    self._surface_pages[name] = page
                self._sidebar_page.set_status(name, _panel_status(page))
                return GLib.SOURCE_CONTINUE if names else GLib.SOURCE_REMOVE
            return GLib.SOURCE_REMOVE

        GLib.idle_add(one, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def reload_surface(self, name: str) -> None:
        """Forget panel `name` and, if it is on screen, show it built afresh.

        Panels are built once and kept (see `_show_surface`), so a panel whose
        switch was just turned on from its own banner would keep saying "off"
        until the app restarted.
        """
        page = self._surface_pages.pop(name, None)
        if page is None:
            return
        if self._content_view.get_visible_page() is page:
            self._content_view.pop()
            self._show_surface(name)

    def _show_surface(self, name: str) -> None:
        """Push a panel onto the content view.

        Built on first use and kept afterwards, so returning to a panel shows the
        readings it had rather than re-running every sense - a panel that
        re-measured the machine each time it was opened would be a panel nobody
        opened twice. One build failure becomes the page's own error state, not a
        window that will not open.
        """
        from shani_chronoa.gui import surfaces
        available = surfaces.available_surfaces()
        if name not in available:
            self.set_status(f"That panel is not available: {name}")
            return
        page = self._surface_pages.get(name)
        if page is None:
            try:
                page = available[name][2](getattr(self, "_app", None))
            except Exception as exc:
                logger.error("Panel %r could not be built", name, exc_info=True)
                page = _broken_page(name, exc)
            # **A surface may build a plain widget rather than a page.**
            # `Adw.NavigationView.push` takes `Adw.NavigationPage` and raises
            # `TypeError: Expected Adw.NavigationPage, but got
            # gi.repository.Gtk.Box` on anything else - measured, and it is why
            # the Listening button could not be pointed at `_show_surface`: a
            # comment claimed two `select` calls would collide and a bare box
            # would be pushed, and the second half of that turned out to be the
            # real one while the first was invented.
            #
            # Wrapped here rather than in each surface, because a surface that
            # builds a `Gtk.Box` is not wrong on its own - only the split view
            # has an opinion, and only here is that opinion held. Anything that
            # already *is* a page is pushed as it is, so a surface that sets its
            # own title or tag keeps it.
            if not isinstance(page, Adw.NavigationPage):
                wrapper = Adw.NavigationPage(title=_surface_title(name))
                wrapper.set_child(page)
                page = wrapper
            self._surface_pages[name] = page
        # Panels are pushed, so they need a way back that does not depend on the
        # sidebar being open - on a narrow window it is a collapsed drawer.
        try:
            page._back = self._pop_panel
        except AttributeError:
            pass
        self._content_view.push(page)
        self._sidebar_page.select(name)
        # Ask the panel how it is, and put that on its sidebar row. The panel
        # answers with `page.status()` if it has one; most do not, and a panel
        # that cannot say is not a panel that is broken - it is a panel with no
        # opinion, so its row keeps no dot rather than being given a green one
        # it did not earn.
        self._sidebar_page.set_status(name, _panel_status(page))
        # A panel was chosen from the sidebar, so the sidebar has done its job.
        # `show_content` is what puts the chat back on top - setting `collapsed`
        # alone would only trade a drawer for a column and, on a wide window,
        # leave the panel's own list covering the panel.
        self._set_panels_visible(False)
        self._sync_sidebar_toggle()

    def set_browser_available(self, available: bool) -> None:
        """Hide the browser button when WebKitGTK is not installed.

        A control that opens nothing and explains why is worse than no control
        for someone who has not read the packaging notes.
        """
        self._browser_available = bool(available)
        self._browser_button.set_visible(available and not self._narrow_applied)
        if getattr(self, "_overflow_browser", None) is not None:
            self._overflow_browser.set_visible(bool(available))

    def focus_input(self) -> None:
        """Put the caret in the message field.

        The window can be raised by a global shortcut while something else has
        focus, and a window you have to click before you can type is one extra
        gesture on every voice-adjacent turn.
        """
        self._input_entry.grab_focus()

    # -- finding something in this conversation --------------------------

    def toggle_search(self) -> None:
        """Ctrl+F, and the search entry's own escape."""
        self._search_bar.set_search_mode_enabled(not self._search_bar.get_search_mode())
        if not self._search_bar.get_search_mode():
            self._transcript.highlight("")
            self._search_entry.set_text("")

    def _run_search(self, text: str) -> None:
        found = self._transcript.highlight(text)
        self._search_entry.set_text(text)
        self._search_entry.set_placeholder_text(
            f"Find in this conversation - {found} match" + ("es" if found != 1 else "")
            if text else "Find in this conversation")

    def _search_next(self) -> None:
        """Walk the hits: the first, then the next one."""
        hits = self._transcript.find(self._search_entry.get_text())
        if not hits:
            return
        self._search_cursor = getattr(self, "_search_cursor", -1) + 1
        if self._search_cursor >= len(hits):
            self._search_cursor = 0
        self._transcript.scroll_to_turn(hits[self._search_cursor])

    def add_tool_call(self, name: str, arguments: dict, result: str = "",
                      ok: bool = True, artifacts: tuple = (),
                      selection=None) -> None:
        """Show a skill's call, its arguments and what it returned.

        `artifacts` is what that dispatch **declared** it produced - never a
        path taken out of `result`. The transcript renders only from that field,
        so a reply containing a path cannot put a file on screen.

        `selection` is what the turn offered and why, from the round that made
        this call. It is passed rather than looked up because the transcript
        builds cards for every turn at once and "the current selection" would
        answer about the wrong one.
        """
        self._transcript.add_tool_call(name, arguments, result, ok, artifacts,
                                      selection=selection)
        # **Collected here, where they are already resolved.** These have been
        # through `tools.artifacts_for`, so they are absolute and present - the
        # rail reads the same list the transcript drew its thumbnails from,
        # rather than a second source that could disagree with the first.
        self._note_artifacts(artifacts)

    #: The rail shows the most recent `RAIL_ARTIFACT_LIMIT`; the rest stay in
    #: the transcript. Capped so one long turn that made thirty files does not
    #: turn the rail into a file browser.
    RAIL_ARTIFACT_LIMIT = 6

    def _note_artifacts(self, artifacts) -> None:
        """Remember what this session made, for the rail's "Made" section."""
        if not artifacts:
            return
        for path in artifacts:
            if path not in self._artifacts:
                self._artifacts.append(path)
        self._artifacts = self._artifacts[-self.RAIL_ARTIFACT_LIMIT:]
        if getattr(self, "_rail", None) is not None:
            self._rail.refresh()

    #: Set from the application once it exists; before that the rail has no
    #: scheduler to read and says so rather than showing an empty section that
    #: looks like "nothing has fired".
    _event_source = None

    def set_event_source(self, source) -> None:
        """Attach the ambient scheduler whose fired rules the rail reports."""
        self._event_source = source

    def _rail_senses(self):
        """What the ambient scheduler will actually poll, right now.

        **`plan()`, not `ambient_senses()`** - the first is the report of every
        known sense *and whether it can be polled*, which is what a person
        asking "is this thing watching me?" needs; the second is only the senses
        that already have, and says nothing about the ones that do not.

        Called on every rail tick, so it must be cheap and must not raise: the
        scheduler holds its own lock and `plan()` is a pure read.
        """
        source = self._event_source
        if source is None:
            return ()
        try:
            return source.plan()
        except Exception as exc:  # noqa: BLE001
            logger.debug("rail: sense plan unavailable: %s", exc)
            return ()

    def _rail_events(self):
        """What the ambient rules have fired.

        **Reads `AmbientScheduler.event_results()`, which had no caller anywhere
        in `usr/`** - rules fired unattended and the window showed nothing. The
        scheduler keeps a bounded list under its own lock and says it is safe
        from any thread, which is what lets the two-second rail tick read it
        directly rather than the tick crossing into the scheduler's thread.
        """
        source = self._event_source
        if source is None:
            return ()
        try:
            return source.event_results()
        except Exception as exc:  # noqa: BLE001
            logger.debug("rail: event results unavailable: %s", exc)
            return ()

    def _rail_artifacts(self):
        return list(self._artifacts)

    def add_notice_row(self, sentence: str, kind: str = "compaction") -> None:
        """A housekeeping note in the transcript - see the transcript's own
        method for why these exist rather than being only a log line."""
        self._transcript.add_notice_row(sentence, kind)

    def add_user_turn(self, text: str) -> None:
        """Show what the user said, so a spoken turn is visible too."""
        self._transcript.add_user_turn(text)

    def _revert_turn(self, turn: str):
        """Put back the files `turn` changed; say so; refresh what shows them."""
        from shani_chronoa.skills.undo_last_change import revert_turn
        restored, skipped = revert_turn(turn)
        if restored:
            self.set_status(f"Reverted {len(restored)} file(s) to before that turn. "
                            "Undo puts them back.")
        refresh = getattr(self, "_refresh_undo_button", None)
        if callable(refresh):
            refresh()
        return restored, skipped

    def clear_transcript(self) -> None:
        self._transcript.show_placeholder()
        self._orb.set_compact(False)

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def set_regenerate_handler(self, handler) -> None:
        """Attach (or detach) what the transcript's "Ask that again" does.

        The window is constructed before the application knows whether there is
        an assistant to re-ask, so the handler arrives after construction - the
        same pattern `set_suggestion_handler` uses. Passing None removes the
        control, which is what happens when there is no model to answer with;
        a button that cannot work is worse than no button.
        """
        self._regenerate = handler
        self._transcript.set_regenerate_handler(handler)

    def _on_regenerate_clicked(self) -> None:
        if self._regenerate is not None:
            self._regenerate()

    def _build_palette(self) -> None:
        """Ctrl+K, and the one list of everywhere you can go.

        Built here rather than by the palette itself because the entries are
        this window's business: its pages, the surfaces its sidebar is showing,
        and the slash commands it can run. The palette only knows how to list
        and filter what it is given.

        **Gathered on open, not cached at construction** - panels come and go,
        and a palette offering one that no longer exists is the stale-list
        failure this repository keeps finding.
        """
        from shani_chronoa.gui import palette as palette_module

        self._palette = palette_module.CommandPalette(on_close=self._close_palette)
        self._palette.set_entries(self.palette_entries())
        self._palette.set_visible(False)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_palette_key)
        self.add_controller(keys)

    def _on_palette_key(self, _controller, keyval, _keycode, state) -> bool:
        """Ctrl+K or Cmd+K opens, Escape closes.

        `Gdk.ModifierType.CONTROL_MASK` is the modifier bit on every platform and
        a Mac keyboard delivers Command as it, so one binding covers both.
        """
        if keyval in (Gdk.KEY_k, Gdk.KEY_K) and bool(
                state & Gdk.ModifierType.CONTROL_MASK):
            self._toggle_palette()
            return True
        if keyval == Gdk.KEY_Escape and self._palette.get_visible():
            self._close_palette()
            return True
        return False

    def _toggle_palette(self) -> None:
        if self._palette.get_visible():
            self._close_palette()
            return
        self._palette.set_entries(self.palette_entries())
        self._palette.set_visible(True)
        self._palette.focus_search()

    def _close_palette(self) -> None:
        self._palette.set_visible(False)
        self.focus_input()

    def palette_entries(self):
        """Everywhere the palette can take you, from the window's own sources."""
        from shani_chronoa.gui import commands as slash
        from shani_chronoa.gui import palette as palette_module
        from shani_chronoa.gui import surfaces

        try:
            all_surfaces = dict(surfaces.all_surfaces())
        except Exception as exc:  # noqa: BLE001
            logger.warning("palette: surfaces unreadable: %s", exc)
            all_surfaces = {}

        entries = [
            # **`view-paged-symbolic`, not `chat-symbolic`** - caught by
            # `test_sidebar_icons_exist_in_adwaita`, which is right and which I
            # had not run. `chat-symbolic` is a Yaru name; GTK falls back to the
            # system theme so it renders fine here and is blank on plain GNOME.
            # Verified by `rglob` over `/usr/share/icons/Adwaita`, not by
            # `Gtk.IconTheme.has_icon` - which reported several icons Adwaita
            # does ship as missing.
            palette_module.Entry("Conversation", "the main window",
                                 "view-paged-symbolic",
                                 self._show_conversation_page,
                                 group="Go to", weight=0),
            palette_module.Entry("Quick ask", "one question, no conversation",
                                 "edit-clear-symbolic", self._show_quick_ask_page,
                                 group="Go to", weight=1),
        ]
        for surface_id in surfaces.SURFACE_IDS:
            row = self._sidebar_page._surface_rows.get(surface_id)
            if row is None:
                # **Not in this window's sidebar is not an error** - a panel can
                # be registered and unavailable. Listing it anyway would offer a
                # row that opens nothing.
                continue
            _row, _lowered, icon, section = row
            # **`all_surfaces()`, not the sidebar's stored title.** The sidebar
            # lowercases it on the way in - `sidebar.py` does
            # `(row, title.lower(), icon, section)` because its search compares
            # against it - so a palette reading that field offers "my files" and
            # "meetings" where the panel says "My files" and "Meetings". The
            # surface's own title is the one a person recognises.
            title = all_surfaces.get(surface_id, (_lowered, icon, None))[0]
            entries.append(palette_module.Entry(
                title, section or "Panel", icon,
                (lambda sid=surface_id: self._show_surface(sid)),
                group="Panels", weight=0))
        entries.extend(self._palette_mode_entries())
        for name in slash.names():
            # `system-run-symbolic` rather than `terminal-symbolic`, for the
            # same reason as the conversation icon above.
            entries.append(palette_module.Entry(
                f"/{name}", "command", "system-run-symbolic",
                (lambda n=name: self._run_command(f"/{n}")),
                group="Commands", weight=0))
        entries.extend(self._palette_cloud_entries())
        entries.extend(self._palette_action_entries())
        entries.extend(self._palette_settings_entries())
        return entries

    #: (title, subtitle, icon, app action) - things that were a shortcut or a
    #: chat request and nothing else. `app.<action>` so the palette fires the
    #: same action the shortcut does.
    _PALETTE_ACTIONS = (
        ("Connect a device", "Bluetooth, Android, KDE Connect", "preferences-system-devices-symbolic", "connect-device::"),
        ("Pair a Bluetooth device", "headphones, watch, keyboard, phone", "bluetooth-symbolic", "connect-device::bluetooth"),
        ("Connect an Android phone", "USB cable or Wi-Fi debugging", "phone-symbolic", "connect-device::android"),
        ("Pair a KDE Connect phone", "same Wi-Fi", "network-wireless-symbolic", "connect-device::kdeconnect"),
        ("Dictate", "speak a long message", "audio-input-microphone-symbolic", "dictate"),
        ("Transcribe a recording", "an audio or video file, speakers told apart", "audio-x-generic-symbolic", "transcribe-file"),
        ("Find in this conversation", "Ctrl+F", "edit-find-symbolic", "find-in-conversation"),
        ("Choose project folder", "Build mode works in it", "folder-open-symbolic", "choose-project"),
        ("Reload MCP servers", "ask each server for its tools again", "view-refresh-symbolic", "reload-mcp"),
        ("Set Chronoa up", "model, listening, voice", "system-software-install-symbolic", "setup"),
        ("Settings", "all settings", "emblem-system-symbolic", "open-settings"),
        ("Body", "what each part of Chronoa is doing", "dialog-information-symbolic", "show-body"),
        ("Stop speaking", "Esc", "media-playback-stop-symbolic", "stop-speaking"),
    )

    def _palette_action_entries(self) -> list:
        from shani_chronoa.gui import palette as palette_module
        app = getattr(self, "_app", None)
        out = []
        for title, subtitle, icon, action in self._PALETTE_ACTIONS:
            # `name::target` carries a string parameter, Gio's detailed form.
            name, _sep, target = action.partition("::")
            if app is None or app.lookup_action(name) is None:
                continue        # not registered here: no entry that does nothing
            value = GLib.Variant("s", target) if _sep else None
            out.append(palette_module.Entry(
                title, subtitle, icon,
                (lambda a=name, v=value: app.activate_action(a, v)),
                group="Actions", weight=0))
        return out

    def _palette_settings_entries(self) -> list:
        """Every Settings page, by its own title, through the page registry."""
        from shani_chronoa import pages
        from shani_chronoa.gui import palette as palette_module
        from shani_chronoa.gui.surfaces import common
        out = []
        try:
            pairs = list(zip(pages.page_ids("settings"), pages.page_titles("settings")))
        except Exception:  # noqa: BLE001
            logger.debug("palette: settings pages unreadable", exc_info=True)
            return out
        for page_id, title in pairs:
            out.append(palette_module.Entry(
                f"Settings: {title}", "settings page", "emblem-system-symbolic",
                (lambda p=page_id: common.open_page(getattr(self, "_app", None),
                                                    f"settings:{p}")),
                group="Settings", weight=0))
        return out

    def _palette_cloud_entries(self):
        """Cloud models, for the people who search rather than browse.

        **Built from the same cache the picker reads**, so the palette and the
        picker cannot disagree about which models exist - which is the
        second-source-of-truth failure `ModelPicker`'s own docstring is written
        against, and the reason those two widgets share one cache rather than
        each fetching.

        **A refusal is an entry, not a silence.** `cloud_llm.cached_models`
        returns `(models, why)`, and when `why` is set the row carries it. A
        provider that refused and a provider nobody asked yet read identically
        if both were dropped, and "no models here" is a claim about a provider
        rather than about this screen.

        **A model with no row cannot be chosen**, so a provider that has never
        been fetched gets one entry offering to read its list rather than
        nothing: the palette is a discovery surface, and "nothing here" for a
        provider the person has a key for is the wrong answer.
        """
        from shani_chronoa import cloud_llm
        from shani_chronoa.gui import palette as palette_module

        out = []
        order = (list(cloud_llm.BYOK_PROVIDER_ORDER)
                 + list(cloud_llm.DEFAULT_PROVIDER_ORDER)
                 + [cloud_llm.CUSTOM_ID])
        for provider_id in order:
            provider = cloud_llm.PROVIDERS.get(provider_id)
            if provider is None:
                continue
            label = provider.name if provider_id != cloud_llm.CUSTOM_ID \
                else "Your own server"
            if not cloud_llm.models_asked(provider_id):
                out.append(palette_module.Entry(
                    f"{label} models", "read its model list",
                    "network-transmit-receive-symbolic",
                    (lambda p=provider_id: self._read_cloud_models(p)),
                    group="Cloud models", weight=0))
                continue
            models, why = cloud_llm.cached_models(provider_id)
            if not models:
                # **A refusal is pressable, and pressing it asks again.**
                # `collect()` drops an entry with no `run`, so a refusal given
                # no callback would vanish from the palette entirely - and a
                # provider that answered "HTTP 401" would read the same as one
                # that was never configured. The reason stays on screen, and
                # the press is the obvious thing to do about it.
                out.append(palette_module.Entry(
                    f"{label}", why or "gave no model list",
                    "network-transmit-receive-symbolic",
                    (lambda p=provider_id: self._read_cloud_models(p)),
                    group="Cloud models", weight=0))
                continue
            # **`collect()` drops an entry with no `run`,** so every row here
            # carries one - a model, a provider to read, and a refusal to ask
            # again - rather than silently vanishing from a filtered list.
            for model in models:
                out.append(palette_module.Entry(
                    model, label, "network-transmit-receive-symbolic",
                    (lambda p=provider_id, m=model: self._answer_cloud_model(p, m)),
                    group="Cloud models", weight=0))
        return out

    def _read_cloud_models(self, provider_id: str) -> None:
        """Fetch one provider's list off the main loop, then refresh the palette."""
        from shani_chronoa import cloud_llm

        try:
            keys = dict(self._app.config.cloud_llm_api_keys() or {})
        except Exception as exc:  # noqa: BLE001
            logger.debug("palette: cloud keys unreadable: %s", exc)
            keys = {}
        try:
            base = self._app.config.get("custom-llm-base-url", "")
        except Exception:  # noqa: BLE001
            base = ""

        def work():
            try:
                cloud_llm.fetch_models(provider_id,
                                       api_key=keys.get(provider_id, ""),
                                       base_url=base)
            except Exception:  # noqa: BLE001
                logger.debug("palette: %s would not answer", provider_id,
                             exc_info=True)
            # The picker shares this cache, so it refreshes too rather than
            # waiting for its own reopen to notice.
            picker = getattr(self, "_model_picker", None)
            GLib.idle_add(lambda: (picker is None or picker.rebuild(), False)[1])
            if self._palette.get_visible():
                self._palette.set_entries(self.palette_entries())

        import threading
        threading.Thread(target=work, name="chronoa-palette-models",
                         daemon=True).start()

    def _answer_cloud_model(self, provider_id: str, model: str) -> None:
        """Hand a palette model choice to the app's own action, then close."""
        if not self._activate_cloud_model(provider_id, model):
            return
        self._close_palette()

    def _activate_cloud_model(self, provider_id: str, model: str) -> bool:
        """Whether the choice reached the app's action. One door, three callers."""
        action = getattr(self, "activate_action", None)
        if action is None:
            logger.debug("palette: nothing to hand the cloud choice to")
            return False
        try:
            action("app.answer-cloud-model",
                   GLib.Variant("(ss)", (provider_id, model)))
        except Exception:  # noqa: BLE001
            logger.warning("palette: could not switch to %s on %s", model,
                           provider_id, exc_info=True)
            return False
        return True

    #: Every mode, by name, **built from the strip's own tables**.
    #:
    #: `((table, action, reader), ...)` - the tables are the same tuples the
    #: chips draw from and the same menus offer, so a mode added to a chip is in
    #: the palette for free and a mode removed from a chip disappears from here
    #: too. **A second list of mode names would drift**, and the drift would
    #: show as a palette entry that sets a mode nothing reads.
    #: The model chip, in the palette rather than on the strip.
    #:
    #: **Added when the other four moved off, and for the same reason.** Its
    #: rows are the installed tiers read fresh rather than a static table, which
    #: is why it is not in `_MODE_TABLES` - so moving the static chips to the
    #: palette would have left the model reachable only by walking to Settings.
    _MODEL_PALETTE = True

    #: **`_ModeStrip.`-qualified, because the tables live on the strip.** They
    #: are defined in the other class; naming them bare here would have been a
    #: `NameError` at import, which is the cheapest possible way to find out.
    _MODE_TABLES = (
        (_ModeStrip._CAPABILITY, "answer-rules", "_read_capability"),
        # Plan is a workflow, not a permission, so it is listed from its own
        # table even though its action carries no value - the entry still
        # opens the chip, which is the part that is useful.
        (_ModeStrip._PLAN, "toggle-plan-mode", "_read_plan"),
        (_ModeStrip._EGRESS, "answer-egress", "_read_egress"),
        (_ModeStrip._AMBIENT, "answer-ambient", "_read_ambient"),
        (_ModeStrip._PRESENCE, "answer-presence", "_read_presence"),
        # **`hands-free`, not `answer-hands`.** Every other action here is
        # named `answer-*` and this one is not, so the pattern is a reliable
        # way to invent a name that does not exist - which is exactly what the
        # first version of this table did. `test_every_mode_entry_names_a_real_
        # action` now reads the real action names out of the file.
        (_ModeStrip._HANDS, "hands-free", "_read_hands_free"),
        (_ModeStrip._STYLES, "reply-style", "_read_reply_style"),
    )

    def _palette_mode_entries(self) -> "list":
        """One palette entry per mode, saying what it is *now*.

        **This is the answer to "what mode am I in?"** Which used to mean
        reading eight chips word by word. The entry's subtitle carries the
        current state, so one Ctrl+K and one line answers it - and it is a line
        that can be copied into a bug report, which a strip cannot.

        Titles are the words the strip shows, so what you see on the strip and
        what you type in the palette are the same word for the same thing.
        """
        from shani_chronoa.gui import palette as palette_module

        out = []
        # **The model chip, added by hand** because its rows are the installed
        # tiers read fresh rather than one of the static tables - so when the
        # other four moved off the strip to here, the model would have been the
        # only one left reachable only from Settings.
        try:
            rows = self._mode_strip._model_rows()
            if rows:
                out.append(palette_module.Entry(
                    "Which model answers", "a locally installed model",
                    rows[0][3] if len(rows[0]) > 3 and rows[0][3]
                    else "preferences-system-symbolic",
                    (lambda: self._open_chip_dropdown(
                        self._mode_strip._model_chip)),
                    group="Modes", weight=0))
        except Exception:  # noqa: BLE001 - a missing tier list is not a reason
            logger.debug("palette: the model rows are unreadable", exc_info=True)
        for table, action_name, reader_name in self._MODE_TABLES:
            if not table:
                continue
            # **`self._mode_strip`, not `self`.** The readers are methods of
            # `_ModeStrip`, and the window does not have them - so `getattr`
            # found nothing, every entry reported the *first* row's state, and
            # the palette would have answered "what mode am I in?" with the
            # same answer eight times.
            strip = getattr(self, "_mode_strip", None)
            reader = getattr(strip, reader_name, None)
            try:
                current = reader() if callable(reader) else ""
            except Exception:                           # noqa: BLE001
                current = ""
            # **The same row `_draw` picks, for the title as well as the line.**
            # It used `table[0][1]`, which is the table's *first* row's word -
            # a constant, identical whatever state the app is in. Across the 20
            # rows these seven tables hold, the palette named the right one
            # **7 times** and the wrong one 13: Chronoa `Asleep` was listed as
            # "Here", `Plan` as "Working", `Build` as "General", "Brief" as
            # "Ordinary", and every cloud egress as "Nothing leaves".
            #
            # The subtitle was already the current row's line, so each row was
            # half right - a plausible description of the wrong state, which is
            # harder to notice than being plainly wrong. And it disagreed with
            # the chip beside it, which does read the state.
            #
            # **On a default machine none of this shows**: every mode is then
            # its table's first row, which is what the 7 are. The defect is
            # invisible until somebody changes something.
            row = next((r for r in table if r[0] == current), table[0])
            icon = row[3] if len(row) > 3 and row[3] else "preferences-system-symbolic"
            out.append(palette_module.Entry(
                row[1], row[2], icon,
                (lambda a=action_name: self._activate_mode(a)),
                group="Modes", weight=0))
        return out

    def _inside_window(self, widget) -> bool:
        """Whether `widget` can reach this window by walking parents.

        **Ancestry, not visibility, and the distinction is the whole point.** A
        widget inside a hidden box *is* a descendant of the window and is
        mapped never, so a check that answered "is it shown" would call a
        palette nested in `_question_row` reachable. `Gtk.Popover` has the same
        contract from the other side: its parent must be inside a toplevel
        before it can make a surface to draw into, and GTK says so itself -
        *"Widgets must be inside a toplevel container before realizing them"*.
        """
        node = widget
        while node is not None:
            if node is self:
                return True
            node = node.get_parent()
        return False

    def _open_chip_dropdown(self, chip) -> bool:
        """Open a mode chip's own dropdown, wherever the chip currently lives.

        **Five of the chips are not in the window at all.** `5ef5c62` moved the
        model switch off the strip to make room, and its parent commit moved
        plan, ambient, hands-free and reply style with it; nothing re-parented
        any of them, so `popup()` on one of those asks GTK for a popup surface
        it cannot have. Measured on a presented window: all five report
        `ancestors to window: None`.

        So the **popover moves and the chip stays**. Re-parenting the chip
        would put a control back on a strip that measures 97% full - which is
        the reason it was moved - and would rebuild the "never appended but
        still one keystroke from usable" assumption that turned out to be
        untrue. The popover carries the menu, the radio marks and the refresh;
        only its anchor changes.

        **The anchor is the palette**, which *is* in the window, so the dropdown
        appears over the list the person just pressed Enter in. And the move is
        undone the moment the chip is back in the tree, so a chip that is ever
        re-appended to the strip gets its own anchor again rather than a
        popover stranded over the palette.

        **The move is `unparent()` then `set_parent()`, and without the first
        the process dies.** Measured, three runs, identical every time, with
        the `unparent()` line deleted and nothing else changed:

            Fatal Python error: Segmentation fault
              window.py, line 3861 in _open_chip_dropdown
              libgtk-4.so.1, gtk_widget_show
              libgtk-4.so.1, gtk_widget_realize
              libgtk-4.so.1, +0x2248e5          <- the fault

        Line 3861 is `popover.popup()`, not the `set_parent` above it. So the
        shape of this is worth stating exactly, because the guess is wrong in
        both halves:

        - `set_parent()` on a widget that already has a parent **returns
          normally**. Measured: **zero** `Gtk-CRITICAL`, **zero** warnings,
          nothing on stderr at all. It does not raise and it does not complain.
        - it also does not move the widget. The popover keeps the parent it
          had, which is the thing outside every toplevel.
        - so the *next* call is the one that fails. `popup()` shows a widget
          that has no place in the tree, GTK tries to realize it, and
          realize dereferences NULL.

        Which means the missing line did not produce a stranded popover. It
        produced a **segfault on every palette route into a mode dropdown** -
        and it produced it silently, with nothing on stderr to explain a
        window that disappeared. `unparent()` first is what makes
        `set_parent` mean what it says.
        """
        popover = chip.get_popover() if hasattr(chip, "get_popover") else None
        if popover is None:
            return False
        anchor = chip if self._inside_window(chip) else getattr(self, "_palette", None)
        if anchor is None:
            return False
        if popover.get_parent() is not anchor:
            popover.unparent()
            popover.set_parent(anchor)
        popover.popup()
        return True

    def _activate_mode(self, action_name: str) -> None:
        """Leave a mode's own dropdown open on its current row.

        **One keystroke should not change a mode.** Changing it needs a chosen
        value, and the dropdown is where the values are; opening it is what
        makes the palette an index of modes rather than a wall of switches
        where one stray Enter commits a privacy setting.

        **The chips live on `_mode_strip`, not on the window.** Every one of the
        seven names below is assigned inside `_ModeStrip.__init__`, and this
        looked them up with `getattr(self, ...)` - on the window, which never
        holds any of them. So the lookup answered `None` for all seven and
        `if chip is not None` never fired: **every palette mode entry did
        nothing**, including the three whose chips *are* on the strip and could
        have been opened. A name that resolves to `None` reads exactly like a
        control that is not there, which is the failure this whole palette was
        built to avoid.
        """
        strip = getattr(self, "_mode_strip", None)
        # **Each setting opens the control it now lives in** - the grouped chip
        # or the composer button - because the single-setting chips are built
        # but not on screen, and a popover can only open from a widget that is.
        model = getattr(self, "_model_picker_button", None)
        chip = {
            "answer-rules": getattr(strip, "_work_chip", None),
            "toggle-plan-mode": getattr(strip, "_work_chip", None),
            "answer-egress": model,
            "answer-ambient": getattr(strip, "_permissions_chip", None),
            "answer-presence": model,
            "hands-free": getattr(strip, "_hands_chip", None),
            "reply-style": getattr(self, "_thinking_picker_button", None),
        }.get(action_name)
        if chip is not None and hasattr(chip, "popup"):
            self._open_chip_dropdown(chip)

    #: Set by a caller that wants to decide rewinds itself; None means use the
    #: real dialogue. It is a seam for tests, and the seam takes the **action**
    #: rather than a verdict - see `_on_rewind_requested`.
    _rewind_confirm = None

    def _confirm_rewind(self, preview: dict, text: str, on_yes):
        """Ask about a destructive rewind. True/False if decided now, else None.

        **None means "not decided yet", and is never read as consent.** A guard
        that treats "could not ask" as "yes" is the same defect as the one this
        file keeps fixing, so a dialogue that fails to appear refuses.
        """
        confirm = self._rewind_confirm or self._ask_rewind_dialog
        return confirm(preview, text, on_yes)

    def _rewind_responded(self, response: str, on_yes) -> None:
        """The dialog's answer, as a method so it can be driven directly.

        **Extracted because "the dialog calls the action" is the load-bearing
        claim and it was untested.** An earlier version compared
        `response == "rewind"` inside a closure; changing that string made the
        rewind impossible in the shipped app and 26 tests stayed green, because
        the only test on it checked the *signature* of the dialog method.

        The cancellation branch matters as much: "Keep everything" must not
        rewind, and neither must the dialog closing without a choice.
        """
        if response == "rewind":
            on_yes()

    def _ask_rewind_dialog(self, preview: dict, text: str, on_yes):
        """The real confirmation. Non-modal, defaulted to cancel.

        **`on_yes()` is called from the response handler**, because GTK 4 has no
        blocking dialog API - an earlier version returned `False` immediately
        after `present()` and the rewind could therefore *never* happen in the
        real app while every test still passed. That is the inert-control defect
        in its purest form, and it is why the action is a parameter.
        """
        try:
            from gi.repository import Adw

            dialog = Adw.AlertDialog.new(
                "Rewind from this turn?",
                f"This removes {preview['sentence']}. It cannot be undone.")
            dialog.set_default_response(1)
            dialog.add_response("cancel", "Keep everything")
            dialog.add_response("rewind", "Rewind")

            dialog.connect("response",
                           lambda d, name: self._rewind_responded(name, on_yes))
            dialog.present(self)
            self._rewind_dialog = dialog
            return None
        except Exception:                                   # noqa: BLE001
            logger.warning("could not ask for rewind confirmation; refusing",
                           exc_info=True)
            return False

    def _show_conversation_page(self) -> None:
        self._close_palette()
        self._show_surface("conversation")

    def _show_quick_ask_page(self) -> None:
        self._close_palette()
        self._show_surface("quick-ask")

    def set_rewind_handler(self, handler) -> None:
        """Attach (or detach) what secondary-clicking a turn does.

        Same late-binding as `set_regenerate_handler`: the window exists before
        the application knows whether there is an assistant to rewind to.
        """
        # **Presence only.** The transcript calls its handler as
        # `(index, text)` while the `rewind-history` signal handler has to be
        # `(window, text)` - two different shapes for the same action, and
        # passing `handler` straight through was how a call with three
        # arguments reached a method taking one. So the window keeps its own
        # method, and `handler` is only the answer to "is there an assistant to
        # rewind to", which is all it was ever for.
        self._transcript.set_rewind_handler(
            self._on_rewind_requested if handler is not None else None)

    def _on_rewind_requested(self, index: int, text: str) -> None:
        """Take turn `index` out of the conversation and put its text back.

        The window truncates only what it owns - the transcript - and *asks* to
        have the history rewound, because the assistant is not its to reach.

        **Order matters, and both orders leave a window where the two disagree.**
        History first, transcript second: a model remembering nine turns while
        four are on screen is the direction this codebase keeps paying for -
        a confident answer built on something the user cannot see. So the turn is
        checked locally first, the history is rewound, and only then is the
        transcript cut; the local check is what makes the cut reliable rather
        than a second thing that can fail after the irreversible one.

        A refused rewind changes nothing. After a compression that dropped the
        turn, "I cannot go back that far" is the only honest answer, and cutting
        the screen anyway would leave the user editing a conversation the model
        no longer has.

        The text goes into the composer rather than being resubmitted, so the
        edit is the user's to make. Rerunning automatically would replace their
        question with the same question.
        """
        if self._transcript.turn_at(index) is None:
            return
        preview = self._transcript.rewind_preview(index)
        if not preview["ok"]:
            return

        # **Ask before discarding, not after.** Rewinding is destructive and was
        # originally instantaneous and silent: it took turns, tool calls and
        # files off the screen with nothing said about any of it. In a codebase
        # whose other half is btrfs blue-green deploys, a control that discards
        # work without saying what it discards is out of character.
        #
        # **Before the history rewind, not after** - the history is the
        # irreversible half, and confirming after it has happened is a notice
        # rather than a question.
        #
        # `confirm` is handed the *action*, not a verdict: an `Adw.AlertDialog`
        # is asynchronous, so the answer arrives after this method has returned.
        # A design that waited for a bool here would have to block the loop, and
        # a design that treated "no answer yet" as "no" would make the rewind
        # permanently inert while looking entirely correct.
        # The `return` under `decided is False` is **load-bearing by accident if
        # it is missing**: nothing currently follows it, so a refusal is honoured
        # only because this is the last statement. Stated explicitly so the next
        # thing added here cannot silently turn "Keep everything" into a rewind.
        decided = self._confirm_rewind(preview, text,
                                       lambda: self._perform_rewind(index, text))
        if decided is False:
            self.set_status("Nothing was removed.")
            return

    def _perform_rewind(self, index: int, text: str) -> None:
        """History first, transcript second, then the question back in the box."""
        if not self.emit("rewind-history", text):
            self.set_status("That turn is too old to rewind to - earlier parts "
                            "of this conversation have been compressed.")
            return
        removed = self._transcript.truncate_from_turn(index)
        if removed is None:                # pragma: no cover - checked above
            logger.warning("turn %d vanished between the check and the cut", index)
            return
        self._set_input_text(text)
        self._input_entry.grab_focus()
        self.set_status("Rewound - everything after that turn is removed. "
                        "Edit it and press Enter to run it again.")

    def _run_command(self, text: str) -> bool:
        """Run a `/command` if the text is one. True when it was handled.

        The model is not consulted for these: on a small local model, asking it
        to translate "clear this conversation" into the right tool call is a
        coin flip. Everything still goes through the same permission layers -
        `/undo` runs the real skill, so the same consent key applies.
        """
        from shani_chronoa.gui import commands as slash
        command, argument = slash.lookup(text)
        if command is None:
            return False
        try:
            said = command.run(self, argument)
        except Exception as exc:  # noqa: BLE001 - a command must not take the window
            logger.error(f"Command /{command.name} failed: {exc}", exc_info=True)
            self.set_status(f"/{command.name} did not work: {exc}")
            return True
        if said:
            self.set_response(said)
        else:
            self.set_status(f"/{command.name} - {command.summary}")
        if self._command_menu is not None:
            self._command_menu.popdown()
        return True

    def _sync_command_menu(self) -> None:
        """Show the command list while the field starts with a slash.

        Only on a *leading* slash, and only while it is still just a slash or a
        name: the menu is a discovery aid, and leaving it up while somebody
        types an ordinary sentence would be a popover arguing with them.
        """
        text = self._input_text()
        showing = text.startswith("/") and " " not in text
        if showing:
            prefix = text[1:].lower()
            self._fill_command_menu(prefix)
        if self._command_menu is not None:
            if showing and not self._command_menu.get_visible():
                # Parented first, here as well as on idle: `popup()` realizes
                # the popover, and realizing one with no parent segfaults in
                # GTK. Typing "/" before the idle callback had run crashed the
                # whole app - reproduced under CPU load, two of two runs.
                self._parent_command_menu()
                self._command_menu.popup()
            elif not showing and self._command_menu.get_visible():
                self._command_menu.popdown()

    def _parent_command_menu(self) -> None:
        """Attach the command popover to the entry, once. Safe to call again."""
        if self._command_menu is not None and self._command_menu.get_parent() is None:
            self._command_menu.set_parent(self._input_entry)

    def _fill_command_menu(self, prefix: str) -> None:
        from shani_chronoa.gui import commands as slash
        child = self._command_list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._command_list.remove(child)
            child = following
        for name, command in sorted(slash.commands().items()):
            if prefix and not name.startswith(prefix):
                continue
            row = Gtk.Button()
            row.add_css_class("flat")
            row.set_size_request(-1, -1)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label(label=f"/{name}")
            box.append(label)
            summary = Gtk.Label(label=command.summary)
            summary.add_css_class("dim-label")
            summary.set_xalign(1.0)
            summary.set_hexpand(True)
            # Right padding, because a summary that reaches the popover's edge
            # is a sentence that looks cut in half. Found by rendering the open
            # menu and looking at it: "/diff  Show what Chronoa changed, file by
            # file" ran straight into the rounded corner, with nothing between
            # the last letter and the border. Ten pixels is enough to read as
            # padding and small enough not to widen the menu noticeably.
            summary.set_margin_end(10)
            box.append(summary)
            row.set_child(box)
            row.update_property([Gtk.AccessibleProperty.LABEL],
                                [f"/{name}: {command.summary}"])
            row.connect("clicked", lambda _b, n=name: self._complete_command(n))
            self._command_list.append(row)
        # The person's own commands (`~/.config/shani-chronoa/commands/*.md`,
        # `user_prompts`). They worked when typed and were expanded on send, but
        # this menu listed only the built-ins, so a command someone had written
        # was reachable only by remembering its name. A built-in of the same
        # name wins, as it does when the line is sent.
        try:
            from shani_chronoa import user_prompts
            own = user_prompts.commands()
        except Exception:  # noqa: BLE001 - a menu must not take the window
            own = {}
        builtin = set(slash.commands())
        for name, path in sorted(own.items()):
            if name in builtin or (prefix and not name.startswith(prefix)):
                continue
            summary_text = user_prompts.command_summary(path) or "your command"
            row = Gtk.Button()
            row.add_css_class("flat")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.append(Gtk.Label(label=f"/{name}"))
            mine = Gtk.Label(label="yours")
            mine.add_css_class("dim-label")
            mine.add_css_class("caption")
            box.append(mine)
            summary = Gtk.Label(label=summary_text)
            summary.add_css_class("dim-label")
            summary.set_xalign(1.0)
            summary.set_hexpand(True)
            summary.set_ellipsize(Pango.EllipsizeMode.END)
            summary.set_margin_end(10)
            box.append(summary)
            row.set_child(box)
            row.set_tooltip_text(str(path))
            row.update_property([Gtk.AccessibleProperty.LABEL],
                                [f"/{name}, your command: {summary_text}"])
            row.connect("clicked", lambda _b, n=name: self._complete_command(n))
            self._command_list.append(row)

    def _complete_command(self, name: str) -> None:
        """Put `/name ` in the field and hand focus back to typing."""
        self._set_input_text(f"/{name} ")
        self._input_entry.grab_focus()
        if self._command_menu is not None:
            self._command_menu.popdown()

    def _on_input_activate(self, entry: Gtk.Entry) -> None:
        self._submit_input()

    def _last_change(self):
        """`(path, description)` for the newest undoable change, or None.

        Goes through `recent_changes()` rather than reading the ring itself:
        that function already skips entries with no file behind them and entries
        whose file is unchanged, so a preview that invented the "after" side is
        something this codebase decided against.
        """
        try:
            from shani_chronoa.skills import undo_last_change
            changes = undo_last_change.recent_changes()
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the undo ring", exc_info=True)
            return None
        if not changes:
            return None
        path = changes[-1][0]
        return path, path.name

    def _refresh_undo_button(self) -> None:
        """Show the undo button only when there is something it can undo."""
        button = getattr(self, "_undo_button", None)
        if button is None:
            return
        found = self._last_change()
        button.set_visible(found is not None)
        if found is None:
            button.set_tooltip_text("Nothing to undo")
            return
        path, name = found
        try:
            allowed, why = self._consent_for_undo()
        except Exception:                               # noqa: BLE001
            allowed, why = False, ""
        button.set_tooltip_text(
            f"Undo the change to {name}" if allowed
            else f"Undo is off: {why}")
        button.set_sensitive(True)
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"undo the change to {name}"])

    def _consent_for_undo(self):
        """The skill's own gate, asked here rather than guessed at.

        `undo_last_change` refuses without `file-edit-enabled` and says why. If
        the button ignored that, it would offer a control whose every press ends
        in a refusal.
        """
        from shani_chronoa.skills import undo_last_change
        return undo_last_change._consent(self._config)

    def _on_undo_clicked(self, _button: Gtk.Button) -> None:
        """Undo the last change, and say what was actually undone.

        **Reports the skill's own words rather than its own assumption.** The
        button says which file it will undo, but the ring can have moved under
        it, and the skill's refusal - "undoing a file change is turned off" - is
        more useful than anything this could invent.
        """
        from shani_chronoa.skills import undo_last_change
        found = self._last_change()
        if found is None:
            self.set_status("There is nothing left to undo")
            self._refresh_undo_button()
            return
        path, name = found
        try:
            said = undo_last_change._run({})
        except Exception:                               # noqa: BLE001
            logger.warning("undo failed", exc_info=True)
            self.set_status(f"Could not undo the change to {name}")
            return
        self.set_status(said.strip() or f"Undid the change to {name}")
        self._refresh_undo_button()

    def _turn_stats_for_footer(self):
        """The running turn's cost, or nothing when there is no turn to cost.

        **Read at the moment the turn is appended, and never cached.** The
        assistant's totals are for the turn in progress, so a footer captured
        earlier would report the number as it stood when the reply appeared
        rather than as it ended.
        """
        assistant = getattr(getattr(self, "app", None), "assistant", None)
        stats = getattr(assistant, "turn_stats", None)
        if stats is None:
            return None
        try:
            return stats()
        except Exception:                               # noqa: BLE001
            logger.warning("could not read the turn's cost", exc_info=True)
            return None

    def _minimap_jump(self, index: int) -> None:
        """A tick was clicked, or a key pressed on the map.

        **A bad index is refused here rather than passed down.** The map's tick
        count and the transcript's turn count are equal by construction, but the
        transcript also holds a placeholder row the map never drew, so "one past
        the end" is reachable by arithmetic and would raise inside
        `scroll_to_turn`'s row lookup. `scroll_to_turn` already range-checks;
        this keeps the failure from being an exception at all.
        """
        if not self._transcript.scroll_to_turn(index):
            logger.debug("minimap: no turn at index %s", index)

    def _on_turn_added(self, _transcript, role: str) -> None:
        """Keep the map's tick count equal to the conversation's turn count.

        **One tick appended, and the comment used to claim that while the code
        rebuilt the lot.** It called `set_turns`, which removes and recreates
        every tick - O(turns) per turn, quadratic over a conversation, and it
        replaces the highlighted tick widget outright, so the viewport's
        highlight silently moved to a fresh tick that no longer had the class.
        Nothing tested tick *identity*, only tick count, so a test would have
        agreed with either version.
        """
        self._minimap.append_turn(role)
        self._update_minimap_viewport()
        # A conversation is on screen, so the orb gives its height to it.
        self._orb.set_compact(True)

    def _on_minimap_scroll(self, _adjustment) -> None:
        self._update_minimap_viewport()

    def _update_minimap_viewport(self) -> None:
        """Where the map thinks you are.

        Wrapped because it runs on every `value-changed`: an exception here is a
        traceback per pixel of scroll, and the map is decoration - it must never
        be able to stop the conversation being scrolled.
        """
        adjustment = self._minimap_adjustment
        if adjustment is None or self._minimap is None:
            return
        try:
            rows = self._transcript._turn_rows()
            self._minimap.update_from(
                adjustment.get_value(), adjustment.get_upper(),
                adjustment.get_page_size(), rows)
        except Exception:  # noqa: BLE001
            logger.debug("minimap: viewport not drawn", exc_info=True)

    def _input_text(self) -> str:
        """Everything typed, newlines and all."""
        buf = self._input_entry.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _set_input_text(self, text: str) -> None:
        """Replace the contents and leave the caret at the end.

        `Gtk.TextView` has no `set_position(-1)`, and every call site that set
        text also wanted the caret after it - a suggestion chip or a slash command
        that fills the field should be followed by typing, not by nothing.
        """
        buf = self._input_entry.get_buffer()
        buf.set_text(text or "")
        buf.place_cursor(buf.get_end_iter())

    def _on_input_changed(self, _buffer) -> None:
        self._input_placeholder.set_visible(not self._input_text())
        self._sync_command_menu()
        self._note_draft()

    #: A draft is saved at most this many seconds after the last keystroke. Ten
    #: is long enough that a fast typist produces one write per pause rather
    #: than one per character, and short enough that closing the window within
    #: ten seconds of stopping still has the text.
    DRAFT_SAVE_SECONDS = 10

    def _note_draft(self) -> None:
        """Remember what is typed, so a crash or a close does not lose it.

        **A `GLib.timeout`, cancelled and restarted on every keystroke**, rather
        than a write per character: an assistant sitting idle while someone
        thinks would otherwise write a file a second for as long as they
        thought. Flushed on shutdown, where "in ten seconds" would be a lie.
        """
        if self._draft_source:
            return                      # restoring; do not save what we are reading
        if self._draft_timeout is not None:
            GLib.source_remove(self._draft_timeout)
        self._draft_timeout = GLib.timeout_add_seconds(
            self.DRAFT_SAVE_SECONDS, self._flush_draft)

    def _flush_draft(self) -> bool:
        from shani_chronoa import composer_draft

        self._draft_timeout = None
        composer_draft.save(self._input_text(),
                            composer_draft.draft_path(self._draft_key),
                            files=[str(p) for p in getattr(self, "_attachments", ())])
        return GLib.SOURCE_REMOVE

    def restore_draft(self) -> None:
        """Put this conversation's saved text and files back, without saving."""
        from pathlib import Path

        from shani_chronoa import composer_draft

        text, files = composer_draft.load_entry(
            composer_draft.draft_path(self._draft_key))
        if not text:
            return
        self._draft_source = True
        try:
            self._set_input_text(text)
            # **The chips with the sentence, in the same guarded block.** A
            # draft that restored only its words would leave the person reading
            # "check these three files" above an empty chip bar - which is the
            # partial restore `composer_draft`'s own docstring calls worse than
            # none.
            self._attachments = [Path(f) for f in files]
            self._refresh_attach_bar()
        finally:
            self._draft_source = False

    def set_conversation(self, key: str) -> None:
        """Move the composer to `key`'s draft, saving this one's on the way out.

        **Why this exists.** The composer is one widget and the draft was one
        file, so a half-typed question travelled with you: open another
        conversation and the box still held what you were writing to the
        previous one, and Enter put it into the new one - silently, because the
        transcript then showed your question in a conversation it had nothing to
        do with. `Ctrl+N` was already fixed for this (`_reset_conversation`
        clears the composer); switching was not, and it is the common case.

        **Save before swap, and cancel the pending write first.** A live
        `_draft_timeout` left armed across the swap would fire *after* the key
        changed and write the outgoing conversation's words into the incoming
        one's file - the same misfire one level down, and silent.

        **Not cleared, switched.** Going to a new conversation keeps what you
        had typed against the old one rather than destroying it, so going back
        finds it where you left it.
        """
        if self._draft_timeout is not None:
            GLib.source_remove(self._draft_timeout)
            self._draft_timeout = None
        # Empty on the very first call, when there is nothing to save yet and
        # `draft_path("")` is the legacy global file.
        self._flush_draft()
        self._draft_key = key or ""
        # **Emptied first, and this line is the fix.** `restore_draft` returns
        # early when there is no draft to restore, so without this a
        # conversation with nothing typed opened showing the *previous*
        # conversation's draft - which is the whole misfire this method exists
        # to remove, reintroduced one line after being fixed. The first version
        # of this method did exactly that, and the test written for it caught it
        # on the first run: switching to a conversation with no draft left the
        # other one's words sitting in the box.
        self._draft_source = True
        try:
            self._set_input_text("")
            self._attachments = []
            self._refresh_attach_bar()
        finally:
            self._draft_source = False
        self.restore_draft()

    def clear_draft(self) -> None:
        """The text is on its way; it is no longer a draft.

        **This conversation's draft, not a global one** - clearing on send must
        not wipe the draft belonging to a conversation the person is not in.
        """
        from shani_chronoa import composer_draft

        if self._draft_timeout is not None:
            GLib.source_remove(self._draft_timeout)
            self._draft_timeout = None
        composer_draft.clear(composer_draft.draft_path(self._draft_key))

    def _composer_is_empty(self) -> bool:
        """True when Enter has nothing to send.

        **Three things count as something to send, and all three must be absent.**
        The text is compared the way `_submit_input` compares it - stripped -
        so this cannot disagree with what would actually be submitted; the
        attachments are read off the list itself rather than through
        `take_attachments()`, which *consumes* them; and a pending question is
        the third, because a question is answered from its own buttons and
        opening a microphone unasked in the middle of a permission prompt is
        the surprise this file records most of its entries about.
        """
        if self._input_text().strip():
            return False
        if getattr(self, "_attachments", ()):
            return False
        return self._pending_question is None

    def _on_input_key_pressed(self, _view, keyval, _keycode, state) -> bool:
        """Enter sends; Shift+Enter is a newline; Enter on nothing starts listening.

        The convention every chat UI has, and the one a person who cannot reach
        the Send button needs: without Shift there is no way to type two lines,
        and without Enter there is no way to send from the keyboard.

        **The third case is the orb's key, which the orb's own tooltip promised.**
        It read *"Start listening (Enter)"* while `toggle-listening` had no
        accelerator anywhere in the tree - so a newcomer on the empty state
        pressed Enter and got nothing at all, because the composer's Enter is
        send and an empty send returns early. The rule written into
        `_on_window_key_pressed` twenty lines below this one says what that
        teaches: *a hint that names a key which is not bound is worse than no
        hint.*

        Enter with nothing typed, nothing attached and no question pending is
        therefore the one keypress on this screen that reaches voice - which is
        what makes the promise true rather than deleting it. It goes through the
        **same app action the orb button uses**, so the two cannot drift; that
        is the reason `_submit_input` exists for the send button and the Enter
        key, and it applies here for the same reason.

        **It is a toggle, like the orb.** A second Enter ends the turn early
        rather than waiting out the silence, because that is what
        `_toggle_listening` already does for a press of the orb itself.
        """
        if keyval != Gdk.KEY_Return and keyval != Gdk.KEY_KP_Enter:
            return False
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if shift:
            return False            # let it through: a newline
        if self._composer_is_empty():
            self._app.activate_action("toggle-listening", None)
            return True
        self._on_input_activate(self._input_entry)
        return True

    def _on_window_key_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        """`?` opens help; Escape stops a running turn and nothing else.

        Returns True only when it acted, so Escape keeps its other jobs - closing
        a popover, dismissing a question - everywhere else.

        **`?` needs Shift, so the shifted form is what is tested.** `Gdk.KEY_question`
        is the key *with* shift; the bare key is a plain `Gdk.KEY_slash`. A check
        for the bare slash would fire on every `/` typed into the composer, which
        is how a help window ends up opening over the thing you were typing.

        **It is here because the shortcut was going to be advertised.** The
        empty state names `?`, and a hint that names a key which is not bound is
        worse than no hint: a person presses it, nothing happens, and they learn
        that this interface lies to them.
        """
        if keyval == Gdk.KEY_question:
            return self.open_help() is not None

        if keyval != Gdk.KEY_Escape:
            return False
        stop = getattr(self, "_turn_stop_button", None)
        if stop is None or not stop.get_visible():
            return False
        self._on_turn_stop_clicked(stop)
        return True

    def _on_turn_stop_clicked(self, _button: Gtk.Button) -> None:
        """Stop the turn in progress, and say plainly what that does.

        **It stops before the next action, not the one in flight.** Cancelling a
        `chat_message` mid-stream would leave half an answer in the transcript,
        so the control is worded to promise only what the mechanism delivers. A
        stop that claims a hard kill it cannot perform is the same defect as a
        chip that reports a state the app has overridden.
        """
        from shani_chronoa import permissions
        try:
            stopped = permissions.cancel_turn()
        except Exception:                               # noqa: BLE001
            logger.warning("could not stop the turn", exc_info=True)
            self.set_status("This turn could not be stopped")
            return
        self.set_status("Stopping after this step" if stopped
                        else "Nothing was running")

    def _on_send_clicked(self, _button: Gtk.Button) -> None:
        self._submit_input()

    def _submit_input(self) -> None:
        """Send whatever is typed, from Enter or the button.

        Both call this rather than duplicating it, so the send button cannot
        drift from the key binding - the bug class where a control is wired to
        a slightly different action than the shortcut beside it.
        """
        raw = self._input_text()
        text = raw.strip()
        if not text:
            return
        self._set_input_text("")
        # **Cleared here, before anything else can fail.** Every path below this
        # line can return early - a slash command, a question being answered - and
        # a draft that outlives the turn it started would restore text that has
        # already been asked.
        self.clear_draft()
        if self._run_command(text):
            return
        if self._pending_question is not None:
            # Answering out loud or typing both land here, and both resolve the
            # question rather than opening a new turn - the turn is still blocked
            # inside the tool waiting for exactly this.
            self._resolve_question(text)
            return
        names, files_block = self.take_attachments()
        self.add_user_turn(text + (("\n📎 " + ", ".join(names)) if names else ""))
        # The stripped text, not the raw entry contents: the turn the user can
        # see is the stripped one, and emitting the raw string meant the
        # assistant received something the transcript never showed. The one
        # addition is the attachment block, shown above as the files' names.
        self.emit("user-input", text + files_block)

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def submit_text(self, text: str) -> None:
        """Send `text` as if typed - for `--ask=`, through the same path as Enter."""
        self._set_input_text(text)
        self._submit_input()

    def get_input_text(self) -> str:
        return self._input_text()

    def set_input_text(self, text: str) -> None:
        """Put text in the composer without sending it.

        Separate from `submit_text` because "show this in the box" and "send
        this" are different acts, and a caller wanting the first used to have
        neither: it could only reach `_input_entry` directly, which is how the
        `?` argument of `/help` ended up nowhere.
        """
        self._set_input_text(text or "")
        self._input_entry.grab_focus()

    def clear_input(self) -> None:
        self._set_input_text("")

    __gsignals__ = {
        "user-input": (GObject.SignalFlags.RUN_FIRST, str, (str,)),
        # Emitted after the transcript has dropped a turn, so the application can
        # take the same turn out of the assistant's history. bool in the return
        # means "I did rewind"; the window checks it, because a window whose
        # display and the model's memory disagree is the failure this exists to
        # avoid.
        "rewind-history": (GObject.SignalFlags.RUN_FIRST, bool, (str,)),
    }


# The main window's addressable pages, declared at import time so that
# `shani-chronoa --show-page=main:quick-ask`, a notification, or a keybinding
# can name one. `show_page` above has always understood these ids; the ids
# themselves were never registered, so `pages.show("main:conversation")`
# answered "no window called 'main'" - and the *main window* was the one
# destination every caller is most likely to name.
def _register_main_pages() -> None:
    from shani_chronoa import pages as page_registry
    page_registry.register(
        "main",
        [("conversation", "Conversation"), ("quick-ask", "Quick ask"),
         ("conversations", "Saved conversations"), ("panels", "Panels"),
         # Registered here and not only in SURFACE_IDS: the button beside
         # Dictate calls pages.show("listening"), and it returned False -
         # found by pressing it, not by reading it. A surface nobody
         # registered is the sidebar button nobody added.
         ("listening", "Listening")],
        factory=lambda app, config=None: ChronoaWindow(app),
    )


_register_main_pages()
