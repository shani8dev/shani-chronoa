"""The voice surface: what Chronoa is speaking with, and what else is installed.

One question a person actually asks - *what voice is this thing using, and what
else is here?* - answered from the code that would act on it rather than from a
second copy of it. There are four answers on this page, in this order:

- **The engine that would speak the next reply.** Resolved through the real
  chain in `tts.py`: `PiperTTS.engine()` on the running dispatcher (`app.tts`,
  the object every reply goes through) or on one built the way
  `application._init_components` builds it. Not a re-implementation of the
  cascade - a page that copied `_engine()` would be a second thing to be wrong
  the day the chain changes, which is the failure this repo keeps recording.
  `engine()` is called with no argument on purpose: that is the "what *could*
  speak here" question `skills/speak.py` asks, and the engine is chosen per
  reply, so a panel opened outside a reply has no other honest answer to give.
  **When that call raises, this page says it cannot be determined and names no
  engine at all.** A guessed engine here is the expensive kind of wrong: the
  whole panel would then confidently describe a voice nobody is using.

- **The voice, and the timbre pass.** The voice comes from the dispatcher
  itself (`kokoro_voice()` when Kokoro speaks, `voice` for Piper), and the
  pitch/tempo/rate row is built from `timbre_description()`,
  `timbre_effects()` and `timbre_problem()` - the same three functions
  `settings_window/voice.py` reads and the same ones `synthesize()` acts on.
  When SoX is absent and a transform is set, the reason is shown in full, next
  to the sentence that matters: a skipped preference is not a lost reply.
  `AGENTS.md` records that a user's speech must never stop over a preference,
  and counting a skipped transform as a failure is exactly how that would
  happen (three of them put speech into the degraded-after-three cooldown).

- **Speech input.** The backend from `stt_backend`, the model resolved the way
  `app/voice.py:_build_stt` resolves it, and the model file's own state on
  disk - asked of the real `STT` object through `senses.hearing.stt_problem`,
  which is the function that distinguishes "no whisper.cpp" from "no model
  downloaded". Collapsing those two is how someone reinstalls a package they
  already have and never downloads the model.

- **Everything else that is installed**, one row per known voice plus one per
  engine, so "what would switching mean" is answerable without leaving the
  page: what is downloaded, what it would cost to download, and which one is
  in use. The cost row states the one number this repository has actually
  measured (Kokoro's real-time factor of about 1.2, which is why it is opt-in)
  and says "not measured in this repository" for the engines with no recorded
  figure, rather than inventing a plausible one.

**Three states everywhere: yes, no, and undetermined.** `installed` /
`not installed` / `unknown`, the same vocabulary `model.py` uses, so no row
here can invent a fourth. A probe that raised is `unknown` and says why, in
this panel above all: a voice page that answered "espeak-ng" because the
settings object was unreadable would send someone to install a package whose
absence was never established.

**Markup is escaped where the label parses markup, and nowhere else.** Every
untrusted string on this page - a voice id from a gsetting, a model name, a
path, a probe's exception text - goes through `markdown_lite.escape` before it
reaches `common.row()`, *when* the row will be an `Adw.ActionRow`. That
condition is not decoration and it is not this repo's usual answer:
`model.py` sets `use_markup(False)` and shows the raw string, which works, but
an `Adw.ActionRow`'s title *and* subtitle are both set through Pango markup
(measured on libadwaita 1.5 - `common.row`'s docstring assumes the subtitle does
not), and a name carrying a bare `&` or a `<b>` is refused by the parser with the
label left **empty**. Escaping keeps the rendered text exactly the name. In the
plain-GTK branch it must *not* escape: `common.row()` builds
`Gtk.Label(label=...)`, which takes no markup, so an escaped string would put
`&lt;b&gt;` on screen. Hence `common.adw_ready()` in `_row` - the same predicate
`common.row()` branches on - rather than one arrangement chosen on faith.

**Read-only.** Nothing here downloads an engine or a voice, writes a setting,
or starts anything. The page that changes the voice is the setup wizard's
(`setup_wizard.setup_voice`, which installs the engine the voice needs) and
Settings' Voice output group, which owns the switch and the three sliders.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import sherpa, stt, stt_provision, tts, voices  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Voice"
ICON = "audio-volume-high-symbolic"
SECTION = "What Chronoa knows"
SUBTITLE = ("Read from this computer: the engine that would speak the next reply, "
            "the voice it would use, and what else is installed to replace it. "
            "Nothing on this page downloads anything or changes a setting.")

#: The row vocabulary, borrowed from `model.py` so no panel here can invent a
#: fourth word for the same three states.
INSTALLED, NOT_INSTALLED, UNKNOWN = "installed", "not installed", "unknown"

#: The chain in `tts.PiperTTS._engine`'s order, because that order *is* the
#: answer to "why not the other one": everything above the chosen engine is
#: better-sounding and unused, which is what the sentence under the engine row
#: is built from.
#:
#: **`cloud` is last, and it was missing.** `_engine()` grew a fifth link in
#: 2026-10-06 (`cloud_voice.CloudTTS`, opt-in, only reached when none of the four
#: local engines can speak), and this tuple was not updated - which is the panel-
#: silently-incomplete defect this page's own docstring warns about for Eyes. A
#: machine whose chosen engine was `cloud` would have found no entry above its own
#: and been told it was using nothing. `tests/test_surface_voice.py` now asserts
#: that `CHAIN` and `_engine()`'s outcomes cannot drift apart, so the next link
#: fails here rather than on screen.
CHAIN = ("kokoro", "piper", "rhvoice", "espeak-ng", "cloud")

#: What each engine costs, in the only currency this repository has measured -
#: the real-time factor, seconds of wall clock per second of speech, where
#: above 1 means the reply cannot finish before playback would start. Copied
#: from the measurements recorded in `tts.py`, `voices.py` and `AGENTS.md`
#: rather than estimated here: an invented RTF on a voice page is exactly the
#: confident wrong answer this page exists to avoid. An engine with no recorded
#: figure says so.
COST: Dict[str, str] = {
    "kokoro": ("about 1.2 - 7.7 s of wall clock for 6.4 s of speech on a CPU "
               "(sherpa-onnx, measured in a ShaniOS slot, the same with 2 or 4 "
               "threads; the earlier Python path measured 1.22). Above 1, so "
               "each reply would open with about six seconds of silence - which "
               "is why it is opt-in"),
    "espeak-ng": ("about 0.005 - 0.018 s of wall clock for 3.7 s of audio. Far "
                  "faster than real time, and it sounds like a 1970s speech "
                  "chip; it is the floor every Shanios image ships, not a "
                  "preference"),
    # **No RTF is claimed**, and the reason is the disclosure rather than the
    # absence of a measurement: this engine's cost is a network round trip to
    # somebody else's computer, with the reply text leaving the machine. Quoting
    # a latency without that would make the row a better sell than it is.
    "cloud": ("no real-time factor on record, because the cost here is not only "
              "time: the reply text is sent to a cloud provider to be spoken. It "
              "is off by default and last in the chain, so nothing local is "
              "displaced by turning it on"),
}

#: Said for an engine with no measurement on record. Not a shrug: a voice page
#: that guesses a speed for the two engines nobody timed is worse than one that
#: admits the number is not known.
COST_UNMEASURED = "not measured in this repository - no real-time factor is on " \
                  "record for it, so none is claimed here"

#: Longest detail a row shows before it is cut. Voice labels are short; a probe's
#: exception string is not.
DETAIL_LIMIT = 220


# ---------------------------------------------------------------------------
# Reading state without raising
# ---------------------------------------------------------------------------


def _clip(text: Any, limit: int = DETAIL_LIMIT) -> str:
    flat = " ".join(str(text if text is not None else "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def _escape(text: Any) -> str:
    """A string safe to hand to a row's label.

    Only meaningful when the row renders markup. `Adw.ActionRow` renders both
    title and subtitle through Pango markup, and a bare `&` or a `<b>` in one is
    refused by the parser with the label left *empty* (measured on libadwaita
    1.5, and `common.row`'s own docstring assumes the subtitle takes no markup -
    it does), so there the string has to be escaped.

    In the plain-GTK branch it must not be: `common.row()` builds
    `Gtk.Label(label=...)`, which takes no markup at all, so an escaped string
    would put `&lt;b&gt;` on screen. That is why `_row` asks `common.adw_ready()`
    - the same predicate `common.row()` branches on - rather than picking one
    arrangement and hoping.
    """
    from shani_chronoa import markdown_lite

    return markdown_lite.escape(str(text))


def _read_setting(config: Any, name: str, default: str = "") -> str:
    """One setting, from a `ChronoaConfig` or anything shaped like one.

    `piper_voice` and `stt_backend` are properties on the real class, so a
    property and a method are both accepted; a read that raises is `default`,
    never an exception out of `build()`.
    """
    if config is None:
        return default
    try:
        value = getattr(config, name)
        if callable(value):
            value = value()
    except Exception:  # noqa: BLE001 - an unreadable setting is a state, not a crash
        logger.debug("cannot read %s", name, exc_info=True)
        return default
    return default if value is None else str(value)


def _read_bool(config: Any, key: str, default: bool = False) -> Optional[bool]:
    """A boolean setting, or None when it could not be read at all."""
    if config is None:
        return None
    try:
        return bool(config.get_bool(key, default))
    except Exception:  # noqa: BLE001 - unknown, which is what it is
        logger.debug("cannot read %s", key, exc_info=True)
        return None


def _shown(text: Any) -> str:
    """The string a label on this page displays for `text`.

    Escaped when the label will parse markup, verbatim when it will not - the
    same condition `_row` applies, exposed here because the accessors and the
    summary label build their own widgets rather than going through `_row`.
    Asking `common.adw_ready()` per call is deliberate: it is the same
    predicate `common.row()` branches on, and caching it would let a page built
    before and after a branch decision disagree with itself.
    """
    return _escape(text) if common.adw_ready() else str(text)


def _state_word(state: Optional[bool], yes: str = INSTALLED, no: str = NOT_INSTALLED) -> str:
    return UNKNOWN if state is None else (yes if state else no)


def _add(group: Gtk.Widget, row: Gtk.Widget) -> None:
    """Put a row into a group from `common.group()`.

    `Adw.PreferencesGroup` has `add()`; a plain `Gtk.Box` on GTK4 does not -
    `append` replaced `add` - and `common.group()` returns whichever of the two
    libadwaita allowed. Asking which is one line, and it is why the plain-GTK
    fallback is a fallback that works rather than one that raises.
    """
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(row)
    else:
        group.append(row)


class _RowText:
    """A row's title or detail, readable from the widget.

    `Adw.ActionRow` keeps both inside labels libadwaita does not hand back, and
    the label a page renders is not necessarily the string it was given once
    escaping is involved - so this is the text the row *displays*, which is what
    a caller (and a test) has to be able to check.
    """

    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text


def _row(title: str, detail: str, tooltip: str = "") -> Gtk.Widget:
    """One line of the page: a fixed title, and content in the subtitle.

    Titles are this module's own words; everything that came off this machine is
    in the subtitle. Both are carried on the row as `_row_title` / `_row_detail`
    so a caller can check a row's wording against the function that produced it,
    and the tooltip keeps the provenance visible.

    The escaping is conditional and the condition is `common.adw_ready()` - the
    predicate `common.row()` itself branches on, called here for the same reason
    rather than imported around. Escaping only where the label parses markup
    keeps the words on screen in both branches; escaping unconditionally would
    print `&lt;b&gt;` in the plain-GTK one, and escaping nowhere would empty the
    label in the libadwaita one. Both were measured, not assumed.
    """
    shown = _escape if common.adw_ready() else (lambda value: str(value))
    row = common.row(shown(title), shown(detail))
    text = tooltip or f"{title}: {detail}"
    row.set_tooltip_text(shown(text))
    row.update_property([Gtk.AccessibleProperty.LABEL], [shown(f"{title}: {detail}")])
    row._row_title = _RowText(shown(title))
    row._row_detail = _RowText(shown(detail))
    return row


# ---------------------------------------------------------------------------
# The engines
# ---------------------------------------------------------------------------


class _Engine(NamedTuple):
    key: str
    label: str
    state: Optional[bool]
    detail: str
    source: str


def _kokoro_engine(dispatcher: "tts.PiperTTS", config: Any) -> _Engine:
    """Kokoro, the neural voice: can it speak here, and is it switched on.

    Two separate facts and both are shown. `kokoro_problem()` is the public
    question ("can it speak"), the switch is a preference, and a page that
    merged them would report a working Kokoro as "not installed" because
    somebody declined it - the same class of wrong as the one `tts.py`'s
    `_kokoro_reason` orders its own checks to avoid.
    """
    source = "PiperTTS.kokoro_problem(), plus the kokoro-tts-enabled setting"
    try:
        problem = str(dispatcher.kokoro_problem() or "")
    except Exception as exc:  # noqa: BLE001 - a probe that raised is unknown
        logger.debug("cannot read the Kokoro state", exc_info=True)
        return _Engine("kokoro", "Kokoro (neural, 82M)", None,
                       f"unknown - the Kokoro check failed: {_clip(exc)}", source)
    if problem:
        return _Engine("kokoro", "Kokoro (neural, 82M)", False, f"not in use - {problem}", source)
    switch = _read_bool(config, dispatcher.KOKORO_KEY, False)
    if switch is None:
        return _Engine("kokoro", "Kokoro (neural, 82M)", True,
                       "installed and able to speak, but whether it is switched on could not be read "
                       f"({dispatcher.KOKORO_KEY})", source)
    if not switch:
        return _Engine("kokoro", "Kokoro (neural, 82M)", True,
                       f"installed and able to speak, but switched off ({dispatcher.KOKORO_KEY}), "
                       "which is why it is not the engine", source)
    return _Engine("kokoro", "Kokoro (neural, 82M)", True,
                   f"installed, able to speak, and switched on ({dispatcher.KOKORO_KEY})", source)


def _piper_engine(dispatcher: "tts.PiperTTS") -> _Engine:
    """Piper: the program *and* the voice file, because the chain needs both.

    `PiperTTS._engine` asks for `piper_path` and `voice_path` together, so a row
    that reported only "the program is installed" would say a chain rung is
    available when the voice that makes it usable is not on disk.
    """
    source = "voices.piper_binary(), PiperTTS.voice_path"
    try:
        binary = voices.piper_binary()
        path = str(dispatcher.voice_path or "")
        on_disk = bool(path) and os.path.exists(path)
    except Exception as exc:  # noqa: BLE001 - a probe that raised is unknown
        logger.debug("cannot read the Piper state", exc_info=True)
        return _Engine("piper", "Piper", None, f"unknown - the Piper check failed: {_clip(exc)}", source)
    if not binary:
        return _Engine("piper", "Piper", False,
                       "not in use - piper-tts is not on this computer; Chronoa's setup, Voice page, "
                       "installs it into this account (the distribution's `piper` package is a "
                       "different program: a gaming-mouse configurator)", source)
    if not on_disk:
        return _Engine("piper", "Piper", False,
                       f"not in use - the program is installed ({binary}) but this account's voice file "
                       f"for {dispatcher.voice or 'the chosen voice'} is not on disk "
                       f"({path or 'no voice chosen'})", source)
    return _Engine("piper", "Piper", True,
                   f"installed and usable - {binary} with the voice file {path}", source)


def _rhvoice_engine() -> _Engine:
    """RHVoice: the program from Arch's `rhvoice` package, plus a voice.

    `PiperTTS._rhvoice_has_voice()` is reached directly because there is no
    public probe for it, and one implementation of "is a voice installed" is
    the point: a second scan of `/usr/share/RHVoice/voices` here would be a
    second thing to disagree with the chain that picks the engine.
    """
    source = "shutil.which('RHVoice-test'), PiperTTS._rhvoice_has_voice()"
    try:
        binary = shutil.which("RHVoice-test")
        has_voice = bool(tts.PiperTTS._rhvoice_has_voice())
    except Exception as exc:  # noqa: BLE001 - a probe that raised is unknown
        logger.debug("cannot read the RHVoice state", exc_info=True)
        return _Engine("rhvoice", "RHVoice", None, f"unknown - the RHVoice check failed: {_clip(exc)}", source)
    if not binary:
        return _Engine("rhvoice", "RHVoice", False,
                       "not in use - RHVoice-test is not on this computer (Arch: the rhvoice package "
                       "and a rhvoice-language-* and a rhvoice-voice-*)", source)
    if not has_voice:
        return _Engine("rhvoice", "RHVoice", False,
                       f"not in use - {binary} is installed but no RHVoice voice is in "
                       "/usr/share/RHVoice/voices", source)
    return _Engine("rhvoice", "RHVoice", True,
                   f"installed with a voice - {binary} speaks the default voice unless 'slt' is present", source)


def _espeak_engine() -> _Engine:
    """espeak-ng: the floor, and a hard dependency of every Shanios image."""
    source = "shutil.which('espeak-ng')"
    try:
        binary = shutil.which("espeak-ng")
    except Exception as exc:  # noqa: BLE001 - a probe that raised is unknown
        logger.debug("cannot read the espeak-ng state", exc_info=True)
        return _Engine("espeak-ng", "espeak-ng", None,
                       f"unknown - the espeak-ng check failed: {_clip(exc)}", source)
    if not binary:
        return _Engine("espeak-ng", "espeak-ng", False,
                       "not in use - espeak-ng is not on this computer. It is a hard dependency of every "
                       "Shanios image, so this means the package was removed", source)
    return _Engine("espeak-ng", "espeak-ng", True,
                   f"installed - {binary}. A hard dependency: it is the floor speech always has, "
                   "not a preference", source)


def _cloud_engine(config: Any = None) -> _Engine:
    """Cloud synthesis: the last link, and the only one that leaves the machine.

    Its detail leads with **where the reply goes**, not with a speed, because
    speed is not this engine's cost - and because a row that reported only a
    latency would read as a neutral alternative to Kokoro rather than as the one
    thing on this page that sends a reply to somebody else's computer.

    The availability question is asked through `cloud_voice.CloudTTS` - the same
    object `tts.py` asks, so this row and the real cascade cannot disagree -
    **with the config handed in.** Constructing one here would instantiate a
    `Gio.Settings`, which on first use creates `~/.config/glib-2.0`; this panel is
    required by `tests/test_surface_voice.py` to write nothing to the config home,
    and a read-only page that created a settings directory is not read-only.
    """
    source = "cloud_voice.CloudTTS(config=...).is_available()"
    try:
        from shani_chronoa import cloud_voice
        refusal = cloud_voice.CloudTTS(config=config).refusal()
    except Exception as exc:  # noqa: BLE001 - a probe that raised is unknown
        logger.debug("cannot read the cloud speech state", exc_info=True)
        return _Engine("cloud", "Cloud provider", None,
                       f"unknown - the cloud speech check failed: {_clip(exc)}", source)
    if refusal:
        return _Engine("cloud", "Cloud provider", False,
                       f"not in use - {refusal}", source)
    return _Engine("cloud", "Cloud provider", True,
                   "in use as a last resort - the reply text is sent to a cloud provider to be "
                   f"spoken. {COST['cloud']}", source)


def _chain(dispatcher: "tts.PiperTTS", config: Any) -> List[_Engine]:
    """Every engine, in the order `tts.PiperTTS._engine` tries them.

    **Five, and the fifth is `cloud`** - added 2026-10-06 with `cloud_voice.py`.
    `_engine()` already chose it; this list is what turns that into rows, and it
    was not extended, so a machine actually speaking through a provider would
    have been told it was using nothing.
    """
    return [_kokoro_engine(dispatcher, config), _piper_engine(dispatcher),
            _rhvoice_engine(), _espeak_engine(), _cloud_engine(config)]


def _resolve_choice(dispatcher: "tts.PiperTTS") -> Tuple[Optional[str], str]:
    """(chosen engine or None, what went wrong if anything did).

    `None` covers the two failures that must never be dressed up as an answer:
    the call raised, or it returned something that is not a name. Neither is a
    "no engine" either - that one is returned by `engine()` itself, and it is an
    answer. Asked once per refresh so the speaking row and the voice rows cannot
    disagree about which engine is in use.
    """
    try:
        chosen = dispatcher.engine()
    except Exception as exc:  # noqa: BLE001 - a failed probe is unknown, not a fallback
        logger.debug("the engine chain could not be resolved", exc_info=True)
        return None, f"asking the engine chain failed: {_clip(exc)}"
    if chosen is None or (isinstance(chosen, str) and not chosen.strip()):
        return None, ""
    if not isinstance(chosen, str):
        return None, f"the engine check returned {type(chosen).__name__} rather than an engine name"
    return chosen.strip(), ""


def _speaking_now(dispatcher: "tts.PiperTTS", chain: List[_Engine]) -> Tuple[str, str]:
    """(title, detail): what would speak the next reply, through the real chain.

    Four answers, because there are four states: a name, "no engine at all",
    "an engine this panel has no row for" (the chain's own doing, reported as
    given rather than rewritten into one of the four), and "cannot be
    determined". The last is the one that must never become the first - so when
    the check fails, no engine is named anywhere in what this returns, not even
    in the explanation.
    """
    chosen, failure = _resolve_choice(dispatcher)
    if failure:
        return ("cannot be determined",
                f"cannot be determined - {failure}. Nothing on this page started, stopped or changed an "
                "engine, and the engine that would speak is not guessed from the other rows")
    if chosen is None:
        return ("no engine at all",
                "no engine - none of " + ", ".join(CHAIN) + " can speak here, so replies are "
                "not spoken aloud. On a Shanios image that means espeak-ng was removed; elsewhere "
                "Chronoa's setup, Voice page, downloads one")
    if chosen not in CHAIN:
        # An engine name this build does not know is still a name, and it is the
        # chain's own - so it is reported as given rather than quietly rewritten
        # into one of the four. The rows below simply say nothing above it.
        return (f"speaking with {chosen}",
                f"{chosen} - an engine this panel has no row for, named by the chain itself. "
                "The engines it is chosen over are the rows below.")
    above = [e for e in chain if CHAIN.index(e.key) < CHAIN.index(chosen)]
    if above:
        why = ("Better-sounding engines above it in the chain are not in use: "
               + "; ".join(f"{e.label} - {e.detail}" for e in above))
    else:
        why = "Nothing above it in the chain, so it is the best engine installed."
    note = ("The engine is chosen per reply: this is the answer for a reply in English. A reply written "
            "in another of your languages' scripts keeps that language's Piper voice instead.")
    return (f"speaking with {chosen}", f"{chosen}. {why}. {note}")


# ---------------------------------------------------------------------------
# The voice, and the timbre pass
# ---------------------------------------------------------------------------


class _Voice(NamedTuple):
    name: str
    label: str
    engine: str
    on_disk: Optional[bool]
    detail: str


def _voice_in_use(dispatcher: "tts.PiperTTS", chosen: Optional[str]) -> _Voice:
    """The voice that would be heard, and whether its file is on disk.

    Two engines, two owners: a Kokoro voice is the `kokoro-voice` setting, a
    Piper voice is the dispatcher's own `voice`. When neither speaks (the floor,
    or no engine) the chosen Piper voice is still named - and the row says so
    rather than implying it is what is audible now.
    """
    if chosen == "kokoro":
        try:
            name = str(dispatcher.kokoro_voice())
            spec = voices.KOKORO_VOICES.get(name)
            label = spec.label if spec else "a Kokoro voice this build does not have a name for"
            problem = str(dispatcher.kokoro_problem() or "")
        except Exception as exc:  # noqa: BLE001 - unknown is a state this page can show
            logger.debug("cannot read the Kokoro voice", exc_info=True)
            return _Voice("", "unknown", "kokoro", None, f"unknown - {_clip(exc)}")
        detail = (f"{name} ({label}). " + (
            "its model and the program that runs it are on disk"
            if not problem else f"not on disk - {problem}"))
        return _Voice(name, label, "kokoro", not problem, detail)
    try:
        name = str(dispatcher.voice or "")
        spec = voices.VOICES.get(name)
        label = spec.label if spec else "a voice Chronoa has no name for"
        path = str(dispatcher.voice_path or "")
        # Two different facts about two different files, and both shown: the
        # chain loads `voice_path` (the .onnx alone), while a complete install is
        # the .onnx *and* its sidecar .json, which is what `voice_installed`
        # checks and what `voices.install_voice` writes. A voice with the model
        # but no sidecar is a half-install, and rounding it up to "downloaded"
        # is how somebody ends up reinstalling a voice they have.
        downloaded = bool(voices.voice_installed(name))
        engine_finds = bool(path) and os.path.exists(path)
    except Exception as exc:  # noqa: BLE001 - unknown is a state this page can show
        logger.debug("cannot read the Piper voice", exc_info=True)
        return _Voice("", "unknown", "piper", None, f"unknown - {_clip(exc)}")
    if engine_finds:
        state = f"on disk - the engine loads {path}"
        if not downloaded:
            state += f". Incomplete: its sidecar {path}.json is missing, which Piper needs as well"
    elif downloaded:
        state = (f"downloaded in this account ({voices.voice_dir()}), but the engine looks at {path}, "
                 "which is not there")
    else:
        state = f"not downloaded - the engine looks at {path or 'nowhere: no voice is chosen'}"
    heard = ("This is the voice Piper would speak with."
             if chosen == "piper" else
             "Piper is not the engine right now, so this voice is not what is audible - it is what a "
             "Piper reply would use.")
    return _Voice(name, label, "piper", engine_finds, f"{name} ({label}). {state}. {heard}")


def _timbre_row(dispatcher: "tts.PiperTTS") -> Tuple[str, str]:
    """(title, detail) for the SoX pass, including why it was skipped.

    `timbre_description()`, `timbre_effects()` and `timbre_problem()` are the
    three functions `apply_timbre` itself branches on, so the row cannot describe
    a transform that will not run. The defaults are a genuine third state and are
    said as one: no effects means SoX is not started at all, which is why an
    untouched reply is byte-for-byte what its engine wrote.
    """
    source = "PiperTTS.timbre_description(), PiperTTS.timbre_effects(), PiperTTS.timbre_problem()"
    try:
        wanted = str(dispatcher.timbre_description() or "")
        effects = list(dispatcher.timbre_effects())
        problem = str(dispatcher.timbre_problem() or "")
        binary = str(dispatcher.sox_path() or "")
        engine_rate = float(getattr(dispatcher, "rate", 1.0))
    except Exception as exc:  # noqa: BLE001 - a probe that raised is unknown
        logger.debug("cannot read the timbre settings", exc_info=True)
        return "unknown", f"unknown - the timbre settings could not be read: {_clip(exc)}"

    speed = f"The engine's own speaking speed (speech-rate) is {engine_rate:g}x, applied by the engine itself. "
    if not effects:
        no_sox = ("sox is not installed, but nothing is skipped: pitch, tempo and rate are all at their "\
                  "identity values" if not binary else "sox is not run at all")
        return ("Pitch, tempo and rate",
                f"the voice is left exactly as the engine wrote it - pitch 0, tempo 1, rate 1, so {no_sox}. "
                f"{speed}Read from {source}")
    applied = f"applied by sox ({binary}) as: sox <reply.wav> <out.wav> " + " ".join(effects)
    if problem:
        # The sentence that matters is the second one: `synthesize()` treats a
        # skipped transform as a success, because counting it as a failure is
        # how a preference nobody asked for would stop somebody's speech.
        return ("Pitch, tempo and rate - NOT applied",
                f"{wanted} is configured, but {problem}. The reply still speaks: a skipped preference is "
                f"never a lost reply. {speed}Read from {source}")
    return ("Pitch, tempo and rate", f"{wanted} is {applied}. {speed}Read from {source}")


# ---------------------------------------------------------------------------
# Speech input
# ---------------------------------------------------------------------------


class _Speech(NamedTuple):
    backend: str
    label: str
    model: str
    binary: str
    model_path: str
    state: Optional[bool]
    model_on_disk: Optional[bool]
    problem: str
    source: str


def _stt_for(app: Any, config: Any) -> Tuple[Optional[Any], str]:
    """The STT object the app would use, and where it came from.

    `app.stt` first: it is the object every utterance is transcribed through, and
    the panel must not describe a different one. Failing that, the object is
    built the way `app/voice.py:_build_stt` builds it - the persisted
    `whisper-model`, else the hardware tier's choice resolved to a model that is
    actually installed, and Parakeet's own default when that backend is chosen.
    A stub app with neither a live STT nor a hardware profile falls back to the
    same `base` `senses/hearing.py` falls back to.
    """
    live = getattr(app, "stt", None)
    if hasattr(live, "model_path") and callable(getattr(live, "is_available", None)):
        return live, "the running app's own stt object"
    backend = _read_setting(config, "stt_backend", stt.BACKEND_WHISPER) or stt.BACKEND_WHISPER
    model = _read_setting(config, "whisper_model", "")
    if not model:
        if backend.strip().lower() == stt.BACKEND_PARAKEET:
            model = str(stt_provision.PARAKEET_DEFAULT_MODEL)
        else:
            preferred = "base"
            hardware = getattr(app, "hardware", None)
            if hardware is not None:
                try:
                    preferred = str(hardware.get_whisper_model() or "base")
                except Exception:  # noqa: BLE001 - detection is best-effort, like app/voice.py
                    logger.debug("the hardware profile could not choose a speech model", exc_info=True)
            model = str(stt.installed_model(preferred))
    return stt.build_stt(model=model, language=_read_setting(config, "language", "en"), backend=backend), \
        "stt.build_stt(), the same call app/voice.py makes"


def _speech_input(app: Any, config: Any) -> _Speech:
    """The backend, the model, and whether that model's file is on disk.

    `model_on_disk` is asked of the file rather than derived from `state`, because
    `is_available()` is one boolean over two independent causes - no binary, or no
    model - and reporting "the model file is not on disk" because the *program*
    is missing would send somebody to download a model they already have. That is
    the mistake `senses/hearing.stt_problem` exists to prevent, so both facts are
    asked separately here.
    """
    backend = _read_setting(config, "stt_backend", stt.BACKEND_WHISPER) or stt.BACKEND_WHISPER
    label = "Parakeet (parakeet-cli)" if backend.strip().lower() == stt.BACKEND_PARAKEET \
        else "Whisper.cpp (whisper-cli)"
    try:
        engine, where = _stt_for(app, config)
    except Exception as exc:  # noqa: BLE001 - a construction that raised is unknown
        logger.debug("cannot construct the speech-input engine", exc_info=True)
        return _Speech(backend, label, "", "", "", None, None, f"unknown - {_clip(exc)}",
                       "app/voice.py:_build_stt, then senses.hearing.stt_problem()")
    source = f"{where}, then senses.hearing.stt_problem()"
    try:
        model = str(getattr(engine, "model", "") or "")
        binary = str(getattr(engine, "whisper_path", "") or "")
        path = str(getattr(engine, "model_path", "") or "")
        state = bool(engine.is_available())
        model_on_disk = bool(path) and os.path.exists(path)
        # `stt_problem` names which of the two halves is missing - no binary, or
        # no model - which is the whole difference between "install whisper-cpp"
        # and "download the model".
        from shani_chronoa.senses import hearing

        problem = str(hearing.stt_problem(engine) or "")
    except Exception as exc:  # noqa: BLE001 - a probe that raised is unknown
        logger.debug("cannot read the speech-input state", exc_info=True)
        return _Speech(backend, label, "", "", "", None, None, f"unknown - {_clip(exc)}", source)
    return _Speech(backend, label, model, binary, path, state, model_on_disk, problem, source)


# ---------------------------------------------------------------------------
# Every other voice
# ---------------------------------------------------------------------------


def _voice_catalogue(chosen: Optional[str]) -> List[Tuple[str, str, str]]:
    """(label, detail, key) for every voice this build knows, Piper's then Kokoro's.

    Two questions per row - is it downloaded, and what would downloading it cost -
    because "what else is installed" is only half of what somebody comparing
    voices wants to know. Sizes are the pinned specs' own, the same two sums the
    setup wizard's Voice page prints, so the two pages cannot disagree.
    """
    out: List[Tuple[str, str, str]] = []
    piper_mb = voices._PIPER.size_bytes / 1e6
    for key, spec in voices.VOICES.items():
        try:
            installed = bool(voices.voice_installed(key))
        except Exception:  # noqa: BLE001 - unreadable is reported as not installed
            logger.debug("cannot read the state of voice %s", key, exc_info=True)
            installed = False
        state = (f"downloaded ({key}.onnx is on disk)" if installed
                 else f"not downloaded - {spec.onnx_size / 1e6:.0f} MB, plus {piper_mb:.0f} MB for Piper once")
        marks = []
        if spec.language != "en":
            marks.append(spec.language)
        if key == chosen:
            marks.append("the voice configured here")
        out.append((f"{spec.label} (Piper)", f"{key}. {state}" + (f". {', '.join(marks)}" if marks else ""), key))
    kokoro_mb = (sherpa.RELEASE.size_bytes + voices._KOKORO_MODEL.size_bytes) / 1e6
    for key, spec in voices.KOKORO_VOICES.items():
        try:
            installed = bool(voices.kokoro_installed(key))
        except Exception:  # noqa: BLE001 - unreadable is reported as not installed
            logger.debug("cannot read the state of Kokoro voice %s", key, exc_info=True)
            installed = False
        state = ("downloaded (one sherpa-onnx release serves all six)"
                 if installed else f"not downloaded - {kokoro_mb:.0f} MB once, for all six Kokoro voices")
        marks = ["the neural voice"] if key == chosen else []
        out.append((f"{spec.label} (Kokoro)",
                    f"{key} (speaker id {spec.sid}). {state}" + (f". {', '.join(marks)}" if marks else ""), key))
    return out


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


class _VoiceSurface(Gtk.Box):
    """Everything under the page's toolbar: four groups, one question each.

    The page itself - title, subtitle, header bar, scrolling - is
    `common.surface()`'s. This is only what goes inside it, which is why
    `build()` returns the page and forwards the accessors below onto it: a
    libadwaita page cannot be subclassed in a module that does not ask for
    libadwaita.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._app = app
        self._now_rows: List[Gtk.Widget] = []
        self._engine_rows: List[Gtk.Widget] = []
        self._speech_rows: List[Gtk.Widget] = []
        self._catalogue_rows: List[Gtk.Widget] = []

        # **Reload is a banner, not a bare button.** It used to be a button with
        # a tooltip, sitting alone above the content with no explanation - so the
        # panel gave you a reading and a control, and never said that the reading
        # was from whenever the panel happened to be built. The sidebar builds a
        # panel once and keeps it, so that can be minutes ago, and on this panel
        # it is a long time: a voice installed by the setup wizard is invisible
        # here until this is pressed. `common.banner` carries the reason and the
        # control in one line, and gives the button a tooltip and an accessible
        # label derived from it.
        #
        # It stays above the content rather than in the header because
        # `common.surface()` builds that bar and offers no slot for a button.
        self._reload_button = common.banner(
            "Read once, when this panel was built. Nothing here is polled - press "
            "Reload to read the engines, the voice, the timbre settings and the "
            "speech-input model from this computer again.",
            "Reload",
            lambda: self.refresh())
        self.append(self._reload_button)

        # The panel's own health, above the content: can anything speak, and can
        # anything hear. One row, one dot, one word - the question this panel is
        # opened for, before the rows that hold the engines.
        #
        # It is a slot rather than a row, because `refresh()` replaces it: every
        # reading here is a claim about the moment it was asked, so a Reload has
        # to be able to move the word from "needs attention" to "ready" without
        # rebuilding the page.
        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.append(self._status_slot)
        #: The same health in the two places that show it: this row and the dot
        #: on the sidebar's row for this panel. One value, written on the way
        #: past and read on the way back, so the two cannot disagree.
        self.status_recorder = common.StatusRecorder()

        self._now_title = _RowText("")
        self._now_detail = _RowText("")
        self._now_row: Optional[Gtk.Widget] = None
        self._cost_row: Optional[Gtk.Widget] = None
        self._voice_detail_row: Optional[Gtk.Widget] = None
        self._timbre_row: Optional[Gtk.Widget] = None
        self._summary = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self._summary.add_css_class("dim-label")

        self._now_group = common.group(
            "Speaking now", "The engine and the voice the next reply would use.")
        self._engine_group = common.group(
            "Every engine, and why it is not in use",
            "The same order the chain tries them in; everything above the engine in use is better-sounding.")
        self._speech_group = common.group(
            "Hearing", "Speech input: which engine transcribes you, with which model file.")
        self._catalogue_group = common.group(
            "Other voices installed",
            "Every voice this build can install. Sizes are the pinned downloads.")
        for group in (self._now_group, self._engine_group, self._speech_group, self._catalogue_group):
            self.append(group)
        self.append(self._summary)

        self.refresh()

    # -- content ------------------------------------------------------------

    def _render_status(self, dispatcher, chain, chosen: "Optional[str]",
                       failure: str) -> None:
        """The panel's own health, rebuilt on every refresh.

        **The speaking half decides; the hearing half is reported under it.**
        Whether a reply is spoken is the question this panel is opened for, so
        "no engine at all" is `attention` - replies are not spoken, which is a
        real loss and not a subtlety - while "could not determine" is `unknown`,
        because a probe that raised has told us nothing at all. Those two read
        alike in the engine rows and mean opposite things in a dot.

        Speech input is asked through `_speech_input`, the same function the
        "Hearing" rows below use, so the sentence here cannot say listening is
        broken while a row under it says listening is fine. It is reported on the
        same row rather than given one of its own: a row per question would make
        the panel's health a list again, and the two are one answer to "can
        Chronoa talk to me".

        Written through `self.status_recorder`, which is what the sidebar's dot
        reads - so the dot cannot say "ready" while this says otherwise.
        """
        common.clear(self._status_slot)
        row = self.status_recorder.row

        hearing_detail = self._hearing_sentence()
        if failure:
            self._status_slot.append(row(
                common.STATUS_UNKNOWN,
                "It could not be determined whether Chronoa can speak",
                f"{failure}. {hearing_detail}"))
            return
        if chosen is None:
            names = " or ".join(e.label for e in chain) or "the engine chain"
            self._status_slot.append(row(
                common.STATUS_ATTENTION,
                "No voice engine is available",
                f"none of {names} can speak, so replies are not spoken aloud. "
                f"{hearing_detail}"))
            # **The one state here with something to do about it**, and the panel
            # used to only say so: "none of X can speak" names the problem and
            # stops. `common.open_setup` is the same door the Models panel's
            # button and the header's use, so all three are one route - and on a
            # Shanios image this state means espeak-ng was removed, which is a
            # package, so the wizard is where the fix is offered rather than a
            # sentence telling somebody to go and find it.
            self._status_slot.append(self._setup_button())
            return
        self._status_slot.append(row(
            common.STATUS_OK,
            f"{chosen} will speak the next reply",
            hearing_detail))

    def _setup_button(self) -> Gtk.Widget:
        """The setup wizard, as a banner action, for the no-engine state only."""
        button = Gtk.Button(label="Set Chronoa up")
        button.add_css_class("suggested-action")
        button.set_tooltip_text(
            "Open the setup wizard, whose Voice page installs a voice "
            "(Ctrl+Shift+S)")
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               ["Set Chronoa up: install a voice"])
        button.connect("clicked", lambda _b: common.open_setup(self._app, self))
        return button

    def _hearing_sentence(self) -> str:
        """One clause about speech input, read through `_speech_input`.

        Never raises: a panel that cannot ask about listening still has to answer
        about speaking, and the two are reported in one row.
        """
        try:
            speech = _speech_input(self._app, getattr(self._app, "config", None))
        except Exception:                               # noqa: BLE001
            return "Whether Chronoa can listen could not be determined."
        if speech.state:
            model = speech.model or "the default model"
            where = "is on disk" if speech.model_on_disk else "is not on disk"
            return f"{speech.label} is available and the model {model} {where}."
        if speech.problem:
            return f"{speech.label} cannot listen: {speech.problem}."
        return f"{speech.label} cannot listen, and this panel could not say why."

    def refresh(self) -> None:
        """Re-read everything and rebuild the rows.

        `Reload` calls this, and so does `build()`. There is no cached reading
        anywhere in this module: `engine()`, `voice_installed()` and
        `stt_problem()` are all asked again, because "right now" is a claim about
        the moment it was asked.
        """
        for group, previous in ((self._now_group, self._now_rows),
                                (self._engine_group, self._engine_rows),
                                (self._speech_group, self._speech_rows),
                                (self._catalogue_group, self._catalogue_rows)):
            # Removed by reference, never by walking the group: libadwaita keeps
            # its rows inside its own boxes, so a walk finds boxes that are not
            # its children and `remove()` refuses them with an Adw-CRITICAL.
            for row in previous:
                group.remove(row)
        self._now_rows, self._engine_rows = [], []
        self._speech_rows, self._catalogue_rows = [], []

        app = self._app
        config = getattr(app, "config", None)
        dispatcher = self._dispatcher(app, config)
        chain = _chain(dispatcher, config)
        now_title, now_detail = _speaking_now(dispatcher, chain)
        # Read once and reused by the voice rows: `engine()` is asked once per
        # refresh rather than twice, so the answer cannot disagree with itself if
        # a probe is expensive - and if it does raise, both questions get the
        # same "cannot be determined" instead of one of them guessing.
        chosen, failure = _resolve_choice(dispatcher)
        voice = _voice_in_use(dispatcher, chosen)

        self._render_status(dispatcher, chain, chosen, failure)

        self._now_title, self._now_detail = _RowText(_shown(now_title)), _RowText(_shown(now_detail))
        self._now_row = _row(now_title, now_detail,
                             f"{now_title}. Resolved through {tts.PiperTTS.__name__}.engine() on the "
                             "dispatcher every reply goes through")
        self._now_row._speaking_title = self._now_title
        self._now_row._speaking_detail = self._now_detail
        self._now_rows.append(self._now_row)
        _add(self._now_group, self._now_row)

        cost_detail = _cost(chosen)
        self._cost_row = _row("What the voice costs", cost_detail,
                              "Real-time factors recorded in tts.py, voices.py and AGENTS.md")
        self._cost_row._cost_detail = _RowText(_shown(cost_detail))
        self._now_rows.append(self._cost_row)
        _add(self._now_group, self._cost_row)

        voice_title = f"Voice in use - {voice.label}" if voice.name else "Voice in use"
        self._voice_detail_row = _row(voice_title, voice.detail,
                                      "voices.VOICES / voices.KOKORO_VOICES, through the dispatcher")
        self._voice_detail_row._voice_name = _RowText(_shown(voice.name))
        self._voice_detail_row._voice_on_disk = voice.on_disk
        self._voice_detail_row._voice_detail = _RowText(_shown(voice.detail))
        self._now_rows.append(self._voice_detail_row)
        _add(self._now_group, self._voice_detail_row)

        timbre_title, timbre_detail = _timbre_row(dispatcher)
        self._timbre_row = _row(timbre_title, timbre_detail, timbre_detail)
        self._now_rows.append(self._timbre_row)
        _add(self._now_group, self._timbre_row)

        for engine in chain:
            word = _state_word(engine.state, "usable", "not usable")
            built = _row(f"{engine.label} - {word}", engine.detail,
                         f"{engine.label}: {word}\n{engine.detail}\nRead from {engine.source}")
            built._engine_key = engine.key
            built._engine_state = engine.state
            self._engine_rows.append(built)
            _add(self._engine_group, built)

        # Two rows, because the backend and the model file are two independent
        # facts with two independent fixes: "whisper.cpp is not installed" and
        # "the model is not downloaded" send somebody to two different places,
        # and one row that merged them would send them to the wrong one.
        speech = _speech_input(app, config)
        state = _state_word(speech.state, "ready now", "not ready")
        # `WhisperSTT` falls back to `/usr/bin/whisper-cli` when nothing is on
        # PATH, so the path it carries is what it *looked for*, not a program
        # that exists. Saying "run by" it would put a confident wrong claim
        # directly above the reason saying it is not installed.
        runs = (f"run by {speech.binary}" if speech.binary and os.path.exists(speech.binary)
                else f"looked for at {speech.binary or '(no path resolved)'}, and there is no program there")
        heard = (f"model {speech.model or '(none chosen)'}, {runs}. "
                 + (speech.problem if speech.problem else "it is ready to transcribe"))
        built = _row(f"Speech input - {speech.label} - {state}", heard,
                     f"{speech.label}: {state}\n{heard}\nRead from {speech.source}")
        built._speech_kind = "engine"
        built._speech_backend = speech.backend
        built._speech_model = speech.model
        built._speech_state = speech.state
        built._speech_problem = speech.problem
        self._speech_rows.append(built)
        _add(self._speech_group, built)

        file_state = _state_word(speech.model_on_disk, "on disk", "not on disk")
        where = speech.model_path or "(nothing resolved)"
        file_detail = (f"{where}. The engine looks for this exact file, and it is there"
                       if speech.model_on_disk else
                       f"{where}. The engine looks for this exact file, and it is not there"
                       + (f" - and that is why: {speech.problem}" if speech.problem else ""))
        built = _row(f"Speech model file - {speech.model or '(none chosen)'} - {file_state}", file_detail,
                     f"Speech model file: {file_state}\n{file_detail}\nRead from {speech.source}")
        built._speech_kind = "model-file"
        built._speech_model_path = speech.model_path
        built._speech_model_on_disk = speech.model_on_disk
        self._speech_rows.append(built)
        _add(self._speech_group, built)

        for label, detail, key in _voice_catalogue(chosen):
            built = _row(label, detail, f"{label}: {detail}")
            built._voice_key = key
            self._catalogue_rows.append(built)
            _add(self._catalogue_group, built)

        counts = ", ".join(f"{e.label} {_state_word(e.state, 'usable', 'not usable')}" for e in chain)
        self._summary.set_text(_shown(
            f"Engine chain, in the order it is tried: {counts}. "
            f"Nothing on this page downloads an engine or a voice, starts a process or changes a "
            f"setting: Chronoa's setup, Voice page installs one, and Settings owns the switch and the "
            f"three timbre sliders."))

    def _dispatcher(self, app: Any, config: Any) -> "tts.PiperTTS":
        """The dispatcher this page reports on.

        `app.tts` when it is a real one - it is the object `synthesize()` runs on,
        and it already holds the settings and the speaking speed the running app
        uses. `isinstance` rather than truthiness because a stand-in app's
        attribute is often a mock, and asking a mock for an engine name would
        produce exactly the confident wrong answer this page must not produce.
        """
        live = getattr(app, "tts", None)
        if isinstance(live, tts.PiperTTS):
            return live
        voice = _read_setting(config, "piper_voice", "en_US-lessac-medium")
        return tts.PiperTTS(voice=voice, config=config)

    # -- for tests, and for any surface that wants the same reads ------------

    def speaking_row(self) -> Gtk.Widget:
        return self._now_row

    def speaking_title(self) -> str:
        return self._now_title.get_text()

    def speaking_detail(self) -> str:
        return self._now_detail.get_text()

    def cost_row(self) -> Gtk.Widget:
        return self._cost_row

    def voice_row(self) -> Gtk.Widget:
        return self._voice_detail_row

    def timbre_row(self) -> Gtk.Widget:
        return self._timbre_row

    def engine_rows(self) -> List[Gtk.Widget]:
        return list(self._engine_rows)

    def speech_rows(self) -> List[Gtk.Widget]:
        return list(self._speech_rows)

    def catalogue_rows(self) -> List[Gtk.Widget]:
        return list(self._catalogue_rows)

    def unknown_engine_keys(self) -> List[str]:
        return [row._engine_key for row in self._engine_rows if row._engine_state is None]

    def summary(self) -> str:
        return self._summary.get_text()

    def reload_button(self) -> Gtk.Button:
        """The Reload control inside the banner, for callers and tests.

        The banner is the widget that goes on screen; this is the button inside
        it. Returning the box instead would make the name a lie, and
        `test_the_reload_button_is_labelled_for_a_screen_reader` asks the tooltip
        off it.
        """
        child = self._reload_button.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                return child
            child = child.get_next_sibling()
        return self._reload_button


def _cost(chosen: Optional[str]) -> str:
    """What the voice costs, in measured real-time factors only."""
    if chosen:
        current = COST.get(chosen, COST_UNMEASURED)
        said = (f"The engine in use, {chosen}, has a real-time factor of {current}" if chosen in COST
                else f"The engine in use, {chosen}: {COST_UNMEASURED}")
    else:
        said = ("The engine in use could not be established here, so no cost is claimed for it; "
                "the two measured figures in this repository are below.")
    return (f"{said}. The neural voice, Kokoro, is the one with a real-time factor of about 1.2 on a "
            f"processor, which is why it is opt-in: above 1 means it cannot finish before playback would "
            f"start. espeak-ng is about 0.005. Piper and RHVoice: {COST_UNMEASURED}")


#: What `build()` forwards from the content onto the page it returns. A
#: libadwaita page cannot be subclassed here (that would mean importing `Adw`),
#: and a caller holding only the page still has to be able to ask it what it is
#: showing - which is the whole point of these accessors.
_FORWARDED = ("speaking_row", "speaking_title", "speaking_detail", "cost_row", "voice_row",
              "timbre_row", "engine_rows", "speech_rows", "catalogue_rows", "unknown_engine_keys",
              "summary", "reload_button")


def build(app: Any) -> Gtk.Widget:
    """Build the voice page for `app`. Reads state; writes nothing."""
    content = _VoiceSurface(app)
    page, set_content = common.surface(TITLE, SUBTITLE)
    set_content(common.scrolled(content))
    for name in _FORWARDED:
        setattr(page, name, getattr(content, name))
    # What this panel says about itself, for the sidebar's health dot, from the
    # same recorder the row at the top of the panel was written through.
    page.status = content.status_recorder.status
    return page


__all__ = ["TITLE", "ICON", "SECTION", "build"]