# Agent instructions — shani-chronoa

This file applies to any AI coding assistant working in this repository
(Claude Code, opencode, Kilo Code, Cursor, Aider, or similar). Read this
before editing, and follow the verification steps before calling any change
done.

## Start here (fast path)

**Always read these first:**
- `What this repo is`
- `Empirical verification (mandatory)` and
  `Rule: verify by actually running it, not by reading it`
- `Required verification for a change`
- `Boundaries`
- `Commit discipline`
- `Cross-repo impact`
- `Package layout and renames` - older sections use pre-2026-10-02 file names
- `ARCHITECTURE-TARGET.md` - the architecture Chronoa is aiming for, every box
  mapped to today's code, and the ordered gap list. Update its row when you
  fill a box; read its Part 4 before building anything it marks 🚫

**Read when your change touches them:**
- `Rendering the UI to a PNG` — GTK4/Adw init ordering and the capture path
- `Machine-state senses` / `Senses layer` — the sense contract
- `Settings window`, `Model choice`, `Packaging` — the two naming
  conventions must not be mixed

**Current known issues — read this before you start:**
- `Audit-verified known issues` and `Garuda Cross-Reference Findings` —
  dated retrospectives. **Grep for the subsystem you are changing.** Full
  methodology in `AUDIT-HISTORY.md`.

  This section mixes fixed history with issues that are **still open**,
  including Critical security ones. Grep it for `not fixed`,
  `still open`, and your subsystem name before you touch anything.

**Background reference — skippable, pure survey material:**
- `Deliberately not done` — read before proposing a feature, so you don't
  rebuild something intentionally excluded.

**Never skip:** running it. Reading the diff has never been sufficient here.

## What this repo is

A local-first GTK4 voice/text AI assistant for Shanios: `whisper.cpp` for
STT, Ollama for LLM inference and tool-calling, Piper for TTS, real mic
capture and audio playback via `pw-record`/`pw-play` (falling back to
`arecord`/`aplay`), plus optional hands-free wake-phrase activation
(`wakeword.py`: vad.py segments utterances, whisper.cpp transcribes each one
biased toward the phrase, matched at the start; openWakeWord was dropped
2026-10-01 - neither it nor onnxruntime/tflite is in Arch's official repos). Listening auto-stops on silence
(`AudioRecorder.start_auto_stop`, `vad.py`) rather than requiring a second
button press - pressing the orb again while listening just ends the turn
early (`cancel_auto_stop`). Barge-in has two layers: talking again always
interrupts TTS mid-reply (`AudioPlayer.stop()`, unconditional), plus an
opt-in continuous-VAD layer (`BargeInMonitor`, gated behind
`barge-in-vad-enabled`, off by default - no acoustic echo cancellation, so
it can self-interrupt on speaker output without headphones).
`tools.py` + `assistant.py` are what make it an actual task-doing assistant
rather than a chatbot: a fixed whitelist of local system actions (open an
app, volume, mute, datetime, timers, battery status, web search) wired
through Ollama's `/api/chat` `tools` parameter — deliberately not a generic
shell-exec tool. When Ollama isn't available, `cloud_llm.py` provides an explicit, opt-in
fallback to free, no-API-key cloud LLM gateways (LLM7, Kilo Gateway,
BlockRun) - gated behind BOTH privacy mode being off AND a separate
`cloud-fallback-enabled` gsetting, so it never activates from a single
switch (see `app.py`'s `_maybe_enable_cloud_fallback`). Each free provider
also supports an optional user-supplied API key (BYOK, `*-api-key`
gsettings) to raise that provider's anonymous rate limits - Kilo's own
429 error during live testing explicitly suggested this. Four more
providers - Anthropic, OpenAI, Google Gemini, Groq - are BYOK-*required*
(confirmed live: all four reject an unauthenticated request outright) and
are tried ahead of the free chain when a key is configured; Anthropic uses
its own native-protocol adapter (`AnthropicLLM`) since it isn't
OpenAI-compatible, the other three reuse `OpenAICompatibleLLM`. Each action is a "skill" module under `shani_chronoa/skills/`
(see that package's docstring for the plugin contract); a user can add their
own by dropping a same-shaped module into `~/.config/shani-chronoa/skills/`
without touching this codebase. Those same skills are also exposed as a real
MCP (Model Context Protocol) server (`mcp.py`, run standalone via the
`shani-chronoa-mcp` binary) so any MCP-speaking tool - Claude Desktop, Claude
Code, Cursor - can call them too; this replaced an earlier `mcp.py` that
only looked like MCP support (see the bug list below). Packaged via both an
Arch `PKGBUILD` and a `DEBIAN/control` (two different package managers, keep
dependency names in the right convention for each — see below).

## Package layout and renames (2026-10-02) - read before grepping

The package was restructured for long-term maintenance on 2026-10-02. **Older
sections of this file keep the names that existed when they were written**;
use this map to translate them. Moving code changed no behaviour; the bugs
that linting turned up were fixed separately and are listed below.

| Older sections say | Now |
|---|---|
| `app.py` | `app/` - `application.py` (`ChronoaApplication`), with `voice`, `brain`, `conversation` and `desktop_integration` mixins, plus `common` |
| `gui.py` | `gui/` - `window.py` (`ChronoaWindow`), `style`, `asking`, `attaching`, `conversations_menu`, `widgets`, `questions` |
| `settings_window.py` | `settings_window/` - `window`, `senses`, `privacy`, `voice`, `activity` |
| `triggers.py` | `triggers/` - `common`, `sources`, `desktop_sources`, `rules`, `event_rules`, `events` |
| `office.py` | `office/` - `common`, `read`, `write`, `edit` |
| `sandbox/executor.py` (the whole thing) | `executor.py` keeps `SandboxExecutor` and the bwrap/Landlock probes; `child.py` does the in-child hardening (`_harden_child`, `_die_with_parent`, core dumps); `commands.py` works out which program an argv really runs (`_blocked_binary`); `limits.py` holds the profile ceilings and the seccomp request (`ProfileLimitError`) |
| `llm.py` | `ollama_llm.py`. The default local brain is now llama.cpp (`local_llm.py`, user unit `shani-chronoa-llm.service`); Ollama stays as an option |
| `models.py` | `model_choice.py` |
| `sessions.py` | `conversation_store.py` (many conversations, plus FTS5 search) |
| `calendar.py` | `eds_calendar.py` (it shadowed the stdlib module) |
| `desktop.py` | `desktop_session.py` |
| `secrets_manager.py`, `vault.json` | **removed** - the vault was XOR plus base64, not encryption. Keys live in GSettings; `redaction.py` (`Redactor`) is what keeps them out of logs, tool output and memory |
| `gateway_supervisor.py` | **removed** (it was dead code). `child_supervisor.py` is the part that was live |

Rules the split follows, so the next change keeps them:

- **A package's `__init__` re-exports its old public names**, so
  `from shani_chronoa.triggers import RuleStore` still works. Code inside the
  package imports from the submodule that defines a name, never from
  `__init__`, which keeps the import graph acyclic.
- **Patch a name where it is read, not where it is re-exported.** After a
  move, `monkeypatch.setattr(executor_mod, "_PR_SET_PDEATHSIG", -1)` sets a
  copy nobody reads, and the test still passes. Two tests did exactly that
  and were repointed to `sandbox.child`. Test the patch: make it break the
  code and confirm the test fails.
- **Data paths are resolved per call, never at import.** `triggers_dir()`,
  `rules_file()` and `screenshot.output_dir()` are functions because, as
  import-time constants, they pointed the test suite at the real
  `~/.local/share/shani-chronoa` (178 fake screenshots and a `triggers/` tree
  ended up there). `tests/conftest.py`'s
  `_trigger_and_capture_paths_are_inside_the_test` guard fails any test that
  writes there.
- **Tests that read source as text** use `tests/_source.py`'s
  `package_source("shani_chronoa.app")`, which concatenates a package's
  modules, so a split does not break them.
- **Lint with pyflakes.** It found a real crash (`DEFAULT_COOLDOWN_SECONDS`
  never imported in `skills/manage_triggers.py`) and a synonym table in
  `tool_select.py` whose duplicated keys silently dropped `edit_image` for
  "fix up my photo". The only expected warnings are the re-exports in
  package `__init__.py` files.

## Empirical verification (mandatory)

**Reading code is analysis; running code is verification.** A change is not
verified by reading the diff, running `bash -n`, or confirming it "looks
correct." It is verified by observing the actual behavior of the real
thing in the real environment — built, served, deployed, signed, running.
If you haven't seen it work (or fail) for real, it isn't verified.

## Test harness: shani-testbed (use it - and improve it, never invent around it)

The ecosystem's real test harness is the sibling repo **`../shani-testbed`**
(read its `README.md` and `AGENTS.md`). It installs a real ShaniOS image with
the real installer, boots its slots (`systemd-nspawn`, and UEFI + TPM VMs),
runs real deploys and rollbacks, drives GUI apps through their accessibility
tree, and checks web pages in a real headless browser. Every command runs from
`../shani-install-media`, which provides the builder container:

```bash
cd ../shani-install-media
./run_in_container.sh build.sh test <command> ...   # `... test help` lists them all
```

**If the check you need does not exist, add it to shani-testbed - do not invent
around it.** A one-off script in this repo, a scratchpad, or a heredoc piped
into a container is lost when the session ends, and the next agent re-derives
it. Extend the harness instead (see "Extend the harness" in its AGENTS.md):

- an in-slot check -> `shani-testbed/slot-tests/<name>.sh` (`# slot-test-mode: boot`,
  prints `RESULT <name> PASS|FAIL|SKIP` lines), run by `slot-test <slot> <name>`;
- a GUI interaction or assertion -> an `app` action in `lib/app.sh`, or a walk
  through a real app as `app-scripts/<app>.actions`;
- a web check -> `lib/web_client.py`;
- a new way to boot, drive or observe -> a command or option in `lib/`;

each with a negative control (a check that cannot fail is not a check), its
self-test (`tests/run-app-actions.sh`, `tests/run-web-client.sh`, ...), and the
`usage` + README updated. One harness run at a time: disk-touching commands
take `disk/.testbed.lock` and a second run is refused. Plain nspawn boots see
the image's whole `/var`; real boots have an empty tmpfs `/var`
(`systemd.volatile=state`) - use `slot-test --volatile`, or a real UEFI boot
with `iso-install --boot-only --console-exec=CMD`, for anything touching `/var`.

### What to run for this repo

- `slot-test <slot> chronoa-senses chronoa-machine-state --local-src-chronoa=/opt/shani-chronoa`
  checks the senses against the real Arch image (run_in_container.sh mounts
  this checkout at `/opt/shani-chronoa`).
- `slot-test <slot> repo-pytest` runs this repo's suite on the image's
  Python/GTK; Landlock and seccomp tests need the real kernel:
  `iso-install --boot-only --console-exec=...`.
- `app <slot> --run=shani-chronoa --strict ...` drives the real window.

## Rule: verify by actually running it, not by reading it

This repo has shipped multiple bugs that read as completely correct and
only broke when actually executed — this is not hypothetical caution, it's
what happened on the first two passes here (2026-09-16):
- A GTK4-removed API (`Gtk.Button.set_relief`/`Gtk.Relic`, GTK3-only
  concepts) that crashed the app on first window creation.
- `async def` handlers (`_process_input`, `_start_listening`) invoked via
  `GLib.idle_add`/plain calls with no asyncio loop anywhere — the
  coroutines were created and silently garbage-collected, so the entire
  chat pipeline was dead code. `bash -n`/`python -m py_compile` catches
  none of this; only actually running it and seeing the LLM never
  respond does.
- A bad type *annotation* on a function parameter
  (`Gio.CommandLine` instead of `Gio.ApplicationCommandLine`) that crashed
  the whole module on import — but the equivalent-looking
  `self.x: Optional[Foo] = None` attribute annotation does NOT get
  evaluated at runtime and is harmless. These look identical on the page
  and behave completely differently; verify, don't assume from the shape
  of the code.
- `super().do_startup()`'s vfunc chain-up throws a `TypeError` on at least
  one real PyGObject build (3.48.2) and caused an actual segfault
  (GLib-GIO-CRITICAL "failed to chain up on ::startup", then SIGSEGV).
  Fixed with the explicit class-qualified call, `Gtk.Application.do_startup(self)`.
- `org.shani.chronoa.gschema.xml` used an invalid `schema="..."` attribute
  on `<key>` and was missing required `type="s"/"b"` attributes —
  `glib-compile-schemas` silently discarded the *entire file* (exit 0, no
  visible error), so every setting silently fell back to hardcoded
  Python defaults. Always run `glib-compile-schemas` on any schema
  change and check it actually produced `gschemas.compiled` — a clean
  exit code alone proves nothing here.
- The orb button (`ChronoaOrbWidget`, the mic icon — Chronoa's primary
  voice-input control) was a `Gtk.Button` never connected to anything:
  no `set_action_name`, no `clicked` handler. Clicking it did nothing;
  voice input was only reachable by calling the `app.toggle-listening`
  GAction directly (e.g. over D-Bus), which nothing in the GUI did. Reading
  `gui.py` alongside `app.py`'s action definitions looked complete — the
  missing wire only showed up by constructing the real window and checking
  `orb.get_action_name()` / firing a real `clicked` signal.
- `wakeword.py`'s first draft called `openwakeword.model.Model(wakeword_models=["hey_jarvis"])`
  and read scores back with `scores.get("hey_jarvis", 0.0)`. Both looked
  reasonable and neither raised on inspection. Actually loading the
  installed package: `Model.__init__` takes `wakeword_model_paths` (file
  paths, not bare names — `TypeError` otherwise), and a loaded model's score
  key is the resolved filename stem (`"hey_jarvis_v0.1"`), not the short
  name — so `scores.get("hey_jarvis", 0.0)` would have silently returned
  `0.0` forever, never once firing. Since exactly one model is ever loaded,
  the fix reads `max(scores.values())` instead of indexing by a guessed key.
  Also: it assumed `pw-record ... -` writes a 44-byte WAV header before raw
  PCM (matching the file-recording path in `audio.py`) and skipped it — a
  real capture + hex dump of the first 200 bytes on this machine's mic
  showed no RIFF header at all when streaming to stdout; the skip was
  silently discarding the first 80ms of every wake-word check. Both bugs
  were confirmed **and fixed** by actually loading the real
  `openwakeword` package and capturing real mic audio, not by reasoning
  about the API from its usage elsewhere.
- Both `usr/bin/shani-chronoa` and (when it was added) `usr/bin/shani-chronoa-mcp`
  computed the package path as `dirname(dirname(__file__))` — for a script
  at `usr/bin/foo` that's `usr/`, but the actual package lives at
  `usr/lib/shani-chronoa/shani_chronoa/`. Every previous verification pass
  in this file ran code via `sys.path.insert(0, '.')` from inside
  `usr/lib/shani-chronoa` directly and never actually invoked the installed
  launcher script, so this shipped for the entire life of the project
  without anyone noticing: running `usr/bin/shani-chronoa` directly has
  always raised `ModuleNotFoundError: No module named 'shani_chronoa'`.
  Found only by actually running the launcher script itself, not the
  package it imports. Fixed by joining `lib/shani-chronoa` onto that
  directory in both scripts.
- `mcp.py`'s real implementation needed the `mcp` package's actual
  `add_tool()` behavior, not an assumption: it infers a tool's JSON schema
  by inspecting the Python function's signature, with **no path to hand it
  a pre-built JSON schema directly**. The obvious-looking shortcut - a
  generic `def _wrapper(**kwargs)` - produces a broken schema (one literal
  field named `"kwargs"` of type string) and fails validation on every real
  call. Setting `__signature__` to a real `inspect.Signature` built from the
  skill's existing schema is what actually works; confirmed end-to-end
  (zero-arg, required, optional-with-default, int/string/bool params) by
  driving the real installed `shani-chronoa-mcp` binary as a subprocess
  over genuine stdio JSON-RPC with the real `mcp` client SDK, not just by
  calling functions in-process.
- `vad.py`'s first draft used a hardcoded RMS silence threshold (400). A
  real 2-second capture from this dev machine's actual mic - nobody
  speaking, just ambient/fan noise - measured a peak of ~2046 and a mean of
  ~592, five times the hardcoded value; a fixed threshold would have
  registered continuous "speech" and never auto-stopped on any real
  machine with normal ambient noise. Fixed by calibrating against a short
  real sample of ambient audio at the start of each session
  (`calibrate_noise_floor()`) instead of a literal. Separately, this dev
  sandbox's mic does not pick up its own speaker output at all (played a
  generated tone through `pw-play` while recording; RMS stayed at ambient
  levels the whole time) - almost certainly a virtual/cloud audio backend
  with no real acoustic path between output and input. That means
  `BargeInMonitor`'s actual real-world false-positive rate from speaker
  bleed (the reason it's off by default) could NOT be verified acoustically
  here - only the detection logic itself was verified, via synthetic PCM
  fed directly into the same code path. Verify the acoustic behavior for
  real on a machine with physical speakers/mic before enabling it by
  default anywhere.
- `app.py`'s first draft of `_llm_status_text()` checked `self.llm is
  None` to decide whether to show "LLM unavailable" - but `self.llm` is
  *never* actually `None` when Ollama is unreachable, it's still a live
  `OllamaLLM` instance that just fails `.is_available()` (the same
  distinction `_submit()`'s existing guard already made correctly). Running
  it for real against this Ollama-less dev machine caught it immediately:
  the status bar claimed "Ready (Ollama: qwen3:4b)" while Ollama was
  completely unreachable. Fixed to check `.is_available()` explicitly.

None of these were caught by type-checking, linting, or reading the diff
carefully — only by constructing the actual GTK objects / running
`glib-compile-schemas` / driving the real GLib main loop and watching what
happens.

## Rendering the UI to a PNG, and what it cost to work out (2026-09-28)

The UI defects in this app's history - `hwmon` and `thermalgrid` shown to users
as module names, reply markdown displayed as `**38G**`, near-white text on a
light theme at a measured 2.80:1 - were all invisible to a suite that
constructed the widgets and walked the tree. Constructing a widget is not the
same as it being legible. `Gsk.CairoRenderer` renders a widget tree offscreen
with no compositor and no window manager, which is the only way to *look*.

**Three requirements, all of them silent on failure. Each returned a plausible
wrong answer rather than an error, which is what makes them worth writing down:**

- **`Adw.init()` must come before any Adw widget is constructed, and before the
  `Gtk.Application` exists.** Initialise it at module scope. The settings window
  is entirely Adw widgets and renders *nothing* without it - no node, no
  exception, just an empty PNG. Calling it inside `activate`, after the window
  was built, does not help.
- **`present()` must happen a main-loop turn before the snapshot.** Inside
  `GApplication::activate` the tree is unallocated (`0x0`, `get_mapped()` False),
  and pumping the context does not help: with nothing pending,
  `iteration(False)` returns immediately. Worse, calling `present()` *again*
  from inside the capture function re-maps the window and the snapshot catches
  a tree with no node.
- **`Gdk.Texture.save_to_png_bytes()` fails on the texture a
  `Gsk.CairoRenderer` produces; `save_to_png(path)` works on the same texture.**
  The bytes form returns nothing with no error, which is indistinguishable from
  a window that rendered blank.

Also: `Gsk.CairoRenderer.new_for_surface` is inherited from `Gsk.Renderer` and
refuses to construct the subclass (`TypeError`); use `new()` then `realize(None)`,
because `render_texture` asserts on being realized. And an `Adw.Application`
id is a D-Bus well-formed name - one containing `Adwaita` is invalid, registers
badly enough that the window never maps, and produces the same empty PNG.

**Measure the pixels; do not trust a description of them.** A written
description of the pre-fix screenshot called the header "black text, clearly
readable" when it measured 2.80:1 and the glyphs were the page's own lightness.
Current measurements: light theme 11.61:1, HighContrast 13.88:1 main window and
17.58:1 settings. A skip is silent, so a display probe that is wrong is worse
than no probe - `Gdk.Display.get_default()` returns None until GTK is
initialised, and the first version of that probe skipped every test on a
machine with a live X session.

`render_ui.py` in the repo root is that renderer, working and committed - it
prints a PNG for `main`, `empty`, `settings` and `help`, and takes an optional
theme argument. It must be run as a script, not imported into a test.

**A visual regression suite was written, could not be made to fail, and was
deleted rather than shipped.** The measurements, so nobody re-derives them:

- A pytest wrapper returned empty PNGs when built in-process; shelling out to
  `render_ui.py` fixed that, and 15 tests passed.
- Restoring the pre-theming `gui.py` (hardcoded `#14141f` background) left all
  15 **passing**, and the header measured 9.89:1 - not the 2.80:1 that the
  original investigation recorded. The regression could not be reproduced.
- The "not a blank page" assertion (`> 50 distinct colours`) was aimed at the
  `Adw.init()` failure above. Rendering the settings window with `Adw.init()`
  omitted still produced **345** distinct colours against **350** for the
  working build - the two are indistinguishable by that measure.

So every assertion in it passed regardless of the state it was meant to
catch. A test that cannot fail is the same failure as a test that always
passes, and is worse than no test because it reads as coverage. The renderer
is the durable artefact; the assertions were not salvageable.

## Commit discipline

Before composing a commit message, run `git log --oneline -20` (and `git
log -5 -- <touched paths>` for the files you changed) and match the
existing style — subject shape, scope prefixes, body detail level —
rather than writing in a generic format.

## Boundaries

- ✅ **Always**: grep for real callers before trusting a `feat:` commit
  message or a passing module-level unit test as proof something is live
  — this repo has shipped multiple modules that were fully built,
  unit-tested, and never actually wired into anything that runs (see
  "Audit-verified known issues" below).
- ⚠️ **Ask first**: adding an MCP *client* (consuming external servers) —
  deliberately not done; it conflicts with the fixed-whitelist skill
  design's whole safety rationale and needs its own trust story first, not
  a quick addition.
- 🚫 **Never**: turn `tools.py`'s skill whitelist into a generic
  shell-exec tool, even as a convenience shortcut — that's the specific
  design boundary that makes this an "actual task-doing assistant" safe to
  run LLM-issued commands through, not an accident to "simplify" away.
- 🚫 **Never**: delete or skip a failing test to make a build/CI pass — fix the underlying code, not the test. A red test is signal; silencing it destroys the signal, not the bug.

*Maintenance note: this file is well past 300 lines. Keep new entries
terse and current-state; consider a dated `AUDIT-HISTORY.md` split (the
pattern already used in shani-docs/shani-install-media/shani-deploy/
shani-builder) if it keeps growing.*

## Required verification for a change

```bash
python3 -m py_compile shani_chronoa/*.py shani_chronoa/skills/*.py   # syntax only, not sufficient alone

# Actually construct the GTK objects — catches API-removed/renamed bugs
# that py_compile can't see:
python3 -c "
import gi; gi.require_version('Gtk','4.0')
from shani_chronoa.gui import CajitaWindow, ChronoaOrbWidget
from gi.repository import Gtk
app = Gtk.Application(application_id='test.chronoa')
app.connect('activate', lambda a: (CajitaWindow(a), a.quit()))
app.run([])
"

# If you touch gschema: verify it actually compiles, don't just eyeball the XML
mkdir -p /tmp/schema-test && cp usr/share/glib-2.0/schemas/*.xml /tmp/schema-test/
glib-compile-schemas /tmp/schema-test/   # must produce gschemas.compiled with no errors

# If you touch app.py's async wiring: prove a coroutine actually executes
# through AsyncBridge inside a real GLib.MainLoop, not just that it compiles
# (see asyncbridge.py's own module docstring for why this matters).
```

### Measured on a real Shanios slot (2026-10-01), not inferred

`shani-testbed/slot-tests/chronoa-speech.sh`, on `@blue` (`shanios-20260925-gnome`),
with the checkout overlaid via `--local-src-chronoa`: **11 pass, 0 fail.**
The same slot with the overlay removed: **3 pass, 8 fail**, including
`ModuleNotFoundError: No module named 'shani_chronoa'`. Both runs are the
evidence; either alone proves nothing.

**Speech output works on Shanios today, and `espeak-ng` is load-bearing.**
`PiperTTS().engine()` resolves to `espeak-ng` and `synthesize()` wrote a real
81,698-byte RIFF/WAVE of 1.85s (confirmed with `ffprobe`). That is why
`espeak-ng` is a hard `depends` and the other tiers are `optdepends` — drop it
and every image loses speech output entirely, because RHVoice is packaged
separately and the neural engines (Kokoro, Piper) are opt-in downloads that
are not there on a fresh install.
**Correction (2026-10-02):** this said "piper is not installable on Arch (no
`onnxruntime`)". The runtime is in `extra` now, and Kokoro is the neural voice
that unblocked; `engine()` is a four-way chain, not a three-way one, and the
measured cost of its first entry is in `Kokoro TTS` above. The conclusion — that
`espeak-ng` must stay the only hard `depends` — is unchanged.

**Speech input is absent by design, not by breakage.** `is_available()` is
`False` and `app.py`'s `"Whisper.cpp not available - STT disabled"` is the
branch actually taken. `whisper-cpp` is an `optdepend` that no image profile
installs, and no `ggml-*.bin` exists on any install. Do not "fix" this by
hard-depending `whisper-cpp`: a user without it must get a working assistant
that says so, not a half-configured one whose microphone silently cannot work.
`test_the_piper_voice_dir_moves_with_the_data_home` guards exactly that, and
also that no hard `depends` entry ever starts with `piper` — on Arch `piper`
is a gaming-mouse configurator, not Piper TTS.

An absent dependency must never be a confident wrong answer. The distinction the
slot-test enforces: STT reporting `False` is a PASS, and STT claiming ready with
no binary and no model would be a FAIL.

`whisper.cpp`, `piper`, and `ollama` are unlikely to be installed on a
generic dev machine — STT/LLM/TTS will report unavailable and the app
should degrade gracefully (it's designed to: check `is_available()` on each
component before use). That's a legitimate environment limitation, not a
reason to skip the checks above. If you change the tool-calling loop in
`assistant.py`, verify it with a mocked Ollama endpoint
(`httpx.MockTransport`) simulating a `tool_calls` response — you don't need
a real Ollama server to prove the round-trip logic (parse tool call →
execute → feed result back → final answer) actually works.

`httpx`, `numpy`, and `openwakeword` are not guaranteed to be on a bare
system Python either (`pip install --break-system-packages` is refused by
design on Debian-family systems) — use a `venv --system-site-packages` if
you need to exercise `llm.py`/`assistant.py` (needs `httpx`) or
`wakeword.py` (needs `numpy` + `openwakeword`) for real; `--system-site-packages`
keeps `gi`/GTK reachable since those aren't pip-installable. If you touch
`wakeword.py`, don't trust the `openwakeword` API from memory or from other
projects' usage of it — versions differ (this repo's dev pass found
`Model(wakeword_models=[...])` doesn't exist on the installed version; it's
`Model(wakeword_model_paths=[...])`, taking file paths under
`<package>/resources/models/`, not short names) - actually import it and
call it. If you touch the skill-loading path (`skills/__init__.py`,
`tools.py`), verify with a throwaway module dropped in a temp dir pointed
at by `skills._USER_SKILLS_DIR` — both a well-formed one and a deliberately
malformed one (e.g. `SKILLS` not a list — an earlier draft here iterated a
plain string character-by-character instead of rejecting it).

If you touch `cloud_llm.py`: these are free, keyless, third-party gateways
with volatile availability - live-tested on 2026-09-16 from this dev
machine, two of three configured providers (Kilo, BlockRun) were already
rate-limited/exhausted, and the third (LLM7) rate-limits concurrent
requests, occasionally returns raw non-JSON error bodies (real HTTP 502),
and re-called a tool a second time instead of concluding when driven
through `assistant.py`'s actual loop. None of this is hypothetical - it's
what happened testing it. Two non-obvious protocol quirks, both confirmed
live and NOT something `raise_for_status()` alone catches: (1) Kilo and
BlockRun both return **HTTP 200 with an `"error"` key in the body** on a
rate limit - you must check for that key regardless of status code: (2) a
successful-looking 200 can still have an empty `choices` list. If you
change the provider list or the error-detection logic, re-verify against
the real endpoints (they're reachable with no key at all - `curl
https://api.llm7.io/v1/chat/completions ...`), not against reasoning about
what an OpenAI-compatible API "should" return. Re-test before trusting any
existing "this provider works" note in that module's docstring - free-tier
rosters and quotas move fast (see shani-docs' own caveat that Zen/Kilo's
model list "changes without much notice").

If you touch `mcp.py` or either `usr/bin/` launcher: run the actual
launcher script directly (not just `sys.path.insert(0, '.')` from inside
`usr/lib/shani-chronoa`) — that's the only way the `dirname(dirname(...))`
path bug above was ever going to surface. For `mcp.py` specifically, drive
it as a real subprocess with the `mcp` package's own client SDK
(`mcp.client.stdio.stdio_client` + `mcp.ClientSession`), not just by
calling `build_server()` in-process — the schema-inference behavior only
shows up over a real `tools/call` round trip.

## Model choice (as of 2026-09-16)

Default model is Qwen3 (`qwen3:4b` on capable hardware, `qwen3:1.7b` on the
lowest tier — see `config.py`'s `HardwareProfile.get_model()`), not
`llama3`. Qwen3's dense checkpoints are tool-call-trained at every size,
unlike most small model families where function calling is only reliable
at 7B+. This has NOT been verified live against a real Ollama server in
this environment — if tool calls come out malformed on real hardware,
step up via `--model=qwen3:8b` (that flag is wired to actually take effect;
verify it still is if you touch `_parse_args`/`_init_components` in
`app.py` — it silently didn't for months before this pass, and `--voice=`
had the identical bug).

## Packaging: two different naming conventions, don't mix them

`PKGBUILD` (Arch) and `DEBIAN/control` (Debian) need different dependency
name conventions for the *same* underlying package — e.g. GTK4 is `gtk4` in
Arch, `gir1.2-gtk-4.0` in Debian; `python-gobject`/`python-httpx` in Arch,
`python3-gi`/`python3-httpx` in Debian. The PKGBUILD here previously had
Debian-style names copied straight in, which would have failed
`makepkg`/pacman dependency resolution outright. Cross-check against a
sibling Arch PKGBUILD doing the same kind of GTK4 + httpx app (e.g.
`shani-pkgbuilds/waydroid-helper`) before trusting a dependency name in
either file.

## Cross-repo impact

No `AGENTS.md` existed for this repo before 2026-09-16 — it's newer than
the other ~15 `shani-*` repos. This repo is currently standalone (no
sibling depends on it, and it doesn't yet talk to `shani-platform` or
`shani-fleet`). `mcp.py` now *is* a real MCP server (see "What this repo
is" above) — that placeholder-REST-client note is stale as of the pass that
replaced it; don't trust old descriptions of `mcp.py` in memory or prior
notes without re-reading the file.

## Settings window (added 2026-09-16)

`settings_window.py`'s `SettingsWindow` now exists - plain GTK4, no
libadwaita, opened via a gear button in `gui.py`'s header row or
`Ctrl+,`/`app.open-settings`. Switch rows call the app's own GActions
(`app.activate_action(...)`) rather than writing gsettings directly, so a
toggle's side effects run through the same code path as the keyboard
shortcuts - see the module's own docstring. Building it surfaced two more
real bugs, both fixed: `config.model` and `config.whisper_model` had
non-empty gschema defaults (`'qwen3:1.7b'`, `'base'`) that were **never
actually read** by `_init_components` (it always used
`HardwareProfile.get_model()`/`get_whisper_model()`) - exposing them as
editable settings would have silently done nothing. Fixed by changing both
defaults to `''` (meaning "no override, auto-detect") and wiring
`_init_components` to check the persisted gsetting between the CLI
`--model=` override and hardware auto-selection; verified live with a
patched `ChronoaConfig.get()` that both an override and the empty default
correctly reach `OllamaLLM.model`/`WhisperSTT.model`.

Testing gotcha hit while verifying this: `Gtk.ScrolledWindow.set_child()`
auto-wraps a plain (non-`Gtk.Scrollable`) child in a `Gtk.Viewport` - a
test script doing `window.get_child().get_child()` to reach the content box
silently gets the Viewport instead and finds only one "row" (the box itself,
misread as a single child). Confirmed by tracing every real `Gtk.Box.append()`
call during construction (all 49 fired correctly) before finding the
missing `.get_child()` hop was in the test, not `settings_window.py`. If a
future check on a `Gtk.ScrolledWindow`'s contents finds implausibly few
children, check for this before suspecting the widget-building code itself.

## Senses layer: wired into the live chat path (2026-09-27)

The senses layer (`senses/`) was built, consent-gated per sense, unit-tested
and exposed through the headless CLI - and reached **no LLM turn at all**.
`app.py`, `assistant.py`, `tools.py` and `mcp.py` imported none of it, so
`shani_chronoa-sense` was its only consumer and every percept it produced died
in the one-shot process that made it. This is the same dead-code class as
`tool_tracking.py`/`gateway_supervisor.py`/`sandbox/profiles.py`
below, and `test_sense_manifest.py` only passed because the CLI counted.

Fixed: `Assistant` takes an optional `percept_store` + `context_builder`, and
`Assistant.build_messages()` re-reads live percepts and inserts them as one
transient system message on **every** request (`app.py:_init_components`
constructs a real `PerceptStore()` + `ContextBuilder()` and passes them in).
`build_messages()` is called fresh on each round of the tool loop, not once
per turn, so a percept added or expired mid-turn is reflected immediately.

Three things this deliberately does **not** do, all of which look like
oversights and are not:

- **Percepts never enter `_history`.** `MAX_HISTORY_MESSAGES` is 40 and
  `_trim_history()` preserves only `_history[0]` - a percept parked at
  `_history[1]` would be silently deleted by the next trim, would eat the
  conversation's context budget (Ollama *drops* rather than errors on
  overflow), and would outlive its own TTL. Asserted directly, including
  after enough turns to force repeated trimming.
- **No sense is exposed to the LLM as a callable tool.** That is a separate
  design decision touching the fixed-whitelist boundary; this change only
  makes perception the assistant already has *visible* to the turn. A
  percept recorded by the CLI (or by any future in-GUI producer) is now live
  on the next turn, but the GUI still cannot itself *ask* a sense anything.
- **Consent is now re-checked when a percept is *sent*, not only when it was
  recorded** (decided 2026-09-29). This was previously recorded here as an
  open decision, and it was a real gap: `ContextBuilder` had no consent check
  at all, so a fact captured while `memory-sense-enabled` was true kept being
  injected into every prompt after the user set it to false. Proven by running
  it, not by reading it. `ContextBuilder` now takes a `consent` predicate and
  drops anything whose sense is no longer permitted, on every turn.

  This **withholds rather than deletes**: the percept stays in the local store
  and reappears if consent is granted again, so withdrawing a permission costs
  the user nothing they did not ask to lose. Two traps worth knowing:

  - The `consent=` argument in `app.py` is load-bearing. Omitted, the
    behaviour reverts silently and nothing else notices — which is how
    `privacy.py` came to gate on a sense name that no longer existed. A test
    constructs the real application and asserts the argument is present.
  - `consent=None` keeps `ContextBuilder` a pure renderer, because the CLI and
    the unit tests construct a bare one. Only the path that actually sends to a
    model supplies a predicate.

  Every sense name stamped on a percept was checked against
  `_SENSE_CONSENT_KEYS` before the filter was added, so it withholds nothing
  legitimate.

**A skill cannot see the app's transient percepts, and the durable file is the
wrong instrument for the question.** `list_percepts` shipped claiming to show
"every fact held right now" and how many "would be sent with the next reply".
Both were false. A skill runs in a subprocess, so it built its own
`PerceptStore` and read `memory.jsonl` — which only ever holds the `memory`
sense's facts. Every transient percept, i.e. what a running app is mostly
holding, was structurally invisible, so it would have under-reported the answer
while stating it as complete. `PerceptStore` now publishes `live.json` beside
the durable file on every `add()` and `clear_transient()`, renamed into place
so a concurrent reader never sees half a file.

  - `clear_transient()` must publish **after** clearing. Publishing before it
    restates exactly the facts the caller just discarded, so a reader landing
    between the two lines is told the opposite of the truth.
  - `_live_path` is a **per-instance** attribute, not a module global read at
    write time. With a global, a store constructed with a custom
    `durable_path` — the parameter tests already use — still publishes into the
    shared location; that is how a test run wrote a fabricated snapshot
    (`pid 424242`) into a real user's `~/.local/share/shani-chronoa/percepts/`.
  - `list_percepts` must name its provenance. A view older than 5 minutes is
    reported as a leftover from a process that is gone, not as the present, and
    an absent view says no app is publishing. A smaller *honest* answer is the
    requirement; a confident wrong one is the failure.

**Verified by running, not by reading:** the real `ChronoaApplication()` (a
genuine `Gtk.Application`, not `__new__`) through the real `_init_components()`,
with a real `Percept` added to the app's own store, produced a prompt
containing `- [memory/fact] the meeting is at 4pm in the annex` at index 1,
`percept in _history: False`, `_history length: 1`. The cross-process case is
proven through the real `usr/bin/shani-chronoa-sense` launcher as a
subprocess, and both the revert-the-wiring and the park-it-in-`_history`
mutations were run to confirm the tests fail without the fix.
Suite: 190 passed, 1 failed - the failure is
`test_tool_tracking.py::test_export_csv` (`NameError: name 'record' is not
defined` at `tool_tracking.py:128`, where the loop variable is `call`), which
is in concurrent, unrelated work in `tool_tracking.py` and was left alone.

**Two real defects found by running, deliberately NOT fixed here** (both are
in the senses CLI, both need a `Sense.run` contract or ownership decision
rather than a mechanical patch):

- `argfile.py`'s module docstring wrote a bare `\uXXXX` in a non-raw
  docstring, which is a `SyntaxError` - so `tools.py`, `assistant.py` and
  `app.py` could not be imported at all, i.e. **the whole assistant was
  unlaunchable**, and every module-level test passed anyway because none of
  them import it. Fixed (one backslash), because it blocked all verification
  here; it is the clearest argument yet for this file's own rule that a
  passing test suite proves nothing about the app starting.
- `shani-chronoa-sense --durable-file PATH run memory operation=remember`
  ignores `PATH` for the *durable* write: the fact lands in the default
  `~/.local/share/shani-chronoa/percepts/memory.jsonl` (the memory sense
  holds its own store, which the CLI cannot reach through `run(arguments)`),
  and a following `percepts --durable-file PATH` then reports zero. The
  option's own help text promises otherwise.

Also worth knowing: a fresh `python3 -c` with a overridden `HOME` loses
`~/.local/lib/python3.*/site-packages` from `sys.path`, so `httpx` disappears
and `app.py` will not import. Pass `PYTHONPATH=$HOME/.local/lib/python3.12/
site-packages` (or use a `venv --system-site-packages`) when probing by hand.
The repo's own `test_sense_scheduler.py` handles this for child processes.

## Machine-state senses (added 2026-09-28, merged 2026-09-29)

Thirty-two senses read the machine rather than the user's world. The
machine-state set added that day was `privilege` (who holds a dangerous
capability), `display`, `network`, `bluetooth`, `rfsense` (WiFi RSSI spread),
`thermalgrid` (MLX90640/AMG8833 over I2C), `hwmon` (fan/temperature/voltage/
current from `/sys/class/hwmon`) and `modelfit` (available RAM against the
models Ollama reports), on top of the pre-existing `memory`, `power`, `storage`,
`cpu`, `gpu`, `security`, `devices` and `audio` — plus `senses/latch.py` and
scheduler de-duplication, so a polled sense that keeps saying the same thing is
not deposited 1440 times a day. `hwmon` and `modelfit` have no external binary:
they read the kernel directly, so there is nothing that can be missing for them
to degrade into.

Five more were added after the merges below took the registry to 23:
`filesystems` (what is mounted, and real room per filesystem — `storage` walks
`/sys/block` and reports *disks*, which is not the same question), `services`
(`systemctl` units and whether they are enabled), `timebase` (NTP sync and
timezone), `usb` (the USB bus — `devices` walks PCI, which is the *internal*
bus, so nothing covered what was actually plugged in) and `resources` (zombie
processes, swap in use, file-descriptor pressure: the ways a machine runs out
of something without `cpu` or `memory` looking busy). On the dev box `usb`
found the integrated camera and the Intel wireless device, which no other sense
reported, and `resources` found a `zypak-sandbox` zombie and 810 MiB of swap in
use — all invisible to every pre-existing sense.

Four more arrived 2026-09-29: `updates` (pending package updates, and the age
of the database the count came from - a stale database makes the number a floor,
not an answer), `faults` (recent journal errors, grouped by the unit named in
the message rather than by the emitting pid, because every unit systemd starts
logs as `systemd[1]` and grouping by pid reported a count of pids as a count of
things that broke), `snapshots` (btrfs subvolumes and which one is booted - the
rollback safety net, which nothing could previously report existed) and
`sessions`. `sessions` defaults **off** and is the only machine-state sense that
does: it reports who *else* is on the machine, which is other people's presence
rather than this machine's own hardware.

**`filesystem` vs `filesystems` are not duplicates and must not be merged.**
`filesystem` reads one text file the user names, confined to their home
directory; `filesystems` reports mounts and room. The names differ by one
letter, so the settings labels are deliberately distinct — "Files and folders"
against "Filesystems and room". Do not rename either to tidy this up: the
consent key is `<sense-name>-sense-enabled` and is derived from the name, so a
rename silently makes a sense permanently ungrantable (see the alias rule
below).

**Seven senses were merged away on 2026-09-29, taking the registry from 28
modules to 23** (28 − 7 retired + 2 new, `capture` and `printing`), and this
file was corrected rather than left to mislead the next reader. `contention`,
`camera`, `thermal` and `cooling` no longer exist as modules, so the lesson
below refers to code that moved:

| Gone | Now |
|---|---|
| `contention` + `camera` | `capture` |
| `thermal` + `cooling` | `hwmon` |
| `monitors` | `display` |
| `smart` | `storage` |
| `link` | `network` |

Each retired consent key **still grants its successor** via
`config._SENSE_CONSENT_ALIASES`, because a rename that silently ungrants a
capability is a permission revocation wearing a refactor's clothes. Every
retired key now defaults `false` in the schema, and that is a fix rather than an
inconsistency: three of them (`monitors`, `smart`, `link`) used to default
*true*, which let the alias override the live key and made the settings switch
appear to do nothing. A test asserts every retired key defaults false.

**Three of the original set were green on the dev box while confidently wrong on
the distribution this actually ships to, and the lesson is not optional to
read:** `contention` (now `capture`) reported every microphone and camera as
*free* when `fuser` (psmisc) was missing, because the OSError was swallowed into
"no holders"; `privilege` used Debian's `dpkg-query`, so on Arch — which is what
ShaniOS is — it found no package owner for anything and reported **every process
as unmanaged third-party software**, burying the one real finding; `bluetooth`
reported "0 devices" when `bluetoothctl` was simply not installed. 584 passing
unit tests caught none of it, because the tests ran on the wrong distro.

**Rule: a sense whose failure mode is a plausible-looking wrong answer is
worse than a sense that fails.** "I could not determine X" must be a state the
code can represent, distinct from "X is false" — and it must fail toward
UNKNOWN, never toward a clean result. Ownership lookups span pacman/dpkg/rpm/
zypper and report *undetermined* when none answers.

Also load-bearing and invisible:

- A malformed sense schema is **skipped at load with only a log line**, so a
  registered-but-unusable sense looks merely absent. The contract is Ollama's
  `{"type": "function", "function": {...}}` wrapper, not raw JSON Schema.
- `glib-compile-schemas` rejects `_` in key names and on hitting one discards
  the **entire file** — all keys at once. A sense name must be legal as a key
  fragment; there is a test asserting this across the whole registry.
- ALSA nodes live in `/dev/snd/`, not `/dev/`; `/proc/net/wireless` writes
  `70.`/`-40.` with trailing points; `i2cdetect` prints bare hex `33` and
  prints "Permission denied" on stderr **while exiting 0**.
- Consent key is `<sense-name>-sense-enabled`, derived from the name, so a
  rename silently makes a sense permanently ungrantable.

`rfsense` deliberately states its own ceiling: the research it sits beside
(RF-Pose, RF-Avatar, arXiv 2401.17417) is real but runs on Channel State
Information, and this machine's Intel AX201/`iwlwifi` exposes no CSI. It
answers "is something moving", not "what" — and says so rather than implying
the research result it cannot produce.

Full methodology, the eight running-only bugs, the research correction, and
the live security finding are in **`AUDIT-HISTORY.md`**.

**Verification status:** unit suite green on Ubuntu (1878 passed, 6 skipped)

**Run the suite with `XDG_STATE_HOME` set, or it writes to the real home
directory.** `skills/timer.py` resolves its store from `XDG_STATE_HOME`,
falling back to `~/.local/state` — and it is *not* the timer tests that leak:
run in isolation they leave nothing, but a full suite run without the
variable set leaves a real `~/.local/state/shani-chronoa/timers.json`
containing fixture data (`pasta`, plus a `'; touch /tmp/pytest-of-...`
shell-injection probe label). Pointing `XDG_STATE_HOME` at a temp dir
contained it completely. `XDG_CONFIG_HOME` and `XDG_DATA_HOME` are not
enough — only `XDG_STATE_HOME` reaches this store. Note the same variable
gates the durable percept store documented above, which has already
contaminated a real user's `percepts/` directory once.

Related trap from this pass: an ad-hoc `python3 -c` probe run **without**
`PYTHONDONTWRITEBYTECODE=1` leaves `.pyc` files inside the package, which
makes `test_no_pycache_in_packaged_payload` and
`test_no_bytecode_files_in_packaged_payload` fail on the *next* full run. The
suite itself leaks nothing. Clean the tree before trusting a red packaging
test — and check whether the bytecode is yours before assuming the repo is
broken.

> **MCP stdio verified 2026-09-29** against a real JSON-RPC client:
> `initialize` (protocol 2024-11-05), `tools/list` (70 tools), and `tools/call`
> for a normal call, a malformed call, a shut consent gate, an unknown tool, and
> five identical calls in a row. All 70 tools carry a title and annotations.
>
> Running it needs `mcp` installed *alongside* system PyGObject, which is the
> non-obvious part. A plain venv does not work: `gi` is a system package pip
> cannot supply, so the server dies with `ModuleNotFoundError: No module named
> 'gi'`. And `pip install --user` is refused by PEP 668 on this box. What works
> is a venv *with* system site packages:
> `python3 -m venv --system-site-packages` then `pip install mcp`. The
> repository itself needs no change for this.

⚠️ **The slot run predates the 2026-09-29 sense merges and does not cover
them.** It was 2026-09-28, at testbed `9be7139`, against the pre-merge
packaging. Ten senses were merged after it, and five more (`filesystems`,
`services`, `timebase`, `usb`, `resources`) were added after that, so no
real-hardware run has yet exercised `capture`, `hwmon`, `storage` or `network`
as the single senses they now are, nor any of the five newest. The unit suite
covers the merges; the honest gap is real-hardware coverage of the merged and
newly-added ones, and it needs a slot run to close.

`usb` and `resources` are the two whose value is least provable from a unit
test, because both exist to surface *this* machine's actual condition — the
integrated camera and Intel wireless device on the USB bus, the
`zypak-sandbox` zombie and 810 MiB of swap. Those readings came off the dev
box, not a slot. A slot run should confirm both degrade honestly when the
sysfs tree or `ps` output looks unfamiliar, rather than reporting clean.

`modelfit` earns one note here rather than in AUDIT-HISTORY, because the slot
test's handling of it is the lesson. It reports UNKNOWN in its *model list*
when Ollama does not answer, while the hardware half reads fine — so the
generic step of the slot test no longer guesses whether that UNKNOWN was
honest and reports SKIP, and a dedicated check asserts both halves: that the
sense says UNKNOWN rather than claiming a model count, and that the
`/proc/meminfo` half is still real. An earlier version of that check asserted
a *reason* for the UNKNOWN that turned out to be false, and the version before
it failed senses whose legitimate readings are all one or two digits. A green
line with an invented explanation is worth less than an admitted SKIP.

## Surfaces added 2026-10-01: six event types, post-conditions, "Open with"

Found by `tools/cli_matrix.py` (see below), which reads Chronoa's own registries
beside the OS's commands and interfaces and showed three gaps.

- **Trigger event types 6 -> 12**: `screenlock` (logind LockedHint/IdleHint),
  `powerstate` (`/sys/class/power_supply`), `netstate` (nmcli), `usbplug`
  (`/sys/bus/usb/devices`), `btconnect` (`bluetoothctl devices Connected`),
  `schedule` (local clock). The `source` names the state to be told about and an
  event exists only while it holds, so the engine's baseline-then-transition
  logic fires on the transition *into* it (arming "locked" on a locked screen
  fires nothing). Each has its own default-off `<type>-sense-enabled` key - the
  names deliberately differ from the `power`/`network`/`bluetooth`/`sessions`
  senses so no switch is shared. A peripheral's battery (`scope=Device`) is not
  the machine's; a schedule missed while asleep (beyond `grace_minutes`, 15) is
  skipped, not replayed. In an unbooted nspawn slot screenlock/netstate/btconnect
  read UNAVAILABLE (no session, no NetworkManager, no bluetoothd) - correct, and
  the real-desktop readings were checked on a laptop.
- **Post-conditions 1 -> 14 of 60 actuators** (volume, set_mic_mute,
  power_profile, set_timezone, control_service, kill_process, create_directory,
  delete_file, trash_file, write_text_file, move_or_copy_file, do_not_disturb,
  clipboard). `verify()` now passes the tool name to a two-argument check, and a
  check returning None is UNVERIFIED. That fixed a live bug: `clipboard`'s check
  could not tell get from set, so **reading** a non-empty clipboard reported
  FAILED. delete/trash checks use `files.expand`, not `resolve` - resolving
  follows a dangling symlink and reads it as removed.
- **"Open with Chronoa"**: the desktop entry passes `%U` with a MimeType list;
  `do_command_line` attaches file arguments/`file://` URIs (relative to the
  *invoking* cwd - a second launch is forwarded to the running instance) and
  `--ask=TEXT` sends a question through the window's own send path. Verified in
  the `@blue` slot: the transcript showed `user said: <text>\n📎 os-release`.

`tools/cli_matrix.py` (not packaged) writes a JSON/Markdown/HTML map of every
command (owner, man NAME line, JSON output, intent, safety, which Chronoa
surface it could feed, which skill/sense/trigger already calls it) plus D-Bus,
typelibs, GSettings, Polkit, sysfs, portals and entry points. Run it in a slot:
`SHANIOS_TEST_EXTRA_BINDS=/opt/shani-chronoa:/mnt/chronoa,<out>:/mnt/out
build.sh test enter blue --local-src-chronoa=/opt/shani-chronoa python3
/mnt/chronoa/tools/cli_matrix.py --out=/mnt/out/shanios-matrix`. Its
classifications are heuristics from man summaries - a starting list, not a verdict,
and the output says how far to trust them: `calibration` scores safety and the
sense fit against Chronoa's own modules (2026-10-01: safety 89%, sense recall
83% with 22% of commands classed as senses, up from 61%/19% after the fixes the
disagreements pointed at - a syscall page chosen over the command's, "Format"
read as a verb, `\bpack` matching "packet", no `mixed` class for managers like
systemctl).

**The matrix is also the only authority in the tree on Arch package names, and
three package names this project states to users were wrong (fixed 2026-10-03;
guarded by `tests/test_package_names_match_arch.py`).** `commands[].package` in
`chronoa-matrix.json` is read out of pacman's own file database on a real image,
so it settles arguments about what ships a binary. Auditing every package name
this tree claims against it found three that name a package which **does not
contain the binary the user needs** — each of which sends someone to
`pacman -S <name>` and gets nothing:

- **`wpctl` is in `wireplumber`, not the `pipewire` package.** It left
  `pipewire` when wireplumber split off the PipeWire project, so this was a
  *stale truth* rather than a guess — the kind that survives review forever
  because it was once true. In `skills/privacy.py` and twice in `senses/audio.py`.
- **`bluetoothctl` is in `bluez-utils`.** `bluez` is the daemon and does not
  ship the client. In `skills/toggle_bluetooth.py`.
- **`udisksctl` is in `udisks2`.** `udisks` is the pre-rename name. In
  `skills/manage_mount.py`.

`files._PACKAGE_HINTS` was four entries, so seven of the eleven skills calling
`tool_missing()` fell back to "the package that provides it" — a sentence that
helps nobody install anything. It now carries 45 entries, all read out of the
matrix.

**Two method traps, both hit during this audit and both worth remembering:**

1. **A substring test is not a check.** The first pass matched package names as
   substrings and reported `bluez` and `udisks` *correct*, because `bluez` is a
   prefix of `bluez-utils` and `udisks` of `udisks2` — it found two of the
   three real bugs as clean. Exact equality only.
2. **The matrix's `gsettings` entries carry 5 *example* keys per schema, not all
   of them**, so it cannot confirm an individual key name. To check
   `desktop_setting.py`'s allowlist, use `gsettings list-keys` against the
   installed schemas instead — which is what was done, and all 8 keys it
   references (`enable-animations`, `cursor-size`, `clock-format`,
   `clock-show-seconds`, `show-battery-percentage`, `enable-hot-corners`,
   `natural-scroll`, `tap-to-click`) exist and are correct. A clean result, worth
   recording so nobody re-audits it blind.

**`open_surfaces` is a lead list, not a gap list - read this before building a
skill out of it (measured 2026-10-03).** The matrix flags a command as a skill
candidate when no Chronoa code *calls that binary*, and this repo deliberately
answers questions without the binary. Checking the top candidates against the
127 skills that exist: `fastfetch`, `lscpu`, `lsblk`, `lsmod`, `ps` and `uptime`
are all flagged, and all six are already answered by `system_info` /
`list_processes`, which read `/proc` on purpose because those binaries can be
absent on a minimal image. `rg`, `fd`, `tree` and `locate` are flagged and are
`search_file_contents`, `find_files` and `directory_tree`. So does `pactl` -
which turns out to be covered too, by `volume.py`'s numeric device-node
handling. **`open_surfaces` measures calls, not answers.** Also note `ideas[].score`
is a category-size number, not a value one: every entry in a big category ties
at 25, so ranking by it surfaces `lur-command` and `gendict`.

Two skills were built from a matrix pass on 2026-10-03 after that check, both on
`/proc`/stdlib rather than the flagged binary, both verified against the real
thing: `json_query` (`jq` was the flagged command; `analyze_table` already reads
JSON but flattens it, so it cannot address a nested field) and `port_owner`
(`ss`/`lsof` were flagged; verified to report the same 10 listening sockets as
`ss -tuln` on this machine, and it attributes sockets that `ss -p` cannot
without privileges).
podman, git, nmcli, flatpak, pactl...), from COMMANDS sections and from
`<cmd>-<sub>` pages; classification is by whole-word name, then the full
description, and defaults to "changes" - `checkout` is not `check`, and
`log-level [LEVEL]` is a setter. `--scaffold` of a mixed tool exposes only
its reading subcommands as an allowlisted enum (systemctl: 20 of 74).

Distro-level modes, all run on the host from the JSONs a slot run wrote (one per
image; the GNOME and Plasma images are separate harness data dirs):
`--audit-packaging A.json B.json [--strict]` checks the matrices against
shani-pkgbuilds and image_profiles (listed-but-not-installed, meta deps, drift
between the profiles' lists and between the images, Chronoa's undeclared runtime
deps, image lag against `pkgver-pkgrel`, setuid and password-less Polkit
surface); `--strict` exits 1 on the hard ones and does not blame a stale image on
its lists (an image older than its profile's last list commit is reported as
stale). `--suggest A.json B.json [--suggest-json F]` ranks fix / rebuild / add /
review / consider: "add" is measured (an installed package's unmet optional
dependency, wanted by two or more; a command or Python module Chronoa uses),
"consider" is a labelled catalog (opinion), X11 tools are notes, not Wayland gaps. The JSON also carries
each package's size, build date, depends and optional deps, enabled-unit links,
broken launchers/units and Chronoa's unresolved imports, so the audit reports
each explicit package's *exclusive* footprint (what removing it alone frees),
services enabled on one image only, and stale builds. `--resolve-files` asks
pacman's files database (run `pacman -Fy` first - the builder can) instead of
the hand-kept command->package map. `--enrich M.json --security-json all.json --tldr-zip tldr.zip
--pkgstats-cache F` adds web data (run in the builder - it has the network and
`vercmp`): Arch security tracker AVGs (only a recorded fixed version newer than
the installed one is a "fix"; an open AVG with no fix is "review", because the
tracker leaves old ones open - 2026-10-01: 25 open, 0 fixable on either image),
tldr coverage, and pkgstats popularity from ONE paged list down to 0.5% (24 s
for 11,873 packages; a request per package took minutes). pkgstats candidates
are filtered to things a person picks: leaves in the sync db, not explained by
a popular dependent (libodfgen <- libreoffice), not the base-devel closure, not
the other desktop's image, not Arch-maintenance tools, and not apps the profile
ships as a Flatpak. Rare-on-Arch packages on the image come with the chain that
pulled them in (arpwatch 0.52% <- shani-tools-network).

## Kokoro TTS (2026-10-02): run by sherpa-onnx, the way Piper is run by its own program

Kokoro (82M, Apache-2.0) is a voice, and like Piper it needs a program to speak
it. It first shipped as `kokoro.py`, which phonemised with espeak-ng and ran the
ONNX graph through `python-onnxruntime-cpu`. **That module is gone (2026-10-02).**
`tts.py` now runs sherpa-onnx's `sherpa-onnx-offline-tts` (`sherpa.py`: one
27 MB release in the user's home, onnxruntime bundled, found through the
binaries' `$ORIGIN/../lib` rpath) with sherpa-onnx's packaging of the model
(`kokoro-int8-en-v0_19`, 98 MB, eleven speakers). Nothing system-wide, no
numpy or onnxruntime for Python, and no PKGBUILD/DEBIAN dependency on either.

- **Voices are speaker ids.** `voices.KOKORO_VOICES` maps six female voices to
  their `--sid`; each id was checked byte for byte - row 30 of the speaker's
  slice of `voices.bin` equals row 30 of that voice's own style bank in
  onnx-community/Kokoro-82M-ONNX (the first 21 rows of every bank are
  identical, so row 0 would prove nothing).
- **Opt-in, by measurement.** RTF about 1.2 on a CPU (7.7 s to make 6.4 s of
  speech in a slot; 2 and 4 threads the same; the old Python path measured
  1.18-1.32 too), so as a default it put seconds of silence before each reply.
  `kokoro-tts-enabled` defaults false; setup's Voice page turns it on when a
  Kokoro voice is chosen.
- **One list of voices.** Setup shows Piper and Kokoro voices together; picking
  one installs the engine it needs (`setup_wizard.setup_voice`), and picking a
  Piper voice turns Kokoro off, so the voice chosen is the voice heard. The
  "Listen" button tries a voice without saving it (`PiperTTS.kokoro_trial_voice`
  / `piper_trial`, never a monkeypatch on a live instance).
- **English only.** A reply in another script (`languages.script_of`) goes to
  that language's Piper voice instead.
- Intelligibility was checked earlier by transcribing each engine's output back
  with whisper: espeak-ng 0.920 and Kokoro 0.926 mean similarity - both clear;
  the gain is naturalness, not intelligibility.

## The goal (2026-10-02): a harness that makes the smallest model beat bigger ones - measured

The user's stated goal: Chronoa's harness should be good enough that the
smallest local model (Qwen3 0.6B/1.7B on a CPU) outperforms much bigger models
on what people actually ask. Judge changes by that, and **measure it**:

- `tools/task_eval.py` + `tools/eval_cases.json` (not packaged): ~60 requests
  with the call a correct assistant makes, scored on tool and arguments,
  **never executed**. Configs: `bare` (every schema - 131 tools are ~19,700
  tokens, past the 8k local context, so it cannot even be sent), `select`
  (tool_select), `select+recover` (the default: calls written as text
  recovered), `+compact`; and `--cloud=kilo|blockrun|llm7` (the free keyless
  providers, user's choice of baseline - "some free are good") as `cloud`
  (every schema, no harness) and `cloud+select`.
- A provider that is busy, rate-limited or over quota is retried and then
  counted as **not measured**, never as the model being wrong (LLM7 was over
  its daily quota during the first run).
- shani-testbed `slot-tests/chronoa-eval.sh` runs it against the real model on
  a slot. `tests/test_task_eval.py` checks the scorer from both sides and that
  every case names a real tool and real arguments.

## Optional extras (setup's More page, 2026-10-02)

Each is opt-in, pinned by size and sha256, installed into the user's home, and
runs on this machine. What needs no model uses the system's own tools.

| Extra | What | Runs as | Download |
|---|---|---|---|
| Eyes | describe a screen or photo | llama.cpp `--mmproj`, `shani-chronoa-model@vision` (port 8767, `--sleep-idle-seconds`) | 0.5 / 1.6 / 3.0 GB by RAM and GPU |
| Imagine | pictures from a description, and changing a photo by description (img2img) | stable-diffusion.cpp release, `sd-server` `@imagine` (8769) | 2.0 GB + 26/35 MB engine |
| Memory | conversation search by meaning (hybrid with FTS5, reciprocal rank) | llama.cpp `--embedding`, `@embed` (8768) | 146 MB |
| Languages | OCR data, a Piper voice, whisper listening | user tessdata + Piper | 1-12 MB each + ~64 MB voice |
| Photos and videos | identify faces/objects, blur faces, blur/remove background, page scans | OpenCV wheel + numpy + 3 OpenCV Zoo models | ~88 MB |
| Sounds | what a sound is (AudioSet 527 classes) | sherpa-onnx + CED-tiny | 28.5 MB |
| Who said what | speaker turns in a recording | sherpa-onnx + pyannote seg 3.0 + CAM++ | 37 MB |

No model needed: video keyframes and "describe this video" (`video_frames.py`,
ffmpeg scene detection), plain photo looks and 2-4x upscale (`edit_image`,
ImageMagick), subtitles and transcripts (`recordings.py`, ffmpeg + whisper),
recording clean-up (ffmpeg `anlmdn,afftdn`: measured 11.8 -> 13.4 dB on real
speech - modest; `loudnorm` made it worse by lifting the noise in the gaps and
resampling to 192 kHz).

Lessons from building these, each found by running:

- **OpenCV 5 orders NanoDet's outputs differently from the Zoo's 4.x
  reference** (all scores, then all boxes; three levels, not four). The
  reference's `outs[::2]` pairing would pair scores with scores; `detect._levels`
  pairs by shape. The Zoo's 0.35 threshold found two people in a one-person
  portrait; 0.5 is right on both sample photos.
- **A page finder must not find the picture's own border** - random noise came
  back as "a page" until candidates spanning ~the whole frame were refused.
- **Downloads resume** (HTTP Range) and are retried; a server that ignores
  Range restarts the file cleanly; the digest still covers the whole file
  (`tests/test_stt_provision.py`, with a wrong-bytes control). A 2 GB model on a
  slow link timed out three times in a slot before this.
- **A truncated download can still list its archive** - a partial `.tar.bz2`
  lists its first members. Pin a file only after its size matches the
  publisher's.
- **Address-space limits shape the design**: a skill child runs under the
  profile's 512 MB RLIMIT_AS, so every model runs as a loopback service and
  skills are HTTP clients; slow skills get a named longer timeout
  (`tools._SLOW_TOOLS`), still capped by the profile.

## Approve from a notification, and four more event types (2026-10-01)

An event rule armed with `ask_first` (manage_triggers' `ask_first`) does not act:
`approvals.py` shows `notify-send --action` Allow once / Deny on its own thread
(both GNOME Shell and Plasma implement org.freedesktop.Notifications; Gio's
GNOME path would need a `dev.shani.chronoa.desktop` that does not exist), and
only Allow runs it, after consent is read again, with the audit origin
`approved`. That is what lets such a rule reach a destructive actuator;
expiry stays notify-only. Event types 12 -> 16: `sleepwake` (BOOTTIME minus
MONOTONIC only grows while suspended), `audiodevice` (pw-dump), `journalmatch`
(cursor of the newest `journalctl --grep` match, pattern as one argv element),
`dbusprop` (`busctl get-property`, every token validated; read-only by D-Bus's
rules). `set_theme` on Plasma 6 uses `plasma-apply-colorscheme` and its
"(current color scheme)" mark as the read-back - the Plasma 5 tool it called does
not exist on the Plasma image. Also: `--scaffold CMD --from-json M.json` writes a
user drop-in skill (flags from the man page's OPTIONS, a `which` guard,
operands that may not start with '-'; anything not read-only is opt-in and
generated disabled), `--diff OLD NEW` compares two runs, and `--check` is what
shani-testbed's `slot-tests/chronoa-matrix.sh` asserts. The dependency audit
follows guards into imported helpers - its first version reported
close_window/focus_window/press_key as unguarded when all three check via
`list_windows.session_problem()`.

## Desktop surfaces added 2026-10-02 (GNOME and Plasma backends, each run on both images)

| Surface | GNOME | Plasma | Gate (default off) | Verified on the images |
|---|---|---|---|---|
| Search provider (`search_provider.py`, `shani-chronoa-search`) | Shell SearchProvider2 | KRunner `org.kde.krunner1` | - (no network, no model, nothing logged) | both: GetInitialResultSet / Match over D-Bus |
| `search_documents` | LocalSearch (`tracker3` fallback) | `baloosearch6` + `balooctl6 status` | `document-search-enabled` | GNOME: honest "index not available"; Plasma: empty output with no index is NOT "nothing found" |
| `calendar_events`, trigger `calendar` | EDS via ECal/ICalGLib 4.0 | none on the image - says so | `calendar-read-enabled`, `calendar-sense-enabled` | GNOME: event created in EDS, read back, trigger fired |
| `phone`, trigger `phone` | GSConnect (ObjectManager + daemon.js CLI) | `kdeconnect-cli` + battery over D-Bus | `phone-control-enabled`, `phone-sense-enabled` | both: link-not-running reported as itself. No SMS, no notification reading, no remote input |
| Wayland input (`portal.py` RemoteDesktop) | gnome portal | kde portal | `input-control-enabled` (existing) | fake portal on a private bus only - the first real use shows the desktop's dialog, which needs a person |
| Global shortcut (GlobalShortcuts) | gnome portal | kde portal | `global-shortcut-enabled` | fake portal; the app thread is stoppable (a thread parked in MainLoop.run deadlocked interpreter exit) |
| `airplane_mode`, `charger_info`, `firmware_updates` | /sys/class/rfkill, power_supply, typec; fwupdmgr --json | same | `radio-control-enabled` (switching only) | real laptop hardware (read paths); rtc wake left out - needs CAP_WAKE_ALARM |
| `desktop_setting` (allowlist) | gsettings | kreadconfig6/kwriteconfig6 --notify | `appearance-control-enabled` (existing) | both: change + read-back verified; the other desktop's setting is refused |
| Background mode (`daemon.py`, user unit) | systemd --user | same | `background-mode-enabled` | both: unit verifies, daemon starts/stops; `runner_lock` = one engine at a time; never the microphone |
| Keyring (`secret_store.py`) | gnome-keyring | KWallet secret service | - (automatic, additive) | GNOME: migrate + read back + clear; Plasma with no keyring: nothing moved, key kept |

**Test hygiene learned the hard way here:** Gio caches ONE session-bus connection per
process. A test that only sets `DBUS_SESSION_BUS_ADDRESS` can still reach the user's
real bus if an earlier test in the run opened it - the global-shortcut test did, once,
sending BindShortcuts to the real desktop portal. Anything that talks D-Bus in a test
takes an explicit private connection (`Gio.DBusConnection.new_for_address_sync`) or
runs in a subprocess. `dbus-launch` is not installed on the dev box; use
`dbus-daemon --session --print-address`.

## Audit-verified known issues (confirmed present)


- **`midi.py` is the clearest illustration of this whole section — and it could
  not even be imported.** Added 2026-10-03 as a third member of this class
  (alongside `singing.py`/`prosody.py`): it reads a Standard MIDI File and fits
  its melody to a line of syllables, it is 357 lines of careful code, and
  `grep -rn "\bmidi\b" --include=*.py usr/` finds **no importer at all** — the
  one hit is `senses/capture.py`'s regex for ALSA `pcm` nodes. There is no
  `tests/test_midi.py` either.

  **It was also broken in the most basic way available, and 4603 passing tests
  said nothing.** A module-level line `_ = (os, struct)` referenced a name
  `os` that was never imported, so `from shani_chronoa import midi` raised
  `NameError: name 'os' is not defined` on line 357 of the file — a module
  that is 100% unreachable and 100% unimportable. Fixed by deleting the line
  (pyflakes 4.0.1 flags it; the suite does not, because nothing imports the
  module).

  **Then `tests/test_midi.py` was added (same day) and two more real bugs
  surfaced within minutes, both of which the empty test suite had been
  concealing.** Writing the first MIDI bytes by hand is what found them:

  - **Every note was half its real length.** `seconds_per_tick` was
    `(60.0 / bpm) / ticks_per_second`, but `ticks_per_second` was itself
    derived from `TICKS_PER_BEAT`, so the length of a beat was applied twice.
    A quarter note at 120bpm in a 480-division file parsed as **0.25s instead
    of 0.5s** — a melody sung from a parsed file ran at double speed. Any file
    whose division was not 480 was wrong by a different factor again (division 96
    is common), because the only correct denominator is the file's own
    `division`. Now `(60.0 / bpm) / division`, with the SMPTE branch kept
    separate since its clock is the recording equipment's, not the tempo's.
    Verified by running: 0.5s / 0.5s / 0.5s at 120bpm for divisions 480 and 96,
    1.0s at 60bpm, onsets 0.0 / 0.5 / 1.0.
  - **`from_url()` could never have worked.** It passed `_Bytes` to
    `read_notes`, which called `Path(path).read_bytes()`; `Path()` rejects an
    object that is not `os.PathLike`, so every call raised
    `TypeError: argument should be a str or an os.PathLike ... not '_Bytes'`.
    `read_notes` now takes either a path or anything with `read_bytes()`.

  Recorded here because the lesson is sharper than any of the three bugs: **the
  absence of an importer is what hid a syntax-level defect, a factor-of-two
  timing error and an unusable public function from the entire suite.** Adding
  the test file is done; **wiring the module to something real is still open**,
  and this entry is the reason to distrust any future "green, so it works"
  claim about a module with no callers.

- **`singing.py` + `prosody.py` are fully built, unit-tested (`tests/test_singing.py`,
  `tests/test_prosody.py`), verified on a real image — and nothing in the
  product imports either module. OPEN as of 2026-10-03.** `grep -rn "singing\|prosody"
  --include=*.py usr/` outside those two files returns **nothing**: no skill, no
  trigger, no `tts.py` path, no CLI. Their only consumer is
  `../shani-testbed/slot-tests/chronoa-singing.sh`. This is exactly the dead-code
  class at the end of this section (a module that is green in isolation and
  unreachable at runtime), so it is recorded rather than assumed shipped.

  What *is* real, measured on a booted `@blue` (`shanios-20260925-gnome`,
  Chronoa overlaid, kokoro + soundstretch + sox present): per-note pitch is
  exact — a four-note rising plan of +0/+2/+4/+6 semitones read back
  +0.0/+2.0/+4.2/+6.1 by zero-crossing count — and the `soothing` voice style
  measurably moves the audio (1.28x at 180 Hz, 0.87x at 3500 Hz, with a `bright`
  control moving 3500 Hz to 1.40x the other way). So the capability works on real
  hardware; what is missing is a **surface**: which entry point should reach it
  (a `sing` skill? a reply mode? a trigger?), and that is a design decision
  rather than a mechanical wire-up — the same reason `ipc.py` was deleted rather
  than wired.

  Two dependencies to know before that surface exists, both measured on the same
  run: `soundstretch` (or `rubberband`) is what shifts a note — `sox` alone
  cannot — and `singing.singing_support()` reports `can_track_pitch: False`,
  which is the honest ceiling and is stated in the module itself: nothing in the
  codebase measures the pitch of the audio it is shifting, so the contour cannot
  be closed against what is actually sung.

- **`SandboxExecutor`: the four policy guards scanned a shell *string*, so shell
  expansion defeated every one of them — FIXED (2026-09-30) by taking `argv`
  instead of a string.** The four checks below are the *pre-fix* shape, kept
  because the reasoning is what explains the fix and because a future change
  that reintroduces string matching should be recognisable as the same mistake:

  | # | Guard | Method |
  |---|---|---|
  | 2 | privilege escalation (`sudo`/`pkexec`/`su`) | `"sudo " in raw_cmd` |
  | 3 | internal binaries, isolated levels | `set(raw_cmd.split())` |
  | 4 | `config.blocked_binaries` | `_first_blocked_binary` |
  | 5 | `DANGEROUS_BINARIES` | `_first_blocked_binary` |

  Verified by running, against the real `SandboxExecutor` at
  `LEVEL_3_HOST_USER` (the default for every skill call): plain `dd` is refused
  with `126`, while `$(echo dd) status=...` and `d\d status=...` both reach the
  **real system `dd`**, which then errors on its arguments — proof it ran, not
  proof the filter declined. Check #2's predicate was tested the same way and
  `$(echo sudo) reboot`, `` `echo pkexec` id ``, `su${IFS}-c id` and
  `sud\o reboot` all pass it.

  **This was latent, not a live hole, and the distinction mattered.** Both
  production call sites `shlex.quote` everything untrusted:
  `tools.py:370` builds `python3 -c {shlex.quote(program)}` and
  `argfile.py:380` builds `python3 -c {shlex.quote(_REFERENCE_PROGRAM)}
  {shlex.quote(envelope_path)}`, writing untrusted *values* into an `O_EXCL`
  0600 JSON envelope rather than into the command string. No skill module uses
  `shell=True` with interpolated values. So on the production path the guards
  inspected `python3 -c '<quoted program>'` and were very nearly a no-op — which
  is exactly why this survived so long unnoticed, and why "the guards are on" was
  never the same claim as "the guards work".

  It was recorded rather than quietly patched because the correct fix is not a
  filter. Two traps, both measured rather than assumed, and both of which decided
  the shape of the fix:

  - The obvious repair — refuse any command containing `$`, a backtick, `\` or
    `${` — false-positives on valid calls, because `shlex.quote` does not strip
    those characters, it only wraps the program in single quotes. Argument
    *data* carrying `$(...)`, a backtick, a regex backslash or `$5` lands
    verbatim inside the quoted program. This is why the fix inspects `argv` and
    never the text, and why `test_a_python_program_carrying_metacharacters_is_not_refused`
    exists: a python program whose *payload* contains a metacharacter is a
    legitimate call and must still run.
  - The fix was not mechanical, because `tests/test_sandbox_parent_death.py`
    calls `_run_host("sleep N; touch MARKER &")` and depends on `;` sequencing
    and `&` backgrounding, neither of which argv can express. **That decision
    was resolved as: an explicit `sh -c` stays reachable as a visible opt-in**,
    which is honest — a blocklist never could police a shell string — and is why
    `_shell_script()` exists and why it is tested to fire only for a real shell.

  Do not "fix" one guard in isolation. They share a root cause and a fix that
  patches only #5 leaves #2 — the privilege-escalation one — just as bypassable.
  That is exactly what happened: all four moved to `argv` together.

  **What the fix actually is.** `execute()` now takes `argv: list[str]` and
  returns `(126, ...)` for a `str`, so the old shape cannot be used by accident.
  The program is resolved with `_program(argv)`, which skips leading
  `VAR=value` assignments and one leading `env` — `env FOO=bar python3 ...` and
  `FOO=bar python3 ...` both run python3, so a guard reading only `argv[0]`
  would see `env`, match nothing, and wave the real program through. Matching is
  still basename-based with dotted variants treated as the binary they are named
  after (`mkfs.ext4`), and still never substring, so `add` and `ddrescue` are not
  `dd`. An explicit shell script is accepted, but `_unresolvable_script()` refuses
  one containing `$(`, a backtick or `${` rather than guessing — the cost is real
  and deliberate (`sh -c "echo $(date)"` is refused too), and the alternative is a
  check that silently fails on the input it exists to catch.

  Verified by running, not by reading: 46 cases in `test_sandbox_argv_policy.py`,
  and three mutations were run to confirm the suite can actually fail — reverting
  `_program()` to a naive `argv[0]` fails 5, re-allowing a `str` fails 2, and
  emptying the `_EXPANSION` tuple fails 3.

- **`SandboxExecutor._run_host()`: `timeout_seconds` was never actually
  enforced for LEVEL_3_HOST_USER (the default level for every skill call)
  — FIXED (2026-09-18).** A foreground command that ran past its configured
  timeout was never killed: the poll loop just gave up waiting and the
  function returned `(0, "Command started and continues running in the
  background (PID: ...)", ...)` — reporting success while the real process
  kept running on the host, completely untracked, for as long as it liked.
  **Verified live**: a command with `timeout_seconds=2` running `sleep 10 &&
  touch marker` returned exit 0 after 2s while the `sleep` kept running and
  the marker file was created 8 seconds later, proving the process was never
  terminated. This defeats the entire stated purpose of the sandbox executor
  (bounding LLM-issued commands) for the common case — every skill call
  goes through `tools.py:_get_sandbox_config()`, which defaults to
  `LEVEL_3_HOST_USER`. Fixed by launching with `start_new_session=True` and,
  when a genuinely-foreground command (not `&`/`gtk-launch`/`xdg-open`)
  exceeds its timeout, `os.killpg()`-ing the whole process group and
  returning `(124, "...timed out...", ...)` instead of `(0, "...continues
  running...")`. **Verified live after the fix**: the same repro now kills
  the sleep within the 2s window (marker file never created) and returns
  124; real backgrounded commands (trailing `&`) and normal quick commands
  were re-verified unaffected.
- **`SecretsManager`'s vault was never populated with the actual secrets it
  exists to protect — FIXED (2026-09-18).** `set_secret()`/`get_secret()`
  were never called anywhere in the app; the real cloud LLM API keys
  (Anthropic/OpenAI/Google/Groq/etc.) are stored via `ChronoaConfig`/
  GSettings instead (a deliberate, documented choice — see
  `cloud_llm_api_keys()`'s own threat-model docstring in `config.py`). This
  meant `sanitize_text_for_llm()` — the P0 "prevent API keys leaking to LLM
  providers" fix — always checked against an empty `_cache` and silently
  redacted nothing. **Verified live**: a fake key embedded in a prompt
  string passed through `sanitize_text_for_llm()` completely unredacted
  before the fix. Fixed by adding `register_runtime_secret()` (registers a
  value in the in-memory cache for sanitization/env-injection purposes only,
  without persisting a second copy to `vault.json` — GSettings stays the
  single source of truth for these keys, matching the existing design) and
  calling it from `app.py:_maybe_enable_cloud_fallback()` for every
  non-empty configured provider key before building the `CloudLLMChain`.
  **Verified live after the fix**: the same fake-key repro is now redacted
  to `$SECRET:CLOUD_LLM_ANTHROPIC`, and the vault file is confirmed to stay
  absent from disk (no second on-disk copy created).
- **The 2026-09-18 security/architecture modules that were built, unit-tested
  in isolation, and never imported by anything that actually runs: two were
  deleted (2026-09-30), and the other two are mid-wiring — grep before you
  trust this list.** Confirmed by `grep` for real callers across the whole
  package, not by a `feat:` message and not by a passing module-level test.

  - **`ipc.py` — DELETED (2026-09-30).** `PeerValidator.sign_message()` was a
    bare `hashlib.sha256(payload)` with **no key**, so signing and verifying
    were the same operation and anyone could forge a "signed" message.
    Wiring it in would have made Chronoa *appear* to have authenticated IPC
    while providing none, which is worse than having none. There was also
    nothing for it to protect: no D-Bus or socket IPC surface exists, the MCP
    server is explicitly stdio-only/same-user-trusted (see `mcp.py`'s own
    "Trust model" docstring), and the package's only near-match was the
    unrelated string `--unshare-ipc` in `sandbox/executor.py` — a bwrap flag,
    not an import. This was Implementation Roadmap item 4's own explicit
    choice: "decide whether there's a real integration point before wiring it
    in, or remove it."
  - **`skills/scan_archive.py` — DELETED (2026-09-30).** A superseded
    duplicate, not an orphan. `extract_archive._check_members()` already
    rejects `..` components, absolute paths and paths resolving outside the
    destination, refuses symlink/hardlink/device/FIFO members, and caps member
    count — strictly better on every count than the deleted copy, which had
    only the first two checks. Two overlapping guards on one path is the
    failure mode this file keeps warning about, where one gets hardened and the
    other silently does not. `tests/test_scan_archive.py` went with the module,
    which briefly left the zip-slip guard itself exercised by no test —
    `tests/test_extract_archive.py` now covers it directly (20 tests), including
    the symlink-through-destination escape the stdlib does not save you from.
  - **`gateway_supervisor.py` and `sandbox/profiles.py` (`AgentProfile`) —
    BOTH WIRED (2026-09-30); do not add either back to the dead-code list.**
    They were dead when first measured; both now have real callers:
    `sandbox/executor.py:23` for `AgentProfile`, `audio.py:42`/`:105` for
    `GatewaySupervisor`. Re-measure with
    `grep -rn 'gateway_supervisor\|AgentProfile' usr/` and trust that output
    over any list here. This entry has already contradicted itself twice, in
    both directions — see `tool_tracking.py` below, which was listed as dead
    while live and would have been deleted by a reader who trusted the list as
    this file instructs. That is the direction that actually destroys work, so
    a stale "dead" label is worse than a missing "live" one.

  **`tool_tracking.py` is not in this list and must not be added back.**
  It was wired in later the same day: `tools.py:37` imports `ToolTracker,
  ORIGIN_USER` and `triggers.py:48` imports `ORIGIN_UNATTENDED`, so it is live
  on both the user-tool and unattended-trigger paths. This entry previously
  listed it as dead while the Implementation Roadmap below said the same work
  was DONE — the file contradicted itself, and a reader who trusts the
  dead-code list (as this file instructs) would delete a live module. Two other
  modules an audit pass reported as dead are also live and must not be
  "cleaned up": `markdown_lite.py` (`gui.py` calls
  `markdown_lite.to_pango`, which is what renders `**bold**` in the
  transcript) and `verification.py` (`tools.py:166` calls
  `verification.verify(...)` on **every** skill invocation — security-critical,
  and its own comment says it "must never kill the action").

  The `NOT_A_SKILL` opt-out marker in `skills/__init__.py` is **kept**, and
  `tests/test_skill_discovery_noise.py` still covers it, including the rule
  that only a built-in may use it — a user module that declares itself a
  non-skill is dropped loudly, never silently. No built-in carries the marker
  now that `scan_archive` is gone; the built-in half of the behaviour is
  covered from the other side, by asserting that no built-in logs a skip
  warning at all. (An earlier version of this file claimed the deleted module
  logged a `Skipping 'builtin:scan_archive'` warning on every startup. That
  was already stale before the deletion — the marker was suppressing it.)

  Do not treat a `feat: add X` commit or a passing module-level unit test as
  proof `X` is live in the running app — grep for real callers first, per this
  file's own verify-by-running rule above.
- **`tests/`: two more pre-existing bugs found and fixed the same pass.**
  Fourteen `.pyc` files under `usr/lib/shani-chronoa/shani_chronoa/**/__pycache__/`
  were committed to git (`git ls-files | grep __pycache__` — the
  `.gitignore` rule existed but never retroactively untracked them),
  directly contradicting this repo's own `test_no_pycache_in_packaged_payload`/
  `test_no_bytecode_files_in_packaged_payload` tests, which were failing
  before this pass — `git rm --cached` fixed it. Separately,
  `tests/test_skills.py::test_execute_tool_non_dict_arguments_are_ignored`
  referenced a `tools_mod._HANDLERS` attribute that doesn't exist (renamed
  to `_HANDLER_FNS` at some point, or the test predates `execute_tool`'s
  current subprocess-based dispatch, which makes monkeypatching a handler
  directly impossible anyway) — rewritten to mock `_SANDBOX.execute` and
  assert on the built command string instead, which actually matches how
  `execute_tool` dispatches today. Full suite: 122 passed / 3 failed before
  this pass, 125 passed / 0 failed after.

## Garuda Cross-Reference Findings (added 2026-09-17)

Based on a full scan of 29 garuda-linux repos mapped against shani (see `../garuda-catalog.md` — 29 repos, not 34; several user-listed names don't exist). See `../garuda-mapping-analysis.md` and `../deep-analysis.md` for full details. Sayri (a Pulsar OS AI assistant, present in garuda-clones/ — not a garuda repo itself) is the most directly comparable repo — both are local-first GTK4 AI assistants.

### 🔴 CRITICAL: Security gaps vs sayri

Sayri has security features Chronoa lacks. These are high-priority:

1. **Add sandbox execution for shell commands** (estimated 2-3 days).
   - Sayri's `adapters/sandbox/executor.py` implements 5-level sandboxing: LEVEL_0 (no exec), LEVEL_1 (bwrap read-only), LEVEL_2 (bwrap isolated dev), LEVEL_3 (host user), LEVEL_4 (host root via pkexec).
   - Blocks privilege escalation (`sudo`, `pkexec`, `su`) for all levels except HOST_ROOT.
   - Blocks dangerous binaries (`mkfs`, `dd`, `shutdown`, `reboot`) via configurable blocklist.
   - Uses `subprocess.run(timeout=...)` for command-level timeouts.
   - Detects GUI access attempts in sandboxed execution.
   - **Where**: `shani_chronoa/tools.py` where `execute_tool()` (defined at `tools.py:25`, called from both `assistant.py:98` and `mcp.py:121`) runs bash commands. **Model**: `sayri/adapters/sandbox/executor.py`.
   - Impact: Prevents the LLM from running `mkfs`, `dd`, `sudo` etc. accidentally.

2. **Add secrets vault** (estimated 1-2 days).
   - Sayri's `domain/secrets_manager.py` stores secrets XOR-obfuscated using a key derived from `/etc/machine-id` + UID, with `os.chmod(file, 0o600)`.
   - `sanitize_text_for_llm()` replaces all secret values with `$SECRET:<key>` BEFORE sending to any LLM provider.
   - `inject_environment()` injects secrets ONLY into child process env at execution time, never stored in plaintext.
   - **Where**: new file `shani-chronoa/usr/lib/shani-chronoa/secrets_manager.py`. **Model**: `sayri/domain/secrets_manager.py`.
   - Impact: Prevents API keys (stored under the `org.shani.chronoa` GSettings/dconf schema — audit-verified 2026-09-17: there is **no** `~/.config/shani-chronoa/config.json`) from leaking to LLM providers.

3. **Add LLM prompt sanitization** (estimated 2-4 hours).
   - Add `sanitize_for_llm()` that redacts secrets from any text before sending to LLM API.
   - **Model**: `sayri/domain/secrets_manager.py:143-152`.

4. **Add peer-validated IPC** (estimated 1 day).
   - Sayri's UNIX socket at `~/.local/share/sayri/sayri.sock` rejects connections from other users (peer UID validation). Chronoa uses D-Bus with no peer validation.
   - **Where**: Chronoa's D-Bus interface. **Model**: `sayri`'s UNIX socket peer check.

### 🟡 HIGH: Architecture gaps

5. **Gateway architecture for external channels** (estimated 2-3 days).
   - Sayri has a Gateway Supervisor managing external channels (Discord, Telegram, Matrix) with per-instance sandbox binding, PID files, auth files, and inactivity timeouts.
   - Chronoa has no gateway concept — add one for any future external integrations. **Model**: `sayri/gateway_supervisor.py`.

### ✅ What Chronoa already does better than Sayri

- Wake word with VAD calibration (Sayri: basic)
- Barge-in with TTS interrupt + VAD layer (Sayri: no barge-in)
- 7+ LLM providers vs Sayri's OpenAI-compatible only
- Explicit cloud fallback (BYOK) — Sayri requires API key setup
- Real MCP server (Sayri: no MCP)
- Calibrated noise floor for voice quality

### 🔍 Re-Scan Findings (2026-09-17)

Re-scan against `../garuda-catalog.md` (29 repos, not 34). **Confirmed mapping: sayri** ✅ — it is an ACTUAL repo at `../garuda-clones/sayri/` (the catalog's discrepancy table confirms `garuda-sayri` → `sayri`). Its source is directly readable, and the file paths cited in the findings above (`adapters/sandbox/executor.py`, `domain/secrets_manager.py`, `gateway_supervisor.py`) are all verified present under `usr/share/sayri/lib/sayri/`.

**Read sayri's source directly for implementation patterns** — `../garuda-clones/sayri/` has its own 210-line README and a full package layout with more patterns than the earlier analysis captured:

- `domain/skills_scanner.py` — skill/plugin risk scoring that can block an installation outright
- `downloads.py` — zip-slip protection on skill/plugin downloads
- `ipc.py` — single-instance UNIX socket (`$SAYRI_STATE_DIR/sayri.sock`, chmod 600, peer UID validation)
- `domain/cron_scheduler.py`, `domain/triggers.py` — scheduled/triggered agent runs
- `wizard.py` — first-run provider/API-key setup screen
- `overlay.py`/`orb.py`/`cajita.py` — transparent layer-shell overlay UI (libgtk4-layer-shell)
- `webkit.py` + `web/` — WebKit-based web UI (App.js, metro.config.js)
- `settings_window.py` — GTK settings window (can download whisper-cli/Piper models on first use)

**New gaps** (sayri has, chronoa lacks):

1. **Skill/plugin download with zip-slip protection + risk scoring** — sayri installs skills from store-os.inled.es/ClawHub with a scanner that assigns risk scores and can block; chronoa only loads local user-dropped skill modules.
2. **Single-instance enforcement via UNIX socket** — sayri is single-instance via `sayri.sock` with peer UID validation; chronoa uses D-Bus with no peer validation (already noted above).
3. **Scheduled/triggered agent runs** — sayri has `domain/cron_scheduler.py` + `domain/triggers.py`; chronoa has no scheduling.
4. **First-run setup wizard** — sayri has `wizard.py` for provider/API-key setup; chronoa relies on gsettings defaults.
5. **Transparent layer-shell overlay UI** — sayri pins a transparent always-on-top overlay (orb + cajita) via libgtk4-layer-shell; chronoa uses a conventional window.

**Shani advantages** (chronoa has, sayri lacks) — the list above is confirmed against sayri's own README: wake word with VAD calibration, barge-in with TTS interrupt, 7+ LLM providers vs sayri's OpenAI-compatible-only, explicit BYOK cloud fallback, real MCP server (sayri: none), calibrated noise floor.

**Qt GUI gap note**: sayri is Python/GTK4 — the same stack as chronoa — so garuda's 12 Qt6 GUI apps are not the relevant comparison for this repo; the GTK4/Python stack is shared with sayri, not with garuda's Qt fleet.

## Deliberately not done (as of 2026-09-16)

Found during a competitor-comparison pass but not implemented, because each
needs a real design decision rather than just typing code - don't assume
these were missed:

- **System tray / background mode.** Wake-word listening makes an
  always-visible foreground window increasingly pointless, but this is a
  real GTK4 application-lifecycle change, not a small one.
- **An MCP client** (consuming external servers like Context7/filesystem/
  GitHub) - conflicts with the fixed-whitelist skill design's whole safety
  rationale and needs its own trust story first.
- **Wyoming protocol support** (the live, maintained successor to
  Rhasspy's dead Hermes/MQTT approach, used by Home Assistant's Assist
  pipeline) - a real, scoped integration opportunity if "join the
  smart-home voice ecosystem" ever becomes a goal, but a new subsystem, not
  a bugfix.
- **sherpa-onnx** as an alternate/backup wake-word engine to openWakeWord -
  filed away, not urgent unless openWakeWord's real-world accuracy
  disappoints.

### 📋 Implementation Roadmap (2026-09-17)

Implementation priorities are per `../IMPLEMENTATION-ROADMAP.md` (master roadmap for the whole shani ecosystem).

**Status correction (2026-09-18, audit-verified — read this before trusting
any item below):** items 1-3 and 7 were implemented as code (commits
`fb5fd67`, `9fde500`, `b3dd356`, `d85df5c`) but were NOT actually functional
as shipped — see the "Audit-verified known issues" section above for the
live-execution proof and the fixes applied this pass (items 1-3, 7 are now
genuinely done). Items 4, 6, 8 remain implemented as standalone modules but
confirmed (via `grep` for real callers) **never imported by anything that
runs** — dead code, not done, and deliberately left that way (each needs a
real design decision - see the entries below and the "Deliberately not
done" section - not a mechanical wire-up like item 7 got). Don't take a `feat:`
commit message or this list's prose as proof of "done" — grep for real
callers first, and check what the default arguments actually do when
nothing overrides them (item 7's `/var/log` default was the second
"passes its own unit test, crashes for real" bug found this pass).

**Later update (2026-09-30):** items 4 and 5 are no longer modules at all —
both were deleted, after deciding not to wire them in. See "Audit-verified
known issues" above for what replaced each and why. Items 6 and 8 are being
wired in a concurrent pass, so this paragraph's claim about them is a
statement about the 2026-09-18 measurement, not about the tree today.

1. ~~**Sandbox Executor** (P0, 2-3 days)~~ **Module exists and IS wired into
   `tools.py`'s `execute_tool()` path, but its timeout enforcement for
   `LEVEL_3_HOST_USER` (the default) was a no-op until fixed 2026-09-18** —
   see "Audit-verified known issues" above.

2. ~~**Secrets Vault** (P0, 1-2 days)~~ **Module exists but its
   `set_secret()`/`get_secret()` were never called anywhere — the vault's
   cache was always empty until `register_runtime_secret()` was added and
   wired into `app.py` 2026-09-18** — see "Audit-verified known issues"
   above. Real API keys still live in GSettings/dconf by design (roadmap
   #2's `config.json` premise was already wrong per the 2026-09-17 note
   below — no such file ever existed).

3. ~~**LLM Prompt Sanitization** (P0, 2-4 hours)~~ **`sanitize_text_for_llm()`
   exists and IS wired into every LLM call path (`llm.py`, `cloud_llm.py`
   x3), but was a silent no-op against real keys until item 2's fix above
   made the vault aware of them — DONE as of 2026-09-18.**

4. **Peer-Validated IPC** (P3, 1 day) — **DECIDED AND CLOSED (2026-09-30):
   removed rather than wired in** (roadmap #28). `ipc.py`'s `PeerValidator` was
   never imported anywhere and was not a real peer-credential check — an unkeyed
   SHA256 hash, so signing and verifying were the same operation. Chronoa also
   has no D-Bus/socket IPC surface for it to protect: the MCP server is
   explicitly stdio-only, same-user-trusted (see `mcp.py`'s "Trust model"
   docstring). See "Audit-verified known issues" for the full reasoning. The
   *capability* gap versus sayri's single-instance socket remains real; the
   fix for that is a real socket with peer-credential checks, which is a new
   subsystem and not this module.

5. **Zip-Slip Protection + Skill Download Validation** (P3, 1 day) —
   **PARTIALLY CLOSED (2026-09-30)** (roadmap #29). The protection half is done
   and stronger than the roadmap assumed: `extract_archive` checks every member
   before writing anything, and refuses `..` components, absolute paths,
   paths resolving outside the destination, link/device/FIFO members and
   oversized listings. `skills/scan_archive.py` was deleted as a superseded
   duplicate of exactly that. The *validation* half — a store to download
   skills from — is still not done, and still is not wanted without its own
   design decision: Chronoa only supports local
   `~/.config/shani-chronoa/skills/` drop-in.

6. **Gateway Supervisor Architecture** (P2, 2-3 days, only if external
   channels are planned) — **the heartbeat half is now WIRED (2026-09-30).**
   `audio.py:42` imports `GatewaySupervisor` and `:105` holds a module-level
   `_SUPERVISOR`, supervising the real `pw-record`/`pw-play` children — a hung
   `pw-record` means the assistant silently stops listening with nothing
   reporting it. `register_agent`/`broadcast_message` remain dead: they imply a
   multi-agent surface Chronoa does not have, and none was invented for them.
   Discord/Telegram/Matrix integration is still not a goal (roadmap #22).

7. ~~**Tool Call Tracking** (P2, 1 day)~~ **DONE (2026-09-18).** Wired
   `ToolTracker` into `tools.py:execute_tool()` — every call (success,
   non-zero exit, or exception) is now recorded to an in-memory ring
   buffer (`_TRACKER.get_calls()`, last 100) and appended to
   `~/.local/share/shani-chronoa/logs/tool_calls.log`. Its own default
   `LOG_DIR` was `/var/log/shani-chronoa` — not writable by the normal
   desktop user Chronoa actually runs as; **confirmed live**:
   `ToolTracker()` with defaults raised `PermissionError` before this fix
   (the module's own unit test never caught this because it always passes
   an explicit `tmp_path` override). Changed the default to
   `~/.local/share/shani-chronoa/logs`, matching the sandbox executor's own
   per-user state directory, and capped the in-memory list to a
   `deque(maxlen=100)` (was an unbounded list) per the roadmap's own "last
   100" spec. **Verified live**: a real `execute_tool('get_datetime', {})`
   call produced a matching entry in both `_TRACKER.get_calls()` and the
   on-disk log file (roadmap #20).

8. **Per-Agent Sandbox Profiles** (P2, 2 days) — **WIRED (2026-09-30).**
   `sandbox/executor.py:23` imports `AgentProfile`/`ResourceCeiling`/
   `profile_for_origin` and enforces a per-**origin** profile, so an unattended
   trigger action gets a stricter sandbox than a user-initiated one. No profile
   field can *loosen* a guard: `ceiling()` only tightens the timeout, no
   allowlist widens, and the blocklist is not runtime-extensible. `memory_limit`
   /`cpu_limit` are enforced with `setrlimit` rather than left decorative —
   wiring the profile in without enforcing them would have created a new piece
   of dead config that merely looks configured. (roadmap #21)

9. **LICENSE file** (P3, 5 min) — **DONE.** A `LICENSE` file is present
   (audit-verified 2026-09-18); the master-roadmap #31 "4 repos missing a
   LICENSE" list is stale for this repo.

10. **Shared CI templates, Renovate, conventional commits** (P1, cross-repo)
    — Adopt `shani-ci-commons` (roadmap #7), add `renovate.json` (roadmap
    #8 — Python repo: pip + GitHub Actions), enforce conventional commits
    (roadmap #9). Still open. **Correction (2026-09-18):** the "this repo is
    not a git repo" note here was stale even at the time it was written —
    verified this pass: real git history, 9 commits, `git status`/`git log`
    work normally. Whatever blocked git detection during the 2026-09-17
    pass was environmental, not a property of this repo.
