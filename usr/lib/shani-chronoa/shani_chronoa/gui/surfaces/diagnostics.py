"""Diagnostics: is any part of Chronoa working right now, and if not, why not.

One question per row, and it is the same question every time: *is this piece of
Chronoa working, and when it is not, what is the named reason?* This panel
exists because "it did nothing" is the least informative sentence in the
project. Every row answers it from the module that would make the thing true -
`local_llm` for the brain, `stt` and `tts` for speech, `pipewire` for the audio
path, `screengrab` and `portal` for the desktop, `search_provider.BUS_NAME` for
the bus name - and never from a copy of it.

**Three words, and a row may not use a fourth.** `working`, `not working`,
`could not determine`. A subsystem whose probe raised, whose binary was not on
`PATH`, or whose bus could not be reached is `could not determine` - never
`working`, and never a clean negative. That distinction is the whole panel: a
confident wrong answer here is worse than an admitted gap, because it sends
someone to reinstall a package that is installed. `_measure` is the only place
a status is invented for a probe that did not finish, and it can only invent
`could not determine` - see `_fallback_status`, and the test that mutates it to
`working` to prove the assertion can fail.

**A missing binary is a fact, not a failure of the probe.** `shutil.which`
answering nothing means the program is not installed; that is named as missing
("`ffmpeg` is not on PATH"), not reported as a crash and not reported as
unknown. The two are different sentences about different worlds, and this
panel says which.

**Two things here cannot be proven from outside the process, and neither is
upgraded into a claim.** The global shortcut is bound on a thread of Chronoa's
own (`app/desktop_integration.py`) inside the desktop portal, so the portal
answering says the desktop *could* serve a bind and nothing more - that is
`surfaces/desktop.py`'s standing admission, kept here in the same words. And the
search provider is D-Bus *activated*: between two searches there is
deliberately nothing on the bus, so "not on the bus" is not a fault.

**Read once, when the panel is built.** A screen capture, four HTTP health
checks, one bus listing, two bus property reads and a handful of `PATH`
lookups; no polling, no threads, no timer, and nothing written anywhere except
the temp file `screengrab` uses for the capture itself. Every subprocess
carries a bounded timeout and every failure path is a sentence. **Nothing here
raises**: a surface that raises takes the window with it, and the window is the
product.

Built from `surfaces/common.py` like every other panel, and the strings are
escaped on exactly the condition `common.row()` branches on: an
`Adw.ActionRow` parses its title and subtitle as Pango markup (measured on
libadwaita 1.5, where `use-markup` defaults to true), while the no-Adw fallback
is a `Gtk.Label`, which takes no markup and would print the entities themselves.
Every row carries a filesystem path - a model, a voice, an env file - and a
home directory is a place a user can have put a `&` in.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any, Callable, List, NamedTuple, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import markdown_lite  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Diagnostics"
ICON = "dialog-information-symbolic"
SECTION = "Desktop and system"

SUBTITLE = (
    "One question per row: is this part of Chronoa working right now, and if not, "
    "why not? Every answer is read from the module that would make the thing true. "
    "A row that could not be answered says so rather than showing a clean answer."
)

#: The closed vocabulary. Three words, and a row may not use a fourth.
STATUS_WORKING = "working"
STATUS_NOT_WORKING = "not working"
STATUS_UNKNOWN = "could not determine"
STATUS_WORDS = (STATUS_WORKING, STATUS_NOT_WORKING, STATUS_UNKNOWN)

#: Css classes on every row and on the label carrying its answer, so a test
#: finds rows by walking the built tree rather than off a list this module
#: stashed on itself. None of these is styled anywhere; they are markers, and
#: saying so is what keeps them honest.
ROW_CSS = "diagnostics-row"
VALUE_CSS = "diagnostics-value"

#: The standing admission about the global shortcut, in the same words
#: `surfaces/desktop.py` uses, because it is the same unprovable thing.
UNPROVABLE_NOTE = (
    "a portal bind cannot be proven from outside the process"
)

#: The D-Bus name the search provider owns: the same constant
#: `search_provider.py:BUS_NAME` uses, restated rather than imported so that
#: reading this panel cannot construct a provider or a bus connection.
SEARCH_PROVIDER_NAME = "dev.shani.chronoa.SearchProvider"

#: The desktop portal's own bus name and object, from the xdg-desktop-portal
#: specification. `portal.py`'s constants, for the same reason.
PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
PORTAL_SHORTCUT_IFACE = "org.freedesktop.portal.GlobalShortcuts"

#: A `busctl` that has blocked for five seconds is a window that has stopped
#: repainting. A screen capture gets eight, which is well inside
#: `screengrab.DEFAULT_TIMEOUT_SECONDS` (20) because this panel is read once by
#: a person who is waiting.
BUSCTL_TIMEOUT = 5
CAPTURE_TIMEOUT = 8

#: What a well-known or unique bus name looks like, so a line of error text that
#: reached stdout is not read as a name. Measured, not assumed (see
#: `surfaces/desktop.py` for the same table and why): dbus rejects a dotless
#: destination before the message is even built, and the NAME column is 47
#: characters wide, so this panel's 32-character provider name prints in full.
_BUS_NAME = re.compile(r"^(?::[0-9]+(?:\.[0-9]+)*"
                       r"|[A-Za-z_-][A-Za-z0-9_-]*(?:\.[A-Za-z_-][A-Za-z0-9_-]*)+)$")

#: One probe's answer: `status` is one of `STATUS_WORDS`, `detail` says the
#: evidence. A probe returns `(status, detail)`; it never returns a widget and
#: never raises on purpose.
Finding = NamedTuple("Finding", [("title", str), ("status", str), ("detail", str)])

Probe = Callable[[], Tuple[str, str]]


def _fallback_status(exc: BaseException) -> str:
    """The only status a probe that raised is allowed to become.

    A separate function, read at call time by `_measure`, so that the test that
    makes a raising probe report `working` patches the name that is actually
    used rather than a copy. It returns `STATUS_UNKNOWN` and nothing else: a
    probe that did not finish has not established that a subsystem works, and
    that is the one claim this panel must never make by accident.
    """
    logger.debug("a diagnostics probe raised", exc_info=True)
    return STATUS_UNKNOWN


def _measure(title: str, probe: Probe) -> Finding:
    """Run one probe and turn whatever happened into a `Finding`.

    This is the load-bearing function of the panel. A probe that raises is
    rendered as `could not determine` with the exception named, and there is no
    path through here from an exception to `STATUS_WORKING`. A probe that
    *returns* a word outside `STATUS_WORDS` is also demoted rather than
    rendered, so a future row cannot introduce a fourth state by accident.
    """
    try:
        status, detail = probe()
    except Exception as exc:  # noqa: BLE001 - the panel shows the failure, it does not raise it
        reason = f"{type(exc).__name__}: {exc}".strip().rstrip(":").strip() or type(exc).__name__
        return Finding(title, _fallback_status(exc), f"the probe did not finish ({reason[:180]})")
    if status not in STATUS_WORDS:
        return Finding(title, STATUS_UNKNOWN,
                       f"the probe answered with a word this panel does not use ({status!r}), "
                       "so it is not reported as an answer")
    return Finding(title, status, detail)


# -- rendering helpers -------------------------------------------------------

def _text(value: Any) -> str:
    """`value` escaped for the row shape `common.row()` is about to build.

    Branches on `common.adw_ready()` - the same condition `common.row()` itself
    branches on, not a proxy for it - because the two shapes want opposite
    treatments (see the module docstring).
    """
    return markdown_lite.escape(str(value)) if common.adw_ready() else str(value)


def _find(node: Gtk.Widget, wanted) -> Optional[Gtk.Widget]:
    """The first descendant (or `node`) that `wanted` accepts, or None.

    libadwaita builds a row's title and subtitle labels itself, inside boxes of
    its own, so this module cannot hold a reference to the subtitle one.
    """
    if wanted(node):
        return node
    child = node.get_first_child()
    while child is not None:
        found = _find(child, wanted)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _is_subtitle(node: Gtk.Widget) -> bool:
    """libadwaita gives a row's subtitle label the `subtitle` CSS class."""
    return isinstance(node, Gtk.Label) and "subtitle" in node.get_css_classes()


def _value_label(row: Gtk.Widget) -> Optional[Gtk.Label]:
    """Mark the label carrying this row's answer with `VALUE_CSS`.

    A missing marker costs nothing but a walk, so if a future libadwaita stops
    naming that label `subtitle` the panel is unaffected and the tests fail
    loudly - which is the right way round.
    """
    label = _find(row, _is_subtitle)
    if isinstance(label, Gtk.Label):
        label.add_css_class(VALUE_CSS)
    else:
        logger.debug("no subtitle label to mark on %s", type(row).__name__)
    return label if isinstance(label, Gtk.Label) else None


def _row(finding: Finding) -> Gtk.Widget:
    """One row: the subsystem's name, then its status and the evidence for it.

    Filled in at construction and never patched afterwards - `Gtk.Box` has no
    `set_subtitle`, so a row built and then given one would raise on the no-Adw
    fallback.
    """
    status = finding.status
    widget = common.row(_text(finding.title), _text(f"{status} - {finding.detail}"))
    widget.add_css_class(ROW_CSS)
    label = _value_label(widget)
    if label is not None:
        # One more CSS class per state, so a person (or a test) can see the
        # three-way split at a glance rather than re-reading sixteen sentences.
        label.add_css_class(f"{ROW_CSS}-{_slug(status)}")
    return widget


def _slug(text: str) -> str:
    return re.sub(r"[^a-z]+", "-", text.lower()).strip("-")


def _add_row(container: Gtk.Widget, row: Gtk.Widget) -> None:
    """Put `row` into a group built by `common.group()`.

    `Adw.PreferencesGroup` is a `Gtk.ListBox` and takes `add`; the plain
    `Gtk.Box` the no-Adw path returns takes `append` on GTK4, where `add` no
    longer exists (measured).
    """
    adder = getattr(container, "add", None)
    if callable(adder):
        adder(row)
    else:
        container.append(row)


# -- the session bus ---------------------------------------------------------

def _bus_names() -> Tuple[Optional[List[str]], str]:
    """Every name on this user's session bus, or `(None, why)` if unanswerable.

    One call, read once for the whole panel. Read-only by construction: the
    only argv built here is `--user --list --no-legend`, which asks the bus what
    is on it and changes nothing.

    An empty or unparseable stdout is **not** an empty bus. Measured on this
    machine, a `DBUS_SESSION_BUS_ADDRESS` naming a socket that does not exist
    gives exit 1, empty stdout and `Failed to connect to bus: No such file or
    directory` - so returning `[]` there would report an unreachable bus as a
    bus with nothing on it, which is a confident wrong answer rather than a
    missing one.
    """
    if shutil.which("busctl") is None:
        return None, "busctl is not installed on this machine"
    try:
        proc = subprocess.run(
            ["busctl", "--user", "--list", "--no-legend"],
            capture_output=True, text=True, timeout=BUSCTL_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"busctl did not answer within {BUSCTL_TIMEOUT}s"
    except OSError as exc:
        return None, f"busctl could not be run ({exc})"
    stdout = proc.stdout or ""
    if proc.returncode != 0 or not stdout.strip():
        noise = ((proc.stderr or "").strip().splitlines()
                 or [f"busctl exited {proc.returncode}"])[0]
        return None, noise[:160]
    names = []
    for line in stdout.splitlines():
        first = line.split()
        if first and _BUS_NAME.match(first[0]):
            names.append(first[0])
    if not names:
        return None, "busctl printed no bus name this panel could read"
    return names, ""


def _busctl(*args: str) -> Tuple[Optional[int], str]:
    """`busctl --user <args>`'s exit status and output, or `(None, why)`.

    Read-only: the only verb used anywhere in this panel is `get-property`,
    which D-Bus's own rules make a read. `get` rather than `Get` - `busctl`
    takes the lowercase long option, and the uppercase spelling is accepted by
    neither version, which would make every portal row say "could not
    determine" for a reason that has nothing to do with the portal.
    """
    if shutil.which("busctl") is None:
        return None, "busctl is not installed on this machine"
    argv = ["busctl", "--user", *args]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=BUSCTL_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return None, f"busctl did not answer within {BUSCTL_TIMEOUT}s"
    except OSError as exc:
        return None, f"busctl could not be run ({exc})"
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip().splitlines()
        detail = stderr[-1] if stderr else f"busctl exited {proc.returncode}"
        return None, detail[:160]
    return proc.returncode, proc.stdout or ""


def _portal_version(iface: str) -> Tuple[Optional[int], str]:
    """The version the desktop portal publishes for `iface`, or `(None, why)`.

    Read through the portal's *own* interface rather than through
    `org.freedesktop.DBus.Properties`, which is a trap worth naming: measured on
    this machine's xdg-desktop-portal, `Get` on
    `/org/freedesktop/portal/desktop` + `org.freedesktop.DBus.Properties` fails
    with `No such interface "org.freedesktop.DBus.Properties"` for interfaces
    the portal plainly does have. Each portal interface carries its own
    `version` property, so that is what is asked for. `--json=short` makes the
    answer machine-readable rather than a string to guess at.
    """
    code, out = _busctl("--json=short", "get-property", PORTAL_BUS, PORTAL_PATH, iface, "version")
    if code is None:
        return None, out
    if code != 0:
        return None, out.strip()[:160] or f"busctl exited {code}"
    try:
        import json

        version = json.loads(out or "null").get("data")
    except (json.JSONDecodeError, AttributeError, ValueError):
        return None, "busctl printed something that is not JSON"
    if not isinstance(version, int):
        return None, f"the portal's version property read back as {version!r}"
    return version, ""


# -- probes: the brain -------------------------------------------------------

def _brain() -> Tuple[str, str]:
    """llama.cpp's server, the model it would load, and whether it answers."""
    from shani_chronoa import local_llm

    port = local_llm.PORT
    binary = local_llm.server_binary()
    if not binary:
        return STATUS_NOT_WORKING, (
            "llama-server is not on PATH, so there is no local model server on "
            f"127.0.0.1:{port} at all (the llama-cpp package provides it; "
            "setup downloads the model separately)"
        )
    model_dir = local_llm.model_dir()
    key = local_llm.active()
    if not key:
        on_disk = local_llm.installed()
        if on_disk:
            return STATUS_NOT_WORKING, (
                f"llama-server is at {binary} and {len(on_disk)} model file(s) are in "
                f"{model_dir}, but none of them is the one current.gguf points at, so "
                "the unit has no model to load"
            )
        return STATUS_NOT_WORKING, (
            f"llama-server is at {binary}, but no model file is in {model_dir}; "
            "setup downloads one and links it as current.gguf"
        )
    spec = local_llm.SPECS[key]
    if not local_llm.is_up():
        return STATUS_NOT_WORKING, (
            f"llama-server is at {binary} with {key} ({spec.filename}) selected, but "
            f"http://127.0.0.1:{port}/health did not answer, so nothing is serving "
            f"replies - the user unit {local_llm.UNIT} is what to look at"
        )
    return STATUS_WORKING, (
        f"llama-server is at {binary}, serving {key} ({spec.filename}) on "
        f"127.0.0.1:{port}, whose /health answered"
    )


# -- probes: speech ----------------------------------------------------------

def _speech_in() -> Tuple[str, str]:
    """Whatever can transcribe here: whisper.cpp, or a cloud provider, or neither.

    Both halves of the local answer, because one half is not enough and reporting
    either alone would be a plausible-looking wrong answer: the binary without a
    model transcribes nothing, and a model without the binary is never loaded.
    The verdict is `WhisperSTT.is_available()` - the module's own answer - and the
    two paths are reported so the missing half can be named.

    **And the cloud engine, because this probe built a `WhisperSTT` directly and
    therefore could not see one.** `cloud_voice.CloudSTT` is a legitimate engine
    now, so on a machine with `cloud-stt-enabled` on, a key configured and no
    local model, this used to report
    *"neither the whisper.cpp binary nor a model file is on this machine, so
    speech input is off whatever the settings say"* - a warning, on a machine
    that was transcribing perfectly well. The row is even labelled
    "Speech in (whisper.cpp)", so the alarm was at least honestly titled; it was
    still an alarm about nothing.

    Probes here are `Callable[[], Tuple[str, str]]` - zero-argument, no app - so
    this asks the two engines directly rather than reading `app.stt`. That is the
    same availability question either way, and it keeps the probe usable from a
    script with no application.
    """
    from shani_chronoa import stt

    cloud_note = _cloud_speech_in()
    engine = stt.WhisperSTT(model=stt.installed_model("base"))
    has_binary = os.path.exists(engine.whisper_path)
    has_model = os.path.exists(engine.model_path)
    model = engine.model
    if engine.is_available():
        return STATUS_WORKING, (
            f"whisper.cpp is at {engine.whisper_path} with the {model} model at "
            f"{engine.model_path}, so transcribing is possible"
        )
    if not has_binary and not has_model:
        local = (f"neither the whisper.cpp binary ({engine.whisper_path}) nor a "
                 f"model file ({engine.model_path}) is on this machine, so local "
                 "speech input is off whatever the settings say; setup's Ears page "
                 "installs both")
    elif not has_binary:
        local = (f"the {model} model is on disk at {engine.model_path} but the "
                 f"whisper.cpp binary is not: {engine.whisper_path} does not exist, "
                 "so nothing can run it")
    else:
        local = (f"whisper.cpp is at {engine.whisper_path} but no model file is on "
                 f"disk at {engine.model_path} (tried the {model} names, then the "
                 "smaller quantised ones)")
    if cloud_note:
        return STATUS_WORKING, f"{cloud_note} Locally: {local}"
    return STATUS_NOT_WORKING, local


def _cloud_speech_in() -> str:
    """Why a cloud provider is transcribing here, or '' when it is not.

    Read fresh and duck-typed, because this module is a diagnostic: it must not
    become the thing that decides. Any exception is '' - "not cloud" - so a broken
    probe can only ever under-report, never invent a working engine.
    """
    try:
        from shani_chronoa import cloud_voice
        engine = cloud_voice.CloudSTT()
        if not engine.is_available():
            return ""
        return ("a cloud provider transcribes instead: recordings are uploaded, "
                "so there is nothing to install here")
    except Exception:  # noqa: BLE001 - a diagnostic must not raise
        return ""


def _speech_out() -> Tuple[str, str]:
    """Which engine `tts.py`'s own chain would speak with here, and why not another.

    `PiperTTS.engine()` with no argument is the right question, not
    `engine("some text")`: the first asks what *could* speak on this machine,
    which is a fact, while the second also asks about the language of a reply
    that does not exist yet. The reason Kokoro was skipped is in
    `_kokoro_reason()`, and the chain below it is the four-way cascade the
    module documents - so the row names the engine that won rather than
    re-deriving the cascade here.
    """
    from shani_chronoa.tts import PiperTTS

    tts = PiperTTS()
    chosen = tts.engine()
    reason = tts._kokoro_reason("")
    kokoro = ""
    if reason:
        kokoro = f"; Kokoro was not used because {reason}"
    # **The chain is read, not retyped.** This said "four-way" in a hardcoded
    # string and listed four engines by hand. `gui/surfaces/voice.py` grew a
    # fifth link (cloud) and this row went on calling the cascade four-way while
    # resolving it through the five-link chain - so the count was wrong on a box
    # where nothing had changed. Worse, when nothing could speak the message
    # listed only the four it knew about, so on a machine relying on the cloud
    # the reason it was not working - no provider with a speech route - was
    # invisible. Measured: the row read "speech out would use cloud, resolved
    # from PiperTTS.engine()'s own four-way chain".
    #
    # `CHAIN` is the one place the order is written down, and a count that can
    # go stale on the same day a feature lands is not worth writing twice.
    chain = _tts_chain()
    shape = f"{len(chain)}-way" if chain else "unknown-shape"
    pretty = ", ".join(_prettify_engine(e) for e in chain) or "no engines known"
    if chosen is None:
        return STATUS_NOT_WORKING, (
            f"no engine in the tts.py chain can speak here: {pretty} were all "
            f"unavailable{kokoro}"
        )
    if chosen == "cloud":
        # **The disclosure belongs here, and this row is where somebody looks.**
        # `tts.PiperTTS._announce` puts it in the log and the Speech-in row puts
        # it on the panel; this row used to say "would use cloud" and stop,
        # which is the least useful sentence in a panel whose job is "what is
        # wrong here".
        return STATUS_WORKING, (
            f"speech out would use a cloud provider, resolved from "
            f"PiperTTS.engine()'s own {shape} chain - the reply text is sent to "
            f"that provider to be turned into audio{kokoro}"
        )
    return STATUS_WORKING, (
        f"speech out would use {chosen}, resolved from PiperTTS.engine()'s own "
        f"{shape} chain{kokoro}"
    )


def _tts_chain() -> Tuple[str, ...]:
    """The TTS cascade as one list, read from wherever it is actually written.

    Falls back to empty rather than raising: this is a diagnostic, and a row
    that says "no engines known" is honest where an `AttributeError` is not.
    """
    try:
        from shani_chronoa.gui.surfaces.voice import CHAIN
        return tuple(str(e) for e in CHAIN)
    except Exception:  # noqa: BLE001 - a diagnostic must not raise
        logger.debug("cannot read the tts cascade", exc_info=True)
        return ()


def _prettify_engine(key: str) -> str:
    """`espeak-ng` and `rhvoice` as a person would write them in a sentence."""
    return {"kokoro": "Kokoro", "piper": "Piper", "rhvoice": "RHVoice",
            "espeak-ng": "espeak-ng", "cloud": "the cloud provider"}.get(key, key)


# -- probes: the microphone and the audio path -------------------------------

def _microphone() -> Tuple[str, str]:
    """The microphones `pipewire` can see, i.e. the nodes `pw-record` can target."""
    from shani_chronoa import pipewire

    if shutil.which("pw-dump") is None:
        return STATUS_NOT_WORKING, (
            "pw-dump is not on PATH, so the live PipeWire graph cannot be read at all "
            "and no microphone can be listed or recorded from"
        )
    inputs = pipewire.list_inputs()
    if not inputs:
        return STATUS_NOT_WORKING, (
            "pw-dump answered but reported no Audio/Source node that is not a virtual "
            "one, so there is no microphone to record from on this machine"
        )
    shown = ", ".join(device.label for device in inputs[:3])
    more = f", and {len(inputs) - 3} more" if len(inputs) > 3 else ""
    return STATUS_WORKING, (
        f"{len(inputs)} microphone(s) on the live graph: {shown}{more}"
    )


def _audio_path() -> Tuple[str, str]:
    """Whether the audio path a reply is played through is up at all.

    Two things and neither is enough: `pipewire.is_available()` for a readable
    graph, and `wpctl status` answering with a WirePlumber line for the control
    side that `volume`, `set_mic_mute` and `AudioPlayer` all go through. A
    `wpctl` that exits non-zero is named as that, and never turned into "no
    devices" - the same reasoning as `pipewire.run_wpctl()`'s own docstring.
    """
    from shani_chronoa import pipewire

    if not pipewire.is_available():
        if shutil.which("pw-dump") is None:
            return STATUS_NOT_WORKING, ("pw-dump is not on PATH, so there is no audio "
                                        "graph to read")
        return STATUS_NOT_WORKING, (
            "pw-dump is installed but yielded no node, so there is no live PipeWire "
            "graph here - an audio session that is not running, not a broken Chronoa"
        )
    if shutil.which("wpctl") is None:
        return STATUS_NOT_WORKING, (
            "the PipeWire graph is live and readable, but wpctl is not installed, so "
            "volume, mute and playback have no control-side tool to use"
        )
    proc = pipewire.run_wpctl("status")
    if proc.returncode != 0:
        noise = (proc.stderr or "").strip().splitlines()
        return STATUS_NOT_WORKING, (
            "the PipeWire graph is live, but wpctl status did not answer "
            f"({(noise[-1] if noise else f'exit status {proc.returncode}')[:120]})"
        )
    graph = _graph_name(proc.stdout)
    if "WirePlumber" not in proc.stdout:
        return STATUS_NOT_WORKING, (
            f"wpctl status answered about {graph} but named no WirePlumber, so the "
            "control side of the audio session is not the one the volume and mute "
            "skills talk to"
        )
    return STATUS_WORKING, (
        f"{graph} is live and WirePlumber is answering, so wpctl can reach the volume "
        "and mute controls the audio path uses"
    )


def _graph_name(status_output: str) -> str:
    """The server name from `wpctl status`'s first line, e.g. `pipewire-0`.

    That line also carries the version, the user@host and a cookie, and it runs
    past 80 characters on an ordinary hostname - so the first draft of this row
    truncated mid-bracket and printed a fragment that reads like damage.
    """
    first = (status_output or "").splitlines()[:1]
    found = re.search(r"PipeWire\s+'([^']+)'", first[0] if first else "")
    return f"PipeWire {found.group(1)}" if found else "the audio graph"


# -- probes: screen and the desktop -----------------------------------------

def _screen_capture() -> Tuple[str, str]:
    """Whether a real capture of the screen on this session works.

    A real one, not a check of the environment: `screengrab.display_environment()`
    reads `WAYLAND_DISPLAY`/`DISPLAY` and would happily say "wayland" on a
    machine where the capture itself then fails. The one question this panel
    asks is whether a screenshot comes out, so it takes one - bounded, scaled
    down, held in memory, written nowhere. `ScreenCaptureError` carries a
    sentence for every failure that is not a bug, and that sentence is the
    named cause this row exists to show.
    """
    from shani_chronoa import screengrab

    environment = screengrab.display_environment()
    if environment == "none":
        return STATUS_NOT_WORKING, (
            "there is no display to capture: neither WAYLAND_DISPLAY nor DISPLAY is "
            "set, so this is a headless session"
        )
    try:
        capture = screengrab.capture_screen(timeout=CAPTURE_TIMEOUT)
    except screengrab.ScreenCaptureError as exc:
        return STATUS_NOT_WORKING, f"the capture failed: {exc}"
    return STATUS_WORKING, (
        f"a real capture of this {environment} session succeeded "
        f"({capture.describe().split('(')[-1].rstrip(')')})"
    )


def _screenshot_portal() -> Tuple[str, str]:
    """Whether the desktop portal publishes its Screenshot interface here."""
    version, problem = _portal_version(PORTAL_SCREENSHOT_IFACE)
    if problem:
        return STATUS_UNKNOWN, (
            f"the portal's {PORTAL_SCREENSHOT_IFACE} version could not be read "
            f"({problem}), which says nothing about whether a capture works"
        )
    return STATUS_WORKING, (
        f"{PORTAL_BUS} publishes {PORTAL_SCREENSHOT_IFACE} version {version}"
    )


def _shortcut_portal() -> Tuple[str, str]:
    """Whether the desktop portal publishes its GlobalShortcuts interface here.

    That is the whole of what is knowable: the bind itself happens on a thread
    of Chronoa's own, inside the portal, so a portal that answers here has not
    bound anything. An older portal simply does not publish this interface, and
    that is a fact about the machine, not a fault in Chronoa.
    """
    version, problem = _portal_version(PORTAL_SHORTCUT_IFACE)
    if problem:
        return STATUS_NOT_WORKING, (
            f"{PORTAL_BUS} does not publish {PORTAL_SHORTCUT_IFACE} here "
            f"({problem}), so the push-to-talk shortcut cannot be bound through the "
            f"portal at all; {UNPROVABLE_NOTE}, so this row says nothing about a "
            "shortcut that is already bound"
        )
    return STATUS_UNKNOWN, (
        f"{PORTAL_BUS} publishes {PORTAL_SHORTCUT_IFACE} version {version}, so the "
        f"desktop could serve a bind, and {UNPROVABLE_NOTE}: only Chronoa's own "
        "thread can say whether the desktop accepted one"
    )


def _search_provider() -> Tuple[str, str]:
    """Whether anything is answering as the desktop search provider right now."""
    names, problem = _bus_names()
    if names is None:
        return STATUS_UNKNOWN, (
            f"the session bus could not be listed ({problem}), so whether "
            f"{SEARCH_PROVIDER_NAME} is answering is not established either way"
        )
    if SEARCH_PROVIDER_NAME in names:
        return STATUS_WORKING, (
            f"{SEARCH_PROVIDER_NAME} is on the session bus right now, so a search typed "
            "in the desktop's overview or KRunner reaches Chronoa"
        )
    return STATUS_UNKNOWN, (
        f"{SEARCH_PROVIDER_NAME} is not on the session bus at the moment ({len(names)} "
        "other names are). That is not a fault: the provider is D-Bus activated and "
        "exits after two idle minutes, so between two searches there is deliberately "
        "nothing on the bus"
    )


# -- probes: the optional extras that gate whole features --------------------

def _binary_extra(binary: str, purpose: str) -> Probe:
    """A probe for an extra that is a program on `PATH`.

    Missing is named as missing. `shutil.which` returning nothing is a fact
    about the install, and it is a different sentence from "the probe failed" -
    conflating them is how a panel ends up saying "could not determine" about
    something it actually knows perfectly well.
    """
    def probe() -> Tuple[str, str]:
        found = shutil.which(binary)
        if not found:
            return STATUS_NOT_WORKING, (
                f"{binary} is not on PATH, so {purpose} cannot work on this machine"
            )
        return STATUS_WORKING, f"{binary} is at {found}, so {purpose} can run"

    return probe


def _math_extra() -> Tuple[str, str]:
    """Whether the two engines `solve_math` needs are both usable.

    **This probed `sympy`.** `skills/solve_math.py` moved to `symengine` (exact
    algebra) plus `bc` (the exact rational arithmetic symengine's own `solve`
    segfaults on), so this panel had been describing a module that no longer
    existed while the maths row on the setup wizard said nothing at all.

    It asks the skill module rather than `find_spec`, because the skill is what
    actually refuses: `se = None` is the exact flag `_run()` checks, and `bc` is
    probed by the same `which("bc")` the bc engine uses. A module that is
    importable but unusable is reported as unusable, because that is what the
    caller experiences.
    """
    try:
        from shani_chronoa.skills import solve_math
    except Exception as exc:  # noqa: BLE001 - an unimportable skill is not working
        return STATUS_NOT_WORKING, (
            f"skills/solve_math.py cannot be imported ({type(exc).__name__}: {exc}), "
            "so no maths question can be answered")
    symbolic = getattr(solve_math, "se", None) is not None
    arithmetic = bool(getattr(solve_math, "_bc_available", lambda: False)())
    if symbolic and arithmetic:
        return STATUS_WORKING, (
            "symengine and bc are both usable, so skills/solve_math.py can do the "
            "exact algebra and the exact arithmetic")
    if symbolic:
        return STATUS_NOT_WORKING, (
            "bc is missing, so every integral, limit, sum and numeric evaluation "
            "refuses; the exact algebra still works (install bc)")
    if arithmetic:
        return STATUS_NOT_WORKING, (
            "symengine is missing, so every symbolic question refuses; numeric "
            "evaluation still works (install python-symengine)")
    return STATUS_NOT_WORKING, (
        "neither python-symengine nor bc is installed, so skills/solve_math.py "
        "refuses every maths question")


def _atspi_extra() -> Tuple[str, str]:
    """Whether the AT-SPI typelib is there, which is the whole gate on `ui_elements`.

    The import is `skills/ui_elements.py`'s own `_atspi()`, so this asks the
    module the gate is in rather than a second opinion about it. That helper
    only imports; it never calls `Atspi.init()` and never touches the
    accessibility bus, which is what keeps this panel read-only and keeps a test
    from reaching the developer's own session.
    """
    from shani_chronoa.skills import ui_elements

    ui_elements._atspi()
    return STATUS_WORKING, (
        "the AT-SPI typelib imports, so skills/ui_elements.py can read another app's "
        "controls through the accessibility bus"
    )


# -- probes: the model servers setup starts ----------------------------------

def _model_server(instance: str) -> Probe:
    """One `shani-chronoa-model@<instance>.service` server: running, and if not, why.

    The port comes from `model_service.PORTS` rather than from a literal here, so
    a port that moves cannot leave this row naming the old one. "Set up" is
    `read_env()`, which is the same env file the unit reads; a server with no
    env file was never configured, and that is a different answer from one that
    is configured and not answering.
    """
    def probe() -> Tuple[str, str]:
        from shani_chronoa import model_service

        port = model_service.PORTS[instance]
        env = model_service.env_file(instance)
        binary, args = model_service.read_env(instance)
        unit = model_service.unit(instance)
        if not binary:
            setup = (f"{env} is absent, so setup has never configured this server and "
                     f"{unit} has no program to run")
        else:
            setup = f"{env} names {binary} with {len(args)} argument(s)"
        if model_service.is_up(instance):
            return STATUS_WORKING, (
                f"{unit} is answering on 127.0.0.1:{port} (its /health returned 200); "
                f"{setup}"
            )
        return STATUS_NOT_WORKING, (
            f"nothing is answering on 127.0.0.1:{port}, so {unit} is not serving; "
            f"{setup}"
        )

    return probe


# -- the rows, in the order they are shown -----------------------------------

#: `(section title, section description, [(row title, probe)])`. The order is
#: the order a person asks in - what it thinks, what it hears, what it hears
#: you through, what it can see of your desktop, what you did not install, and
#: finally the extras that were set up.
_SECTIONS: Tuple[Tuple[str, str, Tuple[Tuple[str, Probe], ...]], ...] = (
    (
        "The brain and speech",
        "The model a reply comes from, and the two halves of talking to it.",
        (
            ("Local model server (llama.cpp)", _brain),
            ("Speech in (whisper.cpp)", _speech_in),
            ("Speech out (Piper / Kokoro / RHVoice / espeak-ng)", _speech_out),
        ),
    ),
    (
        "Microphone and audio",
        "What Chronoa would record from, and what it would play a reply through.",
        (
            ("Microphone", _microphone),
            ("Audio path (PipeWire / WirePlumber)", _audio_path),
        ),
    ),
    (
        "Screen and desktop",
        "What it can see of the desktop, and the desktop interfaces it asks.",
        (
            ("Screen capture", _screen_capture),
            ("Screenshot portal", _screenshot_portal),
            ("Global shortcut portal", _shortcut_portal),
            (f"Search provider ({SEARCH_PROVIDER_NAME})", _search_provider),
        ),
    ),
    (
        "Optional extras",
        "Each of these is missing on a fresh install and gates a whole feature "
        "behind it, so their absence is a reason and not a fault.",
        (
            ("solve_math (symengine + bc)", _math_extra),
            ("ffmpeg (video, audio, subtitles)", _binary_extra(
                "ffmpeg", "video keyframes, subtitles and the audio clean-up filters")),
            ("ImageMagick (edit_image, upscale)", _binary_extra(
                "magick", "editing and upscaling pictures")),
            ("poppler (read_document, page scans)", _binary_extra(
                "pdftotext", "reading a PDF's text aloud")),
            ("at-spi (ui_elements)", _atspi_extra),
        ),
    ),
    (
        "Model servers",
        "The loopback servers setup starts. Each may be asleep, which is a "
        "normal state for all three.",
        (
            ("vision server (port 8767)", _model_server("vision")),
            ("embed server (port 8768)", _model_server("embed")),
            ("imagine server (port 8769)", _model_server("imagine")),
        ),
    ),
)

#: Every row title, in order. Exposed so a caller (and this module's own tests)
#: can assert a row exists for each subsystem without walking a widget tree.
ROW_TITLES: Tuple[str, ...] = tuple(
    title for _heading, _description, rows in _SECTIONS for title, _probe in rows
)

_FOOTER = (
    "Every row above was read once, when this panel was built; reopen it to ask "
    "again. Nothing here is started, stopped, installed or bound - the panel asks "
    "the machine what is already true and changes none of it. The one thing it "
    "does do is take a single screen capture, into memory, to answer the screen "
    "capture row honestly."
)


def _status_row(recorder: "common.StatusRecorder",
                findings: List[Finding]) -> Gtk.Widget:
    """The panel's own health, from the findings it just measured.

    Written through `recorder` so the sidebar's dot and this row are one
    statement about the same list - the findings are walked once here, and
    counting them a second time for the dot is exactly how the two would drift.
    """
    working = sum(1 for f in findings if f.status == STATUS_WORKING)
    unknown = sum(1 for f in findings if f.status == STATUS_UNKNOWN)
    broken = sum(1 for f in findings if f.status == STATUS_NOT_WORKING)
    total = len(findings)
    if broken:
        return recorder.row(
            common.STATUS_ATTENTION,
            f"{broken} of {total} not working",
            f"{working} working, {broken} not working, {unknown} "
            "could not be determined")
    if unknown and not working:
        return recorder.row(
            common.STATUS_UNKNOWN,
            f"None of {total} could be determined",
            f"{unknown} could not be determined, {working} working")
    if unknown:
        return recorder.row(
            common.STATUS_ATTENTION,
            f"{unknown} of {total} could not be determined",
            f"{working} working, {unknown} could not be determined, "
            f"{broken} not working")
    return recorder.row(
        common.STATUS_OK,
        f"All {total} working",
        f"{working} working, {broken} not working, {unknown} "
        "could not be determined")


def _summary(finding_statuses: List[str]) -> Gtk.Widget:
    """The headline: how many subsystems are working, and how many are unknown.

    A banner rather than a footer because this is the sentence someone opens the
    panel for. `could not determine` is counted separately from `not working` on
    purpose: the first means the panel was not able to ask, which is a fact
    about this panel, and lumping it in with a missing dependency would hide the
    probes that need a human to look at them.
    """
    working = finding_statuses.count(STATUS_WORKING)
    unknown = finding_statuses.count(STATUS_UNKNOWN)
    broken = finding_statuses.count(STATUS_NOT_WORKING)
    total = len(finding_statuses)
    parts = [f"{working} of {total} subsystems working", f"{broken} not working"]
    if unknown:
        parts.append(f"{unknown} could not be determined")
    return common.banner(
        _text(" - ".join(parts) + ". The rows below say which, and why the rest are not."),
    )


def build(app: Any = None) -> Gtk.Widget:
    """The diagnostics panel. `app` is accepted and never used.

    Nothing here reads the app's settings on purpose: this panel answers what
    the *machine* can do, and a gate that is off is a different question
    ("should Chronoa use this?") that `surfaces/privacy.py` and the Settings
    window already answer. An app argument is still taken, because the registry
    calls `build(app)` for every surface and a panel that broke on the shape of
    the argument would take the sidebar with it.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    body = common.page_body(18)
    body.set_margin_top(12)
    body.set_margin_bottom(12)

    findings: List[Finding] = []
    for heading, description, rows in _SECTIONS:
        group = common.group(heading, description)
        for title, probe in rows:
            finding = _measure(title, probe)
            findings.append(finding)
            _add_row(group, _row(finding))
        body.append(group)

    # The panel's own health, above the summary: how
    # many probes worked, how many did not, how many
    # could not be told. One row, one dot, one word -
    # the question the panel is opened for, before the
    # rows that hold the findings.
    recorder = common.StatusRecorder()
    body.append(_status_row(recorder, findings))

    body.append(_summary([finding.status for finding in findings]))

    stamp = Gtk.Label(xalign=0.0, wrap=True)
    stamp.add_css_class("dim-label")
    stamp.set_text(_text(
        f"Read once at {time.strftime('%H:%M:%S', time.localtime())}."
    ))
    body.append(stamp)

    footer = Gtk.Label(xalign=0.0, wrap=True)
    footer.add_css_class("dim-label")
    footer.set_text(_text(_FOOTER))
    body.append(footer)

    set_content(common.scrolled(body))
    # What this panel says about itself, for the sidebar's health dot. The same
    # recorder that built the row at the top of the panel, so the dot and the
    # row are one statement about one reading.
    page.status = recorder.status
    return page


__all__ = [
    "TITLE", "ICON", "SECTION", "build",
    "ROW_CSS", "VALUE_CSS", "ROW_TITLES", "STATUS_WORDS",
    "STATUS_WORKING", "STATUS_NOT_WORKING", "STATUS_UNKNOWN",
    "Finding", "UNPROVABLE_NOTE", "SEARCH_PROVIDER_NAME", "PORTAL_BUS",
    "PORTAL_PATH", "PORTAL_SCREENSHOT_IFACE", "PORTAL_SHORTCUT_IFACE",
    "BUSCTL_TIMEOUT", "CAPTURE_TIMEOUT",
]