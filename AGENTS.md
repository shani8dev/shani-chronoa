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

## What counts as done

The governing principle, and the test every change is judged against:

> **कर्मण्येवाधिकारस्ते मा फलेषु कदाचन** — we have the right to our
> *actions*, never to the *fruits* of them. (Gita 2.47, and its chiasm in
> Shanti Parva, Udyoga 153.10-11: *"You have a right to action, but the right
> to its fruits is not yours."*)

In engineering terms: **the work is the deliverable; the metric is an
instrument, never the goal.**

What this rules out, concretely:

- **A metric as the objective.** Accuracy, `beats_chance`, a coverage count.
  These are read, never chased. A change that moves a number without making
  anything more true is not an improvement.
- **A truthful refusal demoted as a failure.** `train_and_save()` declining with
  *"the training split contains no example of verified"* is the correct output
  for that data. Making it return something confident instead would be the
  actual defect.
- **A learned component that authorises anything.** It advises. Authority stays
  with the consent keys - the same division as right-to-action, not
  right-to-outcome.
- **Discarding knowledge for being not-yet-useful.** Retire it with the reason
  kept, and keep learning even when the result is not yet actionable.
- **Machinery whose only purpose is to report success.** If a number cannot
  honestly rise yet, the honest refusal beats the flattering figure.

What survives being wrong: **the log, the post-conditions, and the machine's own
evidence.** No model outlives its own justification, and none is allowed to
have one that isn't measured.

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

**Verification status 2026-10-06: 5364 passed, 27 failed, 39 skipped** (run in
six chunks, because a single 44-minute run kept being lost to a killed process
group). **All 27 failures are in test files this work never touched** - verified
by intersecting the failure list with the modified-file list, which is empty.
Every one is environmental on this box, and each is a real capability that is
genuinely absent rather than a defect:

| file | why |
|---|---|
| `test_browser_window_probe.py` (7) | WebKitGTK is not installed (`require_version` fails for both 6.0 and 4.1); the file's own message says it "proves nothing" in that case |
| `test_setup_extras.py` (6) | `ConsentRequired` - model downloads are gated shut in this environment. Verified pre-existing on the **unmodified** tree |
| `test_sandbox_seccomp.py` (2), `test_sandbox_profiles_live.py` (1) | Landlock and seccomp need the real kernel; AGENTS.md already says so |
| `test_sense_idle.py` (3) | real clock and real X resources, headless |
| `test_everyday_skills.py` (2), `test_input_skills.py` (1) | xdotool / X11 not present |
| `test_permission_decisions.py` (2) | no Wayland screen-capture tool (`grim`, `gnome-screenshot`) |
| `test_portal_input.py` (1) | no `org.freedesktop.impl.portal.RemoteDesktop` backend |
| `test_recordings.py` (1) | the consent switch is off here, so the honest refusal is returned instead of the granted path |
| `test_vision_sense.py` (1) | a real screenshot of this desktop, which needs `grim` |

**Two failures were mine to fix and are fixed**: `test_packaging.py`'s two
bytecode checks (690 stray `.pyc` left by ad-hoc probes - AGENTS.md's documented
trap, and the only reason to prefer `PYTHONDONTWRITEBYTECODE=1` on *every* probe)
and `test_skills.py`'s two lambda handlers (above). Cleaning the bytecode also
silenced four ordering-dependent failures elsewhere, which is worth knowing:
**stray `.pyc` changes what a full run exercises.**

Earlier in the same session: unit suite green on Ubuntu (1878 passed, 6 skipped)

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

**Two tests in `test_skills.py` could not pass as written - FIXED 2026-10-06.**
Both registered `Skill(run=lambda a: ...)`, and `skills/__init__.py` **refuses a
handler whose `__name__` is not an identifier** (the 2026-10-05 fix: a non-local
skill is dispatched by interpolating `handler.__name__` into an import, so
`from module import <lambda>` is a SyntaxError and the skill was advertised to
the model and failed on every call). So both lambdas were skipped with a warning,
`tools` stayed empty, and `test_duplicate_override_keeps_single_schema` read
`assert 0 == 1`. They were testing the **lambda rejection**, not the
duplicate-name behaviour they are named for. Now they use named module-level
functions.

Worth recording because of how it looked: the failure was `assert 0 == 1`, which
points at "the dedupe did not happen", and the dedupe was fine. A green-looking
assertion about a *count* is ambiguous between "the thing was removed" and "the
thing was never added", and only the warning line above the traceback said which.

**A module-level `lambda` is still a lambda.** The first fix here moved them to
module scope to "keep it inline", which reproduced the same skip.

**Kill a stale suite in one command and start the new one in another.**
`pgrep -f` has the same self-match as the `pkill -f` trap the workspace
AGENTS.md records, and the `[p]` trick does not save you when the pattern is a
fragment of your own command line. Measured 2026-10-06: a single command that
both killed a stale run and launched a fresh one matched *its own shell* — the
`/bin/bash -c` line contained the literal text `pytest tests/` — so `kill -9`
killed itself and the new suite died **before writing a byte**. Two consecutive
"SIGKILL, empty log" results read exactly like a suite that crashes on
collection, and sent me looking for a segfault in a GTK teardown that had never
been reached. Confirm the log has grown before believing any result about a run
that "crashed".

**⚠️ And the `[p]` trick fixes *self-match only* — it is not a safety guard.**
This repo's D-Bus work is where that became expensive on 2026-10-06. Cleaning up
the private `dbus-daemon`s from the gateway round-trip harness, I ran
`pkill -f '[d]bus-daem'`. The brackets stopped the pattern matching pkill's own
command line, and it **still matched every real `dbus-daemon`** — so it killed
the user's session bus at `/run/user/1001/bus`, GNOME Shell lost it and logged
out, and logind tore down four sessions in a row (`40`, `41`, `c3`, `42`,
journaled at 16:04:26–16:04:59). It had been run in several commands, hence four
teardowns.

Two things to take from it:

- **Never `pkill -f` a daemon the desktop depends on** — `dbus-daemon`,
  `systemd`, `pipewire`, `gdm`, `gnome-shell`, `at-spi*`, `ibus*`. Bracketing
  changes nothing about that; it only hides the pattern from `ps`.
- **Clean up by PID, not by name.** The private daemons are created with
  `--print-address --fork`, so the address (and therefore the PID, from
  `pgrep -f "dbus-daemon --config-file=$CFG"` where `$CFG` is *your* mktemp
  file) is knowable. Kill that PID. If you cannot name the PID, you cannot
  safely clean up, so leave it and say so.

And a heuristic that would have caught it in one second: **`pkill: killing pid N
failed: Operation not permitted` is not a partial success.** It means the pattern
matched a root-owned process — i.e. it matched more than you meant — and
everything else it matched was killed. Stop and re-read the pattern there
instead of continuing.

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

### Measured, both local models (2026-10-02)

Raw results: `shani-install-media/test-env/eval-out/eval-qwen3-{0.6b,1.7b}.md`.
62 cases, right tool **and** right arguments, scored only - never executed.

| config | 1.7B | 0.6B | mean seconds (1.7B) |
|---|---|---|---|
| `bare` (every schema) | **0/62** | 5/62 | 0.08 |
| `select` (tool_select) | **56/62 (90%)** | 44/62 | 7.7 |
| `select+recover` (the default) | 56/62 | 42/62 | 37.4 |
| `+compact` | 52/62 | 38/62 | 8.7 |

Four things this settles, all now the defaults in `local_llm.py`:

- **The harness is what makes the small model work.** `bare` scores zero on the
  1.7B, and not because the model is bad: 131 tool schemas are ~19,700 tokens
  against an 8k context, so the request is rejected before the model reads a
  word (`request (20724 tokens) exceeds the available context size (8192
  tokens)`). Narrowing to the relevant tools takes the same weights from 0/62 to
  56/62.
- **`bare` scoring 5/62 on the 0.6B but 0/62 on the 1.7B is not the 0.6B being
  better.** It is that the smaller model emits a shorter tool call that
  occasionally fits under the limit. Both are unusable; neither is a result.
- **`COMPACT_TOOLS = False`.** Shrinking the schemas further made it *worse*
  (52/62 and 38/62), not better. Measured, not assumed - this reverses the
  obvious guess.
- **`select` beats `select+recover` on speed** - equal accuracy on the 1.7B
  (56/62 either way) at 7.7 s against 37.4 s, and better on the 0.6B (44 vs 42),
  so recover stays a fallback rather than the default path.

The 1.7B run is the evidence for the whole `tool_select` design; read it before
changing how tools are offered.

## Distillation, and the student that now routes (2026-10-04)

`distill.py` asks the **configured** model which skill answers a request, keeps
only the answers a human already agreed with, and trains a small Bernoulli router
on those. `tools/route_cases.json` is the labelled corpus (54 requests, 6 per
skill, every label read off the live schema); `tools/distill_run.py` runs it and
prints what was learned. Measured on a real booted `@blue` slot, teacher
**llama.cpp / qwen3-0.6b** (`slot-tests/chronoa-distill.sh`, 9 pass 0 fail):

| what | measured |
|------|----------|
| teacher agrees with the human label | **47/54 (87%)** |
| student, held out by request | **62% vs a 12% majority baseline** (8 held out) |
| student right across all 54 cases | 32/54 - it can never be right on the 7 the teacher got wrong |
| `tool_select` sees the written student | yes, and picks `get_datetime` |
| `tools/eval_cases.json` shape (one request per skill) | **refuses**, with the reason |

**The teacher was unreachable for Claude, ChatGPT, Gemini, Groq, OpenRouter,
opencode-zen and the keyless gateways**, because the first version called
`.chat()` and only `OllamaLLM` defines that; llama.cpp and the whole cloud chain
raised `AttributeError`, the loop moved on, and a machine with a configured
Anthropic key reported "no language model is configured". The uniform interface is
`chat_message()`, and `AnthropicLLM` reaches through the adapter already shipped.
Both privacy gates (privacy off **and** cloud-fallback-enabled) gate anything off
this machine, and keys are registered with `redactor` - without that the
sanitisation of the routing cases is a silent no-op.

**Four bugs in the student, all found by running it, not reading it:**

- `parse_choice` returned the first `[a-z][a-z0-9_]*` in a reply, so "The skill
  is `edit_image`" parsed as the skill **`he`**. It now segments whole
  identifiers and accepts a token only if it is a skill that exists.
- The per-class feature map was seeded from the **class** indices, so only the
  handful of features hashing below the class count were counted and every score
  came out equal for all but one class.
- Bernoulli's absent-feature term summed `log(1 - p)` over all 16,384 columns,
  which handed the most-used skill a ~450-point head **before a word was read**;
  every request was answered with it. Counted over the fitted vocabulary only,
  as scikit-learn does.
- The split was a contiguous tail, so the 54-request corpus (grouped by skill)
  put whole skills in the held-out half: a 91%-correct teacher "distilled" to a
  student scoring **0% against a 100% baseline**. It is now stratified per skill
  and never splits one request.

**The student is a prior, never an authority.** `tool_select.distilled()` may only
return names already in the candidate list, and only when the router separates
first from second by a score margin - there is no probability to quote, so there
is no confidence percentage anywhere. `distill.fallback()` carries a turn when
**no model answers at all**, and only for a skill that `safe_to_run_unattended()`
clears: nothing required (a router cannot fill in arguments) and nothing
destructive or outward-facing - `delete_file` declares no required parameter, so
the argument gate alone waves it through and the second gate is what stops a
classifier from choosing to delete something.

`distill.harvest_rows()` mines this machine's own sessions for
(request, skill) pairs, filtered by the **recorded** verdicts rather than a
prediction. On this machine it finds 11 rows, all of one repeated question, which
is why no student is shipped here and the fixture was used instead.

## The outcome model learns now, and this is the measured shape of it (2026-10-04)

Before today it could not be trained at all, and the reason was four defects
rather than weak features. All measured on this machine's own log - **11,636
labelled calls: 10,936 `unverified`, 351 `verified`, 349 `failed`**:

- `OutcomeModel.__init__` defaulted `self.w` to `None` while `fit()` read
  `self.w.get(...)`, so **every fit from scratch raised `AttributeError`** and
  `train_and_report()` crashed instead of reporting. A crash is not a refusal: the
  report that would have said "no signal" never appeared either.
- Unweighted, the model predicted `unverified` for **all 11,636 calls** -
  precision and recall exactly 0.000 on both minority verdicts. A 3%-positive
  class does not move a 30-step gradient through 1e-4 of L2. Inverse class
  frequency fixes it; 150 epochs instead of 30 changed nothing measurable.
- The balanced weights were **not normalised**, so every step was ~1e-7, the
  fitted weights landed near 1e-4, and `to_dict`'s `round(v, 6)` stored them as
  **zeros**: a file that verified its own digest and predicted 0.3333 for all
  three verdicts on every input.
- The split was ordered by time, so the training half held 8,462 `unverified`,
  264 `failed` and **zero** `verified` and nothing could be written. Folding over
  **feature vectors** fixed the starvation - all 1,979 `verified` calls live in
  just **3** distinct vectors, so no split that keeps a vector whole can balance
  them. `cross_validate()` is what `train_and_save` now judges on, and the model
  written is fitted on everything.

`Report.honest()` no longer asks only "does top-1 beat 94%?". That question
cannot be answered by any model on this data, and the gate had never once been
able to say yes. It now accepts a verdict that is genuinely **detected**: at
**2x its own base rate on both recall and precision**, with recall >= 20%.
Both halves are required because either alone is gameable - `verified` is 3% of
the data, so a model that flags *everything* as verified scores **~33x recall
lift** while being useless. `tests/test_outcome_model_learns.py` asserts that
degenerate report is refused.

**What it can and cannot do, on 5-fold grouped CV over all 11,636 calls:**

| verdict | recall | precision | recall lift | precision lift | verdict |
|---------|-------:|----------:|------------:|---------------:|---------|
| `unverified` | 85.5% | 96.8% | 0.91x | 1.03x | not detected |
| `verified` | 99.7% | 17.7% | **33.1x** | **5.86x** | **detected** |
| `failed` | 0.0% | 0.0% | 0.00x | 0.00x | not detected |

So: it can flag a call that looks like it will verify, 5.9x better than chance on
precision; it **cannot** spot failures at all; and as a top-1 classifier it scores
83% against a 94% constant, so it is a flag and not a predictor. `failed` is 7
vectors and 2 of them are 1,831 examples each - there is nothing to generalise
from. `tests/test_outcome_model_learns.py` holds all of this shut, and
`learning.render_outcome()` prints both lifts so a 94% score cannot read as
success.

## Installing on immutable ShaniOS: verified, not assumed (2026-10-04)

`shani-testbed/slot-tests/chronoa-state.sh` boots a slot and asks where
Chronoa's state actually lands, because on this layout "it works on my machine"
and "it works here" are different questions. The image's own
`image_profiles/shared/overlay/rootfs/etc/fstab` is the spec:

| path | what it is | consequence |
|------|-----------|-------------|
| `/` | read-only root slot (@blue/@green) | nothing may be written here |
| `/etc` | overlay, upper in `/data` | writable, survives a switch |
| `/var` | tmpfs (`systemd.volatile=state`) | **gone on reboot** |
| `/home`, `/root` | `@home`, `@root` btrfs | persists |
| `/data` | `@data` btrfs | persists |
| `/var/cache` | `@cache` btrfs | persists, **shared between slots** |
| `/var/log` | `@log` btrfs | persists |

Measured in a booted slot: **every** Chronoa path is writable, none is on
volatile storage, and a write-read-back round trip passes through all of them.

**The download cache is on `@cache`, and that is not luck.** `stt_provision`
defaults to `/var/cache/shani-chronoa/models`, which the fstab documents as the
directory that exists "to avoid re-downloading packages after slot switches" - so
a 639 MB model is fetched once per machine, not once per reboot *and not once per
blue/green switch*. `SHANI_DOWNLOAD_CACHE` overrides it.

**The write side was already right, and it is worth knowing why.**
`stt_provision._install()` always writes to `user_dir` and treats `system_dir`
(`/usr/share/piper`, `/usr/share/shani-chronoa/llm`, ...) as *read-only* - those
paths are only where a reader may look. So a read-only root needs no special
case: nothing tries to install into `/usr` in the first place.

**Two failures this test reported that were the test's own:**

- It exported `HOME=$T/home` under `/tmp`, then asked whether the paths were
  volatile - so it audited its own scratch directory and reported, in all
  seriousness, that Chronoa's models were "on tmpfs, so lost on reboot". The
  verdict now runs under `env -u HOME -u XDG_DATA_HOME ...`, because the question
  is where Chronoa puts things on a real machine.
- It failed every path on "not in my list of persistent filesystems", which under
  nspawn is `overlay` rather than `btrfs`. **Only tmpfs is a finding**; a
  filesystem this test does not recognise is reported as unrecognised, not as a
  defect - the same rule the diagnostics panel applies to `systemctl is-*`.

Both are the shape of bug this repository documents: a check that fails for its
own reasons and is then believed.

## The UI features worth taking from assistd, sayri and Alpaca (2026-10-04)

**Taken: the wait is now a state.** `assistd` carries
`VoiceCaptureState::Queued` — "waiting for the GPU to free up before
transcribing" — beside `Idle`, `Recording` and `Transcribing`. Chronoa had built
the gate that produces that wait (`speech_gate.py`) without producing anything
anyone could *see*: the orb simply paused between listening and thinking, which is
indistinguishable from the microphone having died. There is now a real
`AssistantState.QUEUED`, wired in `app/voice.py:_transcribe`, so the orb moves
and holds there for as long as the wait lasts.

Adding a state is not one line, and the tests that say so are the point:

- `test_window_ux.py` refuses **any colour reuse**, because colour alone excludes
  colourblind users. A first version deliberately reused listening's green - the
  reading that felt right - and the existing invariant overruled it. Queued is
  `#14b8a6`, teal, between listening (green) and thinking (amber), which is where
  the state actually sits.
- `test_window_theming.py` keeps an explicit `STATE_PALETTE` so a colour cannot
  "quietly slip through", and requires a matching `halo-<state>` ring so the two
  rings cannot disagree about what is happening.
- `tests/test_queued_state.py` adds the forward guard: every state needs a
  colour, an icon, a label, a CSS rule, and - **only if it animates** - a
  reduced-motion rule. That last qualifier matters: a first version demanded one
  of every state, which produced a test that could only fail.

**And then taken, because it turned out to be a missing feature rather than a
UI change: `assistd`'s presence cycle.** Measured on a real slot with a real
model, `slot-tests/chronoa-presence.sh`:

| step | measured |
|------|----------|
| while Active | **2,659 MB** resident in `llama-server` |
| after "Free the model" | **no process remains** — the memory went back |
| after a question | answering again, cold-started |
| what the UI shows | `ACTIVE | Free the model` |

**Chronoa could not release the model at all.** `local_llm` had `start_service()`
and no `stop_service()`, and setup ran `systemctl --user enable --now` — so from
the first login the unit came up on its own and stayed up, holding a 1.1-4 GB
model resident whether or not anyone was talking to it. There was no lever.

**Drowsy does not claim to unload weights**, because nothing here can. It is:
listening, model not resident, next question loads it cold. Sleeping is the same
but waits to be asked — which is the distinction a person actually wants between
"let go of the memory for now" and "leave the machine alone". A state called
"drowsy" that quietly meant "still fully loaded" would be the exact failure this
repository documents. `tests/test_presence.py` pins the *wording*, because the
wording is the claim.

Two things release depends on, and both were missing:

- `_use_local_server(wake=True)` now **starts the service** when it is not
  answering. With a bare `is_up()` check, a released model was simply gone — a
  question would have fallen through to the cloud fallback or nowhere. Releasing
  a model nothing brings back is not a feature, it is a brick. `wake=False`
  remains for callers that must not start anything: *checking* whether a model
  exists is not a reason to load one.
- The guard reads a **presence**, not the `_can_answer` bool. A bool cannot tell
  "no model installed" from "asleep on purpose", and a first version compared the
  bool to an enum — never true, so it silently disabled the whole guard.

`stop_service()` deliberately does **not** `disable` the unit: it stays enabled
so a question can start it again, which is what makes Drowsy mean *released, not
gone*. Disabling would leave a machine whose assistant silently never answers.

**Two measurement notes, both the same lesson.** The slot has no user session
bus, so `systemctl --user` cannot be exercised there — which is also why every
GUI run in that slot logs "Ollama not available" and starts with no model. The
test therefore measures the *memory* claim directly, because that claim does not
need systemd. And a first version started the binary straight from an `llm.env`
that had never been written, so the server took its own default port — **8080**
in llama.cpp 0.5.0, against the 8765 Chronoa configures — and the health check
polled the wrong one for four minutes. Reproduce the product's *sequence*, not
just its commands.

**And then taken as well, on the user's instruction to do both.**

### Alpaca's model manager — `gui/surfaces/models.py`

An `Adw.NavigationPage` with a view stack: **On this machine** against **Available
to add**, a search bar filtering both, and a `Gtk.FlowBox` per list whose
`min/max-children-per-line` are bound to `Adw.Breakpoint`s at 560px and 1000px.
Registered in `SURFACE_IDS` under "This machine", so it is in the sidebar.

25 cards across all four engines, read from `local_llm.TIERS`,
`stt_provision.MODELS`, `voices.VOICES`/`KOKORO_VOICES` and `local_vision.MODELS`
rather than a list kept in step by hand. Kokoro's card says **one download covers
every Kokoro voice**, because six cards without that sentence read as six
purchases of the same 310 MB.

**A card states a fact and offers one action; none of them loads a model.** A pin
writes a setting and stops - the model comes back through a question, via
`start_service()`, so a pin can never leave the assistant claiming to be ready
while a server it never started stays down.

Three things this cost, each found by building rather than by reading:

- `Adw.SearchBar` **does not exist** on libadwaita 1.5; `Gtk.SearchBar` does. Same
  shape as `Adw.BackButton` earlier - check the installed library.
- `KOKORO_VOICES` holds `KokoroVoice` objects, not strings, so `.split` on one
  raised on the first render.
- **The Eyes list was silently empty.** The vision check called
  `local_vision.is_provisioned`, which that engine does not have - the function is
  `verify` - inside a bare `except: pass`. So the panel shipped with no vision
  cards at all, which are exactly the models the "what do I have" view exists to
  show. `except: pass` around a probe is how a panel ends up quietly incomplete;
  the import guard is now around the *import* only, and a test asserts the kinds.

`Adw.Breakpoint.new()` takes a condition rather than a width and the condition is
built by `Adw.BreakpointCondition.parse()` - neither of which the first attempt
used. `gui/sidebar.py` already documents both by calling the installed library
and recording what raised, which is why this was copied rather than rediscovered.
And `Adw.Breakpoint` exposes no way to read its setters back, so the binding is
asserted where it is observable: the flowboxes' own column properties.

### sayri's tray indicator — `app/tray.py`

One click is one action: the indicator's `activate` calls the same
`app.toggle-listening` the window's button calls, and `popup-menu` is swallowed, so
there is no menu in the way. **There is one implementation of "start listening",
not two that can disagree.** The icon's label follows `AssistantState`, so it never
claims more than the assistant is.

GTK4 removed `Gtk.StatusIcon`, so this needs `libappindicator`, which Chronoa does
**not** depend on - and that is the point rather than a problem: `available()`
returns `(usable, why)`, `build()` returns `None`, and the Desktop panel has a
**"Tray icon"** row that names the package which would fix it. A tray that silently
does not appear reads as a bug in the assistant; one that reports "not installed -
the libappindicator package provides one" reads as a missing package.
- **assistd's *Drowsy* in its original sense** - llama-server still running with
  the weights unloaded, which needs llama-server's model-load API. What is built
  stops the server instead, which frees the same memory from a person's point of
  view at the cost of a cold start. The weights-unloaded variant is recorded here
  rather than claimed.

## What harness-study's assistd and sayri were worth (2026-10-04)

`harness-study/` holds 20 third-party harnesses. Reading `assistd` (Rust,
llama.cpp, Unix-socket IPC) and `sayri` (Python desktop assistant) against
Chronoa's own code, **one of the two headline ideas was already here**:

- **assistd streams TTS sentence by sentence** so playback starts before the
  model has finished. Chronoa already has this - `speech.SpeechQueue`, built per
  turn at `app/conversation.py:213` - and it is wired. Worth recording, because
  the plausible assumption is that a streaming reply needs streaming speech, and
  the sentence queue turns out to be the whole of it.

- **assistd's `QueuedTranscriber` asks "is the GPU busy" before every
  transcription**, and logs which way it went. Chronoa did not, so a transcript
  starting mid-reply competed with llama.cpp for the same accelerator. Now
  `speech_gate.py`, wired at `app/voice.py:_transcribe`: a bounded wait for the
  model to finish generating, and **transcribes anyway at the timeout** rather
  than losing what somebody said.

  Two details worth keeping: the wait is on the executor, not the GTK thread (the
  transcription is off-thread for the same reason), and `wait()` takes its clock
  as a parameter, so the tests prove the *timeout* instead of measuring how long
  four seconds is.

  `waited` means **it actually slept**, not "it was busy on the first look" -
  because assistd's `gpu_busy_timeout_ms = 0` means "ask, do not queue behind a
  stream that may never end", and counting that as a deferral would put a number
  on the log that never happened.

## Cloud voice, and the wizard's cloud branch that never asked (2026-10-06)

**There was no cloud voice at all, and "In the cloud" produced an assistant that
could not hear.** Three defects, all found by walking the real wizard and reading
what it said.

### 1. "Nothing to download" was true because the wizard had stopped asking

The Mode page's "In the cloud" row reads *"nothing to download"*. The flow it
produced was `welcome -> mode -> cloud-keys -> done`, and `on_finish` writes
`setup-complete=true` unconditionally - after which `needs_setup()` returns False
forever. So somebody who deliberately chose the cloud got a Chronoa that could
think and **could not hear**, and was never asked about it. The cloud branch now
goes `cloud-keys -> ears -> voice`, and Ears and Voice both say what they cost.

### 2. The Mode page's radio did nothing, and the Ears page charged for nothing

**The question was asked and the answer discarded.** `navigate()` took the
destination as a string and bound it into the button's handler when the page was
built - before anyone could toggle a radio on it. Measured: choose "In the cloud",
press Next, land on `brain`, the page offering a 1.1 GB local download. That is
the specific failure the page's own comment says it exists to prevent, three lines
above the wiring that caused it. `navigate()` now takes a callable, and the Mode
page re-titles the button on `toggled` so the label cannot disagree with the
destination (`tests/test_mode_choice_routes_the_flow.py`; both halves are asserted,
because a button that *goes* to Cloud keys while still reading "Next: Brain" is
the same defect wearing a different hat).

**In cloud mode the Ears page showed nothing and still charged 60 MB.** The model
list was built as `[]`, so the choice group rendered no rows - and then the same
`else` branch ran anyway and queued the recommended model's download. Measured with
`setup-mode=cloud`: Ears listed **zero rows** and the Review page read
**"Download 3 thing(s) ... 1.3 GB across 3 download(s)"**, one of which had no row
in front of it to see, choose or decline. A charge with no row in front of it is
not a choice, and that page's own subtitle promises *"Everything you picked, and
nothing you did not."* Fixed in `tests/test_cloud_branch_is_honest.py`.

**A gap that test file had, found by mutation rather than by reading it.** Three of
its four mutations were caught; `cloud-keys -> done` was **not**, because the tests
reached `ears` and `review` with `push_by_tag` - a test that navigates *around* a
route is not a test of the route. It now presses Next on the page the branch
actually lands on. This is the "negative control that cannot fail" trap in a new
place, and the fourth time in this repo that a source-level check has agreed with
code that does nothing.

### 3. `cloud_voice.py`: speech recognition and synthesis over the API

`stt.py`, `stt_parakeet.py`, `tts.py`, `sherpa.py` and `voices.py` contained
**five URLs between them and every one is a download** - a sherpa-onnx release, two
Piper releases, a Piper-voice archive. Speech itself was whisper-cli, Parakeet,
espeak-ng, Piper and sherpa-onnx, all local subprocesses; "cloud" meant
`cloud_llm.py`'s text and nothing else.

`cloud_voice.py` adds `CloudSTT` (`is_available`/`transcribe`/`transcribe_stream`,
`WhisperSTT`'s exact surface) and `CloudTTS` (`synthesize_to_bytes`/`synthesize`,
`PiperTTS`'s), so each is a *selection* rather than a branch at every call site.

**Four reasons it is not just another provider in `cloud_llm.py`:**

- **Audio is a different disclosure from text.** `redactor` keeps API keys out of
  prompts; nothing makes "the last thirty seconds of your microphone" safe. So it
  gets **two** switches, `cloud-stt-enabled` and `cloud-tts-enabled`, both default
  **false** - deliberately *not* `cloud-fallback-enabled`. A person who allowed
  prompts to leave as text has not agreed to upload a recording, and one switch
  cannot express that difference.
- **"Fallback" is the wrong word** for the primary listener on a machine with no
  local model. Hence separate names.
- **The privacy gate is per request here, and that is the structural difference.**
  `cloud_llm` enforces privacy at chain *selection*
  (`_maybe_enable_cloud_fallback`) and on toggle (`_toggle_privacy` drops
  `self.llm`); `cloud_llm` itself only *records* `egress.privacy_mode_enabled()`.
  Sound for a chat turn, which is request-scoped. **A microphone stays open for
  minutes** - dictation's ceiling is 300 s against an ordinary turn's 20 - so
  privacy mode can be turned on halfway through and the next chunk would be sent
  by a chain chosen legally before the person changed their mind.
  `_privacy_refusal()` is therefore called *inside* `transcribe()` and
  `synthesize_to_bytes()`, failing closed.
  `test_privacy_turned_on_mid_dictation_stops_the_next_chunk` builds that exact
  sequence, because a gate only ever tested in one state cannot tell "reads the
  flag every time" from "read it once at construction".
- **`egress.MODEL_HOSTS` is a model-*download* allowlist** and `cloud_llm` never
  calls `check_destination`, only `egress.record()`. Reusing it would be wrong in
  both directions, so `cloud_voice` records its own events with `purpose` set to
  `speech-recognition`/`speech-synthesis` and `bytes_out` set to the **real audio
  size** - `payload_size` is meaningless here.

**It displaces nothing local.** Cloud TTS is the *last* link of
`PiperTTS.engine()`'s cascade, after Kokoro, Piper, RHVoice and espeak-ng -
`espeak-ng` is a hard package dependency, so on any working install that branch is
never reached and turning the switch on cannot displace a local voice.
`app/voice.py:_build_stt` only reaches for the cloud when `local.is_available()`
is False, and re-decides per utterance so installing a local model later takes
precedence without anybody turning a switch back off.

**Every refusal case in `tests/test_cloud_voice.py` installs a transport that
raises if reached.** Checking `is_available() is False` proves a flag was read; it
does not prove a request was not made. The three refusals are kept apart because
they are three sentences and only one is actionable: the switch is off, privacy
mode is on *right now*, or nothing configured has the route.

### The capability table was wrong three times before it was right

**The first table was built from an empty POST, reading `400` as "the route
exists".** That is wrong, and each of the three failures looks exactly like a
working provider:

| provider | a real body says | the 400-based guess claimed |
|---|---|---|
| `kilo` | `"This endpoint only accepts the path /chat/completions"` | has a speech route |
| `blockrun` | `"Unknown speech model: tts-1. Available models: elevenlabs/..."` | right answer, wrong reason |
| `openrouter` | `ZodError ... response_format ... values: ["mp3","pcm"]` | returns WAV |

**A status code is not a capability; the server's own sentence about what is wrong
is.** `probe_capabilities()` now sends a real body and a real WAV and returns four
verdicts - `yes` / `no` / `needs-key` / `unreachable` - because "I could not ask"
and "it does not have one" are different answers.

Four findings that shape the design, all from the corrected probe:

- **No cloud speech is anonymous anywhere.** Every provider with a route answers
  401 without a key, Kilo with `"PAID_MODEL_AUTH_REQUIRED": "You need to sign in
  to use speech-to-text."` This matters because the *text* chain is deliberately
  keyless, so "speech follows suit" is a natural and wrong assumption: enabling
  `cloud-stt-enabled` with no key configured enables **nothing**.
- **The free keyless chain (`llm7, kilo, blockrun`) has exactly one speech
  provider** - BlockRun, and it names its models after ElevenLabs. So somebody with
  no API key gets a cloud *brain* and a local *voice*, and a provider whose free
  tier has run out gets silence.
- **OpenRouter cannot return WAV**, so `_to_wav` converts with the `ffmpeg` already
  used by `recordings.py` and **raises** if ffmpeg is absent - never handing back
  MP3 under a name every caller here believes means WAV.
- **BlockRun's speech endpoint bills crypto.** Measured: HTTP 402 with an x402
  challenge - `{"x402Version":2,"accepts":[{"scheme":"exact","network":
  "eip155:8453","amount":"2000"}],"error":"Payment Required","message":"This
  endpoint requires x402 payment","price":{"amount":"0.002000","currency":"USD"}}`.
  0.002 USD in USDC on Base, **per request**. Its *chat* tier is free, which is
  exactly what makes this easy to get wrong, so BlockRun is deliberately **not**
  offered for speech - a switch labelled "Cloud speech synthesis" that quietly
  bills a wallet is worse than not offering the provider. Its ElevenLabs model
  name stays in `TTS_MODELS` so the finding is recorded in the module, and
  `probe_capabilities()` reports `needs-paid` rather than losing it.

**And the corrected classifier caught its own author.** `verdict()` needed a
`needs-paid` state because its first version read BlockRun's 402 as `no` - the
same "a status code is not a capability" mistake this module documents, made one
function above where it was being described. Found by running the live probe
against the table and diffing all nine providers: it disagreed on exactly one,
and the disagreement was in the direction that would have shipped a crypto bill.
The probe and the table now agree on all nine.

**Also fixed while in there:** `_stt_backend_label()` answered from the
*configured* backend, so once `_build_stt` could return a cloud engine the startup
line named `Whisper.cpp` on a machine that never ran it. It now reads the object
(`CloudSTT.label = "Cloud"`) - the same "two log lines a millisecond apart,
disagreeing" failure this file has recorded before, reached from a new direction.

### Tests and mutations

`tests/test_cloud_voice.py` (23), `tests/test_cloud_branch_is_honest.py` (5),
`tests/test_mode_choice_routes_the_flow.py` (2),
`tests/test_welcome_rows_are_targets.py` (14). Mutations run and confirmed to
fail: privacy read once at construction (1 test), switch ignored (2), keyless
providers allowed (2), Kilo put back in the routes (2), MP3 returned as WAV (1),
empty cloud model list restored (1), cloud branch skipping Ears (1, after the gap
above was closed), ears queued anyway when cloud is on (1), honest paragraph
removed (1), welcome rows inert (11), a wrong row tag (1), `Start` renamed (1).

**The probe method was wrong before the code was.** An empty POST to Kilo returns
`400`, and so does a *valid* body to a provider that merely names its models
differently - so the first table agreed with three providers that cannot do what
it said. `tests/test_cloud_voice.py` pins the four-verdict classification
specifically so the mistake cannot come back quietly.

### The egress panel answered "what left?" with a URL and a byte count

`gui/surfaces/privacy.py`'s **Recent network activity** group rendered

    {method} {url} - {bytes_out} bytes

— accurate about bytes and **silent about the only part that matters**. An upload
of a recording therefore read as

    POST https://api.openai.com/v1/audio/transcriptions - 53312 bytes

which looks like a data POST and not like the voice of the person sitting at the
machine. The person opening that panel is asking one question, *what left?*, and
for the newest kind of egress the answer was a URL and a size.

**The byte count is the kind of number that reads as complete.** 53,312 bytes of
anything looks like a request; 53,312 bytes of 16 kHz mono PCM is fourteen
seconds of somebody speaking. The panel cannot tell the difference, because
`payload_size` is a *length* and a length does not say what was measured — only
the `purpose` does. `egress.record` has carried that field since model
downloads, and `cloud_voice` writes `speech-recognition` /
`speech-synthesis`; none of it reached the screen. Now it does, and events with
no purpose render **exactly** as before, because an addition that made old rows
unreadable would be the worse regression.

Deliberately **not** done: deriving seconds from the byte count. That needs a
sample rate and a channel count, and guessing either is the confident wrong
answer this panel exists to avoid. `tests/test_egress_panel_says_why.py`
(3 tests, mutations: purpose dropped 2, prefix applied unconditionally 1,
hardcoded prefix 3).

`gui/browser.py`'s `_record_navigation` had the same gap and now records
`purpose="page-navigation"`, so a page the person opened is legible in the same
panel as everything else. Its two existing properties are pinned alongside, both
because they are easy to break while adding a field: `privacy_mode` is still
**read at the call site** (omitted it computes `violation=False` structurally,
which is how `webtext.retrieve` was uninstrumented while looking instrumented),
and a `file://` page is still **not recorded at all** (an alarm that fires for
local files is an alarm nobody reads).

**Two test-shape mistakes here, both worth recording.** The file's first version
*skipped* when the panel build raised — a skip reads as coverage and is not, so it
now builds through the real `surface.build(app)` with a config stub and has no
skip path. And its "old rows are unchanged" assertion was a **substring** check
(`"GET ... - 12 bytes" in texts`), which a mutation that prefixed every row with
`request: ` still satisfied, because the prefix goes *before* the string being
looked for. That mutation ran green. It now asserts **equality on the egress
row**, selected by host — the panel also renders privacy mode, the switches and
every consent row, so "the rendered text contains X" was never the right shape.

### The Voice panel did not learn about its own fifth engine

Adding the link to `tts.PiperTTS._engine` is not adding it to the panel.
`gui/surfaces/voice.py`'s `_chain()` had its own four engines, so a machine
speaking through a provider would have found no row at or above its own and been
told it was using nothing — the panel-silently-incomplete defect the file's own
docstring records for the Eyes list.

The instructive part is what the tests said while it was broken. Two existing
assertions **passed anyway**:

- `test_a_probe_that_raises_is_unknown_not_absent` proves a failed probe names no
  engine, by asserting the absence of `ENGINE_NAMES` — and `ENGINE_NAMES` listed
  four, so omitting the fifth was invisible to the guard. **An omission in a
  negative-space guard cannot fail.**
- `ENGINE_NAMES` was the same four-tuple in a second place, kept in step by hand.

Both now derive from `surface.CHAIN`, and the state list is asserted as a
*property* ("exactly one engine is present, and it is the floor") rather than as
a literal — because `cloud` sits **after** `espeak-ng` and is False, so
"all False then a True" was never going to be the right shape and I wrote it that
way first.

**And the panel is read-only, which `cloud_voice` had to learn.** Asking the cloud
engine's availability by constructing a `ChronoaConfig()` instantiates a
`Gio.Settings`, which on first use creates `~/.config/glib-2.0` —
`test_building_writes_nothing_to_the_data_home` caught it, correctly. `CloudTTS`
and `CloudSTT` now take an optional `config` and `_read_switch`/`_provider_key`
use it when given. Better dependency direction anyway; the injectable half exists
because a read-only page that creates a settings directory is not read-only.

### The root-side plan validator was a non-executable file

Found by asking "which functions in this tree look like enforcement but have no
caller?" — an AST scan for `may_`/`can_`/`enforce`/`require`/`check_`/`deny`/
`refuse`/`consent`/`validate` names with no usage outside their own definition.

Three came back at zero. **Two were false positives and one was a live
shipped defect**, and the false positives taught the scan its own lesson:

- `sandbox/landlock.py:apply_filesystem_allowlist` — called from line 594, which
  is *inside the source string returned by `get_landlock_wrapper()`*. The
  executor writes that generated program into the workspace and runs it, so the
  caller is generated code, not the tree.
- `netprovision.revalidate` — called by **`usr/bin/shani-chronoa-lab-network`**,
  which my scan had not looked at because it only walked
  `usr/lib/shani-chronoa/`. *Any scan of "who calls this" in this repo has to
  include `usr/bin/`; the scripts there import the package and are the
  privileged half.*
- `cloud_llm.check_health` — genuinely uncalled, and consistently so: the Model
  panel already says *"would answer through … never asked here; availability is
  per-request"*, so nothing anywhere claims a health probe runs. A dead
  convenience method, not a defect.

**The live one.** `usr/bin/shani-chronoa-lab-network` was mode **`100644`** in git
— the only non-executable file in `usr/bin/`, where all five siblings are
`100755`. Measured on the repo copy:

| mode | result |
|---|---|
| `644` | `Permission denied`, exit 126 |
| `755` | runs, and refuses to do anything without root (exit 77) |

`skills/lab_network.py` documents the root path as
`pkexec /usr/bin/shani-chronoa-lab-network apply /path/to/plan.json`, so on any
install built from this commit the lab-network skill's root path **could not be
executed at all**.

That matters more than a dead launcher usually does, because this file is the
**only** enforcement point for a security claim.
`netprovision.revalidate()`'s docstring says a plan file *"can only ever contain
commands this module would have produced from a request it accepts — there is no
path by which a plan file becomes an arbitrary root command"*, and this helper
is what calls `revalidate()`. An unexecutable validator is the same defect as an
uncalled one, pointed the other way: code that reads as a control nothing can
reach.

The existing `test_pkgbuild_installs_every_binary_not_a_hand_kept_list` could
never have caught it — it checks that the **manifest mentions** each launcher,
never its mode. The new test asserts **git's recorded mode** (`git ls-files -s`),
not the working tree's, because the working tree's mode is what a local `chmod`
papers over while a fresh checkout still produces a 644 file. Mutation run: mode
back to `100644` in git → the test fails. It also caught the mode silently
reverting when a `git stash`/`git stash pop` round-tripped the file, which is the
second reason to read the index rather than `stat`.

### The gateway, driven from a second process — and a grant that is only a label

I had verified the gateway in-process. This drove it across **two processes on a
private `dbus-daemon`**, the app exporting and an unrelated client calling:

```
CLIENT Submit(phone,'what is the weather') -> "reply to 'what is the weather'"
CLIENT Submit(laptop,'ping')               -> "reply to 'ping'"
CLIENT Submit(nosuch,'ping')  -> <Refused: no gateway called 'nosuch'; registered: laptop, phone>
CLIENT Submit(phone,'')       -> <Refused: empty message>
CLIENT Submit(phone,'x'*99999)-> <Refused: message is 99999 characters; the limit is 4000>
CLIENT bogus method           -> <UnknownMethod>
```

and the server's submit callable reported the text it actually received —
`'what is the weather', 'ping'` — so the string crossed the bus rather than being
echoed by a stub. Introspection advertises
`dev.shani.chronoa.Gateways.Submit(ss) → (s)` correctly.

**And it exposed a claim that is false.** The second line is a channel registered
with the **default `ask` grant**, and it executed. An AST search confirms why:
`self.grant` is written once in `__init__` and read once, in `may_execute()`,
which **nothing in the tree calls**. So `ask` and `execute` behave identically
today; the grant is what the Settings row displays.

The security argument does not rest on the grant, and that is worth being precise
about: the submitted turn meets the same per-sense consent keys as anything typed
into the window, which is why an inbound message cannot delete a file by itself.
What does not exist is any *behavioural* difference between the two grants.

`Gateway.may_execute`'s docstring said the grant is "the *outer* limit". There is
no outer limit. It now says plainly that it has no caller, that the enforced
limits are `admit()`'s three plus the consent keys, and that a method named
`may_execute` with no caller reads like a security control it is not — which is
the same shape as `Registry.register()` having zero callers, the defect that
kept the whole inbound channel dead. Four tests pin the behaviour (AST search for
callers, both grants submitting, `admit`'s three limits, `describe` still showing
the grant); four mutations confirmed to fail.

### What the log taught, once something showed it — and the "cannot liar" sentence

`learning.lessons()` and `render_lessons()` were fully written with **zero
callers**, so a machine that had logged 14,000 tool calls could not be told what
any of it meant. Wired, and the first thing it showed was nonsense:

```
cannot  liar  failed 323/323 here and has never succeeded on this machine
```

`liar` is not a skill. Neither is `unver`. **712 records** name those two — four
and five characters, always `args: {}`, always in pairs ~80 ms apart, all
`origin: user` — and the count matches `provenance.examples_for_unknown_tools`
exactly, so the training path already knew.

**This is not a new finding about the log.** `unknown_tool_examples()` has
documented those two by name for a while, including that they are "**313 of the
378 failures**" — so a model fitted on this log learns its entire failure signal
from something no longer installed. What was missing is anywhere a *person* would
see it, and the lessons row is the first such place. Worth being explicit,
because I nearly wrote it up as a discovery.

The renderer now names those apart from real findings, so `get_clipboard failed
5/5` keeps its authority and `liar` stops borrowing it.

**My first fix was worse than the bug.** I resolved names with
`skills.discover_skills()`, which returns 152 names that do **not** correspond to
the log's `tool_name` values — so it immediately reclassified `delete_file
succeeded 820 of 915` and `get_clipboard failed 5/5` as "tools this build does
not have". There is one registry and it is `tools.TOOLS`, the same one
`unknown_tool_examples()` uses. There is also a test for exactly that, because
the second attempt is the one that would have stuck.

### Merging, and the two features beside it that nothing could reach

`merge_models` now has a **Merge models** button, and the group explains that the
result is scored before it is written. `_merge_verdict()` reports the numbers
including the argmax, so a merge that detects `failed` at 25× while losing the
argmax says both.

Also removed a **dead duplicate** `outcome_button` assignment that passed its
handler as `_run_async(b, _train_outcome, status)` while every live button used
`_handler(b, status)` — it was overwritten immediately, and had it ever run it
would have raised on the arity.

And the Train group's subtitle repeated the false claim this commit removes: it
said an outcome model that does not beat the majority baseline "is not written".
The test is not that, and the group now says which test it is.

**Four mutations, and the first attempt at one of them was not caught** — the
lessons test called `_lessons_sentence()` directly, so deleting the *row* from
`build()` left it green. A helper can be correct and still be unreachable, which
is the entire subject of this batch, so the row's title is now asserted through
the built widget. Redone: caught.

Learning/distill/surface suites: **149 passed**.

### A merge was marked trustworthy by construction — the worst thing I found today

`merge_models()` wrote `"honest": True` into the payload it signed and **never
evaluated the weights it had just combined**. Every writeup on TIES/DARE/SLERP
says the same thing about merging: the result is a *hypothesis* and you evaluate
it like one. Sign election makes conflicts cancel; nothing guarantees the outcome
beats the models that went into it, and on independently fitted models — which is
what these are — there is no shared base to make it likely. That is precisely why
the module's own docstring rejects addition and plain averaging.

It was not a stylistic shortcut. **`tools._outcome_model()` refuses any model
whose `provenance.honest` is false**, so a merge that was pure noise was stamped
trustworthy *by construction* and then loaded.

Now the merged weights are scored against this machine's own log, the provenance
carries the measured `accuracy`/`baseline`/`beats_baseline`/`detected`/both
lifts, and **a merge that fails the same bar a fitted model must pass is not
written at all**:

```
three noise models -> merged=False
  reason: the merged model is not worth quoting (0.0% against a 91.2% constant,
          and no minority verdict is detected), so it was not written
```

Two genuine merges, measured on the real 16 MB log:

| | accuracy | vs constant | detected |
|---|---|---|---|
| model A (half the log) | 0.857 | 0.913 | `verified` 17.5× / 5.6× |
| model B (the other half) | 0.858 | 0.910 | `verified` 16.8× / 5.6× |
| **TIES merge of A+B** | **0.880** | 0.912 | **`failed` 25.4×** |

So the merge **beat both parents** (0.880 > 0.858) and found a class neither
parent did — and now that is a measurement rather than an assumption. It also
**fails closed**: with nothing to measure against, it refuses rather than writing
an unmeasured model.

**One equivalent mutant, kept and documented.** Replacing
`"honest": bool(report.honest())` with `"honest": True` leaves the file green —
necessarily, because the gate above it only lets a file be written when
`honest()` is already true, so the expression is redundant *given the gate*.
Deleting the gate (mutation 2) **is** caught, which is what proves the gate is
load-bearing rather than decorative.

**A fixture that made a test unfalsifiable, twice.** The first version of the
"the refusal quotes its numbers" test passed against a mutation that hardcoded
`"0.0%"`, because the synthetic held-out data had **no `failed` examples at
all** — so a noise merge predicted the absent class and scored exactly 0.0%,
which is the string the mutation hardcoded. Right answer, unfalsifiable test. Two
fixes: all three verdicts are now present, and the **baseline** is the
load-bearing assertion (0.0% accuracy against a **50.0%** constant), because
accuracy alone on this fixture can coincide with a plausible constant.

### The learning layer: the export dropped the one thing it exists to carry

Found by AST-scanning `learning.py` for public names with **no caller anywhere in
`usr/` or `tests/`** — 15 of 111. Most are a statistics toolbox (`bayes`,
`timeseries`, `clustering`) that is legitimately library surface. Three were not,
and all three are the portability feature.

**`bundle["bandit"]` was always empty, on every machine, ever.**
`export_knowledge()` read the arms with `Bandit().arms()`, and `Bandit.__init__`
does not read the file — a fresh instance has an empty `_arms`. `load_bandit()`
is what loads. Measured, with a bandit holding real history written to disk
(`say` 5/5, `espeak` 5/0), no fitted model and no conversation log:

```
on disk, load_bandit() reads: {'say': (5, 5.0), 'espeak': (5, 0.0)}
a fresh Bandit().arms()    : {}
export_knowledge(...)      -> None
```

So the Export button said *"there is no trained model on this machine yet"* while
the thing it wanted sat on disk. Both halves of the feature's docstring describe
the arms travelling — "the knowledge a fresh machine cannot get any other way" —
and `import_knowledge` promises to adopt them "even when the model is not".
Neither had ever happened. Now `load_bandit()`, and the bundle carries
`{'say': {'pulls': 5, 'wins': 5.0}, 'espeak': {'pulls': 5, 'wins': 0.0}}` with
`import_knowledge` reporting `"arms": 2`.

### …and "Written to `<path>`" on a model that lost to a constant

Training this machine's real 16 MB log (14,094 examples, **24 s** on CPU) gives
`accuracy 0.857` against `baseline 0.912` — *worse than always answering
"unverified"* — and `_train_outcome` reported **"Written to …"** and stopped. A
person reading that concludes the model is good, and then `recommend()` acts on
it.

The trap here is that **two flags answer two different questions** and
conflating them hides the finding either way:

- `beats_baseline` — "does it win the argmax a constant already wins 91% of the
  time". Here **no**.
- `Report.honest()` — "is it worth quoting". It accepts *either* a top-1 win
  *or* a minority verdict genuinely `detected()` at ≥2× its base rate on **both**
  recall and precision. Here **yes**: `verified` at **17.1× recall, 5.6×
  precision**.

So the model is legitimately loaded — `tools._outcome_model()` refuses only on
`honest` being false — and the sentence has to say **both**. `_verdict_sentence()`
does, and `learning.py`'s "saved whether or not it beat the baseline" stays: its
docstring is explicit that the gate exists "to stop the *caller* from relying on
a useless model", and **the caller was the thing failing**.

The surface's own docstring claimed "writes a file only when the fit beat the
majority baseline" — false, and contradicted by `learning.py`'s deliberate
design. `_nothing_to_export_sentence()` and `_verdict_sentence()` now carry the
truth; `_import_sentence()` reports a refusal as a refusal (arms adopted, model
refused, *which was which*).

**The Import button, which the module docstring described and did not have.**
"Export and import are here because the models are portable" — beside an Export
button. `import_knowledge` was fully implemented, digest-checking and refusing,
with **zero callers**. It has a button now, and `_import_sentence()` says what
actually happened rather than "imported".

Six tests, six mutations confirmed to fail. **One of those six needed a second
attempt**: my first mutation used 8 spaces of indentation against a 4-space line,
so `str.replace` was a silent no-op and the suite stayed green — which is the
"a negative control that cannot fail is not a control" trap, hit from a new
direction. Every mutation after that asserts `s.count(old) == 1` before writing.

Related suites: **151 passed, 2 skipped**.

### The grant is now enforced, and the whole thing hinged on a thread

I called wiring it "a feature question, not an audit finding" and left it alone.
Asked for a decision that is secure *and* useful long-term, I took the third
option rather than the second: `ask` now goes through `permissions.decide()`,
the layer that already exists and already fails closed.

- **Reused, not reinvented.** `permissions.decide()` returns `None` for a
  refusal, a dismissal, a timeout and a headless run alike, records the refusal
  so the same question is not put again on a retry, and gives the user "yes,
  this once" and "yes, for this session" for free. The session grant is keyed on
  the **channel**, so answering once for `phone` does not answer for `laptop`.
- **The action is its own** - `submit_from_gateway`, not one of the existing
  actions - so a rule written for an inbound gateway cannot accidentally widen
  something else.
- **Fails closed on every path.** Verified across two processes on a private bus:

  | what happened | `phone` (`ask`) | `desk` (`execute`) | server received |
  |---|---|---|---|
  | approved | submitted | submitted | both |
  | refused | **refused**, switch named | submitted | desk only |
  | dismissed | **refused**, switch named | submitted | desk only |
  | nobody there | **refused**, *"nothing is on screen"* | submitted | desk only |

### …and then the thing I built it on turned out to have a no-op in it

Wiring the grant to `permissions.decide()` means the gateway now inherits
whatever `decide()` actually guarantees — so I measured that rather than reading
it, and **"Allow for this session" asked again on the very next call.**

`decide()` had a short-circuit for an answer already on record, and the set it
checked was only the three **refusals**. A session *grant* was written to
`_grants` and then never read. Measured, with a presenter that always picks the
session option: **three calls, three prompts**, two of them on the same channel:

```
1st='allow_session' 2nd='allow_session' other-channel='allow_session'
times the person was asked: 3   (3 calls, 2 on 'phone')
rules recorded: [('submit_from_gateway', 'phone', 'allow_session'),
                 ('submit_from_gateway', 'phone', 'allow_session'),
                 ('submit_from_gateway', 'laptop', 'allow_session')]
```

So the option was offered, recorded, and did nothing — a label that lies. Same
shape as the grant itself being a label, one level down, and it is **not the
gateway's bug**: `decide()` is shared by every tool, so the fix belongs there.

`ALLOW_SESSION` is now honoured. `ALLOW_ONCE` is deliberately still asked every
time, because "this once" means once — and that contrast is the test that stops
this being over-fixed into "the prompt never appears". Measured after:

| the person picks | prompts for 3 calls | returns |
|---|---|---|
| Allow for this session | **1** | `allow_session` ×3 |
| Allow this once | **3** | `allow_once` ×3 |
| No, don't allow | **1** | `None` ×3 |

**The second defect was in the same three lines of output above**: `_grants` held
the *same rule twice* for two calls on one channel. `add_rule` appended
unconditionally, so a channel with a session grant grew `_grants` by one entry
per approved message for the whole session — and `evaluate()` walks the whole
list on *every* permission check, so the cost is paid by checks that did not
record it. Identical rules are now not re-appended; replacing rather than
appending is a no-op for "last match wins", since an identical triple contributes
the same answer wherever it sits.

Six tests, five mutations confirmed to fail (including the over-fix, and one
where the dedupe reorders instead of skipping). Blast radius: **250 passed**
across every consent, permission, tool-activity, approval and gateway suite; the
only 2 failures are the recorded environmental `grim` ones.

**A harness note, because I made this mistake twice in one afternoon:** calling
`permissions.decide()` from a GLib timeout callback — i.e. on the main loop —
hangs for `DECISION_TIMEOUT_SECONDS`, which is **120**, not the 60 one assumes. My
first probe did exactly that and looked like a permissions bug. Ask from a
worker, which is what the gateway now does.

**Why I rejected "refuse `ask` at the bus"**, which was the smaller change: it is
secure and useless. The default grant would do nothing until somebody edited a
setting, so the feature would ship dark. This way the default works - one click,
then not again for the rest of the session.

**And why that needed a worker thread, which is the part worth remembering.**
A GDBus method handler runs *on* the main loop, and `decide()` blocks on a
`threading.Event` that only the main loop can set. So an approval asked from the
handler **cannot be answered** - it waits out its full timeout every time, which
would have looked exactly like the gate working. Measured with a presenter that
resolves from an idle callback: **3.0 s and no answer on the main loop thread,
0.0 s on a worker.** `export()` now dispatches through `off_the_main_loop()`,
which is a named function so the property can be asserted directly rather than
only observed through a live bus. The rate limit and the submit callable no
longer run on the main loop either, which was blocking the whole UI on a slow
call.

**Four pre-existing tests failed when the gate landed, and all four were right
to fail.** `test_submitted_text_reaches_the_window_entry`, the length limit, the
rate limit and the bus round-trip all submitted through an `ask` channel with
nobody present. They are tests about the bus and the limits, not about approval,
so they now use `execute` - with a note saying why. A gate that changes what
other tests can be about is worth noticing; the fix was not to weaken the gate.

Mutations run and confirmed to fail: the gate removed (5), `None` treated as yes
(2), the headless check removed (1), `execute` channels asked as well (2),
dispatch put back on the calling thread (1), the question no longer showing the
message (1).

**Three harness traps, each of which cost a wrong intermediate conclusion:**

- `Gio.bus_get_sync` resolves `DBUS_SESSION_BUS_ADDRESS` on the **first**
  session-bus connection and caches that connection for the process. Setting it
  after `gi` is imported does nothing — my first server exported on the *real*
  session bus (unique name `:1.10577`) while the client sat on the private one,
  and the client got `ServiceUnknown`.
- `Gio.TestDBus` is constructed as `Gio.TestDBus.new(flags)`, not
  `Gio.TestDBus(flags)` — the latter is `TypeError: GObject.__init__() takes
  exactly 0 arguments`.
- **A bus server with no GLib main loop answers nothing.** My first probe
  `time.sleep`ed instead of running a `MainLoop`, so every `Submit` timed out and
  the handler never ran — which read exactly like a broken export. It was the
  probe. (`Gio.bus_get_sync` was called before the loop existed, which is fine;
  dispatch needs the loop, not the connection.)

### The cloud-speech privacy story, verified by running it end to end

`cloud_voice` has a `_transport` hook, so the whole path can be driven for real
with `httpx.MockTransport` — real `_post`, real `_record`, real httpx, only the
wire faked. Measured with a hand-built 16 kHz mono WAV (32044 B) and a real
provider-shaped reply:

| | privacy off | privacy on |
|---|---|---|
| `CloudTTS.synthesize_to_bytes` | 32044 B of real RIFF WAV back | `CloudVoiceRefused` |
| `CloudSTT.transcribe` | the provider's text back | `CloudVoiceRefused` |
| ledger rows added | 2, both with a `purpose` | **0** |
| wire hits | 2 | **0** |

The two directions record **different** things, correctly:

```
purpose='speech-synthesis'   component='cloud_tts:openai'  bytes_out=11
purpose='speech-recognition' component='cloud_stt:openai'  bytes_out=32044
```

Synthesis records the 11 bytes of **reply text**, because that is what left;
recognition records the 32044 bytes of **audio**, because that is what left.
Answering "how much of my voice left this machine" means reading `bytes_out` on
a `speech-recognition` row — which is exactly what the egress panel's `purpose`
column now makes possible.

**A harness trap worth knowing, because it nearly had me reading a real user's
data.** `egress.EGRESS_LOG` is a module-level constant built from `~/.local/share`
and is **not** where a redirected run writes. `record()` resolves the path per
call through `egress._egress_log()`, which honours `XDG_DATA_HOME` and gives a
pinned `EGRESS_LOG` priority. So a probe that sets `XDG_DATA_HOME` and then reads
`egress.EGRESS_LOG` silently reads the *real* `~/.local/share/shani-chronoa/…`
ledger — 1256 lines of this machine's actual history, which is what it printed
before I noticed. Read `egress._egress_log()` in a probe, and set
`XDG_DATA_HOME` as well as `XDG_STATE_HOME`; `egress` does not look at
`XDG_STATE_HOME` at all.

### A hardcoded cascade count that went stale the same day I made it go stale

`_speech_out()` resolved through the **real** `PiperTTS.engine()` — correct — and
then described it in a hand-typed sentence:

```
speech out would use cloud, resolved from PiperTTS.engine()'s own four-way chain
```

`gui/surfaces/voice.py` gained a **fifth** link (cloud) and this row went on
saying "four-way" while resolving it through the five-link chain. A count that
can be wrong on the same day a feature lands is not worth writing twice, so the
count and the list are now **read from `voice.CHAIN`** — the one place the order
is written down.

Two more false claims in the same row, both measured:

- **When cloud wins, nothing said the text left the machine.** `tts._announce`
  puts it in the log ("the reply text was sent off this machine") and the
  Speech-in row puts it on the panel; this row said "would use cloud" and stopped
  — the least useful sentence in a panel whose job is *what is wrong here*.
- **When nothing worked, the message listed only the four it knew about**, so on
  a machine relying on the cloud, the reason it was not working — no provider
  with a speech route — was invisible.

Mutations run and confirmed to fail: "four-way" hardcoded again (1), the cloud
disclosure removed (1), the nothing-works list back to four (1), and
`_tts_chain` returning a hardcoded tuple instead of reading `CHAIN` (3 — the one
that proves the number is derived rather than retyped).

### A third instance, with the loudest alarm: Diagnostics said speech input was broken

`gui/surfaces/diagnostics.py`'s `_speech_in()` constructed a `WhisperSTT`
**directly**, so it could not see a `CloudSTT` even when that was the engine
transcribing every utterance. On a machine with cloud recognition on, a key
configured and no local model it returned

> neither the whisper.cpp binary (…) nor a model file (…) is on this machine, so
> speech input is off whatever the settings say

— a **warning**, on a machine transcribing perfectly well. The row is titled
"Speech in (whisper.cpp)", so the alarm was at least honestly titled; it was still
an alarm about nothing.

The row is now "what can transcribe here", and the local half is reported
**alongside** rather than suppressed — a person who wants whisper.cpp locally is
still told exactly what is missing. `STATUS_WORKING` with the cloud named first,
then `"Locally: …"`.

**Zero-argument probes, so this asks the engines directly rather than reading
`app.stt`.** `Probe = Callable[[], Tuple[str, str]]` — no app, by design, so the
panel stays usable from a script. That is the same availability question either
way. `_cloud_speech_in()` is total: any exception is `''` — "not cloud" — so a
broken probe can only ever *under*-report, never invent a working engine.

Mutations run and confirmed to fail: the cloud half ignored (the original
defect), a raising probe claiming `working`, the local truth dropped along with
the alarm (1 each).

### Two engine rows could both claim to be the one answering

`gui/surfaces/model.py`'s llama.cpp row rendered
`f"{HOST}:{PORT} is answering with {active}"` off `local_llm.is_up()` — a claim
about the **app** made from a probe of the **server**.

Those genuinely diverge, because the LLM is chosen **once, at startup**:
`_maybe_enable_cloud_fallback` returns early on
`isinstance(self.llm, CloudLLMChain)` and never reconsiders. So a machine that
started without llama-server and started it afterwards has an *available* local
engine and a *selected* cloud one. Measured in exactly that state: the local row
said **"127.0.0.1:8765 is answering with qwen3-1.7b"** while `app.llm` held a
`CloudLLMChain`, and the cloud row said it "would answer". Two rows, one
asserting something false.

The rows stay availability-scoped ("is up and X is ready to load"), and
`_selected_engine(app)` names the choice **from `app.llm` itself** — because
re-deriving the condition is exactly how the two came to disagree. It also says
*why* they can differ: "chosen at startup, and not reconsidered since".

**This is the opposite of the Voice panel, deliberately.** There,
`PiperTTS.engine()` is asked per reply, so the running object and the selected
engine coincide and resolving through `app.tts` is right. Here the LLM is
selected once. Copying either approach to the other panel would be wrong, and
that is the note worth leaving.

Mutations run and confirmed to fail: "is answering" restored (1), selection
derived from config rather than the object (2), `isinstance` check dropped (1,
**only after the assertion was tightened**), the "chosen at startup" caveat
removed (1).

That last one is the interesting one. `assert "cloud" in chosen.lower()` **also
matches "Answers through CloudLLMChain"** — the class name contains the word — so
a mutation that deleted the `isinstance` check entirely ran green. The assertion
is on the phrase *"cloud provider"* and on the class name *not* appearing.

### And the *input* side had the same defect, which fixing the output side did not

I fixed the Voice panel's engine rows and stopped. The speech-**input** half of
the same panel was wrong in the same way, and by the same mechanism:

- **`_stt_for`'s guard was whisper-shaped.** It read
  `hasattr(live, "model_path") and callable(live.is_available)` — which
  identifies a speech engine by a field only the two *local* backends happen to
  have. `CloudSTT` has `is_available` and `transcribe` and no `model_path`, so
  the guard rejected the **running** object and the panel built a local
  `WhisperSTT` instead. Measured: the row read **"Whisper.cpp (whisper-cli)"**
  and *"the whisper.cpp model ggml-base.bin is not downloaded"* on a machine
  that was transcribing in the cloud and had no whisper-cpp at all. The guard's
  *intent* — "the panel must not describe a different object" — is right; the
  implementation identified the object by the wrong thing.
  `is_available` plus `transcribe` is the contract `stt.build_stt` says its own
  two backends share, so that is what it asks for now.
- **`senses/hearing.stt_problem` raised on it.** It read
  `engine_stt.whisper_path` and `.model_path` directly, so any engine without
  those attributes raised `AttributeError`; the caller caught it and reported
  `unknown - AttributeError: 'CloudSTT' object has no attribute 'whisper_path'`.
  Honest, and useless — it points at whisper-cpp on a machine that does not use
  it. An engine with neither attribute is now asked **itself**, which is what
  `CloudSTT.refusal()` is for: an engine that can explain its own absence beats
  any explanation assembled from attributes it happens not to have.

`CloudSTT.label` is **"Cloud provider"**, read verbatim by both
`app/voice.py:_stt_backend_label()` and the panel, so the log line and the row
say the same thing.

Mutations run and confirmed to fail: the guard back to `hasattr(model_path)` (3),
the label back to the configured backend (1), `stt_problem` reading
`whisper_path` again (3), `stt_problem` refusing to ask the engine (2).

**Every other `hasattr` guard in the tree was checked and is deliberate**, so
nobody re-audits them: 19 name a specific attribute, and the only one that
identified a *pluggable implementation* by an implementation detail was the guard
above. The rest are legitimate — `settings_window/voice.py`'s
`hasattr(live, "use_config")` guards a *call* on the single class that has one;
`gui/surfaces/models.py`'s `hasattr(voice, "label")` exists precisely because
`KOKORO_VOICES` holds `KokoroVoice` objects on one path and strings on another;
`midi.py`'s `hasattr(..., "read_bytes")` is the documented fix for `_Bytes`;
the `SIGALRM`/`setitimer`/`flatten`/`get_children` ones are GTK- and
platform-version probes.

**One test-hygiene note, because I wrote it badly first.** The test that
exercises `stt_problem`'s refusal assigns `cloud_voice._read_switch` directly and
restores it in a `finally`. If the assertion fails before the restore, the
module stays globally patched for every later test in the file — a failure
corrupting the rest of the run. It uses `monkeypatch` now.

`COST["cloud"]` claims **no real-time factor**, and says why: the cost is not
time but that the reply text leaves the machine, and quoting a latency would make
the row read as a neutral alternative to Kokoro.

Mutations run and confirmed to fail: `cloud` dropped from the panel chain (2), the
disclosure removed from `COST` (1), the panel building its own config again (1).

## The inbound gateway: two faults in one function, and no test file at all (2026-10-06)

`gateway.py` is the channel AGENTS.md's "Channels and the jail" section cites as
its security argument - one D-Bus method, `Submit(gateway, text)`, whose text
goes through the window's own `_submit` so it meets the same whitelist, consent
keys and post-conditions. That argument was written about a module **with no
`tests/test_gateway*.py` anywhere in the tree**, and whose one activation
function had never once succeeded.

### The three findings

**Nothing could turn it on.** `_export_gateways()` runs from `do_activate` and
returns early because `Registry.names()` is empty - because, measured with an
AST search over every call whose receiver mentions a gateway, **nothing in the
tree ever called `Registry.register()`**. No config key, no CLI flag, no Settings
row. A complete, tested-by-nobody inbound channel with no switch.

**`export()` threw on every call.** It passed the `_Service` object to
`register_object`, which needs a *closure*; a plain object with methods is not
one, and the type check rejects it. Measured:

    could not export the gateway interface: Must be callable, not _Service

The `except Exception` around it turned a line that had never once worked into a
log message reading "could not", which reads as transient.

**`BUS_NAME` was the application's own name.** It was `dev.shani.chronoa`, which
is `ChronoaApplication`'s `application_id`, so `export()` requested a name the
`GApplication` already held, with `REPLACE | ALLOW_REPLACEMENT`. Two owners on
one well-known name: the bus arbitrates, ownership ping-pongs, and a client gets
`ServiceUnknown`. Now `dev.shani.chronoa.Gateways`, matching the interface name
the introspection XML already declared.

**The second fault was invisible *because* of the first** - the name was never
requested, because `register_object` threw first. "Fixed the error" and "the
feature works" are different claims and only running it settles which one you
have. Verified end to end afterwards: export, own the name, `Submit` over a real
private bus, and the text arrives at the window's entry point, with every refusal
specific (`no gateway called 'signal'; registered: telegram, whatsapp`,
`message is 5000 characters; the limit is 4000`, `empty message`).

### Three ways this took longer than it should have, all recorded because all three will recur

- **`Gio.bus_get_sync` caches one session-bus connection per process**, resolved
  from `DBUS_SESSION_BUS_ADDRESS` at first call. Spawning a private
  `dbus-daemon` and setting that variable *inside* a script that has already
  imported `gi` does nothing. Measured: the service owned `:1.7503` on the real
  bus and the client sat on `:1.0`, and every call returned `ServiceUnknown`
  while the log said `gateway bus name acquired`. `export(..., connection=)` now
  exists so a test can hand it a bus, and `tests/test_gateway.py` uses
  `Gio.TestDBus` - which also needs `up()` before `get_bus_address()` returns
  anything but `None`.
- **`register_object` refuses a second export of the same interface at the same
  path on one connection**, and `bus_unown_name` releases the name without
  unregistering the object. So the bus fixture is function-scoped: a shared
  connection would let the first export decide every later one's outcome.
- **A registry captures `self._submit_gateway_text` when it is built**, so
  replacing that attribute on the instance afterwards is never read - and the
  resulting "the text did not arrive" looks exactly like a product bug.

### And a third fault, which only exists *because* the first two are fixed

Editing a channel name reloads rather than restarting, so `export()` runs
repeatedly on one connection. **`register_object` refuses a second export of the
same interface at the same path** (`g-io-error-quark: An object is already
exported for the interface dev.shani.chronoa.Gateways at
/dev/shani/chronoa/Gateways`), and `bus_unown_name` frees the *name* while
leaving the *object* registered.

Measured on the app's own reload path before the fix: **reload 1 owned the name;
reloads 2, 3 and 4 all reported `owner=False, on_bus=False`** while the registry
still listed the channel - so the Settings row would have read "on the bus as
whatsapp" while nothing was listening. A feature that works once and stops
working the first time you edit it.

The fix needed the **registration id**, and that is the trap: GLib's C function
is `g_dbus_connection_unregister_object(connection, object_path)`, but PyGObject
introspects it as `unregister_object(registration_id: int)` - measured by passing
the path and getting `TypeError: Must be number, not str`. Reading the C header
and writing the Python is how that happened, and it is the same class of mistake
as assuming a 400 means a route exists: **the signature you remember is not the
one you are calling.** `test_reloading_keeps_the_channel_reachable` asserts the
reload works rather than that a particular unregister call is spelled correctly,
so it survives the next re-spelling.

**This is also why the round-trip test's bus fixture is function-scoped** - one
root cause seen from two directions. And it was found by running the reload path
I had just written, immediately after fixing a test fixture that failed for the
identical reason: I had witnessed the constraint and then written code that
violated it.

### One equivalent mutant, kept rather than hidden

Deleting the `if handler is None` guard inside `dispatch` leaves the file **fully
green**: GDBus refuses any method absent from the interface info before the
closure runs, so that branch is unreachable over D-Bus. The guard is kept (it is
the right thing to have if the XML ever grows a method with no handler) and the
test now says so, including that GDBus's own wording is `No such method
"RunAnything"` - accurate, and it does not point at the one method that exists.

### Now reachable

`gateways` in the schema, parsed by `gateway.parse_config` into `(entries,
errors)` - **errors are returned, never raised and never dropped**, because a
channel name containing `/` or `.` cannot work (it becomes part of a D-Bus method
name) and a setting that silently ignores what it cannot parse is a switch that
appears set and does nothing. Format: `name` or `name:execute`, comma-separated.
An invalid entry is logged per-entry, the good ones still register, and the
Settings row reports both.

Settings → Privacy gained **"Inbound channels"** and a live **"Listening for"**
row that names what is on the bus and what was ignored. Changes reload live
(`_reload_gateways`, which releases the previous owner before taking a new one,
so a reload does not leave two owners of one name).

`tests/test_gateway.py`, 32 tests, mutation-checked: object-instead-of-closure (2
fail), `BUS_NAME` back to the application's (1), rate limit removed (1), length
limit removed (2), bad entry silently dropped (3).

**Also corrected here:** AGENTS.md said `register_agent`/`broadcast_message`
"remain dead". They no longer exist - removed in the 2026-10-02 structure review,
per `child_supervisor.py`'s own docstring. What is left of that module *is* live,
imported by `audio.py:42` to supervise the `pw-record`/`pw-play` children. And a
naming collision worth knowing: **"Kilo Gateway API key" in Settings is an LLM
provider**, unrelated to this module; "gateway" means two unrelated things in
this codebase.

## Channels and the jail: the two review points, audited rather than asserted (2026-10-05)

From the same thread: *"you can connect other channels like whatsapp etc to give
commands straight to your pc... so best to sandbox/jail the app fully."*

**Channels are not a WhatsApp integration.** `gateway.py` is the part that has to
exist before any of them can be written safely, and its shape is the argument:

- **The session bus, not a socket.** No port, no listener, no network. Whatever
  carries a message to it later is a separate component with its own credentials,
  and Chronoa never sees a token.
- **One method: `Submit(gateway, text)`.** A gateway cannot ask Chronoa to run a
  skill, name a tool, read a file, or reach the model server. It hands over words
  and gets words back.
- **The text goes through the window's own `_submit`**, so it meets the same
  whitelist, the same consent keys, the same post-conditions and the same log. A
  gateway has no way to lower a gate because it never touches one - which is the
  property that matters, since a second path into the assistant makes every gate
  the window respects optional for whoever finds it.
- **Ask-only by default.** Executing anything needs an explicit per-gateway
  grant, *and* still goes through `tools.execute_tool_outcome`.
- **Bounded**: 4,000 characters, 20 messages a minute, no attachments. An
  unbounded pipe into an assistant that can run tools is a denial-of-service with
  extra steps.
- **Off unless asked for.** No registered gateway, nothing exported - so a
  machine that has not opted in has no inbound interface at all.

### The jail, measured

`tests/test_exposure.py` audits the doors rather than asserting a slogan, because
"we are local-first" is a claim about configuration and configuration drifts:

| door | measured |
|------|----------|
| model servers | `local_llm`, `stt_server`, `model_service` all bind `127.0.0.1`; ports 8765/8766/8767-8769 |
| the MCP server | stdio only - no `HTTPServer`, no socket, no `AF_INET` |
| exported bus names | exactly one before this change: `dev.shani.chronoa.SearchProvider` |
| the gateway | one method, and it reaches no tool - checked from the **AST**, not by grepping |

Two failures of my own checks while writing it, both the same shape as the
animation one: a grep that hit the module's own **docstring** (which explains, in
prose, that a submission still goes through `tools.execute_tool_outcome` - the
sentence *is* the argument), and a regex for `^HOST = "127.0.0.1"` that silently
matched nothing because the line is `HOST, PORT = "127.0.0.1", 8766`.

## Dictation: what the review asked for, and what actually blocked it (2026-10-05)

From the GitHub issue: *"add features like transcription of Google Meet etc
could be very useful as it will be able to listen locally on dev/snd directly."*

**Chronoa had every part of it and could not do it.** `audio.py` already shells to
`pw-record`, whisper.cpp was already there, and the turn pipeline was already
there. The blocker was one number: **an ordinary spoken turn is capped at twenty
seconds**, which is right for "what time is it" and wrong for talking *at* the
machine - a paragraph is not twenty seconds, and 1.2 seconds of silence cuts at
every pause *inside* a paragraph rather than at its end.

So it is two ceilings chosen when the turn starts, not one compromise:

| | ordinary turn | dictation |
|---|---|---|
| ceiling | 20s | **300s** |
| silence to finish | 1.2s | **2.5s** |
| started by | button or wake word | **<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+D** |

`app.dictate()` reuses the same capture, transcription and turn, so there is no
second audio path to keep honest, and it is stopped the same ways - the stop
button, Esc, or the silence.

**And it says when it was cut.** `audio.py` now records *why* it stopped
(`silence` / `limit` / `cancelled`) because a recording that ended because the
person finished and one that ended because the clock ran out are different facts,
and only the second is a transcript that stops mid-sentence. A transcript that
silently stops reads as "that is everything I said", which is the one thing a
transcript must never imply. The first version of this had a
`_dictation_note()` that claimed to report a cut and could never report one,
because nothing set the flag.

Two bugs of mine, both caught by writing the test before the run:

- **`self._stop_listening()` does not exist.** The second press on dictation
  would have raised `AttributeError` - at exactly the moment dictation most needs
  to work. It now goes through `_toggle_listening`, the microphone button's own
  handler, so there is one implementation of "stop listening".
- **Restoring `1.2` overrode the person's setting.** `_start_listening` reads
  `_listen_silence_seconds` *or the configured `end-of-speech-pause`*, so putting
  the constant back meant one dictation silently overrode the user's own
  microphone timing for every later turn. It restores `None`, which means "use
  the setting".

`tests/test_dictation.py`, 9 tests, drives the product's own methods rather than
stand-ins.

## What the assistant was actually learning from: two fixtures (2026-10-05)

Built a per-skill verdict summary in the Tool activity panel - worst failure rate
first, every rate carrying the count it is a proportion of - and it found the
most important thing in the log on its first run.

**`liar` failed every one of its 313 calls and never verified once.** It returns
`"Created the file."` 343 times while its post-condition records *"the file was
never created"*. There is no `skills/liar.py` in this tree: it is a fixture that
did exactly what it was built to do.

**`liar` and `unver` account for 692 of 12,856 logged calls and 313 of the 378
failures.** So this machine's entire failure signal comes from tools that are no
longer installed, and a model fitted on that log reports a number about a machine
that does not exist any more - unless it says so. Two changes, both about
*showing* rather than deciding:

- The panel's row ends **"not installed any more, so this is history, not a
  fault"**, because "failed 313 of 313" reads as *broken* and only the registry
  can tell you that it was a fixture.
- `train_and_save`'s provenance records **`examples_for_unknown_tools`** and
  which tools. **Recorded, not filtered**: filtering would be the more helpful
  behaviour and the wrong one, because it changes what the model says about the
  past without saying it did. Whether to train on it is a decision for a person
  holding the figure.

This also explains the outcome model's failure blind spot, which I had put down
to a weak feature space. Measured again on the same log: `liar` fails 313/344 and
`delete_file` 60/2,176. The signal was never weak - it was *concentrated in a
fixture*, and the feature view collapsed it into seven vectors.

`unverified` calls are excluded from the per-skill rates on purpose: they are
most calls, and counting them as successes would turn every rate into a statement
about post-condition coverage rather than about the skill.

## Auditing every surface for scroll, wrap, overflow and motion (2026-10-05)

`tests/test_ui_layout_contract.py` is an audit turned into a contract, over all
twenty surfaces. Measuring found two real defects that reading had not.

**One surface could not scroll.** `inventory` passed its column straight into
the toolbar's content slot, so a window shorter than its twenty-odd rows could
not reach the bottom of them - and it is a list of every function and its state,
so the last few are exactly the ones nobody can get to. Nineteen surfaces had a
`Gtk.ScrolledWindow`; that one had none, and nobody had noticed, because a panel
that is *mostly* visible looks fine in a screenshot taken at full height.

**Every wizard page was too wide, and there was a horizontal scrollbar on all of
them.** The bar in the bottom of every wizard screenshot - visible since the
first slot run and unexplained until now. It belongs to
`Adw.NavigationPage`'s *own* scroller, whose horizontal policy is the default, and
it appears when the page content is wider than the page. The cause:

> **`Gtk.Label.set_wrap(True)` does not bound a label.** It wraps the text *at
> whatever width it is given* and reports its natural width as the full
> unwrapped run. Measured: the wizard's welcome description asked for **947px in
> a 560px window**; `set_max_width_chars(56)` brings the same label to **437px**.

So `common.wrap_label()` and the wizard's `wrapping()` cap every wrapping label
they create, and `tests/test_ui_layout_contract.py` fails if any is left
uncapped. Thirteen of sixteen wizard pages now have no node wider than the window
at all.

**And the wizard is now clean too — all sixteen pages.** After the label caps,
three pages still asked for more than the window (774, 956, 983px). The cause was
**libadwaita 1.5 does not wrap its own text**, measured on the installed library:

| widget | a 130-character string measures |
|--------|----------------------------------|
| `Adw.ActionRow` subtitle | **1,227px** |
| `Adw.PreferencesGroup` description | **1,428px** |
| a capped `wrapping()` label | 437px |

So every piece of prose in the wizard now lives in a capped `wrapping()` label or
a tooltip, and the libadwaita slots carry at most a few words. **Worst demand
across all sixteen pages: 538px, in a 560px window.**

Three measurement mistakes of my own, all recorded because each produced a
confident wrong answer:

- **Reading allocation as demand.** A widget allocated 900px reports 900px. The
  number that decides whether a page needs a sideways scrollbar is what its
  *content asks for*, measured on the child of the page's own scroller - not on
  the page. This was chased in circles for a while because the page-level numbers
  never moved when the text got shorter.
- **`propagate_natural_width(False)` does nothing here.** Measured: a scrolled
  window with a 900px child still reports 900. It is left out of the code rather
  than left in under a comment claiming it works.
- **The block regex counted prose as selectors**, so the first animation test
  reported eleven uncovered animations and every one was a sentence.

**Two things that looked like the fix and were not, both measured rather than
assumed:**

- `Gtk.ScrolledWindow.set_propagate_natural_width(False)` **does not reduce a
  scrolled window's natural width** on this GTK: a 900px child still measures
  900 with it off. It is left out of the code rather than left in under a comment
  claiming it works.
- My first version of the animation test reported eleven uncovered animations
  and **every one was a sentence** - the block regex was capturing the sheet's
  prose comments as selectors. Strip `/* ... */` before parsing CSS.

**What the contract asserts**, each with a failure mode behind it: every surface
has a scroller; none needs more than **480px** (the widest measured came to 256px
before the fix, and the 560px wizard looked clipped because the harness has no
window manager - a clipping that was the environment, not the layout); a label
over 30 characters either wraps or ellipsises; and **every selector with an
`animation` has a `.reduce-motion` rule**, because the desktop's accessibility
setting is only honoured if something reads it. The sheet has three animations -
the orb's listening and queued pulses, the suggestion-bar reveal, the transcript
turn-in - and all three are stoppable.

## Every page has an id, and the things that had no UI at all (2026-10-04)

`pages.py` is the registry; `shani-chronoa --show-page=settings:privacy` and
`app.show-page('setup:review')` reach any page of any window, and a retired id
resolves to whatever absorbed it. This is shani-cassini's shape (`notebook.py`'s
`PAGES`/`page_ids()`/`select(pid)`, `--section=`, a `show-section` action),
generalised to three windows.

It exists because **every page was reachable only by holding the mouse.** The
settings sections by typing in a search box; the wizard steps by pressing Next;
the main window by whatever happened to be open. So a notification could not say
"open Settings on Privacy", a keybinding could not, and a test could not - it had
to guess at pixel selectors, and the guessing is expensive:

- `entry:Search settings` never matched, because a `Gtk.SearchEntry`'s role is
  **`search-box`**, not `entry`. The typing went to no widget.
- Four screenshots of four "different" settings sections came out
  **byte-identical** (1024000 px, 0 different) while every step reported green.
- Every wizard page's forward button was called "Next", so with a stack of
  visited-but-alive pages behind it the tree lists several identical "Next" and a
  script clicking "the first Next" lands on the wrong page's. They now read
  **`Next: Ears`**, which is also the answer to "what am I agreeing to".
- A fixed `sleep=25` after Download meant the click on "Start using Chronoa"
  landed before the page was drawn, the wizard stayed up, and the "main window"
  screenshot was the wizard.

**Three bugs in that class were only findable by running it:**

- `setup_wizard.build_window` **raised** on `Gtk.Box.connect("shown", ...)` -
  only an `Adw.NavigationPage` emits `shown`. So `_open_setup` died, the app
  carried on, and "the setup wizard is not shown" was the whole symptom. Three
  runs of screenshots showed the main window every time.
- The review page's Download handler took no argument, so `clicked`'s button
  raised `TypeError` on the only press that matters - the one that would have
  started the download.
- **My own measurement was the broken thing once.** `get_accessible_property` does
  not exist in this PyGObject, so a local probe read every accessible name as
  `""` and I nearly reported that the settings window was unnamed. The harness's
  AT-SPI tree says otherwise. A probe that cannot fail is not a probe.

### What had no UI at all, and now does

`gui/surfaces/learning.py` - **"What it has learned"** - is the surface for four
pieces of machinery that had no entry point in the app at all:
`learning.train_and_save()`, `distill.harvest_rows()`,
`learning.export_knowledge()` and `distill.train_router()`. Five read rows (facts,
bandit arms, routing pairs, a distilled router, the outcome model) and three
actions (train outcome, train router, export), each off the main loop because
every fit reads the whole log.

It **leads with the detection, not the accuracy**, because on a 94%-majority log
83% is what not learning scores too; and it checks a model's `honest` flag before
quoting any number from that model's own report.

### The extras got their own pages, and the hub went

`generate_image` and `photos` were filed under **Files**, and `describe_screen`,
`identify_sound`, `diarize_speakers`, `search_conversations` and `remember_fact`
were **not mapped to any page at all** - so a 2.0 GB picture model was reachable
only as "Files > make a picture", and three engines had nowhere to live. They now
have Eyes, Imagine, Photos and video, Sounds and recordings, and Languages, in
the same words the setup wizard uses.

**Memory was nearly a false claim.** A first pass wrote that `local_embed` was
"installed by the wizard and read by nothing at all", from grepping for the
module name and not finding a caller. It is read - `conversation_store.search()`
embeds the query and the stored turns through it, and the `conversations` skill
exposes it as `action: search`. **"grep found no caller" is not "nothing calls
it"**: the caller was two indirection away, in a module named after the *store*
rather than the *embedding*, behind a skill whose tool name shares no word with
either. This is the same shape as the `_outcome_model` mis-addressing above, and
it is why the capability pages here were assigned from what each engine is
actually imported by.

The setup wizard has no "Optional extras" hub either: it existed only to list the
other pages, and the review page now carries one row per extra.

And the **welcome page lists all nine things individually, each with its real
size** - Eyes 546 MB, Imagine 2.0 GB, Memory 146 MB, Photos 88 MB, Sounds 29 MB,
Who said what 37 MB, Languages 432 MB, plus what Brain/Ears/Voice are doing. It
used to say Chronoa "needs three things", list those three, and collapse the six
extras into one row reading "eyes, pictures, memory and languages - after the
voice" - so the first screen a new person saw named three of nine, and the only
mention of a 2.0 GB picture model was inside a comma-separated string with three
100 MB ones. Nine rows is four more lines, and each one can be clicked into,
which the lumped row could not be because it was not a target.

**Those nine rows were not clickable, and this file said they were - FIXED
2026-10-06.** Walking the real welcome page for anything pressable gave exactly
one answer: `Start`. All nine were bare `Adw.ActionRow(title=..., subtitle=...)`
with no `activatable`, no suffix control, and nothing on `activated`, two lines
under a comment claiming they were targets - and that claim is also the *reason*
given for splitting the lumped row, so the rationale rested on a capability the
rows did not have. The module already knew the device (`choice_group` passes
`activatable_widget` to every row it builds); it had been left off here. Each row
is now activatable, carries a chevron and a tooltip naming its page, and is
verified by **pressing it** - one fresh wizard per row, because `goto()`
truncates its own history and a reused window made the first version of that
test report `Brain -> voice` and `Ears -> imagine`, which looks like a routing
bug and is a probe sharing state (`tests/test_welcome_rows_are_targets.py`).

### And three log lines that were confident and wrong

- `Whisper.cpp not available - STT disabled` - logged on a machine that had just
  had whisper-cpp installed, because `is_available()` needs the binary *and* the
  model and the model is a download. It now names what is missing.
- `Initialized: ... stt=Whisper.cpp (medium)` on the same run, one line after
  saying STT was disabled - it interpolated the backend's *label* rather than
  whether it could listen.
- `LLM unavailable (no Ollama, ...)` - on a machine where Ollama was never the
  default and llama.cpp is. It now says which of the three backends is missing
  what, which is the only reason that line exists.

The same shape was in the UI: the state line read **"Ready"** on a machine with
no model, while the line under it read "LLM unavailable". `set_can_answer()` now
overrides it, because "Ready" is the idle label and so is true by construction on
every machine.

## Six functions that were alive in one copy of the tree and dead in the other (2026-10-04)

`weights_from_records`, `weights_from_tracker`, `reorder`, `organ_status`,
`reliability`, `weight` existed only under `pkg/shani-chronoa/...`, while
`usr/lib/shani-chronoa/...` - the canonical package the tests import and the
overlay installs - did not have them. `tool_select` still called them, inside
`try/except`, so `learned_weights()` returned `{}` on **every** request and
`reorder()` was never reached: the learned half of selection was dead and silent.
16 tests in `tests/test_sleep_and_learning.py` were red about it.

Restored, with the boundary named. **A guarded import of a function that does not
exist is the absence-shaped green this workspace keeps being bitten by**, and the
only test that could have caught it asserted the *effect*
(`tool_selection_applies_what_was_learned`), not the import.

`test_it_runs_nothing_on_its_own` was itself a false positive: it scanned the
module text for `threading`, `asyncio`, `while True` and `sleep(`, which cannot
tell an import from a paragraph - and `learning.py` is 4,000 lines of prose about
why threading is the wrong shape here, so it went red on its own docstring. It
parses the AST now, and carries a control module that really does import
`threading` so the check is known to be able to fail. Same class as pinning an
exact command line: assert on the shape of an interface, not its text.

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

- **Four surfaces disagreed about which consent key opens the microphone. FIXED
  2026-10-05.** The `heard-sound` sense gated on `heard-sound-sense-enabled`;
  its own schema description told the model it needed `sound-sense-enabled`; the
  `listen` skill in `skills/recording.py` gated on `sound-sense-enabled`; and
  Settings → Privacy had a row for `sound-sense-enabled` and none for
  `heard-sound-sense-enabled`. The sense was therefore **unreachable by a user** —
  the only switch that granted it was not on screen. Verified by execution after
  the fix (with the recorder stubbed to raise, so no audio is ever captured): one
  switch, both paths.

  Two keys for two things is correct and worth keeping: `heard-sound-sense-enabled`
  is *ask and it listens once*, `sound-sense-enabled` is *an automatic rule may
  listen for a doorbell*. A user who allows the second has not agreed to the
  first, and one key cannot express that difference.

- **Arm-time source validation matched English, so malformed sources armed.**
  `manage_triggers` decided a source was unreadable by regex-matching the
  openings of the readers' refusal messages, which only covered the three
  readers whose wording happened to match. Five verified cases (`sound:12345`,
  `sound:doorbell:1.5`, `powerstate:battery-below:0`, `…:abc`,
  `journalmatch:unit:bad unit:x`) armed cleanly and then reported
  `SIGNAL_UNAVAILABLE` on every poll forever, with `list` still showing them
  enabled. Now the reader's **status** is consulted — a contract rather than
  English — behind a small fixed list of phrases that distinguishes "your source
  string is malformed" from "the thing it names does not exist yet", because
  arming `git` on a repository you have not cloned yet is legitimate and must
  keep working. All five now refuse; all legitimate cases still arm.


- **Three skills were advertised to the model and to every MCP client, and
  failed on every call. FIXED 2026-10-05.** `calendar_month`, `date_math` and
  `stopwatch` were declared `run=lambda a: _run(a)`. A lambda passes
  `callable()`, so the loader accepted it — but a non-local skill is executed in
  a sandboxed child process whose program is built by interpolating the
  handler's name:

      from shani_chronoa.skills.stopwatch import <lambda>; import sys; ...

  which is a `SyntaxError`. The tool's *result*, as the model saw it, was a
  Python traceback. `tests/test_everyday_batch2.py`/`batch3.py` call the private
  `_run()` directly, which works perfectly, so **the suite was green while three
  skills were dead**. `tests/test_every_skill_handler_is_importable.py` now
  asserts the transport-level property (every handler's `__name__` is a real
  identifier and resolves in its own module), and `skills/__init__.py` refuses
  the registration with a warning rather than shipping a broken tool.

  The general lesson, and it is the same one as the `midi.py` entry below:
  **a test that calls the function is not a test that the skill is reachable.**
  Always exercise the registered handler through `execute_tool_outcome`.

- **`guardrail.py` was fully built, unit-tested and never called. FIXED
  2026-10-05.** It answers a different question from every other layer in the
  dispatch path — not "is this call permitted" but "is it even well-formed" —
  and `tools._dispatch` now consults it next to the reaction layer, where it can
  only stop a call that was already allowed. It turns `speak(text=42)` and
  `set_volume()` with no arguments into one readable sentence instead of a
  traceback three frames down in a subprocess. Verified by execution: both
  return `ran=False` and the well-formed call still runs.

- **`dream.py` was built and had no caller. FIXED 2026-10-05.** It now runs on
  the daemon's existing sleep tick (`senses` and `consolidation` already lived
  there), separately guarded so a dream that cannot run cannot also cost the
  consolidation pass above it. A separate timer unit would have meant a second
  thing to start, stop and misfire.

- **`memory` disclosed and deleted with consent off. FIXED 2026-10-05 — the
  most serious of this batch.** The gate lived only on the write paths
  (`remember_fact`, `link_entities`), so with `memory-sense-enabled=false` the
  `recall`, `about`, `history` and `forget` operations still returned the full
  contents of the durable store, and `forget` still erased from disk. Reproduced
  by execution before fixing; the gate is now at the top of `_run`, so it covers
  every operation. The per-write checks stay — they are reached directly by other
  callers, and an entry-point gate is not a gate on the function.

- **`labnetworks` raised `NameError` on every call. FIXED 2026-10-05.**
  `_record_state()` called `record_path()`, which was never imported. The sense
  was 100% broken — it could produce no reading at all, granted or not — and it
  had **no test file whatsoever**, which is why it survived.
  `tests/test_sense_labnetworks.py` now covers it, including an AST check that no
  module uses a name it never binds (the general form of this bug).

- **`midi.py` is the clearest illustration of this whole section — and it could
  not even be imported.** Added 2026-10-03 as a third member of this class
  (alongside `singing.py`/`prosody.py`): it reads a Standard MIDI File and fits
  its melody to a line of syllables, it is 357 lines of careful code, and
  `grep -rn "\bmidi\b" --include=*.py usr/` finds **no importer at all** — the
  one hit is `senses/capture.py`'s regex for ALSA `pcm` nodes. There is no
  `tests/test_midi.py` either. **Now reachable: `skills/sing.py` takes an
  optional `midi_file` and fits that tune instead of a named contour, which is
  the "a caller who wants a specific tune has to bring it" line its own
  docstring always pointed at.**

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

- **`singing.py` + `prosody.py` were dead code; that is FIXED, and this entry is
  kept because the lesson is the point.** They were fully built, unit-tested
  (`tests/test_singing.py`, `tests/test_prosody.py`) and verified on a real
  image, and as of 2026-10-03 nothing in the product imported either module -
  `grep -rn "singing\|prosody" --include=*.py usr/` returned nothing, and their
  only consumer was `../shani-testbed/slot-tests/chronoa-singing.sh`. **As of
  2026-10-05 they are reachable**: `skills/sing.py` exposes
  `SKILLS = [Skill(name="sing", ...)]`, verified by execution as both
  *discovered* and *dispatchable* (`skills.discover_skills()` → 152 schemas,
  `"sing" in handlers`). `midi.py` also imports `prosody` directly.

  This was exactly the dead-code class at the end of this section - a module
  that is green in isolation and unreachable at runtime. **Grep for a consumer
  before believing "done", and do not leave a green-but-unwired module described
  as shipped.**

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

   **Same class, still open as of 2026-10-05** — three more modules that pass
   their own tests and that **nothing that runs imports**. Measured, not read:
   `grep -rn` for a real importer across `usr/lib/shani-chronoa/`, then each
   candidate opened by hand:

   - `midi.py` — reads a `.mid` into a `prosody.Melody`. `skills/sing.py` exists
     and is dispatchable, but it **does not import it**: it takes notes the model
     supplies. So the "sing me a real tune" path is the one thing still missing
     from a feature that otherwise works.
   - `guardrail.py` — a well-formedness check between model and tool. Only
     referenced in `organism.py`'s metaphor text, which lists it as `BUILT`.
   - `dream.py` — the offline pass over the day's tool calls.

   **Two false positives worth remembering**, because a grep-only audit gets
   these exactly backwards: `mcp.py` and `search_provider.py` *also* have no
   importer inside the package, and both are genuinely shipped — they are entry
   points reached by `usr/bin/shani-chronoa-mcp` and `usr/bin/shani-chronoa-search`.
   **A module with no in-package importer may be a binary's `main`.** Check
   `usr/bin/` and the systemd units before calling anything dead.

   Not fixed here: each needs a design decision (what surfaces singing a real
   tune, where a pre-tool check belongs in the dispatch order, what schedules
   the dream pass), which is the same reason `ipc.py` was removed rather than
   wired.

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
