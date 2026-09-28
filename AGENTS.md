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

**Read when your change touches them:**
- `Rendering the UI to a PNG` — GTK4/Adw init ordering and the capture path
- `Machine-state senses` / `Senses layer` — the sense contract
- `Settings window`, `Model choice`, `Packaging` — the two naming
  conventions must not be mixed

**On-demand reference:**
- `Audit-verified known issues` and `Garuda Cross-Reference Findings` —
  dated retrospectives. **Grep for the subsystem you are changing.** Full
  methodology in `AUDIT-HISTORY.md`.
- `Deliberately not done` — read before proposing a feature, so you don't
  rebuild something intentionally excluded.

**Never skip:** running it. Reading the diff has never been sufficient here.

## What this repo is

A local-first GTK4 voice/text AI assistant for Shanios: `whisper.cpp` for
STT, Ollama for LLM inference and tool-calling, Piper for TTS, real mic
capture and audio playback via `pw-record`/`pw-play` (falling back to
`arecord`/`aplay`), plus optional hands-free wake-word activation
(`wakeword.py`, openWakeWord). Listening auto-stops on silence
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

## Empirical verification (mandatory)

**Reading code is analysis; running code is verification.** A change is not
verified by reading the diff, running `bash -n`, or confirming it "looks
correct." It is verified by observing the actual behavior of the real
thing in the real environment — built, served, deployed, signed, running.
If you haven't seen it work (or fail) for real, it isn't verified.

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
`ipc.py`/`tool_tracking.py`/`gateway_supervisor.py`/`sandbox/profiles.py`
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
- **Consent is still checked only at perception time**, in
  `ChronoaConfig.sense_allowed()`. Nothing yet re-checks a *stored* percept
  against the current consent state before it is sent, so a fact remembered
  while the memory sense was on is still sent after it is switched off. That
  is an open decision, not a finished design.

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

## Machine-state senses (added 2026-09-28)

Eleven senses that read the machine rather than the user's world —
`contention` (who holds a capture device), `privilege` (who holds a dangerous
capability), `thermal`, `display`, `network`, `bluetooth`, `camera`
(enumeration only), `rfsense` (WiFi RSSI spread), `thermalgrid`
(MLX90640/AMG8833 over I2C), `hwmon` (fan/temperature/voltage/current from
`/sys/class/hwmon`) and `modelfit` (available RAM against the models Ollama
reports) — plus `senses/latch.py` and scheduler de-duplication, so a polled
sense that keeps saying the same thing is not deposited 1440 times a day.
`hwmon` and `modelfit` have no external binary: they read the kernel directly,
so there is nothing that can be missing for them to degrade into.

**Three of the eleven were green on the dev box while confidently wrong on the
distribution this actually ships to, and the lesson is not optional to read:**
`contention` reported every microphone and camera as *free* when `fuser`
(psmisc) was missing, because the OSError was swallowed into "no holders";
`privilege` used Debian's `dpkg-query`, so on Arch — which is what ShaniOS is —
it found no package owner for anything and reported **every process as
unmanaged third-party software**, burying the one real finding; `bluetooth`
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

**Verification status:** unit suite green on Ubuntu (645 passed, 3 skipped),
**and 42 pass / 0 fail on a real booted ShaniOS slot** (2026-09-28, testbed
`9be7139`, packaging at `shani-pkgbuilds` `031337f` = `shani-chronoa 0.1.0-6`)
— including `privilege-uses-package-manager` PASS with 18 holders correctly
attributed to packages, which is the check that only exists to catch the Arch
failure. Real readings from the slot: `eth0: up, carrier`,
`DNS: 192.168.31.1`, `SEN2: 90.0C`, `intel_backlight 9514/96000 (10%)`,
`hci0 (unblocked)`, and for `modelfit` `31795 MB RAM total, 25656 MB
available` read from `/proc/meminfo` while Ollama was unreachable. Repeating it
needs `--cgroupns=host` for nspawn and a hand-rolled mount for
`shani-chronoa` — see `AUDIT-HISTORY.md`.

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

## Audit-verified known issues (confirmed present)


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
- **Four of the six 2026-09-18 security/architecture modules are dead code —
  built, unit-tested in isolation, and never imported by anything that
  actually runs.** Confirmed via `grep` across the whole package: none of
  `ipc.py` (`PeerValidator` — also isn't a real peer-credential check, it's
  an unkeyed SHA256 hash, not a signature, and Chronoa has no D-Bus/socket
  IPC surface for it to protect in the first place — the MCP server is
  explicitly stdio-only/same-user-trusted, see `mcp.py`'s own "Trust model"
  docstring), `tool_tracking.py` (`ToolCall`), `gateway_supervisor.py`, and
  `sandbox/profiles.py` (`AgentProfile`) are referenced from `app.py`,
  `tools.py`, `assistant.py`, or `mcp.py`. `skills/scan_archive.py`
  (zip-slip protection) is additionally miscategorized: it lives under
  `skills/` but doesn't match the skill contract, so `discover_skills()`
  logs a `Skipping 'builtin:scan_archive': SKILLS must be a list of Skill
  entries` warning on every startup — harmless (doesn't break skill
  loading, verified live) but pure noise, and correct anyway since there is
  no skill-download feature in Chronoa for it to guard (only
  `~/.config/shani-chronoa/skills/` local drop-in, per this file's own "What
  this repo is" section). Do not treat a `feat: add X` commit or a passing
  module-level unit test as proof `X` is live in the running app — grep for
  real callers first, per this file's own verify-by-running rule above.
  Wiring these in (or deciding they're not worth wiring in) is still open
  work, not done — see the Implementation Roadmap section below, which was
  written under the same mistaken assumption and needs re-reading with this
  in mind.
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
done" section - not a mechanical wire-up like item 7 got). Item 5 is
dead-but-harmless (gracefully skipped, logs a warning). Don't take a `feat:`
commit message or this list's prose as proof of "done" — grep for real
callers first, and check what the default arguments actually do when
nothing overrides them (item 7's `/var/log` default was the second
"passes its own unit test, crashes for real" bug found this pass).

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

4. **Peer-Validated IPC** (P3, 1 day) — `ipc.py`'s `PeerValidator` exists but
   is dead code: never imported anywhere, isn't a real peer-credential check
   (unkeyed SHA256 hash, not a signature), and Chronoa has no D-Bus/socket
   IPC surface for it to protect — the MCP server is explicitly stdio-only,
   same-user-trusted (see `mcp.py`'s "Trust model" docstring). Still open;
   decide whether there's a real integration point before wiring it in, or
   remove it (roadmap #28).

5. **Zip-Slip Protection + Skill Download Validation** (P3, 1 day) —
   `skills/scan_archive.py` exists, is correct, but is unused and
   miscategorized: it lives under `skills/` without matching the skill
   contract, so it's skipped with a harmless warning log on every startup.
   There is still no skill-download feature in Chronoa (only local
   `~/.config/shani-chronoa/skills/` drop-in) for it to guard. Still open
   (roadmap #29).

6. **Gateway Supervisor Architecture** (P2, 2-3 days, only if external
   channels are planned) — `gateway_supervisor.py` exists but is dead code,
   never imported. Not needed for the current local-first design; still
   open only if Discord/Telegram/Matrix integration ever becomes a goal
   (roadmap #22).

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

8. **Per-Agent Sandbox Profiles** (P2, 2 days) — `sandbox/profiles.py`'s
   `AgentProfile` dataclass exists but is dead code, never imported by the
   sandbox executor or anything else. Still open (roadmap #21).

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
