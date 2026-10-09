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
| ~~`test_permission_decisions.py` (2)~~ **FIXED 2026-10-09 — this entry named the wrong cause** | it was never the missing `grim`/`gnome-screenshot`. The fixture already writes a fake `grim` to a temp dir and puts it on `PATH`, because the skill runs in a sandboxed child that inherits `PATH` and not the test process's patches. What it did **not** supply was a *display*: `screengrab.capture_screen` calls `display_environment()` first and raises *"there is no display to capture"* when neither `WAYLAND_DISPLAY` nor `DISPLAY` is set — so on a headless runner the fake `grim` was never executed. **Supplying the tool does not supply the display.** The fixture now sets `WAYLAND_DISPLAY` too (preferred over `DISPLAY`, because `display_environment()` prefers it and setting only `DISPLAY` on a Wayland desktop would capture the XWayland root — the exact mistake that function's docstring warns against, committed in this test). 19 pass headless **and** with `DISPLAY` set; removing the display fails 2, removing the tool fails 5, so both halves are load-bearing. **A list that says "environmental" stops asking why, and this one was wrong for as long as it was there** — the same lesson as the seccomp entry above. |
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

### A logic-pattern scan across all 377 files: one live finding, three clean results

Structural scans were done, so this one went after **logic patterns** instead.

**`argfile._make_argfile_dir()`'s docstring said "A private 0700 directory" and
delivered the umask default.** Measured on this machine with the usual
`umask 002`: `~/.local/share/shani-chronoa/argfiles/` is **0775**, sitting beside
`egress/`, `logs/`, `sessions/`, `models/` and `percepts/`, all 0700.

Not a disclosure hole — the per-call directories come from `mkdtemp`, so they are
0700 whatever the umask, and the envelopes inside are written 0600. An
**integrity** one: a 0775 directory lets another local account unlink and replace
an entry. And the function's own docstring claimed a mode it was not delivering.

**`mkdir` then `chmod`, not `mkdir(mode=0o700)` alone**, which is the part worth
remembering and is already documented in `egress.py`: with `parents=True` the
mode argument applies to the **leaf only** — intermediate directories are created
at the umask default — and `exist_ok=True` leaves an already-created directory
exactly as it was. So a root that predates the mode argument stays 0775 forever.
The `chmod` makes the docstring true on every machine. Two mutations confirmed
to fail (chmod removed, chmod to a wrong mode).

**Three clean results worth having, so nobody re-derives them:**

- **Zero `subprocess.run` / `check_output` / `call` without a timeout.** My first
  scan said "28 subprocess calls with no timeout" and that was **wrong** — it
  matched `Thread.run` and `asyncio.run` by attribute name. The 12 real `Popen`
  calls are long-lived children (audio capture, playback, the STT server,
  wakeword, the sandbox executor) where a timeout would *kill the thing it
  started*, so none is a defect.
- **Zero `shell=True`**, so no string-built command lines to inject into. The
  sandbox executor's comment records that it used to.
- **Zero bare `except:`.**

One artifact looked like a finding and is not: `sandboxes/t/landlock_wrapper.py`
is mode **0664**, world-readable, beside 600 files. It is dated **2026-09-27** —
nine days *before* `executor.py` gained its `chmod 0o700`. Stale state from
before the fix, not a live bug.

### A Rebuild that built a model nothing could load

Wired earlier today and **measured useless on inspection**. Three separate
defects, found by asking "what happens to the file it produces?":

**1. The result was unreachable.** `current.gguf` is a **symlink**, so
`server_args` would load any file on disk - but the only function that could
point it there, `use(key)`, resolves its target through
`SPECS[key].filename`, a fixed catalogue of downloads. A re-quantization of a
model already on disk has no `SPECS` entry *by construction*: it did not come
from the catalogue. Settings -> Models -> **Rebuild** reported `Done: Q4_K_M`,
named a path in `$TMPDIR`, and discarded it. So the button built a measurably
better model (42.571 against 43.611 on the same corpus, same bytes) and left it
where the system clears it. Fixed with `adopt_path()`, which promotes **any**
file behind the same perplexity gate `use()` applies, and by making Rebuild
report the path *and* adopt it.

**2. Every run leaked ~200 MB.** `calibrated_quantize()` built its scratch with
`mkdtemp()` and **never removed it, on any of the four exit paths** - including
the two refusals that had built nothing worth keeping. Each directory held a
corpus, an `imatrix.dat` and **two** ~100 MB GGUFs. The scratch now lands under
`model_dir()` rather than `$TMPDIR`, and the two paths that built nothing reclaim
it. The one path that hands back the naive file keeps it - and names the
directory, because a caller holding a file must be able to find its home.

**3. Nothing could ever reclaim it.** `_QUANT_SCRATCH` is in memory, so it is
empty in every process except the one that made the directory - which is to say
the Settings window could never reclaim last week's leftovers. **Measured: the
first version found the directory and removed nothing.** The guard is now the
name prefix *and* the parent directory, checked inside `_remove_scratch_dir()`,
which is the only function here that deletes anything on a pattern.

Two bugs of my own, both caught by measurement rather than reading, both recorded
because the shape recurs:

- **`directory.name not in _QUANT_SCRATCH_PREFIXES` asks whether the whole name
  *equals* the prefix.** `mkdtemp` appends random characters, so every real
  scratch directory was refused: two present, zero removed, logged as "not a
  quantization scratch name". It has to be `startswith`.
- **The reclaim guard compared a file against a directory.** It protected
  `link.resolve()` - the model *file* - then compared it to each scratch
  *directory*, so it could never match and never fired. Measured: after
  adopting `chronoa-quant-a/m.gguf`, reclaim deleted `chronoa-quant-a` and left
  `current.gguf` dangling. Both the file and **its parent** must be protected.

`f"{freed / 1e6:.0f} MB"` also reported **"0 MB"** for a 4 kB reclaim - the exact
size the button exists to explain - so sizes go through `_human_bytes()`.

**32 tests, 12 mutations confirmed to fail.** Two of those mutations were
**survivors on the first run** and are worth remembering:

- The self-adopt test passed with or without the guard, because with no
  `llama-perplexity` installed `quality_verdict()` returns `measured: False`
  for *everything*. A test that passes for an unrelated reason is not a test;
  stubbing a number was what made it real.
- Only the **first** failure path had a reclaim test. Removing the reclaim from
  the second one - the expensive path, where the matrix has already been fitted
  and both ~100 MB builds exist - left all 19 green.

And the reclaim row originally had **no test at all**, proved by a mutation that
replaced its call with a hardcoded "nothing to do" and stayed green. Its handler
was a closure inside `_build_models`, unreachable without a display, so it is now
a staticmethod - the same reason `_rebuild_calibrated` already is.

**Regression caught by existing tests, not by new ones:** an edit to those two
lines dropped `directory.mkdir()`, so every `work=` run failed with "llama-quantize
could not produce". `test_the_happy_path_names_all_three_artifacts` failed before
the new tests were even run.

### The 76% failure signal about a tool that no longer exists

`unknown_tool_examples()` has been *reporting* this since before the Learning
page existed, with a docstring that says the answer is the person's:

> It is recorded rather than filtered. **Filtering would be the more useful
> behaviour and the wrong one here**... Whether to train on it is their decision,
> made with the figure in front of them.

That argument is right for a **default** and wrong as a **dead end**. So the
default is untouched - `only_tools=None` trains on everything, byte-identical to
before - and the filter now exists for when a person wants it, **next to the
figure on the same page**.

Measured on this machine's log (14,395 records), via the code's own reader:

| | |
|---|---|
| records naming tools this build lacks | **712** (4.9%), `liar` and `unver` |
| of those, failures | **`liar`: 323 of the 423 total failures - 76%** |
| examples dropped when filtered | 650 of 14,102 |

So the outcome model's most confident, most heavily-repeated lesson is about a
tool that cannot run, and its "detects failure" score is substantially a memory
of `liar`. That is not a bug in the model; it is the model being faithful to a
log full of a fixture.

**`_EXAMPLE_CACHE` was the whole ballgame.** Keyed by path and revision alone,
so the first unfiltered call populated it and a filtered caller was served the
**unfiltered** list: 4 examples either way, from the same file, with the button
appearing to work and changing nothing. A feature that does exactly what it says
and does not do it is worse than a missing one. The key is now
`(path, sorted-filter)` - sorted, so `{a, b}` and `{b, a}` share an entry rather
than parsing the whole log twice.

Three of my own test bugs, all the same shape - **asserting the implementation
instead of the behaviour**:

- Spied on `train_and_report` to prove the filter reaches the fit. It never gets
  called: `train_and_save` splits and cross-validates itself. `load` is the one
  boundary every route crosses.
- Asserted `provenance` on a return that legitimately had none, because the real
  model refused to be written on four examples. A test that needs a save to
  succeed in order to check a filter breaks for the right reason at the wrong
  time.
- Asserted the filter reached `cross_validate` as a **kwarg**. It should not -
  `load()` already filtered the list. What matters is that CV gets a *shorter
  list*, which is what is asserted now.

And `example_count` lives **inside `provenance`**, not at the top level of
`train_and_save`'s return - reading it from the wrong level reports "Trained on
None of your records".

12 tests, 3 mutations confirmed to fail.

### `refuse_catalogue` refused `/` and `~`, and nothing around them

The guard for `delete_file`, `extract_archive`, `trash_file`, `edit_file` and
`undo_last_change` tested `path == root`. Measured by relocating `$HOME` into a
temp tree and recording any `shutil.rmtree` call instead of performing it:
`delete_file` with `recursive=True` and `path=<the parent of $HOME>` called
`shutil.rmtree` on a directory **holding the user's entire home directory**, and
`refuse_catalogue` said nothing.

The check is now `root.is_relative_to(path)` - equality is the narrowest case of
"this path contains a protected root"; the parents are the rest, and deleting a
parent deletes the root inside it just as surely. Subdirectories of home, deep
descendants, and siblings of home are still allowed, and nine tests pin the
difference.

**Severity, honestly.** On a conventional multi-user Linux box, the parent of
`$HOME` is `/home`, root-owned, so the `rmtree` fails with `EACCES` and the
filesystem stops what the guard missed. The trade fails open in the guard and
closed in the permissions, so the guard is the second line. It was the *first*
line on any layout where the parent is user-writable - a single-user system
with `$HOME` directly under a user-owned directory - and it is a hole in the
function whose own docstring said its job was to close it.

Also: both concurrency candidates this scan turned up are already covered -
`planmode` reads `_enabled` and `_reason` under a lock, `ask_bridge` touches
`_presenter` only once at startup. No fix was the honest outcome there, and no
`open()` call outside a `with` block is an actual leak (each is closed by a
later `with handle:`). The HTTP layer sets a timeout on every client; zero were
missing. Positive results recorded so nobody re-derives them.

### Not wired on purpose: a quality gate for the vision and embedding models

`local_llm` gained a perplexity gate; `local_vision` and `local_embed` have
none, and **copying it across would be theatre**:

- **The embedding model cannot be measured by perplexity at all.** It runs with
  `--embedding --pooling mean`, producing vectors rather than tokens, and
  `llama-perplexity` has **zero** `--embedding` or pooling flags (measured:
  `llama-perplexity --help | grep -c` returns 0). There is nothing to gate on, so
  a "quality gate" here would be a check that cannot fail.
- **The vision model's language backbone could be measured** - `llama-perplexity`
  does accept `--mmproj` - but that scores how well it predicts *text*, which is
  not the thing the vision model is for. It would be reported as if it were.

Both stay digest-verified and unmeasured, which is what they honestly are. If
this is revisited the measurement to reach for is retrieval quality against a
labelled set, not perplexity.

### The full inventory: what is dead, what is deliberate, and what is a lie

An AST scan of **all 1,025 public functions and classes** in the package found
**57 never called anywhere**. The 379 "called only inside their own module" are
ordinary internal helpers and were not treated as findings. Triaged in full so
nobody re-derives this:

**Wired this session (15).** `merge_models`, `lessons`, `render_lessons`,
`experience_summary`, `evaluate_bandit`, `organ_status`, `adopt_retired`,
`import_knowledge`, `probe_capabilities`, `perplexity`, `quality_verdict`,
`calibrated_quantize`, `audio_status`, `seccomp.is_active`, and
`permissions.cancel_requested` (which had a caller added rather than a caller
found).

**Deliberate, and the reason is recorded in the code.**
`retire_model` is a no-op on purpose — a rename is the only genuinely fragile
operation in that layer and the space check on load already refuses a stale
model. `summarize_tool_results` / `summarize_history` take an opt-in
`summarizer` that `assistant.py:185` passes none of, so compression is
elision-only by choice, not by omission. `open_settings_button` /
`wire_page_button` are **speculative API, not duplication**: the four call sites
that navigate to Settings use `banner()`'s own button, not a suffix button, so
the helper has no user. `bayes`, `timeseries`, `clustering`, `classification`,
`anomaly` are a statistics toolbox. `Point`, `Cert`, `Busy` are dataclasses.
`prctl_set_seccomp_attempt` is the legacy path kept on purpose.

**False positives a `usr/lib`-only scan produces, and the two that bit me.**
`apply_filesystem_allowlist` is called from *generated code* inside
`get_landlock_wrapper()`. `revalidate` and `read_envelope` are called by
`usr/bin/shani-chronoa-lab-network` — **any scan of "who calls this" in this
repo has to include `usr/bin/`**, because that is where the privileged half
lives. `check_destination` appeared in the discarded-result scan and is
correct: it returns `None` and raises on refusal, so `_guard()` fails closed.

**Still unwired, genuinely candidate.** `run_from_file`, `webkit_version`,
`known_organ`, `page_titles`, `install_hint`, `pending_count`, `price`,
`collapse`, `capability_for`, `summarize_*` siblings, `from_url`,
`save_record`, `lab_network_namespace`, `encode_rejection`,
`describe_captured`, `remember_from_turn`, `tempo_map` / `apply_contour` /
`within`.

**All 154 schema keys are read** — there is no unwired setting in this
repository. Worth recording as a clean result.

### Re-run of the same scan: eight names wired, and five were live defects

The scan above re-run (a token-count pass over `usr/lib`, `usr/bin`, `tests/`
and the root scripts, excluding each definition's own line) found 49. Eleven
were wired; **five of those eleven were not missing features but shipped
defects**, which is the argument for running the scan again rather than
trusting the list above. Each was found by running the real thing, not by
reading the caller.

**`--show-page=` did not work, for five of six destinations.** `pages.py`'s
own docstring gives `--show-page=settings:privacy` as the example. Measured on a
real app: `main:conversation`, `settings:senses`, `settings:privacy` all
returned **False**, and `setup:review` logged *"setup cannot show a page"*. Four
separate causes, none of them visible in the registry:

- **The registry was only populated by building the window.** `register()` ran
  inside `SettingsWindow.__init__` and inside `build_window`, so `show()`'s
  `window_id not in _PAGES` check fired *before* the factory that could have
  built the window was ever consulted. The factory was unreachable code. Both
  now register at import time, and `pages._DECLARERS` imports the declaring
  module on demand so an id is answerable before anything has been built.
- **`senses` and `privacy` matched no group at all.** The seven ids the registry
  promises are **sections**; the window's groups are 25 and are titled after
  their own content ("Getting started", "Privacy and network"). Measured on a
  real window: declared-but-not-built was exactly `['privacy', 'senses']`. So
  the search-based `show_section` could never land on them — while a window with
  fourteen sense rows sat there. `group._section_family` is now set by each
  builder and `show_section` reveals a family when the id names one.
- **The wizard's `show_page` was a closure held only by the local scope that
  built it.** `pages.show()` looks for `window.show_page`; there was no
  attribute to find, and the wizard logged "cannot show a page" on a wizard with
  the Review page fully built. It is attached now.
- **`forget_window` had no caller**, so `_WINDOWS` grew forever and a
  destroyed window stayed addressable. Now dropped on `destroy` — not on
  `close`, because closing a `Gtk.Window` can merely hide it and forgetting it
  then would make `show()` build a second copy of a live window.
- **Clearing the search box did not restore the window.** `_on_search` with an
  empty needle fell through to "no needle, so everything matches" only by
  accident of the `not needle or` clause; after `show_section` had set
  visibility directly, clearing the box left the person with a fraction of the
  window. Measured: `25 → 14 (senses) → 3 ("camera") → 25` after the fix, and it
  stopped at 14 before it.

The three lookup functions (`page_ids`, `resolve`, `page_titles`, `windows`)
also load their declarer first, so "does this id exist" cannot depend on which
module happened to be imported already. `_DECLARERS` maps to a **callable**, not
a module: importing once is not enough, because `tests/test_pages.py` resets the
registry and would otherwise leave every window permanently undeclared.

**Four more, each measured before and after:**

| helper | what it was doing | what was wrong |
|---|---|---|
| `ChronoaWindow.clear_input` | unused | Ctrl+N emptied the transcript and left a half-typed question in the composer, so the next Enter asked about the *previous* conversation |
| `capabilities.help_prompt` | unused | `/help what can you do?` opened the window and **threw the question away**; the command now fills the composer, and `set_input_text` is a separate method from `submit_text` because "show this" and "send this" are different acts |
| `permissions.allows_prompting` | unused | The Approvals page read only `can_ask()`, so `DONT_ASK` **with a listener present** said "Chronoa will ask before running a gated action" — a policy `decide()` refuses on the spot, on the one page whose subject is what the app enforces |
| `is_parakeet_provisioned` | unused | The Models panel listed only `stt_provision.MODELS`, so a machine with a Parakeet model installed showed **every speech card** as "not downloaded" — the confident-wrong-answer shape this file already records for the Eyes list, reached from the other direction |

Plus `provenance.describe_boundary()` into `SYSTEM_PROMPT`: every tool result
has been fenced as untrusted data since provenance landed, and the model was
never told what the markers meant. **A fence the model cannot interpret is
decoration.** And `organism.Organ.is_live` into the Inventory panel's per-row
tooltip, so "a light means something is happening" and "this row has no light"
are reconciled on the same screen rather than only in the summary group.

`voices_for("en")` now answers the wizard's question instead of the wizard
filtering `VOICES` itself — which voices may be offered is a fact about the
catalogue, and one list re-deriving it is how a catalogue change would show up
in one place and not the other.

**Mutation-verified, all failing when reverted:** wizard `show_page` detached
(1), `senses` family removed (1), search-clear restore removed (1), family
filter widened to everything (1), composer clear removed (1), `/help` argument
discarded (1), approvals mode check falling back to `can_ask()` (1), Parakeet
cards removed (1), boundary line removed from the prompt (1).

**`tests/test_pages.py::test_all_three_windows_answer_to_show_page` was
green while two of the three windows were unaddressable**, because it grepped
the source for `def show_page`. Rewritten against the real registry and a real
wizard, plus `test_every_declared_settings_id_shows_something`, which builds the
window and asserts each declared id reveals *some but not all* groups — an id
that hides nothing is as wrong as one that shows nothing. A negative-space guard
that cannot fail is the same failure as a test that always passes.

### Two more that existed, answered nothing, and were exactly what a panel is for

A second scan — not "never called" this time, but **query-shaped functions whose
return value is discarded at a call site** — turned up two, and both are the
Diagnostics panel's job:

- **`audio.audio_status()`** — zero callers, and its own docstring says why that
  is a loss: it returns the statuses *"rather than a boolean for the reason
  `check_heartbeats` documents — a caller has to be able to tell **'not started
  yet' from 'hung' from 'gone', because only one of those three is worth waiting
  for**."* A boolean cannot carry that, and Diagnostics is where somebody looks
  when audio is not working.
- **`sandbox.seccomp.is_active()`** — zero callers, and its docstring is careful
  about scope: the filter is installed in the **sandboxed child**, so this
  long-lived process reads `False` while every skill call is still filtered. The
  row keeps that honesty: `False` reads **"could not determine"**, never
  "working", because a row that said *"no seccomp"* would be a lie.

**`STATUS_WORDS` is a closed vocabulary of three and a row may not use a fourth**,
so a gone audio child is `STATUS_NOT_WORKING` with the three states named in the
detail — my first draft invented a fourth word, which would have broken the
rule the module states in its own constants.

**Two mutations, and one of them was my fault twice.** Collapsing the three audio
states into one boolean left the suite green on the first attempt, because I put
the mutation **before** the loop that classifies them — a no-op dressed up as a
test. Redone in the right place: caught. That is the second time this session a
`str.replace` "mutation" silently did nothing; every mutation now asserts
`s.count(old) == 1` **and** is placed where it actually changes behaviour.

`check_destination` also showed up in the discarded-result scan and is a **false
positive**: it returns `None` and raises on refusal, so discarding the value is
correct and `_guard()` fails closed.

### The model actions were reachable only as a side effect of changing models

`local_llm.perplexity()` and `quality_verdict()` run `llama-perplexity` — which
is installed on this machine and was **never invoked anywhere in this
repository** — but were reachable **only through `use()`**, i.e. only as a side
effect of switching which model is loaded. There was no way to ask "is this model
any good?" without changing something. `calibrated_quantize()` had no caller at
all.

Two buttons on the Models page, both off the main thread (perplexity takes
seconds, calibration takes minutes), both re-enabling themselves so one run does
not end the feature for the session:

- **Measure** — perplexity of the model in use against another installed here,
  through the same gate `use()` applies. An unmeasurable result reads
  *"Not measurable"*, never a number.
- **Rebuild** — re-quantizes an **F16/BF16** source with a calibrated
  importance matrix. It refuses when there is no high-precision source, because
  **re-quantizing an already-quantized file is meaningless and the error is not
  obvious** — the output is simply worse than the input while claiming to be an
  improvement.

Measured on this box, both report honestly rather than inventing: *"No model is
installed yet"*, *"No F16/BF16 model to re-quantize"*.

One test passed for the wrong reason first: it created a model file but no
`current.gguf`, so the handler answered *"No model is installed yet"* and never
reached the verdict under test.

### Which providers can do speech — the table was real and unreachable

`cloud_voice.probe_capabilities()` was written and measured live on 2026-10-06,
then left with **zero callers**. So the capability table lived inside a
maintenance function no person could reach, while the two switches above it in
Settings said only *"needs an API key"* and stopped.

It is a **button and not a row**, because it makes real requests to up to five
providers with a 30 s timeout each, and a probe that can take two minutes cannot
run on the thread that draws the window.

**My first renderer invented keys of its own** — `stt_yes`, `tts_yes`,
`needs_key` — and would have printed *"none"* for every provider while looking
like a measurement. The real shape is `{pid: {"base_url", "stt": {...},
"tts": {...}}}` with `verdict` inside each, and the verdicts are **hyphenated**:
`yes`, `no`, `needs-key`, `needs-paid`, `unreachable`. Read correctly it
reproduces the measurement exactly: speech in from *groq, kilo, llm7, openai,
openrouter*; speech out from *groq, openai, openrouter* only; **blockrun paid**;
**anthropic with no route at all**.

All five verdicts are kept apart, and two of them are the ones that matter:
a route answering **402 for money** is not one that works, and **"could not be
asked"** is not **"has no route"** — the first is a network problem, the second a
fact about the provider. Collapsing them is what made the table wrong three times
before it was right.

Nine tests, four mutations confirmed to fail (hyphenated verdicts unrecognised,
the paid route folded into "works", unreachable folded into "no route", and the
probe moved onto the main loop).

### The last of the orphaned fifteen, wired — and one that misdescribed itself

The AST scan found **15 public names in `learning.py` with no caller anywhere in
`usr/` or `tests/`**. Most are a statistics toolbox (`bayes`, `timeseries`,
`clustering`) that is legitimately library surface. Nine have now been wired;
these are the last three, and each was **fully written** rather than stubbed.

| function | what the panel now says |
|---|---|
| `evaluate_bandit` | **"3 tool(s) scored; top-5 precision 33%; but it does NOT rank them better than chance yet (rank correlation -0.5)"** |
| `experience_summary` | *"1244 scored call(s) across 4 tool(s) on this machine"* |
| `organ_status` | *"none - no tool has been scored enough to carry a learned weight"* |
| `adopt_retired` | a **Restore a retired model** button |

**`evaluate_bandit` is the one that mattered.** It measures whether the bandit is
*learning*, and it had never been called — so "the bandit works" was an
assumption, and three thin estimates were one panel away from reading as a
ranking. On this machine the honest answer is negative, and the row says so
rather than showing the arms.

**`organ_status`'s docstring claimed the Inventory panel showed its numbers. It
does not** — `inventory.py` renders `organism.INVENTORY`, a static table of which
organs are *built*, and never mentions tools, trust or doubt (verified: none of
the four tokens appear in that file). So the function was orphaned *and* its claim
was wrong, in both directions. Its docstring is corrected and the row is here.

**`adopt_retired` reports its own absence.** Retirement was removed on purpose — a
rename is the only genuinely fragile operation in this layer, and the space check
on load already refuses a stale model without anyone moving anything — so this
fires only on a machine that already has a set-aside model from an older version.
Saying "restored", or staying silent, would both be wrong; it says nothing was
set aside.

Five mutations, all confirmed to fail, including the bandit's negative verdict
being dropped and the absence reported as success.

Suites: **174 passed**.

### Calibrated quantization — and a flag-order bug that failed silently

The "modify an open model with its own weights" path. It **does not train** — no
parameter is updated from a gradient — but it re-quantizes at a chosen precision
guided by an importance matrix, so the precision budget goes where activations
say it matters. That is the whole of what is available without CUDA or torch.

`calibration_corpus()` builds the imatrix corpus from **Chronoa's own source**,
not filler: llama.cpp's guidance is calibration data "derived by running a model
over a representative text corpus", and the representative corpus for this system
is the tool schemas, skill docstrings and settings copy it is asked to reason
about.

**The bug, found by running it rather than reading it.** My first `quantize()`
appended `--imatrix` after the model paths. The installed tool's usage is

```
llama-quantize [--imatrix file] model-f32.gguf [model-quant.gguf] type [nthreads]
```

and that trailing `[nthreads]` is **positional**, so the flag is parsed as a
thread count:

```
main: invalid nthread '--imatrix' (stoi)
```

That is the verbatim output on this box. It costs the entire calibration while
reporting only that — which is why the flag now precedes the positionals, and why
there is a test asserting the order rather than a comment.

`calibrated_quantize()` **refuses rather than pretending**: with no
`llama-imatrix` it returns `calibrated: False` and names the uncalibrated file as
`uncalibrated_fallback`. Handing that back under `calibrated: True` would be worse
than failing, because the caller could not tell them apart — and the entire point
of an importance matrix is that it is not the same file.

Nine tests, three mutations confirmed to fail (flag order moved back, the naive
file relabelled as calibrated, a timeout turned into a pass).

Measured on the real model, base `SmolLM2-135M-Instruct-F16` (259 MB) from
`unsloth/SmolLM2-135M-Instruct-GGUF`: naive `Q4_K_M` in **751 ms**, calibrated
`Q4_K_M` in **10.9 s** with the 631 KB matrix.

### A model was promoted on a matching digest and nothing else

`provision()` verifies a **digest**. Nothing verified the *artifact*: a
correctly-hashed bad quant passed every check there was. Meanwhile
`llama-perplexity` is installed on this machine and was **never invoked anywhere in
this repository** — `local_llm.py`, `local_vision.py` and `local_embed.py` all
download *pre-quantized* GGUFs and point `current.gguf` at one.

Perplexity is the right measure because that is how llama.cpp documents
quantization loss: the upstream `tools/quantize/README.md` measures it in
"ppl and/or KLD", and `llama-imatrix` exists because the same calibration data
improves the quant. So `local_llm.perplexity()` runs the installed tool over a
**fixed** corpus — fixed so two models are scored on identical bytes, and
written into the module so it cannot go missing from a package and silently turn
the gate into a no-op.

Measured on the two models already on this box:

| model | perplexity |
|---|---|
| `SmolLM2-135M-Instruct-Q4_K_M` | **1.805** |
| `Qwen3-0.6B-Q8_0` | **1.373** |

and promoting the first for the second is **refused**: *31.5% worse, past the 2%
limit*. Five seconds per model.

**`None` means unknown, never "fine"** — no binary, a timeout, unparseable output
and a missing file all read as `None`, because a probe that could not read
anything must not report a pass. And an **unmeasured** model is still promoted,
with the reason recorded: a minimal install without the tool should not be unable
to pick a model at all. What is refused is the *claim*, not the choice.
`use(key, gate=False)` is the explicit escape hatch.

**My first parser matched nothing.** The installed tool prints
`Final estimate: PPL = 1.0128 +/- 0.00145`; my patterns were `perplexity = ...`
and a case-sensitive `\bppl`, so **every** measurement came back `None` on a box
where the tool runs in four seconds. Verified against the real binary before
writing the test, so the test asserts the real format.

Thirteen tests. Five mutations; **one equivalent** (breaking the first of three
parsing patterns leaves the two fallbacks matching, which is defence in depth by
design) and the real break — all patterns removed — is caught by three tests. One
assertion was too weak: the refusal quotes a percentage *derived* from the two
numbers, so dropping the raw figures still left "31.5% worse" in the sentence. It
asserts **both** absolute numbers now.

`local_llm` / `modelfit` / `surface_model` / `vision` suites: **153 passed, 2
skipped**, and the single failure is the recorded `grim` one — "no Wayland screen
capture tool is installed".

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

## The channel bridge: Telegram and WhatsApp, thin clients of `Submit` (2026-10-06)

`channel_bridge.py` (`shani-chronoa-bridge`) is the other half of the inbound
channel: adapters that *carry* a message to `gateway.Submit` and print the
reply. Telegram (Bot API `getUpdates` long poll - dials out, no public URL)
and WhatsApp (Cloud API webhook - the one adapter with a port, loopback by
default, every POST checked against `X-Hub-Signature-256` before it is
believed). Tokens come from the bridge process's environment
(`CHRONOA_TELEGRAM_TOKEN`, `CHRONOA_WHATSAPP_*`); Chronoa never sees one, and
a reply that mentions one is redacted before it is logged.

**Verified by running, not by reading:** 16 tests in
`tests/test_channel_bridge.py` - `httpx.MockTransport` for both wires, a real
loopback HTTP server for the webhook (challenge GET + signed POST + a 403 on
a forged signature), and the bus call itself driven **across two processes on
a real `dbus-daemon`**: the app's own `gw.export` on one side, `cb.submit` on
the other, asserting the reply round-trips and an unconfigured channel is
refused by name. An in-process `Gio.TestDBus` **cannot** drive `cb.submit` -
its `call_sync` needs the connection's main context iterating and deadlocks
against the gateway's worker-thread reply (measured: the test hung until
killed). The launcher itself was run directly: `--help` works, and both
channels exit 2 naming the missing env var rather than starting token-less.

`shani-pkgbuilds/shani-chronoa/PKGBUILD` gained the `install -Dm755` line for
the new binary (the packaging test reads git's recorded mode, so the file is
staged; a checkout that is not committed still fails that test by design).

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

## The right rail, and what it may say (2026-10-07)

The window had a left sidebar and a transcript, and nothing else. Three
questions a person has while talking to an assistant with its hands on their
machine had nowhere to live, because the conversation is a *transcript*: it
grows downward and the useful part scrolls off the top. `gui/rail.py` is a
right-hand column answering them, in that order:

1. **Now** - the live state and the tool currently running, read from
   `_state_label` and `_detail_label` rather than tracked separately. The
   first version kept its own copy and rendered "Idle" while the window said
   "Ready"; reading the widgets is the fix, and it is also why the rail is
   built at the *end* of `_setup_ui` - built earlier it reads labels that do
   not exist yet.
2. **Timers** - pending countdowns with the time left, so a countdown is a
   countdown rather than a number that never moves.
3. **Changed** - files Chronoa wrote that the undo ring can still put back,
   each row a button that opens the Diff panel. Same store the Diff panel
   reads, deliberately: a second change log would drift from the first.
4. **Tasks** - outstanding items from the task list, **only when its consent
   key is on**. With the key off it says so, because silence would read as
   "no tasks" when the truth is "you are not looking at them".
5. **Posture** - permission mode, memory consent, sandbox qualification. Three
   lines that each change what a reply is allowed to mean.

**Not in it, on purpose.** Not the senses and not health - those panels own
them, with a per-row reason. Not goals or a plan: `goals.py` has no producer
yet, and a card for a queue nothing can enqueue is a dead control. Every
section above reads a store that already exists and renders an honest
sentence when it is empty, because a rail of decoration is worse than no rail:
it looks like it knows something.

**Two bugs found by rendering, not by reading.** The rail's scroller had
`set_propagate_natural_width(True)`, so a long posture line pushed the column
past its own width request and off the right edge of the window - the one
thing a fixed-width column exists to prevent. And `RailBreakpoint` was first
handed the *row* holding the conversation, so at 600px it hid the conversation
too: an empty right half with no orb, no transcript, no composer.

**`SidebarBreakpoint` was a dead control too, and the narrow layout was
impossible for a reason nobody had looked for.** The breakpoint was constructed
and returned; nobody ever called `window.add_breakpoint()`, and an
`Adw.Breakpoint` no window knows about is never evaluated. Registering both is
what makes the rail responsive - but at 600px the sidebar still did not
collapse, and the cause was upstream of it: the split view reported it needed
**762px**, because **the organ strip asked for 470px of its own**. Eight little
status lights were the single reason the conversation could not fit a narrow
window.

`OrganStrip` is now a `Gtk.FlowBox`, so it wraps instead of demanding a row
(measured: 470px minimum -> 90px). Two follow-ups the render forced: the cells
must not be *homogeneous* (equal-width cells broke "thinking" onto a second row
at 1280px with room to spare), and `max_children_per_line` is set explicitly
because the default still wrapped early. The collapse itself is applied on
width change rather than by the breakpoint alone - the breakpoint's setter runs
before the split view finishes setting itself up and is then overwritten - and
it goes through `_set_panels_visible`, because `collapsed` alone leaves the
drawer *open* over the conversation, which is the exact bug that method's own
docstring records for F9.

Two declarations were also doing nothing but printing a warning on every window
build: `width`/`height` on `.sidebar-dot` and `cursor: pointer` in the diff
stylesheet. GTK4's CSS parser has neither property; the dot was being sized by
its `min-*` pair all along, and the pointer is now set through `set_cursor`
where GTK is new enough for it.



`../harness-study/HARVEST-2/` mined 42 agent harnesses; every item it proposed
is now in the code or rejected with a reason in its own `00-MASTER-BACKLOG.md`
implementation log. Three rounds: Tranche 1 patches, then the feasible Tranche 2
items, then the T3 structural core. What landed, and the reasoning that decided
the shape:

- **`ToolFailure` (`toolfailure.py`)** — a skill that ran but whose effect did
  not hold says so in-band; the child carries it as a stdout marker, and both
  the local and the sandboxed path report `ran=True, verdict=FAILED`. This is
  the first honest fourth state; everything else in `DispatchResult` is
  "ran/not ran" crossed with "verified/not".
- **Per-origin round budgets (`assistant.py`)** — `handle(origin=...)` is the
  parameter the T2.17 blocker analysis kept asking for. A person gets 4 rounds,
  a trigger-driven turn gets 1, an origin nobody has named gets 1 (fails closed
  like `profile_for_origin`). The same parameter now reaches `execute_tool`, so
  the audit log can finally tell a typed turn from a bridge turn.
- **`permissions.Mode` — `DEFAULT` / `DONT_ASK` / `EXPLORE`, plus
  `bypass_immune`** — `DONT_ASK` refuses every ask and suspends session grants
  (the unattended-trigger default); `EXPLORE` refuses anything outside
  `capabilities.READ_ONLY_TOOLS`; destructive tools never answer from a
  standing grant and no longer *offer* one. **This is the item most likely to
  be argued with**, and the argument is worth having: `delete_file` asking again
  on the third call in a session is friction that buys a property. It buys it
  because the alternative is a session grant written by one "yes" standing in
  for every later delete, and the harvest is explicit that the answer to
  "should this be delegated" is per-tool.
- **Requery/validation budget** — three never-run calls in a row ends the turn
  honestly. A round where the only tool output is refusal, repeated, is a circle
  more rounds do not break.
- **`goals.py`** — a plain-data goal run that can `park` and `resume`, with
  `PlannedStep` variable dependencies that refuse to run a step whose inputs do
  not exist yet. Deliberately un-wired into the UI: a task card over a queue
  nothing can enqueue is a dead control.
- **`restart.py` / `sandbox/qualify.py`** — the bounded restart table and a
  fail-closed qualification that reports `missing`/`unknown` rather than
  blending "off" into "yes". On this unprivileged machine it correctly reports
  `proven is False`.

**What the wiring audit caught, which the unit tests had not:** `ToolFailure`
was only ever proved in-process — a live dispatch through the real subprocess
was the first thing that proved the marker crosses; the permanent test includes
a control that must succeed, so the assertion can fail. The event sink had no
live subscriber until `ConversationMixin` started handing it to `handle()` in
place of three callback slots. And two surfaces (`diff`, `artifact_store`)
returned pages with no `status()` at all, so every surface walker raised — the
new `diff-symbolic` icon was a glyph this theme does not ship.

**The permission-mode row is session-scoped on purpose.** Every subtitle says
"Back to Normal when Chronoa restarts". Persisting `EXPLORE` would leave a
restarted assistant unable to change anything with no visible cause; that is the
"an option whose label promises something the program does not do" failure the
module already has a scar for.

## Six defects the first full run after the harvest work found (2026-10-07)

The first complete run since that work landed: **53 failed, 5739 passed, 39
skipped** in 45:44. Eleven of the failure *files* were already on the
environmental list below and are unchanged; these are the rest.

**`office/` was mid-rewrite when the run started, and was reverted while it ran.**
At the start of the run `office/write.py` was an uncommitted rewrite that had
dropped `make_docx`, `make_pptx`, `rows_from_text`, `_Strings` and `_xlsx_cell`
— `skills/analyze_table.py:312` and three tests call the missing `make_xlsx`, so
`save_as='~/x.xlsx'` raised `AttributeError` on every call, and `office/edit.py:105`
still called a `_Strings` that no longer existed. Nine `test_office_document.py`
failures came from that. **Partway through this pass `office/` was reverted
outside the session** and now matches HEAD, which carries its own `make_xlsx`
(shared-string table, `fullCalcOnLoad`, `[Content_Types].xml` first).
`test_office_document.py` (11) and `test_analyze_table.py` (13) both pass
against it. Nothing here is claimed as the fix: **a suite someone else's
`git checkout` turned green is not a repair**, and the one genuine finding from
that thread is worth keeping — the rewrite's xlsx writer emitted `<w:row>` into a
document binding only `x:` and `r:id` into a workbook that never declared `r:`,
so the file it produced failed `read_xlsx_rows` with `OfficeError: workbook.xml
inside the file is not well-formed XML (unbound prefix)`. No test caught that
because nothing ever read a workbook this module had written.

**Zeroing the organ strip's cell padding removed the gap between the lights.**
The `OrganStrip` is a `Gtk.FlowBox` so it wraps on a narrow window, and the
`flowboxchild` padding was costing ~230px of width — `padding: 0; margin: 0`
fixed that measurement and broke the reading: the eight labels rendered as
**"listening looking speaking network sensing remembering acting thinking"**,
one run-on string rather than eight named lights. No test caught it, because
constructing the strip is not looking at it and the width test *passed* the
whole time.

The first repair put a 4px CSS **margin** back, which is what the render wanted
— and left the gap in two places (that stylesheet plus
`OrganStrip.set_column_spacing`'s 2px), so no test could measure it and either
half could be removed without failing anything. It is now
`set_column_spacing(6)` in `organs.py` and nothing else, with
`test_the_organ_lights_are_separated_by_a_gap` on that property. **The lesson
is the width measurement's blind spot**: it measured the right number and the
render was still broken.

**The organ strip: what was asked, what is actually wrong, and the lead.**
Asked whether the strip is misaligned and whether it drops anything. Measured,
not read:

| question | measurement | verdict |
|---|---|---|
| does every organ appear? | `ORGANS` 8, `ORGAN_ICONS` 8, no key either way, **8 of 8 cells mapped** at 1280 and 640 | nothing is missing |
| is any label truncated? | every word whole in the render at both widths | no |
| is each icon over its own label? | pixel-measured: worst offset **1.0px** across the eight | yes, centred |
| is the *row* aligned? | labels span x=296..791 (centre 543) while the mode chips below span 477..782 (centre 629) | **86px off** |

So the misalignment is the row, not the icons: it is hard against the left of
the chat column while the chips and composer beneath it are centred. **Two fixes
were tried and both are measurably no-ops, which is why neither is in the tree:**

- `set_halign(Gtk.Align.CENTER)` on the strip: the property is set and no pixel
  moves. A vertical `Gtk.Box` hands its child the full cross-axis width whatever
  the child's alignment is — the strip was allocated **680px of a 680px box**
  with `halign=CENTER`.
- Wrapping it in a horizontal row between two expanding spacers, the usual
  trick: also nothing, because **there is no slack to distribute** — the strip
  fills its container.

The numbers nobody had looked at are why. Measured in the real window at 1280:

```
strip.measure(HORIZONTAL, -1)  ->  min 90,  natural 834,  allocated 680
cells allocated                 ->  58, 50, 59, 56, 50, 90, 41, 54   (= 458)
7 gaps x 6px                                                (=  42)
                                                            total  500
```

So the strip is handed the whole 680px column, its cells fill only **500px** of
it, and the ~180px left over is the lopsided right edge. Its *reported* natural
width (834) is larger than the sum of its children's own naturals (500) by
almost exactly 8 x the default flow-box child padding - so the measure counts
padding the allocation does not apply. That is why `halign` and the spacer trick
both had nothing to work with: the strip is measured as full and allocated as
full while its contents sit at the left.

Two more measurements, both contradicting the story I wrote down first, and
recorded for that reason:

- **`.organ-strip > flowboxchild { padding: 0 }` does match.** Loaded in
  isolation against eight synthetic cells: 444px natural without it, **402px
  with it** - worth 42px, not the ~350px the 834 figure suggested. The
  stylesheet does what it says; it is not the missing 334px.
- `ORGAN_IDLE_CSS` in `organs.py` is a **second** stylesheet declaring
  `.organ { padding: 2px 6px }` and `.organ-label { font-size: 10px }`. Neither
  reaches the widget either: the cells allocate exactly their labels' natural
  widths (58px for "listening", whose text is 57px). Two stylesheets for one
  strip and one of them does nothing - worth knowing before editing either.

`Gtk.Center` and `Gtk.Alignment` are both gone in GTK4 and `Gtk.FlowBox` has no
`justify`, so this is not a one-liner.

**CLOSED 2026-10-09 - and the numbers above are why it was never closed.** The
two no-op attempts were real, and so is the fix: `OrganStrip.do_measure`
overrides `Gtk.FlowBox`'s widest-child-x-count measure with the sum of the
cells' own naturals, so the strip is allocated **exactly** what its lights need
and `halign=CENTER` finally has slack to work with. The `set_halign(CENTER)`
line is in the tree, it was written *before* `do_measure` existed, and it was a
no-op until `do_measure` landed. Nothing was left out; the second one was simply
built in the wrong order.

Measured on a real window (`tests/test_organ_strip_is_centred.py`, broadway):

| | `reported_natural` | content left | content right | verdict |
|---|---|---|---|---|
| as shipped, 1024px | 479 | 130 | 131 | **centred**, 0.5px off |
| `halign` -> `FILL` | 479 | 0 | 258 | 129px off |
| `do_measure` removed | **815** | 1 | 260 | 130px off |

815 is the exact figure this section recorded for the un-overridden flow box, so
the historical root cause is reproduced rather than approximated, and 129px here
against 86px at 1280px is the same defect at a different column width.

**Measure the cells, not the strip's own box.** Under `halign=FILL` the box is
trivially centred - left 0, right 0 - while the lights sit hard against the left
with 258px of nothing beside them. A centring check written against the box
reports "centred" for the broken layout and passes; the first version of that
test did exactly that and stayed green under mutation until the FILL control
was written.

**Three defects the second full run found, all of them mine.**

- **`show()` replaced `present()` on the activation path.** The start-hidden
  work (`_start_hidden` + `toggle-start-hidden` + `show-window`) swapped
  `self.window.present()` for `self.window.show()` in two places. `show` only
  maps a window; `present` maps it **and** asks the desktop to raise and focus
  it. The assistant therefore opened behind whatever was already in front, with
  nothing wrong-looking to explain it — and
  `tests/test_sense_scheduler_is_live.py` failed on a stub window that only ever
  implemented the older call, which is how it surfaced. Both sites are `present()`
  again, `show-window` included: "show the window" from a tray icon or a
  notification means raise and focus.
- **The Diff preview tests asserted on process-wide state they did not own.**
  `recent_changes()` reads the undo ring, and *any* earlier test that wrote a
  file through `write_text_file` or `edit_file` added a pre-image to it — so
  `assert len(pairs) == 1` was really asserting that no other test in the
  process had ever edited a file. Measured: 3 failures in a chunked run, 0 in
  isolation. The helper now empties the ring first, through the module's own
  `_save` so the on-disk format stays the one the module writes.
- **The slash-command menu had no test at all**, and it is the one part of that
  UI whose reachability nobody had ever checked: the registry tests assert each
  command runs, and nothing asserted that typing `/` opens the menu. New
  `tests/test_slash_menu_opens.py` drives a real window in a real main loop,
  inserts a real `/` through `insert_text()` (not `set_text()`, which emits no
  `changed`), and presses a row. Three mutations confirm it can fail: the menu
  never pops up (caught), it never pops down (caught), and `/diagnostics` is
  neutered (caught, 2/2 — after one false start recorded below).

  **The first version of that last assertion was vacuous and measured green
  against a dead command.** It asserted `"diagnostics" in window._surface_pages`
  — but the window **pre-builds every surface in an idle callback**, so that key
  is present from startup whatever the command does. Asserting on the click at
  all was also the wrong shape: a row *completes* the text (`/diagnostics `) and
  hands focus back, which is cline's behaviour and the reason the menu is a
  discovery aid rather than a one-click macro. It now asserts the completion and
  then that submitting it brings the Diagnostics page to the front.

**And two test bugs of my own that a green suite was hiding.**

- `_CONSENT_NAMES` in `test_skill_gates_are_enforced.py` was missing
  `vision_sense_enabled`, so a correct skill (`screenshot.py`, which gates by
  reading `config.vision_sense_enabled` — a property that returns
  `sense_allowed("vision")`) was reported as ungated. The control test then made
  it worse by demanding the *implementation detail* (`"sense_allowed" in names`)
  rather than that a gate was consulted at all. Fixed to
  `set(_CONSENT_NAMES) & gated`, mutation-verified: deleting the gate from
  `screenshot.py` fails three tests including this one.
- `test_paths_read_as_paths.py` asserted a path appeared unbroken in a label
  that **wraps mid-path at the hyphen** ("shani-\nchronoa"), so it read as "the
  panel stopped showing the path" when the panel was showing it perfectly.
  Pre-existing — it fails identically with `gui/style.py` unmodified from HEAD.

**A test kept reading a declaration out of a class that no longer holds it.**
`test_dead_ends_have_buttons.py::test_every_settings_target_is_a_page_that_exists`
parsed `inspect.getsource(SettingsWindow)` for a `register([...])` call and
checked five settings page ids against it. Two things moved under it, and it was
reading neither: the declaration became `_declare_families()` at module level
(still called at the bottom of the module, so still at **import** time — nothing
changed about *when* registration happens, which is why `pages.py` can now also
call it on demand for a lazy `--show-page`), and the call passes
`list(_SECTION_FAMILIES)` rather than an inline list literal. The class source
then contained no `register` call at all, so the scan read an **empty set** and
reported five working buttons as dead ends.

Measured both directions: it fails against the current module and passes against
the pre-change one, which is how a *reader* defect gets told apart from a
*behaviour* defect. It now resolves the module-level table and additionally
asserts the registry call passes that table — the stronger claim, because a
literal-list scan could not tell a complete table from one nothing registers.
Two mutations confirm it can fail: dropping a family id, and replacing
`list(_SECTION_FAMILIES)` with `[]`.

**`RESERVE_DISTINCT_KEYS` was discarding facts.** The reserve loop kept *one*
fact per key and `continue`d past every other, so two true facts about the same
subject lost one — and it broke a pre-existing ordering test. The reserve's
stated purpose is that a bulk of one key cannot starve another subject; two
passes (one per distinct key, then fill) gives that without dropping facts. This
is the failure this file keeps describing under another name: a loop written to
*reorder* silently *filtered*.

**`execute_tool(name, args, origin=…)` broke a two-argument test stub.** The
stub was out of date, not the call.

**`_apply_narrow_layout` re-opened the panels the person had just closed.** Its
"widening" branch ran on every width notification rather than on a transition,
so a late notification undid the toggle. Measured 2 of 5 runs failing with it,
0 of 5 without; now keyed on a `_narrow_applied` bucket.

**Two `test_approval_ux.py` tests proved grant scoping through
`bypass_immune`'s back door.** Both used `delete_file`, whose session grant is
now ignored on purpose (T2.6), so the narrow-grant property they were written
for had no witness left. They now run on `edit_file`, which is not immune, and a
new one asserts the immunity itself — through dispatch, because an assertion on
`evaluate()` alone reads the ignored grant as "granted".

**`list_services` called a healthy machine broken.** `failed_only=True` with
nothing failed returned "systemctl returned no service units, which is not
expected on a running system" — the anomaly sentence, on the one input where
an empty answer is the good one. The test that caught it had the mirror-image
fault: it required this machine to *have* a failed unit, so it failed on a
healthy box and would have passed on a sick one.

**One thing deliberately left alone, with the numbers.** `tests/test_sidebar_toggle.py`
is flaky for an environmental reason: it measures on-screen layout, this runner
has no compositor, so the sidebar's slide animation is advanced only when a
frame happens to arrive (4 of 6 runs pass with the harness fixes; 4 of 6 with
`window.py` **unmodified from HEAD**, which is how you show the residue is not
yours). Two harness fixes there are real and measured: the width is now *stated*
(`ChronoaWindow` defaults to 460x640, below the sidebar's collapse threshold, so
a harness that takes what it is handed measures the drawer contract while
asserting the column one), and a control asserting the header toggle is
unreachable in the drawer state was removed because today's measurement
contradicts it (at 1100px the overlay is not allocated over the header: header
y=0..46, sidebar 0x0, toggle mapped) — it also failed against `window.py`
unmodified.

**`test_at_1280_both_columns_are_back` was not flaky — it tested a path no
headless display can reach (2026-10-09).** It failed 5 of 5, and also with the
whole session's work `git stash`ed, so it was pre-existing and unexplained.
Measured rather than diagnosed:

| display | allocated | rail | result |
|---|---|---|---|
| `broadway :96` | **1024x768** | hidden | fails |
| the real X11 (`:0`) | 1280 | visible | **18 passed** |

**Broadway caps windows at 1024x768 and the rail appears above 1040px**, so the
wide-rail path sits sixteen pixels above what any broadway display can be — and
at the 1024px actually achieved the rail *correctly* hides. The product was
right on both; only the harness was wrong. `tests/test_now_rail.py` now picks the
backend **before** `gi.repository.Gtk` is imported, because GDK chooses one at
GTK init and no fixture can change it afterwards. With no wide display it
**fails and names the number** — never a skip, since a green skip would leave
the rail's entire wide path unasserted on every headless machine, which is where
it is least exercised. It is `pytest.fail`, the same "a skip reads as coverage
and is not" rule the sidebar test above turned on itself.

**The rail's threshold was not lowered to 900 to make broadway satisfy it.**
Measured: with `RailBreakpoint.apply`'s `max_width` at 900 the test *still*
fails on broadway, because the allocation cap is what binds — so the wrong fix
would have bought a failing test *and* a worse product constant.

**My own probe was wrong first, and the failure looked like the product's.** The
first version read `xdpyinfo`'s `dimensions: 1920x1080 pixels` as
`int(line.split()[1])` — the whole token `1920x1080` — so `int()` raised, the
`except` swallowed it, and the probe answered *"no display on this machine"*
about a 1920px screen while taking **15.1 s**. Fixed to split on the `x`; the
same probe returns `:0` in **0.1 s**. A probe that is always False is the same
defect as a test that always skips, and here it read as a confident verdict
about the product.

Its settling step is a **fixed pump, deliberately not a convergence loop**:
with animations on and no compositor the state after "show the panels" is
correct while the column measures 0px, and that state is *stable* — so "wait
until two readings agree" is satisfied by the broken frame, and the test then
reports a layout that has not happened. `gtk-enable-animations` off is what makes
it reliable (3 of 3 recovered, against 0 of 3 with it on); `queue_draw`,
`queue_resize`, `present()` and `set_default_size` were each tried as a nudge
first and none moves it, because the widget waits on the frame clock.

**And the run itself nearly produced nothing.** It was launched as
`pytest … | tail -60`; after 45 minutes the shell was still alive with an empty
log, because the pytest process had exited normally while a **forked child still
held the write end of the pipe**, so `tail` never saw EOF. `kill <that pid>`
recovered the entire summary. This is what the "44-minute run keeps getting
lost" note above actually was: **a suite whose output is piped is at the mercy
of any process that inherits the pipe.** Write to a file, then read the file.

## Two UI gaps verified by looking at them, and what looking found (2026-10-07)

The harvest work was verified by behaviour throughout — tests drove the real
paths and the mutations were confirmed to fail. That is not the same as having
*looked* at it, and this repository's own history is that a widget can behave and
still be broken: the organ strip passed its width test while rendering as one
run-on string. So both new surfaces were rendered and read.

**The slash-command menu** (`tests/test_slash_menu_opens.py` added first, because
it had no coverage at all). Rendered with the menu open: six named commands,
each with a plain-language summary, anchored under the composer — and one defect
the tests could not see. **A summary ran straight into the popover's rounded
corner** with nothing between the last letter and the border, so "Show what
Chronoa changed, file by file" read as a sentence cut in half. Ten pixels of
right margin; the menu went 398px -> 408px and now has visible padding.

Three harness facts worth keeping, each found by the attempt before the real one:

- **A `Gtk.Popover` is its own surface.** The first capture was a 1280x900 image
  of the *window* with the menu reporting `visible: True` and six rows, and no
  menu in it. Capturing the popover itself is what shows the menu.
- **A widget captured on its own looks invisible.** `WidgetPaintable` of the task
  card produced near-white text on transparency: the theme's foreground with
  nothing painted behind it. Indistinguishable from a legibility defect, and not
  one — the window has to be captured and the card cropped out of it.
- **Pump the loop before capturing.** Without a compositor nothing guarantees an
  allocation, and `snapshot.to_node()` on an unallocated widget returns `None` —
  "NO NODE", which reads like a rendering bug.
- Also: `ChronoaApplication` hardcodes the single-instance name
  `dev.shani.chronoa`, so a render collides with a running app and fails with
  "Failed to register: Timeout was reached". The harnesses use a minimal
  `Gtk.Application` with their own id, as `_render_one.py` already does.

**The task card.** Rendered with three seeded tasks: all three states read
correctly and are distinguishable **without colour** (ellipsis / circle / warning
triangle, plus the words "doing" and "blocked"). One real gap: the blocked row
said "blocked check the backup" and the reason was **only in a tooltip** — which
needs a hover or a keyboard focus, never appears on touch, and is in no
screenshot. `blocked_by` exists to say what is in the way, so "waiting on disk
full" is now on the row, dimmed, wrapping, with the tooltip kept.

**And the change immediately regressed something the render caught and the tests
did not:** the first version returned a fresh vertical box holding the two
labels and left the icon behind, so the next render showed "blocked" with no
warning triangle — the one marker distinguishing it from "doing" without colour.
The head box goes *inside* the row rather than being replaced by it. Both halves
are now covered: the test asserts the reason is in the rendered text **and** that
the blocked row still carries `dialog-warning-symbolic`, and each was confirmed
to fail when its half is removed.

## The suite, run in chunks because one long run keeps dying (2026-10-07)

A single full run is not a reliable way to get a number out of this suite on
this box: over the course of one afternoon it lost its output three ways, and
only the third is obvious.

| what happened | why | what it cost |
|---|---|---|
| `pytest ... | tail -60`, log stayed empty after 45 min | the pytest process had exited **normally** and a forked child still held the write end of the pipe, so `tail` never saw EOF | the whole summary, sitting in a buffer |
| SIGTERM twice | the process group was killed | 45 minutes each |
| `Fatal Python error: Aborted` at ~60% | `g_application_run` asserts inside `tests/test_setup_button_is_reachable.py:123` | everything after that test |

**Write the output to a file, not down a pipe**, and **run it in chunks**. Eight
chunks of ~36 files, one summary file each: **36 failed, 5845 passed, 39
skipped**, no abort. A crash then costs one chunk instead of the run.

Of those 36, **27 were the environmental list above**, unchanged. Ten were real
and are fixed; the residue after the fixes was re-measured over every file that
had failed, and came to **26 failed / 451 passed / 2 skipped — every one of the 26
on the documented environmental list** (browser_window_probe 7, setup_extras 6,
sense_idle 3, sandbox_seccomp 2, permission_decisions 2, everyday_skills 2,
sandbox_profiles_live 1, input_skills 1, portal_input 1, vision_sense 1).

The abort is **pre-existing and still unexplained**: that test passes alone
(12/12, three runs) and no file pair I tried reproduced it. A dead session bus
was my first hypothesis and it is **ruled out** — pointing
`DBUS_SESSION_BUS_ADDRESS` at a nonexistent socket and caching the connection
with `Gio.bus_get_sync` first still lets `app.run()` return normally. Recorded
as an open item rather than a fix.

### What the chunked run also showed: two order-dependent failures of mine

- **`test_settings_targets_resolve.py` had the same defect as
  `test_dead_ends_have_buttons.py`**: a generator that read the page ids out of
  the `SettingsWindow` class body yielded **nothing** once the declaration moved
  to `_declare_families()`, so every assertion built on it passed for the wrong
  reason. Fixed the same way, mutation-verified.
- **`test_skills.py::test_execute_tool_non_dict_arguments_are_ignored` picked its
  handler by insertion order**, and the first candidate is `airplane_mode` —
  which is consent-gated (`radio-control-enabled`, off by default). So the test
  passed or failed depending on whether an earlier test in the process had left
  that switch on: intermittent in a full run, never in isolation. It now picks
  the first **sorted**, **ungated**, non-read-only handler with no required
  arguments (`audio_output` today) — the point being that the claim is about
  argument handling, and a gated tool never gets far enough to exercise it.

## Six harness-UI gaps closed (2026-10-07)

Comparing Chronoa's UI against what opencode, cline, Roo-Code, OpenHands and
sayri ship surfaced six things we did not have. All six are now in, each
verified by execution rather than by the feature existing:

1. **Context meter** (`context_meter.py`) - opencode's
   `session-context-usage.tsx` + `session-context-breakdown.ts`. A headline
   (`781 of 8,192 tokens (9.5%)`), a per-segment breakdown (instructions /
   percepts / user / assistant / tool results / tool schemas), and a
   *usable* percent that reserves reply room. Two rules the comparison
   forced: when the window size is unknown the meter says `None` rather than
   inventing a percentage against a made-up denominator, and its divisor is
   *read from* `local_llm.CHARS_PER_TOKEN` - my first guess (4) was wrong;
   the shipped one is 3, and the test that caught it is permanent.
2. **Compaction says what it cut.** `compression.compress()` now reports into
   `last_elision()` (messages shortened, characters removed), the assistant
   fires an `on_compaction` sink event once per turn, and the transcript
   shows a dim row. cline's `CompactionRow`, OpenHands' `CondensationEvent`.
   A local assistant that elides silently looks like it forgot.
3. **A cloud-turn is announced.** `CloudLLMChain` tracks `last_provider`; the
   assistant fires `on_cloud_turn` once per turn; the transcript shows "This
   turn was answered by X, not on this machine." The privacy strip and the
   egress audit existed for this, but neither told a person their sentence had
   left - they had to have known they enabled a fallback at all.
4. **Slash commands.** `gui/commands.py` - kimi-cli's registry, cline's
   `SlashCommandMenu`. Six commands, each with a real action behind it:
   `/new /diff /undo /tasks /diagnostics /help`. `/undo` runs the real skill,
   so the permission layers are not bypassed. A command with no wired action
   is the dead-control class this repo keeps meeting; the test refuses it.
5. **The plan sits in the chat.** `gui/task_card.py` - OpenHands' 3-state
   `TaskItem`, placed above the composer where it cannot scroll away, hidden
   entirely when there is nothing outstanding, and *absent* (not blank) when
   its consent key is off or the store is unreadable - "you have no tasks"
   and "you cannot read them" are different facts.
6. **Tool output is collapsible.** This turned out to already be there
   (`ToolCallCard` has a toggle + revealer); a test pins it so it is not
   "fixed" twice.

## A full visual pass over every page, and what looking found (2026-10-07)

Every page of all three windows (chat, 21 panels, 7 settings sections, 17 wizard
pages, Help) was rendered at 1280x860 and read. `render_ui.py` gained
`page:<window>:<id>` (any registered page, through `pages.show()`) and is now
`NON_UNIQUE` - before that, a parallel batch registered one bus name and every
render but the first exited as a remote having written nothing. `panel:` also
never sized its window, so panel captures were a small window stretched up.
Batch: `xargs -P4` over `render_ui.py`, ~35s for all 49. For the light scheme
set `ADW_DEBUG_COLOR_SCHEME=prefer-light`: the `[theme]` argument sets
`gtk-theme-name`, which libadwaita's style manager overrides, so a "light"
render with it comes back dark.

Defects found only by looking, all fixed and each pinned by a test that was run
against the old code and failed:

- **Red for a choice.** The health vocabulary had no word for "off on purpose",
  so a shut consent key was "Needs attention": five sidebar rows red on a default
  install, and Devices printed "This is a setting on this machine, not a fault."
  under its own red word. `common.STATUS_OFF` ("Turned off", grey) now covers
  consent-off (calendar, devices, privacy mode, background mode, no senses
  granted); an empty calendar/memory/artifact list is `OK`; Machine counts only
  senses that *failed* (refused/absent are by design) and banners only those.
  `tests/test_status_off_is_not_attention.py`.
- **Wizard extras had four buttons, two primary**, and the halves routed
  differently: "Skip the rest" and Languages' "Next: Done" went past Review - the
  only page that downloads - so an extra picked on the way was never fetched;
  "Finish" went to Review. Each extra now has one primary "Back to the list" and
  a flat "Next: <extra>". Cloud keys had two primaries; Next now also saves typed
  keys. Pages are clamped to 640px. `tests/test_wizard_footer_one_primary.py`.
- **Settings had no way to a section but typing.** Section chips call the
  existing `_show_only_family`. `Gtk.SearchEntry` emits `search-changed` at once
  for empty text and *delayed* for typed text, so type-and-clear can deliver only
  the empty event; the chosen section is forgotten on `changed` instead.
  `tests/test_settings_section_chips.py`.
- **Help** drew a second title and close button under the title bar's; that
  space is now a search (`HelpWindow.filter`). Its "switch on *Speak answers
  aloud* to send a notification" was correct and read as wrong: the one key does
  both, so the label is now "Spoken replies and notifications".
  `tests/test_help_search.py`.
- Smaller: the user's bubble filled the row with text pushed right (now hugs
  right, accent-tinted); Setup's `system-run` icon is a gear in Yaru beside the
  Settings gear (now `system-software-install`; Help/Quick Ask icons swapped to
  the conventional ones; sidebar toggles use `sidebar-show[-right]`); panel
  subtitles were a centred narrow block over left-aligned rows; a banner's button
  sat flush on the window edge; Senses rows showed module ids (`cgroup`,
  `rfsense`) - now `common.SENSE_TITLES`, id kept in the tooltip; "Cloud fallback
  when Ollama is unavailable" now names the real condition (no local model
  answers - llama.cpp by default).

**Every tool card vanished from the chat when the reply arrived.**
`TranscriptView._replace_blocks()` removed every child of the turn, and the turn
also holds the tool cards and notice rows, which are added *before* the reply
text because skills run first. Unit tests built cards alone and never followed
one with a reply; a render of the real order showed the reply and no card while
the rail listed the changed file. It now keeps `ToolCallCard`s and `.turn-notice`
rows. On top of that, a single-file write (`edit_file`, `write_text_file`,
`find_and_replace` on a file) shows its changed lines in the card
(`blocks.InlineDiff`, from the undo ring) with "Show full diff" into the panel.
The Diff panel itself, rendered with real content for the first time: it loaded
only at build (panels are cached, so it went stale - now reloads on `showing`),
its count toolbar was a third column over the file list, its diff scroller was
one line tall, every hunk header was wrong (old side read from a removed line's
`-1`), and its fills were light-theme hex colours (white on near-white on a dark
desktop). All in `tests/test_inline_diff_in_chat.py`, driven through the real
`edit_file` skill.

**Layout, conversations and sidebar (same day, third pass).** Renders now run on
a headless `gtk4-broadwayd :5` (`GDK_BACKEND=broadway BROADWAY_DISPLAY=:5`, unset
`DISPLAY`/`WAYLAND_DISPLAY`): on the real display every render window takes focus
and the person's typing lands in it (a filter field rendered holding "ho").
Broadway caps windows at 1024x768 and scales anything larger, so measure widths
with a probe, not a picture. Found and fixed:

- **The header bar needed 632px** - wider than the 600px narrow layout - so every
  narrow window clipped the chat's right side. Quick ask / Browse / Help fold into
  a "More" button below the breakpoint (Setup and Settings stay visible, which
  `test_setup_button_is_reachable` requires); the title ellipsises; the split
  view's automatic back arrow (a duplicate of the sidebar toggle) is off. 443px
  narrow, 483px wide.
- **The organ strip could not centre** because `Gtk.FlowBox` reports natural
  width as widest-child x count (815px for 482px of lights). `OrganStrip.do_measure`
  reports the real sum; it now centres and still wraps.
- Conversation and composer are clamped to 860px (`widgets.reading_width`).
- **Conversations panel** rows were a centred bold `• title  (02 Oct 00:42)`;
  now a title plus `Today 23:35 · 2 messages · Current`, a warning mark for an
  unfinished last turn, and inset margins.
- **Sidebar sections** follow the organ lights: Remembering, Acting, Sensing,
  Thinking, Health and trust (the old four had Models under "This machine" and
  Skills under "What Chronoa did"). Rows ~40px instead of 50. "Model" is now
  "Answering now", so it no longer sits beside "Models" with no difference.
- **Five icon names Adwaita does not have** - `chat-symbolic`,
  `office-calendar-symbolic`, `emblem-documents-symbolic` (the "remembering"
  light), `emblem-ok-symbolic` (the tool card's tick),
  `audio-input-microphone-muted-symbolic`. The existing icon checks ask
  `has_icon` on the *installed* theme, and this machine is Yaru, so all five
  passed while drawing blank boxes on GNOME. `tests/test_sidebar_icons_exist_in_adwaita.py`
  reads Adwaita's files. Also de-duplicated: Artifacts/Export (one arrow),
  Background mode/Desktop/header Settings (three gears), Memory/Artifacts.
- `test_sidebar_toggle.py::test_two_presses_return_exactly_where_it_started` is
  **flaky on untouched HEAD** (4 of 12 runs), the narrow-layout race recorded
  above - not caused by this pass (1 of 12 with it).

- **Three capture paths left the organ strip dark.** `screengrab` lights
  "looking" and `AudioRecorder` lights "listening"; `capture_video` (ffmpeg on
  /dev/video directly), `scan_document`'s scanner, and the wake word's own
  always-open mic stream lit nothing - the camera could record, or the mic stay
  open all day, with the strip idle. `body.lit()` is a context manager that
  lights an organ for a `with` block and always puts it out; all three use it.
  `tests/test_capture_lights_the_strip.py` (fails on the old files, measured).
- Also failing and **not from this pass**: `test_sidebar_toggle.py::test_the_panels_open_beside_the_chat_and_come_back`
  fails 4/4 on untouched HEAD; `test_capabilities.py::...other_group` fails on
  `skills/browse.py`, an untracked file from a concurrent session.

**Fourth pass (2026-10-08): rail, panels, mode strip.**

- Rail sections are quiet cards with real small-caps headings (the CSS comment
  promised letterspacing and uppercase; neither was set), and Context has a
  `Gtk.LevelBar` when the window size is known.
- "Nothing here yet" is `STATUS_OK`, not `STATUS_UNKNOWN`: Activity, Diff,
  Triggers, What it has learned and Conversations all showed amber "Could not
  determine" for an empty store read without error. Diff's empty page said it
  three times; it is now one empty state.
- Desktop headlined red "not answering" for being off the bus - the normal state
  between searches, as its own row says. Red now means the service file is
  missing; off-the-bus with the file present is OK.
- Privacy's sense-consent rows and Skills' rows used raw ids; now
  `common.sense_title()` / `capabilities.tool_title()`, id kept in the subtitle.
- Models' view-switcher tabs had no icons (`add_titled`), drawn as blank cards.
- Artifacts: status row first, count beside the search, a real empty state - and
  **its "open folder" button never worked**: `subprocess` was never imported and
  a broad `except` hid the `NameError`. Now `Gio.AppInfo.launch_default_for_uri`.
- Mode strip: **Talk over** (`toggle-barge-in-vad`) and a **reply-style** menu
  chip on a new stateful `app.reply-style` action (Ordinary / Brief /
  Explanatory; applies from the next reply, verified through the registered
  app). Below 560px the chips go icon-only (`set_compact`) - with words the strip
  needs 512px and the window can be 380px. Permission modes were left as they
  are: Explore duplicates Plan mode by design, and Don't ask is for unattended
  turns.
- `OrganStrip.do_measure` must return -1 baselines for the horizontal axis, or
  GTK warns "reported a horizontal baseline" on every layout.

**What the backend can do that the UI did not show (audit, 2026-10-08).**
Four layers checked by script, not by reading: backend modules with no GUI
reference (33 of 101, mostly plumbing or reached through skills), schema keys no
UI code names (55 of 161, nearly all sense keys the Senses panel builds by
f-string), app actions with no control (none - `show-page` is for D-Bus), and
binaries. Fixed:

- **A custom model server had no field.** `custom-llm-base-url` / `-model` /
  `-api-key` are read by `app/brain.py` (first in the cloud chain) and shown by
  "Answering now", and nothing could set them. Settings -> Privacy now has
  "Your own model server"; the key goes through `set_api_key` (keyring).
- **Every text field in Settings was two fields.** `_entry` packed a `Gtk.Entry`
  inside an `Adw.EntryRow`, which is itself a field (rendered: two boxes per
  row), and the secret reveal toggle called `set_visible` - press 1 revealed
  nothing, press 2 hid the field (run, not inferred). Now one `EntryRow` /
  `PasswordEntryRow`. `tests/test_settings_text_fields.py`.
- **Your own `/commands`** (`~/.config/shani-chronoa/commands/*.md`) worked when
  typed but were missing from the `/` menu; they are listed now, marked "yours",
  summarised by their first line. A **rules file** that is active now shows in
  the rail's Posture card.

Then wired, same day:

- **Connections** panel (`gui/surfaces/connections.py`, section Acting, gear to
  Settings -> Privacy): whether the MCP server can run here (it needs the `mcp`
  package, absent on this machine - the panel says so), the exact `claude mcp
  add` line and `mcpServers` JSON with Copy buttons; for Telegram/WhatsApp,
  whether the `gateways` setting accepts each, which bridges are running (read
  from /proc), and the command that starts one. It never asks for a token: the
  bridge reads its own environment by design, so Chronoa never holds one.
- **What it has learned -> Background passes**: Dream (the daemon's pass, run
  now, with when it last wrote one), Reflexes (all five listed, "check now"),
  Compare predictors (`model_zoo.compare/render/recommend`, previously imported
  by nothing). Off the main loop; the whole report is shown.
- `tests/test_surfaced_backends.py`: builds both panels and drives the real
  backends, including a real process named like the bridge (killed by PID).

Further gap layers checked the same day: every skill is in Help (182/182); no
GUI button is wired to nothing (AST scan; the five flagged are wired by their
callers); every app action has a control. Found and fixed:

- **Replies never streamed.** `ConversationMixin` passes only `sink=` and puts
  `_on_reply_text` (live text *and* sentence-by-sentence speech) on the sink;
  `handle()` read only its own `on_text` parameter, so with a streaming backend
  the reply arrived whole. Measured with one fake model: 0 pieces via `sink=`,
  2 via `on_text=`. `handle` now takes the sink's. `on_state` is declared on
  `EventSink` and neither fired nor subscribed - unused, not broken.
  `tests/test_reply_streams_through_the_sink.py` (fails on HEAD's assistant).
- **Conversations could not be renamed** although `conversation_store.rename`
  exists (and protects the title from the model). Pencil button per row.
- **Trigger rules could be deleted, not made**, outside chat. The Triggers panel
  has an "Arm a rule" form that calls `tools.execute_tool("manage_triggers")` -
  so consent, argument checks and the destructive refusal are the chat path's.
  Probing it needs a settings backend a child process can read: skills run in a
  sandboxed child, and `GSETTINGS_BACKEND=memory` is per-process, so a grant
  made in the probe was invisible to the skill and read as "turned off".

Third gap round (function level and reverse direction, same day):

- The arm form also arms **event rules** (all 19 `EVENT_TYPES`: screen lock,
  USB plug, schedule, ...) with a source field; a bad source gets the skill's
  own list of what that type accepts.
- **Branch a conversation** (`conversation_store.fork`, chat-only before): a
  button per row that copies and opens the copy, leaving the original intact.
- **Gears where a panel names a switch it could not reach**: Desktop ->
  Privacy (global shortcut, document search), Machine -> Senses (its refused
  rows), Activity -> Tool activity. `NO_GEAR` in
  `tests/test_settings_targets_resolve.py` listed Desktop and Machine on
  reasons the code contradicts; the note there now records the evidence.
- Test-isolation trap found on the way: `ChronoaApplication` registers a
  spoken permission presenter in `ask_bridge` at startup and nothing clears it,
  so any later in-process call that needs consent *asks* and blocks. Tests that
  expect a skill's own refusal must clear `ask_bridge._presenter` (see
  `tests/test_surfaced_backends.py::_nobody_to_ask`).
- Checked clean: no setting in the UI that nothing reads (the three hits are
  read by the wizard and the organ strip); session grants are listed and
  revocable in Settings > Approvals; the remaining store functions without UI
  are plumbing, or `goals.py`, which is unwired on purpose (no producer yet).
Pre-existing failures seen in this pass, all failing on untouched HEAD too:
`test_config_gsettings.py::test_api_key_defaults_are_empty_not_quoted`,
`test_slash_menu_opens.py::test_typing_a_slash_opens_the_menu...` (flaky, 1/2).

Not changed, noted: Machine's readings are monospace blocks outside the row
cards; the 8-word organ strip shows no state at rest; the Models switcher shows
a red "blocked" glyph on both tabs.

## Twenty skills from the matrix shortlist (2026-10-07)

Picked from `chronoa-matrix.json`'s `ideas` after checking each against the
existing skills (`open_surfaces` measures calls, not answers - see above).
Read-only: `snapshot_status` (shani-deploy `--status --json` + the snapshots
sense), `security_status`, `list_containers` (adds `distrobox list`),
`list_vms` (`virsh`, session and system separately), `boot_report`
(`systemd-analyze time`/`blame`), `disk_health` (LUKS found by walking `lsblk`
ancestors), `temperatures`, `usb_devices` (adds `boltctl`), `driver_info`
(`/proc/modules`, `/sys/module`, `device/driver` links), `list_fonts`
(`fc-list` + `fc-match`, substitutions called out), `photo_metadata` (own stdlib
EXIF reader - no exiv2 on the image, Pillow not a dependency), `crash_report`,
`login_history` (`last`, else wtmp parsed directly). Changing: `audio_output`
(`wpctl set-default`), `default_apps` (`xdg-mime`/`xdg-settings`), `pdf_pages`
(poppler merge/split/extract; no rotate - `qpdf` is not on the image),
`print_queue` (cancel), `set_hostname`, `set_locale`, `speed_test`.

Rules they follow, so the next one does too:

- **A skill that reports a sense's facts shares that sense's switch**, checked in
  the skill's own source (`sense_reading.py` explains why; the precedent is
  `git_inspect`). Only the sense half of a mixed skill is withheld - turning the
  snapshots sense off hides the snapshot list, not the deploy state. Found by
  running them for real: five of the eight senses gate inside `_run` and three do
  not, so the first version's "consent is not consulted" docstrings were false
  for five of them.
- `audio_output` and `pdf_pages` are ungated, by precedent: `set_volume` for
  one, `convert_document` (writes only new files) for the other. The other five
  changers each have their own key, default off; `speed_test` also refuses in
  privacy mode, which defaults on.
- `set_locale` passes back every existing assignment: `localectl set-locale`
  replaces all of them with exactly the list given, so changing LANG alone
  would drop LC_TIME. Mutation-tested.
- `pdf_pages`' post-condition requires the output to be fresh: a refused call
  leaves an older file of the same name, and matching its page count would
  verify work that never happened. The first control could not catch this (a
  non-PDF fails the page count for another reason); it is now a real 4-page PDF
  dated in the past.

**`mcp.py` builds a Python signature in schema property order**, so a required
property listed after an optional one raises `ValueError: non-default argument
follows default argument` and takes the **whole MCP server** down, not just that
tool. `default_apps` hit it; its properties are reordered. The fix belongs in
`mcp.py` (sort required first) and is not made here.

Verified: `tests/test_matrix_skills.py` (51, every skill through
`execute_tool_outcome`; 16 mutations, all caught), `tool_select` picks the right
skill for 28 of 28 natural phrasings, and shani-testbed
`slot-tests/chronoa-matrix-skills.sh` on `@blue` (`shanios-20261006-plasma`):
**29 PASS, 0 FAIL** - every binary present, shani-deploy's status read, a real
poppler merge VERIFIED, all five gated paths refuse, and the control names
`fontconfig` for a hidden `fc-list`.

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

## A real task end to end in the real app, and what it took (2026-10-08)

Driven by recording demos: first a scripted one, then the **real
`ChronoaApplication`** with a free cloud model (Kilo Gateway via the custom
model-server fields, `kilo-auto/free`). The final run: one typed request ("book
the cheapest Boston -> London flight on blazedemo.com, weather, rupees,
itinerary, remind me tomorrow 9am") -> 27 tool calls chosen by the model ->
booked (demo site, no real payment), weather, INR, HTML itinerary, reminder.
Every gap below was found by a run failing, fixed, and re-run.

**Harness gaps (each one ended the task):**
- `MAX_TOOL_ROUNDS` 4 -> 40 and `MAX_TURN_SECONDS` 300 -> 600. Four rounds cut
  every multi-step task off; the loop detector, per-tool budget, wall clock and
  Stop remain the bounds. `test_multi_step_turns.py`.
- The out-of-rounds answer (sent without tools) was the model's next call as
  raw markup; now `assistant._out_of_rounds` says the steps ran out instead.
- `tool_select.wants_a_browser`: a request naming a site or a page action
  (book, fill in, sign in, order...) is offered `browse`. Before, the model
  tried to book with `web_search`.
- Text tool calls: `recover_tool_calls` now also reads Qwen3-Coder XML
  (`<function=x><parameter=k>v</parameter>`) and a reply that is only a JSON
  call, and the **cloud** client now runs it (its `postprocess` returned the
  message unchanged). Only offered tools; quoted JSON is left alone.
  `test_text_tool_calls_recovered.py`.
- `reaction.py`'s repeat check ("needs a person") never reached a person:
  `tools._reaction_refuses` turned the question into "Not run". It now asks
  through `ask_bridge` (person's own turns only, never from the GTK main
  thread), and an approval restarts that tool's count. `test_reaction.py`.

**browse in the in-app browser (fork-built, then verified here):** runs
in-process while a GUI browser window can serve it (`tools._runs_locally`),
egress-checks every model navigation incl. clicks and redirects, refuses
`evaluate` and `file://`. Added here: a visible cursor/ring/ripple and an
activity strip in the window; typing in visible chunks that carries on into the
focused field when a framework re-creates the input (Wikipedia did); smooth
scroll + `direction`; a click on a hidden element is an error naming the
form's real button (it used to report success); CAPTCHA pages are handed to
the person, never solved; **typing into a card field or pressing a
Pay/Purchase/Place-order button asks the person** (once per site for 5 min;
nobody to ask = not done). `test_browse_in_app.py` (real window on Broadway).

**Skill bugs the recordings showed on screen:** `reminders` refused its own
schema example "tomorrow 9am" and built "tomorrow" on today's date;
`create_document` HTML dumped markdown as one paragraph (now rendered, escaped
first). `test_reminder_times_and_html_documents.py`.

**Window control (`shani_chronoa/windows/`):** one API (list/find/focus/close/
minimize/maximize/fullscreen/move/resize/set_workspace) over GNOME (the shipped
`usr/share/gnome-shell/extensions/chronoa-windows@shani.dev` - enabling it is
the permission), KWin scripting, X11 (xdotool, wmctrl fallback), and the
AT-SPI bus. Measured on GNOME 50: GTK4 frames expose `window.close/minimize/
toggle-maximized` and those work; nothing focuses or moves through AT-SPI;
GTK3/Chromium expose no window actions. New skill `arrange_window`.
`test_windows_gnome_live.py` runs a **private headless gnome-shell** (own
bus/HOME/runtime dir, `--no-x11`) with real windows and drives the skills
through `tools.execute_tool`. **KWin and X11 backends are written but not yet
run against a real KWin/X server.** sway/Hyprland deliberately not done.

**Recording:** GNOME's Screencast in the headless shell produced one frame
for a minute of activity - not usable. Windows record themselves instead
(`Gtk.WidgetPaintable` -> `render_texture` -> PNG at ~6 fps, joined by
ffmpeg at the frames' real timestamps). A fully covered GTK window on Wayland
stops being drawn, so windows are laid out side by side (`_Shell(monitor=)`).

## Voice, measured end to end through real audio (2026-10-08)

`tests/test_voice_loop_live.py` and `demos/record/record.py voice` run a private
PipeWire graph: `mic_feed -> demo_mic` (a virtual microphone) and
`demo_speaker -> speaker_tap` (a virtual speaker). Spoken WAVs go in, Chronoa's
voice is recorded out, and both directions are scored by word error rate. A
transcript that merely exists is not evidence: "two hundred dollars" came back
as "to $100".

**Bugs this found and fixed:**
- **No named voice style was ever applied.** `voice_style.selected_style`
  called `config.get_string`, which ChronoaConfig does not have. The
  AttributeError was swallowed as "unstyled". The same call made the Settings
  "Voice" style row fail to build, so it never appeared. The unit tests' fake
  configs had `get_string`, so they passed. Now `config.get`, with a
  real-config test and a control.
- **The window froze for about 8 seconds after start.**
  `_seed_status_dots` built every sidebar panel in one idle callback,
  Diagnostics' real probes (a screen capture) included. The mic press waited
  behind it, and a spoken request was lost before recording began. Now one
  panel per main-loop turn, and Diagnostics has `PREBUILD = False`.
  `test_startup_does_not_freeze.py`, with a control.
- **Every spoken request waited 4 seconds at the transcription gate.** Its
  busy probe read the window state, and the voice path sets THINKING itself
  just before transcribing. It now asks `VoiceMixin._model_turn_in_flight`
  (`_turn_future`). `test_speech_gate.py`.

**Measured with whisper `base`:**
- **Hearing:** WER 0.00–0.29 for the synthetic "person" voice. Wording
  matters: "at nine" is heard as "time", "at nine o'clock" exactly.
- **Speaking:** espeak-ng (the fallback; no Piper voice in the test profile)
  scores WER 0.35–0.47 with numbers lost, so the word-for-word speaking test
  skips under espeak-ng and runs once a neural voice exists.
- **espeak variants:** `+m3` at 160 wpm 0.27, against the default `+f3` at
  0.39. Changing the default voice's gender is a product call, not made here.
- **Styles** (with SoX) shape timbre: 0.33–0.42, inside the noise.
- **SoX is only an optdepend.** Without it, every style, plus pitch, tempo
  and rate, is a silent no-op.

**Voice engines compared** (`test_voice_engines_measured`, 8-thread laptop CPU,
no GPU; voices installed once into the git-ignored `cache/data-home/`):

| engine | WER | first sentence ready | RTF |
|---|---|---|---|
| espeak-ng `+f3` | 0.39 | 0.02 s | 0.00 |
| **Piper Lessac** | **0.18** (0.00 on the reply sentence) | **0.47 s** | **0.11** |
| Kokoro (int8, 4 threads) | 0.41 (dropped words) | 9.9 s | 2.0-2.4 |

Kokoro reloads its model in each per-sentence `sherpa-onnx-offline-tts` call
(about 3.4 s of load). Without that, synthesis alone is still RTF ≈ 1.3, so a
persistent worker would not make it keep up on this class of CPU and was not
built. It stays opt-in; Piper, which setup's Voice page installs on every path,
is the voice to use. The Kokoro thread count is now `min(4, cpu_count)`, not
a fixed 2: 4 threads were fastest, and 8 were slower again.

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

> **Superseded (2026-10-08).** The live list is `ARCHITECTURE-TARGET.md` Part 4,
> mirrored as data in `organism.STANDING_DECISIONS` and shown in the app on the
> Inventory panel under "Deliberately not built";
> `tests/test_standing_decisions_are_shown.py` fails when the two drift. Of the
> items below, the tray and background mode have since been built (`app/tray.py`,
> `daemon.py`) and sherpa-onnx is in use (Kokoro); the MCP client stays refused.

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

## The Siri/Google parity batch shipped a skill that could never run (2026-10-08)

Six builders were launched in parallel to close the gap between what Siri and
Google Assistant can do and what Chronoa could. The session hit a spend limit
mid-build; the files landed, the wiring did not. Four of the new skills
(`alarm`, `calendar_edit`, `maps`, `news`) had **no test file at all**, and
checking what they actually did turned up three defects that only running them
showed.

**`calendar_edit` refused every call, and the switch it named did not exist.**
Measured, through the real dispatch path:

```
create -> Refusing: changing your calendar is turned off
          (enable 'calendar-write-enabled' in Settings). Nothing was changed.
```

`calendar-write-enabled` was **not in the schema at all**, so `get_bool` returned
its Python default `False` for every value, and there was no row in Settings to
turn it on either. A permanent refusal wearing the shape of a permission - the
same class as the `heard-sound` sense above, where the only switch that granted
it was not on screen. This is worse than a missing feature because the skill
looks configurable: the model advertised it and the refusal explained itself.
Fixed by adding the key (default `false`), the Privacy row beside the read
switch, and the `GATED` entry. Verified by granting it: the skill then reaches
the calendar service and reports the honest environmental reason (no Evolution
Data Server bindings on this box) - a *different* sentence, which is what shows
the gate opened. `tests/test_parity_skill_gates_are_reachable.py`.

**`eds_calendar._escape` never escaped a semicolon.** Written as a backslash in
front of `;` in a non-raw string literal, which Python reads as just `;`. A
`SyntaxWarning` on every import (visible) and a malformed property value for any
title containing one (not visible). The other four escapes in the same call were
correct, which is why only this one was wrong.

**Eight of the twelve new tools fell through to the `Other` help group**, failing
`test_capabilities.py::test_no_builtin_skill_lands_in_the_other_group` - a test
that was already red on HEAD for an unrelated untracked file, so the failure was
easy to mistake for the known one. The full name list is the tell: the assertion
prints all eight.

**A documented invariant was nearly broken by the obvious fix.** `calendar_edit`
was in neither `MUTATING_TOOLS` nor `READ_ONLY_TOOLS`, so `cli_matrix`
classified it as neither actuator nor reader and no post-condition column was
computed for it. Adding it to `MUTATING_TOOLS` is the intuitive repair and is
**wrong**: the module states that a consent-gated tool must not be in that set,
because `tool_annotations` has a separate gated branch that already answers
`read_only_hint: False` and the two branches disagree about `idempotent_hint`.
`toggle_wifi` and `take_photo` were gated too, so all three had to stay out.
`alarm` and `routines` are ungated and are in it - `alarm` has a post-condition,
and `routines` gained one that re-reads the saved store, because a writer that
reports success for a store it never wrote is its own failure mode.

**A duplicate dict key had been silently killing a help label.** `browse`
appeared twice in `capabilities._GROUPS` with two different menu labels ("Use a
web page: click, type and read it" and "Drive a web browser"). Python resolves a
duplicate key to the last one, so the better label was dead with no error in
either direction - the table is correct as far as any reader can tell, and the
Help window showed the worse text. `python3 -m pyflakes` names it; nothing in the
suite did. Worth remembering that pyflakes is already this repo's declared lint
(AGENTS.md, "Lint with pyflakes") and a duplicate key in a data table is exactly
the class it is for.

**Two tests that could not fail, found by mutation rather than by reading:**

- `test_a_refused_confirmation_is_reported_as_a_refusal_not_as_a_change` asserted
  that the skill *asked*, so a mutation that skipped the confirmation while still
  calling `_confirm()` stayed green - the question was still asked, the answer
  ignored. It now asserts on `cal.remove_event` never being called.
- `maps`' location-gate test asserted the answer contained the word "location",
  which the *environmental* refusal on this box ("This computer's location is not
  available") also contains. With the gate deleted the test passed. The
  `locate` probe is now stubbed to **succeed**, so consent is the only thing that
  can stop the lookup.

**Three test bugs of my own, each recorded because the failure pointed at the
code instead of at the test:** a stub written with `netjson.get_bytes`'s
signature where `news._fetch`'s was needed (recording a params dict where a URL
belonged, so the section-feed assertion failed as if routing were broken); a
haversine band tightened to a distance remembered as ~115 km that is really
120.2 km, and a second pair asserted at ~1380 km when it is 735; and a store
reader that raised on an absent file, so "nothing was stored" assertions failed
with a `FileNotFoundError` that looked nothing like the bug.

Run: 224 passed across the parity, capability, consent, annotation and
post-condition suites. Each skill's gate, refusal and post-condition confirmed by
reverting it and watching the test fail.

## The wearable read twice; `listen` had to be rebuilt on bluez's own API (2026-10-08)

Asked how Da Fit changes smartwatch settings over Bluetooth, then asked to find
the paired device. Doing the second thing is what found four bugs in the first.

**The device is MoYoung.** Read through the real path, live:

| | |
|---|---|
| `FB BGS002` | `B3:69:73:62:B2:B6`, connected, RSSI -67, 9 services / 21 characteristics |
| Manufacturer Name | **`MOYOUNG-V2`** |
| Firmware Revision | `JLQFNJFD1.0` · Software `MOY-7OT4-2.0.1` · Sensor location `chest` |
| services | 0x1800, 0x1801, **0x180D**, 0x180F, 0x180A, **0x190E**, 0xFEEA, 0xFEE7, **0xAE00** |
| vendor command channel | **0xAE01 `write-without-response` / 0xAE02 `notify`** |

So the earlier research was right about the vendor and this is that vendor. **And
0xAE01/0xAE02 is the closed MoYoung protocol**, the one whose framing lives in a
closed-source AAR — see the Dart plugin's `MethodChannel.invokeMethod` chain with
zero UUIDs in its 93 KB Java layer, and `McuPlatform`'s six MCU dialects. Its
*shape* is now known and still not safe to drive: an unknown write to a wearable
is how firmware is bricked, so it is not implemented and that is a decision, not
an oversight. 0x190E (Phone Alert Status) is the GATT half of what
`bluetooth_call` does over HFP.

**`read` was truncating every multi-byte value.** `_READ_VALUE` was
`([0-9a-fA-F]+)`, which stops at the **first space** in gatttool's
`Characteristic value/descriptor: 06 4a 00`. Battery Level (0x2A19) is one byte,
so it read correctly and hid it completely — while a firmware string came back
as one character. Before/after on real hardware: `4a` → **`JLQFNJFD1.0`**,
`4d4f594f554e472d5632` → **`MOYOUNG-V2`** instead of "M".

Formats are taken from the binary's own strings, not from what a BLE tool is
assumed to print — `strings $(which gatttool)` gives both: a read is
`Characteristic value/descriptor: <bytes>`, a notification is
`handle: 0x0022 <tab> value: <bytes>`. The first version of the notification
pattern matched a *different* tool's format entirely and captured one byte, so
every live reading arrived too short to decode.

**`decode_heart_rate` had flags bit 0 backwards** (bit 0 is uint16, not uint8),
so **every 8-bit measurement** — nearly all of them — was refused with "the
measurement did not say whether the rate is 8 or 16 bit", the sentence you write
for a device that failed to set a bit rather than one that correctly cleared it.

### `listen` could not have worked, because gatttool cannot see a device bluez holds

The whole `listen` action was built on `gatttool --listen`, and on a real desktop
it fails **always**: gatttool opens a connection of its own, and bluez holds the
link to any in-range paired device by policy. Measured with the device connected
at RSSI -67, every call answers `Device or resource busy (16)`. Disconnecting
first works, but bluez reclaims the device and the next call fails — so it would
have worked in the seconds after a manual disconnect and nowhere else.

It now goes through bluez's own `org.bluez.GattCharacteristic1` first, with
gatttool as the fallback, and **says which route it used**. Four traps, all
measured, all silent:

- **`busctl get-property` takes the service name** —
  `get-property SERVICE OBJECT INTERFACE PROPERTY`. Omitted, every property
  returns empty, so **all 21 characteristics of a connected device read as
  absent** — a lookup that silently finds nothing.
- **`busctl tree` takes a service name, never an object path.** Given a path it
  prints `Invalid bus service name: <the path>`. The whole tree is fetched and
  filtered.
- **bluez is on the *system* bus.** `busctl --user tree org.bluez` answers "not
  provided by any .service files"; `--system` lists it owned by `bluetoothd`.
- **The method name moved between bluez versions**: the installed daemon has
  `StartNotify`, and `StartNotifications` is refused with `doesn't exist`.

`busctl monitor` is **polkit-denied** (`Access denied` on `BecomeMonitor`), so
the route polls the cached `Value` property at 200 ms rather than watching
`PropertiesChanged` — unprivileged, and a heart rate notifies at ~1 Hz.

### Two bugs that fix created, and the one that mattered most

- **`ay 0` is an *empty* array, not a zero reading.** The first version read it as
  the byte `0` and reported **"Battery Level: 0%"** for a device that had never
  sent anything — a believable, confidently wrong number about a battery, from a
  change made to *fix* a failure. Introduced by the bluez work.
- **`ay 1 00` parsed as `100`**, because the array *length* was taken for a byte,
  so every real value would have been off by one leading byte.
- A notify session left subscribed holds the connection, so `StopNotify` is in a
  `finally` and asserted to be called.

**And the device confirms why `read` can never answer heart rate**: bluez reports
`0x2A37` with `Flags: "notify"` — no `read` property at all. `read` on it is not
slow or flaky, it is impossible, which is why the refusal now names `listen`.

`0x180A` and `0x190E` were both advertised by that device and came back as
`UUID 0x180a` / `UUID 0x190e`. **Both were then checked against
`NordicSemiconductor/bluetooth-numbers-database` `v1/service_uuids.json`, the
SIG's adopted-number list, and one of the two names this file had recorded was
wrong:**

- **`0x190E` is not Phone Alert Status Service — `0x180E` is.** The name was
  right and the UUID was wrong, written from memory earlier the same day. The
  wearable advertises `0x190E`, which is **not in the SIG's adopted list at
  all**, so it is now reported by its number. That is the exact failure this
  module documents — a name attached to the wrong UUID reads as fact and nobody
  can tell it was invented. It also *shadowed* the real gap: `0x180E` was
  missing from the table the whole time.
- `0x180A` = Device Information: confirmed correct.
- Three added from the same source while checking: `0x1802` Immediate Alert,
  `0x1811` Alert Notification Service, `0x1812` Human Interface Device.

**`0x1811` is Alert Notification Service, not the Health Device Profile.** Android's
`BluetoothHealth` class is HDP, and HDP is **not a GATT service** — it is a
classic Bluetooth profile discovered over SDP. So Android's health-device support
maps onto a protocol `bluetooth_gatt` does not speak at all, which is a real
boundary and not a gap in the UUID table.

`0x0003`/`0x0004` remain named from the HID *usage* specification rather than
the SIG list, because they are HID usage descriptors and the SIG list assigns
those numbers differently (`0x2A4B` is "Report Map" there). Two numbering spaces,
deliberately distinguished in a comment.

**`bluetoothctl` on this box exposes no SDP at all** — measured: its menu has
`scan`, `devices`, `connect`, `disconnect` and nothing else; `search-services`
and `register-service` are absent. `rfcomm` *is* installed, so classic-Bluetooth
serial is available as a binary while SDP is not available through the menu.

### The manifest could not be built, and the suite could not see it

An apostrophe inside a single-quoted `optdepends` description —
`'pulseaudio-utils: pactl, for bridging a Bluetooth call's audio ...'` — closes
the quote early. `bash -n` reports `syntax error near unexpected token '('`, so
**the whole PKGBUILD stops parsing and makepkg cannot build the package at all**.
It was introduced in this session and caught only because a packaging test
failed with an unrelated-looking `ValueError: No closing quotation` from
`shlex.split`.

Every existing check in `test_packaging.py` reads the manifest as **data** — a
regex for the array, `shlex` for its entries — and both are blind to this.
Worse, `bash` itself then evaluated every entry *after* the apostrophe as
commands, so `sox` and the RHVoice voices were no longer declared at all and
`tts.py apply_timbre` would have been a permanent no-op on any real install. The
guard added is `bash -n`, the only one of the three that names the real problem,
plus a named check for the specific mistake.

Run: **301 passed** across the bluetooth, capability, consent, annotation,
post-condition and packaging suites. **20 mutations** (14 for the skill, 6 for
the manifest and the new bluez route) confirmed to fail.

## A Raspberry Pi HFP write-up, checked rather than believed (2026-10-08)

[Automating Calls on Real Mobile Devices with Bluetooth HFP on a Raspberry
Pi](https://sipfront.com/blog/2025/03/automating-calls-on-real-mobile-devices-with-bluetooth-hfp-on-a-raspberry-pi/)
(March 2025) is the closest public description of what `bluetooth_call` does, so
it is worth reading closely. **Its architecture is right and its tooling is
Debian's** — which is the useful distinction, because the interesting parts are
the claims that survived contact with this box.

**oFono is not on Arch, so every command in that post is unavailable here.** No
`ofono` package, no `/usr/share/ofono`, nothing named `org.ofono` on either bus
(measured). So `list-modems`, `dial-number` and `hangup-active-calls` do not
exist on ShaniOS. That is not a gap to close — `org.pipewire.Telephony` **is**
the equivalent, and it is what this skill talks to. Introspecting it live:

    org.pipewire.Telephony   (session bus, owned by wireplaster)
      ObjectManager          GetManagedObjects / InterfacesAdded / InterfacesRemoved
      org.ofono.Manager      GetModems / ModemAdded / ModemRemoved

PipeWire **re-exports the ofono interface names** rather than inventing its own,
which is why searching for `org.ofono` is the right instinct even on Arch — and
why `bluez_cards()` having to walk `/proc/asound` for the HFP card is the only
part of this path that is not already a D-Bus call.

**Their `pw-dump` caveat is stale, and it matters.** The post says
`pw-dump | jq` "does not work due to PipeWire producing broken JSON" and uses
`pw-cli ls` + scraping `object.serial` instead. Measured here: `pw-dump` exits 0
and yields **264,627 bytes that `json.loads` parses into 87 objects**. So the
text-scraping workaround is not needed on this PipeWire, and anything that wants
call-audio node ids should read the JSON.

**The one finding to take seriously is the mixing.** "When playing to and
recording from the phone at the same time, regardless of the sources and sinks
used, we always got both streams mixed into the recording", worked around by
tapping the PipeWire bluetooth *device node* rather than a sink. That is a real
limitation of routing through a sink, and it is the kind of thing that otherwise
shows up as mysteriously two-sided audio in a recording.

Also true and worth stating so nobody expects more: the HFP codec ceiling is
**CVSD 8 kHz or mSBC 16 kHz** even when the cellular leg is EVS/wideband, and
there is Bluetooth latency in both directions.

### What is actually missing, and one dead alias removed

The post treats **call events** as first class — "receive phone events (incoming
call, call closed)". `bluetooth_call` has none: `status/dial/answer/hangup/audio/
profile` all *act or report on request*, and an incoming call is never
signalled. That is the real gap, and it is the one worth building, because
"your phone is ringing" is exactly what a voice assistant should say out loud
and there is currently no way for it to know.

It maps onto the existing trigger engine — `read_btconnect` in
`triggers/desktop_sources.py` is the exact shape — and the data is there:
`InterfacesAdded` on the ObjectManager carries `org.ofono.Voice`/`org.ofono.Call`
for a modem, so an incoming call is a state transition on an object already
being polled. **Not built, and the reason is the one this file keeps recording:
there are zero gateways connected on any machine here, so a reader for it could
only be verified returning UNAVAILABLE.** The honest reading — "no phone is
connected" — is the easy half; detecting a real ringing phone is not something
a unit test substitutes for.

Second, smaller: `_audio_report` **reports** where call audio is going and what
is missing, and moves nothing. The post's actual subject is injecting and
capturing call audio by node id, which `pw-dump` now makes reachable here.

**Removed a dead alias found on the way.** `if action in ("audio", "route")`
accepted `route`, but the schema enum is
`["status", "dial", "answer", "hangup", "audio", "profile"]` — so `route` was
unreachable from the model and from every MCP client, and existed only as a
second spelling of a live action. Same shape as the slash commands "that are
advertised without a check": a name that reads as capability and can never be
invoked.

## NFC: the library is on every install and nothing reads it (2026-10-08)

Asked about [Android's NFC
overview](https://developer.android.com/develop/connectivity/nfc), which
describes three modes - **reader/writer**, **card emulation**, and **HCE** - and
noted that ShaniOS has libnfc. Both are right, and checking the second one
against the first is worth writing down.

**It is genuinely on every install.** `shani-pkgbuilds/shani-peripherals`
`depends` on `libnfc` (with `ccid`, `pcsclite`, `acsccid`, `opensc`,
`pcsc-tools`), and `shani-peripherals` is in **all four** image profiles -
`gnome` and `cosmic` in `Packages-Desktop`, `plasma` in `Packages-Desktop`,
`gamescope` in `Packages-Base`. Verified by reading the profile lists, not
assumed.

**And it ships the command-line tools**, which is the part that decides whether
a skill is possible at all. Arch's `libnfc 1.8.0-3` runs cmake without
`BUILD_UTILS=OFF`; upstream `libnfc-1.8.0/CMakeLists.txt` has
`option (BUILD_UTILS "build utils ON/OFF" ON)` and an unconditional
`add_subdirectory (utils)`. So the binaries are:

    nfc-list  nfc-scan-device  nfc-mfclassic  nfc-mfultralight
    nfc-jewel  nfc-emulate-forum-tag4  nfc-relay-picc  nfc-barcode
    nfc-read-forum-tag3  nfc-utils

Mapping Android's modes onto those: reader/writer and NDEF are `nfc-list` plus
`nfc-mfultralight`; raw tag tech is `nfc-mfclassic`/`nfc-mfultralight`; and
**HCE has a Linux equivalent after all** - `nfc-emulate-forum-tag4` presents an
ISO-DEP Type 4 tag and `nfc-jewel` a Type 1 one, so "tap the laptop with your
phone" is library-supported. It is **hardware-gated**: emulation needs a
PN532-class reader, and x86 laptops essentially never have an NFC controller
built in. Arch builds with `LIBNFC_DRIVER_PCSC`, `ACR122_PCSC` and
`PN53X_USB` on, so an ACR122U or PN532 USB dongle is the whole hardware
requirement - a few dollars - and `pcsclite`/`ccid`/`acsccid` are already deps.

**Arch's package is `arch=('x86_64')`**, worth knowing before anyone tries this
on a Pi - which is the platform the HFP write-up above is about.

**Nothing here can verify any of it.** This dev box is **Ubuntu 26.04, not
Arch** (the repo's own "the unit tests ran on the wrong distro" trap in a new
place): no `pacman`, no reader on the USB bus, no `/sys/class/nfc`, no
`/dev/nfc*`, no `ccid`/`pcsc` modules, and not one of the ten binaries. So the
read and write paths can only be written, not exercised.

Chronoa references NFC in exactly **one** line today - `airplane_mode.py`'s
rfkill label list includes `"nfc"` next to wlan/bluetooth/wwan/uwb - so the
radio is already acknowledged and the data path is entirely unwired.

## The `nfc` skill, and the one bit that killed it (2026-10-08)

Built on the finding above: libnfc is on every install, ten tools come with it,
and nothing read them. The skill is `scan` (is there a reader), `read` (tap a
tag, be told what is on it), `write` (a link onto a sticker) and `emulate`
(present a tag to a phone - Android's HCE, which libnfc can do via
`nfc-emulate-forum-tag4`).

**The NDEF decoder is written here rather than delegated to a binary**, because
NDEF is a published format and `nfc-list`'s output is a human dump. So the
verifiable part is the format itself, and the tests run against real bytes with
no reader present.

**One bit made the whole thing decode to nothing, silently.** The short-record
bit is `0x20`. A first version emitted `0xD1` - MB | ME | TNF=1, SR *clear* -
then wrote a one-byte payload length where the decoder, reading the same bits,
expected the high byte of a four-byte one. Every record produced decoded to an
empty list, with no error and no warning anywhere. The correct bytes are

    E1 01 0C 55 04 65 78 61 6D 70 6C 65 2E 63 6F 6D

which is what an NTAG213 actually holds, and the test asserts
`ndef_bytes("https://example.org") == that literal` **and** decodes the literal
separately - so a broken encoder cannot make the test pass by being decoded by a
matching broken decoder. The URI abbreviation table is also ordered longest
first, because `0x02` "https://www." has to beat `0x04` "https://" or the
result is `https://https://www...`.

**The destructive-write refusal runs before the tool check, deliberately.** With
the checks the other way round, the answer to "write to my bank card" was
"nfc-mfultralight is not installed" - so the refusal existed only on machines
that happened to have the tool. A refusal about destroying somebody's card has
to be the same answer everywhere. Eight different names (bank card, transit pass,
hotel key, credit card, fob, mifare classic, door card, smartcard) are all
refused, and the tests assert the tool is never *invoked*, not merely that the
answer sounds right.

**Three of my own test bugs, all the same shape - asserting the wrong byte.**
The record layout is `[0]` header, `[1]` type *length*, `[2]` payload length,
`[3]` the type itself, `[4:]` the payload. I indexed `[5]`, then `[4]` as a
length, before reading `ndef_bytes` again. The record was right every time.
Separately, four hand-written fixtures had length fields disagreeing with their
payloads and the decoder was right about all of them - so `record()` in the test
file **computes** its lengths, and the MIME/long-record cases are correct now.

**One equivalent mutant, kept and documented.** Making the *text* branch also
accept `"U"` changes nothing: the URI branch precedes it and returns for every
input. Verified rather than assumed - the mutation stays in the run and is
reported with the reason, alongside the real risk it stands for (the text branch
removed entirely), which is caught.

**Wiring:** `nfc-enabled` (default `false`) in the schema, a Privacy row, a
`GATED` entry and a Devices help-group entry, plus seven `files._PACKAGE_HINTS`
entries - without which `tool_missing` printed the literal
*"it comes from the 'the package that provides it' package"*, since libnfc ships
all ten binaries as one package. Two suite tables also needed it
(`test_skill_gates_are_enforced._IMPL`, and `libnfc` in
`test_package_names_match_arch`'s list of real Arch packages, verified against the
Arch package API: `libnfc 1.8.0-3`, `extra`, `arch=(x86_64)`).

63 tests in `tests/test_nfc_skill.py`, **23 mutations, 22 caught** and the one
survivor documented above. 366 passing across the bluetooth, nfc, capability,
consent, annotation, post-condition, packaging and package-name suites.

**Still unexercised:** every read and write path. This box is Ubuntu with no
`pacman`, no reader on the USB bus, no `/sys/class/nfc` and none of the ten
binaries. The refusals, the gate and the NDEF format are all verified; the I/O
is written and marked so.

## BLE: two hard boundaries, measured rather than assumed (2026-10-08)

From [Android's BLE overview](https://developer.android.com/develop/connectivity/bluetooth/ble/ble-overview).
It is conceptual - GATT/ATT, central-vs-peripheral, client-vs-server, and what a
descriptor is - and two of its points turn out to be limits of **bluez**, not
of the protocol.

> **WRONG - corrected 2026-10-09.** The paragraphs below introspected `/org/bluez`;
> the advertising and GATT-server interfaces live on the **adapter**,
> `/org/bluez/hci0`. There: `Adapter1.Roles = ["central", "peripheral"]`,
> `GattManager1.RegisterApplication` and `LEAdvertisingManager1` (12 instances).
> Proved by registering a `LEAdvertisement1` named "Chronoa": `ActiveInstances`
> went 0 -> 1 -> 0. So this laptop **can** be a BLE peripheral, a beacon, and a
> GATT server (man org.bluez.GattManager(5), org.bluez.LEAdvertisingManager(5)).
> Kept below as the record of how a wrong object path produced a confident "no".

**bluez is central-only. There is no peripheral role, so no beacon.** Android's
own page says it "provides built-in platform support for BLE in the central role",
which reads like an Android limitation. Measured on this box, it is also a bluez
one: `busctl --system introspect org.bluez /org/bluez` exports exactly three
interfaces - `AgentManager1`, `HealthManager1`, `ProfileManager1` - and
**`LEAdvertisingManager1` is not among them**. No `RegisterAdvertisement`, no
peripheral, no Eddystone, no iBeacon.

So the first use case on that page - *"interacting with proximity sensors to
give users a customized experience based on their current location"* - **cannot
be built on this stack at all**, and no amount of skill work changes it. Reading
a device that advertises is everything Chronoa can do; being one it can see is
not.

**`HealthManager1` being present corrects what this file said an hour ago.** The
entry above records that Android's `BluetoothHealth` "is not a GATT service - it
is a classic profile found over SDP, which is a different protocol from
everything above", which is true of `bluetooth_gatt` and misleading about the
machine: **bluez does expose `org.bluez.HealthManager1`**, in the same object
tree the GATT reader walks. HDP devices - blood-pressure cuffs, glucose meters,
the SIG-profiled ones - therefore have their own D-Bus home, reached through the
proxy bluez registers rather than through a characteristic read.

That is a better answer than reading a raw characteristic would have been, and it
is a different one: HDP carries RACP (remote access control), so the protocol
handles the session rather than this module interpreting bytes. **Not built.**
There is no HDP device connected to anything here, so it could only be verified
returning "nothing registered" - the same wall as `bluetooth_call`'s call
events.

**Descriptors are the third door, and this device has nothing behind it.** The
page notes a characteristic carries "0-n descriptors that describe the
characteristic's value ... a human-readable description, an acceptable range, or
a unit of measure". That is `0x2901` User Description, and it would beat any
UUID table this module maintains - the *device* would say what a characteristic
is. Measured: the wearable has **8 descriptors and all 8 are CCCD `0x2902`**,
which is exactly the set you need for `listen` to work, and is confirmation that
every one of its notify/indicate characteristics is subscribable. But there is
not one `0x2901` here, so the improvement is real and unexercised.

Worth noting as the reason to want it: `0x2901` is **authoritative where this
module's own table is not** - and this session's table had one wrong entry
(`0x190E` named "Phone Alert Status", which is `0x180E`). A device's own
descriptor cannot have my UUID transcription wrong.

## The wearable, driven live: find, listen, and what the suite was hiding (2026-10-08, later)

All of this was run against the real FB BGS002 (MoYoung) through
`tools.execute_tool`, with consent granted in a scratch keyfile GSettings
(`GSETTINGS_BACKEND=keyfile`; the keyfile group is `[org.shani.chronoa]`, the
schema id, because `config._new_settings` roots the backend there - a
`[org/shani/chronoa]` group is silently ignored and every gate stays closed).

**`find_device` (new tool, same module, same `bluetooth-gatt-enabled` switch).**
Two fixed writes only: the SIG Immediate Alert (0x2A06 <- 0x02) and MoYoung's
`CMD_FIND_MY_WATCH`, taken from Gadgetbridge (`MoyoungConstants` 97,
`MoyoungPacketOut.buildPacket(mtu=20)`): `FE EA 10 05 61` to 0xFEE2, only when
the 0xFEEA service is present too. **The watch vibrated**, confirmed by the
person wearing it, first by hand and then through the tool. Measured traps:
bluez's `WriteValue` answers `Not connected` (this watch drops bluez's LE link
within half a second of "Connection successful"), and `gatttool --char-write`
only ever ends at its timeout; gatttool's interactive mode is the route that
works. A classic device (the JBL) is refused at once instead of waiting 25 s.
`bluetooth_gatt` is now in `READ_ONLY_TOOLS` (the one write is the separate
tool), so its reads no longer end with "unverified - nothing observed it".

**`listen` over gatttool never worked.** `gatttool --listen -a H` with no
command prints the usage text and exits 1, which read as "sent nothing in 15s"
after 4 s. It now writes 0x0100 to the characteristic's own CCCD, found with
`cccd_for` from `--char-desc` (never handle+1), and an early stop is reported
as one. Live after the fix: two real Heart Rate notifications, correctly
flagged "sensor contact LOST" for a watch not being worn.

**`list` sent people to connect a device a read reaches anyway**: a read on
the "not connected" watch succeeded and left it connected. The line now says so.

**Wiring the parity batch missed:** `fm_radio` named `fm-radio-enabled`, which
was not in the schema - a permanent refusal (same as `calendar_edit` above).
Added the key, a Privacy row, `GATED`, a help group, `_IMPL`, and
`rtl_fm -> rtl-sdr` (verified: `rtl-sdr 2.0.3-1`, `extra`, ships
`usr/bin/rtl_fm`). `nfc-enabled` had a Privacy row but was missing from the
settings-coverage test's `CONTROLLED`; the window-building test confirms both rows.

**Other suite fixes:** pyflakes found a dead duplicate `captions` key in
`tool_select` (the later entry always won); the cloud-keys wizard page was
590px of 560 because a sentence was appended to a `PreferencesGroup`
description (now its own wrapping label); the memory-ceiling test's
`'ALLOCATED-OK' not in out` matched the traceback echoing its own source line;
`timer` now accepts `60.0` by design, so the test moved it to the accepted list.

**Not fixable on this dev box (Ubuntu), not regressions:** two seccomp tests
need unprivileged user namespaces (`kernel.apparmor_restrict_unprivileged_userns
= 1`: bwrap "Failed to make / slave"); the vision real-path test times out in
`gnome-screenshot` on GNOME Wayland. And `bluetooth_call` could only be checked
saying "no phone connected": no phone is paired here as a hands-free gateway.

**Running the suite:** about 960 tests per sixth of the files; a serial run
was ~10% after 15 minutes. Running six file shards in parallel makes GUI and
live-audio tests flaky (`test_question_presenter` and others failed only
there), and one shard aborted inside GTK; re-run failures serially before
believing them. Running the package outside pytest leaves `__pycache__` in
`usr/`, which fails two packaging tests until removed.

**The phone (acer ZX), connected over HFP, same session.** `bluetooth_call`
answered "no phone is connected" with the phone connected, for two reasons
found only by running it: PipeWire 1.4's `GetModems` returns the gateway with
**no fields** (`{"/org/pipewire/Telephony/ag1":{}}`) while the skill kept only
rows with `Name`/`Address`; and the text parser expected a count before every
key, which busctl never prints - its fixtures had been hand-written in that same
wrong shape. Now parsed from `busctl --json=short`, the address read from
`GetManagedObjects` (`AudioGateway1.Address`), the name from bluez. Third:
`calls()` asked `GetCalls` of `org.ofono.Manager`, where it does not exist
(measured), so a ringing phone always read as "no calls"; it is on
`org.ofono.VoiceCallManager`. Live after: `acer ZX (74:6B:AB:67:7F:91), no call
in progress, audio: card 94`. Fixtures now carry the captured JSON; 3 mutants
caught. Nothing was dialled. **GATT on the phone is a dead end**: bluez shows no
GATT services for it (Android serves none to a classic-connected laptop), and a
separate gatttool LE link times out. What it does offer is AVRCP
(`.../avrcp/player0`, `org.bluez.MediaPlayer1`: Play/Pause/Next/Previous,
Position, Shuffle, Repeat), plus PBAP, MAP, OBEX push and PAN NAP in its UUIDs -
none of which Chronoa uses yet.

## The phone with no app: MAP, PBAP, HFP, OPP (2026-10-08, later still)

`phone_bluez.py` is a third `phone` backend, used only when neither GSConnect
nor KDE Connect is installed and bluez has a paired phone (`Icon: phone`).
Measured live on the acer ZX through the real `phone` skill: `status` (battery
20% from `org.bluez.Battery1`) and `messages` (225 messages, 67 conversations,
names from the phone's 813 contacts). Text and names were redacted in every
run; nothing was sent, dialled or shared.

- **obexd drops a session when the D-Bus client that made it disconnects.**
  Driven from `busctl`, one process per call, every PBAP/MAP session vanished
  before its first method, and the error ("Method ... doesn't exist") reads
  like the phone refusing. `_Session` holds one Gio connection for the lot.
  `Target` is lowercase (`pbap`, `map`, `opp`).
- **Android gates both per device**: Settings > Bluetooth > this computer >
  "Contacts and call history" / "Text messages". Off, PBAP answered
  `OBEX Connect failed with 0x46` and MAP timed out; `_why` names the switch.
- **MAP `ConversationId` is useless on this phone**: all 225 messages had the
  same one. Threads are keyed by correspondent (`thread_key`, last 9 digits).
  The text is in `Subject`; RCS/MMS arrive with an empty one.
- **Calls are placed, not typed into a dialer**: over HFP `dial` calls
  `AudioGateway1.Dial`, so the confirmation asks "Call X now from <phone>?"
  (`ph.places_calls()`) instead of "Open the dialer".
- Sending a text is `PushMessage` of a bMessage whose LENGTH counts bytes from
  BEGIN:MSG to END:MSG inclusive. Written and unit-tested; **not yet sent
  live**. Sharing is OBEX Object Push, files only. Ring and ping are refused
  over Bluetooth: no profile does it; a watch does it through its own app.
- A messages listing takes ~24 s (813 contacts plus 200 messages per call).
- **Music needs no code**: bluez's own `mpris-proxy` user unit (shipped in
  bluez-utils on Arch and Ubuntu) exposes the phone's AVRCP player as MPRIS,
  and `media_control` then reports "acer ZX is paused". It was enabled but had
  exited; keeping it running belongs to the image (`shani-settings`), not here.

**The watch as an input device for this laptop is blocked by pairing, not code.**
Connecting its HID profile logged `hidp_add_connection() Rejected connection
from !bonded device`: the watch dials in over classic HID, and bluez only
accepts classic HID from a bonded device; this laptop holds an LE bond only.
bluez's encrypted LE link is also dropped within a second while gatttool's
unencrypted one holds, which fits stale LE keys. Re-pairing is the fix, and it
is the owner's call because it touches the watch's pairings.

**Phone panel, incoming calls, call audio (same day, last).** `gui/surfaces/phone.py`
(Calls with dialer / answer / hang up / recent calls / audio route, Messages with
thread and reply, Contacts, Music, Watch) runs everything in threads through
`phone.py`, `bluetooth_call`, `media_control` and `bluetooth_gatt`/`find_device`.
Rendered headless on Broadway against the real phone: 30 calls, 40
conversations, 60 contacts, the watch row, status `ok`. Found by running it:
- **A phone serves one PBAP session at a time.** Loading contacts and call
  history together gave `OBEX Connect failed with 0x53` (busy); `phone_bluez`
  now holds one lock around every obexd session. After that burst the phone
  **withdrew** the contacts permission (PBAP refused, MAP still fine), so the
  person had to re-allow it on the phone.
- **`bluetooth_call` could never dial, hang up, or answer properly**: `Dial`
  was passed without its `s` signature; hang-up called `SendReleaseAndHangup`,
  which the gateway does not have (`HangupAll` it does); answer used
  `ReleaseAndAnswer`, which ends the active call to take a waiting one. Answer
  is now `Call1.Answer` on the ringing call. 3 mutants caught.
- **Call audio route** is `AudioGatewayTransport1` on the gateway: `Activate()`
  brings it to this PC, writable `RejectSCO` keeps it on the phone, `State`
  says which (flipped and restored live with no call). `bluetooth_call audio
  to=pc|phone` and the panel's This PC / Phone toggle use it.
- **`incoming_call.py`** posts an incoming call (named from cached contacts)
  with Answer/Decline via org.freedesktop.Notifications, critical urgency,
  `category=call.incoming`, and closes it when the call is answered anywhere or
  ends. Verified on a private dbus-daemon with stand-in services (6 tests, 3
  mutants caught); **not yet seen with a real ringing phone.**

## The watch's own protocol (MoYoung / Da Fit), live (2026-10-08, night)

`moyoung.py` + skill `watch` + the Phone panel's Watch tab. Protocol from
Gadgetbridge (`service/devices/moyoung/`, read that day), verified on the FB
BGS002 through `tools.execute_tool`: settings read (goal 10000, 12h, metric,
raise-to-wake on), clock set, messages sent, step goal 10000 -> 10001 -> 10000
read back each time, **SpO2 97 %** and **heart rate 100 bpm** measured on the
wrist. Steps, sleep, stress all answered with zeros (watch not worn / Da Fit
had synced them off); training and HR history did not answer on this firmware;
blood pressure did not answer in 70 s.

Measured, not from Gadgetbridge:
- **The watch takes one LE connection.** With Da Fit connected on a phone it
  stops advertising entirely; turning the phone's Bluetooth off frees it.
- **A set is unanswered and takes ~2 s**: read back after 0.5 s showed
  nothing; after 2 s the new value (`SET_SETTLE`).
- **Heart rate from command 109 streams on 0x2A37**, not as a 0xFEE3 reply.
- **Stress is 48 half-hour slots** (51-byte reply), not the 26 Gadgetbridge reads.
- **An empty night is one all-zero sleep triple**, not "awake at 00:00".
- Interactive gatttool prints `char value handle: 0x..`, one-shot prints `=`.
- **The sandbox's 30 s default killed every measurement** (and `bluetooth_gatt
  listen` above ~25 s, which accepts up to 60): `_SLOW_TOOLS` now has watch 150,
  bluetooth_gatt 100, find_device 60.
- Each skill call is its own process, so the one-connection rule is an flock
  per watch in `$XDG_RUNTIME_DIR`, not a thread lock.
- Weight is a profile value (`profile`), never measured.

From Maze Connect (github.com/berk-kucuk/Maze-Connect, read as a reference; it
is a LAN + own-Android-app link, so its transport does not apply): text from a
phone is screened for bidi overrides/isolates and control characters before it
is shown (`phone.clean_text`, applied to every backend's messages and contacts).

## Files both ways, the watch companion, AI voice (2026-10-08, late)

All verified live on the acer ZX and the FB BGS002 unless marked.

- **Files to the phone (OBEX Push)**: received on the phone, confirmed by its
  owner. `_Session` now subscribes to Transfer1 status *before* sending; the
  first version treated "the transfer object vanished" as success, which a
  refusal also does.
- **Files from the phone** (`obex_receive.py`, an obexd Agent1): Accept/Decline
  notification, then **staged in ~/.cache/obexd and moved to ~/Downloads**.
  Returning a Downloads path failed live: obexd refuses any path outside its
  root (`open(...): Operation not permitted`, OBEX Forbidden to the phone).
  Names are stripped of paths and direction characters, never overwrite.
- **Phone music without mpris-proxy**: it was measured exiting 1 at boot and
  **segfaulting** 40 s after a start. `media_control` now falls back to bluez's
  `MediaPlayer1` directly and **reads the state back** - Play was accepted with
  the player still paused when no music app was open.
- **Phone battery triggers work over Bluetooth** (`read_phone` via phone.py).
- **`watch_companion.py`** (switch `watch-companion-enabled`, off: holding the
  watch locks Da Fit out) - live: find-my-phone (98, `00` start / `ff` stop),
  camera (102), music next/play (103 `02`/`06`), volume (103 `04`/`05`, 0.40 ->
  0.55, echoed back as 103 [12, level/16]), and the `watch` skill served
  **through the companion's socket** while it held the watch. Reconnects by
  itself after a drop.
- **Found running it**: polling now-playing through `tools.execute_tool` every
  5 s tripped the repeat guard after 35 calls and then refused `media_control`
  to everyone - it reads the player directly now. A SIGTERM'd caller left
  gatttool holding the watch; gatttool now gets PR_SET_PDEATHSIG.
- **AI voice is 0xF9** (not in Gadgetbridge): `01 01 00`/`01 01 01` on press,
  `02 00` when it ends. Wired to Chronoa's listening (`_watch_voice`). The
  start was captured once and the stop twice; **not yet seen starting a real
  listening turn in the app**.
- **Weather to the watch** (requested by 100 on connect): Gadgetbridge's four
  packets, WMO codes mapped to the watch's icons, through the weather skill's
  web and location gates. Refused live by privacy mode (correct), then by **no
  location source on the dev box** - so `home-place` (Settings > Privacy) is
  now the fallback for the weather skill too. Not yet seen on the watch screen.
- **take_photo timed out at 30 s** when the camera button fired it; webcam
  capture itself, not the watch path. Not investigated yet.

## Calibration, and the log the learning layer trains on (2026-10-09)

- **The outcome model's probabilities are now calibrated** (`learning.fit_calibration`,
  `cross_fit_calibration`, `calibration_metrics`; stdlib, after the standard
  temperature-scaling method, plus a per-class shift for the prior the class-balanced
  fit removes on purpose). Fitted on `out_of_fold_logits` - the same folds
  `cross_validate` scores, which now shares that code - and scored by a 2-way
  cross-fit over feature vectors so its own before/after is held out too. Measured
  on this machine's 17,891 labelled calls: ECE **0.430 -> 0.107**, NLL 0.938 -> 0.547,
  Brier 0.561 -> 0.272, argmax accuracy 85.1% -> 85.8% (majority baseline 89.0%).
  T = 0.28: the balanced fit is *under*-confident, not over. Stored in the model
  file as `calibration`; a file without it loads as identity, and out-of-range
  values are ignored. A fit that does not lower held-out NLL returns identity.
- **`score_predictions` paired every prediction with the first call of its tool
  ever logged** (sorted with a constant key, never consumed a call). Now: the
  next same-tool call at or after the prediction's epoch (2 s slack, 600 s
  window), each call answering one prediction, plus NLL/Brier/ECE of the recorded
  probabilities. Negative control: widening the slack to admit earlier calls
  makes `test_predictions_pair_with_their_own_call_not_the_first_one` fail.
- **Tests were writing into the real user's `tool_calls.log`.**
  `tool_tracking.LOG_DIR` was computed at import and `tools._TRACKER` is built at
  import, before conftest redirects `XDG_DATA_HOME`, so every test that dispatched
  a tool appended to `~/.local/share/shani-chronoa/logs/tool_calls.log`. The
  development machine's 18,277-line log holds 374 `liar`, 378 `unver`, 369
  `add_reminder`, 40 `harvest2_tf_ok` fixture calls (and the heavy
  `get_datetime`/`screenshot`/`delete_file` counts look synthetic too) - which the
  outcome model trained on. Fixed: the path is resolved per write (module
  `__getattr__` keeps `tool_tracking.LOG_FILE` / `dream.LOG_FILE` working).
  Measured: four test files that added 2 lines to the real log now add 0.
  **The existing log was not cleaned** - it is append-only and the user's to
  decide; until it is, read the outcome model's numbers as being about a mix of
  real use and fixtures.
- **The distilled router has nothing to learn from on this machine.**
  `harvest_rows()` reads (request, tool_calls) pairs from saved sessions, and the
  only session file is test residue (128 user turns, no tool calls). The router
  trained in `shani-install-media/test-env/eval-out/distill-qwen3-0.6b.json`
  (62% vs 12% baseline on 8 held-out) was written into a throwaway HOME. Its
  `DISTILLED_MARGIN` stays uncalibrated until there is a router to calibrate.
- **Tool selection, Qwen3-1.7B, before/after on the same server** (15 cases,
  `select+recover` and `+greedy`): rewriting the `web_search`/`news`/
  `get_world_time`/`open_file`/`find_files`/`calculate` descriptions boundary-first
  changed nothing measurable (14/15 both). The one miss, `past-chat`, was the model
  putting the search words in `which`; giving `query` its own parameter description
  fixed it (3/3 re-run, both configs), and `search` also accepts `which` now.
  `search-web` ("search the web for the latest news about ISRO") accepts `news` as
  well, since it is a correct answer; `search-web-fact` is the case that requires
  `web_search`.
- **"what is two plus two" sent only `ask_user`**: `CONVERSATIONAL` treated every
  "what is X" as chat. Arithmetic (digits, number words, plus/minus/percent...) is
  excluded now; "explain gravity" / "thanks!" still are chat.
- **The OBEX receiver held obexd's only agent slot while switched off**, rejecting
  every incoming file and keeping the desktop's own receiver out. It now registers
  only while `phone-control-enabled` is on (re-read every 5 s), on its own thread's
  context: `GLib.timeout_add_seconds` attaches to the *global* default context, so
  the poll never ran (a test caught it) and the per-file decline timeout ran on
  the GTK thread.

## A pre-existing highpass that passed 5,000 Hz, and a rules file nobody could fix (2026-10-09)

Three failures that were **red on unmodified source**, found by running the whole
suite file-by-file (318 files, each in its own process — the per-file log is what
made "which ones are mine" answerable at all). Two were real, one was a missing
declaration, and **in every case the first explanation was wrong.**

### `fmdsp.butter_sos(..., "high")` built a filter with no zero at DC

`tests/test_fmdsp.py` was 5 red and stayed red through a previous pass, which is
how a real defect sits for months in a suite nobody reads to the end. Measured: a
**300 Hz highpass at 250 kS/s attenuated 5 kHz by 116 dB** — it passed nothing.
`highpass(4, 300)` reported 1.6e-06 where the test asked for > 0.95, and the
order-8 impulse response peaked at **8529** against a bound of 1000.

**How live this was: not at all, and that is worth stating before the severity.**
`grep` for callers first, as the Boundaries section says: `fmdsp.highpass()` and
`butter_sos(..., "high")` have **no caller in `usr/`**. The only product consumer
of this module is `skills/fm_radio.py`, and it uses exactly four of it —
`dc_block`, `lowpass`, `measure_tone`, `resample` — all of which were already
correct and still are. So nothing on a user's machine was receiving a silent
audio output; what was broken was a **latent** filter, correct in shape but wrong
in every number, sitting one call away from being used.

The exception is the point. `dc_block`'s own docstring says it **replaced** a
highpass Butterworth at the same cutoff for numerical reasons, so this was not
shipped audio that happened to sound bad — it was a filter that had already been
demoted, and the test kept pinning the mathematics. Had the caller count been
checked first, the honest write-up is "fixed and unreachable", not "fixed a live
defect". Recorded here because the repo's rule is the reverse of what I did: this
pass found a 5-failure test file and reached for the loudest true statement about
it before establishing whether anything depended on it.

The cause was a comment:

> *"The first version marked extra zeros with a (1 - z^-1)^2 numerator. That is
> not derived from this prototype - **a Butterworth highpass does not have its
> zeros at DC** - and it made the numerator near-zero across the whole of the
> passband."*

**That sentence is backwards.** A highpass blocks DC, which *means* a zero at
DC; a lowpass blocks Nyquist. So the "correction" removed the zeros on the
strength of a false premise, and left behind `H_hp(z) = (-1)^N * H_lp(-z)`
implemented as *keep the constant numerator, flip `a1`'s sign* — which is wrong
independently, because `z -> -z` flips the sign of **every odd power**, not one
coefficient. A comment that reads as settled mathematics is worse than no
comment: it is a reason not to check.

Fixed by `_highpass_poles()` — the analog prototype inverted with `s -> w/s`,
dividing by the same complex exponential rather than negating, because negation
reflects through the origin and puts some poles in the *right* half-plane, which
the bilinear transform then puts outside the unit circle — and by a `(1 - z^-1)^2`
numerator on every section. Measured after: **-48 dB at ¼ cutoff, -3.1 dB at
cutoff, ~0 dB in the passband, all poles inside the unit circle**, for orders 4
and 8 at 300 Hz and 5 kHz. 5 failures → 0.

**4 mutations, all caught:** zeros moved back to Nyquist (2), zeros removed
entirely (2), poles negated instead of inverted (2), the pole angle offset dropped
(2). The first two are the two wrong designs above, so the suite now fails on
each of them rather than on the consequence.

### Two test expectations that were measuring the harness, not the filter

Both were fixed **in the test**, and both are recorded because "fix the code, not
the test" is not the rule — *make the test measure the thing* is, and these two
were not measuring the filter at all.

- **`test_rolloff_is_twice_the_order` failed on its own clamp.** Order 8 read a
  slope of 14.0 dB/octave against 16. The true amplitude at 20 kHz is
  **2.8e-10**, and `max(amp, 1e-9)` reports -180.0 dB for it — so the test was
  dividing by its own measurement floor and the slope came out of a *clamped*
  number. Single-pass slopes, which never reach the floor, were 1.94 / 3.97 /
  7.94 against 2 / 4 / 8. The clamp is now 1e-15, chosen from this signal
  length and not picked to pass: Goertzel over 30000 float64 samples of an
  all-zero signal reads exactly 0.0, and order 8 at 40 kHz is 7.1e-15, i.e. at
  the noise. A control asserts the response is above the floor, and asserts the
  all-zero signal reads 0.0, so the floor stays honest as orders grow.
- **`test_it_removes_offset_and_keeps_audio` measured inside the transient.**
  `dc_block` is a one-pole `y = x - x[n-1] + r*y[n-1]`, `r = 0.999246` at 30 Hz
  and 250 kS/s, so its time constant is **1326 samples** and the offset decays as
  `r**n`. Measured means of the same signal: 33.16 whole, 0.0351 over
  `[10000:30000]` — the window the test used, still inside the decay — 1.9e-5 over
  `[20000:40000]`, 0.0 over the tail, with the 1 kHz audio at **1.002** amplitude
  throughout. The filter was never wrong. It now measures a settled window, **and
  a new test asserts the transient decays at exactly the rate the one-pole model
  predicts**, so the settling is checked rather than skipped past.
- **`test_lengths_match_the_ratio` asked for a length the input cannot produce.**
  `resample(sig_199999, 32000, 128000)` is a quarter of 199999 = 49999.75, and the
  code returns **50000**. The expected `(31999, 32000)` is the answer for a
  128000-sample input. Corrected, plus a property test over five rate pairs so a
  future pair cannot pass by luck. **Two equivalent mutants kept and documented**
  (reducing the pair list, weakening a bound) — both leave the file green because
  both are true, which is what an equivalent mutant is.

### `soundtouch` and `rubberband` were declared in Debian and not in Arch

`tests/test_prosody.py` was 4 red: `prosody.apply_song()` raises
`SingingUnsupported` because neither `soundstretch` nor `rubberband` is
installed. **`DEBIAN/control` has carried both since it was written; the Arch
`PKGBUILD` — the one that is actually built — declared neither.** So on every Arch
install `sing` refused for a reason nobody could act on, and sox cannot substitute:
`sox` shifts a whole file in one go, which is precisely the intonation path the
per-syllable design replaced.

Added to `shani-pkgbuilds/shani-chronoa/PKGBUILD`, with the package names read
out of pacman's own file database via `chronoa-matrix.json`
(`/usr/bin/soundstretch` → **`soundtouch`**, not a package named
`soundstretch`) rather than guessed, and offered rather than required for the same
reason as sox. `tests/test_packaging.py` now derives the requirement from the
binary names `singing.py` actually looks for, so a differently-named shifter is
caught at packaging time instead of at runtime; 2 mutations (each entry removed)
confirmed to fail, and `bash -n` passes.

The four measurement tests now carry `@needs_transposer`, whose condition is
probed through `singing._best_transposer()` — **the module's own chooser, not a
second `shutil.which`**, because two probes answering different questions is how a
suite skips where the feature works. And the branch a machine without a shifter
always takes is now **asserted rather than skipped**: the refusal names both
packages, writes no output file, and `singing_support()` reports
`can_transpose: False` instead of claiming the capability. Verified the other way
too, with a stand-in `soundstretch` on `PATH`: the four measurement tests run and
fail on pitch (the stub copies the file without shifting it), which is the
behaviour of a machine that *can* call the shifter.

### One lesson, repeated a fourth time in this repository

The stray-bytecode pair (`test_no_pycache_in_packaged_payload`,
`test_no_bytecode_files_in_packaged_payload`) went red twice during this pass and
both times it was **my own probe** leaving `.pyc` in `usr/`, not a regression —
`PYTHONDONTWRITEBYTECODE=1` on every invocation, and check whether the bytecode is
yours before believing the repo is broken. Verified by bisecting: no single test
file in the group writes bytecode, and cleaning between runs makes both green.

## Two gaps from `digital-travel-agent`, one of them a control (2026-10-09)

`harness-study/digital-travel-agent` (LangGraph, 15.4k LOC, pointed at a live
airline) studied for the first time; the write-up with every `path:line` is
`../harness-study/HARVEST-DTA.md`. Its headline mechanism turned out to be one
Chronoa already has and is further along with — the approval gate is
`permissions.decide()` inside `tools.py:_dispatch_inner`, central for all 190
skills, where the gate there is one `frozenset` beside the tools. Seven such
"already here" rows are in that document; **do not re-propose them.** Two things
were genuinely open, and the harness worked the same way it always has: the
first version of each was wrong, and running it is what said so.

### `rules.md` was a standing system-message injection with no content check

`user_prompts.rules_message()` put `~/.config/shani-chronoa/rules.md` in the
**system** role on every request, under the preamble *"follow them unless they
conflict with safety"* — and the safety half of that was the model's. On the
tool side Chronoa has a control; on the prompt side it had a prompt, which is
`digital-travel-agent`'s `graph.py:12-18` arriving from the other direction.

`provenance.py` fences content by **where its text came from**, and a rules file
is the user's own words, so it is unwrapped by design — correct, and it left
this uncovered. `commands/*.md` are safe for a different reason: the person typed
`/name` this turn, and `forced_tool()` refuses to fire on a user command of the
same name.

A rules file that reaches for tool behaviour or a gate is now **refused** — not
loaded, and the refusal replaces the file rather than sitting under it, because
two voices in the system role is the shape that loses. That is not theory
either: that project's recorded test is that appending a contradicting note
*below* an instruction left both present and the model still took the wrong one.

**Four false positives, all found by running the first regex, not reading it.**
A bare `do not ask` refused *"Do not ask me for the time; use my timezone"*, and
a bare `bypass` refused *"Bypass the corporate proxy for local addresses"* —
ordinary standing rules. Both now require their **object**; the word alone is
never the signal. Then the derived term list turned out to hold **nine gated
tool names that are simply English words** — `phone`, `watch`, `news`, `maps`,
`browse`, `notify`, `screenshot`, `temperatures`, `conversations` — and refused
*"My phone is called edit."* A term now qualifies only if it carries a `_` or a
`-`, which every consent key does and which 11 of the 12 destructive tools do.
The twelfth is `conversations`, carried by the phrase patterns, and that ceiling
is written down rather than patched with a hand-kept exception list.

**The term list is derived, never typed**, from `capabilities.GATED` and
`DESTRUCTIVE_CONSENT_KEYS`, so a newly registered destructive tool is covered
the day it is registered. `tests/test_user_prompts.py` asserts that as a
property of the module's **AST** — collection literals only — because a raw
substring search refuses its own test: the first version of it failed because a
*comment* explaining the derivation named `delete_file`, which is prose about
the list rather than the list.

**The refusal is visible in two places that cannot disagree.** `rules_verdict()`
is the single answer; `assistant.py` sends what it returns and `gui/rail.py`
describes what it returns. The rail says *which phrase* tripped it, because a
refusal nobody can locate is a refusal nobody will fix — and a refused file
showing **no** row would be indistinguishable from "you have no rules".

**Its ceiling is a soft check and cannot be a jail**, stated in the module
docstring for the reason `persona.py` states it: the refusal text and the system
prompt are plain text read by the same model. It catches the obvious case and
refuses it loudly. It is **not** applied to `commands/*.md`, because refusing
those is `provenance.py`'s documented mistake run backwards.

### A duplicate action is born at the approval

Two of the three ways a person can say yes are reachable **without the window
focused** — `approvals.py`'s `notify-send --action`, and the gateway's
worker-thread call — so a notification pressed twice, or a client reconnecting
and replaying after a timeout that actually succeeded, ran the same call twice.
`grep -rn idempot usr/ tests/` found nothing: only `idempotent_hint` on the MCP
descriptions, which is a claim to a client and not a control.

`tools.py` now keeps a bounded, TTL'd receipt for a completed gated call
(`REPLAY_TTL_SECONDS = 60`, `REPLAY_CAPACITY = 64`, `time.monotonic()` like every
other TTL here). The check sits **after** `permissions.decide`, so the question
is asked again every time and only the second *action* is skipped — hoisting it
would quietly undo both "allow once means once" and `is_bypass_immune`.

**The first version cached `ran=True, UNVERIFIED` and was wrong, and running the
real skill is what showed it.** In Chronoa that cell is where a skill's *own*
consent refusal lands — `DispatchResult`'s docstring says so — so a refused call
was stored as a completed one, and a retry after the permission was granted came
back with the stale refusal and could never run. **`VERIFIED` is now the whole
rule**, because it is the only verdict that means the effect held.

**What that costs, measured: 12 of the 62 gated tools** have a post-condition and
can reach `VERIFIED` — `airplane_mode`, `control_service`, `default_apps`,
`delete_file`, `desktop_setting`, `kill_process`, `office_document`,
`print_queue`, `set_hostname`, `set_locale`, `take_photo`, `toggle_wifi` — of
which four are also classified destructive. The other 50 are **not**
replay-protected, and `install_app`, `power_action`, `move_pointer`,
`click_pointer`, `type_text` and `connect_wifi` are among them. Widening it needs
a refusal marker in the child, as a sibling to `ToolFailure.MARKER` — a change
across the skill set, so it is noted rather than faked, which is the same trade
`DispatchResult` made. `tests/test_tool_replay.py::test_the_tools_named_as_covered_still_have_post_conditions`
fails if that list rots.

**Driving these for real needs a keyfile gsettings backend, not a stub.**
`GSETTINGS_BACKEND=memory` is per-process, so a grant made in the probe is
invisible to the sandboxed child that runs the skill and the refusal reads as
"turned off" however the parent was configured. The group is `[org.shani.chronoa]`
— the schema id — because `[org/shani/chronoa]` is silently ignored and every
gate stays shut. With that, `delete_file` really deletes and really verifies.

**Three of my own mistakes, all caught by running, all recorded because the shape
recurs.** The replay's own note was stored instead of the clean receipt, so a
*third* call came back with the note twice — the note now leads the text and
`_dispatch` skips re-recording anything that starts with it. The ordering test
scanned with `ast.walk`, which is **breadth-first**: it reported `replay` before
`decide` for a function where the opposite is true, until the calls were sorted
by line number. And `test_tool_replay.py` left `_REACTIONS` tripped and
`ask_bridge._presenter` clobbered, so `test_tool_outcome_structure.py` failed
four files later with *"has now touched 12 different targets"* — which read as
that file's bug and was this one's. Both globals are now restored, and the
fixture says why.

### Not done, and why

- **Capture-once-then-reuse for an approved argument.** The right shape is
  real — `digital-travel-agent`'s `payment_mandate.py:47-50` captures the exact
  passenger data shown on the checkout page so the later booking reuses it
  *verbatim instead of trusting the model to retype it several turns later* —
  and Chronoa already has both halves of the store (`undo_last_change.py`'s
  pre-image ring, and the Diff panel and rail that read it). What is missing is
  that `_resource_for()` builds the scoped resource from **the model's argument**,
  so a drifted path means the gate asks about the wrong file. **Not built:** which
  file a person *meant* is a design question about the selection UX, not a
  mechanical wiring, and shipping the wrong answer would make the gate describe
  the wrong resource with total confidence.
- **"Everything a tool says must have a spoken sentence."** `TurnEnvelope`'s rule
  that `text` is always complete is the most transferable idea in that repo, and
  Chronoa has three renderers of one turn — transcript, MCP result, and
  `speech.SpeechQueue`, which has no card to fall back on, so a result that
  exists only as a card is a turn that did not happen for a voice user. **Not
  asserted:** no claim is made that the gap exists. Checking it needs a
  `shani-testbed` `slot-test`/`app` action, because constructing a widget is not
  the same as looking at it, and this repo has two recorded instances of a width
  test passing against a row nobody could read.


## `audio.py` mistook a dead microphone for a silent room — and only under load (2026-10-09)

`test_child_supervisor_live.py` passed in isolation and **failed 4 of 5 runs**
during the 330-file sweep. The instinctive reading is "a flaky timing test, make
the timeout longer". It was not: it was a real defect, and the flake was the only
thing that ever showed it.

**The invariant.** A capture that ended the way it was meant to — silence, the
deadline, the user pressing stop — terminates its own child, so it is not a fault
and the child is forgotten. A child that **died on its own** is the fault and
stays tracked, because forgetting it erases the exit status at the moment it is
the only evidence there is: `pw-record` dying mid-turn is otherwise
indistinguishable from a quiet room (`on_done(None)` either way).

**The defect.** The `finally` decided this with `if proc.poll() is None`. Two
things are wrong with that:

- **The question is the wrong one.** `poll()` asks "has the child exited *by
  now*", not "did it die, or did we stop it". What is needed is "did the pipe
  close on us", which is only knowable at the instant the pipe closes.
- **It is racy in the main loop.** The `while` condition is re-checked *before*
  the blocking `stdout.read()`, so a read that overshoots the deadline — which a
  loaded machine makes ordinary — exits on the deadline and the closed pipe is
  never observed at all. The child is then classified as "ended the way it was
  meant to", forgotten, and its exit status goes with it.

`pipe_closed` now records it at the only moment the question can be answered, at
**both** EOF sites, and the `finally` reads that. It is declared **before** the
`try`, because the calibration loop can also see EOF — a child that dies before
producing four frames never reaches the main loop — and initialising it inside the
`try` (as the first attempt did) raised `NameError` inside a `finally`, replacing
the real fault with a confusing one and still reporting the wrong verdict.

**Four defects of my own here, and the two that mattered were both "fix the wrong
thing":**

- **My first fix moved the release to just after the callback**, reasoning that a
  caller asking `capture_status()` from its callback must find the child tracked.
  That is backwards for the clean-stop test, which asserts the opposite, and it
  *also* could not work: `_release()` tests `proc.poll() is None` and the
  `finally`'s `proc.terminate()` runs first, so it could never fire. Reverted; the
  real fix was in the **reason**, not the **position**.
- **That "fix" broke a second previously-passing test**, and the honest reading of
  the conflict is that the two tests were describing a genuine ambiguity — not
  that one of them should be edited to agree with me.
- **A new test I wrote failed**, and the fault was the fixture: 40 silent frames
  at 0.01 s is 0.4 s, more than the 0.16 s `silence_seconds`, so the *detector*
  fired first and my "child dies mid-turn" test was the clean-stop test wearing a
  different name. It now feeds loud frames only, so there is no silence to detect.
- **The mutation that proved the fix was ambiguous.** Both EOF sites are the same
  two lines of text, so the first attempt hit `anchor=2` and reported nothing —
  the recorded "`str.replace` was a silent no-op" trap. Mutating by line number
  found that **the mid-turn site is load-bearing and the calibration site is
  not**, which turned out to be the real finding: printing `proc.poll()` at
  calibration EOF shows **`None`**, because the child has closed its pipe but has
  not been reaped, so the old check reached the same verdict *by coincidence of
  scheduling*. One green test was covering a correct mechanism and hiding a
  defective one in the other path — the same shape as the other four cases in this
  file. Both sites are now pinned by their own test, and the calibration one is
  documented as an **equivalent mutant** rather than left looking load-bearing.

**Wall-clock budgets scale with load** (`_contention_scale()`, from
`/proc/loadavg`, 1.0 idle and capped at 2.5×). This is about **false negatives
only**: every use is a deadline on a wait that must *reach* a state before the
assertions run, so a longer budget lets a slow machine finish and cannot make a
real fault pass. Verified under **26× load on 8 cores**: 20 passed, where the
file failed 4 of 5 before.

224 passed, 14 skipped across the audio, capture, singing, DSP and packaging
suites.

### Three "environmental" failures that were two wrong guards and one honest refusal

The sweep's last failures are on this file's environmental list. Two of them were
**test guards answering the wrong question**, and each cost real time to
diagnose — the environment list says "environmental" and stops there, which is
exactly the "a skip reads as coverage" shape in reverse.

- **`test_sense_idle.py` (3): the guard asked whether a variable was set.**
  `skipif(not os.environ.get("DISPLAY"))` — and on this machine `DISPLAY=:0`
  **is** set while the X server has no `MIT-SCREEN-SAVER` at all, so the guard did
  not apply and three tests failed with the sense's own honest refusal,
  `MIT-SCREEN-SAVER missing on display ":0"`. The product was right and the test
  called the refusal a defect. It now asks `idle.read_idle_seconds()` — **the
  sense**, not the environment — for exactly the reason every other availability
  check here is asked of the component that does the work. The `os` import went
  with it; pyflakes was the thing that noticed it was now unused.
- **`test_vision_sense.py` (1): the guard read `display_environment()`, which is
  a fact about variables, and the test then burned the full 90-second capture
  timeout.** Measured here: `gnome-screenshot -f FILE` prints *"Unable to use
  GNOME Shell's builtin screenshot interface, resorting to fallback X11"*, then
  `Gdk-CRITICAL: gdk_pixbuf_get_from_surface: assertion 'width > 0 && height > 0'
  failed`, then **exits 0 having written no file** — the confident-wrong-answer
  shape this file keeps recording, in a real binary. The guard now attempts one
  real capture at a short timeout and asks whether it produced a sized image.
  That is 94 s → **11.5 s**, and the skip reason names the actual cause.
  **Checked that the new guard can answer True**, since a probe that is always
  False is the same defect as a skip: on a real `Capture` the predicate returns
  True for 1920x1080-with-bytes and False for the zero-sized and empty-data
  cases, so it discriminates.
- **`test_sandbox_seccomp.py` (2): left alone deliberately, and it is the
  example of the rule.** AppArmor restricts unprivileged user namespaces here
  (`kernel.apparmor_restrict_unprivileged_userns = 1`), so seccomp cannot be
  reached at all. The tests do not fail by asserting the filter works — they fail
  in their **own control**: *"the control did not escape, so the refusal below
  would prove nothing"* (`PermissionError: [Errno 13] Permission denied`).
  A check that refuses to certify itself when its premise is absent is the
  correct behaviour, and "make it green" here would mean deleting the control.
  It needs a kernel with Landlock reachable — `slot-test ... repo-pytest` on a
  real image, which is what AGENTS.md already says.

150 passed, 4 skipped across the vision, idle, sense-manifest and sandbox suites.

### The seccomp filter silently broke bubblewrap — and the last two "environmental" failures (2026-10-09)

`test_sandbox_seccomp.py` was 2 red and had been left alone twice with the
reason "Landlock and seccomp need the real kernel; AGENTS.md already says so."
**One of the two was a real product defect**, and it was invisible because the
environment table said it was expected. Checking the recorded reason rather than
the recorded conclusion is the whole lesson.

**Measured, directly.** Installing the real filter and then running the real
bwrap, in one process, in that order:

    filter ON  -> bwrap rc=1  bwrap: Failed to make / slave: Operation not permitted
    filter OFF -> bwrap rc=0

`_harden_child` runs as `preexec_fn` on the **bwrap process itself**, so the
filter is installed *before* bwrap builds the namespace it exists to build — and
the filter denies `mount(2)`, correctly, because Landlock has no mount right in
any ABI. So **every `LEVEL_1`/`LEVEL_2` call failed whenever the gate was on**:
a confinement mechanism refusing to confine anything, reported as an exit code
rather than as a security decision.

Fixed by withholding the filter **from a bwrap child only**, at the point where
bwrap is actually chosen rather than where `_PENDING_SECCOMP` is set — that
assignment happens earlier, and whether bwrap will wrap the command is only
known inside `_run_landlock`. Narrowing the deny list was rejected: `mount` is
the one syscall in that list a sandbox exists to deny, and bwrap's namespace is
already kernel-enforced confinement for this level. One layer lost, named in a
`logger.warning` once per level, rather than a hole left open quietly.

Verified after, through the real `SandboxExecutor` with the gate on:

    gate=false -> code=0 out='WRAPPER ok'
    gate=true  -> code=0 out='WRAPPER ok'

and confinement is intact — reading a file outside the allowlist still returns
`PermissionError: [Errno 13]` with no leak. One mutation (keep the filter on)
fails. 320 passed across the sandbox, actuator and matrix-skill suites.

**The second failure: the premise is absent, and my first two probes were wrong.**
The headline test's control needs `memfd_create` + `execve` to actually escape
Landlock. Here it does not, and I got the reason wrong twice:

1. First I concluded "the kernel returns `EACCES` (13), a memfd execution policy."
   Wrong: my probe used `['/nonexistent']` as the interpreter, so `execve` failed
   with **`ENOENT` (8)** — an absent interpreter, not a policy at all. The real
   payload, with `/bin/echo` copied in and executed, **works on the host**.
2. Then I wrote a probe that looked for errno 13 or `EPERM`. It found neither and
   returned False for the wrong reason — which would have skipped the test on
   *any* machine, for *any* cause. That is the "a check that cannot fail" trap in
   a new place: a probe that is always False is a test that always skips.

**The actual cause is an LSM, and it kills the process before Python can catch
anything:**

    Security violation: Requested utility `3` does not match executable name:
      /memfd:chronoa-probe (deleted)

AppArmor's `exec` profile, so there is no errno to read — the process is gone.
The probe now runs the **real payload** and asks the only question that matters,
"did it print `PREMISE-OK`?", which is the control's own assertion, so guard and
test cannot disagree. Verified it discriminates: `False` here, and `True` for a
control payload that does print — so it is not a test that always skips. 56
passed, 1 skipped with the reason naming AppArmor.

**What I got wrong by not measuring first, in one line:** I read "the environment
list says these are expected" as "these are environmental", and the first of the
two was a defect that had been sitting behind that sentence.

### `lab_network_destroy` refused every call, and its *plan* path could not
### work at all (2026-10-09)

Found while closing the gap left by `c2408de` (pyflakes: undefined names in
shipped code). That commit **fixed** the first of these two bugs. Neither had a
test, because `skills/lab_network.py` had no test file at all — and
`test_netprovision_routes.py` covers the *plan the builder emits*, which is a
different question from *whether the skill's gate opens and its teardown runs*.

**Bug 1 (fixed by `c2408de`):** one line refused every call.

    blocked = (reason if not allowed else _refuse("")) or _root_paths("")

With consent **granted**, `reason` is `""`, so the `or` moved on to
`_refuse("")` — a *truthy* refusal sentence — and never reached the name check,
the record lookup or the teardown. The undefined `_root_paths` behind it was
reported by pyflakes and simultaneously unreachable on every call, so linter and
runtime agreed on a fact that could never happen.

**Bug 2 (found here, still present after that fix):** the read-only half of the
same call rebuilt the network request **by hand and dropped `uplink`** — a field
the record has carried since the helper started writing it
(`usr/bin/shani-chronoa-lab-network:186`). `parse_request` rejects a NAT network
without an uplink, and NAT is the default, so:

    destroy WITHOUT apply, pre-fix:
      Not removed: the record for 'lab1' no longer validates (nat is on but no
      uplink was given, so a public subnet's default route would have no next hop...)

**Every plan preview was refused**, while `apply` worked — because that path
hands the name to the helper, which does its own correct rebuild. The two halves
of one call disagreed about what the record means, and only the read-only half
was broken, which is why it survived: the destructive path is the one a person
notices. Fixed with `_request_from_record()`, named so both halves share one
definition.

Verified through the real record store and real keyfile gsettings, against a
record with a real interface:

    plan shown?  True      (was: refused)
    apply ->     'destroy lab1 done'

`tests/test_lab_network_skill.py` (7 tests): the teardown is asserted on
`helper_calls`, **not on the return string**, because bug 1 returned a plausible
refusal sentence and asserting on text would only prove some sentence came back.
Five controls, each aimed at a mutation that would otherwise survive: consent
refused (catches removing the gate), root precondition consulted (catches
dropping the call — the granted path stubs it to `None` and would never notice),
an unrecorded name refused, a missing name asked about rather than guessed.

**Mutations: 6 confirmed to fail, and the original bug restored verbatim fails
6 of 7.** One survivor worth recording, because it was my mutation that was
wrong, not the test: inserting `blocked = _refuse("") or None` *above* the real
`blocked = _root_precondition()` left all 7 green, because the next line
overwrote it. That is the recorded "a `str.replace` that silently does nothing"
trap — asserted `s.count(old) == 1`, which passed, and still mutated nothing
behaviourally. The check that caught it was noticing that a *whole-file* fault
should not leave the file green.

**Two of my own test bugs, the second one worse than a failing assertion:**

- The first `RECORD` fixture had `subnets: []`, which `parse_request` **rejects**
  — so the plan test was measuring that rejection and calling it a plan. Then
  it had no `uplink` at all, the same trap one level down. Both fixed by
  building the fixture from the shape `usr/bin` actually writes, with the
  interface looked up from `/sys/class/net` rather than typed, since
  `parse_request` checks that the named interface exists.
- A `try`/`finally` restore that put `_invoke` back to itself — so an assertion
  failure would have left the module **globally patched for every later test**,
  a failure that reads as the next file's bug. `monkeypatch` throughout. This
  is the `NameError`-inside-a-`finally` lesson in a different guise: both are a
  failure corrupting the run instead of reporting itself.

75 passed across the lab-network, netprovision, capabilities, gate,
handler-importability, tool-outcome and labnetworks-sense suites; 35 more
across packaging and package names.

**`cli_matrix_v2.py` — deleted rather than merged (2026-10-09).** Asked to merge the
two matrix scripts. `tools/cli_matrix_v2.py` (891 lines, added 2026-10-07 in the
harvest2 commit and never referenced since) turned out not to be a version of
`cli_matrix.py` but an abandoned fork with its own incompatible CLI, so there was
nothing to merge *into* v1. Measured, not read:

| question | answer |
|---|---|
| callers anywhere in the repo | **zero** — no test, no doc, no harness, no `shani-testbed` slot-test |
| can it run today | **no** — its `main` requires `chronoa-matrix/chronoa-matrix.json` to already exist and prints *"No matrix JSON found"*; that directory only exists on a slot |
| does it read Chronoa's registries | **no** — `SKILLS_DIR` is defined and never used; `discover_skills`/`ast.parse` appear 0 times against v1's 2 |
| its own headline feature | `enhanced_intent_detection` is called **nowhere**, not even by its own `main` |
| what v1 has that it lacks | `--check`, `--diff`, `--audit-packaging`, `--suggest`, `--scaffold`, `--enrich`, `--resolve-files`, `--package-report`; and it reads the real skill schemas, so its `open_surfaces` measures *calls* |

**Five claims in its own docstring, and the two most interesting are false.** It
advertises "cross-profile analysis to identify consistent missing functionality" —
implemented nowhere in the file, while v1 has a real `audit_packaging` across the
image profiles. And "more comprehensive intent classification" names a function with
no caller.

**Its regexes are corrupted, which is how this was found.** `INTENTS` and
`CATEGORIES` have every alternative repeated two or three times — one 33-way
alternation contains 22 duplicates (11 unique terms), e.g.
`\bsearch\b|\blookfor\b|\bfind\b|...` with each word three times over. **`search`
appears twice in `INTENTS`**, so the first entry can never match anything. That is
not a stylistic complaint; a classifier whose first rule is unreachable and whose
rules are triple-counted produces a confident coverage number derived from nothing,
which is the exact failure this file's `--suggest` section warns about when it says
`open_surfaces` measures calls, not answers.

Removed with `git rm`. A fork nobody runs, that cannot start, that reads no
registry, and whose only distinctive function is unreachable, is not a merge
candidate — merging it would have put a second CLI and a second classifier into
`tools/` for a reader to choose between, with no way to tell from the code which
one produced a given number.

**The two matrix scripts were one live tool and one abandoned fork (2026-10-09).**
Asked to merge `tools/cli_matrix.py` and `tools/cli_matrix_v2.py`. Measured first,
because "merge" is the wrong verb if one of the two is not a version of the other:

| question | v2 |
|---|---|
| callers anywhere in the repo | **zero** - no test, no doc, no harness, no slot-test |
| can it run today | **no** - its `main` needs `chronoa-matrix/chronoa-matrix.json` to exist and prints *"No matrix JSON found"*; that path only exists on a slot |
| does it read Chronoa's registries | **no** - `SKILLS_DIR` defined and never used; `discover_skills`/`ast.parse` appear 0 times against v1's 2 |
| its own headline feature | `enhanced_intent_detection` called **nowhere**, not even by its own `main` |
| what v1 has that it lacks | `--check`, `--diff`, `--audit-packaging`, `--suggest`, `--scaffold`, `--enrich`, `--resolve-files`, `--package-report`; and it reads the real schemas, so `open_surfaces` measures *calls* |

So v2 is a fork with its own incompatible CLI, added 2026-10-07 with the harvest2
work and never referenced since. **Deleted with `git rm`**, not merged: merging
would have left two CLIs and two classifiers in `tools/` for a reader to choose
between, with nothing in the code to say which produced a given number - the same
defect as `scan_archive.py`, which was deleted as a superseded duplicate of a
strictly better guard.

**Two things worth knowing about v2 before anyone restores it.** Its docstring
makes five claims and the two most interesting are false: "cross-profile analysis
to identify consistent missing functionality" is implemented nowhere in the file,
while v1 has a real `audit_packaging` over the image profiles. And its regexes
are corrupted - `INTENTS` and `CATEGORIES` repeat every alternative two or three
times (one 33-way alternation holds 22 duplicates, 11 unique terms), and
**`search` appears twice in `INTENTS`**, so the first entry can never match. A
classifier whose first rule is unreachable and whose rules are triple-counted
produces a confident coverage number derived from nothing, which is the failure
`--suggest`'s own caveat already warns about.

**`--check` on a real image found a live defect, which is the argument for the
slot-test over any host test.** `set_theme` shelled to three KDE helpers it never
checked for. Nothing crashed - `_run_cmd` returns `None` and every caller handled
it - so on a GNOME desktop a person asking "am I in dark mode?" got a bare "no"
with no reason. Fixed both halves: `_missing_kde_tools()` checks first, and
`files._PACKAGE_HINTS` gained `kreadconfig6 -> kconfig` and
`plasma-apply-colorscheme -> plasma-workspace`, read out of pacman's file database
via `chronoa-matrix.json`'s `commands[].package` - **the only machine-readable
authority in the tree for what package ships a binary**. `plasma-lookandfeeltool`
and `kreadconfig5` get no hint on purpose: they are Plasma 5 names, absent from a
Plasma 6 image, so naming a package for them would be the invented answer that
table exists to avoid. Measured before/after on `@blue`: FAIL, then
`chronoa-deps-explained PASS (26 tool(s)/sense(s) need a command this image lacks,
and every one checks for it first)`, and tree-wide `unguarded_missing == []`.

Running the matrix outside a slot is a silent refusal: `--check` prints
*"No pacman database here: run this on a Shanios/Arch system or in a testbed
slot."* and **exits 0**, which reads as a pass. The run needs a bootstrapped slot
(`build.sh test ca`, then `bootstrap -p gnome -d latest`, ~40 min) and an output
directory that exists **inside the container's mount namespace** - a host
`/tmp/opencode/...` path makes nspawn die with *"Failed to clone ...: No such file
or directory"* while the overlay still reports success, so the failure looks like
a boot problem. Use `test-env/mout:/mnt/out`.

**Forty failures that were a missing display, not a broken product (2026-10-09).**
A chunked run came back with 59 failures where the last verified state had 6.
Nine were `test_window_input_and_copy.py`, **twenty-eight** were
`test_tool_activity_panel.py`, three were errors of
`test_help_gate_labels_match_settings.py` — and all of them **pre-existing**,
checked by restoring the senses directory from `9817575` where the same counts
fail. Not one was a product fault.

Each file runs a real `ChronoaWindow` in a subprocess. With no display backend
the subprocess died with `RuntimeError: Gtk couldn't be initialized` and
`Gtk-CRITICAL: ... 'GDK_IS_DISPLAY (display)' failed`, printed **no `RESULT`
line**, and the module fixture read the missing line as `{}`. Every assertion
then failed on **a key that had never been written** — `KeyError:
'send_visible'` for a send button that works on every display-capable machine,
where that file is **9 of 9**.

**An absence presented as a failure is the same defect as an absence presented
as a pass**, and this file has been bitten by both. The failure *text* is what
costs: it names the product, not the machine.

**`Gtk.init_check()` is not the question.** It returns `True` with no display and
every widget built afterwards is unallocated — `Adw.init()` warns `invalid
(NULL) pointer instance`, icon-theme lookups critically fail, and
`Gtk.ApplicationWindow.__init__` raises. A guard written against `init_check`
passes and then the window raises anyway, which is what the first version of this
fix did. The guard asks **`Gdk.Display.get_default()`**: whether a window can
exist, not whether the library loaded.

**Two halves, because one was not enough.** A function-scoped autouse fixture
covers the files whose harness fixture is function-scoped; a
**`pytest_ignore_collect` hook** covers the module-scoped ones, because a module
fixture is set up *before* any function fixture and raised into
`ERROR at setup` — three errors where a skip belongs, the same absence in a
different word. Refusing to collect is the honest shape for a file that cannot
run: no test to skip, because no test exists on this machine. Note the hook does
**not** fire for a file named directly on the pytest command line — verified,
and it is why the two halves are both needed.

**Neither half can hide a product fault**: it fires only when no display backend
exists at all, and `test_window_input_and_copy.py` is 9 of 9 on X11. That is the
control, and `tests/test_a_harness_that_cannot_run_says_so.py` holds it — with a
probe that proves `init_check() == True` **and** `display == None`, which is the
claim the entire fix rests on.

**A new sense, found by the matrix's own calibration and then checked against the
tree: `kernel_log`** (49 senses → 50). `chronoa-matrix.json` lists `dmesg` with
`used by: []` while `faults` reads **journalctl** and `containers` asks the
runtime — so nothing read the kernel ring buffer at all. That is the one record
no service has touched: a disk controller that reset, a USB denial, an OOM kill
before journald was running. Route order is `dmesg`, then `/dev/kmsg` drained
**non-blocking** (it is a stream; a blocking read waits for a message that may
never come and burns the whole skill budget), and a permission failure is
**UNKNOWN**, never an empty list — "no kernel messages" and "the kernel will not
tell me" are opposite claims.

**Two loader conventions that make a finished sense silently dead**, both caught
by running it rather than by reading: the loader reads **`SENSES`, not
`SKILLS`** (`senses/__init__.py:_register`), and a module carrying `SKILLS` is
treated as a library module and returns **without a warning** — the same defect
class as `SKILLS` itself had in the sense layer. And `sensitivity` must be one
of `personal`/`private`/`public`; `"machine"` is not in the vocabulary and the
loader refuses the whole sense over it. The kernel ring is `public`, beside
`faults` and `firewall`.

**`kernellog`, not `kernel_log` — and the reason is worth keeping.** The new
kernel-ring sense was written as `kernel_log`, and two rules collide on that
name. The consent key is **`<sense-name>-sense-enabled` derived from the name**
(so `kernel_log-sense-enabled`), and **`glib-compile-schemas` rejects `_` in key
names and discards the entire file when it hits one** — all keys at once, not
just the bad one. A single snake_case sense name would therefore have silently
undeclared every other key in the schema. Renamed to `kernellog`, which is legal
as a key fragment.

Measured, both halves: with `kernel_log-sense-enabled` the schema compiles to
*"Invalid name ... only lowercase letters, numbers and hyphen are permitted. This
entire file has been ignored"* and `test_sense_manifest` fails with *"these senses
are registered but have no consent key ... so `sense_allowed()` denies them
forever"* — the `calendar_write` / `fm_radio` dead-switch class, reached a third
way. The rule this file already records (*"a sense name must be legal as a key
fragment; there is a test asserting this across the whole registry"*) catches it
too late to be useful here, so the fix is the rename, and the schema now compiles
clean with `gschemas.compiled` produced.

Its Settings row sits **beside `faults` deliberately** — the same question
("what went wrong") asked of two different records, and a reader comparing them
is doing exactly the right thing. Off by default, unlike `faults`: the ring
usually needs root or a group membership, and it carries hardware detail rather
than a service's own account of itself.
