"""First-run setup: make Chronoa able to think, hear and speak on this computer.

A fresh ShaniOS install has none of the three: no Ollama, no speech model, and
espeak-ng for a voice (the CLI matrix, both images). This window gets each one
working, in plain language, with sizes stated before anything downloads and
nothing fetched until the person presses the button - which is also how the
`model-download-enabled` consent is given. Harvested from sayri's `wizard.py`
(detect what is already there, offer the rest, download resumably), built on
Chronoa's own verified downloads:

- **Brain** - `local_llm`: llama.cpp's `llama-server` - fetched by `engines` when
  this machine has none, so it is a download here rather than a package
  dependency - plus one pinned GGUF model chosen by RAM, and by GPU when
  `ggml-vulkan` sees one. Ollama, if present, is used instead and nothing is
  downloaded.
- **Ears** - `stt_provision`: a pinned whisper.cpp model for `whisper-cli`.
- **Voice** - `voices`: Piper's release build and a pinned female voice, with
  a "Listen" button so the choice is heard, not read; and, the same way, the
  Kokoro neural voice (sherpa-onnx's release build and its Kokoro model).

Then a **More** page of optional extras, each with its own button, none
needed for setup to count as done:

- **Eyes** - `local_vision`: a llama.cpp vision model and its projector, by
  RAM (and GPU). Reading text (tesseract) already ships with ShaniOS.
- **Imagine** - `imagegen`: stable-diffusion.cpp's release build (the Vulkan
  one with a GPU) and SD-Turbo.
- **Memory** - `local_embed`: a small embedding model, so conversations are
  searched by meaning as well as by their words.
- **Languages** - `languages`: OCR data, a Piper voice where one exists, and
  whisper listening for the language.

Every extra runs as its own loopback server (`model_service`), started here.

The logic is the module-level functions (state, steps); the window only shows
them, so the steps are tested without a display and the window with one.
"""

from __future__ import annotations

import logging
import shutil
import threading
from typing import Callable, Dict, List, Optional, Tuple

from shani_chronoa.config import (  # noqa: E402  (the factory above needs the real class)
    API_KEY_SETTINGS,
    ChronoaConfig,
)

logger = logging.getLogger(__name__)


class Cancelled(Exception):
    """The person pressed Cancel; the partial download has been removed."""


# --------------------------------------------------------------------------
# State and steps (no GTK)
# --------------------------------------------------------------------------


def _cloud_ready(config) -> dict:
    """What a cloud provider can offer right now, and whether it is allowed.

    Built, never called: constructing a `CloudLLMChain` resolves keys and skips
    providers that cannot work, which is the answer without touching the
    network. `allowed` is the two gates `app/brain.py` enforces - privacy off
    *and* cloud-fallback-enabled - reported separately so this page can say *why*
    nothing is available rather than just offering a download.
    """
    from shani_chronoa.distill import _cloud_allowed
    allowed, why = _cloud_allowed(config)
    out = {"allowed": allowed, "why": why, "available": False,
           "provider": "", "model": ""}
    if not allowed:
        return out
    try:
        from shani_chronoa.cloud_llm import CloudLLMChain
        from shani_chronoa.redaction import redactor
        for provider_id, key_value in config.cloud_llm_api_keys().items():
            if key_value:
                redactor.register(f"cloud_llm_{provider_id}", key_value)
        chain = CloudLLMChain()
        out["available"] = chain.is_available()
        if out["available"]:
            out["provider"] = chain._backends[0].provider.name
            out["model"] = chain.model
    except Exception as exc:  # noqa: BLE001 - no provider is a normal answer
        logger.debug("cloud availability: %s", exc)
    return out


def state(config=None) -> dict:
    """What this computer has now, for each of the three - read fresh every time."""
    from shani_chronoa import engines, local_llm, stt_provision, voices
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.ollama_llm import OllamaLLM
    config = config or ChronoaConfig()
    ollama = False
    try:
        ollama = OllamaLLM(host=config.ollama_host, model=config.model or "x").is_available()
    except Exception:  # noqa: BLE001 - "is it there" must never crash setup
        ollama = False
    # `whisper.cpp` was a program named two ways here, and neither spelling
    # matched anything: `whisper.cpp` is not a program (`stt.py` names the
    # package `whisper-cpp`, whose binary is `whisper-cli`, and
    # `model_status.py` had a third spelling). `engines.has` is the one answer,
    # and it also sees a copy downloaded into this user's own directory - which
    # the `which()` pair could not, so setup reported no speech program on a
    # machine that had just fetched one.
    whisper = engines.has("whisper-cli")
    stt_model = any(stt_provision.is_provisioned(k) for k in stt_provision.MODELS)
    voice = config.piper_voice
    from shani_chronoa import imagegen, languages, local_embed, local_vision
    gpu = local_llm.gpu_devices()
    # **A cloud provider counts as a brain.** `needs_setup()` asks whether
    # Chronoa can think, and with only a cloud key configured - which is the whole
    # point of the cloud branch, where nothing is downloaded - the answer was
    # still "no", because the check only looked for a local model. Measured
    # consequence: the wizard reopened itself on every launch for someone who had
    # deliberately chosen to download nothing.
    cloud = _cloud_ready(config)
    return {
        "brain": {
            "ollama": ollama,
            "cloud": cloud,
            "server": bool(local_llm.server_binary()),
            "up": local_llm.is_up(),
            "installed": local_llm.installed(),
            "active": local_llm.active(),
            "recommended": local_llm.recommended_for_machine() if local_llm.server_binary() else local_llm.recommended(),
            "gpu": gpu,
            "ram_gb": round(local_llm.ram_gb(), 1),
            "ready": ollama or local_llm.is_up() or bool(cloud.get("available")),
        },
        "ears": {"binary": whisper, "model": stt_model, "ready": whisper and stt_model,
                 "recommended": stt_provision.DEFAULT_MODEL},
        "voice": {"piper": bool(voices.piper_binary()), "voice": voice if voices.voice_installed(voice) else "",
                  "ready": (bool(voices.piper_binary()) and voices.voice_installed(voice))
                           or (config.get_bool("kokoro-tts-enabled", False) and voices.kokoro_installed(
                               config.get("kokoro-voice", voices.KOKORO_DEFAULT_VOICE))),
                  "chosen": voice if voice in voices.VOICES else "en_US-lessac-medium"},
        "kokoro": {"installed": voices.kokoro_installed(), "enabled": config.get_bool("kokoro-tts-enabled", False),
                   "voice": config.get("kokoro-voice", voices.KOKORO_DEFAULT_VOICE)},
        "eyes": {"ocr": bool(shutil.which("tesseract")), "installed": local_vision.installed(),
                 "active": local_vision.active(), "ready": local_vision.available(),
                 "recommended": local_vision.recommended(gpu=bool(gpu))},
        "imagine": {"ready": imagegen.available(), "gpu": bool(gpu)},
        "memory": {"ready": local_embed.configured()},
        "photos": {"ready": _photos_ready()},
        "sounds": {"ready": _sounds_ready()},
        "speakers": {"ready": _speakers_ready()},
        "languages": {"chosen": languages.chosen(config),
                      "reads": [c for c in languages.LANGUAGES if languages.reads(c)],
                      "speaks": [c for c in languages.LANGUAGES if languages.speaks(c)],
                      "listening": config.get("language", "en") == "auto"},
    }


def _photos_ready() -> bool:
    from shani_chronoa.opencv import runtime
    return runtime.installed()


def _sounds_ready() -> bool:
    from shani_chronoa import sounds
    return sounds.installed()


def _speakers_ready() -> bool:
    from shani_chronoa import speakers
    return speakers.installed()


def needs_setup(config=None) -> bool:
    """True when the window should open by itself: not yet completed, and something is missing."""
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    if config.get_bool("setup-complete", False) or config.get_bool("setup-dismissed", False):
        return False
    s = state(config)
    return not (s["brain"]["ready"] and s["ears"]["ready"] and s["voice"]["ready"])


def _progress(report: Optional[Callable[[float, str], None]], cancel: threading.Event, what: str):
    seen = {"bytes": 0}

    def hook(chunk: int, total: int) -> None:
        if cancel.is_set():
            raise Cancelled(what)
        seen["bytes"] += chunk
        if report:
            report(min(1.0, seen["bytes"] / max(total, 1)), f"{what}: {seen['bytes'] >> 20} of {total >> 20} MB")
    return hook


#: Where a model's bytes come from, in the words the consent row shows. The
#: egress layer holds a matching list (`egress.MODEL_HOSTS`) and the two must not
#: drift: this is what a person reads before agreeing, that is what decides
#: whether the request is a breach.
MODEL_HOSTS = ("huggingface.co", "github.com")


def _consent_given(config, granted: bool = True) -> None:
    """Record the consent the user actually gave.

    **The switch in the window is the only thing that calls this with True.**
    It used to be called with True by every `setup_*` function the moment a
    Download button was pressed, which meant the gate the downloaders check
    (`model-download-enabled`) was flipped by the act of asking - so "consent"
    was a side effect of clicking, the size was never on screen, and nothing
    said the bytes were going to the internet. A gate that opens itself when
    someone pushes it is not a gate.
    """
    config.set("model-download-enabled", "true" if granted else "false")


def setup_brain(key: str, report=None, cancel: Optional[threading.Event] = None, config=None,
                wait_seconds: float = 120.0, transport=None) -> str:
    """Download the model, start the service, wait until it answers. Returns a sentence for the window."""
    import time
    from shani_chronoa import engines, local_llm
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    try:
        engines.ensure("llama-server", progress=_progress(report, cancel, "llama.cpp"),
                       config=config, transport=transport, gpu=bool(local_llm.gpu_devices()))
    except engines.EngineMissing as why:
        return (f"{why} Until then you can use Ollama, or the free online models in Settings.")
    spec = local_llm.SPECS[key]
    local_llm.provision(key, progress=_progress(report, cancel, spec.filename), config=config, transport=transport)
    if report:
        report(1.0, "Starting the model...")
    problem = local_llm.start_service()
    if problem:
        return f"The model is downloaded, but it could not be started: {problem}."
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise Cancelled("start")
        if local_llm.is_up():
            where = "the graphics card" if local_llm.gpu_devices() else "the processor"
            return f"Chronoa now thinks on this computer ({key}, on {where})."
        time.sleep(1.0)
    return "The model is downloaded and starting; it is taking a while - Chronoa will use it once it answers."


def setup_ears(key: str, report=None, cancel: Optional[threading.Event] = None, config=None, transport=None) -> str:
    from shani_chronoa import engines, stt_provision
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    try:
        engines.ensure("whisper-cli", progress=_progress(report, cancel, "whisper.cpp"),
                       config=config, transport=transport)
    except engines.EngineMissing as why:
        return f"{why} Typing works in the meantime."
    spec = stt_provision.MODELS[key]
    stt_provision.provision(key, config=config, progress=_progress(report, cancel, spec.filename), transport=transport)
    config.set("whisper-model", key.split("-")[0])
    return "Chronoa can now hear you."


def setup_voice(voice: str, report=None, cancel: Optional[threading.Event] = None, config=None, transport=None) -> str:
    """Install `voice` and whatever engine speaks it, and make it the one Chronoa answers with.

    A voice belongs to an engine - Piper's voices to Piper, Kokoro's to Kokoro -
    so the person picks a voice and the engine comes with it.
    """
    from shani_chronoa import voices
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    if voice in voices.KOKORO_VOICES:
        return setup_kokoro(voice, report, cancel, config, transport)
    voices.install_piper(progress=_progress(report, cancel, "Piper"), config=config, transport=transport)
    voices.install_voice(voice, progress=_progress(report, cancel, voices.VOICES[voice].label.split(" - ")[0]),
                         config=config, transport=transport)
    config.set("piper-voice", voice)
    config.set("kokoro-tts-enabled", "false")  # the voice picked is the one heard
    return f"Chronoa now speaks with {voices.VOICES[voice].label.split(' - ')[0]}'s voice."


def _start_and_wait(instance: str, path: str, cancel: threading.Event, wait_seconds: float) -> str:
    """'' when the server answers within `wait_seconds` (0: do not wait), else a sentence saying what happened."""
    from shani_chronoa import model_service
    problem = model_service.start(instance)
    if problem:
        return f"it is downloaded, but it could not be started: {problem}"
    if wait_seconds and not model_service.wait_up(instance, wait_seconds, path, cancel):
        if cancel.is_set():
            raise Cancelled("start")
        return "it is downloaded and starting; it is taking a while"
    return ""


def setup_eyes(key: str, report=None, cancel: Optional[threading.Event] = None, config=None,
               wait_seconds: float = 60.0, transport=None) -> str:
    from shani_chronoa import engines, local_llm, local_vision
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    try:
        engines.ensure("llama-server", progress=_progress(report, cancel, "llama.cpp"),
                       config=config, transport=transport, gpu=bool(local_llm.gpu_devices()))
    except engines.EngineMissing as why:
        return f"{why} So there is no vision model to run."
    m = local_vision.MODELS[key]
    local_vision.provision(key, progress=_progress(report, cancel, m.model.filename), config=config,
                           transport=transport)
    if report:
        report(1.0, "Starting the vision model...")
    problem = _start_and_wait(local_vision.INSTANCE, "/health", cancel, wait_seconds)
    if problem:
        return f"The vision model {problem[3:]}."
    return f"Chronoa can now see what is on the screen when you ask ({key})."


def setup_imagine(report=None, cancel: Optional[threading.Event] = None, config=None,
                  wait_seconds: float = 30.0, transport=None, gpu: Optional[bool] = None) -> str:
    from shani_chronoa import imagegen, local_llm
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    gpu = bool(local_llm.gpu_devices()) if gpu is None else gpu
    imagegen.provision(gpu, progress=_progress(report, cancel, "the picture model"), config=config,
                       transport=transport)
    if report:
        report(1.0, "Starting the picture maker...")
    problem = _start_and_wait(imagegen.INSTANCE, "/sdapi/v1/sd-models", cancel, wait_seconds)
    if problem:
        return f"The picture maker {problem[3:]}."
    where = "the graphics card" if gpu else "the processor"
    return f"Chronoa can now make pictures, on {where}. Ask for one, e.g. \"draw a lighthouse at dusk\"."


def setup_memory(report=None, cancel: Optional[threading.Event] = None, config=None,
                 wait_seconds: float = 30.0, transport=None) -> str:
    from shani_chronoa import engines, local_embed, local_llm
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    try:
        engines.ensure("llama-server", progress=_progress(report, cancel, "llama.cpp"),
                       config=config, transport=transport, gpu=bool(local_llm.gpu_devices()))
    except engines.EngineMissing as why:
        return f"{why} So there is no memory model to run."
    local_embed.provision(progress=_progress(report, cancel, local_embed.MODEL.filename), config=config,
                          transport=transport)
    problem = _start_and_wait(local_embed.INSTANCE, "/health", cancel, wait_seconds)
    if problem:
        return f"The memory model {problem[3:]}."
    return "Chronoa now finds earlier conversations by what they meant, not only by their words."


def setup_photos(report=None, cancel: Optional[threading.Event] = None, config=None, transport=None) -> str:
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.opencv import runtime
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    runtime.install(progress=_progress(report, cancel, "OpenCV"), config=config, transport=transport)
    return ("Chronoa can now find faces and objects in photos and videos, blur faces or backgrounds, "
            "and straighten a photographed page.")


def setup_sounds(report=None, cancel: Optional[threading.Event] = None, config=None, transport=None) -> str:
    from shani_chronoa import sounds
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    sounds.install(progress=_progress(report, cancel, "the sound model"), config=config, transport=transport)
    return "Chronoa can now tell what a sound is - in a recording you give it, or when you ask it to listen."


def setup_speakers(report=None, cancel: Optional[threading.Event] = None, config=None, transport=None) -> str:
    from shani_chronoa import speakers
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    speakers.install(progress=_progress(report, cancel, "the speaker model"), config=config, transport=transport)
    return "Chronoa can now say who said what in a recording."


def setup_languages(codes, listen: bool, report=None, cancel: Optional[threading.Event] = None, config=None,
                    transport=None) -> str:
    from shani_chronoa import languages
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    codes = [c for c in codes if c in languages.LANGUAGES]
    if not codes:
        return "Choose at least one language."
    said = []
    for code in codes:
        lang = languages.LANGUAGES[code]
        done = languages.install(code, progress=_progress(report, cancel, lang.label.split(" - ")[-1]),
                                 config=config, transport=transport)
        said.append(f"{lang.label.split(' - ')[-1]} ({' and '.join(done)})")
    languages.set_listening(listen, config)
    heard = " Chronoa also listens for them when you speak." if listen else ""
    return f"Added {', '.join(said)}.{heard}"


#: Phrases in a step's answer that mean it did not finish (the steps answer in sentences, not codes).
_NOT_DONE = ("is not installed", "could not be started", "Choose at least")


def setup_kokoro(voice: str, report=None, cancel: Optional[threading.Event] = None, config=None,
                 transport=None) -> str:
    """Download and verify sherpa-onnx and the Kokoro model, then speak with `voice`."""
    from shani_chronoa import voices
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    voices.install_kokoro(voice, progress=_progress(report, cancel, "Kokoro"), config=config, transport=transport)
    config.set("kokoro-voice", voice)
    config.set("kokoro-tts-enabled", "true")
    name = voices.KOKORO_VOICES[voice].label.split(" - ")[0]
    return (f"Chronoa now speaks with Kokoro ({name}). On a processor it takes a moment longer to start "
            "talking than Piper; turn it off in Settings -> Voice to go back.")


def sample_sentence() -> str:
    return "Hello, I'm Chronoa. I'll be right here whenever you need me."


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------


#: The wizard's page ids, declared at import so that `--show-page=setup:review`
#: can name one **before the wizard has ever been built**. Registering from
#: inside `build_window` meant the only ids that existed were the ones a wizard
#: that was already open had created, so the one caller whose job is to *open*
#: it was the one that could not ask.
#: **Chronoa's orb, embedded as SVG bytes.**
#:
#: It used to be `Gtk.Image.new_from_icon_name("shani-chronoa")`, and that is a
#: *theme lookup*: it needs the icon in an `XDG_DATA_DIRS` that is on the path.
#: Measured, it does not resolve from a plain checkout - the render came back as
#: GTK's missing-image placeholder, **a gear**, which is how "some page had a gear
#: icon" got reported. Shipping `usr/share/icons/hicolor/scalable/apps/` is right
#: for an installed system and useless for the tree the wizard is developed in.
#:
#: `Gtk.Svg.new_from_bytes` + `Gtk.Picture` renders vector bytes with no file and
#: no compiled `GResource` (there is none in this repo), so the mark is identical
#: in the app, in a render and in a test. Sized in the SVG itself rather than
#: `set_pixel_size`, because a `Gtk.Picture` reports the paint's own size.
_ORB_MARK_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="112" height="112" '
    b'viewBox="0 0 112 112">'
    b'<circle cx="56" cy="56" r="48" fill="#3584e4" fill-opacity="0.85"/>'
    b'<rect x="48" y="30" width="16" height="34" rx="8" fill="#ffffff"/>'
    b'<path d="M38 62a18 18 0 0 0 36 0" fill="none" stroke="#ffffff" '
    b'stroke-width="7" stroke-linecap="round"/>'
    b'<path d="M56 80v12M46 92h20" fill="none" stroke="#ffffff" '
    b'stroke-width="7" stroke-linecap="round"/>'
    b'</svg>'
)


def _jump_label(text: str):
    """A label with a chevron: the sign that a press goes somewhere.

    **The chevron is not decoration.** A `flat` button renders as unstyled words -
    on the Brain page the "Use a model on this computer" jump sat underneath the
    Next button looking exactly like a dead label, which is the "minor UI glitch
    during model installation" that got reported. The control was wired the whole
    time; the affordance was missing.

    The same device this file already uses for the welcome rows
    (`Adw.ActionRow` + `go-next-symbolic`), whose comment records that the chevron
    exists because an `Adw.ActionRow` gives no affordance of its own for "this is
    activatable". A `flat` button has even less: it has a border when styled and
    none when it is not.

    Imports its own `Gtk`, because this module imports it inside `build_window` on
    purpose - it is also imported by the MCP server and the CLI, which may run on
    a machine with no display at all.
    """
    from gi.repository import Gtk

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row.append(Gtk.Label(label=text))
    row.append(Gtk.Image.new_from_icon_name("go-next-symbolic"))
    return row


def _mark_px() -> int:
    """The mark's own size, read out of the SVG it is drawn from.

    Derived rather than typed so that redrawing the mark cannot leave a threshold
    below it - the same reasoning that derives `<sense-name>-sense-enabled` from
    a sense's name rather than keeping a second spelling.
    """
    import re
    # The blob is **bytes**, and `re` refuses a str pattern on a bytes subject -
    # which is a TypeError at import rather than a wrong number, so it cannot
    # pass unnoticed. Decode a short prefix rather than the whole document.
    text = _ORB_MARK_SVG[:400].decode("utf-8", "replace")
    match = re.search(r'width="(\d+)"', text)
    return int(match.group(1)) if match else 128


#: A void has to be able to **hold the mark**, which is what this missed.
#:
#: `_void_has_room` asked whether the content scrolls, which is a different
#: question from whether there is space to put something in. A page whose content
#: ends **one pixel** above the window answered "yes, there is a void", and a
#: 112px mark was drawn centred over the last rows.
#:
#: Measured on `setup:extras`, rendered 2026-10-13 - the first time any wizard
#: page had ever been rendered in this repository. The mark's blue pixels measure
#: **y=705..798, x=593..686**, and the row-card borders on that page fall at y=
#: 693, 748 and 803: so it was drawn across **two** of them, covering the word
#: "speaker" in *"Who said what - split a recording by speaker"*, with 824
#: near-white text pixels immediately to its left. The page's own layout is
#: correct (`test_ui_layout_contract.py` is 16 passed, no wizard page asks for a
#: sideways scrollbar) - the only thing wrong was a decoration drawn on top of it.
MARK_PX = _mark_px()

#: The mark plus a little air, so a void exactly the size of the mark does not
#: leave it touching the content above or below.
VOID_NEEDED_PX = MARK_PX + 24


def _void_has_room(adjustment, content_height=None) -> bool:
    """Whether a page's content leaves a void **the mark can actually sit in**.

    **`upper <= page_size` is the no-scrollbar case**, but it is also what an
    *unallocated* scroller reports - `upper` and `page_size` are both 0 before
    the widget has ever been laid out, and `0 <= 0 + 1` is true. Measured: the
    first version of this showed the mark on **all sixteen pages**, including
    `review`, whose content demands 1029px of a 620px window, because the page
    had been built and never shown.

    So a page that has never been measured is not a page with room in it, and
    the answer is False rather than a guess in either direction.

    **A void of any size is not a void.** The mark is `MARK_PX` square, so the
    gap has to be at least that; anything less is a page with no room, and the
    honest answer is no mark rather than a mark over the text. Measured on
    `setup:extras`: a **six-pixel** gap passed the old test, and a 112px mark was
    drawn across two row cards, covering the word "speaker".

    **And the gap has to be measured off the content, not off the adjustment.**
    `upper` is the *allocated* height of the scrolled child, and this child is an
    `Adw.Clamp` with `vexpand`, so it is handed the whole viewport and reports
    `upper == page_size` **whatever its contents measure**. The first version of
    the threshold used `upper` and therefore removed the mark from `setup:brain`,
    whose two lines of text leave roughly 500px of obvious void - the fix was
    over-fitted and caught by re-rendering the two pages rather than by the unit
    tests, which were green throughout.

    So the caller **measures the content and passes the number in** rather than
    this function reaching for a widget: `Gtk` is deliberately not imported at
    module scope here (the MCP server and the CLI import this module on a machine
    with no display), and a pure function of two integers is what can be tested
    without one. `upper` remains the fallback for a caller with nothing to measure.
    """
    if adjustment.get_page_size() <= 1:
        return False
    needed = adjustment.get_upper() if content_height is None else content_height
    return (adjustment.get_page_size() - needed) >= VOID_NEEDED_PX


def _orb_mark():
    """The mark that fills a wizard page's void. A picture, never a control.

    Imports its own, because `Gtk` is imported inside `build_window` on purpose -
    this module is imported by the MCP server and the CLI, which may run on a
    machine with no display at all.
    """
    from gi.repository import GLib, Gtk

    svg = Gtk.Svg.new_from_bytes(GLib.Bytes.new(_ORB_MARK_SVG))
    picture = Gtk.Picture.new_for_paintable(svg)
    picture.set_can_shrink(False)
    # A picture of the app is not a control: it must never take a click, or the
    # space above a button becomes a dead target.
    picture.set_can_target(False)
    picture.set_focusable(False)
    return picture


_SETUP_PAGES = [
    ("welcome", "Welcome"), ("mode", "Mode"), ("cloud-keys", "Cloud keys"),
    ("brain", "Brain"), ("model-picker", "Model"), ("ears", "Ears"),
    ("voice", "Voice"), ("review", "Review"), ("extras", "Optional extras"),
    ("eyes", "Eyes"), ("imagine", "Imagine"), ("memory", "Memory"),
    ("photos", "Photos and videos"), ("sounds", "Sounds"),
    ("speakers", "Who said what"), ("languages", "Languages"),
    ("done", "Done"),
]

#: Retired ids, kept so a stored one opens whatever absorbed it.
_SETUP_ALIASES = {"more": "extras", "code": "eyes", "picture": "imagine",
                  "download": "review", "keys": "cloud-keys", "tts": "voice"}


#: Widest a wizard page's content column gets, in px. **Equal to the 560px
#: default window, and it has to be.** An `Adw.Clamp` only starts squeezing when
#: `tightening_threshold` is reached, so a maximum *wider* than the window is no
#: limit at all - measured, at 640px the `cloud-keys` page asked for 640px of a
#: 560px window and got a sideways scrollbar, the same defect this file records
#: as fixed once already at 590px. The same number twice is the point.
WIZARD_MAX_WIDTH = 560


def _register_setup_pages() -> None:
    """Declare the wizard's pages. Idempotent; called from `build_window` too."""
    from shani_chronoa import pages as page_registry
    page_registry.register(
        "setup", _SETUP_PAGES,
        factory=lambda app, config=None: build_window(app, config or ChronoaConfig()),
        aliases=dict(_SETUP_ALIASES),
    )


_register_setup_pages()


def _wizard_show_page(win, view, page_id: str, goto) -> bool:
    """Show one setup page by id, from anywhere.

    Every page here is also reachable through `goto`, but only from inside the
    builder that builds them - so `shani-chronoa --show-page=setup:review` could
    not have worked, and neither could a notification saying "finish the
    download step".

    The ids are checked against the registry rather than against the view's own
    tags, so a page that failed to build is a page that refuses rather than one
    that pushes nothing and reports success.
    """
    from shani_chronoa import pages as page_registry
    if page_id not in page_registry.page_ids("setup"):
        return False
    win.present()
    goto(page_id)
    return True


def build_window(application, config=None, on_finished: Optional[Callable[[], None]] = None):
    """The setup window (created lazily so importing this module never needs a display)."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk

    from shani_chronoa import engines, local_llm, sherpa, stt_provision, voices
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()

    win = Adw.Window(application=application, title="Set up Chronoa", default_width=560, default_height=620)
    win.set_modal(False)
    #: Page title by tag, so the forward button can say where it goes.
    _titles = {"welcome": "Welcome", "mode": "Mode", "cloud-keys": "Cloud keys",
               "brain": "Brain", "model-picker": "Model", "ears": "Ears",
               "voice": "Voice", "extras": "Optional extras", "review": "Review",
               "eyes": "Eyes", "imagine": "Imagine", "memory": "Memory",
               "photos": "Photos and videos", "sounds": "Sounds",
               "speakers": "Who said what", "languages": "Languages",
               "done": "Done"}

    view = Adw.NavigationView()
    toolbar = Adw.ToolbarView()
    # **A back button, which this had none of.** `Adw.NavigationView` does not
    # add one, and with an empty `Adw.HeaderBar` the wizard had exactly one
    # direction: forward. Measured on a real screen, on the Brain page the only
    # controls in the title bar were minimise, maximise and close - so a user who
    # picked the wrong model could not go back and change it. They could skip the
    # rest of setup or close the window and start again.
    #
    # Written against what libadwaita 1.5 actually has: `Adw.BackButton` and
    # `can-go-back` arrived in 1.6, and on 1.5 `Adw.NavigationView` exposes
    # `pop()` but no way to ask whether there is anything to pop to. So the depth
    # is tracked here from `notify::visible-page` - which is also what makes the
    # button's own state testable rather than hoped for.
    back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back", visible=False)
    back.update_property([Gtk.AccessibleProperty.LABEL], ["Back to the previous step"])
    back.connect("clicked", lambda *_: view.pop())
    header_bar = Adw.HeaderBar()
    header_bar.pack_start(back)
    toolbar.add_top_bar(header_bar)
    toolbar.set_content(view)
    win.set_content(toolbar)
    #: Tags the view has shown, oldest first, mirroring its own stack. libadwaita
    #: 1.5 cannot be asked whether a pop is possible, so the depth is tracked
    #: here from `notify::visible-page` - which is also what makes the button's
    #: state testable rather than hoped for.
    _history: List[str] = []

    def on_visible_page(_view, page) -> None:
        # `visible-page` also fires while the view is being built and while
        # `replace_with_tags` swaps the whole stack, and the object handed over
        # is then not always a page with a tag. `getattr` rather than an
        # `isinstance` check: the widget class is right, the tag is what is
        # missing, and an exception inside a notify handler takes the whole
        # window down rather than skipping one update.
        tag = getattr(page, "get_tag", lambda: None)()
        if tag:
            if tag in _history:
                # Going back: truncate to the entry that is showing, so the list
                # stays a mirror of the view's stack rather than growing forever.
                _history[:] = _history[:_history.index(tag) + 1]
            elif not _history or _history[-1] != tag:
                _history.append(tag)
        back.set_visible(len(_history) > 1)
        back.set_tooltip_text("Back" if len(_history) <= 2 else
                              f"Back to {_history[-2]}")

    view.connect("notify::visible-page", on_visible_page)
    win.cancel = threading.Event()
    #: Whether a consent switch on the page currently being built is on, and every
    #: download button that has to follow it. Declared before the first page so
    #: `consent_row` and `worker_area` can both reach them.
    pending_consent = [False]
    _download_buttons: List[Gtk.Button] = []

    def _refresh_download_buttons() -> None:
        for button in _download_buttons:
            on = pending_consent[0]
            button.set_sensitive(on)
            button.set_tooltip_text("" if on else
                                    "Turn on \"Allow downloading models\" first")

    def on_close(*_):
        # Closing it is "not now": it stops opening by itself (Settings, the
        # dock menu and --setup still open it); without this it came back on
        # every launch until someone finished every page.
        win.cancel.set()
        config.set("setup-dismissed", "true")
        return False
    win.connect("close-request", on_close)
    s = state(config)

    #: Every wrapping label here is capped to this many characters. Without it a
    #: label's natural width is its whole unwrapped sentence, the page grows past
    #: the window, and `Adw.NavigationPage`'s own scroller adds a sideways
    #: scrollbar - the grey bar that sat under every page of this wizard for
    #: days. Measured: the welcome description alone asked for 947px in a 560px
    #: window, and 437px once capped.
    MAX_WRAP_CHARS = 56

    #: A few words per extra, for a row subtitle - which cannot wrap. The
    #: sentence lives on the extra's own page and in the tooltip.
    EXTRA_HINTS = {
        "eyes": "describes the screen",
        "imagine": "makes a picture",
        "memory": "finds past talk",
        "photos": "faces and objects",
        "sounds": "what a sound is",
        "speakers": "who said what",
        "languages": "more languages",
    }

    def wrapping(label: str, **kwargs) -> Gtk.Label:
        """A label that wraps, and that does not decide how wide the page is."""
        widget = Gtk.Label(label=label, wrap=True, xalign=0, **kwargs)
        widget.set_max_width_chars(MAX_WRAP_CHARS)
        return widget

    def page(title: str, tag: str, description: str):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=18, margin_bottom=18,
                      margin_start=18, margin_end=18)
        # **Capped, or this label decides how wide the whole page is.** With
        # `wrap=True` alone, GTK reports the natural width of the whole
        # unwrapped sentence - 947px for the welcome description - so every page
        # grew to more than the window and `Adw.NavigationPage`'s own scroller
        # (horizontal policy: default) put a sideways scrollbar underneath it.
        # That grey bar was in the bottom of every wizard screenshot for days
        # before this was measured rather than guessed at. `set_max_width_chars`
        # is what bounds it; the same label measures 437px capped.
        head = wrapping(description)
        box.append(head)
        # The horizontal policy has to be NEVER, because the bar belongs to
        # `Adw.NavigationPage`'s *own* scroller, whose policy is the default, and
        # it appears whenever the page's content is wider than the page.
        #
        # What stops the page being widened is capping the *labels* - see
        # `wrapping()` above. `set_propagate_natural_width(False)` looks like it
        # should help and **does not**: measured on this GTK, a scrolled window
        # with `propagate_natural_width` off still reports its child's full
        # natural width (a 900px child measured 900). It is left out rather than
        # left in with a comment claiming it works.
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=WIZARD_MAX_WIDTH, tightening_threshold=WIZARD_MAX_WIDTH)
        clamp.set_child(box)
        scroller.set_child(clamp)

        # **The void gets a mark, and only when there is one.**
        #
        # Measured against the 620px default window, and reported as "minor UI
        # glitches during model/voice installation": four of the sixteen pages
        # have far less content than the window - `ears` 270px, `brain` 84px,
        # `mode` 316px, `model-picker` 470px - so between the last row and the
        # pinned action there was 150 to 536px of nothing. Holding a button at
        # the bottom is right (see the footer note below); leaving half a screen
        # of plain nothing above it is what reads as broken.
        #
        # It is drawn **over the scroller**, not appended to the content, because
        # the content is what a page's caller fills in after this returns - an
        # appended mark would land in the middle of a page that later adds rows.
        # And it is shown only when the content cannot scroll, so on the eleven
        # pages that fill or overflow the window it is absent rather than drawn
        # over a row. `upper <= page_size` is the no-scrollbar case read off the
        # adjustment: fewer pixels of content than the window has room for.
        mark = _orb_mark()
        mark.set_halign(Gtk.Align.CENTER)
        mark.set_valign(Gtk.Align.END)
        mark.set_margin_bottom(24)
        overlay = Gtk.Overlay()
        overlay.set_child(scroller)
        overlay.add_overlay(mark)

        def _mark_follows_the_void(adjustment) -> None:
            # Measured off `clamp`, not off `adjustment.get_upper()` - the
            # clamp has `vexpand`, so the adjustment reports a full-height child
            # whatever its contents measure. See `_void_has_room`.
            natural = clamp.measure(Gtk.Orientation.VERTICAL, -1)[1]
            mark.set_visible(_void_has_room(adjustment, natural))

        for signal in ("changed", "value-changed"):
            scroller.get_vadjustment().connect(signal, _mark_follows_the_void)
        _mark_follows_the_void(scroller.get_vadjustment())

        # **The action row is pinned below the scroller, not appended to the
        # content.** It used to be the last children of `box`, which put it at
        # the bottom of the *scrolling content* - so it scrolled away with it.
        # Measured against the 620px window, five of the sixteen pages have more
        # content than that (`languages` 1137px, `review` 1029px, `voice` 897px,
        # `welcome` 849px, `cloud-keys` 672px), which means on the **first screen
        # a new person sees** the only forward action - "Start" - was 849px down
        # and out of sight. On the other eleven pages it looked fine, which is
        # why it survived: the defect is invisible until the page is tall.
        #
        # `[ScrolledWindow(content), footer]` is the ordinary libadwaita shape,
        # and it is the only structure that keeps a button on screen.
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=18,
                         margin_start=18, margin_end=18)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.append(overlay)
        outer.append(footer)

        p = Adw.NavigationPage(title=title, tag=tag)
        p.set_child(outer)
        # Keyed by `id(box)` for the same reason `_choice_rows` below is: content
        # and actions were one box, so the callers still holding `box` need a way
        # to reach the footer that now belongs to it.
        _footers[id(box)] = footer
        return p, box

    #: The check buttons of every `choice_group`, keyed by the group widget.
    _choice_rows: Dict[int, Dict[str, Gtk.CheckButton]] = {}

    #: The pinned action row of every page, keyed by that page's content box.
    _footers: Dict[int, Gtk.Box] = {}

    def page_footer(box) -> Gtk.Box:
        """Where a page's buttons belong: the row pinned under its scroller.

        Falls back to `box` itself so a caller handed some other box still puts
        its widgets somewhere visible rather than nowhere.
        """
        return _footers.get(id(box), box)

    def choice_group(title: str, items, selected: str):
        group = Adw.PreferencesGroup(title=title)
        first, rows = None, {}
        for key, label, subtitle in items:
            check = Gtk.CheckButton()
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.set_active(key == selected)
            row = Adw.ActionRow(title=label, subtitle=subtitle, activatable_widget=check)
            row.add_prefix(check)
            group.add(row)
            rows[key] = check
        # The check buttons themselves, so a page can listen for a change of
        # selection and re-record what it plans to download. `chosen()` alone
        # says what is selected *now*; it cannot say that it changed.
        _choice_rows[id(group)] = rows
        return group, (lambda: next((k for k, c in rows.items() if c.get_active()), selected))

    def worker_area(box, button_label: str, run: Callable[[Callable], str], next_tag: Optional[str],
                    done_label: str = "", skip_label: str = "", needs_consent: bool = False):
        """A button that runs `run` on a thread with a progress bar; with `next_tag`, Skip/Next move on.

        `skip_label` exists because the skip button means two different things on
        different pages. On the three required pages it is "Skip for now" - this
        one step, and the wizard carries on without it. On the optional extras
        pages it is "Skip the rest", because there "for now" would be a lie: it
        leaves seven pages, not one step.
        """
        status = wrapping("")
        bar = Gtk.ProgressBar(show_text=True, visible=False)
        go = Gtk.Button(label=button_label, css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
        nxt = Gtk.Button(label=f"Next: {_titles.get(next_tag, next_tag)}",
                         css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER,
                         visible=False)
        # A download button stays insensitive until the page's consent switch is
        # on. `pending_consent` is the single source of truth for that, set by
        # `consent_row`; with no consent row on the page it defaults to granted,
        # which is why the pages that need no network (Ollama, "already
        # installed") are unaffected.
        widgets = [bar, status, go]
        if needs_consent:
            go.set_sensitive(pending_consent[0])
            go.set_tooltip_text("Turn on \"Allow downloading models\" first"
                                if not pending_consent[0] else "")
            _download_buttons.append(go)
        if next_tag:
            skip = Gtk.Button(label=skip_label or ("Skip for now" if next_tag != "done" else "Close"),
                              css_classes=["flat"], halign=Gtk.Align.CENTER)
            skip.connect("clicked", lambda *_: goto(next_tag))
            nxt.connect("clicked", lambda *_: goto(next_tag))
            widgets += [nxt, skip]
        for w in widgets:
            page_footer(box).append(w)
        if done_label:
            status.set_label(done_label)

        def report(fraction: float, text: str) -> None:
            GLib.idle_add(lambda: (bar.set_fraction(fraction), bar.set_text(text), False)[2])

        def finished(message: str, ok: bool) -> bool:
            status.set_label(message)
            bar.set_visible(False)
            go.set_sensitive(True)
            go.set_visible(not ok)
            nxt.set_visible(ok and bool(next_tag))
            return False

        def start(*_):
            go.set_sensitive(False)
            bar.set_visible(True)
            bar.set_fraction(0)
            status.set_label("")

            def body():
                try:
                    msg = run(report)
                    ok = not any(s in msg for s in _NOT_DONE)
                except Cancelled:
                    msg, ok = "Cancelled - nothing was kept.", False
                except Exception as exc:  # noqa: BLE001 - every failure is a sentence in the window
                    logger.exception("setup step failed")
                    msg, ok = f"That did not work: {exc}", False
                GLib.idle_add(finished, msg, ok)
            threading.Thread(target=body, daemon=True).start()
        go.connect("clicked", start)
        return go, status

    # Declared once, here, because two places need them: the welcome page lists
    # what each optional extra costs before anything is chosen, and each extra's
    # own page then installs it. They used to be imported further down, which
    # left the welcome page unable to name a single size - and it named none.
    from shani_chronoa import imagegen, languages, local_embed, local_vision
    from shani_chronoa import sounds as sounds_mod, speakers as speakers_mod
    from shani_chronoa.opencv import runtime as cv_runtime

    # 1. welcome
    #
    # **Every entry on its own row, and every one of them says what it costs.**
    # This page used to say Chronoa "needs three things", list Brain, Ears and
    # Voice, and then collapse six optional extras into a single row reading
    # "eyes, pictures, memory and languages - after the voice". So the first
    # screen a new person saw named three of the nine things that exist, and the
    # only mention of a 2.0 GB picture model was in a comma-separated string
    # with three 100 MB ones. Nine rows is four more lines, and each can be
    # clicked into - which the lumped row could not be, because it was not a
    # target.
    welcome, box = page(
        "Welcome", "welcome",
        "Chronoa runs on this computer. The first three are what it needs to "
        "think, hear you and talk back; the rest are optional and each has its "
        "own page. Nothing is downloaded until you ask, and every file is "
        "checked before use.")

    def _mb(size_bytes: float) -> str:
        """A download size in the units a person expects, and **never "0 MB"**.

        `{:.0f}` on anything under 500,000 bytes rounds to zero, so a real
        download rendered as a download of nothing. Measured 2026-10-13 by
        rendering the Languages page - the first time any wizard page had been
        rendered in this repository - where **Punjabi** read
        *"0 MB - text only (no voice yet)"* beside eleven rows carrying real
        sizes. It is **497,721 bytes**, so the honest answer is 486 KiB, and
        `files.human_size` already existed to say it.

        `human_size` is 1024-based (MiB) and this is 1000-based (MB); both are
        right in their own convention, so the small-value branch is where the two
        meet, and it is the only place on a row where the units differ.

        Imported here rather than at module scope, because this module is
        imported by the MCP server and the CLI and keeps its GUI imports inside
        `build_window` on purpose.
        """
        from shani_chronoa import files

        mb = float(size_bytes) / 1e6
        if mb < 0.5:
            return files.human_size(size_bytes)
        return f"{mb:.0f} MB" if mb < 1000 else f"{mb / 1000:.1f} GB"

    core = Adw.PreferencesGroup(
        title="To think, hear you and talk back",
        description="These three are the ones a conversation needs.")
    def _jump_row(title: str, subtitle: str, tag: str) -> "Adw.ActionRow":
        """One of the welcome rows, and it goes to its page when pressed.

        **These rows were inert, and the comment above them said they were not.**
        Every one was a bare `Adw.ActionRow(title=..., subtitle=...)`: not
        activatable, no suffix control, and nothing connected to `activated`.
        Measured by building the real wizard and walking the visible page for
        pressable controls - **the Welcome page had exactly one: `Start`.** Nine
        rows naming nine features and nine download sizes, none of them a target,
        on the first screen a new person sees, two lines under a comment claiming
        "each can be clicked into - which the lumped row could not be, because it
        was not a target".

        This module already knows how to make a row a target - `choice_group` uses
        `activatable_widget` - so this is the same device applied to the case that
        had it missing.

        The suffix chevron is not decoration: `Adw.ActionRow` gives no affordance
        of its own for "this is activatable", so a row that responds to a press and
        looks like a label is its own small lie. The tooltip names the page, so the
        destination is knowable before the press rather than after it.
        """
        row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.set_tooltip_text(f"Open the {_titles.get(tag, tag)} page")
        row.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{title}: open the "
                              f"{_titles.get(tag, tag)} page"])
        row.connect("activated", lambda _r, t=tag: goto(t))
        return row

    for name, tag, ready, ok_text, missing_text in (
            ("Brain", "brain", s["brain"]["ready"], "ready", "needs a language model"),
            ("Ears", "ears", s["ears"]["ready"], "ready",
             "needs a speech-recognition model"),
            ("Voice", "voice", s["voice"]["ready"], "natural voice ready",
             "using the basic voice")):
        core.add(_jump_row(name, ok_text if ready else missing_text, tag))
    box.append(core)

    optional = Adw.PreferencesGroup(
        title="Optional, each on its own page",
        description="None of these is needed to finish, and each is chosen later.")
    for title, tag, size_bytes in (
            ("Eyes", "eyes", local_vision.TIERS and
             local_vision.MODELS.get(s["eyes"]["active"],
                                    list(local_vision.MODELS.values())[0]).size_bytes),
            ("Imagine - pictures from a description", "imagine",
             imagegen.MODEL.size_bytes + imagegen.engine_for(s["imagine"]["gpu"]).size_bytes),
            ("Memory - find earlier conversations by meaning", "memory",
             local_embed.MODEL.size_bytes),
            ("Photos and videos", "photos", cv_runtime.DOWNLOAD_BYTES),
            ("Sounds - what a sound is", "sounds", sounds_mod.MODEL.size_bytes),
            ("Who said what - split a recording by speaker", "speakers",
             speakers_mod.SEGMENTATION.size_bytes + speakers_mod.EMBEDDING.size_bytes),
    ):
        ready = bool(s[tag].get("ready"))
        optional.add(_jump_row(
            title, "ready" if ready else f"{_mb(size_bytes)} to download", tag))
    chosen = s["languages"]["chosen"]
    optional.add(_jump_row(
        "Languages",
        (f"{len(chosen)} added" if chosen else
         f"{_mb(sum(languages.install_size(c) for c in languages.LANGUAGES))} "
         "if you add any"),
        "languages"))
    box.append(optional)
    start_btn = Gtk.Button(label="Start", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
    start_btn.connect("clicked", lambda *_: goto("mode"))
    page_footer(box).append(start_btn)
    view.add(welcome)

    def already_there(done_label: str, next_tag: str, reason: str = "") -> None:
        """A page whose model is already downloaded and verified.

        **Continuing is the primary action, and the download is not offered at
        all.** Measured in a slot run: with a 639 MB model already in place, the
        brain page still led with a blue "Download and start" and offered no way
        forward except that button - so the honest answer to "is this ready?" was
        a re-download, and the wizard's own `Skip for now` was the only cheaper
        route. A page that already has what it needs should say so and move on;
        a user who wants a different model can pick one, and the download button
        comes back for that choice.
        """
        box.append(wrapping(done_label + (" " + reason if reason else "")))
        n = Gtk.Button(label=f"Next: {_titles.get(next_tag, next_tag)}",
                       css_classes=["suggested-action", "pill"],
                       halign=Gtk.Align.CENTER)
        n.connect("clicked", lambda *_: goto(next_tag))
        page_footer(box).append(n)

    def consent_row(box, size_mb: float, what: str, config,
                    on_change: Optional[Callable[[bool], None]] = None) -> "Adw.SwitchRow":
        """The switch that grants permission to download, and what it says.

        **This is the answer to "does setup need consent, and where is it?"**
        Until now the key was set by pressing Download, so the person saw a size
        in a list and a button, and never saw either the word "download" as a
        permission or the fact that the bytes leave the machine. So:

        - the switch is **off** unless permission was already granted, so a first
          run states the default rather than inheriting one;
        - its subtitle names the size, the host and the fact that every file's
          fingerprint is checked before use - what they are agreeing to, not that
          there is a switch;
        - the Download button stays insensitive until it is on, and says what to
          do if pressed (`go.set_tooltip_text`), rather than raising a
          `ConsentRequired` from a worker thread where it would surface as a
          status line nobody reads.

        Toggling it is the consent act: on grants, off withdraws. Withdrawing
        re-locks the page, which is the point of a switch rather than a
        checkbox.
        """
        already = bool(config.get_bool("model-download-enabled", False))
        row = Adw.SwitchRow(
            title="Allow downloading models from the internet",
            # A few words, because libadwaita 1.5 does not wrap a row subtitle:
            # measured, the full sentence below made this row 844px and its page
            # 956px, in a 560px window. The rest is a capped label underneath,
            # where it wraps *and* reports a bounded width.
            subtitle=f"{size_mb:.0f} MB for {what}",
            active=already)
        box.append(row)
        box.append(wrapping(
            f"From {', '.join(MODEL_HOSTS)}. Chronoa checks every file's "
            "fingerprint before it is used, and nothing else is fetched."))

        def on_toggle(state: bool) -> None:
            # **Toggling the switch is the consent act.** On grants it, off
            # withdraws it - which is why this is a switch and not a checkbox:
            # a checkbox you can tick twice has no off state to withdraw with.
            _consent_given(config, state)
            pending_consent[0] = state
            _refresh_download_buttons()
            if on_change is not None:
                on_change(state)

        row.connect("notify::active", lambda r, *_: on_toggle(r.get_active()))
        pending_consent[0] = already
        return row

    # ── the plan ──────────────────────────────────────────────────────────
    # What the user has chosen, in one place, and downloaded only once at the
    # end.
    #
    # **Each page used to download its own thing the moment you pressed its
    # button**, so a person who wanted eyes and a voice paid for the voice
    # before being offered eyes, could not tell what the total would be, and
    # discovered the size of a decision after making it. That is the wrong order
    # for anything measured in gigabytes. So every section now *records* a
    # choice and the last page shows all of them together and downloads them in
    # one go - which is also the only place the consent switch makes sense,
    # because "yes, download 3.4 GB of these five things" is one question and
    # five questions was never what anyone was asked.
    plan: Dict[str, Dict[str, object]] = {}

    def choose(item_id: str, label: str, size_bytes: float,
               run: Callable[[Callable], str], detail: str = "") -> None:
        """Record what this section wants, so the review page can show it."""
        plan[item_id] = {"label": label, "size": float(size_bytes),
                         "run": run, "detail": detail}

    def _record_when_shown(page_widget, item_id: str, label: str, size_bytes: float,
                           run: Callable[[Callable], str], detail: str = "") -> None:
        """Put an extras item in the plan the first time its page is shown.

        `Adw.NavigationPage` has a `shown` signal in libadwaita 1.5, which fires
        when the page becomes the visible one - so the plan holds what the person
        looked at, not what the window happened to build.
        """
        def on_shown(page) -> None:
            choose(item_id, label, size_bytes, run, detail)

        # The signal belongs to the *page*, not to the body box inside it.
        # Passing the box raised `TypeError: unknown signal name: "shown"` the
        # first time anybody opened the wizard, so `_open_setup` died and the app
        # carried on showing the main window with no wizard and no message - which
        # is what "the setup wizard is not shown" looked like from the outside,
        # and why every screenshot in three runs was the same main window.
        if not isinstance(page_widget, Adw.NavigationPage):
            raise TypeError(
                "_record_when_shown needs the page, not "
                f"{type(page_widget).__name__}: only a NavigationPage emits "
                "'shown', so a body box can never record itself when seen")
        page_widget.connect("shown", on_shown)

    def unchoose(item_id: str) -> None:
        plan.pop(item_id, None)

    _engine_counted: Dict[str, float] = {}

    def with_engine(program: str, size_bytes: float) -> float:
        """`size_bytes` plus whatever this plan has not yet counted for `program`.

        **One program, one charge, for the whole plan.** The Brain, Eyes and
        Memory rows all run `llama-server`, and each of them would otherwise add
        the same 18 MB release to a total the person is being asked to agree to -
        three charges for one file, which is a question about bytes that is
        simply wrong rather than merely pessimistic.

        The tally is keyed by program rather than by row, which is also what
        makes it correct when a choice is *changed* rather than added: switching
        the brain's radio re-records that row, and the second call charges
        nothing. Dropping the brain while keeping Eyes leaves the charge in
        place, because that program is still going to be fetched for them.

        A program this tree has no release for contributes **0** rather than a
        guess at a size - `engines.ensure` will refuse it with a sentence naming
        the package manager, so a number here would be pricing a download nobody
        is ever offered.
        """
        already = _engine_counted.get(program, 0.0)
        extra = max(0.0, float(engines.pending_bytes(program)) - already)
        _engine_counted[program] = already + extra
        return float(size_bytes) + extra

    def plan_total() -> float:
        return sum(float(i["size"]) for i in plan.values())

    def build_review() -> None:
        """The one page that downloads anything."""
        review, body = page(
            "Review", "review",
            "Everything you picked, and nothing you did not. Each line is one "
            "download; the total is what this will take.")
        group = Adw.PreferencesGroup(title="To download")
        body.append(group)
        _review_rows: List[Adw.PreferencesRow] = []

        def render() -> None:
            # Only the rows this function added. Walking `get_first_child()` on
            # an `Adw.PreferencesGroup` hands back a non-child placeholder when
            # the group is empty, and removing that prints
            # "tried to remove non-child ... from ... AdwPreferencesGroup" -
            # which is what a first render with an empty plan did.
            for stale in list(_review_rows):
                group.remove(stale)
            _review_rows.clear()
            if not plan:
                row = Adw.ActionRow(title="Nothing to download",
                                    subtitle="Everything you need is already here")
                group.add(row)
                _review_rows.append(row)
                return
            for item_id, item in plan.items():
                mb = float(item["size"]) / 1e6
                size_text = f"{mb:.0f} MB" if mb < 1000 else f"{mb / 1000:.1f} GB"
                # A size, and at most a few words: `detail` is prose written
                # for the plan, and prose in a row subtitle is prose that cannot
                # wrap. The full text belongs in the tooltip, which can.
                row = Adw.ActionRow(title=str(item["label"]),
                                    subtitle=f"{size_text}"
                                              + (f" - {item['detail']}"
                                                 if item["detail"] else ""))
                row.set_tooltip_text(item["detail"] or "")
                drop = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
                drop.connect("clicked", lambda _b, i=item_id: (unchoose(i), render()))
                row.add_suffix(drop)
                group.add(row)
                _review_rows.append(row)
            total = plan_total()
            total_mb = total / 1e6
            total_row = Adw.ActionRow(
                title="Total",
                subtitle=(f"{total_mb:.0f} MB" if total_mb < 1000
                          else f"{total_mb / 1000:.1f} GB")
                         + f" across {len(plan)} download(s)")
            group.add(total_row)
            _review_rows.append(total_row)

        render()
        status = wrapping("")
        bar = Gtk.ProgressBar(show_text=True, visible=False)
        body.append(bar)
        body.append(status)
        go = Gtk.Button(label=f"Download {len(plan)} thing(s)" if plan else "Continue",
                        css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)

        def on_consent(state: bool) -> None:
            go.set_sensitive(bool(plan) or not state is False)
            if plan and not state:
                go.set_tooltip_text("Turn on \"Allow downloading models\" first")
            else:
                go.set_tooltip_text("")

        consent_row(body, plan_total() / 1e6,
                    f"the {len(plan)} thing(s) listed above", config, on_consent)

        def run_all(_button=None) -> None:
            # `clicked` hands the button in. `run_all()` with no parameter raised
            # `TypeError: takes 0 positional arguments but 1 was given` on the
            # only press that matters - the one that would have started the
            # download - and the traceback went to the terminal behind the window.
            # Every handler connected to a signal in this module takes the
            # emitter; this one did not, because it was written as a plain
            # function and called directly from a test.
            if not plan:
                goto("done")
                return
            go.set_sensitive(False)
            items = list(plan.items())

            def worker() -> None:
                done = 0
                for label, item in items:
                    GLib.idle_add(bar.set_text, str(label))
                    GLib.idle_add(bar.set_visible, True)
                    try:
                        item["run"](lambda f, t: GLib.idle_add(
                            bar.set_fraction, f))
                    except Exception as exc:                     # noqa: BLE001
                        GLib.idle_add(status.set_label,
                                       f"{label} did not finish: {exc}")
                        continue
                    done += 1
                    GLib.idle_add(bar.set_fraction, done / len(items))
                GLib.idle_add(status.set_label,
                               f"Finished {done} of {len(items)}.")
                GLib.idle_add(bar.set_visible, False)
                GLib.idle_add(goto, "done")

            threading.Thread(target=worker, daemon=True).start()

        go.connect("clicked", run_all)
        on_consent(pending_consent[0])
        body.append(go)
        more = Gtk.Button(label="Add something optional", css_classes=["flat"],
                          halign=Gtk.Align.CENTER)
        more.connect("clicked", lambda *_: goto("extras"))
        body.append(more)
        if extra_entries:
            body.append(wrapping(
                "Optional, if you want them. Each opens its own page and "
                "costs nothing until you pick something on it."))
            extras_on_review = Adw.PreferencesGroup()
            body.append(extras_on_review)
            for title, tag, _page_description, ready in extra_entries:
                # The extras page's own description is a sentence, and a row
                # subtitle cannot wrap (measured: 1034px, and a sideways bar).
                # So the row says what the extra *is* - a few words - and the
                # page keeps the sentence.
                row = Adw.ActionRow(
                    title=title,
                    subtitle=("Ready." if ready
                              else EXTRA_HINTS.get(tag, "not set up")))
                if ready:
                    row.add_prefix(Gtk.Image.new_from_icon_name(
                        "object-select-symbolic"))
                go_extra = Gtk.Button(label="Set up" if not ready else "Review",
                                      valign=Gtk.Align.CENTER)
                go_extra.connect("clicked", lambda _b, t=tag: goto(t))
                row.add_suffix(go_extra)
                row.set_activatable_widget(go_extra)
                extras_on_review.add(row)
        view.add(review)

    def goto(tag: str) -> None:
        """Push a page and keep the back button honest.

        **Not `view.push_by_tag` directly.** `notify::visible-page` is how this
        used to learn the depth, and on libadwaita 1.5 it does not fire in the
        order needed - measured: after pushing the Brain page the back button was
        still hidden, so the one thing it exists for did not work. Going through
        one function makes the depth something this code *sets* rather than
        something it hopes it was told, and the button's state is then testable
        rather than observed once and hoped for.
        """
        if tag in _history:
            _history[:] = _history[:_history.index(tag)]
        else:
            _history.append(tag)
        back.set_visible(len(_history) > 1)
        back.set_tooltip_text("Back" if len(_history) <= 2 else f"Back to {_history[-2]}")
        view.push_by_tag(tag)

    def goto_back_to_list(box) -> None:
        """An extras page's way out: back to the list of everything chosen.

        Extras are optional one at a time, so each returns to the review rather
        than marching on to the next one - taking six unwanted pages after
        saying "I'll have eyes" is how a wizard loses people.
        """
        n = Gtk.Button(label="Back to the list", css_classes=["suggested-action", "pill"],
                       halign=Gtk.Align.CENTER)
        n.connect("clicked", lambda *_: goto("review"))
        page_footer(box).append(n)

    def navigate(box, next_tag, skip_label: str = "") -> "Gtk.Button":
        """Just the way forward: a Next button and a way past the rest.

        Split out from `worker_area` because a page that has nothing to download
        still needs a Next button, and borrowing `worker_area` for it puts a
        dead "Download" button on a page that has nothing to download.

        `next_tag` may be a callable. **A page whose destination depends on an
        answer the person has not given yet cannot bind the destination when it
        is built** - see the Mode page below, where the whole point of the page
        is the question and the question's answer decides where Next goes. The
        callable is resolved on the press, and `retitle()` re-reads it for the
        label, so the button never says one thing and do another.
        """
        def resolve() -> str:
            return next_tag() if callable(next_tag) else next_tag

        def retitle(button: "Gtk.Button") -> None:
            tag = resolve()
            button.set_label(f"Next: {_titles.get(tag, tag)}")

        # **The button says where it goes.** Every page's forward button was
        # "Next", so on a page with a stack of visited-but-alive pages behind it
        # the accessibility tree lists several identical "Next" buttons - and a
        # script clicking "the first Next" lands on the wrong page's, silently
        # doing nothing. That is not only a scripting problem: a person reading
        # "Next" learns nothing about what they are agreeing to, and "Next:
        # Ears" is the answer to that at no cost.
        nxt = Gtk.Button(label="Next", css_classes=["suggested-action", "pill"],
                         halign=Gtk.Align.CENTER)
        nxt.connect("clicked", lambda *_: goto(resolve()))
        page_footer(box).append(nxt)
        retitle(nxt)
        if skip_label:
            skip = Gtk.Button(label=skip_label, css_classes=["flat"], halign=Gtk.Align.CENTER)
            skip.connect("clicked", lambda *_: goto(
                "done" if resolve() != "done" else resolve()))
            page_footer(box).append(skip)
        return nxt

    def pick_another(label: str) -> Gtk.Button:
        """A flat 'download a different one' that reveals the chooser again.

        Kept because 'already installed' must not become 'impossible to change':
        the button re-adds the same chooser and the same worker the page had
        before, so there is one code path for downloading rather than two.
        """
        b = Gtk.Button(label=label, css_classes=["flat"], halign=Gtk.Align.CENTER)
        return b

    # ── where should Chronoa think? ───────────────────────────────────────
    # **This question comes before the model list, not after it.** The whole
    # point of a cloud model is that there is nothing to download, so offering
    # "1.1 GB of Qwen" to someone who intends to use Claude was asking them to
    # pay for the answer to a question they had not been asked. And the two
    # answers have genuinely different consequences - one downloads, the other
    # sends every question to somebody else's computer - so they cannot share a
    # page without one of them being the default, and the default would be the
    # one people did not mean.
    mode = config.get("setup-mode", "") or ("cloud" if config.get_bool(
        "cloud-fallback-enabled", False) else "local")

    def _mode() -> str:
        return config.get("setup-mode", "local") or "local"

    def pick_mode(key: str) -> None:
        config.set("setup-mode", key)
        # **And the setting that actually decides it.** `setup-mode` chose which
        # pages the wizard showed; `answer-mode` is what the running app reads,
        # and until 2026-10-12 nothing here wrote it - so "In the cloud" was a
        # promise about which screens came next while `_init_components` built
        # Ollama and llama.cpp first regardless. A person who chose the cloud and
        # happened to have a local model installed was answered locally, by the
        # download they had just declined.
        # **Writing `answer-mode` alone was not enough, and the dependency was
        # invisible.** `answer-mode` is not what decides whether the cloud is
        # reachable: `privacy-mode` is the lock that gates it, and
        # `_apply_answer_mode` returns early while it is on. So choosing "In the
        # cloud" on a machine where the lock was set left the wizard reporting
        # that it had chosen the cloud while the app answered locally - the same
        # class of defect as the strip chip that read "Cloud" under a local-only
        # rule, one layer down and reached from the wizard instead of the strip.
        #
        # Nothing declared that dependency, so nothing could check it. It is
        # written explicitly here instead, next to the setting that needs it.
        config.set("answer-mode", "cloud" if key == "cloud" else "local-cloud")
        config.set("privacy-mode", False)
        config.set("cloud-fallback-enabled", True)
        unchoose("brain")
        unchoose("ears")

    mode_page, box = page(
        "Mode", "mode",
        "Where should Chronoa do its thinking? You can change this later in "
        "Settings, and both can be set up.")
    # A few words per row, because **libadwaita 1.5 does not wrap its own
    # text**: measured on the installed library, a 130-character
    # `Adw.ActionRow` subtitle is 1,227px wide in a 560px window, and a
    # `PreferencesGroup` description 1,428px. Either one puts a horizontal
    # scrollbar under the page. So the prose lives in a capped `wrapping()`
    # label, which wraps *and* reports a bounded width, and the rows are labels.
    box.append(wrapping(
        "On this computer downloads a language model once "
        f"({local_llm.SPECS[local_llm.recommended()].size_bytes / 1e9:.1f} GB for "
        "the recommended one) and nothing else leaves this machine. In the cloud "
        "downloads nothing and sends every question to the provider you pick, so "
        "it needs an API key."))
    modes = [
        ("local", "On this computer", "nothing leaves the machine"),
        ("cloud", "In the cloud", "nothing to download"),
    ]
    group, chosen_mode = choice_group("Choose one to start with", modes, mode)
    box.append(group)
    pick_mode(chosen_mode())
    # **Where Next goes depends on the answer to this page, so it cannot be
    # decided until the answer is given.** Measured on the real wizard: choosing
    # "In the cloud", then pressing Next, landed on `brain` - the page that
    # offers a 1.1 GB local download - because the destination had been baked
    # in when this page was built, before anyone could toggle anything. The
    # comment above this page says the whole point of asking first is that
    # "offering 1.1 GB of Qwen to someone who intends to use Claude was asking
    # them to pay for the answer to a question they had not been asked"; the
    # question was asked and the answer discarded. Resolved on the press, and
    # re-titled on the toggle so the label never disagrees with the destination.
    def after_mode_picked() -> str:
        return "cloud-keys" if chosen_mode() == "cloud" else "brain"

    mode_next = navigate(box, after_mode_picked, skip_label="Skip for now")
    for _check in _choice_rows[id(group)].values():
        def _on_toggled(_button):
            pick_mode(chosen_mode())
            _dest = after_mode_picked()
            mode_next.set_label(f"Next: {_titles.get(_dest, _dest)}")
        _check.connect("toggled", _on_toggled)
    view.add(mode_page)

    # ── cloud keys ────────────────────────────────────────────────────────
    # Entering a key here rather than in Settings means the person is told, at
    # the moment they are about to send every question somewhere, which is the
    # only moment the sentence matters.
    from shani_chronoa import cloud_llm

    def _load_models(provider_id: str, api_key: str = "") -> "tuple[list[str], str]":
        """`(ids, why)` for one provider, **with the person's key and address**.

        **The key was never sent, and that made three providers unreachable.**
        The page called `offerable_models(provider_id)` with no `api_key`, so
        the default `""` applied, so `list_models` built no `Authorization`
        header - and `cloud_llm.py`'s own docstring records that the keyed
        providers *"reject an unauthenticated request outright"*. Measured
        against each provider's own `base_url`: **openai and groq answer 401
        without a key**, so neither could ever list a model here, on any
        machine, whether a key was saved or not, and the row said the provider's
        own URL "could not be read (HTTP Error 401)" - which reads as *your key
        is wrong or missing* when in fact **no key was ever offered**.

        **And the address, for the one provider whose URL is a setting.**
        `custom` is "your own server on another machine, with a port in it", so
        its `base_url` is whatever the person typed; without passing it here it
        has no URL to fetch and answers "no address has been set" on a machine
        that has one.

        One implementation, so the gear below and the page's own first pass
        cannot disagree about what a provider answers - which is the second
        door this page already had, and the reason a fix here is a fix
        everywhere rather than in the first reader only.
        """
        try:
            return cloud_llm.offerable_models(
                provider_id, api_key=api_key or "",
                base_url=config.get("custom-llm-base-url", ""),
            )
        except Exception as exc:  # noqa: BLE001 - a report, not a crash
            return [], str(exc)

    keys_page, box = page(
        "Cloud keys", "cloud-keys",
        "Chronoa needs a key to talk to a provider on your behalf. Keys are "
        "kept in your desktop keyring when there is one, and never written to "
        "a log. Free gateways further down need no key at all.")
    existing = config.cloud_llm_api_keys()
    key_rows = Adw.PreferencesGroup(title="Providers")
    box.append(key_rows)
    entered: Dict[str, str] = {}
    for provider_id in cloud_llm.BYOK_PROVIDER_ORDER:
        provider = cloud_llm.PROVIDERS.get(provider_id)
        if provider is None:
            continue
        entry = Gtk.Entry(text=existing.get(provider_id, ""),
                          placeholder_text="paste your key", hexpand=True)
        entry.set_visibility(False)          # a key is not shoulder-surfed
        entry.update_property([Gtk.AccessibleProperty.LABEL],
                              [f"{provider.name} API key"])
        entry.connect("changed", lambda e, pid=provider_id: entered.__setitem__(pid, e.get_text()))
        from shani_chronoa import cloud_voice as _cv
        # What this key unlocks, so someone choosing the cloud can see which
        # provider will also hear and speak - not only which one can chat.
        row = Adw.ActionRow(title=provider.name,
                            subtitle=("a key is already saved" if existing.get(provider_id)
                                      else "needs a key") + " - " + ", ".join(_cv.abilities(provider_id)))
        row.add_suffix(entry)
        row.set_activatable_widget(entry)
        key_rows.add(row)
    free = Adw.PreferencesGroup(
        title="No key needed",
        description=", ".join(cloud_llm.PROVIDERS[p].name
                              for p in cloud_llm.DEFAULT_PROVIDER_ORDER if p in cloud_llm.PROVIDERS))
    box.append(free)

    # ── which model answers ─────────────────────────────────────────────
    # **The page named the provider and never the model.** Someone choosing
    # "Anthropic" was told which providers can also hear and speak, and nothing
    # about *what would answer* - and every provider has one model, chosen by us
    # and changeable by nobody. The chain has read an `anthropic_model` override
    # since it was written and ignored the other eight, so the capability was
    # half-present and entirely unreachable.
    #
    # Measured 2026-10-12: every provider here speaks the OpenAI wire and answers
    # `GET /models` with its own list - **64 for llm7, 115 for BlockRun, 83 for
    # OpenCode Zen, 390 for Kilo, 458 for OpenRouter** - and five of the eight do
    # it with no key at all, including all three free gateways. So the list is
    # real, it is live, and it is read in a thread: nine providers at up to 12s
    # each would freeze the window for a minute and a half.
    models = Adw.PreferencesGroup(
        title="Which model answers",
        description="Read from each provider as you look at this page. Empty "
                    "means that provider's own default, so nothing here has to "
                    "be touched.",
    )
    box.append(models)
    pickers: Dict[str, Gtk.DropDown] = {}
    key_editors: Dict[str, tuple] = {}
    # Per-provider handles the fill thread needs on the main loop. **Dicts,
    # not closure defaults**: the refusal callback is queued with
    # `GLib.idle_add` and runs after this loop has finished, so a name it
    # captured would be the *last* provider's row - which is the
    # `key = ...`-in-a-loop bug this page has already paid for once.
    note_for: Dict[str, Gtk.Label] = {}
    row_for: Dict[str, Adw.ActionRow] = {}
    words_for: Dict[str, str] = {}
    # **"Your own server" is named in neither tuple**, which is correct - it is
    # not a BYOK-required provider and not one of the free gateways, and
    # `app/brain.py` prepends it on its own when an address has been set. Which
    # means a loop over those two tuples builds **no row for it at all**: the
    # same defect anthropic had, one level down. Measured, this page built nine
    # provider rows out of the ten it can configure, and the missing one was the
    # only one needing an address, a model *and* a key.
    for provider_id in (list(cloud_llm.BYOK_PROVIDER_ORDER)
                        + list(cloud_llm.DEFAULT_PROVIDER_ORDER)
                        + [cloud_llm.CUSTOM_ID]):
        provider = cloud_llm.PROVIDERS.get(provider_id)
        if provider is None:
            # **This branch is now unreachable, and it used to be a silent
            # deletion.** `anthropic` was in `BYOK_PROVIDER_ORDER` and not in
            # `PROVIDERS`, so this line dropped it - no row, no key, no model
            # choice, and nothing saying it was absent - while `app/brain.py`
            # built an `AnthropicLLM` from the key the moment one was saved.
            # Both lists are named in one place now and a provider in one and
            # not the other is a defect rather than a shrug, so the refusal
            # says which id rather than continuing past it.
            logger.warning("wizard cloud-keys: %s is ordered but not "
                           "registered, so it has no row", provider_id)
            continue
        is_custom = provider_id == cloud_llm.CUSTOM_ID
        if is_custom:
            name, model_word = "Your own server", "a model the server lists"
        else:
            name, model_word = provider.name, provider.default_model
        row = Adw.ActionRow(
            title=name,
            subtitle=f"answers with {model_word} until you pick another")
        picked = Gtk.DropDown.new_from_strings(["reading the list..."])
        picked.set_valign(Gtk.Align.CENTER)
        picked.set_sensitive(False)
        picked.update_property([Gtk.AccessibleProperty.LABEL],
                               [f"Model for {name}"])

        # ── the key, in the row that needs it ────────────────────────
        # **A gear on every provider, because the key and the model are one
        # decision about one provider and this page made them two.** The key
        # lives in the "Providers" group above and the model in this one, so
        # the moment you discover you needed a key - the dropdown is disabled
        # and says so - the control that fixes it is in a different part of
        # the page. The gear opens it *here*, on the row that turned it off.
        #
        # **`preferences-system-symbolic` because it is measured present in
        # Adwaita on this machine** - the rule this file has been bitten by
        # three times, each time by a plausible name that is Yaru-only and
        # draws an empty box that reads as a broken control. It is also the
        # icon `_AMBIENT`'s own table already uses on the mode strip, so it is
        # known to resolve here rather than assumed to.
        gear = Gtk.Button(icon_name="preferences-system-symbolic",
                          valign=Gtk.Align.CENTER)
        gear.add_css_class("flat")
        gear.set_tooltip_text(
            f"Set up {name}."
            + (" Its address, the model it lists and an optional key."
               if is_custom else
               (" A key is mandatory here."
                if provider.requires_key else
                " It works without one, at a lower rate limit.")))
        gear.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"Set up {name}"])

        fields: list[tuple] = []
        if is_custom:
            # **Three fields, because your own server is three settings and
            # one of them is an address.** A gear that revealed only a key
            # would leave the two this provider actually needs on another page
            # - and the address is the one carrying the host and the port.
            for label, key, placeholder in (
                    ("Server address", "custom-llm-base-url",
                     "http://192.168.1.20:8080/v1"),
                    ("Model name", "custom-llm-model", "as the server lists it")):
                field = Gtk.Entry(hexpand=True, placeholder_text=placeholder)
                field.set_text(config.get(key, ""))
                field.update_property([Gtk.AccessibleProperty.LABEL],
                                      [f"{name} {label}"])
                fields.append((label, key, field))
        key_field = Gtk.Entry(hexpand=True, placeholder_text="paste your key")
        key_field.set_visibility(False)     # a key is not shoulder-surfed
        key_field.update_property([Gtk.AccessibleProperty.LABEL],
                                  [f"{name} API key"])
        fields.append(("API key", API_KEY_SETTINGS
                       .get(provider_id, f"{provider_id}-api-key"), key_field))
        entered.setdefault(provider_id, "")

        key_note = wrapping("")
        key_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for label, key, field in fields:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            caption = Gtk.Label(label=label, xalign=0)
            caption.add_css_class("dim-label")
            line.append(caption)
            line.append(field)
            key_box.append(line)
        key_box.append(key_note)
        key_box.set_visible(False)         # hidden until the gear is pressed

        suffix = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        suffix.set_valign(Gtk.Align.CENTER)
        suffix.append(picked)
        suffix.append(key_box)
        row.add_suffix(suffix)
        row.add_prefix(gear)
        row.set_activatable_widget(picked)
        models.add(row)
        pickers[provider_id] = picked

        def show_key(*_a, pid=provider_id, box_=key_box, btn=gear,
                     row_=row, nm=name, word=model_word) -> None:
            """Open the editor in this row. **Toggles**, so the same control
            closes it - a gear that only ever opens leaves no way back."""
            opening = not box_.get_visible()
            box_.set_visible(opening)
            btn.set_icon_name("window-close-symbolic" if opening
                              else "preferences-system-symbolic")
            btn.set_tooltip_text(f"Close the {nm} settings" if opening
                                 else f"Set up {nm}.")
            row_.set_subtitle(
                "address, model and key are below" if opening else
                f"answers with {word} until you pick another")
            if opening:
                fields[0][2].grab_focus()

        gear.connect("clicked", show_key)
        key_editors[provider_id] = (fields, key_note, key_box)
        note_for[provider_id] = key_note
        row_for[provider_id] = row
        words_for[provider_id] = model_word

    model_status = wrapping("")
    box.append(model_status)

    def fill() -> None:
        """One thread walks the providers so the window never blocks."""
        import threading
        from gi.repository import GLib

        def work() -> None:
            from shani_chronoa import cloud_llm as _cl
            from shani_chronoa.config import ChronoaConfig as _Config

            saved = _Config().cloud_llm_models()
            # **The keys, so the keyed providers are asked as themselves.**
            # Reading them here rather than closing over the page's `existing`
            # is deliberate: this thread is started before anything typed on
            # this page is saved, and it must describe the machine as it is,
            # not as this page hopes it will be.
            have = _Config().cloud_llm_api_keys()
            listed, refused = 0, []
            for provider_id, picked in pickers.items():
                ids, why = _load_models(provider_id,
                                        api_key=have.get(provider_id, ""))
                if not ids:
                    # **The reason is kept, not just the fact.** A dropdown left
                    # disabled with nothing said reads as "this provider has no
                    # models"; `why` says which of the two it actually was -
                    # a 401 because the key is missing, or nothing reachable.
                    refused.append((provider_id, why))
                    def leave_off(picked=picked, note=note_for[provider_id],
                                  row=row_for[provider_id],
                                  word=words_for[provider_id]) -> bool:
                        picked.set_sensitive(False)
                        # **The reason on the row that turned the control
                        # off.** It used to reach only the summary label at the
                        # bottom of the page, so a disabled dropdown explained
                        # itself only to somebody who scrolled past it - and a
                        # dropdown that is off with no reason reads as a bug
                        # rather than as a provider that could not be asked.
                        note.set_label(why)
                        row.set_subtitle(
                            f"{why} - answering with {word} in the meantime")
                        return False
                    GLib.idle_add(leave_off)
                    continue
                listed += 1
                default = _cl.PROVIDERS[provider_id].default_model
                # **Your own server has no default model in the registry** -
                # it is whatever the person typed, on a machine nobody else
                # can see - so an empty one is the normal answer, not a
                # missing one. Inserting it would put a **blank option** at the
                # top of a dropdown, which is a control that looks broken
                # rather than one that has nothing to show.
                current = saved.get(provider_id) or default
                if current and current not in ids:
                    ids.insert(0, current)

                def apply(provider_id=provider_id, picked=picked, ids=ids,
                          current=current) -> bool:
                    picked.set_sensitive(True)
                    picked.set_model(Gtk.StringList.new(ids))
                    picked.set_selected(ids.index(current))
                    return False

                GLib.idle_add(apply)

            def said() -> bool:
                if refused:
                    names = ", ".join(_cl.PROVIDERS[pid].name for pid, _ in refused)
                    first = refused[0][1]
                    model_status.set_label(
                        f"{listed} provider(s) answered with a model list. "
                        f"These keep their own default: {names} - for example "
                        f"{names.split(', ')[0]}: {first}")
                elif listed:
                    model_status.set_label(
                        f"{listed} provider(s) answered with a model list.")
                else:
                    model_status.set_label(
                        "No provider answered with a model list - nothing was "
                        "reachable from here, so each one keeps its default.")
                return False

            GLib.idle_add(said)

        threading.Thread(target=work, daemon=True).start()

    def remember_models() -> None:
        """Write whatever is selected. **Called by Next as well as Save**, for
        the same reason the keys are: Next is what people press."""
        for provider_id, picked in pickers.items():
            if not picked.get_sensitive():
                continue
            item = picked.get_selected_item()
            if item is None:
                continue
            config.set_cloud_model(provider_id, item.get_string())

    fill()

    # ── the gear actually does something ─────────────────────────────
    # Saving the key re-asks that one provider, on a thread, and fills its
    # dropdown. **Without this the gear is decoration**: the key would be
    # stored and the row would stay disabled until the page was rebuilt, so
    # the person would save a key and watch the one thing that key was for
    # stay greyed out - which reads as "the key did not work" rather than as
    # "nothing has looked again yet".
    #
    # **One provider, not all nine.** This is the same walk as the page's
    # first pass, so it is exactly what would happen to have been re-run; the
    # difference is that it is cheap enough to do the moment a person is
    # waiting for it.
    def save_one_key(provider_id: str) -> None:
        fields, note, _box = key_editors[provider_id]
        setting = API_KEY_SETTINGS.get(provider_id,
                                                 f"{provider_id}-api-key")
        by_key = {key: field for _label, key, field in fields}
        # **The key is the only field that may be left blank.** Your own server
        # is configured by its address and its model, and its key is explicitly
        # optional there ("only if the server asks for one"), so refusing to save
        # without one would make the single provider that needs no key the
        # single provider that cannot be configured. Every *other* field is
        # required, because a blank address is not a server.
        key_value = (by_key[setting].get_text().strip()
                     if setting in by_key else "")
        missing = [label for label, key, field in fields
                   if key != setting and not field.get_text().strip()]
        if missing:
            note.set_label(f"{', '.join(missing)} is empty, so nothing was "
                           "saved.")
            return
        for _label, key, field in fields:
            value = field.get_text().strip()
            if key in API_KEY_SETTINGS.values():
                if not value:
                    # **An empty key must not overwrite a working one.** The
                    # remedy for a mistyped key is to type it again, not to
                    # lose the one that already worked.
                    continue
                config.set_api_key(key, value)
                entered[provider_id] = value
            else:
                config.set(key, value)
        picked = pickers[provider_id]
        note.set_label("Saved. Reading this provider's models again...")
        picked.set_model(Gtk.StringList.new(["reading the list..."]))
        picked.set_sensitive(False)

        def work() -> None:
            from gi.repository import GLib

            ids, why = _load_models(provider_id, api_key=key_value)
            default = cloud_llm.PROVIDERS[provider_id].default_model
            if ids and default and default in ids:
                ids.remove(default)
            if ids:
                ids.insert(0, default or ids[0])
            current = config.cloud_llm_models().get(provider_id) or default

            def apply() -> bool:
                if not ids:
                    # **The reason, not just the fact.** "This provider still
                    # cannot be reached" and "your key was refused" are
                    # different problems with different fixes, and the sentence
                    # the fetch returns is the only thing that knows which.
                    note.set_label(
                        f"The key was saved, but {why or 'that provider'}"
                        " still gave no model list.")
                    picked.set_model(Gtk.StringList.new([default]))
                    picked.set_selected(0)
                    picked.set_sensitive(False)
                    return False
                note.set_label(f"Saved. {len(ids)} model(s) offered; "
                               f"{default or 'the first'} is the default.")
                picked.set_model(Gtk.StringList.new(ids))
                picked.set_selected(
                    ids.index(current) if current in ids else 0)
                picked.set_sensitive(True)
                return False

            GLib.idle_add(apply)

        threading.Thread(target=work, daemon=True).start()

    for _pid, (_fields, _note, _box) in key_editors.items():
        # **Enter saves.** A key field with no way to commit it is the
        # `Gtk.TextView`-in-a-`Gtk.Entry` shape: the value is on screen and
        # nothing has it. `activate` is the signal an `Entry` emits for
        # Enter, and it is distinct from the row's own activation, so the
        # gear's toggle is not stolen by it.
        for _lbl, _k, _fld in _fields:
            _fld.connect("activate",
                         lambda _e, pid=_pid: save_one_key(pid))

    # A label of its own, not part of the description above: appended there it
    # widened the page to 590px of 560 and gave it a sideways scrollbar.
    box.append(wrapping("Those are chat only: no provider listens or speaks without a key."))
    key_status = wrapping("")
    box.append(key_status)
    # Not `suggested-action`: Next is the page's one primary action, and two
    # orange pills stacked read as "press both" with no way to tell which
    # moves on. Next saves what was typed as well (below), so this button is
    # for someone who wants the confirmation sentence before moving on.
    save_keys = Gtk.Button(label="Save these keys", css_classes=["pill"],
                           halign=Gtk.Align.CENTER)

    def save() -> None:
        for provider_id, value in entered.items():
            config.set_api_key(API_KEY_SETTINGS[provider_id], value)
        typed = sum(1 for v in entered.values() if v)
        key_status.set_label(
            f"Saved {typed} key(s). Chronoa will "
            f"try {', '.join(p for p, v in entered.items() if v) or 'the free gateways'} "
            "when it needs to think.")
        remember_models()
        _consent_given(config, True)          # asking a cloud model is consent
        # **And now it says whether any of them work.**
        # This page used to end at the sentence above: the key was saved and that
        # was the whole of the feedback. An expired, revoked or wrong-product key
        # is indistinguishable from a working one until a turn *fails* - and by
        # then the question has been asked, miles from this page. Measured on the
        # real page during this change: pressing "Save these keys" with a key
        # typed said "Saved 1 key(s)..." and nothing about whether it works.
        #
        # Off the main loop: nine providers at up to 20 s each would otherwise
        # freeze the window for minutes.
        if not typed:
            key_status.set_label(key_status.get_label()
                                 + " No key was entered, so there is nothing to test.")
            return
        key_status.set_label(key_status.get_label() + " Testing the keys...")

        def work() -> None:
            from shani_chronoa import cloud_llm

            try:
                found = cloud_llm.probe_keys()
                said = cloud_llm.keys_sentence(found)
            except Exception as exc:  # noqa: BLE001 - a failed probe is a report
                said = "Could not test them: %s" % (exc,)

            def done() -> None:
                from gi.repository import GLib

                GLib.idle_add(lambda: (key_status.set_label(said), None)[1])

            import threading

            threading.Thread(target=done, daemon=True).start()

        import threading

        threading.Thread(target=work, daemon=True).start()

    save_keys.connect("clicked", lambda *_: save())
    page_footer(box).append(save_keys)
    # **On to the Ears, not to Done.** This used to be `navigate(box, "done")`,
    # and it was the single worst routing in the wizard: the cloud branch is the
    # one branch where nothing was downloaded, so routing straight to the end
    # produced a Chronoa that could think and **could not hear**, with
    # `setup-complete` set and `needs_setup()` returning False forever after.
    #
    # Measured by walking the real wizard: `mode -> In the cloud -> cloud-keys ->
    # Next -> done`, and `on_finish` writes `setup-complete=true` unconditionally.
    # So "nothing to download" was literally true and practically misleading - it
    # was true because the wizard had stopped asking. The Ears page says what
    # listening actually costs, whichever way this branch got here.
    keys_next = navigate(box, "ears", skip_label="Skip for now")
    # A key typed and then left behind by pressing Next was silently dropped:
    # only the Save button wrote it. Next is what people press.
    keys_next.connect("clicked", lambda *_: (save() if any(entered.values()) else None,
                                             remember_models())[1])
    view.add(keys_page)

    # ── where should Chronoa think? ─
    # 2. brain
    b = s["brain"]
    hw = (f"This computer has {b['ram_gb']} GB of memory and "
          + (f"a graphics card llama.cpp can use ({b['gpu'][0].split(':', 1)[-1].strip()[:60]})." if b["gpu"]
             else "no graphics card llama.cpp can use, so the model runs on the processor."))
    # The chooser is its own page, because the question "what is answering now?"
    # and the question "which model?" are different questions: with a cloud key
    # already working, the first is answered and the second is optional. Asking
    # for 1.1 GB from someone who chose the cloud is the mistake; so the status
    # page is the default and the chooser is one click away.
    brain, box = page("Brain", "brain", "Chronoa needs something that understands you.")
    if b["cloud"].get("available"):
        box.append(wrapping(
            f"{b['cloud']['provider']} is configured and will answer "
            f"({b['cloud']['model']}), so nothing needs downloading."))
    elif b["ollama"]:
        box.append(wrapping(
            "Ollama is running on this computer, so Chronoa uses it - "
            "nothing to download."))
    elif b["up"]:
        # Only `active` is interpolated, in the concatenation below, so an
        # f-string here has no placeholder of its own - which pyflakes names.
        # Pre-existing at HEAD; cleared here because this file is being touched.
        box.append(wrapping(
            "llama-server is answering on this computer"
            + (f" with {b['active']}" if b.get("active") else "")
            + ", so nothing needs downloading."))
    elif b.get("installed"):
        # **"Downloaded" and "answering" are different questions, and this page
        # asked only the second while presenting its answer as the first.**
        # Measured on a real machine with 1.1 GB and 5 GB of models on disk and
        # `llama-server` stopped: the page read "Nothing is set up to answer
        # yet." - which sent someone to the download flow for models already
        # sitting in their home directory. `is_up()` is the service; `installed()`
        # is the disk, and the branch above never looked at the disk.
        names = ", ".join(b["installed"])
        box.append(wrapping(
            f"{len(b['installed'])} model(s) are already on this computer "
            f"({names}). Nothing is answering yet, because the local model "
            f"service is not running."))
        start = Gtk.Button(label="Start the model service",
                           css_classes=["pill"], halign=Gtk.Align.START)

        def _start(*_a):
            start.set_sensitive(False)
            start.set_label("Starting...")
            import threading

            def work():
                from shani_chronoa import local_llm

                problem = local_llm.start_service()

                def done():
                    start.set_sensitive(True)
                    start.set_label(
                        "Start the model service" if problem
                        else "Started - the model is answering")
                    if problem:
                        started.set_text("Could not start it: %s" % problem)
                    return False
                from gi.repository import GLib

                GLib.idle_add(done)

            threading.Thread(target=work, daemon=True).start()

        start.connect("clicked", _start)
        started = wrapping("")
        started.add_css_class("dim-label")
        # **`page_footer(box)`, not `box`.** The pinned row under the scroller is
        # where a page's action belongs - which is where `Next` already goes on
        # every page. Appended to `box`, this one sits *inside* the scroller, so
        # on a tall page the only control on it slides out of reach; measured,
        # by `test_surfaces_actually_render.py`, which walks every wizard page
        # for actions inside the scroller.
        page_footer(box).append(start)
        page_footer(box).append(started)
    else:
        box.append(wrapping("Nothing is set up to answer yet."))
    n = Gtk.Button(label=f"Next: {_titles.get('ears', 'Ears')}",
                   css_classes=["suggested-action", "pill"],
                   halign=Gtk.Align.CENTER)
    n.connect("clicked", lambda *_: goto("ears"))
    page_footer(box).append(n)
    # **Visibly a control, not a bare line of text.** This was a `flat` button,
    # which in a dark theme renders as unstyled words - so it sat underneath the
    # Next button looking exactly like a dead label, which is the "minor UI glitch
    # during model installation" that got reported. The control has been wired the
    # whole time (it goes to the model picker); the affordance was missing, and
    # the chevron is what makes one.
    local_button = Gtk.Button(css_classes=["flat", "jump-link"],
                              halign=Gtk.Align.CENTER)
    local_button.set_child(_jump_label("Use a model on this computer"))
    local_button.set_tooltip_text("Pick which language model this computer uses")
    local_button.connect("clicked", lambda *_: goto("model-picker"))
    page_footer(box).append(local_button)
    view.add(brain)

    picker, box = page("Model", "model-picker", "The language model is what understands you. " + hw)
    if True:
        items = [(spec.key, label + (" - recommended" if spec.key == b["recommended"] else ""),
                  ("downloaded" if spec.key in b["installed"] else f"{spec.size_bytes / 1e9:.1f} GB download"))
                 for spec, label, _ram in local_llm.TIERS]
        group, chosen = choice_group("Choose a model", items, b["active"] or b["recommended"])
        # Present, but not the first thing offered: a verified model already on
        # disk is the answer, and `local_llm.verify` is the check that makes
        # "already there" mean "already there and intact" rather than "a file
        # with the right name".
        if b["active"] and b["active"] in b["installed"] and local_llm.verify(b["active"]):
            spec = local_llm.SPECS[b["active"]]
            # The tier's own label, so the sentence reads exactly like the row
            # the user would have clicked ("Medium (1.1 GB) - the best fit...").
            label = next((text for s, text, _ram in local_llm.TIERS
                          if s.key == b["active"]), b["active"])
            already_there(
                f"{label.split(' - ')[0]} is already downloaded and checked "
                f"({spec.size_bytes / 1e9:.1f} GB). Chronoa will use it.",
                "ears",
                ("The server is already answering." if b["up"] else
                 "It will start when you continue."))
            again = pick_another("Choose a different model")
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            holder.append(group)
            again.connect("clicked", lambda *_: (
                box.remove(again), box.append(holder),
                worker_area(holder, "Download and start",
                            lambda report: setup_brain(chosen(), report, win.cancel, config), "ears")))
            box.append(again)
        else:
            box.append(group)
            # Recorded, not downloaded. The review page at the end downloads it,
            # so the person sees the whole list and the total before anything
            # moves.
            _spec = local_llm.SPECS[chosen()]
            choose("brain", f"Language model - {local_llm.SPECS[chosen()].key}",
                   with_engine("llama-server", _spec.size_bytes),
                   lambda report: setup_brain(chosen(), report, win.cancel, config),
                   None)
            _download_buttons.clear()
            navigate(box, "ears", skip_label="Skip for now")
    view.add(picker)

    def _cloud_speech_switch(title: str, key: str, route, what: str) -> Gtk.Widget:
        """A switch for cloud listening or speaking, naming who can do it.

        Shown on the cloud path's Ears and Voice pages, which used to say only
        that cloud speech "needs a switch in Settings and an API key" without
        naming the providers or offering the switch.
        """
        from shani_chronoa import cloud_voice as _cv
        names = ", ".join(_cv.provider_names(route))
        keyed = [p for p in route if config.cloud_llm_api_keys().get(p)]
        group = Adw.PreferencesGroup()
        row = Adw.SwitchRow(title=title, subtitle=(
            f"{what}. Can do it: {names}. "
            + ("You have a key for one of them." if keyed else
               "Needs a key for one of them - add it on the Cloud keys page.")))
        row.set_active(config.get_bool(key, False))
        row.connect("notify::active", lambda r, _p: config.set(key, "true" if r.get_active() else "false"))
        group.add(row)
        return group

    # 3. ears
    e = s["ears"]
    ears, box = page("Ears", "ears", "To understand what you say, Chronoa needs to hear you.")
    from shani_chronoa import cloud_voice as _cloud_voice
    cloud_stt_ok = _cloud_voice.CloudSTT().is_available()
    if _mode() == "cloud":
        # **This page used to show nothing and still charge 60 MB.**
        #
        # In cloud mode the model list was built as `[]`, so the choice group was
        # empty - and then the same `else` branch ran anyway and queued the
        # recommended model's download. Measured on the real wizard with
        # `setup-mode=cloud`: the Ears page listed **zero rows**, and the Review
        # page then read **"Download 3 thing(s) ... 1.3 GB across 3
        # download(s)"**, one of which was a 60 MB whisper model the person had
        # no row to see, choose or decline.
        #
        # So the honest answer for someone who chose the cloud is stated rather
        # than implied: **there is no cloud speech recognition unless you turn it
        # on and have a key.** Both the choice and its cost are on screen.
        if cloud_stt_ok:
            box.append(wrapping(
                "Cloud speech recognition is on: your recordings are uploaded to "
                f"{_cloud_voice.CloudSTT().last_provider or 'a provider'} to be "
                "transcribed, and never downloaded locally. Turn it off in "
                "Settings, and privacy mode turns it off on its own."))
        else:
            box.append(wrapping(
                "There is no cloud speech recognition switched on. Your voice has "
                "to be heard on this computer, which needs a model downloaded - "
                "the choices and their sizes are below. No provider accepts an "
                "anonymous recording, so a cloud alternative needs both a switch "
                "and an API key."))
        box.append(_cloud_speech_switch(
            "Listen in the cloud", _cloud_voice.STT_SWITCH, _cloud_voice.STT_ROUTES,
            "Your recordings are uploaded to be transcribed"))
    items = [(k, f"{k.split('-')[0].title()}" + (" - recommended" if k == e["recommended"] else ""),
              f"{spec.size_bytes / 1e6:.0f} MB - {spec.note}") for k, spec in stt_provision.MODELS.items()]
    group, chosen_stt = choice_group("Choose how well it listens", items, e["recommended"])
    if e["model"]:
        have = [k for k in stt_provision.MODELS if stt_provision.is_provisioned(k)]
        already_there(
            "A speech model is already downloaded and checked ("
            + ", ".join(have) + "). Chronoa will use it.", "voice",
            "Listening will start when you continue.")
        again = pick_another("Choose a different one")
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        holder.append(group)
        again.connect("clicked", lambda *_: (
            box.remove(again), box.append(holder),
            worker_area(holder, "Download",
                        lambda report: setup_ears(chosen_stt(), report, win.cancel, config), "voice")))
        box.append(again)
    elif cloud_stt_ok:
        # Cloud recognition is live, so **nothing is downloaded for ears** - and
        # saying so is the point. The model list is still there for somebody who
        # would rather listen locally, and picking it queues the download as
        # usual.
        box.append(group)
        unchoose("ears")
        navigate(box, "voice", skip_label="Skip for now")
    else:
        box.append(group)
        _espec = stt_provision.MODELS[chosen_stt()]
        choose("ears", f"Listening - whisper.cpp {chosen_stt()}",
               with_engine("whisper-cli", _espec.size_bytes),
               lambda report: setup_ears(chosen_stt(), report, win.cancel, config),
               _espec.note)
        _download_buttons.clear()
        navigate(box, "voice", skip_label="Skip for now")
    view.add(ears)

    # 4. voice: one list of voices; each brings the engine that speaks it
    v, ko = s["voice"], s["kokoro"]
    voice_page, box = page("Voice", "voice", "Pick the voice Chronoa answers with. Piper voices start "
                                            "speaking quickly; Kokoro voices sound most like a person but, on a "
                                            "processor, each reply starts a moment later.")
    # **Say that a voice already exists before asking for 90 MB of one.** The
    # basic voice is espeak-ng, which is a hard package dependency, so it is on
    # every install; and cloud synthesis is the *last* link of the chain, so it
    # can never be the reason to skip a local one. Both are true whichever branch
    # this page takes, and neither is visible from a list of voice downloads.
    from shani_chronoa import cloud_voice as _cloud_voice_tts
    if shutil.which("espeak-ng"):
        box.append(wrapping(
            "Chronoa already has a basic voice on this machine (espeak-ng), so "
            "you can skip all of this and still be answered out loud. These "
            "choices are about sounding more like a person."))
    if _cloud_voice_tts.CloudTTS().is_available():
        box.append(wrapping(
            "Cloud speech synthesis is on, and it is the last resort - it is "
            "used only if none of the local engines can speak, so turning it on "
            "does not replace any of these."))
    if _mode() == "cloud":
        box.append(_cloud_speech_switch(
            "Speak from the cloud", _cloud_voice_tts.TTS_SWITCH, _cloud_voice_tts.TTS_ROUTES,
            "Replies are sent to be spoken, only when no voice on this computer can"))
    piper_mb = voices._PIPER.size_bytes / 1e6
    kokoro_mb = (sherpa.RELEASE.size_bytes + voices._KOKORO_MODEL.size_bytes) / 1e6
    # `voices_for("en")` rather than filtering `VOICES` here: which voices may be
    # offered is a fact about the catalogue, and the wizard was the one place
    # that re-derived it, so a catalogue change that added a language would show
    # up in one list and not the other.
    items = [(k, f"{voices.VOICES[k].label.split(' - ')[0]} (Piper)",
              voices.VOICES[k].label.split(" - ")[1] +
              (" - downloaded" if voices.voice_installed(k)
               else f" - {voices.VOICES[k].onnx_size / 1e6:.0f} MB, plus {piper_mb:.0f} MB for Piper once"))
             for k in voices.voices_for("en")]
    items += [(k, f"{x.label.split(' - ')[0]} (Kokoro)",
               x.label.split(" - ")[1] + (" - downloaded" if voices.kokoro_installed(k)
                                          else f" - {kokoro_mb:.0f} MB once, for all six Kokoro voices"))
              for k, x in voices.KOKORO_VOICES.items()]
    current = ko["voice"] if ko["enabled"] and ko["voice"] in voices.KOKORO_VOICES else v["chosen"]
    group, chosen_voice = choice_group("Voices", items, current)
    box.append(group)
    # "extras", not "more": the single clubbed extras page is now an index, and
    # this is the only thing that points at it. Leaving it on the old tag would
    # make the voice page's skip button push a tag no page answers to, which
    # Adw.NavigationView ignores silently - the run then sits on a page that
    # cannot move on, with no error anywhere.
    # **Piper and Kokoro are both ways to speak**, so they belong on one page
    # with the voice choice under them - not as two rows a person has to decode.
    # The size follows the selection: Kokoro's engine and model are one download
    # shared by all six of its voices, so ticking Sarah costs the same 131 MB as
    # ticking Emma, and the review page says so.
    def voice_size(key: str) -> float:
        if key in voices.KOKORO_VOICES:
            return sherpa.RELEASE.size_bytes + voices._KOKORO_MODEL.size_bytes
        spec = voices.VOICES.get(key)
        return voices._PIPER.size_bytes + (spec.onnx_size if spec else 0)

    def voice_label(key: str) -> str:
        entry = voices.KOKORO_VOICES.get(key) or voices.VOICES.get(key)
        name = entry.label.split(" - ")[0] if entry else key
        engine = "Kokoro" if key in voices.KOKORO_VOICES else "Piper"
        return f"Speaking - {name} ({engine})"

    def record_voice(_b=None) -> None:
        key = chosen_voice()
        choose("voice", voice_label(key), voice_size(key),
               lambda report: setup_voice(key, report, win.cancel, config),
               "shared by all six")

    # Seed the plan, and keep it in step with the radio buttons. Listening to each
    # check rather than to the group means switching from Piper to Kokoro
    # re-records the *size* too, which is the number the review page totals.
    for _check in _choice_rows[id(group)].values():
        _check.connect("toggled", lambda *_a: record_voice())
    record_voice()
    _download_buttons.clear()
    # The status line belongs to the Listen preview now, not to a download this
    # page no longer performs - it says what the preview is doing, or why it
    # cannot.
    status = wrapping("")
    box.append(status)
    listen = Gtk.Button(label="Listen to this voice", css_classes=["pill"],
                        halign=Gtk.Align.CENTER)

    def on_listen(*_):
        from shani_chronoa.tts import PiperTTS
        voice = chosen_voice()
        kokoro = voice in voices.KOKORO_VOICES
        ready = voices.kokoro_installed(voice) if kokoro else (voices.voice_installed(voice) and voices.piper_binary())
        if not ready:
            status.set_label("Download this voice from the review page first, "
                             "then come back and listen.")
            return
        listen.set_sensitive(False)

        def speak():
            try:
                if kokoro:
                    tts = PiperTTS(config=config)
                    tts.kokoro_trial_voice = voice  # this choice, before it is saved
                else:
                    tts = PiperTTS(voice=voice, config=config)
                    tts.piper_trial = True
                audio = tts.synthesize_to_bytes(sample_sentence())
                from shani_chronoa.audio import AudioPlayer
                if audio:
                    AudioPlayer().play_bytes(audio)
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(status.set_label, f"Could not play it: {exc}")
            GLib.idle_add(listen.set_sensitive, True)
        threading.Thread(target=speak, daemon=True).start()
    listen.connect("clicked", on_listen)
    page_footer(box).append(listen)
    # The voice page's own way forward, into the extras index. Its `worker_area`
    # skip already targets "more", which is now `extras` - see `worker_area`'s
    # `next_tag` above - so this is the path a user takes after picking a voice.
    # Straight to the review, not to a page of optional extras. The shortest
    # honest path is: choose what you want to think with, hear you and speak,
    # then see the whole list and its total. An extras page in the middle of
    # that is a gate the person did not ask for - and the review page can offer
    # them without hiding them.
    to_review = Gtk.Button(label=f"Next: {_titles.get('review', 'Review')}",
                           css_classes=["suggested-action", "pill"],
                           halign=Gtk.Align.CENTER)
    to_review.connect("clicked", lambda *_: goto("review"))
    page_footer(box).append(to_review)
    view.add(voice_page)

    # 5. the optional extras, **one page each**.
    #
    # They were seven sections stacked on one page, and the page was ~9300px
    # tall: Eyes, Imagine, Memory, Photos and videos, Sounds, Who said what and
    # Languages, all reached by scrolling one very long list in which a missing
    # 2.0 GB download sat next to a 90 MB one with nothing to say which was
    # which. Nobody reads that, and the sections a person wants are lost among
    # the ones they do not. Each is now its own page with its own title, its own
    # button and its own back button.
    #
    # **There is no hub page.** It existed only to list the other pages, so
    # reaching Eyes meant going through "Optional extras" first - a page about a
    # page, with a "take none of these" button to escape it. The review page
    # carries one row per extra instead, so each is an entry rather than a
    # chapter in a list.
    ey, im, la = s["eyes"], s["imagine"], s["languages"]
    #: The optional extras in the order they were built, read by the review page.
    EXTRAS: List[Tuple[str, str, str, str]] = []

    def extras_page(title: str, tag: str, subtitle: str, next_tag: str):
        """One extra, on its own page, with a way back to the list.

        **Navigation only.** An earlier version called `worker_area` here to get
        the Next/Skip pair and passed a `lambda report: None` as the work, which
        put a working-looking "Download" button on all seven extras pages that did
        nothing at all - and then the caller added the *real* `worker_area`
        below it, so each page carried two Download buttons and the first was a
        lie. `navigate` and `goto_back_to_list` exist so there is one way to add
        a way forward and neither can bring a button with it.
        """
        p, body = page(title, tag, subtitle)
        # **One footer, built here, and nowhere else.** This used to call
        # `navigate()` here *and* `goto_back_to_list()` after each page's
        # content, so six of the seven extras showed four stacked buttons, two of
        # them primary: "Next: Photos and videos / Skip the rest / Back to the
        # list / Finish". Rendered, not inferred. Worse, the halves went to
        # different places - "Skip the rest" and the Languages page's "Next: Done"
        # went to Done, past the Review page that is the only place anything is
        # downloaded, so an extra picked on the way was silently never fetched;
        # and "Finish" went to the Review, not to the finish.
        #
        # So: the primary action returns to the Review (where the choice is
        # downloaded or removed), and the next extra is one flat button for
        # someone browsing them in order. The last extra has no "next".
        goto_back_to_list(body)
        if next_tag not in ("done", "review"):
            on = Gtk.Button(label=f"Next: {_titles.get(next_tag, next_tag)}",
                            css_classes=["flat"], halign=Gtk.Align.CENTER)
            on.connect("clicked", lambda *_: goto(next_tag))
            page_footer(body).append(on)
        EXTRAS.append((title, tag, subtitle, next_tag))
        view.add(p)
        return body, p

    body, body_page = extras_page(
        "Eyes", "eyes",
        "Describe what is on the screen or in a photo. "
        + ("Reading text in pictures already works, without this." if ey["ocr"] else ""),
        "imagine")
    items = [(m.key, m.label.split(" - ")[0] + (" - recommended" if m.key == ey["recommended"] else ""),
              ("downloaded" if m.key in ey["installed"] else f"{m.size_bytes / 1e9:.1f} GB download")
              + " - " + m.label.split(" - ", 1)[1]) for m in local_vision.TIERS]
    group, chosen_eyes = choice_group("Vision model", items, ey["active"] or ey["recommended"])
    body.append(group)

    if ey["installed"] and ey["active"]:
        body.append(wrapping(
            f"{ey['active']} is already downloaded. Chronoa will use it."))
        choose("eyes", f"Eyes - {chosen_eyes()}",
               local_vision.MODELS[ey["active"]].size_bytes,
               lambda report: setup_eyes(chosen_eyes(), report, win.cancel, config),
               "already downloaded")
    else:
        _record_when_shown(body_page, "eyes", f"Eyes - {chosen_eyes()}",
                           with_engine("llama-server",
                                       local_vision.MODELS[chosen_eyes()].size_bytes),
                           lambda report: setup_eyes(chosen_eyes(), report,
                                                     win.cancel, config),
                           "describes the screen")

    # --- Imagine ----------------------------------------------------------
    im = s["imagine"]
    size = (imagegen.MODEL.size_bytes + imagegen.engine_for(im["gpu"]).size_bytes) / 1e9
    body, body_page = extras_page(
        "Imagine", "imagine",
        f"Make pictures from a description (SD-Turbo, {size:.1f} GB). "
        + ("Uses the graphics card." if im["gpu"]
           else "On the processor a picture takes tens of seconds."),
        "memory")
    # `size` is in **GB** here (it was divided by 1e9 above), so the bytes are
    # `size * 1e9`. It was `1e3`, which put Imagine at "0 MB" in the review list
    # and made a 2.0 GB download look free.
    _record_when_shown(body_page, "imagine", "Imagine - SD-Turbo", size * 1e9,
                       lambda report: setup_imagine(report, win.cancel, config),
                       "from a description")

    # --- Memory -----------------------------------------------------------
    body, body_page = extras_page(
        "Memory", "memory",
        f"Find earlier conversations by what they meant, not only by their words "
        f"({local_embed.MODEL.size_bytes / 1e6:.0f} MB).",
        "photos")
    _record_when_shown(body_page, "memory", "Memory - search by meaning",
                       with_engine("llama-server", local_embed.MODEL.size_bytes),
                       lambda report: setup_memory(report, win.cancel, config),
                       "by meaning, not by words")

    # --- Photos and videos ----------------------------------------------
    body, body_page = extras_page(
        "Photos and videos", "photos",
        f"Find faces and everyday objects, blur faces or the background, and "
        f"straighten a photographed page ({cv_runtime.DOWNLOAD_BYTES / 1e6:.0f} MB). "
        "Video keyframes and plain photo looks need nothing extra.",
        "sounds")
    _record_when_shown(body_page, "photos", "Photos and videos", cv_runtime.DOWNLOAD_BYTES,
                       lambda report: setup_photos(report, win.cancel, config),
                       "faces, objects, blur, straighten")

    # --- Sounds -----------------------------------------------------------
    body, body_page = extras_page(
        "Sounds", "sounds",
        f"Tell what a sound is - a doorbell, a dog, an alarm "
        f"({sounds_mod.MODEL.size_bytes / 1e6:.0f} MB).",
        "speakers")
    _record_when_shown(body_page, "sounds", "Sounds", sounds_mod.MODEL.size_bytes,
                       lambda report: setup_sounds(report, win.cancel, config),
                       "doorbell, dog, alarm")

    # --- Who said what ----------------------------------------------------
    body, body_page = extras_page(
        "Who said what", "speakers",
        f"Split a recording's transcript by speaker "
        f"({(speakers_mod.SEGMENTATION.size_bytes + speakers_mod.EMBEDDING.size_bytes) / 1e6:.0f} MB). "
        "Subtitles and transcripts need nothing extra.",
        "languages")
    _record_when_shown(body_page, "speakers", "Who said what",
                       speakers_mod.SEGMENTATION.size_bytes
                       + speakers_mod.EMBEDDING.size_bytes,
                       lambda report: setup_speakers(report, win.cancel, config),
                       "splits by speaker")

    # --- Languages --------------------------------------------------------
    la = s["languages"]
    body, body_page = extras_page(
        "Languages", "languages",
        "Read, speak and listen in more languages. Listening outside English is "
        "less accurate with the smaller speech models.",
        "done")
    lang_group = Adw.PreferencesGroup()
    lang_checks = {}
    for code, lang in languages.LANGUAGES.items():
        check = Gtk.CheckButton(active=code in la["chosen"])
        has = ("voice and text" if lang.voice else "text only (no voice yet)")
        # Through `_mb`, not a second copy of the division - the Languages page
        # carried its own `f"{... / 1e6:.0f} MB"`, and so had the same "0 MB"
        # bug the helper now cannot have.
        subtitle = ("added" if code in la["reads"] else _mb(languages.install_size(code))) + " - " + has
        row = Adw.ActionRow(title=lang.label, subtitle=subtitle, activatable_widget=check)
        row.add_prefix(check)
        lang_group.add(row)
        lang_checks[code] = check
    body.append(lang_group)
    listen = Adw.SwitchRow(title="Listen for these languages when I speak", active=la["listening"])
    listen_group = Adw.PreferencesGroup()
    listen_group.add(listen)
    body.append(listen_group)
    _lang_mb = sum(languages.install_size(c) for c, b in lang_checks.items() if b.get_active()) / 1e6
    consent_row(body, _lang_mb, "the languages you ticked", config)
    _download_buttons.clear()
    _lang_codes = [c for c, b in lang_checks.items() if b.get_active()]
    if _lang_codes:
        def record_languages(_page=None) -> None:
            choose("languages", f"Languages - {', '.join(_lang_codes)}",
                   sum(languages.install_size(c) for c in _lang_codes),
                   lambda report: setup_languages(_lang_codes, listen.get_active(),
                                                  report, win.cancel, config),
                   "only the ticked ones")

        body_page.connect("shown", record_languages)

    #: (title, tag, subtitle, already_ready) per optional extra, filled after
    #: every page is built because readiness is only known then.
    extra_entries = []
    for title, tag, subtitle, _next in EXTRAS:
        extra_entries.append((title, tag, subtitle, bool(
            {"eyes": ey["ready"], "imagine": im["ready"],
             "memory": s["memory"]["ready"], "photos": s["photos"]["ready"],
             "sounds": s["sounds"]["ready"], "speakers": s["speakers"]["ready"],
             "languages": bool(la["chosen"])}.get(tag))))
    # 6. review: the only page that downloads anything
    build_review()

    # 7. done
    done, box = page("Done", "done", "That is everything. You can run this again from Settings at any time.")
    finish = Gtk.Button(label="Start using Chronoa", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)

    def on_finish(*_):
        config.set("setup-complete", "true")
        if on_finished:
            on_finished()
        win.close()
    finish.connect("clicked", on_finish)
    page_footer(box).append(finish)
    view.add(done)
    _register_setup_pages()
    # Attached to the window, not just defined. `pages.show()` looks for
    # `window.show_page` and logs "cannot show a page" when there is none - which
    # is what happened: this function existed, was correct, and answered
    # `setup:review` for nobody, because the only thing that ever held a
    # reference to it was the local scope that was about to end. Measured
    # before the fix, on a real wizard: `pages.show("setup:review")` logged
    # "setup cannot show a page" while the Review page sat there fully built.
    win.show_page = lambda page_id: _wizard_show_page(win, view, page_id, goto)

    view.replace_with_tags(["welcome"])
    return win
