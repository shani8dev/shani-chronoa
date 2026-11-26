# Headless Chronoa — map for the shell team

This document maps every GTK/Adw surface in Chronoa to the engine function
behind it and to the bridge op the shell can call today. shani-shell
(`../shani-shell/bridge/chronoa-bridge.py`) draws the surfaces in QML; Chronoa
becomes an engine that never creates a GTK window. All facts below were read
from source in this tree on 2026-11-26, not from memory.

---

## 1. Method and counts

### Enumeration

Commands run from the worktree root. `usr/lib/shani-chronoa/shani_chronoa/`
is called `PKG/` below.

1. Total Python files:
   `find usr/lib/shani-chronoa/shani_chronoa -name '*.py' | sort | wc -l`
   → **547** (544 at HEAD plus `engine.py`, `inventory.py`, `ui_gtk/__init__.py`).
2. Broad GI match (candidate pool):
   `grep -rlnE 'from gi\.repository import|gi\.require_version|import gi' PKG --include='*.py'`
   → **111** files.
3. Strict GI statement (real import or `require_version` line, anchored so a
   string or comment cannot match):
   `grep -rlE '^[[:space:]]*from gi\.repository import .*(Gtk|Adw|Gdk|Gio|GtkLayerShell)|^[[:space:]]*gi\.require_version\(["'"'"'](Gtk|Adw|GtkLayerShell|Gdk|Gio)' PKG --include='*.py'`
   → **98** files.
4. GTK-proper subset (drops modules that import only `Gio` for DBus/settings):
   same expression with `Gio` removed from both alternations → **78** files.
   `comm -23 strict gtk_proper` → **20** Gio-only files.
5. Layer-shell users (verified by reading the lines, not by counting text):
   `grep -rlnE 'GtkLayerShell|Gtk4LayerShell' PKG --include='*.py'` returns
   `mascot.py`, `automation_overlay.py`, `ui_driver.py`; only the first two
   contain a real `from gi.repository import Gtk4LayerShell` /
   `importlib.import_module(...)` call. `ui_driver.py` mentions it in comments
   only. **2** genuine layer-shell modules.
6. `gi.require_version("Gtk"|"Adw")` statement lines appear in **75** files.

### False positives distinguished

- The broad pattern `import gi` is a substring of `import git` and
  `import gives`. It wrongly matched `skills/resolve_conflict.py`
  (`from shani_chronoa.senses import git`), `skills/vcs_status.py`
  (`... import git_state`) and `voice_clone.py` (prose "at import gives a").
- Ten files import a non-GTK GI namespace only: `GLib` (`asyncbridge.py`,
  `browser_bridge.py`, `engine.py`, `gui/browser_driver.py`, `imagegen.py`),
  `Atspi` (`senses/accessibility.py`, `skills/input_control.py`,
  `skills/ui_elements.py`), `ECal`/`EDataServer`/`ICalGLib` (`eds_calendar.py`),
  `Secret` (`secret_store.py`). `gui/browser.py` imports `Gtk` genuinely (it
  is strict); its `WebKit` import is extra.
- Nine files were caught by an early attribute-use regex only because
  `Gtk.`/`Adw.`/`Gdk.`/`Gio.` appeared in a docstring or comment:
  `approvals.py`, `cloud_voice.py`, `gui/__init__.py`,
  `gui/surfaces/__init__.py`, `markdown_lite.py`, `mascot_drag.py`,
  `pages.py`, `settings_window/__init__.py`, `tts.py`. They are **not** strict.
- `111 − 13 = 98`: the 13 broad-only files are the substring/non-GTK cases
  above. All 98 strict files were checked to have a real statement line.

### Headline counts

| measure | count |
|---|---|
| `.py` files total | 547 |
| broad (`from gi.repository import` / `gi.require_version` / `import gi`) | 111 |
| **strict** (real import/`require_version` of Gtk, Adw, Gdk, Gio, GtkLayerShell) | **98** |
| GTK-proper (excludes `Gio`-only plumbing) | 78 |
| `Gio`-only (DBus, settings, app-info, window managers; no GTK widget) | 20 |
| genuine layer-shell users | 2 |

The strict set, grouped: `app/` 7, `gui/` 21, `gui/surfaces/` 39,
`settings_window/` 6, top-level 25 (including 3 under `skills/` and 3 under
`windows/`). Sum = 98.

---

## 2. Strict-importing modules

`GI` column: **GTK** = imports Gtk/Adw/Gdk/GtkLayerShell; **Gio** = imports
`Gio` only. "Engine calls" lists domain engine modules imported; `core` means
a generic `from shani_chronoa import …`; `—` means presentation only.

### `app/`

| path | GI | what it does for a person | engine calls |
|---|---|---|---|
| `app/application.py` | GTK | Startup, window, GActions, CLI args | agent_service, assistant, audio, config, gateway, presence, senses, stt, tts, triggers, wakeword, cloud_llm/ollama_llm |
| `app/brain.py` | GTK | Chooses which LLM answers; starts setup when none can | cloud_llm, ollama_llm, presence, redaction, tool_tracking, tts |
| `app/common.py` | GTK | Constants shared inside `app/` | — |
| `app/conversation.py` | GTK | Turns text into a reply and saves the conversation | assistant, senses, gui.blocks, gui.quick_ask |
| `app/desktop_integration.py` | GTK | Global shortcut, autostart, background mode, settings window, debug log | settings_window, gui.browser |
| `app/tray.py` | Gio | Tray icon when the desktop offers one (GTK4 has no `StatusIcon`) | gui (menu only) |
| `app/voice.py` | GTK | Listening, wake phrase, transcription, sentence-by-sentence speech, barge-in | audio, stt, tts, config |

### `gui/`

| path | GI | what it does for a person | engine calls |
|---|---|---|---|
| `gui/about.py` | GTK | The About and welcome windows | core |
| `gui/asking.py` | GTK | A pending question: shown, answered, or abandoned | gui.blocks, gui.locale |
| `gui/attaching.py` | GTK | Files for the next message: drop, pick, list, remove | core |
| `gui/blocks.py` | GTK | Turns a reply into blocks and widgets | skills.undo_last_change, words, gui.browser, gui.surfaces.diff |
| `gui/browser.py` | GTK | In-app web browser; hands the page to the conversation | WebKit, gui.locale |
| `gui/charts.py` | GTK | Ring, bars, ranked bars, meter, line, stacked strip | — |
| `gui/code_hl.py` | GTK | Syntax highlighting in replies | core |
| `gui/context_bar.py` | GTK | Shows how full the model's context window is | gui.locale |
| `gui/conversations_menu.py` | GTK | Conversations menu and saved transcript redraw | core |
| `gui/minimap.py` | GTK | One tick per turn for long conversations | — |
| `gui/model_picker.py` | GTK | Which model answers, and why that one | gui.surfaces, core |
| `gui/organs.py` | GTK | Live strip of what Chronoa is doing | core |
| `gui/palette.py` | GTK | Command palette | gui.locale |
| `gui/pdf_render.py` | GTK | First PDF page to a pixbuf via `pdftoppm` | — |
| `gui/questions.py` | GTK | Tool-asked questions (`ask_user`, permission prompts) | core |
| `gui/quick_ask.py` | GTK | One question, one answer, nothing kept unless asked | gui.locale |
| `gui/rail.py` | GTK | Right rail: activity, obligations, arming | gui.surfaces.goals, sandbox.qualify, skills, words |
| `gui/sidebar.py` | GTK | What this machine can be asked, and current thinking | gui.surfaces, gui.locale |
| `gui/style.py` | GTK | Stylesheet and reduced motion | — |
| `gui/task_card.py` | GTK | The plan in chat, where it cannot scroll away | skills, core |
| `gui/timers.py` | GTK | Pending timers, seen and stopped | skills.timer |
| `gui/widgets.py` | GTK | Orb, transcript, suggestion bar, help window, states | gui.locale, words |
| `gui/window.py` | GTK | Main window: layout, state, transcript, text input | config, senses, skills, cloud_llm, gui.surfaces.machine |

### `gui/surfaces/` — the panels

| path | GI | what it does for a person | engine calls |
|---|---|---|---|
| `gui/surfaces/activity.py` | GTK | Tool-call audit trail | core, words |
| `gui/surfaces/artifact_store.py` | GTK | Where models, voices, documents, exports live | words |
| `gui/surfaces/calendar.py` | GTK | The user's calendar | capabilities, skills |
| `gui/surfaces/common.py` | GTK | Shared Adwaita scaffolding for surfaces | capabilities, config |
| `gui/surfaces/compare.py` | GTK | Compare two conversations by questions asked | core, words |
| `gui/surfaces/connections.py` | GTK | The two ways something reaches Chronoa | core, words |
| `gui/surfaces/conversations.py` | GTK | Full page of saved conversations | core |
| `gui/surfaces/daemon.py` | GTK | Background mode, user unit, daemon log | core |
| `gui/surfaces/dashboards.py` | GTK | Dashboards Chronoa built; see and open | core |
| `gui/surfaces/desktop.py` | GTK | Which desktop integration is connected now | app, capabilities |
| `gui/surfaces/diagnostics.py` | GTK | Is each part of Chronoa working, and why not | files, sandbox, skills, tts, gui.surfaces.voice |
| `gui/surfaces/diff.py` | GTK | File-by-file diff, like Claude Code's diff panel | skills.undo_last_change, words |
| `gui/surfaces/driving.py` | GTK | Can Chronoa drive an app on this desktop | core (`ui_driver`) |
| `gui/surfaces/editor.py` | GTK | Open, change and save a file through skills | skills |
| `gui/surfaces/export.py` | GTK | Get things out of a local-first assistant | core, words |
| `gui/surfaces/git_panel.py` | GTK | Repo state and the two gated writes | skills |
| `gui/surfaces/goals.py` | GTK | Saved multi-step plans and run controls | core, words |
| `gui/surfaces/hub.py` | GTK | One page for a job split across panels | gui.surfaces only |
| `gui/surfaces/inventory.py` | GTK | What Chronoa is, function by function | gui.locale only |
| `gui/surfaces/learning.py` | GTK | What Chronoa learned here; move it elsewhere | core, words |
| `gui/surfaces/listening.py` | GTK | Can Chronoa transcribe what an app plays | config, sandbox.profiles |
| `gui/surfaces/machine.py` | GTK | What this machine says about itself | senses |
| `gui/surfaces/meetings.py` | GTK | Record, keep and read back a meeting | core, words |
| `gui/surfaces/memory.py` | GTK | Percept store, fact by fact | senses.memory |
| `gui/surfaces/model.py` | GTK | Running model, serving engine, extras | cloud_llm, ollama_llm, hardware_profile, opencv, skills |
| `gui/surfaces/models.py` | GTK | On-machine and available models, with search | cloud_llm, config, presence |
| `gui/surfaces/my_files.py` | GTK | Searchable own files | core, words |
| `gui/surfaces/notices.py` | GTK | Things Chronoa noticed, newest first | core, words |
| `gui/surfaces/privacy.py` | GTK | Privacy mode, egress events, per-sense consent | capabilities, config, senses |
| `gui/surfaces/processes.py` | GTK | Everything Chronoa started, with stop buttons | core |
| `gui/surfaces/routines.py` | GTK | One phrase standing for a whole request | core |
| `gui/surfaces/sandbox.py` | GTK | Where commands run, and proof | sandbox |
| `gui/surfaces/senses.py` | GTK | Every known sense and its consent switch | capabilities, config, senses |
| `gui/surfaces/skills.py` | GTK | Filterable list of what Chronoa can do | skills |
| `gui/surfaces/today.py` | GTK | Everything with a date or due time | skills |
| `gui/surfaces/triggers.py` | GTK | Trigger rules, read-only list | triggers, triggers.common, senses |
| `gui/surfaces/voice.py` | GTK | What Chronoa speaks with, and what else is installed | audio, tts, senses, setup_wizard |
| `gui/surfaces/workbench.py` | GTK | A command's man page turned into a skill | core |

Note: `gui/surfaces/` also holds five `*_hub.py` modules — `models_hub.py`,
`automation_hub.py`, `changes_hub.py`, `health_hub.py`, `privacy_hub.py`. They
do not import `Gtk` directly: each imports `shani_chronoa.gui.surfaces.hub`,
which builds the widget. They are covered by the `hub.py` row above, so the
strict count is 39 surface modules, not 44.

### `settings_window/`

| path | GI | what it does for a person | engine calls |
|---|---|---|---|
| `settings_window/window.py` | GTK | Settings pages, search, shared row helpers | core |
| `settings_window/privacy.py` | GTK | Privacy mode, cloud fallback, API keys, permission presets, action permissions | capabilities, config |
| `settings_window/senses.py` | GTK | Every sense with consent switch, grouped | config, senses |
| `settings_window/voice.py` | GTK | Listening, wake phrase, speech output, speed, models, devices | core (config/tts) |
| `settings_window/activity.py` | GTK | Session approvals and live tool-activity log | tool_tracking, verification |
| `settings_window/email.py` | GTK | Email account setup | core |

### top-level modules

| path | GI | what it does for a person | engine calls |
|---|---|---|---|
| `agent_service.py` | Gio | Owns `dev.shani.chronoa.Agent`: Turn, Approve, Write, FindFiles, Event | ask_bridge, agent_contract, events, app.application |
| `automation_overlay.py` | GTK | Cue layer seen when Chronoa drives a screen; layer-shell overlay | — |
| `away.py` | GTK | What Chronoa did while the screen was locked | desktop_cue, gui.surfaces |
| `channel_bridge.py` | Gio | Thin adapters feeding the inbound gateway | core |
| `config.py` | Gio | Config store: GSettings, privacy mode, keys, senses | capabilities, cloud_llm |
| `desktop_cue.py` | GTK | Says on screen what Chronoa is doing, per tool call | gui.blocks, skills |
| `gateway.py` | Gio | Inbound text channel `Submit(gateway, text) -> reply` | core |
| `ibus_input.py` | Gio | Types text into any app via IBus | — |
| `landline/__init__.py` | Gio | JioFiber landline client through shani-link | — |
| `link_app.py` | Gio | Shani Link app: phone/watch/device screens | — |
| `mascot.py` | GTK | Layer-shell desktop companion | automation_overlay |
| `mascot_notch.py` | GTK | Companion as a GNOME Shell top-edge notch | core |
| `mascot_sounds.py` | Gio | One note per mascot decision | — |
| `mic_watch.py` | Gio | Watches for an app starting to use the mic | — |
| `portal.py` | Gio | xdg-desktop-portal RemoteDesktop and GlobalShortcuts | core |
| `search_provider.py` | Gio | Ask Chronoa from GNOME overview / Plasma KRunner | skills |
| `setup_wizard.py` | GTK | First-run setup: think, hear, speak | audio, cloud_llm, config, distill, ollama_llm, opencv, redaction, tts |
| `skills/list_apps.py` | Gio | Skill: list launchable applications | skills |
| `skills/list_shortcuts.py` | Gio | Skill: this machine's keyboard shortcuts | skills |
| `skills/open_application.py` | Gio | Skill: launch an app by name | skills, windows |
| `ui_driver.py` | GTK | Drive any app window the way `browse` drives a page | capabilities, config, files, windows.niri |
| `windows/atspi.py` | Gio | Windows through the accessibility bus | windows |
| `windows/gnome.py` | Gio | Window control via Chronoa Shell extension | windows |
| `windows/kwin.py` | Gio | Window control via KWin scripting API | windows |

---

## 3. What each window/panel needs from the engine

### Existing interfaces (verified in source)

- **`agent_service.py` / `agent_contract.py`** — D-Bus name
  `dev.shani.chronoa.Agent`, path `/dev/shani/chronoa/Agent`. Methods
  `Turn(text)->turn_id`, `Approve(turn_id, decision)->ok`,
  `Write(instruction, text)->result`, `FindFiles(query, limit)->json`,
  `Inventory(section)->json`;
  signal `Event(json)`. Event kinds in `agent_service.py`: `tool`,
  `tool_done`, `reply`, `state`, `approval`, `approval_closed`, `notice`,
  `selection`, `error`, `done`. Every event carries `turn`.
- **`gateway.py`** — `dev.shani.chronoa.Gateways`, `Submit(gateway, text)->reply`,
  ask-only by default, rate and length capped. Fallback used by the bridge
  when no Agent is on the bus.
- **`mcp.py`** — `build_server()` exposes every loaded skill as an MCP tool
  with consent annotations (`capabilities.tool_annotations`). Stdio server,
  no panel of its own.
- **shell bridge ops** in `../shani-shell/bridge/chronoa-bridge.py`:
  `ask` (Agent.Turn, else Gateways.Submit), `approve` (Agent.Approve),
  `write` (Agent.Write), `findfiles` (Agent.FindFiles), `action` (GAction
  `show-page:<id>`, otherwise `org.freedesktop.Application.ActivateAction`),
  `ping`, `geometry`, `locked`, `release-sleep`. The last three are
  shell-internal, not engine calls.
- **Chronoa → shell D-Bus**, already bridged: `org.shani.Chronoa.Mascot`
  (`Status`, `Art`, `Rect`, `Notch`, `PanelHeight`, `Pointer`, `Show`, `Scale`,
  `Host`) exported by `mascot_notch.py`; `org.shani.Chronoa.Windows.Cue(text,
  hold)` exported by `desktop_cue.py`; the bridge also proxies
  `logind`/`screensaver` lock state.

### Engine functions that really exist (checked)

- Conversations: `conversation_store.list_sessions`, `load`, `search`,
  `switch`, `rename`, `delete`, `fork`, `export_markdown`, `checkpoint`,
  `revert`, `list_checkpoints`, `new_session`.
- Skills: `skills.discover_skills() -> (tools, handlers)`.
- Senses: `senses.discover_senses() -> dict[str, Sense]`.
- Capabilities/permissions: `capabilities.live_capabilities()`,
  `find_capabilities`, `PRESETS`, `apply_preset`, `current_preset`,
  `ACTION_CONSENT_KEYS`, `DESTRUCTIVE_CONSENT_KEYS`.
- Routines: `routines.load()`, `save_all()`, `expand()`.
- Triggers: `triggers.rules.RuleStore` (`all`, `names`, `get`, `add`,
  `remove`, `clear`) and `TriggerEngine.evaluate*`.
- Privacy switches: `config.ChronoaConfig.privacy_mode`,
  `cloud-fallback-enabled`, `*-api-key` ( Secret Service via `secret_store`),
  plus `capabilities` presets.
- Memory: `senses.memory.get_store`, `about`, `memory_history`,
  `pending_proposals`, `decide_candidate`, `remember_fact`.
- Setup: `setup_wizard.state()`, `needs_setup()`, `stt_catalogue()`,
  `setup_brain`, `setup_ears`, `setup_voice`, `setup_eyes`, `setup_memory`,
  `setup_languages`, `setup_kokoro` (the wizard's `build_window()` is the GTK
  part; the setup steps themselves are engine code).
- Approvals/activity: `approvals.ask`, `confirm`, `NotifyApprover`;
  `tool_tracking.ToolTracker.get_calls`, `log_files`, `export_csv`;
  `verification.post_condition_for`, `verify`.
- Models: `model_choice.tasks/describe/resolve/explain`,
  `model_service.start/stop/is_up/read_env/write_env`.

### New engine op already implemented: `Agent.Inventory(section) -> json`

`shani_chronoa/inventory.py` is the engine-side reader for the panels below.
It imports no GTK, caps every list at 100 items, and answers an unknown
section with an error that names the allowed sections rather than with an
empty list. One broken store fails only its own section. The engine method is
in `agent_service.py`; the **shell bridge still needs a read op** that calls
it (e.g. `{"op":"inventory","id":…,"section":"models"}` → `{"type":"inventory",
"id", "items"}`). No QML was written here.

| section | panel it replaces | engine source |
|---|---|---|
| `conversations` | Conversations, compare, minimap | `conversation_store.list_sessions` |
| `models` | Model picker, Models page, context bar | `model_choice.resolve/explain` |
| `skills` | Skills page | `skills.discover_skills` + `capabilities.gated_by` |
| `routines` | Routines page | `routines.load/phrases` |
| `triggers` | Triggers page | `triggers.rules.RuleStore.all` |
| `privacy` | Privacy page | `ChronoaConfig`, `egress`, `discover_senses` |
| `senses` | Senses page | `discover_senses` + consent keys |
| `memory` | Memory page | `senses.memory.get_store().durable()` |
| `capabilities` | Sidebar, Inventory page | `capabilities.live_capabilities()` |
| `status` | Diagnostics (engine half) | config, `local_llm.installed`, STT/TTS probes |

Sections are read-only. Setup, writes, approvals and the remaining ops in the
table below are still to be provided by the shell bridge or by later engine
interface work. `tests/test_agent_inventory.py` covers every section.

### Per window/panel

| window / panel | bridge path | payload / note |
|---|---|---|
| Main chat — ask a turn, stream reply | **EXISTING** `ask` | `{"op":"ask","text":…}` → `turn` id + `event` frames; `apply` events to QML transcript |
| Main chat — approval card | **EXISTING** `approve` + Agent.Event | `event:"approval"` gives `id,text,options,detail`; send chosen **label**, not a code |
| Main chat — rewrite text with LLM | **EXISTING** `write` | `{"op":"write","id","instruction","text"}` → `written` |
| Main chat — file search "by meaning" | **EXISTING** `findfiles` | `{"op":"findfiles","id","query"}` → `found` hits |
| Main chat — navigation, palette, toggles | **EXISTING** `action` | `{"op":"action","name":"show-page:<id>"}` or any GAction: `quick-ask`, `toggle-listening`, `toggle-privacy`, `toggle-wake-word`, `toggle-barge-in-vad`, `toggle-cloud-fallback`, `toggle-model-download`, `toggle-plan-mode`, `toggle-sidebar`, `toggle-rail`, `toggle-debug`, `setup`, `setup-page`, `open-browser`, `open-settings`, `show-window`, `show-body`, `quit`, `zoom-*`, `dictate`, `transcribe-file`, `stop-speaking`, `answer-question`, `choose-project`, `clear-project`, `connect-device`, `delete-conversation`, `reset-conversation`, `switch-conversation`, `find-in-conversation`, `download-speech-model`, `reload-mcp`, `toggle-auto-start`, `toggle-start-hidden` |
| Saved conversations / compare / minimap | **ENGINE READY** `inventory(conversations)`; shell bridge op needed | `{"op":"inventory","section":"conversations","limit":…}` → ids, titles, update times, message counts. Load/switch/rename/delete/fork/search/export/checkpoint still need their own (write) ops |
| Attachments on next turn | **NEW op needed: `ask-with-files`** | `{"op":"ask-with-files","text","paths":[...]}` → same turn/event stream. Engine has `attachments.py`; `Ask` only takes text today |
| Quick Ask (no history kept) | **NEW op needed: `ask` option** | add `ephemeral:true` to `ask`; maps to `app/conversation.py` quick-ask path. Plain `ask` persists |
| Hand a web page to the conversation | **NEW op needed: `turn-page`** | `{"op":"turn-page","url","title","text"}` → turn/event stream; replaces `gui/browser.py` |
| Model picker / context bar | **ENGINE READY** `inventory(models)` + `inventory(status)`; shell bridge op needed | Model ids + reasons + service up/down; `choose` is a write op, still to be designed. Context-window fill is not in Inventory |
| Sidebar "what can be asked" | **ENGINE READY** `inventory(skills)` + `inventory(capabilities)`; shell bridge op needed | `{name,description,gate}` from `discover_skills()` and `capabilities.live_capabilities()`; setting a gate stays a settings write |
| Tool activity / session approvals | **NEW op needed: `activity`** | `{"op":"activity","action":"list","since","limit"}` → recent `ToolCallRecord`s + post-condition verdicts; no bridge op today |
| Settings — privacy, senses, voice, keys | **READ SIDE READY** `inventory(privacy)` + `inventory(senses)`; shell bridge op needed; writes still need `settings` | Consent keys and their on/off state, privacy mode, egress count. `inventory` never returns secret values; `set` stays a separate consented op |
| Settings — permission presets | **NEW op needed: `settings`** | `{"action":"preset","name":…}` → `capabilities.PRESETS` / `apply_preset` / `current_preset` |
| Setup wizard | **NEW ops needed: `setup-state`, `setup-step`** | `setup-state` → `state()`, `needs_setup()`, `stt_catalogue()`; `setup-step {"step","value"}` → calls `setup_*`, emits progress events. Wizard's GTK `build_window()` is not reusable |
| Routines panel | **ENGINE READY** `inventory(routines)`; shell bridge op needed | Saved routines and their phrases; `expand` needs its own read op |
| Triggers panel | **ENGINE READY** `inventory(triggers)`; shell bridge op needed | `RuleStore.all()` as dicts; read-only, no write op proposed |
| Memory / percepts | **ENGINE READY** `inventory(memory)`; shell bridge op needed | Durable facts, source, time and metadata; `about`, `history`, `proposals` and consent-gated `decide` still need their own ops |
| Notices / "away" summary | **NEW op needed: `notices`** | `{"op":"notices","action":"list\|dismiss","id"}` → queued notices and why |
| Calendar / Today / Meetings | **NEW op needed: `schedule`** | `{"op":"schedule","action":"events\|month\|meetings"}` → EDS calendar events and stored meetings |
| Skills / Inventory / Diagnostics / Driving / Sandbox | **NEW op needed: `capabilities` + `diagnostics`** | `diagnostics {"action":"probe"}` → per-subsystem ok/reason (sandbox, seccomp, senses, tts, file perms) |
| Files / Editor / Diff / Git / Workbench / My files / Export | **NEW op needed: `file` and `invoke`** | `file {"action":"read\|list\|diff","path"}` → text/diff (read-mostly). `invoke {"skill","args"}` → existing whitelist via `tools.execute_tool_outcome`, consent-gated; needed for save/git writes |
| Processes / Daemon / Learning / Artifacts | **NEW op needed: `runtime`** | `{"op":"runtime","action":"processes\|daemon\|learning\|artifacts"}` → started children + stop handles, user-unit status, learned models, download paths |
| Voice / Listening panels | **NEW op needed: `voice`** | `{"op":"voice","action":"engines\|devices\|catalogue"}` → TTS engines, audio devices, STT catalogue from `setup_wizard.stt_catalogue()` |
| Companion (mascot/notch) and tool-call cue | **EXISTING** bridge surface | shell owns `org.shani.Chronoa.Mascot` and `org.shani.Chronoa.Windows`; Chronoa drives it via `Status/Art/Rect/Notch/PanelHeight/Pointer/Show/Scale/Host` and `Cue`. `geometry` op feeds `Rect()` back |

The bridge's `ask`, `approve`, `write`, `findfiles`, `action`, `ping` already
cover the conversation loop end to end. Every "NEW op" above is a
**read-mostly** query for data that today is only reachable by constructing a
GTK widget and reading its model.

---

## 4. Engine vs UI verdict

### Pure UI — safe behind a UI package the engine never imports

No domain engine call; only local drawing/constants. Rebuild directly in QML,
keep out of the engine:

`gui/charts.py`, `gui/context_bar.py`, `gui/minimap.py`, `gui/palette.py`,
`gui/pdf_render.py`, `gui/style.py`, `gui/widgets.py` (widget construction only),
`gui/surfaces/common.py`, `gui/surfaces/hub.py`, `gui/surfaces/inventory.py`
(no engine imports at all), `app/common.py`, `mascot_sounds.py`,
`mic_watch.py`, and `skills/list_apps.py`, `skills/list_shortcuts.py`,
`skills/open_application.py`, `windows/atspi.py`, `windows/gnome.py`,
`windows/kwin.py` (Gio plumbing, no widget).

### Mixed engine logic + GTK — need a bridge op before the GTK part can go

These import a domain engine module and must keep their logic on the engine
side; the shell draws only the result.

| GTK module | engine logic that must stay | op that must carry it |
|---|---|---|
| `app/application.py` | turn dispatch, actions, component startup | `ask`, `action`, `runtime`, `voice` |
| `app/brain.py` | LLM selection and setup trigger | `models` |
| `app/conversation.py` | turn → reply, persistence | `ask`, `conversations` |
| `app/voice.py` | listening/wake/barge-in state machine | `voice`, `ask` |
| `app/desktop_integration.py` | shortcut, autostart, background | `settings`, `runtime` |
| `gui/window.py`, `gui/asking.py`, `gui/questions.py`, `gui/blocks.py`, `gui/task_card.py`, `gui/timers.py` | transcript, approvals, task plan, timers | `ask`, `approve`, `activity` |
| `gui/model_picker.py`, `gui/organs.py`, `gui/sidebar.py`, `gui/rail.py`, `gui/quick_ask.py` | model choice, organs, capabilities, goals | `models`, `capabilities`, `ask` with `ephemeral`, `routines` |
| `gui/browser.py`, `gui/attaching.py`, `gui/conversations_menu.py`, `gui/code_hl.py` | page-to-turn, attachments, saved history, reply rendering | `turn-page`, `ask-with-files`, `conversations` |
| `gui/surfaces/*` (all domain panels) | read models from conversation_store, senses, skills, config, routines, triggers, memory, tool_tracking | `conversations`, `senses`, `settings`, `skills`, `routines`, `triggers`, `memory`, `activity`, `diagnostics`, `runtime` |
| `settings_window/*` | GSettings writes, permission presets, approvals/activity, email setup | `settings`, `activity` (plus a dedicated `email` settings group) |
| `setup_wizard.py` | setup steps are engine code; only `build_window()` is GTK | `setup-state`, `setup-step` |
| `mascot.py`, `mascot_notch.py`, `automation_overlay.py`, `desktop_cue.py`, `ui_driver.py` | gaze, cue text, window control | **already bridged**: Mascot/Windows D-Bus + `geometry` |
| `agent_service.py`, `gateway.py`, `mcp.py` | Gio-only, no widget; already engine-side | `ask`, `approve`, `write`, `findfiles`; MCP needs no panel |

Not bridged today and not proposed as ops: the fixed-whitelist skill
execution path (`tools.execute_tool_outcome`) is reachable only through the
Agent turn loop; a direct `invoke` op would be a new authority surface and
needs its own consent decision before the shell team builds against it.
