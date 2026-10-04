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
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class Cancelled(Exception):
    """The person pressed Cancel; the partial download has been removed."""


# --------------------------------------------------------------------------
# State and steps (no GTK)
# --------------------------------------------------------------------------


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
    return {
        "brain": {
            "ollama": ollama,
            "server": bool(local_llm.server_binary()),
            "up": local_llm.is_up(),
            "installed": local_llm.installed(),
            "active": local_llm.active(),
            "recommended": local_llm.recommended_for_machine() if local_llm.server_binary() else local_llm.recommended(),
            "gpu": gpu,
            "ram_gb": round(local_llm.ram_gb(), 1),
            "ready": ollama or local_llm.is_up(),
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


def _consent_given(config) -> None:
    # Pressing Download in this window is the consent the downloaders check for.
    config.set("model-download-enabled", "true")


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
    _consent_given(config)
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
    _consent_given(config)
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
    _consent_given(config)
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
    _consent_given(config)
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
    _consent_given(config)
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
    _consent_given(config)
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
    _consent_given(config)
    runtime.install(progress=_progress(report, cancel, "OpenCV"), config=config, transport=transport)
    return ("Chronoa can now find faces and objects in photos and videos, blur faces or backgrounds, "
            "and straighten a photographed page.")


def setup_sounds(report=None, cancel: Optional[threading.Event] = None, config=None, transport=None) -> str:
    from shani_chronoa import sounds
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    _consent_given(config)
    sounds.install(progress=_progress(report, cancel, "the sound model"), config=config, transport=transport)
    return "Chronoa can now tell what a sound is - in a recording you give it, or when you ask it to listen."


def setup_speakers(report=None, cancel: Optional[threading.Event] = None, config=None, transport=None) -> str:
    from shani_chronoa import speakers
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    cancel = cancel or threading.Event()
    _consent_given(config)
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
    _consent_given(config)
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
    _consent_given(config)
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
    view = Adw.NavigationView()
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    toolbar.set_content(view)
    win.set_content(toolbar)
    win.cancel = threading.Event()

    def on_close(*_):
        # Closing it is "not now": it stops opening by itself (Settings, the
        # dock menu and --setup still open it); without this it came back on
        # every launch until someone finished every page.
        win.cancel.set()
        config.set("setup-dismissed", "true")
        return False
    win.connect("close-request", on_close)
    s = state(config)

    def page(title: str, tag: str, description: str):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=18, margin_bottom=18,
                      margin_start=18, margin_end=18)
        head = Gtk.Label(label=description, wrap=True, xalign=0)
        box.append(head)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(box)
        p = Adw.NavigationPage(title=title, tag=tag)
        p.set_child(scroller)
        return p, box

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
        return group, (lambda: next((k for k, c in rows.items() if c.get_active()), selected))

    def worker_area(box, button_label: str, run: Callable[[Callable], str], next_tag: Optional[str],
                    done_label: str = ""):
        """A button that runs `run` on a thread with a progress bar; with `next_tag`, Skip/Next move on."""
        status = Gtk.Label(wrap=True, xalign=0)
        bar = Gtk.ProgressBar(show_text=True, visible=False)
        go = Gtk.Button(label=button_label, css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
        nxt = Gtk.Button(label="Next", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER,
                         visible=False)
        widgets = [bar, status, go]
        if next_tag:
            skip = Gtk.Button(label="Skip for now" if next_tag != "done" else "Close", css_classes=["flat"],
                              halign=Gtk.Align.CENTER)
            skip.connect("clicked", lambda *_: view.push_by_tag(next_tag))
            nxt.connect("clicked", lambda *_: view.push_by_tag(next_tag))
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

    # 1. welcome
    welcome, box = page("Welcome", "welcome",
                        "Chronoa runs on this computer. To think, hear you and talk back it needs three things. "
                        "Nothing is downloaded until you press a button, and every file is checked before use.")
    summary = Adw.PreferencesGroup()
    for name, ready, ok_text, missing_text in (
            ("Brain", s["brain"]["ready"], "ready", "needs a model"),
            ("Ears", s["ears"]["ready"], "ready", "needs a speech model"),
            ("Voice", s["voice"]["ready"], "natural voice ready", "using the basic voice")):
        summary.add(Adw.ActionRow(title=name, subtitle=ok_text if ready else missing_text))
    extras = [n for n, k in (("eyes", "eyes"), ("pictures", "imagine"), ("memory", "memory"),
                             ("photos", "photos"), ("sounds", "sounds"), ("speakers", "speakers")) if s[k]["ready"]]
    extras += [f"{len(s['languages']['chosen'])} more language(s)"] if s["languages"]["chosen"] else []
    summary.add(Adw.ActionRow(title="More (optional)", subtitle=", ".join(extras) + " ready" if extras
                              else "eyes, pictures, memory and languages - after the voice"))
    box.append(summary)
    start_btn = Gtk.Button(label="Start", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
    start_btn.connect("clicked", lambda *_: view.push_by_tag("brain"))
    box.append(start_btn)
    view.add(welcome)

    # 2. brain
    b = s["brain"]
    hw = (f"This computer has {b['ram_gb']} GB of memory and "
          + (f"a graphics card llama.cpp can use ({b['gpu'][0].split(':', 1)[-1].strip()[:60]})." if b["gpu"]
             else "no graphics card llama.cpp can use, so the model runs on the processor."))
    brain, box = page("Brain", "brain", "The language model is what understands you. " + hw)
    if b["ollama"]:
        box.append(Gtk.Label(label="Ollama is running on this computer, so Chronoa uses it - nothing to download.",
                             wrap=True, xalign=0))
        n = Gtk.Button(label="Next", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
        n.connect("clicked", lambda *_: view.push_by_tag("ears"))
        box.append(n)
    else:
        items = [(spec.key, label + (" - recommended" if spec.key == b["recommended"] else ""),
                  ("downloaded" if spec.key in b["installed"] else f"{spec.size_bytes / 1e9:.1f} GB download"))
                 for spec, label, _ram in local_llm.TIERS]
        group, chosen = choice_group("Choose a model", items, b["active"] or b["recommended"])
        box.append(group)
        worker_area(box, "Download and start", lambda report: setup_brain(chosen(), report, win.cancel, config), "ears")
    view.add(brain)

    # 3. ears
    e = s["ears"]
    ears, box = page("Ears", "ears", "To understand what you say, Chronoa needs a speech-recognition model "
                                    "(whisper.cpp). It never sends your voice anywhere.")
    items = [(k, f"{k.split('-')[0].title()}" + (" - recommended" if k == e["recommended"] else ""),
              f"{spec.size_bytes / 1e6:.0f} MB - {spec.note}") for k, spec in stt_provision.MODELS.items()]
    group, chosen_stt = choice_group("Choose how well it listens", items, e["recommended"])
    box.append(group)
    worker_area(box, "Download", lambda report: setup_ears(chosen_stt(), report, win.cancel, config), "voice")
    view.add(ears)

    # 4. voice: one list of voices; each brings the engine that speaks it
    v, ko = s["voice"], s["kokoro"]
    voice_page, box = page("Voice", "voice", "Pick the voice Chronoa answers with. Piper voices start "
                                            "speaking quickly; Kokoro voices sound most like a person but, on a "
                                            "processor, each reply starts a moment later.")
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
    go, status = worker_area(box, "Download this voice", lambda report: setup_voice(chosen_voice(), report, win.cancel,
                                                                                    config), "more")
    listen = Gtk.Button(label="Listen", css_classes=["pill"], halign=Gtk.Align.CENTER)

    def on_listen(*_):
        from shani_chronoa.tts import PiperTTS
        voice = chosen_voice()
        kokoro = voice in voices.KOKORO_VOICES
        ready = voices.kokoro_installed(voice) if kokoro else (voices.voice_installed(voice) and voices.piper_binary())
        if not ready:
            status.set_label("Download this voice first, then listen.")
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
    view.add(voice_page)

    # 5. more: optional extras, each with its own button; none is needed to finish
    more, box = page("More", "more", "Optional extras. Each runs on this computer and can be added later "
                                     "from Settings -> Run setup again. Sizes are what will be downloaded.")

    def section(title: str, subtitle: str):
        frame = Adw.PreferencesGroup(title=title, description=subtitle)
        box.append(frame)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6, margin_bottom=12)
        box.append(inner)
        return frame, inner

    from shani_chronoa import imagegen, languages, local_embed, local_vision
    ey = s["eyes"]
    frame, inner = section("Eyes", "Describe what is on the screen or in a photo. "
                           + ("Reading text in pictures already works." if ey["ocr"] else ""))
    items = [(m.key, m.label.split(" - ")[0] + (" - recommended" if m.key == ey["recommended"] else ""),
              ("downloaded" if m.key in ey["installed"] else f"{m.size_bytes / 1e9:.1f} GB download")
              + " - " + m.label.split(" - ", 1)[1]) for m in local_vision.TIERS]
    group, chosen_eyes = choice_group("Vision model", items, ey["active"] or ey["recommended"])
    inner.append(group)
    worker_area(inner, "Download and start", lambda report: setup_eyes(chosen_eyes(), report, win.cancel, config),
                None, "Ready." if ey["ready"] else "")

    im = s["imagine"]
    size = (imagegen.MODEL.size_bytes + imagegen.engine_for(im["gpu"]).size_bytes) / 1e9
    frame, inner = section("Imagine", f"Make pictures from a description (SD-Turbo, {size:.1f} GB). "
                           + ("Uses the graphics card." if im["gpu"] else "On the processor a picture takes "
                              "tens of seconds."))
    worker_area(inner, "Download and start", lambda report: setup_imagine(report, win.cancel, config),
                None, "Ready." if im["ready"] else "")

    frame, inner = section("Memory", f"Find earlier conversations by what they meant, not only by their words "
                           f"({local_embed.MODEL.size_bytes / 1e6:.0f} MB).")
    worker_area(inner, "Download and start", lambda report: setup_memory(report, win.cancel, config),
                None, "Ready." if s["memory"]["ready"] else "")

    from shani_chronoa import sounds as sounds_mod, speakers as speakers_mod
    from shani_chronoa.opencv import runtime as cv_runtime
    frame, inner = section("Photos and videos", "Find faces and everyday objects, blur faces or the background, "
                           f"and straighten a photographed page ({cv_runtime.DOWNLOAD_BYTES / 1e6:.0f} MB). "
                           "Video keyframes and plain photo looks need nothing extra.")
    worker_area(inner, "Download", lambda report: setup_photos(report, win.cancel, config), None,
                "Ready." if s["photos"]["ready"] else "")
    frame, inner = section("Sounds", f"Tell what a sound is - a doorbell, a dog, an alarm "
                           f"({sounds_mod.MODEL.size_bytes / 1e6:.0f} MB).")
    worker_area(inner, "Download", lambda report: setup_sounds(report, win.cancel, config), None,
                "Ready." if s["sounds"]["ready"] else "")
    frame, inner = section("Who said what", "Split a recording's transcript by speaker "
                           f"({(speakers_mod.SEGMENTATION.size_bytes + speakers_mod.EMBEDDING.size_bytes) / 1e6:.0f} MB). "
                           "Subtitles and transcripts need nothing extra.")
    worker_area(inner, "Download", lambda report: setup_speakers(report, win.cancel, config), None,
                "Ready." if s["speakers"]["ready"] else "")

    la = s["languages"]
    frame, inner = section("Languages", "Read, speak and listen in more languages. Listening outside English "
                           "is less accurate with the smaller speech models.")
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
    inner.append(lang_group)
    listen = Adw.SwitchRow(title="Listen for these languages when I speak", active=la["listening"])
    listen_group = Adw.PreferencesGroup()
    listen_group.add(listen)
    inner.append(listen_group)
    worker_area(inner, "Add these languages",
                lambda report: setup_languages([c for c, b in lang_checks.items() if b.get_active()],
                                               listen.get_active(), report, win.cancel, config), None)

    to_done = Gtk.Button(label="Next", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
    to_done.connect("clicked", lambda *_: view.push_by_tag("done"))
    box.append(to_done)
    view.add(more)

    # 6. done
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
    view.replace_with_tags(["welcome"])
    return win
