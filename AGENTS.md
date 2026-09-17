# Agent instructions — shani-chronoa

This file applies to any AI coding assistant working in this repository
(Claude Code, opencode, Kilo Code, Cursor, Aider, or similar). Read this
before editing, and follow the verification steps before calling any change
done.

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

This repo is the **P0 CRITICAL** security gap in the whole ecosystem: no sandbox execution, plaintext API keys, and no prompt sanitization. The reference implementation for the security items below is sayri, readable at `/home/shrinivaskumbhar/Documents/shani/garuda-clones/sayri/` (verified present: `usr/share/sayri/lib/sayri/adapters/sandbox/executor.py`, `domain/secrets_manager.py`, `gateway_supervisor.py`). Chronoa already beats sayri on wake word with VAD calibration, barge-in with TTS interrupt, 7+ LLM providers, BYOK cloud fallback, a real MCP server, and a calibrated noise floor — none of those are being touched.

1. **Sandbox Executor** (P0, 2-3 days) — Port the 5-level `bwrap` sandbox from `sayri/adapters/sandbox/executor.py` (LEVEL_0_NO_EXEC → LEVEL_4_HOST_ROOT) into `tools.py`'s `execute_tool()` path (`tools.py:25`, the single implementation both `assistant.py:98` and `mcp.py:121` call): privilege-escalation blocking (`sudo`/`pkexec`/`su`), a dangerous-binary blocklist (`mkfs`, `dd`, `shutdown`, `reboot`, `mount`), `subprocess.run(timeout=...)`, and GUI-access detection. This is what stops the LLM from running destructive commands. Do NOT copy sayri's `AgentProfile`/`SandboxConfig` dataclasses — Chronoa needs its own config model (roadmap #1).

2. **Secrets Vault** (P0, 1-2 days) — Add `secrets_manager.py` modeled on `sayri/domain/secrets_manager.py`: XOR obfuscation with a machine-id + UID salt, `os.chmod(0o600)`, `inject_environment()` that only injects into child-process env, and masked previews in `list_secrets()`. This replaces plaintext API keys — audit-verified 2026-09-17: there is **no** `config.json`; keys live in GSettings/dconf (`org.shani.chronoa`) — and must support the longer BYOK key formats (Anthropic, OpenAI, Google, Groq) (roadmap #2).

3. **LLM Prompt Sanitization** (P0, 2-4 hours) — Add `sanitize_text_for_llm()` (pattern: `sayri/domain/secrets_manager.py:143-152`) and wire it into every LLM API call path before the `httpx.post` to any provider, so a secret can never leak even if the vault is bypassed. Handle secrets embedded in longer strings and partial matches (roadmap #3).

4. **Peer-Validated IPC** (P3, 1 day) — Chronoa's D-Bus interface has no peer validation; sayri's UNIX socket (`~/.local/share/sayri/sayri.sock`, chmod 600) rejects connections from other UIDs. Validate the D-Bus peer UID and reject/log unauthorized connections (roadmap #28).

5. **Zip-Slip Protection + Skill Download Validation** (P3, 1 day) — If/when skills are downloaded rather than only user-dropped, scan archives for `../` path traversal and reject executable files outside expected directories (pattern: sayri's `downloads.py` + `domain/skills_scanner.py` risk scoring; roadmap #29).

6. **Gateway Supervisor Architecture** (P2, 2-3 days, only if external channels are planned) — Port the *architecture* of `sayri/gateway_supervisor.py` (per-instance process management, per-instance env binding, dynamic secret injection from the vault, inactivity timeouts) if Discord/Telegram/Matrix integration ever becomes a goal. Not needed for the current local-first design (roadmap #22).

7. **Tool Call Tracking** (P2, 1 day) — `ToolCall` dataclass (`name`, `args`, `status` PENDING/RUNNING/SUCCESS/DENIED/FAILED/TIMEOUT, `result`, `duration_ms`, `timestamp`) wired into `execute_tool()` in `assistant.py`, with recent calls (last 100) kept in memory for conversation context and surfaced via MCP responses. Chronoa currently has zero audit trail for LLM-initiated actions (roadmap #20).

8. **Per-Agent Sandbox Profiles** (P2, 2 days) — `AgentProfile` dataclass (`name`, `sandbox_level` 0-4, `allowed_commands`, `blocked_commands`, `timeout_seconds`), default level 3, per-agent config in `~/.config/shani-chronoa/agents/<name>.yaml`; sandbox executor (item 1) looks up the profile before each command (roadmap #21).

9. **LICENSE file** (P3, 5 min) — Chronoa is one of the 4 repos still missing a LICENSE (master-roadmap #31). Add GPL-3.0-only matching the OS-side repos (the web repos' sibling `shani-blog` is MIT — not the precedent here; chronoa is OS-side).

10. **Shared CI templates, Renovate, conventional commits** (P1, cross-repo) — Adopt `shani-ci-commons` (roadmap #7), add `renovate.json` (roadmap #8 — Python repo: pip + GitHub Actions), enforce conventional commits (roadmap #9). Implemented centrally, tracked here. Note: this repo is **not** a git repo (no `.git`, verified 2026-09-17) — git-dependent CI hooks need the directory git-initialized first.
