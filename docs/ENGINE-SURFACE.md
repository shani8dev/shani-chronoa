# Engine surface — everything the engine can do, in every client

One row per capability. The engine owns the logic; GTK, the shell panel, the
terminal client and the command line are four views of the same function.
Sources were read from this tree on 2026-11-26 (the GTK split is on disk: GTK
code is under `usr/lib/shani-chronoa/shani_chronoa/ui_gtk/`). `PKG/` below is
`usr/lib/shani-chronoa/shani_chronoa/`.

Columns: what it does for a person | where the logic lives now | engine module
it belongs in | Agent method | TUI slash | CLI subcommand | R/W | consent gate.

## Already exposed (before this work)

| capability | now | engine module | Agent method | slash | CLI | R/W | gate |
|---|---|---|---|---|---|---|---|
| ask a turn, stream reply, approve | `ui_gtk/app/conversation.py:_submit`, `agent_service.py:Turn` | `assistant.py`, `ask_bridge.py` | `Turn` | free text, `/quit` | `chronoa -p` | R/W (skill whitelist) | per-tool `ACTION_CONSENT_KEYS` |
| ask with files / no history / resume | `agent_service.py:TurnWith`, `attachments.py:accept/block` | `attachments.py`, `conversation_store.py` | `TurnWith` | `@file`, `/new`, `/resume` | `-p` options | R/W | same as a turn |
| approve or deny a pending step | `agent_service.py:Approve`, `permissions.py:parse_reply` | `permissions.py` | `Approve` | numbered choice | `--allow LABEL` | W | explicit reply, never implied |
| stop a running turn | `agent_service.py:Cancel` | `asyncbridge.py` | `Cancel` | Ctrl+C | — | W | none (only own turn) |
| rewrite selected text | `agent_service.py:Write` | `llm_choice.py` | `Write` | — | — | R/W | none (no tools) |
| find own files by meaning | `agent_service.py:FindFiles`, `file_index.py` | `file_index.py` | `FindFiles` | — | — | R | `file-index-enabled` |
| read-only panels | `inventory.py:*`, `ui_gtk/gui/surfaces/*` | `inventory.py` | `Inventory` | `/models /skills /memory /routines /triggers /privacy /senses /status /conversations` | `chronoa <section>` | R | none |

## Moved into engine modules and exposed now

| capability | now | engine module | Agent method | slash | CLI | R/W | consent gate |
|---|---|---|---|---|---|---|---|
| model choice: Ollama else local, cloud fallback, answer mode | `ui_gtk/app/brain.py:_use_local_server/_maybe_enable_cloud_fallback/select_llm`, `config.py:answer_mode` | `llm_choice.py` | `ModelsList`, `ModelsUse` | `/models`, `/models use NAME` | `chronoa models`, `chronoa models use NAME` | R/W | cloud fallback: `cloud-fallback-enabled` + privacy off; `confirm` for off-machine |
| read and write named settings (readable labels) | `ui_gtk/settings_window/privacy.py`, `senses.py`, `voice.py`, `config.py:set` | `engine_ops/settings.py` | `SettingsList`, `SettingsGet`, `SettingsSet` | `/settings`, `/settings get NAME`, `/settings set NAME VALUE` | `chronoa settings [get\|set NAME VALUE]` | R/W | turning a `GATED`/`DESTRUCTIVE_CONSENT_KEYS` key ON, or any `*-api-key`, needs `confirm` |
| permission presets | `ui_gtk/settings_window/privacy.py:_presets_row`, `capabilities.py:apply_preset/current_preset` | `engine_ops/settings.py` | `SettingsPreset` | `/settings preset NAME` | `chronoa settings preset NAME` | W | `confirm` (turns many consent keys on) |
| email account setup | `ui_gtk/settings_window/email.py:_build_email` | `engine_ops/settings.py` | `SettingsEmail` | `/settings email JSON` | `chronoa settings email --stdin` | W | `confirm`; secret only on stdin or the string arg |
| first-run setup state and steps | `ui_gtk/setup_wizard.py:state/needs_setup/stt_catalogue/setup_*` | `engine_ops/setup.py` | `SetupState`, `SetupStep` | `/setup`, `/setup step STEP VALUE` | `chronoa setup`, `chronoa setup step STEP VALUE` | R/W | `model-download-enabled`; `confirm` for downloads |
| conversations list / resume / rename / delete | `ui_gtk/app/conversation.py:_open_conversation/_delete_conversation`, `conversation_store.py:*` | `engine_ops/conversations.py` | `Conversations`, `ConversationsResume`, `ConversationsRename`, `ConversationsDelete` | `/conversations`, `/conversations resume\|rename\|delete` | `chronoa conversations [resume\|rename\|delete]` | R/W | `confirm` for delete |
| memory facts and proposals, decide a proposal | `ui_gtk/gui/surfaces/memory.py:_read`, `senses/memory.py:pending_proposals/decide_candidate` | `engine_ops/memory.py` | `MemoryList`, `MemoryDecide` | `/memory`, `/memory decide ID yes\|no` | `chronoa memory`, `chronoa memory decide ID yes\|no` | R/W | `memory-sense-enabled` to read; `confirm` to write a fact |
| routines list and run | `ui_gtk/gui/surfaces/routines.py`, `routines.py:load/expand` | `engine_ops/automation.py` | `Routines`, `RoutinesRun` | `/routines`, `/routines run NAME` | `chronoa routines`, `chronoa routines run NAME` | R/W | routine's own consent keys at run time |
| triggers list | `ui_gtk/gui/surfaces/triggers.py`, `triggers/rules.py:RuleStore.all` | `engine_ops/automation.py` | `Triggers` | `/triggers` | `chronoa triggers` | R | none |
| notices list and dismiss | `ui_gtk/gui/surfaces/notices.py`, `notices.py:list_all/dismiss` | `engine_ops/automation.py` | `Notices`, `NoticesDismiss` | `/notices`, `/notices dismiss ID` | `chronoa notices`, `chronoa notices dismiss ID` | R/W | none |
| voice engines, devices and catalogue | `ui_gtk/gui/surfaces/voice.py:_chain/_voice_catalogue/_speech_input`, `ui_gtk/settings_window/voice.py:_device_picker` | `engine_ops/voice.py` | `VoiceEngines` | `/voice` | `chronoa voice` | R | none |
| tool activity and approval log | `ui_gtk/gui/surfaces/activity.py:_records`, `tool_tracking.py:get_calls`, `settings_window/activity.py` | `engine_ops/health.py` | `Activity` | `/activity` | `chronoa activity` | R | none |
| diagnostics probe | `ui_gtk/gui/surfaces/diagnostics.py:*` (the probe functions, not the widget) | `engine_ops/health.py` | `DiagnosticsProbe` | `/diagnostics` | `chronoa diagnostics` | R | none |
| quick ask (one answer, nothing kept) | `ui_gtk/app/conversation.py:quick_ask`, `ui_gtk/gui/quick_ask.py` | `assistant.py` via `TurnWith` | `TurnWith{ephemeral:true}` | `/ask FILE` | `chronoa -p --ephemeral` | R/W | same as a turn |
| page handed to the conversation | `ui_gtk/gui/browser.py:text_for_attachment`, `ui_gtk/app/desktop_integration.py:_browser_attach` | `attachments.py` + `TurnWith` | `TurnWith{paths:[text]}` | `@file` | `-p` with `@file` | R/W | `web-sense-enabled` to fetch |

## Still GTK-only by design (no engine logic to move)

`ui_gtk/gui/charts.py`, `context_bar.py`, `minimap.py`, `palette.py`,
`pdf_render.py`, `style.py`, `widgets.py`, `surfaces/common.py`, `hub.py`,
`surfaces/inventory.py`, `ui_gtk/app/common.py`, `mascot_sounds.py`,
`mic_watch.py`, the `list_apps`/`list_shortcuts`/`open_application` skills and
the three `windows/*` desktop adapters: local drawing or Gio plumbing only,
rebuilt in QML by the shell. The mascot, cue, window control and skill
execution path stay behind their existing D-Bus surfaces (HEADLESS.md §4).

## Consent rule for every write

A write that turns a consent or privacy key ON, writes an API key, starts a
download, or sends data off the machine carries a `confirm` argument. The
engine refuses when it is false; the terminal client asks `y/N`, and the
command line needs `--yes`. Secrets travel in the `SettingsSet` string
argument or on stdin, never in argv, logs or output. Existing methods are
additive: none of `Turn`, `TurnWith`, `Approve`, `Cancel`, `Write`,
`FindFiles`, `Inventory` or `Event` changed shape.
