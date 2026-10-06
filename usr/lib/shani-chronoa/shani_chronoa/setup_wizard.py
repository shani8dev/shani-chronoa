"""First-run setup: make Chronoa able to think, hear and speak on this computer.

A fresh ShaniOS install has none of the three: no Ollama, no speech model, and
espeak-ng for a voice (the CLI matrix, both images). This window gets each one
working, in plain language, with sizes stated before anything downloads and
nothing fetched until the person presses the button - which is also how the
`model-download-enabled` consent is given. Harvested from sayri's `wizard.py`
(detect what is already there, offer the rest, download resumably), built on
Chronoa's own verified downloads:

- **Brain** - `local_llm`: llama.cpp's `llama-server` (a package dependency)
  plus one pinned GGUF model chosen by RAM, and by GPU when `ggml-vulkan` sees
  one. Ollama, if present, is used instead and nothing is downloaded.
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
    from shani_chronoa import local_llm, stt_provision, voices
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.ollama_llm import OllamaLLM
    config = config or ChronoaConfig()
    ollama = False
    try:
        ollama = OllamaLLM(host=config.ollama_host, model=config.model or "x").is_available()
    except Exception:  # noqa: BLE001 - "is it there" must never crash setup
        ollama = False
    whisper = bool(shutil.which("whisper-cli") or shutil.which("whisper.cpp"))
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
    from shani_chronoa import local_llm
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    if not local_llm.server_binary():
        return ("llama.cpp is not installed, so there is no local model to start. It comes with the next "
                "ShaniOS update; until then you can use Ollama, or the free online models in Settings.")
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
    from shani_chronoa import stt_provision
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    if not (shutil.which("whisper-cli") or shutil.which("whisper.cpp")):
        return ("Speech recognition (whisper.cpp) is not installed. It comes with the next ShaniOS update; "
                "typing works in the meantime.")
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
    from shani_chronoa import local_llm, local_vision
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    if not local_llm.server_binary():
        return "llama.cpp is not installed, so there is no vision model to run."
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
    from shani_chronoa import local_embed, local_llm
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    if not local_llm.server_binary():
        return "llama.cpp is not installed, so there is no memory model to run."
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


def build_window(application, config=None, on_finished: Optional[Callable[[], None]] = None):
    """The setup window (created lazily so importing this module never needs a display)."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk

    from shani_chronoa import local_llm, sherpa, stt_provision, voices
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
        scroller.set_child(box)
        p = Adw.NavigationPage(title=title, tag=tag)
        p.set_child(scroller)
        return p, box

    #: The check buttons of every `choice_group`, keyed by the group widget.
    _choice_rows: Dict[int, Dict[str, Gtk.CheckButton]] = {}

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
            box.append(w)
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
        mb = float(size_bytes) / 1e6
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
    box.append(start_btn)
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
        box.append(n)

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

    def goto_back_to_list(box, close_label: bool = False) -> None:
        """An extras page's way out: back to the list of everything chosen.

        Extras are optional one at a time, so each returns to the review rather
        than marching on to the next one - taking six unwanted pages after
        saying "I'll have eyes" is how a wizard loses people.
        """
        n = Gtk.Button(label="Back to the list", css_classes=["suggested-action", "pill"],
                       halign=Gtk.Align.CENTER)
        n.connect("clicked", lambda *_: goto("review"))
        box.append(n)
        skip = Gtk.Button(label="Close" if close_label else "Finish", css_classes=["flat"],
                          halign=Gtk.Align.CENTER)
        skip.connect("clicked", lambda *_: goto("review"))
        box.append(skip)

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
        box.append(nxt)
        retitle(nxt)
        if skip_label:
            skip = Gtk.Button(label=skip_label, css_classes=["flat"], halign=Gtk.Align.CENTER)
            skip.connect("clicked", lambda *_: goto(
                "done" if resolve() != "done" else resolve()))
            box.append(skip)
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
        row = Adw.ActionRow(title=provider.name,
                            subtitle=("a key is already saved" if existing.get(provider_id)
                                      else "required"))
        row.add_suffix(entry)
        row.set_activatable_widget(entry)
        key_rows.add(row)
    free = Adw.PreferencesGroup(
        title="No key needed",
        description=", ".join(cloud_llm.PROVIDERS[p].name
                              for p in cloud_llm.DEFAULT_PROVIDER_ORDER if p in cloud_llm.PROVIDERS))
    box.append(free)
    key_status = wrapping("")
    box.append(key_status)
    save_keys = Gtk.Button(label="Save these keys", css_classes=["suggested-action", "pill"],
                           halign=Gtk.Align.CENTER)

    def save() -> None:
        for provider_id, value in entered.items():
            config.set_api_key(config.API_KEY_SETTINGS[provider_id], value)
        key_status.set_label(
            f"Saved {sum(1 for v in entered.values() if v)} key(s). Chronoa will "
            f"try {', '.join(p for p, v in entered.items() if v) or 'the free gateways'} "
            "when it needs to think.")
        _consent_given(config, True)          # asking a cloud model is consent

    save_keys.connect("clicked", lambda *_: save())
    box.append(save_keys)
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
    navigate(box, "ears", skip_label="Skip for now")
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
    else:
        box.append(wrapping("Nothing is set up to answer yet."))
    n = Gtk.Button(label=f"Next: {_titles.get('ears', 'Ears')}",
                   css_classes=["suggested-action", "pill"],
                   halign=Gtk.Align.CENTER)
    n.connect("clicked", lambda *_: goto("ears"))
    box.append(n)
    local_button = Gtk.Button(label="Use a model on this computer",
                              css_classes=["flat"], halign=Gtk.Align.CENTER)
    local_button.connect("clicked", lambda *_: goto("model-picker"))
    box.append(local_button)
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
                   _spec.size_bytes,
                   lambda report: setup_brain(chosen(), report, win.cancel, config),
                   None)
            _download_buttons.clear()
            navigate(box, "ears", skip_label="Skip for now")
    view.add(picker)

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
                "in Settings and an API key."))
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
        choose("ears", f"Listening - whisper.cpp {chosen_stt()}", _espec.size_bytes,
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
    piper_mb = voices._PIPER.size_bytes / 1e6
    kokoro_mb = (sherpa.RELEASE.size_bytes + voices._KOKORO_MODEL.size_bytes) / 1e6
    items = [(k, f"{x.label.split(' - ')[0]} (Piper)",
              x.label.split(" - ")[1] + (" - downloaded" if voices.voice_installed(k)
                                         else f" - {x.onnx_size / 1e6:.0f} MB, plus {piper_mb:.0f} MB for Piper once"))
             for k, x in voices.VOICES.items() if x.language == "en"]
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
    box.append(listen)
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
    box.append(to_review)
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
        # "Skip the rest" goes to the review, not to the next extra: it says
        # "the rest", and taking a person to the *sixth* optional page after they
        # said skip would be the same word doing a different job.
        navigate(body, next_tag,
                 skip_label="Close" if next_tag == "done" else "Skip the rest")
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
        navigate(body, "review", skip_label="Back to the list")
    else:
        _record_when_shown(body_page, "eyes", f"Eyes - {chosen_eyes()}",
                           local_vision.MODELS[chosen_eyes()].size_bytes,
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
    goto_back_to_list(body)

    # --- Memory -----------------------------------------------------------
    body, body_page = extras_page(
        "Memory", "memory",
        f"Find earlier conversations by what they meant, not only by their words "
        f"({local_embed.MODEL.size_bytes / 1e6:.0f} MB).",
        "photos")
    _record_when_shown(body_page, "memory", "Memory - search by meaning",
                       local_embed.MODEL.size_bytes,
                       lambda report: setup_memory(report, win.cancel, config),
                       "by meaning, not by words")
    goto_back_to_list(body)

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
    goto_back_to_list(body)

    # --- Sounds -----------------------------------------------------------
    body, body_page = extras_page(
        "Sounds", "sounds",
        f"Tell what a sound is - a doorbell, a dog, an alarm "
        f"({sounds_mod.MODEL.size_bytes / 1e6:.0f} MB).",
        "speakers")
    _record_when_shown(body_page, "sounds", "Sounds", sounds_mod.MODEL.size_bytes,
                       lambda report: setup_sounds(report, win.cancel, config),
                       "doorbell, dog, alarm")
    goto_back_to_list(body)

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
    goto_back_to_list(body)

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
        subtitle = ("added" if code in la["reads"] else f"{languages.install_size(code) / 1e6:.0f} MB") + " - " + has
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
    goto_back_to_list(body, close_label=True)

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
    box.append(finish)
    view.add(done)
    from shani_chronoa import pages as page_registry
    page_registry.register(
        "setup",
        [("welcome", "Welcome"), ("mode", "Mode"), ("cloud-keys", "Cloud keys"),
         ("brain", "Brain"), ("model-picker", "Model"), ("ears", "Ears"),
         ("voice", "Voice"), ("review", "Review"), ("extras", "Optional extras"),
         ("eyes", "Eyes"), ("imagine", "Imagine"), ("memory", "Memory"),
         ("photos", "Photos and videos"), ("sounds", "Sounds"),
         ("speakers", "Who said what"), ("languages", "Languages"),
         ("done", "Done")],
        factory=lambda app, config=None: build_window(app, config or ChronoaConfig()),
        aliases={"more": "extras", "code": "eyes", "picture": "imagine",
                 "download": "review", "keys": "cloud-keys", "tts": "voice"},
    )
    def show_page(page_id: str) -> bool:
        """Show one setup page by id, from anywhere.

        Every page here is also reachable through `goto`, but only from inside
        this function - so `shani-chronoa --show-page=setup:review` could not
        have worked, and neither could a notification saying "finish the
        download step".
        """
        if page_id not in page_registry.page_ids("setup"):
            return False
        win.present()
        goto(page_id)
        return True

    view.replace_with_tags(["welcome"])
    return win
