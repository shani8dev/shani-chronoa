"""What is running: the model that answers, the engine that serves it, and the extras.

Three questions, one page, and every one of them answered from state that is
already on this machine:

- **The model.** `config.model` - the persisted `model` gsetting, empty meaning
  "no override" - and, when that is empty, `HardwareProfile.get_model()`. The
  precedence is `app/_model_override` (the session-only `--model=`), then the
  gsetting, then the hardware tier, copied from `application._init_components`
  so the page names the model that would actually be used. The profile it was
  chosen for is named too, because "qwen3:1.7b" on a `low` tier is a deliberate
  answer rather than a default one.

- **The engine.** The same three-way order `_init_components` uses: Ollama if
  it answers, else llama.cpp's `llama-server` if it is up, else the cloud
  chain - which needs *both* privacy mode off *and* `cloud-fallback-enabled`.
  Each row says whether that engine is available right now.

- **The extras.** One row per extra on the setup wizard's More page, each
  reading the same function that wizard's `state()` reads
  (`local_vision.installed()`, `imagegen.installed()`, `local_embed.installed()`,
  `languages.chosen()`, `opencv.runtime.installed()`, `sounds.installed()`,
  `speakers.installed()`). Nothing here keeps a second copy of "is this
  installed", because a second copy is a second thing to be wrong.

**Three states, not two, everywhere.** `True`, `False`, and `unknown`, which is
what a probe that raised produces. This repo's rule is that a failure mode
which looks like a clean answer is worse than a failure: a row that said
"not installed" because the probe blew up would send someone to download a
2 GB model they already have. So a missing engine, an unreadable setting, an
unimportable extra module and a model file that is not there all render as
what they are, and only the last of those says "not installed". The row
vocabulary is unchanged by the move to Adwaita rows - the three words are still
`INSTALLED`, `NOT_INSTALLED`, `UNKNOWN`, and a row cannot invent a fourth.

**No network calls to find out.** The two probes are loopback: `OllamaLLM`'s
`is_available()` and `local_llm.is_up()` ask a service running on this
machine, and they are skipped entirely when there is nothing to ask (no
`llama-server`, no model file) or when the answer would have to come from
off-machine. If the configured `ollama-host` is not loopback this page says so
and reports the state as unknown rather than reaching across the network to
find out. The cloud row is decided from configuration alone -
`CloudLLMChain.is_available()` is documented as "not a live check", it only
counts configured providers - so naming a provider never touches it.

Plain GTK4 is still the floor, and this module still never asks for libadwaita
itself: every `Adw` widget needs `Adw.init()` to have run before it is
constructed, and this page has to build under a bare `Gtk.Application` (the
sidebar's rule), in a test process, and in any tool that imports it. The page
now takes its title, its toolbar and its rows from `surfaces/common.py`, which
owns exactly that problem - it initialises Adw once, guarded, and every helper
it offers has a plain-GTK answer - so the same two rules hold here without this
module ever importing `Adw`. The one interactive control - Reload - exists
because "available right now" is only true until the next change, and it
re-reads the same functions rather than caching anything.

**`build()` returns the page, and forwards this module's accessors onto it.** The
content lives in a `_ModelSurface` box: `common.surface()` builds the page and
this module builds what goes inside it, so a caller holding only the page still
gets `model_rows()`, `engine_rows()`, `extras()`, `summary()`, `serving()` and
`reload_button()` from it - set as attributes, because a libadwaita page cannot
be subclassed here without importing `Adw`. Reload empties the three groups by
reference (`Adw.PreferencesGroup.remove()` on each row it added, measured
working on libadwaita 1.5 in both orders and with the tree never walked first);
walking a group for children does *not* work, because libadwaita keeps its rows
inside its own boxes - which is also why the rows are tracked rather than
found.

**A row is an `Adw.ActionRow` with markup switched off.** `Adw.PreferencesRow`
renders its title and subtitle through Pango, and both of those come from a
gsetting, a command line, a probe's exception string and a spec table - `a & b`
and `<b>` are ordinary content in all four. Escaping instead would put
`&amp;` on screen; switching markup off shows the words themselves, which is
what `_escape` did for the two hand-built labels a row used to carry - which
is why it is gone with them, and `GLib` with it.
`_row_title` and `_row_detail` still answer `get_text()` with exactly those
strings, which is the point of them: a caller can check a row's wording
against the function that produced it.

Read-only apart from Reload: nothing here downloads a model, starts a service,
or writes a setting. Installing an extra is `shani-chronoa --setup`, and this
page says so rather than pretending otherwise.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, NamedTuple, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import (  # noqa: E402
    imagegen,
    languages,
    local_embed,
    local_llm,
    local_vision,
    sounds,
    speakers,
)
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.opencv import runtime as opencv_runtime  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Model"
ICON = "media-playback-start-symbolic"
SUBTITLE = ("Local state only. The cloud row is read from configuration and is never contacted; "
            "the two local probes ask a service running on this computer.")

#: Longest detail a row shows before it is cut. Model names, paths and provider
#: lists are short, but an `OSError` string from a probe is not.
DETAIL_LIMIT = 150

#: Words for the three states, in one place so a row cannot invent a fourth.
INSTALLED, NOT_INSTALLED, UNKNOWN = "installed", "not installed", "unknown"
AVAILABLE, UNAVAILABLE = "available now", "not available"


# ---------------------------------------------------------------------------
# Reading state without raising
# ---------------------------------------------------------------------------


def _clip(text: Any, limit: int = DETAIL_LIMIT) -> str:
    flat = " ".join(str(text if text is not None else "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def _read_setting(config: Any, name: str, default: str = "") -> str:
    """One setting, from a `ChronoaConfig` or anything shaped like one.

    `model`, `ollama_host` and `hardware_profile` are properties on the real
    class and methods on some stubs, so both are accepted; a property whose
    read raises is `default` rather than an exception out of `build()`.
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


def _read_str(config: Any, key: str) -> str:
    """A string setting, or "" when it could not be read at all.

    `""` rather than None on purpose: both "unset" and "unreadable" mean the
    same thing to the caller here - there is no usable endpoint - and inventing
    a third state would only give the status line a way to be wrong.
    """
    if config is None:
        return ""
    try:
        return str(config.get(key, "") or "")
    except Exception:  # noqa: BLE001 - unreadable is the same as unset here
        logger.debug("cannot read %s", key, exc_info=True)
        return ""


def _loopback(host: str) -> bool:
    """Whether `host` names this machine, so asking it is not a network call."""
    text = str(host or "")
    for scheme in ("http://", "https://"):
        if text.startswith(scheme):
            text = text[len(scheme):]
    authority = text.split("/", 1)[0].rsplit("@", 1)[-1]
    if authority.startswith("["):
        return authority[1:].split("]", 1)[0] in ("::1",)
    return authority.split(":", 1)[0] in ("", "127.0.0.1", "localhost", "::1")


def _hardware(app: Any) -> Tuple[Any, str]:
    """The app's own hardware profile, or a freshly detected one.

    The app's is preferred because `application._init_components` has already
    applied the persisted `hardware-profile` override to it, so `profile` is the
    effective tier rather than the detected one. A fresh `HardwareProfile()`
    only happens for a caller that has none, and that is deliberately not
    cached at import: it runs `nvidia-smi`/`vulkaninfo`/`rocm-smi` probes.
    """
    hardware = getattr(app, "hardware", None)
    if hardware is None:
        try:
            from shani_chronoa.hardware_profile import HardwareProfile

            hardware = HardwareProfile()
        except Exception:  # noqa: BLE001 - no hardware object is a state, not a crash
            logger.debug("no hardware profile available", exc_info=True)
            return None, ""
    try:
        return hardware, str(getattr(hardware, "profile", "") or "")
    except Exception:  # noqa: BLE001
        return hardware, ""


def _read_dir(module: Any) -> str:
    """The directory an extras module reads from, or '' when it exposes none."""
    for name in ("model_dir", "models_dir", "tessdata_dir", "engine_dir"):
        getter = getattr(module, name, None)
        if callable(getter):
            try:
                return str(getter())
            except Exception:  # noqa: BLE001 - a path that cannot be resolved is omitted
                logger.debug("cannot resolve %s.%s()", getattr(module, "__name__", module), name, exc_info=True)
    return ""


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


def _model_name(app: Any, config: Any, hardware: Any) -> Tuple[str, str]:
    """(the model that would be used, where that came from).

    The precedence is `_model_override` then `config.model` then the hardware
    tier, read straight out of `application._init_components`; anything else
    would describe a model the app does not use.
    """
    try:
        override = str(getattr(app, "_model_override", "") or "")
    except Exception:  # noqa: BLE001
        override = ""
    if override:
        return override, "from --model on the command line (this session only)"
    configured = _read_setting(config, "model", "")
    if configured:
        return configured, "set in Settings, overriding the hardware choice"
    if hardware is not None:
        try:
            name = str(hardware.get_model() or "")
        except Exception:  # noqa: BLE001 - an unreadable tier is reported below
            logger.debug("hardware profile could not choose a model", exc_info=True)
            name = ""
        if name:
            return name, "chosen for this computer's hardware tier"
    return "", "no model chosen and no hardware tier to choose one from"


def _model_file_detail() -> Tuple[str, str]:
    """(title, detail) for the GGUF file the local engine would load.

    A missing file says so in the same words as a missing extra, and for the
    same reason: `setup_brain` writes exactly this file, so its absence is the
    single fact that decides whether Chronoa can think at all.
    """
    try:
        if not local_llm.server_binary():
            return ("Model file", "not installed - llama-server is not on this computer "
                                 "(the llama-cpp package), so nothing local can run")
        installed = list(local_llm.installed())
        directory = str(local_llm.model_dir())
        if not installed:
            return "Model file", f"not installed - no model file in {directory}"
        active = local_llm.active()
        if not active:
            return ("Model file", f"not installed - {len(installed)} model file(s) are in {directory}, "
                                 "but none of them is the one the service would load")
        return "Model file", f"{active} - {local_llm.current_link()}"
    except Exception as exc:  # noqa: BLE001 - a model file that cannot be read is unknown
        logger.debug("cannot read the local model file", exc_info=True)
        return "Model file", f"unknown - {_clip(exc)}"


# ---------------------------------------------------------------------------
# The engines
# ---------------------------------------------------------------------------


class _Engine(NamedTuple):
    key: str
    label: str
    state: Optional[bool]
    detail: str
    source: str


def _ollama_engine(config: Any, model: str) -> _Engine:
    """Ollama, the first thing `_init_components` tries."""
    host = _read_setting(config, "ollama_host", "http://127.0.0.1:11434")
    if not _loopback(host):
        # Answering this would mean reaching off this machine, which this page
        # does not do. Unknown is the honest state; "not available" would be a
        # guess about a server that may well be up.
        return _Engine("ollama", "Ollama", None,
                       f"unknown - the configured Ollama host ({host}) is not on this computer, "
                       "and this page does not contact one that is not.",
                       "config.ollama_host")
    try:
        from shani_chronoa.ollama_llm import OllamaLLM

        ok = bool(OllamaLLM(host=host, model=model or "local").is_available())
    except Exception as exc:  # noqa: BLE001 - a probe that raised is unknown, not down
        logger.debug("the Ollama probe failed", exc_info=True)
        return _Engine("ollama", "Ollama", None, f"unknown - the probe failed: {_clip(exc)}",
                       f"OllamaLLM(host={host}).is_available()")
    if ok:
        return _Engine("ollama", "Ollama", True, f"answering at {host}", f"OllamaLLM(host={host}).is_available()")
    return _Engine("ollama", "Ollama", False, f"nothing is listening at {host}",
                   f"OllamaLLM(host={host}).is_available()")


def _local_engine() -> _Engine:
    """llama.cpp's `llama-server`, the second thing `_init_components` tries."""
    source = "local_llm.is_up()"
    try:
        binary = local_llm.server_binary()
        if not binary:
            return _Engine("local", "llama.cpp on this computer", False,
                           "not installed - llama-server is not on this computer (the llama-cpp package)",
                           "local_llm.server_binary()")
        installed = list(local_llm.installed())
        if not installed:
            return _Engine("local", "llama.cpp on this computer", False,
                           f"installed but nothing to load - no model file in {local_llm.model_dir()}",
                           "local_llm.installed()")
        active = local_llm.active() or "no model chosen"
        try:
            up = bool(local_llm.is_up())
        except Exception as exc:  # noqa: BLE001 - a probe that raised is unknown
            logger.debug("the llama-server probe failed", exc_info=True)
            return _Engine("local", "llama.cpp on this computer", None,
                           f"unknown - the probe failed: {_clip(exc)}", source)
        if up:
            # **Availability, not selection.** This used to read
            # "... is answering with qwen3-1.7b", and that is a claim about the
            # app that the probe cannot support. `local_llm.is_up()` asks the
            # *server*, not the dispatcher, and the two genuinely diverge: once
            # `app/brain.py:_maybe_enable_cloud_fallback` has selected the cloud
            # chain it returns early on `isinstance(self.llm, CloudLLMChain)` and
            # never re-evaluates - so starting llama-server afterwards leaves the
            # app answering through the cloud while this panel says the local one
            # is answering.
            #
            # That state is trivially reachable (start without llama.cpp, start it
            # later) and both rows claimed to answer. Measured: this row read
            # "127.0.0.1:8765 is answering with qwen3-1.7b" while `app.llm` held a
            # `CloudLLMChain`.
            return _Engine("local", "llama.cpp on this computer", True,
                           f"{local_llm.HOST}:{local_llm.PORT} is up and "
                           f"{active} is ready to load", source)
        return _Engine("local", "llama.cpp on this computer", False,
                       f"installed ({binary}) with {active}, but nothing is listening on "
                       f"{local_llm.HOST}:{local_llm.PORT}", source)
    except Exception as exc:  # noqa: BLE001 - a whole engine unreadable is unknown
        logger.debug("cannot read the local engine state", exc_info=True)
        return _Engine("local", "llama.cpp on this computer", None, f"unknown - {_clip(exc)}", source)


def _cloud_engine(config: Any) -> _Engine:
    """The cloud chain: the last resort, and the only one that leaves the machine.

    `CloudLLMChain.is_available()` is documented as "not a live check" - it
    counts configured providers - so asking it here contacts nothing.
    """
    source = "CloudLLMChain(provider_ids=...).is_available()"
    if config is None:
        return _Engine("cloud", "Cloud fallback", None,
                       "unknown - no settings object, so neither gate could be read", source)
    privacy = _read_bool(config, "privacy-mode", True)
    enabled = _read_bool(config, "cloud-fallback-enabled", False)
    if privacy is None or enabled is None:
        return _Engine("cloud", "Cloud fallback", None,
                       "unknown - the cloud-fallback-enabled switch could not be read", source)
    if privacy or not enabled:
        shut = []
        if privacy:
            shut.append("privacy mode is on")
        if not enabled:
            shut.append("cloud-fallback-enabled is off")
        gates = shut[0] if len(shut) == 1 else " and ".join([", ".join(shut[:-1]), shut[-1]])
        return _Engine("cloud", "Cloud fallback", False,
                       f"not available - both gates must be open, and {gates}", source)
    try:
        from shani_chronoa.cloud_llm import (
            BYOK_PROVIDER_ORDER,
            CUSTOM_ID,
            DEFAULT_PROVIDER_ORDER,
            CloudLLMChain,
            custom_provider,
        )

        api_keys = config.cloud_llm_api_keys()
        order = BYOK_PROVIDER_ORDER + DEFAULT_PROVIDER_ORDER
        # Mirrors app/brain.py:_maybe_enable_cloud_fallback: a usable hand-typed
        # endpoint is put first, and is named in the list this panel shows. The
        # two must agree - a status line that omits the endpoint actually in use
        # is the confident-wrong-answer shape this repo keeps finding.
        custom_url = _read_str(config, "custom-llm-base-url")
        custom_model = _read_str(config, "custom-llm-model")
        if custom_provider(custom_url, custom_model) is not None:
            order = (CUSTOM_ID,) + order
        chain = CloudLLMChain(provider_ids=order, api_keys=api_keys,
                              custom_base_url=custom_url, custom_model=custom_model)
        ok = bool(chain.is_available())
        named = [p for p in order
                 if api_keys.get(p) or p in DEFAULT_PROVIDER_ORDER or p == CUSTOM_ID]
        providers = ", ".join(named) or "none configured"
    except Exception as exc:  # noqa: BLE001 - a chain that cannot be built is unknown
        logger.debug("cannot read the cloud fallback state", exc_info=True)
        return _Engine("cloud", "Cloud fallback", None, f"unknown - {_clip(exc)}", source)
    if ok:
        return _Engine("cloud", "Cloud fallback", True,
                       f"would answer through: {providers} (never asked here; availability is per-request)",
                       source)
    return _Engine("cloud", "Cloud fallback", False, "no cloud provider is configured", source)


def _selected_engine(app: Any) -> str:
    """Which engine the app is actually holding, from `app.llm` itself.

    **This is the one authoritative answer on the panel**, and it is not
    derivable from the rows above it. Availability and selection are different
    questions here, unlike in the Voice panel:

    - `PiperTTS.engine()` is asked per reply, so the running object and the
      selected engine coincide - which is why `gui/surfaces/voice.py` resolves
      through `app.tts`;
    - the LLM is chosen **once**, at startup. `_maybe_enable_cloud_fallback`
      returns early when `isinstance(self.llm, CloudLLMChain)`, so a machine that
      fell back to the cloud never reconsiders - and starting llama-server
      afterwards adds an *available* engine without changing the *selected* one.

    So the rows stay availability-scoped and this names the choice. Reads the
    object rather than re-deriving the condition, because re-deriving it is
    exactly how the two came to disagree in the first place.
    """
    llm = getattr(app, "llm", None)
    if llm is None:
        return "No engine has been chosen yet."
    name = type(llm).__name__
    try:
        from shani_chronoa.cloud_llm import CloudLLMChain
        if isinstance(llm, CloudLLMChain):
            providers = str(getattr(llm, "model", "") or "")
            return ("Answers through a cloud provider"
                    + (f" ({providers})" if providers else "")
                    + " - chosen at startup, and not reconsidered since")
    except Exception:  # noqa: BLE001 - a name is better than no row
        logger.debug("cannot identify the selected engine", exc_info=True)
    return f"Answers through {name}, chosen at startup."


def _engines(config: Any, model: str) -> List[_Engine]:
    """Every engine, in the order `application._init_components` tries them.

    The order is the answer to "which one would serve this", so it is also the
    order they are shown in.
    """
    return [_ollama_engine(config, model), _local_engine(), _cloud_engine(config)]


def _serving(engines: List[_Engine]) -> str:
    """Which engine would serve the model now - or the honest reason none would.

    A row whose state is unknown does not settle the question: with no
    available engine and one unreadable probe, "nothing can answer" would be a
    claim this page cannot support.
    """
    for engine in engines:
        if engine.state:
            return f"Would answer now: {engine.label}."
    unknown = [e.label for e in engines if e.state is None]
    if unknown:
        return ("Would answer now: cannot be determined - "
                + ", ".join(unknown)
                + " could not be read. Nothing on this page started or stopped anything.")
    return ("Would answer now: nothing. There is no engine with a model behind it; "
            "`shani-chronoa --setup` downloads one.")


def _state_word(state: Optional[bool], yes: str, no: str) -> str:
    return UNKNOWN if state is None else (yes if state else no)


# ---------------------------------------------------------------------------
# The extras
# ---------------------------------------------------------------------------


#: (installed, detail). None for installed means "could not be determined".
Probe = Callable[[Any], Tuple[Optional[bool], str]]


def _eyes(config: Any) -> Tuple[Optional[bool], str]:
    found = local_vision.installed()
    if found:
        return True, f"{len(found)} vision model(s) downloaded"
    return False, "no vision model and its projector are downloaded together, so neither is here"


def _imagine(config: Any) -> Tuple[Optional[bool], str]:
    if not imagegen.server_binary():
        return False, "the stable-diffusion.cpp program is not installed"
    if not imagegen.installed():
        return False, "the program is there but the picture model is not downloaded"
    return True, "the picture program and its model are installed"


def _memory(config: Any) -> Tuple[Optional[bool], str]:
    if local_embed.installed():
        return True, "the embedding model that searches conversations by meaning is downloaded"
    return False, "conversation search stays keyword-only without it"


def _languages(config: Any) -> Tuple[Optional[bool], str]:
    """The wizard's Languages page: a list is chosen, then the data is fetched.

    `chosen` is the record setup writes, but a chosen language with nothing
    downloaded is not an installed extra, and saying "installed" for it would be
    the one confident wrong answer on this page - the user would ask Chronoa to
    read a page in a script and get nothing back. So the state is the disk's,
    and the detail always separates what is chosen from what is there.
    """
    chosen = list(languages.chosen(config))
    if not chosen:
        return False, "no extra language chosen (English needs nothing added)"
    reads = [c for c in chosen if languages.reads(c)]
    speaks = [c for c in chosen if languages.speaks(c)]
    bits = [f"{len(chosen)} chosen",
            "reading data for " + ", ".join(reads) if reads else "no reading data downloaded",
            "a voice for " + ", ".join(speaks) if speaks else "no voice installed"]
    detail = "; ".join(bits)
    if not reads and not speaks:
        return False, f"chosen, but nothing is downloaded yet - {detail}"
    return True, detail


def _photos(config: Any) -> Tuple[Optional[bool], str]:
    if opencv_runtime.installed():
        return True, "OpenCV, numpy and its three models are installed in your home"
    return False, "faces, objects, blur and page scans need OpenCV and its models"


def _sounds(config: Any) -> Tuple[Optional[bool], str]:
    if sounds.installed():
        return True, "the audio-tagging program and its model are installed"
    return False, "telling a doorbell from a dog needs a model"


def _speakers(config: Any) -> Tuple[Optional[bool], str]:
    if speakers.installed():
        return True, "the diarization and speaker-embedding models are installed"
    return False, "splitting a recording by speaker needs both models"


def _math(config: Any) -> Tuple[Optional[bool], str]:
    """Algebra and calculus, which `skills/solve_math.py` gates on **symengine
    and `bc`** - not on one of them.

    Two engines because one is not enough: symengine does the exact algebra
    (derivative, series, expand, factor, primes, linear solve) and `bc` does
    the exact rational arithmetic that symengine's own `solve` segfaults on. So
    a row that reported only symengine would say "installed" on a machine where
    every matrix question raises, and one that reported only `bc` would say it
    on a machine where nothing symbolic can run at all.

    **This read `solve_math.sp`.** There is no `sp` any more - the module binds
    `se` for symengine - so the probe raised `AttributeError`, the row came back
    *unknown*, and a real dependency change left the wizard describing a module
    that had not existed for days. The state that answers this is the skill
    module's own flags: the exact ones `_run()` and the bc engine refuse on.
    """
    try:
        from shani_chronoa.skills import solve_math
    except Exception as exc:  # noqa: BLE001 - an unimportable skill is unknown
        logger.debug("cannot import solve_math", exc_info=True)
        return None, f"could not be determined - {_clip(exc)}"
    symbolic = getattr(solve_math, "se", None) is not None
    arithmetic = bool(getattr(solve_math, "_bc_available", lambda: False)())
    if symbolic and arithmetic:
        return True, ("symengine and bc are both available, so solve_math can "
                      "do the exact algebra and the exact arithmetic")
    if symbolic:
        return False, ("symengine is installed but bc is not, so every integral, "
                       "limit, sum and numeric evaluation refuses (install bc); "
                       "the exact algebra still works")
    if arithmetic:
        return False, ("bc is installed but symengine is not, so every symbolic "
                       "question refuses (install python-symengine); numeric "
                       "evaluation still works")
    return False, ("neither python-symengine nor bc is installed, so solve_math "
                   "refuses everything")


#: key -> (label, what it is for, probe, the module the probe reads). The order
#: the wizard's More page offers them, with maths last because it is not one of
#: that page's downloads.
_EXTRAS: Tuple[Tuple[str, str, str, Probe, Any], ...] = (
    ("eyes", "Eyes", "describe what is on the screen or in a photo", _eyes, local_vision),
    ("imagine", "Imagine", "make a picture from a description, or change a photo by one",
     _imagine, imagegen),
    ("memory", "Memory", "find earlier conversations by meaning, not only by their words",
     _memory, local_embed),
    ("languages", "Languages", "read, speak and listen in more languages", _languages, languages),
    ("photos", "Photos and videos", "find faces and objects, blur faces or a background, "
     "straighten a photographed page", _photos, opencv_runtime),
    ("sounds", "Sounds", "tell what a sound is", _sounds, sounds),
    ("speakers", "Who said what", "split a recording's transcript by speaker", _speakers, speakers),
    ("math", "Maths", "algebra and calculus, through symengine and bc", _math, None),
)


class _Extra(NamedTuple):
    key: str
    label: str
    what: str
    installed: Optional[bool]
    detail: str
    source: str


def _extra_source(module: Any) -> str:
    """Where the answer came from, so a row can be checked against its source."""
    if module is None:
        return "skills/solve_math.py's symengine and bc availability"
    name = getattr(module, "__name__", "the extras module")
    directory = _read_dir(module)
    return f"{name}" + (f" (reads {directory})" if directory else "")


def _extras(config: Any) -> List[_Extra]:
    """Every extra, in `_EXTRAS` order, each read from its own on-disk state.

    The single definition of both the row set and the row count, so the widget
    and a test that counts rows are counting the same list. One probe raising
    costs that row its answer and nothing else: an unreadable extra is not a
    reason to hide the other seven.

    `installed` stays `None` when the probe raised - `bool(None)` is `False`,
    which is exactly the "installed extra reported absent" claim this page must
    never make on a machine where reading the state failed.
    """
    out: List[_Extra] = []
    for key, label, what, probe, module in _EXTRAS:
        source = _extra_source(module)
        try:
            installed, detail = probe(config)
        except Exception as exc:  # noqa: BLE001 - unknown is a state this page can show
            logger.debug("extra %s could not be read", key, exc_info=True)
            installed, detail = None, f"could not be determined - {_clip(exc)}"
        out.append(_Extra(key, label, what, None if installed is None else bool(installed), str(detail), source))
    return out


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


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


def _no_markup(widget: Gtk.Widget) -> None:
    """Switch Pango markup off on a row built by `common.row()`.

    Capability-checked rather than typed, and the reason is this module's
    docstring: the row is an `Adw.ActionRow` when libadwaita is there and a
    plain `Gtk.Box` when it is not, only the first has the switch, and naming
    the type would mean importing `Adw` in a module whose whole point is that
    it does not ask for libadwaita.
    """
    setter = getattr(widget, "set_use_markup", None)
    if callable(setter):
        setter(False)


class _RowText:
    """A row's title or detail, readable the way the old labels were.

    `Adw.ActionRow` keeps both in labels libadwaita does not hand back, so this
    is the string the row displays. What `_row_title` and `_row_detail` are
    for has not changed: a caller checks a row's wording against the function
    that produced it, which a shim over that exact string still allows.
    """

    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text


def _access(widget: Gtk.Widget, label: str) -> None:
    """Set the accessible LABEL.

    Its own call because it is the only part of a row a screen reader acts on,
    and because some PyGObject builds have no getter to read it back - the
    tooltip carries the same words and is assertable.
    """
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])


def _note(text: str) -> Gtk.Label:
    label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
    label.set_text(text)
    label.add_css_class("dim-label")
    return label


def _row(title: str, detail: str, tooltip: str, accessible: str) -> Gtk.Widget:
    """One line of the page: a title, a plain detail, and both texts carried on
    the tooltip and the accessible label so either can be read without a
    screen."""
    row = common.row(title, detail)
    # After the title, not before: `common.row()` sets both, and a title
    # carrying markup warns once on stderr here before rendering as plain text.
    _no_markup(row)
    row.add_css_class("model-row")
    row.set_tooltip_text(tooltip)
    _access(row, accessible)
    row._row_title = _RowText(title)
    row._row_detail = _RowText(detail)
    return row


class _ModelSurface(Gtk.Box):
    """The page's content: Reload, the summary, the three groups, and the
    answer.

    The page itself - title, subtitle, toolbar, scrolling - is
    `common.surface()`'s. This is only what goes inside it, which is why
    `build()` returns the page and forwards the accessors below onto it.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._app = app
        self._model_rows: List[Gtk.Widget] = []
        self._engine_rows: List[Gtk.Widget] = []
        self._extra_rows: List[Gtk.Widget] = []

        self._summary = _note("")
        self._serving = _note("")

        # The panel's own health, above every group: is the
        # model set up, is an engine answering, or could it
        # not be told? One row, one dot, one word - the
        # question the panel is opened for, before the rows
        # that hold the model.
        #: The panel's health, written once into the row above and read back
        #: for the dot on its sidebar row. Same value, so the dot cannot
        #: disagree with the sentence directly above it.
        self.status_recorder = common.StatusRecorder()
        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.append(self._status_slot)

        # Reload sits at the top of the content rather than in the page's
        # header bar: `common.surface()` builds that bar and offers no slot for
        # a button, and reaching into its tree to find one would make this page
        # depend on how it built it.
        #
        # **It is a banner, not a bare button**, and that is the same change
        # `voice.py` made. As a right-aligned button it gave a reading and a
        # control and never said the reading was from whenever the panel was
        # built - and the sidebar builds a panel once and keeps it, so on this
        # panel that can be a model downloaded minutes ago that is still reported
        # as absent. The banner carries the reason and the control together, and
        # its button inherits a tooltip and an accessible label from that reason.
        self._reload_button = common.banner(
            "Read once, when this panel was built. Nothing here is polled - press "
            "Reload to read the model, the engines and the extras from this "
            "machine again.",
            "Reload",
            lambda: self.refresh())
        self.append(self._reload_button)
        self.append(self._summary)

        # The three groups are built once and emptied on every read, so the
        # rows a previous read put in them are removed by reference.
        self._model_group = common.group("The model")
        self._engine_group = common.group("Which engine answers")
        self._extra_group = common.group("Optional extras")
        for group in (self._model_group, self._engine_group, self._extra_group):
            self.append(group)

        self.append(self._serving)
        self.append(_note("Nothing on this page downloads a model, starts a service or changes a setting. "
                          "Run `shani-chronoa --setup` to add an extra."))

        self.refresh()

    # -- content ------------------------------------------------------------

    def _status_row(self) -> Gtk.Widget:
        """The panel's own health, from the model and its engines.

        **The two states that have no model carry a button, because they are the
        two a person can undo.** "No model is chosen" and "no engine answers"
        both used to be sentences naming the setup wizard, which is a second
        window with no link from here - and this panel is the answer to "why is
        Chronoa not answering", so the place that explains the problem is the last
        place that should make you go looking for the fix. The states where the
        model is chosen and an engine will run it do not get one: there is nothing
        to fix, and a button on every row would be noise.
        """
        app, config = self._app, getattr(self._app, "config", None)
        hardware, profile = _hardware(app)
        model, origin = _model_name(app, config, hardware)
        engines = _engines(config, model)
        if not model:
            widget = self.status_recorder.row(
                common.STATUS_ATTENTION,
                "No model is chosen",
                f"{origin}; the setup wizard chooses one")
            return self._with_setup(widget)
        if not engines:
            widget = self.status_recorder.row(
                common.STATUS_ATTENTION,
                "No engine answers",
                f"{model or '(none chosen)'}, {origin}; no engine "
                "is available to run it")
            return self._with_setup(widget)
        available = sum(1 for e in engines if e.state)
        chosen = _selected_engine(app)
        if available:
            return self.status_recorder.row(
                common.STATUS_OK,
                f"{model or '(none chosen)'} - {available} of "
                f"{len(engines)} engines available",
                f"{origin}; {available} of {len(engines)} engines "
                f"can run it. {chosen}")
        return self.status_recorder.row(
            common.STATUS_ATTENTION,
            f"{model or '(none chosen)'} - no engine available",
            f"{origin}; none of the {len(engines)} engines can run it")

    def _with_setup(self, widget: Gtk.Widget) -> Gtk.Widget:
        """A status row plus the one button that can act on it.

        A column rather than a bare concatenation, because `refresh()` empties the
        status slot on every reload and this has to go with it - a banner left
        behind after the model appears would be a button offering to install a
        model that is installed.
        """
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        column.append(widget)
        button = Gtk.Button(label="Set Chronoa up")
        button.add_css_class("suggested-action")
        button.set_halign(Gtk.Align.CENTER)
        button.set_tooltip_text(
            "Open the setup wizard, which is the only thing that downloads a "
            "model (Ctrl+Shift+S)")
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               ["Set Chronoa up: download a model"])
        button.connect("clicked", lambda _b: common.open_setup(self._app, self))
        column.append(button)
        return column

    def refresh(self) -> None:
        """Re-read everything and rebuild the rows.

        `Reload` calls this, and so does `build()`. There is nothing to clear
        but the widgets: every value on this page is read per call, because
        "available right now" is a claim about the moment it was read.
        """
        app, config = self._app, getattr(self._app, "config", None)
        common.clear(self._status_slot)
        self._status_slot.append(self._status_row())
        for group, previous in ((self._model_group, self._model_rows),
                                (self._engine_group, self._engine_rows),
                                (self._extra_group, self._extra_rows)):
            for row in previous:
                group.remove(row)
        self._model_rows, self._engine_rows, self._extra_rows = [], [], []

        hardware, profile = _hardware(app)
        model, origin = _model_name(app, config, hardware)
        engines = _engines(config, model)
        extras = _extras(config)

        override = _read_setting(config, "hardware_profile", "auto")
        tier = f"Hardware profile: {profile or 'unknown'}"
        if override and override not in ("auto", profile):
            tier += f" (Settings ask for {override}; detection says {profile or 'unknown'})"
        elif override == "auto":
            tier += " (detected, nothing set in Settings)"
        self._summary.set_text(f"{tier}. Model: {model or 'none chosen'} - {origin}.")

        file_title, file_detail = _model_file_detail()
        self._model_rows = [
            _row("Model", model or "(none chosen)",
                 f"The model in use: {model or 'none chosen'}. {origin}.",
                 f"Model: {model or 'none chosen'}, {origin}"),
            _row("Hardware profile it was chosen for", profile or "unknown",
                 f"The tier the choice came from: {profile or 'unknown'}. {tier}.",
                 f"Hardware profile: {profile or 'unknown'}. {origin}"),
            _row(file_title, file_detail,
                 f"{file_title}: {file_detail}\nRead from {local_llm.model_dir()}",
                 f"{file_title}: {file_detail}"),
        ]
        for built in self._model_rows:
            _add(self._model_group, built)

        for engine in engines:
            state = _state_word(engine.state, AVAILABLE, UNAVAILABLE)
            built = _row(f"{engine.label} - {state}", engine.detail,
                         f"{engine.label}: {state}\n{engine.detail}\nRead from {engine.source}",
                         f"{engine.label}: {state}. {engine.detail}")
            built._engine_key = engine.key
            built._engine_state = engine.state
            self._engine_rows.append(built)
            _add(self._engine_group, built)

        for extra in extras:
            state = _state_word(extra.installed, INSTALLED, NOT_INSTALLED)
            built = _row(f"{extra.label} - {state}", f"{extra.what}; {extra.detail}",
                         f"{extra.label}: {state}\n{extra.what}\n{extra.detail}\n"
                         f"Read from {extra.source}\nInstall it with: shani-chronoa --setup",
                         f"Extra {extra.label}: {state}. {extra.what}. {extra.detail}")
            built._extra_key = extra.key
            built._extra_state = extra.installed
            self._extra_rows.append(built)
            _add(self._extra_group, built)

        self._serving.set_text(_serving(engines))

    # -- for tests, and for any surface that wants the same reads ------------

    def model_rows(self) -> List[Gtk.Widget]:
        return list(self._model_rows)

    def engine_rows(self) -> List[Gtk.Widget]:
        return list(self._engine_rows)

    def extras(self) -> List[Gtk.Widget]:
        return list(self._extra_rows)

    def extra_count(self) -> int:
        return len(self._extra_rows)

    def installed_keys(self) -> List[str]:
        return [row._extra_key for row in self._extra_rows if row._extra_state is True]

    def unknown_keys(self) -> List[str]:
        return [row._extra_key for row in self._extra_rows if row._extra_state is None]

    def summary(self) -> str:
        return self._summary.get_text()

    def serving(self) -> str:
        return self._serving.get_text()

    def reload_button(self) -> Gtk.Button:
        """The Reload control inside the banner.

        The banner is the widget on screen; this is the button inside it, which
        is what `reload_button()` has always meant to callers.
        """
        child = self._reload_button.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                return child
            child = child.get_next_sibling()
        return self._reload_button


#: What `build()` forwards from the content onto the page it returns. A
#: libadwaita page cannot be subclassed here (that would mean importing `Adw`),
#: and a caller holding only the page still has to be able to ask it what it is
#: showing - which is the whole point of these accessors.
_FORWARDED = ("model_rows", "engine_rows", "extras", "extra_count",
              "installed_keys", "unknown_keys", "summary", "serving", "reload_button")


def build(app: Any) -> Gtk.Widget:
    """Build the model page for `app`. Reads state; writes nothing."""
    content = _ModelSurface(app)
    page, set_content = common.surface(TITLE, SUBTITLE)
    set_content(common.scrolled(content))
    for name in _FORWARDED:
        setattr(page, name, getattr(content, name))
    # What this panel says about itself, for the sidebar's health dot, from the
    # same recorder the row at the top of the panel was written through.
    page.status = content.status_recorder.status
    return page


__all__ = ["TITLE", "ICON", "build"]
