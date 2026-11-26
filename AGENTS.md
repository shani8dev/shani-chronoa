# Agent instructions — shani-chronoa

This file applies to any AI coding assistant working in this repository
(Claude Code, opencode, Kilo Code, Cursor, Aider, or similar). Read this


## Skill source of truth

`CAPABILITIES.md` is generated from the skill registry by
`tools/gen_capabilities.py`. It is the source of truth for what Chronoa can
do and how each skill is gated. `AGENTS.md` is only a current-instructions
index: do not hand-edit `CAPABILITIES.md`, change the registry and regenerate
it. `ARCHITECTURE-TARGET.md` Part 4 is the source of truth for deliberate
non-goals.

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
- `Third-party studies` - the `../harness-study/HARVEST-*.md` index below.
  **A harvest is a dated claim about this tree**, not a finding about the repo
  that was cloned: the named symbol may have moved, and one recorded claim
  (`HARVEST-2/E-memory-mcp-special.md`'s "threshold gate") was already found
  false. Check a claim against the code before acting on it.

### Third-party studies - `../harness-study/HARVEST-*.md`

Findings from reading outside harnesses, and **what this repository did about
each**. Four of these were unlinked from this index when it was added
(2026-10-12), including a 286-line deep pass, so nothing here depended on having
read them. `harness-study/` is **not a git repository**, so a harvest nothing
points at exists on one disk until that disk is reused.

| file | subject | what came of it |
|---|---|---|
| `HARVEST-OIU.md` | open-intelligent-ui | five transfers, 7 passes: goal edits, `safeUrl` links, the bounded cache, `clip` |
| `HARVEST-HERETIC.md` | heretic | one transfer: a measurement reported beside its baseline |
| `HARVEST-MIMIC.md` | mimic | none taken; **already refused** by `ARCHITECTURE-TARGET.md` |
| `HARVEST-ECC.md` | everything-claude-code | skill coverage, the durable history read, the sense budget |
| `HARVEST-LAYA.md` | laya-decision | `EventSink` shape, the confidence floor, `prompt_pieces`, temperature bounds |
| `HARVEST-AIRLLM.md` | airllm | residency vs runnability; a 297x sparse-vector win |
| `HARVEST-EDGE0.md` | edge0 | KV width cross-check; bytes-per-token |
| `HARVEST-EDGE0-DEEP.md` | edge0, deep pass | a hygiene suite that imports nothing; presets as measurements |
| `HARVEST-PERSONAPLEX.md` | PersonaPlex | the audio-codecode quantizer; the loudness target, measured then built |
| `HARVEST-DTA.md` | digital-travel-agent | the rules-file refusal; the approval-time replay guard |
**Recorded against myself, in the same pass: `git commit --only <file>` is not
safe in this workspace, and the rule above did not say so.** The workspace's
`AGENTS.md` documents `git commit` with no paths sweeping another session's staged
work, and prescribes `--only`. It scopes **paths, not lines**. `git add AGENTS.md
&& git commit --only AGENTS.md` was run while a concurrent session had ~200 lines
of an uncommitted section in the same file, and **their work landed inside my
commit** (`c3ba7b5`, whose message is about the harvest). Nothing was lost and no
file was clobbered; the cost is that their work is now attributed to me and their
next commit will not contain it, because the file is clean. The fix is the one the
workspace already gives for the narrow case: **back up, write the note, and commit
only what you can show is yours** - here, by checking `git show --stat` named the
line count *before* pushing the commit. Rewriting the history is a person's
decision, not a mechanical one, and it is recorded rather than fixed.

| `HARVEST-LINUX-HELLO.md` | linux-hello | three transfers, all measured: a pure `plausible()` beside detection so the face decision is testable without OpenCV; a **15-line V4L2 capability probe** - `/dev/video1` here is a metadata node that cannot capture, while `QUERYCAP` answers for it anyway; and **infrared capture in both photo skills**, verified against a real `v4l2loopback` IR camera: 58,791 bytes of h264 holding 90 frames, 206 levels from a 127-level source, which is why both replies state what ffmpeg's conversion did. The anti-spoofing set, the IR liveness constants, `mmap` streaming, the warm daemon and the PAM profile are refused with reasons. Recorded as a defect in the upstream: **face templates written world-readable (dir 0755, file 0644)** |
| `HARVEST-OPENDOTS.md` | CopilotKit/OpenDots | **nothing taken, and the reason is measured**: transcribing its SSRF private-range list and comparing found 3 disagreements in 29 probes - each right where the other is wrong - and Chronoa's permissive allow side is the documented inverted-polarity choice (`127.0.0.1` is its own inference endpoint). Two rows kept: the approval replay guard (**already here**, args are in the replay key), and `learningSelector`'s owner/agent scoping, which is a question Chronoa has never asked - `train_and_save` has `only_tools` and no `only_origin`, while `_replay_key` records that a trigger-driven turn is a different decision. **Second pass, the Python `Shashankss1205/OpenDots`, has one real finding**: its `_assess_relevance` is a reference implementation of the organ Chronoa's table already records as `missing="no salience or priority queue"` - checked, not assumed, since `tool_select.py` does score tools but for fitting the context window, not for choosing between competing events. The part kept is that below the confidence minimum the status is **`uncertain`, not a second "no"**. Its `assessment_key` (which includes the model), its daily model-call budget and its `retry(inspected=True)` are all recorded as **already here by another mechanism**; its `preexec_fn` warning is answered by Chronoa's single documented chokepoint. **A deep pass found the same shape in both repos**: the TypeScript one carries `expectedRevision` on `pages.update` plus a `conflict` status that keeps both copies and never picks a winner (*"Your draft is safe"*), with `dirty` true on error/conflict - while `composer_draft` has **no version token at all**, so a stale write is undetectable by construction and last-writer-wins. What saves it is `ChronoaApplication` being a `Gio.Application` without `NON_UNIQUE` - **an invariant that is load-bearing and never written down as a reason**, while AGENTS.md records that same enforcement as a real capability gap versus sayri's socket. Also recorded as a checked-and-**refused** candidate: `pages.update` discards `.run()`'s row count on a compare-and-swap, which reads as a silent lost-update until you notice `BEGIN IMMEDIATE` takes SQLite's write lock *before* the read - so it cannot fire, and reporting it would have been the confident wrong answer |
| `HARVEST-COUCOU.md` | coucou (Louis-CFM, 5.3k) | **one thing ported and mutation-proven - the mascot's gaze** (`tools/mutations/gaze_iris.py` 16/16, `gaze.py` 14/14; 0 survivors either side) - **and the headline feature being unportable is still the finding.** Its README says in one sentence what we had measured and refused over - *"GNOME has no layer-shell, so there it runs through XWayland as a dock window at the top of the screen"* - and it **shows the companion anyway**: `prefer_x11_on_gnome()` (l.111) picks the backend *before* GTK init, then `make_non_activating()` (l.370) falls back to a `Dock`-hinted, focus-refusing, keep-above window rather than not drawing. Measured on this box (GTK **4.22.4**, both backends): all three of those knobs - `set_accept_focus`, `set_keep_above`, `set_type_hint`/`Gdk.WindowTypeHint` - **do not exist in GTK4 at all**, and GTK4 exposes **no positioning API whatsoever** (`Gtk.Window.move`, `set_position`, `Gdk.Surface.move`/`get_origin`, `Gdk.Toplevel.move`/`get_position`: all absent). So `available()`'s refusal stands, but **for a different reason than its docstring gives**: not that Mutter lacks layer-shell - which it does, and which they also hit - but that if we ever declined layer-shell we would have no way to place the window and would produce the exact stray bottom-right window the docstring forbids. The real door is process-level and outside this module: `usr/bin/shani-chronoa` already exists to re-exec for the LD_PRELOAD link order, and choosing X11 there is the same shape of fix. Recorded, not taken. Also **already here and ours is the better shape** - their `stateOverride ?? focusTask.state` is a transient value of the *same* enum as the persistent state so the caller cannot tell which is load-bearing, while our `_expr`/`_expr_timer` is a second axis that cannot collide (6 states x 7 expressions); their **character has no accessible name** (`aria-*` is all in `views/`+`settings/`, none in `island/`, `mochi/`, `pill/`, `core/`) where `_announce()` is **ahead of the reference implementation**; their fail-open hook relay is **not applicable** because we have no hook binary and sit in nobody's critical path; their 60fps `requestAnimationFrame` loop has no counterpart in a render-on-state-change SVG. Two bugs recorded without claim: WebKitGTK never paints a first frame in a fresh layer surface (not ours - we rasterise through `GdkPixbuf`), and **GTK resets the input region to the whole window on every map**, which is an open question against `automation_overlay.py` this pass did **not** verify. **Second pass (drag/drop, Parts 5-6), same commit `f09ac0b`:** the recorded paths are relative to **`windows/src/`**, not the repo root - `ls core/anim.ts` at the root finds nothing, which is the "a harvest is a dated claim about a tree" trap in its purest form. Three implementations exist and `CLAUDE.md` states it: `NotchBuddy/` is the macOS Swift original, `windows/` the Tauri port for **Windows and Linux**, `android/` Kotlin - so `core/anim.ts` is a *port*, and the original is `CoucouKit/BotEngine.swift`. **There is no pet-draggable to copy**: the island is a fixed top-edge overlay and Mochi follows a *dragged file*, not a dragged pointer - so what ports is the *follow mechanic* as pure arithmetic (spring follow, a **60/90 lock hysteresis** so a pointer near the threshold does not chatter, `speed < 180` so a fast sweep does not lock, velocity tilt, a 150 ms catch-hop, a mouth spring), not a feature. Their `upload/sequence.ts` header is the transferable sentence: **"the engine owns no DOM and draws nothing"** - `frame()` is a pure function of cursor and drop time. And **two findings in the reference worth keeping**: `anim.ts` claims its eases mirror `BotEngine.swift`'s `enum Ease`, which is **accurate for four and silent about a fifth** (`easeIn` is not in the Swift enum); and the TypeScript tree holds **two copies of the same easing set** - `core/anim.ts` (imported by five files) and a self-contained `upload/sequence.ts` with **zero imports**, whose `back` carries `c1 = 1.70158` against the original's `1.7`. That is this repo's own `wpctl`-three-copies shape found in someone else's tree, which is the argument for our mascot keeping one easing vocabulary. **The hard part of the gaze port was not the geometry**: every test called the handlers *unbound* on a `Mascot` whose `_build()` never ran, so the `GestureMotion` controller was never connected and **three wiring mutations survived** - a witness has to invoke the handler `_build()` *recorded*, and the helper needs patching at **what it reads** (`automation_overlay.reduced_motion`), because a test that patches the helper to `True` is blind to the helper's body by construction | **Third pass (Parts 5-7), and a live defect found by measuring a claim instead of believing it**: `_layer_shell()` read the library with `getattr(gi.repository, namespace)`, and `gi.repository` on this PyGObject **does not import a submodule on attribute access** - measured cold, the `getattr` raises `AttributeError` with the typelib installed and `require_version` satisfied, while `importlib.import_module` returns the real module. So the gate **refused on every machine**, including one whose compositor speaks wlr-layer-shell, and named `GtkLayerShell 0.1` as unusable because the loop kept only its *last* failure. Fixed; `available()` now reports the real cause (Mutter has never implemented wlr-layer-shell, and the preload ordering `usr/bin/shani-chronoa` does not do) instead of a fictional missing package. **And PART 5's premise that window motion is blocked by "GTK4 exposes no positioning API" was false in the direction that removed the feature**: a layer-shell surface is positioned by anchors and margins, `set_margin` is present, and `_bob_tick` **already calls it every frame**. PART 1's own four measurements are about *ordinary* windows and stand; they are qualified in place rather than revised. The real blocker is coucou's own comment - *"a surface that moves under the pointer can't tell where it is"* on Wayland - and their workaround (stretch the surface over the whole display) needs an input region **GTK4 has no API for at all** (`Widget.set_input_region`, `Gdk.Region` and `Gdk.Window.input_shape_*` all absent, measured three ways), so a full-screen surface would eat every click. **Three of four witnesses passed with the defect fully restored**, each for the same reason: **`gi.repository` is process-global and `test_both_library_spellings_are_tried_gtk4_first` warms it** for everything after - so the gate's own witness needed a subprocess probing coldness *first* and discovering the namespaces with `require_version` alone (measured not to warm), because a precondition check that is itself the thing that breaks the precondition is not a precondition check |
| `HARVEST-BWRAP.md` | bubblewrap | the three sandbox protections that belong to the machine |
| `HARVEST-MAZE.md` | Maze-AI | nothing; a short negative result |
| `HARVEST-TencentDB-Agent-Memory.md` | TencentDB-Agent-Memory | **one real port: a finished capability nobody could reach.** Reading its cold-start story ("import the save file, then get to work") prompted a grep, and the answer was measured rather than guessed: `conversation_store.checkpoint()`, `revert()` and `list_checkpoints()` were implemented and documented with **zero callers** anywhere - not the GUI, not a skill, not `app/brain.py` - so a person could neither save that point nor return to it by any route. Ported in `74d2158`. Three of its four flagship ideas were already here (`local_llm.fit_to_context` is turn-aware and already says in a note how many turns it left out; `context_meter.py`; `test_memory_recall_ranking.py`), one is **not applicable** (team ACL visibility on a single-user local-first box - porting it would be cargo cult), and the one genuinely new mechanism, L0->L3 distillation, is a real gap that needs an LLM pass per conversation and was deliberately not ported |

**Read when your change touches them:**
- [Rendering the UI to a PNG](docs/AGENTS-HISTORY.md#s0027) — GTK4/Adw init ordering and
  the capture path
- [Machine-state senses](docs/AGENTS-HISTORY.md#s0030) / [Senses layer](docs/AGENTS-HISTORY.md#s0029) — the sense
  contract
- [Settings window](docs/AGENTS-HISTORY.md#s0028), `Model choice`, `Packaging` — the two
  naming conventions must not be mixed

**Current known issues — read this before you start:**
- [Audit-verified known issues](docs/AGENTS-HISTORY.md#s0063) and
  [Garuda Cross-Reference Findings](docs/AGENTS-HISTORY.md#s0064) — dated retrospectives in
  the archive. **Grep for the subsystem you are changing.** Full
  methodology in `AUDIT-HISTORY.md`.

  These sections mix fixed history with issues that are **still open**,
  including Critical security ones. Grep them for `not fixed`,
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
interrupts TTS mid-reply (`AudioPlayer.stop()`, unconditional), plus a
continuous-VAD layer (`BargeInMonitor`, gated behind
`barge-in-vad-enabled`, **on by default since 2026-10-19** - there is no
acoustic echo cancellation, so on speakers (not headphones) the mic may pick
up Chronoa's own voice and self-interrupt, which is what the switch is there
to turn off).
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

**A command that is not on this box is a slot run, not a workaround and never
a guess.** This dev machine is Ubuntu (see `test_package_names_match_arch.py`),
so an image binary like `arping` or `smartctl` may be absent or behave
differently than on ShaniOS. When a CLI command is the thing being wired, the
honest order is: run it on the image first (`cd ../shani-install-media;
./run_in_container.sh build.sh test ...`, or `slot-test <slot> <name>` after
`bootstrap`), and only then write the parser against its real output.

Measured cost of skipping that step (2026-10-10): `check_internet` needed a
duplicate-address check. `arping -D` is the usual probe, `iputils` ships it on
both images, and it was *not installed here* - so the code was written against
the kernel's ARP cache instead (`ip neigh show`), which needs no extra binary,
sends nothing, and was verified for real on this box. The swap was defensible
on its own merits, but the reason it was chosen was that the tool was missing,
not that the tool was wrong - and its exit-status contract went unmeasured. If
a slot had been used, `arping`'s real status codes would have settled it and
the choice could have been made on the merits instead of the environment.

Two rules that follow, both already stated above in other words:

- **A contract you cannot measure is a contract you do not have.** Exit codes,
  empty-output-on-success, and permission-denied-on-rc-0 are all documented
  for real binaries elsewhere in this file; do not reason one out from a man
  page summary or from another distribution's version.
- **Prefer a mechanism whose behaviour is verifiable here** when the two are
  otherwise equal - but record *why* the preference happened, so a later
  reader can tell a measured decision from an environment's accident.

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
- 🚫 **Never** run `git checkout <dir>`, `git checkout .`, `git stash`, `git stash pop`, `git reset --hard` or `git clean` **anywhere in this repository**. Another session is committing here continuously — it landed `polkit_policy`, `thread_activity`, `graphics_stack` and `autostart_entries` in a single afternoon — and every one of those commands reverts *their* uncommitted work along with yours. Measured cost (2026-10-11): `git checkout -- usr/lib/shani-chronoa/shani_chronoa/` was run to test "is this failure pre-existing", and it reverted the whole package, including `pipewire.py`'s uncommitted `list_streams` and `skills/recording.py`'s `streams` action. Both had to be re-created. **A path operation in a shared tree is a destructive operation.**
  - To ask "is this failure mine or pre-existing": copy **only your own files** to a temp directory, restore **only those** afterwards (`cp`, never `git`), and restore them from your own copy. Never scope the restore wider than the files you changed.
  - To measure at HEAD: `git show HEAD:<path> > /tmp/x.py` and swap that **one file**, then swap your copy back. A test run that needs a wider revert than one file is a test run whose conclusion does not need those files.
  - Back up your own files outside the tree *before* any swap, and keep the backup until the work is committed — this file already records a record lost to a rollback that way, and this is the destructive version.


*Maintenance note: dated audits and change records were moved to
`docs/AGENTS-HISTORY.md`. Keep this index short and current-state; add new
history to the archive, not here.*

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

**2026-11-26 — the Kundali/Panchang engine and its astronomy moved to shani-shell.**
`skills/kundali.py` and `astronomy.py` are gone from this package. shani-shell
owns `bridge/kundali_engine.py` (the plain computation layer) and
`bridge/astronomy.py` (the pure-Python calculations, copied verbatim); its
`bridge/kundali.py` imports the local astronomy module with no path shim and
keeps the QML panel's JSON stdout contract. The skill's `_GROUPS` row, both
modules, and the library-module exclusion in `tests/test_no_unwired_public_helper.py`
were removed with them; `CAPABILITIES.md`, the README counts and the reranker
corpus were regenerated. Nothing under `skills/` calculates the sidereal sky
any more — ask the shell's Kundali bridge, not this registry.

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

## History archive

Dated audits, research sweeps and change records are in
[docs/AGENTS-HISTORY.md](docs/AGENTS-HISTORY.md). Nothing was deleted: the
archive has a linked index of every moved section. Start there for:

- [The dated audit and change record](docs/AGENTS-HISTORY.md#s0001) — the bulk of the history.
- [Audit-verified known issues](docs/AGENTS-HISTORY.md#s0063) and
  [Garuda cross-reference findings](docs/AGENTS-HISTORY.md#s0064) — dated retrospectives that
  mix fixed items with ones still open. Grep for the subsystem you are
  changing and for `still open` before you trust a current-state claim.
- [Implementation Roadmap](docs/AGENTS-HISTORY.md#s0065) — status corrections on the
  old roadmap.

GTK capture internals are in
[Rendering the UI to a PNG](docs/AGENTS-HISTORY.md#s0027); the sense contract is in
[Machine-state senses](docs/AGENTS-HISTORY.md#s0030) and [Senses layer](docs/AGENTS-HISTORY.md#s0029); the old
[Settings window](docs/AGENTS-HISTORY.md#s0028) record is there too.
Add new history to the archive, not to this index.

## Shared architecture (all repos)

One implementation per capability, logic out of UI code, every UI and the
assistant built on the same services: the plan is `../shani-shell/docs/SHARED-ARCHITECTURE.md`, and this
repo's part is `docs/SHARED-PLAN.md`. Read both before changing a backend, a
CLI, a device engine or a settings page, and do as much of this repo's part
as your task allows.
