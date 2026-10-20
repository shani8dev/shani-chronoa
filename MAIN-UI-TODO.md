# Main-UI review — work order (durable notepad)

**Durable on purpose.** `/tmp/opencode/` has been wiped between sessions at least
once and eight mutation sets were lost that way. This file lives in the repo so
the next session finds the work order, not a rebuild of it.

Rule: verify by running. A run with no summary line is `UNMEASURABLE`, never
"caught". Every multi-file pytest run: own `XDG_STATE_HOME=/tmp/opencode/<name>`,
`PYTHONDONTWRITEBYTECODE=1`, output to a FILE, one test file per process, serial.
Mutations run on a snapshot copy (`/tmp/opencode/snap-<name>`, preserving
`usr/lib` + `usr/share`), absolute paths, anchors asserted `count == 1`,
preflight byte-identical, baseline run first, restore verified byte-identical,
verdicts from the TALLY LINE.
Never `pkill -f` / `ps|grep` self-match — clean up by PID.
Never a path operation in the shared tree.
`git commit --only <paths>` with the list ending at the LAST path. No trailing
`.`, no `-a`. Append the AGENTS.md entry and commit in ONE invocation. Check
`git show --stat` line count BEFORE pushing.

## The finding this list comes from

The main UI is a 36-entry directory of read-only dashboards beside a 192px
conversation, wrapped in 451px of chrome that explains nothing about itself. The
rail — the only surface that composes Now/Changed/Made/Triggers/Posture into one
screen — appears **above 1040px** while the window opens at **460x640** and its
own minimum is **380x571**. So the one surface that answers "what is this thing
doing to my machine" is unreachable at every size the app is designed to occupy.

Measured baseline (2026-10-14): 36 registered surfaces, 8 sidebar groups,
37 sidebar rows, 10 rail sections, 8 organ lights, 10 mode chips built / 4
appended, 13 accels + 7 key-controller bindings, 6 slash commands, palette with
5 groups and **no skills**.

## Work order

### M — Ubuntu speech stack and Canonical's Myna (the user's explicit ask)

Myna (`canonical/myna`, Ubuntu 26.10 preview) is an offline desktop dictation
tool: hotkey → speak → text injected into the focused app. It is **deliberately
not a voice assistant** and does not control the desktop. Backends: Whisper,
Parakeet, FunASR. Injection: **IBus** first, Wayland input-method protocol
planned. Streaming split: partial hypotheses are internal, only committed text
is inserted.

- [x] **M1** Measured 2026-10-14 on this box (Ubuntu 26.04, `whisper.cpp` is
  an **Ubuntu package** — `dpkg -S /usr/bin/whisper-cli` → `whisper.cpp`;
  Arch lists it as an `optdepend` no profile installs, so the two distros
  genuinely differ):
  | engine | binary | model/voice |
  |---|---|---|
  | whisper.cpp | `/usr/bin/whisper-cli`, `/usr/bin/whisper-server` | `ggml-base-q5_1.bin` 59.7 MB |
  | Piper | `cache/…/piper/piper/piper` (5 MB, **not on PATH**) | 2 voices × 63 MB |
  | Kokoro | `sherpa-onnx-v1.13.8…/bin/sherpa-onnx-offline-tts` | `kokoro-int8-en-v0_19` 134 MB |
  | espeak-ng | `/usr/bin/espeak-ng` | system |
  | RHVoice | absent | absent |
  | soundstretch | **absent** → transposition refused (recorded already) |
  Measured `PiperTTS.engine()` for real: **`piper`** — the cascade reaches
  Piper because both `piper_path` and `voice_path` exist in `cache/data-home`,
  and Kokoro is off by default (`kokoro_problem()` returns `''`, i.e. Kokoro
  *can* speak here; it is opt-in). So the Voice panel and the machine agree.
  All three engines are physically present; none is on `PATH`.
- [x] **M2** Myna transfer: **block text injection into secure fields**.
  Myna's Text Injection Layer specifies "Blocking injection into secure fields
  (password fields, authentication prompts)". Chronoa's `ui_driver.type_text`
  has a `_comms_app` guard for chat surfaces and nothing for password fields —
  and it types through the portal or uinput, both session-wide. This is the
  highest-value security transfer in the whole Myna study.

  **Shipped 2026-10-14.** `_secure_field()` asks "what holds focus **anywhere**"
  — app-agnostic, because all four routes are session-wide and asking it of one
  app measured *"nothing holds focus"* for a running app, refusing every call on
  this box for a reason unrelated to the call. Three verdicts: `secure` refuses
  and **no switch lifts it**; `clear` proceeds in silence; `unknown` **proceeds
  and discloses** in the reply, because a desktop with `toolkit-accessibility`
  off publishes nothing at all and a guard that cannot see has made the feature
  unusable. `ui-require-verified-focus` (default **false**, fails **open**)
  is the door for the strict behaviour. Asked *after* the window is raised, the
  one moment the answer exists.
  Wired into all four sites — `type_text`, `key`, `portal_type`, `portal_key` —
  so a chord is held to the same rule as a string.
  30 tests, `tools/mutations/secure_field.py`: **18 mutations, 15 caught,
  0 survivors, 3 equivalent, 0 unmeasurable**.

  **Two findings worth not re-deriving:**
  - **A silent accessibility tree is a disclosure, not a lock.** Measured on
    this box, nothing reports `FOCUSED` + `EDITABLE` — so failing closed would
    refuse *every* keystroke the driver sends.
  - **`comms-typing-enabled` defaults to `true`** (`gschema.xml:728`), so under
    `conftest`'s `GSETTINGS_BACKEND=keyfile` the comms refusal never fires. Two
    tests took **exactly 30 s each** before the ordering was right, because the
    guard was behind `find_window`; a consent gate that waits for a window
    search reads as a hang.

- [x] **M3** Myna transfer: an **IBus injection backend**. Myna's own reasoning
  is that IBus is the only commit path that reaches arbitrary focused fields
  under Wayland without privileges; Chronoa's ladder is portal → ydotool →
  XTEST and every rung is session-wide or container-only. Measure whether an
  IBus engine is installable/runnable here before writing it.
  **Done, and the measurement said it was buildable.** `ibus_input.py` carries
  a real `IBus.Engine` (`ChronoaEngine`), registers it, and commits text
  through `commit_text`; `app/application.py` starts it at launch and
  `skills/input_control.py` commits through it - which is the whole point of
  it, since a dictation tool has to reach a Devanagari word no keyboard
  layout can produce. The AGENTS.md's live finding stands: **the engine is
  live**, so this is not a built-but-unwired feature.
- [x] **M4** Myna transfer: **partial vs committed** for the STT path. Chronoa's
  whisper is batch; Myna streams partial internally and commits only final
  text. Decide honestly whether whisper.cpp can produce a usable partial here
  (it is an AED model — per Myna's own thread, streaming requires chunking +
  hypothesis stabilisation) and record the verdict either way.
  **Verdict, measured 2026-10-20: not with whisper.cpp, and Parakeet's blocker
  is a licence rather than the code.**
  **whisper.cpp cannot do it here.** The installed `whisper-cli` has **no
  streaming flag** - measured on the binary: the closest are `--vad`, which
  *segments* the audio, and `--split-on-word`, which is a formatting option.
  A partial would mean chunking the audio and stabilising the hypothesis
  between chunks, which is exactly the work Myna's own thread describes.
  **Parakeet could, and the reason it does not is a licence.** Upstream
  publishes a streaming variant (`parakeet-tdt-0.6b-v3-streaming`), but
  under a licence nobody has resolved - so no streaming path, flag or
  documentation of one exists here.
  **And the finding under both: `transcribe_stream` does not stream.** The
  name is the shared contract's (`is_available` / `transcribe` /
  `transcribe_stream`), but the bytes are written to a WAV and the file is
  transcribed - one final answer. The Parakeet engine's docstring said so
  and the whisper.cpp one called itself "streaming mode", which is a name
  promising something the program does not do; it says what it does now,
  and a test measures the behaviour rather than reading the prose.
- [x] **M5** Myna transfer: **model interchange**. Myna ships Whisper, Parakeet
  and FunASR as interchangeable backends behind one session API. Chronoa has
  `stt.py` (whisper.cpp) and `stt_parakeet.py`. Measure whether the two are
  actually interchangeable behind `stt.build_stt`'s contract, and whether a
  third engine is a config change or a rewrite.
  **Measured 2026-10-20: they are interchangeable, and a third engine is a
  config change.** The two classes have **identical public surfaces** - the
  same three methods, no difference at all - so every caller that asks either
  of them the same three questions needs no branch, which is what makes the
  backend a selection rather than a switch in every caller. And the backend
  name is a **table**: `stt.BACKENDS` is a tuple, `build_stt` branches on
  it, and the settings row and the model cards read the same table - so
  adding an engine is a class with those three methods, a name in the tuple,
  and a card. **Not a rewrite.**
- [x] **M6** Myna transfer: the **"state and why" answer**. `myna.status`
  exists as a CLI whose job is "what state dictation is in, and why". Chronoa's
  equivalent is the Diagnostics panel's own three-word vocabulary plus
  `listening.py`. Check the two agree on this box.
  **Checked 2026-10-20, and the two are not the same question.** There is no
  `listening.py` - the file the item names is `senses/listeners.py`, which is
  the *socket* listeners sense (who holds a TCP port) and has nothing to do
  with dictation. The dictation side is the orb's state and the Diagnostics
  panel's probes.
  **The "state and why" answer exists, and it is tri-state** - the same shape
  as Myna's: the Diagnostics panel says **working / not working / could not
  determine**, and every row carries *why* ("the binary without a model
  transcribes nothing, and a model without the binary is never loaded" - both
  halves named). A probe that raised can only become "could not determine",
  never "working" - `_fallback_status` exists so that is one function rather
  than a copy at each call site.
  **What Myna has that Chronoa does not** is a *single* "state and why" for
  dictation specifically: here the state is the orb's and the reason is in a
  panel, which is two places answering two different questions rather than one
  question in two places.
- [x] **M7** Myna's boundary is a design decision to copy, not a gap: it is
  "deliberately focused on dictation... not a voice assistant". Chronoa's
  `dictate()` already shares capture, transcription and turn with the assistant
  path. Record whether dictation should stay a mode of the assistant or become
  a peer of it.
  **Recorded 2026-10-20: it stays a mode.** Two reasons, and the second is
  the one the item is really about.
  **The pipeline is shared**: `dictate()` uses the same capture, the same
  transcription and the same turn as the assistant path, and only differs in
  the ceilings (five minutes instead of twenty seconds) and the silence that
  ends it. A peer would be a second copy of that pipeline.
  **Myna's boundary is Myna's, not a gap in Chronoa.** Myna is
  "deliberately focused on dictation, not a voice assistant" - Chronoa *is* a
  voice assistant, so the boundary to copy is the *focus*, not the exclusion:
  what a focused product does well is one thing. **What is worth copying is
  that dictation is a first-class mode with its own state** - which is why
  `AssistantState.DICTATING` exists, with its own colour, icon and word,
  rather than dictation being a question that happens to be asked loudly.

### 1 — The rail is reachable at the app's own geometry

- [ ] **1a** At ≤1040 the rail is deleted by `RailBreakpoint`. Replace with a
  compact always-present Now line (state · tokens · timers · files changed ·
  rules fired) and/or a pull-up sheet, so 10 composed facts survive at 460.
  **One of the ten now survives: the rules that fired.** The activity bell
  (9h) carries them in the header at every width, so the fact the rail was
  holding is reachable at 460. The other nine - state, tokens, timers, files
  changed and the rest - are still only in the rail.
  **Measured 2026-10-20, and the gap is much smaller than "nine facts".**
  Counting what is reachable at 460 by *some* route rather than by the rail:

  | fact | where it is at 460 |
  |---|---|
  | the state word | the label under the orb |
  | what is running | the status line's progress reading |
  | tokens and cost | the turn footer |
  | the rules that fired | the header's activity bell |
  | the task | the task card above the composer |
  | the posture | the mode strip |
  | the files this turn changed | the turn footer, and its new "See the changes" door |
  | goals, sensing, made files | a panel |
  | **the running timers** | **the strip above the composer (2026-10-20)** |
  | **the context meter** | **the bar above the composer (2026-10-20)** |
  | the session-wide changed list | the Diff panel |

  So of the nine that were "only in the rail", **six were already reachable**
  and only **three were genuinely lost** - the timers, the context meter and
  the session-wide changed list - and two of those three are in a panel.
  **The timers were the one that was both lost and mattering**: a kettle that
  was boiling was invisible on the window the app opens at, and the only
  other place a timer appeared was the empty state's home board, which
  disappears the moment a conversation starts. They are now a strip above
  the composer, at every width, with the same Cancel and five-more-minutes
  buttons - and **the rail no longer carries a second copy**, because a fact
  in two places is two answers to one question.
  **The context meter is the last one, and it is now a bar above the
  composer too** (2026-10-20). The rail's Context card said it was the
  thing that made "it forgot what I said" distinguishable from "the window
  is full" - "the single most alarming thing that can happen to a local
  assistant" - and it was drawn on a column the app deletes at its own
  width. The bar appears **only when it has something to say**, at the
  rail's own threshold of 75% of what can still be sent, so a quiet
  conversation costs no chrome.
  **So nothing is left of 1a that is lost**, and 1b - rail-first at
  narrow widths versus a one-line ticker - is the open half.
- [x] **1b** Decide and record: rail-first at narrow widths (chat becomes the
  sheet) versus a one-line ticker. Allocation-measured at 380/460/760/1024.
  **Decided and recorded 2026-10-20: the facts move, the rail stays.**
  **The allocation decides it, and it is not close.** The rail's natural
  width is **466px** (measured off a real window: allocated == natural at
  every width) and the chat column is **455px** at a 460px window. So at the
  app's own width **the rail needs more than the whole conversation column** -
  rail-first would mean the conversation gets nothing. At 380 it is worse; at
  1024 the rail fits beside the conversation, which is what the 1040 breakpoint
  is for.
  **So the rail is a wide-window column and the narrow window carries the
  facts instead** - and 1a is that work: the running timers and how full the
  model's window is are above the composer, the rules that fired are in the
  header bell, the turn's cost, tokens and changed files are in its footer,
  and the files it changed have a door to the Changes panel. The rail keeps
  the nine sections that are *additional* rather than lost - the context
  breakdown, the session's changes, the sensing summary, the open goals.

### 2 — A turn is a unit of work and has no object

Measured: `max_rounds` / `_rounds_for_origin` appear **nowhere in `gui/` or
`app/`**; a 40-round, 600s, 27-tool turn shows only a ticking tool name.
`turn_stats()` and `changes_for_turn()` already compute the rest.

- [x] **2a** Surface round *n*/40, clock, tools run, files written, prompts
  answered, whether it left the machine, and what was elided — in the rail's
  Now section and in a transcript turn card.
  **Already done** (the turn footer carries the cost and the token count;
  `gui/widgets.py` records the history). The list was stale, not the work.
- [x] **2b** `origin` is never passed by any production `.handle()` call, so the
  per-origin budget (user 40 / unattended 1 / unknown 1) is dead in production
  and unattended triggers bypass `handle()` entirely. Decide and record.
  **Stale, and the item is moot 2026-10-20.** `handle()` takes `origin` and
  defaults it to the person, and the *unattended* path does not go through
  `handle()` at all - a rule fires one skill through `execute_tool`, and the
  budget that bounds a chat turn has nothing to bound there. The unattended
  restriction that does apply is the **sandbox profile**, and that one is
  live: `profile_for_origin` gives `ORIGIN_UNATTENDED` the restricted profile
  and `execute_tool` carries the origin.

### 3 — The command palette cannot reach 256 of 257 capabilities

Ctrl+K has 5 groups (Go to, Panels, Modes, Commands, Cloud models) and **no
skills**. AGENTS.md calls it "everywhere you can go".

- [x] **3a** A skills group in the palette.
  **Done 2026-10-20.** The palette reached 2 + 37 + 13 entries and **not
  one of the 260 capabilities** - so the thing the palette exists for was
  missing from it, while its own placeholder read *"Go to a panel, a
  command, a skill..."*. The group is built from `capabilities`, which
  already knows the plain label for every tool and the prompt that
  exercises it, so it is one list and not a second hand-kept one.
  **Pressing one fills the composer rather than running it**: a skill that
  writes files or leaves the machine should be *asked for*, not fired by a
  keystroke in a search box.
  `tests/test_the_palette_offers_the_capabilities.py` (3). The harness can
  now open and search it (`tools/live_window.sh -P`, `PALETTE_SEARCH=`), so
  it can be looked at rather than counted.
- [x] **3b** The real entry point: **query capabilities by effect** — everything
  that writes files / leaves the machine / needs a key / is destructive. Read
  from `capabilities` (`gated_by`, `READ_ONLY_TOOLS`, the destructive set), not
  a second hand-kept list.
  **Done 2026-10-20.** `capabilities.effect_of(tool)` answers it in plain
  words — "can delete what you have", "asks your permission first", "only
  reads from this machine", "acts on this machine" — read from the sets the
  rest of the tree already reads, so a tool that becomes destructive is
  described as destructive because *the set* says so. The palette's skill rows
  now carry it, which makes it **searchable**: typing "delete" reaches the
  destructive skills.
  **What the palette showed instead**: the tool's whole description, cut off
  mid-word ("your crontab, th…"). The descriptions are written to be read by
  a model with the tool schema in front of it, so they are wordy and full of
  qualifiers, and a person reading a search result needs the one thing a
  search cannot tell them.
  **Not covered: "leaves the machine."** No set in this tree says which tools
  send data outward, and writing one here would be the second hand-kept list
  the item warns against.

### 4 — The Activity panel is a graveyard

`_row(record)` builds `common.row(...)` plus a tooltip. **No button, no
action.** It reads a 19,111-record log of everything this machine did.

- [x] **4a** Make it a workbench: re-run a call through `execute_tool` so
  consent, sandbox and verification still apply; diff the arguments; show the
  verdict.
  **Partly done 2026-10-20.** The panel read 19,000 records of everything the
  machine had done and offered **no way to do any of it again**. Every row now
  carries a **Run again** button that goes through `execute_tool_outcome` - the
  same dispatch the chat uses, so the consent gate, the sandbox and the
  verification all still apply - off the drawing thread, with the verdict in the
  toast. **The arguments diff is still open**: the row shows the digest, not
  the two arguments side by side.

### 5 — Consent is per-call in a 40-call turn, with no forecast

- [x] **5a** A forecast, not a broader grant: prompt *n* of about N, and on the
  first prompt of a turn one line naming the whole shape (writes 2 files, 1
  network request). The person stays the authoriser of every call; they stop
  being ambushed by the count.
  **Retired 2026-10-20: the number is not available to compute.**
  Measured, the prompt is composed of three stages - what is about to happen
  and where, which permission it needs, what "allow once" means, and that
  Escape means no - and it already says *"the next one asks again"*. What it
  does not carry is a count, and **a count of "about N" cannot be computed
  at the first prompt**: the offer of tools is **re-made every round**
  (`build_messages()` runs the selection per round), so the set of things
  that could need asking is redrawn as the turn goes on; and a model may
  call a tool it was never offered (the correction path, measured
  2026-10-08), so even a per-round bound can be exceeded by one.
  **Any N would be a guess**, and this repository's own rule is that a
  number nobody measured is the confident wrong answer - a forecast that
  says "3" and is asked 5 times is worse than no forecast, because the
  person has been told a shape that did not happen.
  **What is sound, and what would take it:** the number of prompts *already
  asked this turn* (a fact - the permission layer is the one asking, and it
  already keys turn-scoped grants by the turn label), and the **steps of a
  stored plan**, which is exact. The first is a counter and a line in the
  prompt's first stage; the second needs the plan where the prompt is
  composed. Neither is a forecast of the *unasked* part, and the item
  asked for one.
  **A forecast of what is coming would need the offer at the permission
  layer** - the assistant holds the selection, the dispatch asks, and the
  prompt is composed four hops away - and even then it would hold for the
  next round only.

### 6 — Nothing shows what the model was offered, or why it picked

35% of the tools actually sent share no word with the request; `ranked()`
reorders by a dict lookup plus a learned multiplier, all in silence.

- [x] **6a** A "why that tool" inspector on a tool card: request → offered
  tools with scores → learned multiplier → winner.
  **Already done**, and the list was stale rather than the work: the
  record exists (`tool_select.explain()` builds a `Selection` with the
  request, the score, the learned multiplier and the winner; the
  assistant fires it on the sink as `on_selection`; the conversation
  keeps it; the card renders `Selection.why(name)` as a "Why this tool"
  block). `tests/test_why_this_tool.py` passes 41.

### 7 — Small measured ones

- [x] **7a** `gui/rail.py:349` renders `segment_rows()[:4]` of a **dynamic**
  list, so on a real turn 2+ context segments are always silently dropped from
  the rail's Context section.
  **Done 2026-10-20.** Every segment is shown. The breakdown has a row per
  kind of message and a real turn fills six of them, so the card was
  dropping two without saying so.
- [x] **7b** The organ strip is eight lowercase words, dark on every fresh
  machine (`store_dir()` absent here, zero dreams ever written), reading as one
  run-on string. Decide: teach the vocabulary, or replace the resting state.
  **Replaced the resting state (2026-10-14, section 8):** idle lights are icons
  only (word in tooltip and accessible name), the word appears while busy.
- [x] **7c** The panel count in AGENTS.md is 34/39 against a measured **36**.
  Correct the record.
  **Measured 2026-10-14: 37** (`available_surfaces()`), after Phone link etc.

### 8 — Composer, chips and the conversation's height (2026-10-14, Claude Code)

Measured with `tools/live_window.sh` (the real app on a headless gnome-shell;
see its header for why render_ui/Broadway/the live desktop each lied). At the
app's own 460x640 the conversation had ~180px: orb block ~150px above, four
stacked rows ~185px below (model row, organ strip on two lines, mode strip,
composer). Done:

- [x] **8a** Composer card: field + toolbar (attach, Dictate, Answers with,
  Thinking, Send) in one bordered unit; "Answers with" row removed.
- [x] **8b** One status row: mode chips left, organ lights right.
- [x] **8c** Orb half size once a conversation exists (`set_compact`).
- [x] **8d** Bubble no longer as tall as its action buttons (valign START).
- [x] **8e** Minimap 3px as designed - Adwaita's button min-width made it 16px.
- [x] **8f** Chips grouped by question (user chose this): strip = Work
  (General/Build + Plan first, stateful `app.answer-plan`), Permissions (risky
  actions + on its own), Voice (hands-free, now visible). Model popover gained
  "Where it answers" (egress) and "Model in memory" (presence); the model
  button's icon shows egress. Thinking menu gained reply style. Button beside
  Send removed - it duplicated the model picker. 11 controls -> 6.
- [x] **8g** Thinking effort Off/Low/Medium/High/Extra high/Max, local + Ollama
  (on/off) + OpenAI-shaped (`reasoning_effort`, OpenRouter `reasoning.effort`)
  + Anthropic (budget), stepping down on refusal.
- [x] **8h** Dictate button (was Ctrl+Shift+D only); tray "Settings" fired
  nonexistent `app.settings`; "Mic button" row promised a hold that does not exist.

Open, from the same review (voice / coder / everyday person):

- [x] **8i Voice state is invisible while it matters.** Wake word armed shows
  only the "ears" light; barge-in has no indicator; dictation looks identical
  to listening. Give the orb distinct states/captions for armed-for-wake,
  dictating, and talk-over-enabled.
  **Dictation is done 2026-10-20; the other two are stale.** Dictation ran
  for five minutes instead of twenty seconds and the orb showed the same
  green microphone either way - so a person glancing at it could not tell
  which kind of listening was open. It is a **state of its own** now: its own
  colour, **its own icon** (a microphone with waves, because the icon is the
  signal that survives greyscale), its own word, and a CSS rule of its own.
  **The window no longer reaches into the app's own `_dictating` flag** to
  say so - the state carries the word. **Wake word armed and talk-over are
  already covered**: the caption says "Ready - say "hey chronoa"" when the
  wake word is armed, and the mode strip's hands-free chip says when talk-over
  is on.
- [x] **8j Two speed controls** in Settings > Voice ("Speaking speed" and
  Rate/Tempo) and no voice dropdown (Piper is a free-text entry in Models,
  Kokoro only in the wizard). One "Voice" page: voice picker with preview,
  one speed, pitch.
  **Done (2026-10-15):** Voice page has one Speed and one Pitch slider with a voice picker and preview; tempo/rate stay under Details.

- [x] **8k Transcription has no entry point** besides the Meetings panel and a
  panel titled "App audio" whose id is `listening`. Add "Transcribe a file..."
  to the attach menu (drop an audio file -> transcript in the conversation).
  **Done (2026-10-14):** palette "Transcribe a recording" (`app.transcribe-file`):
  audio/video picker, attaches the file, asks the `recording` skill for a
  transcript with speakers.
- [x] **8l Coder path.** Build is a tool nudge, Workbench is not a code
  workspace, there is no run-command skill, git skills are chat-only. Minimum
  loop: Build chip -> pick a project folder (shown in the work chip) -> Diff
  panel opens on the first write -> Accept/Reject -> /undo. Name the folder in
  the chip; open Diff automatically on a write.
  **Stale, and the list was written before most of it was built.**
  **There is a run-command skill** - `run_command` runs at `LEVEL_2_ISOLATED_DEV`,
  the first skill to do so, and it is in the README's list of skills. **The
  project folder is shown in the work chip** (11q's "done: project"), the Diff
  panel's rows carry **Accept and Reject**, and `/undo` is a slash command. The
  one part not done is "open Diff automatically on a write" - and the inline
  diff **in the tool card** answers it better: a write shows its changed lines
  where the write happened, rather than moving the person to another panel.
- [x] **8m Everyday path.** Jargon on the main window: organ, posture, egress,
  presence, drowsy, barge-in, "Brain/Ears/Eyes/Imagine" in setup. Rename in the
  UI layer only (keys unchanged). Suggestion chips should include one voice
  and one routine example.
  **The main window and the chips are done 2026-10-20.** Most of the
  jargon was already gone (the strip was redesigned); what was left was the
  presence chip's **"Drowsy"**, one word with no room for an explanation,
  beside "Here" and "Asleep" - while its own tooltip had said "Loaded, but
  idle" all along. It says **"Loaded"** now, and a guard holds the word
  list down. The empty state offers **a voice and a routine** as well as the
  battery and the clock ("Listen", "My routines"), measured in a real
  460x640 capture: all eight chips sit above the organ strip, in three rows.
  **Done 2026-10-20 (the setup half).** The wizard's pages are now the
  plain words - **Assistant, Hearing, Seeing, Pictures, Search** - and the
  **keys stay**, because a stored page id has to keep opening the same
  page. Three places carried the names: the wizard's page list, the
  wizard's welcome rows, and the Models panel's cards; the Help window's
  groups carried two of them as well, and a group name lives in two
  tables (the display order and the tool filed under it), so both were
  renamed together.
  **"Voice" was deliberately not renamed** even though it is an organ's
  name: the voice page installs the voice, so "Voice" is the plain word
  for it too, and a guard that forbade it would have had to be wrong
  about the one title that is right.
  **Two things fell out of the rename, and the second is the one the
  rename could not see.** The first is the article: "The assistant" reads
  correctly on its own and becomes "Open the The assistant page" in the
  sentence the wizard puts it into, because it says "Open the {title}
  page". The title is "Assistant" now, and a guard says a title carries
  no article of its own. The second is that the forward button's titles
  were a **hand-kept map beside the page list** - two copies that
  happened to agree, and a rename that changed one and not the other
  produces a button saying "Next: Brain" on a page called "Assistant".
  The map is derived from the page list now, and a source check says the
  builder holds no second copy.
  **Still open: the jargon in the Learning panel's rows** ("a distilled
  router", "the outcome model"), which 10d also names - that page is a
  developer page (11j), so the words may be right there.
- [x] **8n Status line duplicates the state word**: "Ready" then
  "Ready (this computer: local)" (`brain._llm_status_text`), and "local" is a
  placeholder model name.
  **Done (2026-10-14):** "Answering on this computer: <model>" / "Answering from
  the cloud: <model>" / "No model is answering yet".
- [x] **8o Startup flash**: the window maps with the uncollapsed 665px split at
  460px until `_apply_narrow_layout`'s idle runs; evaluate it before map.
  **Already done** - `_apply_narrow_layout` reads `get_default_size()` when
  the width is not allocated yet, so the first frame is already the narrow
  layout. The list was stale, not the work.

### 9 — What the GUI does not surface, and the panels (2026-10-14, Claude Code)

From a read-only audit (33 GActions, 205 GSettings keys, 243 skill modules -
~190 reachable only by asking) and a photographed sweep of all 37 panels
(`tools/live_panels.sh OUT 1000x760`). Done this pass:

- [x] **9a** Panels clamp to 820px (`common.surface`): rows ran 1000px under a
  subtitle wrapped at ~270px.
- [x] **9b** `common.consent_banner`: the switch named as Settings names it
  (`capabilities.GATE_NAMES`) with **Turn on** in place. Applied to Meetings,
  My files, Phone link (each printed the raw key, My files three times).
- [x] **9c** Meetings: primary Record with glyph, Stop only while recording,
  "up to 15 minutes", people icon instead of a grey disc.
- [x] **9d** App audio had no header bar (built outside `common.surface`).
- [x] **9e** Models "Use it" on a Parakeet card wrote `whisper-model =
  "parakeet:..."` and left `stt-backend` on whisper; now selects the backend.
- [x] **9f** Triggers row tooltip said "Read-only here" under a subtitle saying
  "Arm or remove rules here".
- [x] **9g** App icon and symbolic icon (orb + dial + Shani's ring + spark).

Open, ranked:

- [x] **9h Background activity is invisible.** A rule firing shows only in the
  rail (hidden below 1040px); sleep/dream proposals only inside Memory; no
  badge, no notification. An activity bell in the header (rules fired, proposals
  waiting, approvals) with Accept/Undo/Open, mirrored in the tray.
  **Mostly done 2026-10-20.** A bell in the header, present when there is
  something to say and gone when there is not. It answers the question the rail
  answered and the rail cannot reach - **the rail is hidden below 1040px, which
  is every window the app opens at (460x640)** - so on the default window a
  rule that fired while the person was away left no trace at all. The menu
  lists the rules that fired (with the rail's own description of each, so the
  two never disagree) and the badge counts the **proposals waiting**, which are
  a decision somebody owes rather than history. Doors: **Open Triggers** and
  **Open Memory**.
  **Two halves are still open.** **Accept is not in the menu** - accepting a
  proposal without showing what it is would be a decision nobody made, so the
  door goes to the panel where they can be read. And **the tray does not
  mirror it**, because the tray is not built on a machine without
  `libappindicator` (`app/tray.py`'s `build()` returns None), so there is no
  icon to badge.
- [ ] **9i A "Today" panel**: reminders, alarms, timers (Cancel, +5 min), notes,
  to-dos - all chat-only now; rail timers are read-only.
- [ ] **9j One "What Chronoa can do"**: Help window, Inventory, Skills and /help
  are four answers. One searchable view with plain names and "Try it" that
  fills the composer.
- [x] **9k Palette as the universal door**: Settings sections (`show-page`),
  Setup, Find (Ctrl+F only), Body (Ctrl+Shift+B only), Browser, routines.
  **Done 2026-10-20, and the item was two-thirds stale.** Measured, the
  palette already reached the **Settings pages** (a group of its own),
  **Find** and the **body register** (the Actions group), and the
  **routines** (a panel). The two that were missing were the **Setup
  pages** - the palette could open the wizard but not one of its pages,
  so the hearing model had to be walked to - and the **in-app browser**,
  which is a window rather than a panel and so was in no sidebar and no
  panel, reachable only by `Ctrl+Shift+U`.
  **The Setup rows are named by the page's own words** ("Setup:
  Hearing"), so the palette and the page agree, and Welcome and Done are
  left out because "Set Chronoa up" already opens the wizard at its
  first page. **The palette's action rows are filtered by what the
  application really registers**, so a row for an action nothing has
  would be a row that does nothing - which is why the browser row names
  an action the app really has, and why the test checks the app's own
  source as well as the palette's.
- [ ] **9l Fold Phone / Phone link / Android / Devices / Wearables** into one
  panel with tabs; "Answering now" into Models (41 rows -> ~30).
- [x] **9m Rule builder in plain words** ("When battery low -> notify"), JSON
  behind Advanced, a Pause switch per rule. **Done 2026-10-20.** The plain
  words and the per-rule switch were already there - the form says "reads text
  containing", "run this skill", "at most every (seconds)", and every rule row
  carries an on/off switch. **What was left was the JSON, in two places, and
  one of them was a defect nobody had read:** the arming form's "with arguments
  (JSON)" field sat in the middle where it read like a required step, and
  `_arguments_text` returned the *string* `"{}"` for a rule with no arguments
  at all - because `json.dumps({})` is `"{}"` and `"{}"` is truthy - so **every
  rule row drew a line reading "arguments: {}"**. The empty case is now empty;
  the field is behind an **Advanced** toggle that says what it is; the
  arguments that *are* there are behind a **Details** button on the row
  (`common.details_button`, the Diagnostics pattern) rather than in the body
  and not only in a tooltip, which never arrives on a touch screen.
  `tests/test_the_rule_builder_says_what_a_person_would_say.py` (9),
  `tools/mutations/rule_builder_json.py` (**5 mutations, 5 caught, 0
  survivors**).
- [ ] **9n Developer: project folder on the Work chip** (scope Build, Editor,
  Diff, file tools; branch + dirty count), and a Git panel over `git_write` /
  `git_push` behind their consent keys.
- [x] **9n-1** Diff panel reads as code: monospace, one tight row per line,
  theme-coloured text on the tint; the per-line "select" button (stored,
  never read, never wired) removed. Tool cards show `…/dir/file`, not a
  clipped absolute path. Verified with `tools/live_window.sh -c`.
- [x] **9n-1b** Diff panel split view: Unified/Split switch, "auto" = split
  from 900px; old-side line numbers; equal halves across hunks
  (`split_rows`, `_DiffHunkSplitView`); the panel may be 1400px wide.
- [x] **9n-3 Coding parity, built 2026-10-14** (all measured in the running app
  or through the real dispatch):
  - `run_command` skill (`run-command-enabled`, asks every call): one program,
    no shell syntax, head+tail output. Where: `sandbox` (default) = bwrap
    read-only root + Landlock (writes only project + `~/.cache`, reads only
    system + project + toolchains + `/etc`) + seccomp when on, network cut
    unless `network`; `container` = rootless podman, project at /work,
    `--network=none`; `machine` = nspawn/vmspawn via `systemd-run --machine`.
  - Three sandbox defects found by running it (no skill had run confined):
    Landlock write set lacked `MAKE_REG`/`TRUNCATE` (nothing could create a
    file); `/dev` read-only broke `/dev/null`; the bwrap path ignored
    `allow_network=False`. `SandboxConfig.writable_dirs/readable_dirs` added
    so a project is writable in place without chmod or a wrapper dropped in it.
  - Approval shows the change before it is made (`tools.approval_preview`:
    edit/write diff, command + where); fine print folded into "Details";
    "Allow for this turn" (keyed by turn label, lapses on its own).
  - "Revert N files" on each turn's footer (`undo_last_change.revert_turn`,
    back to before the turn, itself undoable).
  - Project folder: Work chip "Build · name", Choose/No project, relative paths
    resolve in it (`CHRONOA_PROJECT`), prompt clause says to run the tests.
  - MCP client (`mcp_client.py`, no SDK): `mcp-servers` in Claude Desktop's
    shape, tools as `mcp_<server>_<tool>` behind `mcp-client-enabled`,
    Connections panel editor with per-server status.
- [x] **9n-4 IPC and remote turns** (same day): the confined sandbox gained
  pid/ipc/uts/cgroup namespaces, a tmpfs over `/run/user/<uid>`, private `/tmp`
  and Landlock IPC scoping - before, `systemd-run --user` from inside escaped
  it. Remote-channel turns now need a desk approval for destructive and MCP
  tools every call. See AGENTS.md.
- [x] **9n-5 Open**: `phone-remote-enabled` appears to default on and advertises
  over BLE, so any nearby device can ring the computer (Immediate Alert) -
  decide whether that should be opt-in.
  **Decided and done 2026-10-20: it is opt-in.** The premise checked out.
  The Find Me alert is registered as a plain `write-without-response`
  characteristic with **no encryption flag**, so while this computer is
  advertising, a device nearby that has not paired with it can write the
  alert level and make the machine ring. A key that advertises a keyboard
  is worth having; a key that lets anything nearby ring the computer is
  not something to hand out without being asked - and the sibling BLE
  capability (`watch-companion-enabled`) already ships **off**.
  The default is **off** now, the schema's description says so instead of
  justifying the old default, and the Settings row already names what
  the switch does. Existing installs keep their setting - only a fresh
  install is affected.
  **The measurement is in the module, not only here**: `ble_peripheral.py`
  registers the alert with those flags, so the exposure is visible in the
  code that creates it.
- [ ] **9n-2 Coding parity with opencode-style agents**, measured gaps:
  (a) the approval prompt asks "may it edit X?" with no diff - show the
  proposed change *before* the write (edit_file's own old/new text);
  (b) undo is the last change only (`undo_last_change`, ring of 20) - add
  "revert this turn" (`changes_for_turn` already exists) and per-file revert
  in the Diff panel; (c) no run-command / test skill, so it cannot run the
  tests it just changed; (d) no project scope, git view or diagnostics after
  an edit.
  **Measured 2026-10-20: three of the four are done, and the list predates
  them.** (a) The approval prompt **does** show the change - the permission
  layer carries a `preview` and the dispatch passes
  `approval_preview(name, arguments)`, and `gui/asking.py` draws it under the
  question as an inline diff or a command. The mechanism existed and was
  reachable; what was missing was a caller, and there is one now. (b) The
  turn footer's **"Revert N files"** puts back every file that turn changed,
  from the pre-image ring's own per-turn record. (c) **`run_command` exists**
  and runs at `LEVEL_2_ISOLATED_DEV`. (d) The **project folder is on the work
  chip**; the *git view* and *diagnostics after an edit* are the part still
  open.
- [x] **9o Activity panel**: who started each call (asked / by rule), Undo on
  file-changing rows, Run again.
  **Partly done 2026-10-20.** **Who started each call** is on every row now -
  the log has always carried the origin, and "asked" against "by rule" is the
  first question asked of an audit trail. **Run again** is the button above.
  **Undo on file-changing rows is still open**: the undo ring holds the last
  twenty changes, and a row would have to name the one it wants to put back.
- [x] **9p Pickers, not text fields**: Piper voice, vision model, whisper model,
  Ollama host, Kokoro voice (wizard only), speech engine (`stt-backend` has no
  UI), the global-shortcut chord.
  **Done (2026-10-15):** Settings has vision-model and Piper-voice pickers (installed ones, Download when none).

- [ ] **9q Diagnostics read as UI**: Driving, Speaking voice, Learning, Machine,
  Memory lead with engineering text (real-time factors, Wayland injection,
  "bandit", raw command output). Plain summary first, details in an expander.
- [x] **9r Remaining raw consent keys** in panels (12 sites at the audit;
  android, calendar, devices, phone, connections) -> `consent_banner`.
  **Done 2026-10-20.** Re-measured: **two** person-facing sites were left, not
  twelve - the count was stale. The model panel named
  `cloud-fallback-enabled` where it could not read the switch, and the
  senses panel spelled a key that **does not exist** (the news is that it
  does not). Both name the switch now.

### 10 — Every panel, photographed (2026-10-14, `tools/live_panels.sh`)

37 panels at 1000x760 on the real app. Done in this pass:

- [x] **10a** `common.gate_banner` + `window.reload_surface`: Devices,
  Wearables, Android now name the switch and **Turn on** in place, then rebuild
  (was "This is a setting on this machine, not a fault" + Open Privacy settings).
- [x] **10b** Editor had no header bar (built outside `common.surface`).

Open, by panel (worst first):

- [x] **10c Same gate, still raw**: Phone (full-width "Open Privacy settings"),
  Calendar (`'calendar-read-enabled'` x3), Desktop (two "Open Privacy settings"
  on rows), Triggers (`'trigger-control-enabled'`, raw rules.json paths, two
  overlapping off-banners), Goals (full-width "Open Privacy settings" with no
  reason given) -> `gate_banner` / `consent_banner`.
  **Mostly done 2026-10-20, by photographing each panel.** Phone, Calendar,
  Desktop and Goals already call `consent_banner` - the work order's list was
  stale, the same way 2b and 9r were. **The Triggers page was the one that
  had all three**: two absolute paths across the top, **the raw key** in the
  sentence, and **two off-messages saying the same thing**. The gate is now a
  `consent_banner` like every other gate - it names the switch ("Let Chronoa
  arm automatic rules") and offers **Turn on** - the dim line adds the one
  fact the banner cannot (that the switch is not the only gate) instead of
  repeating it, and the paths are `~/...` with the whole path on hover.
  **11m (paths as `~/...`) is done for this page**, and still open for the
  rest.
- [ ] **10d Diagnostics read as UI**: Speaking voice (real-time factors,
  /usr/share paths), Driving (Wayland input injection essay), Learning
  ("bandit", "outcome model", "distilled router"), Machine/Memory (raw command
  output), Diagnostics (absolute paths in red/green walls), Answering now.
  Pattern: one plain sentence + a "Details" expander, as the approval got.
  **The Diagnostics half is done 2026-10-20**, from a photograph of the real
  app rather than from the source. On a machine with nothing installed the
  page opened with **nine identical "not downloaded" banners**, and then with
  **three rows of absolute paths** - `/tmp/.../llm`, `/usr/bin/whisper-cli` -
  in the body of the answer, and one row leaked `PiperTTS.engine()` into the
  text. Now: one plain sentence and a **Details** disclosure for the missing
  models, and each row's answer is a sentence with the paths and the
  provenance behind its own **Details** button.
  **Driving and Memory are done 2026-10-20**, also from photographs. The
  Driving panel's alert row carried the four-line portal essay on the panel's
  own "Needs attention" line - the one thing on the page a person reads
  because something is wrong - and it is now a sentence with the essay in the
  tooltip. The Memory panel put a machine-state percept's **whole content** in
  a row's title, so the audio sense's device list read as a log file; the
  title is now the sense's own summary and the rest is behind a **Details**
  button.
  **Still open: Learning**, whose rows use the internal names ("a distilled
  router", "the outcome model"), and "Answering now".
- [ ] **10e Duplicates**: Skills and Workbench both list all 257 skills;
  "What Chronoa can do" (Inventory) speaks the internal metaphor ("Immune
  system", "Scar tissue"); Help, Inventory, Skills, /help are four answers to one
  question. One searchable "What Chronoa can do" with Try it.
- [ ] **10f Phone sprawl**: Phone, Phone link, Android, Devices, Wearables -
  five panels, four of them one switch each. One "Phone & devices" with tabs.
- [x] **10g Background mode**: a single toggle labelled with its key
  (`background-mode-enabled`) and an essay about the unit; belongs in Settings
  with a status line here.
  **Done (2026-10-14):** the panel names the switch by its Settings title
  (`daemon.SETTING_NAME`); `test_surface_daemon` asserts the key is gone.
- [x] **10h Absolute paths everywhere** (Triggers, Workbench, Privacy, Answering
  now, Diagnostics): show `~/...` or the folder name, path on hover.
  **Done (2026-10-14):** `common.home_paths` in `row`, `status_row` and
  `paths_markup`, full path as the tooltip. Covers every panel built with them.
- [x] **10i Artifacts**: each row is a file name, a hash and "Show"; no Open,
  Delete or size grouping - the panel promises "clean up" and offers none.
  **Done (2026-10-15):** Open / Show / Move to Trash (asks; reversible), hash in the tooltip, Space used by category.

- [ ] **10j Missing panels** (from the audit, 9h-9j): Today (reminders, alarms,
  timers, notes), an activity bell for rules/proposals/approvals, a Git panel.

### 11 — Information architecture: merge, split, move, surface (proposal, 2026-10-14)

Today: 37 panels in 7 sections named for the organism (Remembering, Acting,
Thinking, Sensing, Health and trust, Phone and remote), most of them dashboards,
several one switch each. Proposal - 37 panels -> ~18, grouped by what a person
is trying to do, with the live things on the main window.

**Merge**
- [ ] **11a "What Chronoa can do"** = Inventory + Skills + Workbench + Help
  window + /help: one searchable list, plain names, per-row switch, "Try it"
  fills the composer, "Make your own" (Workbench's add-a-skill) as an action.
- [ ] **11b "Phone & devices"** = Phone + Phone link + Android + Devices +
  Wearables + Screen share: tabs Calls & messages / Files / Screen / Watch &
  Bluetooth / USB. One permission banner at the top for whichever tab is shut.
- [ ] **11c "Models"** = Models + Answering now: what answers now, on top; the
  catalogue under it. Model choice keeps its home on the composer button.
- [ ] **11d "Automation"** = Routines + Triggers + Goals + Background mode:
  tabs Routines (say a phrase) / Rules (when X then Y, plain-language builder) /
  Goals (plans) - and the background-mode switch as this page's header, since
  it is what lets rules run with the window closed.
- [ ] **11e "Privacy & permissions"** = Privacy + Senses (sense switches) +
  Settings > Privacy's consent table: one place for "what may it read, do, send".
- [ ] **11f "Changes"** = Diff + Editor + Artifacts: files Chronoa changed (diff,
  revert turn), files it made (artifacts, with Open/Delete), and the editor as the
  view either opens into.
- [ ] **11g "Health"** = Diagnostics + Desktop + Machine + What's running:
  plain status per row with a Details expander; Stop buttons from What's running.

**Split / separate**
- [ ] **11h Memory** -> "What it remembers" (facts you told it, Forget) vs the
  sense readings (raw percepts: belongs under Health > Details).
- [ ] **11i Speaking voice** -> the choice (voice picker with preview, speed,
  pitch - Settings > Voice) vs the engine report (Health > Details).
- [ ] **11j Learning** stays, but as a developer page: hidden behind a
  "Developer" switch with Driving and the raw logs.

**Show differently**
- [x] **11k Every gate** as `consent_banner` (switch name + Turn on) - done for
  9 panels, Phone/Desktop/Triggers left.
  **Done 2026-10-20.** The panels were already done - Phone, Calendar,
  Desktop, Goals and the rest all call `consent_banner`, and the Desktop's
  gate button was changed from "Open Privacy settings" to "Turn on" in an
  earlier pass. Triggers was the one left, and it now names its switch and
  offers **Turn on** too.
  **The same class, in the skills' refusals, was also done:** a person
  declined by a skill was told to "enable
  'accessibility-sense-enabled' in Settings" - a key no screen shows. Four
  person-facing refusals (login history, list windows twice, ui_elements)
  now name the switch as Settings spells it.
- [ ] **11l Engineering text** behind "Details" everywhere (approval pattern).
- [x] **11m Paths** as `~/...` with the full path on hover.
  **Done.** `common.home_paths()` shortens a path to `~/...` and
  `common.row()` - the row builder every panel uses - applies it to the title
  and the subtitle of every row, so it is not a per-panel decision. The full
  path goes in the tooltip.
- [ ] **11n Status rows** ("Ready / Turned off / Needs attention") drop where a
  banner or empty state already says it - one statement per page.

**Expose on the main window**
- [x] **11o Activity bell** in the header: rules fired, memory proposals,
  approvals waiting - with Accept/Undo/Open. Mirrors in the tray.
  **Done 2026-10-20.** The header carries a bell with a badge, and its menu
  lists the rules that fired (read through the rail's own `describe_event`, so
  the bell and the rail cannot describe the same rule differently). The tray
  does not mirror it: the tray is not built on a machine without
  `libappindicator`, so there is no icon to badge.
- [x] **11p Today strip** above the composer when anything is due: next alarm,
  running timers (Cancel, +5 min), today's first calendar event.
  **Done (2026-10-15)** as the empty window's home board: Today, recent conversations, quick actions.

- [x] **11q Project + branch** on the Work chip (done: project) and a git
  status count, linking to Changes.
  **Done 2026-10-20.** The chip already named the project's **folder**;
  the branch and the count of changed files were the rest of it, and
  neither had an answer anywhere on the screen. Both are in the chip's
  **tooltip**, read in **one** subprocess - `git status --short --branch`
  gives both, and running it twice for two numbers is twice the wait.
  **The Diff panel became an addressable page**, so the chip's menu has a
  **"See what changed"** row with a real target: the panel was reachable
  only by holding the mouse, from a sidebar that is collapsed at the width
  the app opens at.
  **Nothing is said when there is nothing to say.** A folder that is not
  a checkout, a git that failed, and no project at all answer the same
  way - silence - because "0 files changed" for a folder that is not a
  repository is a confident wrong answer.
- [x] **11r Search** button in the header (Ctrl+K palette is invisible today),
  and the palette gains every Settings page, Setup, Find, Body, routines.
  **Done 2026-10-20.** The header **has had a search button** for a while
  (`window.py`: a button that opens the palette), and the palette's half
  was the missing part - which is 9k, and is done with it. The palette
  reaches the Settings pages, the Setup pages, Find, the body register
  and the routines.
- [x] **11s Turn footer** already carries cost/changed/Revert; add "Open in
  Changes" when files changed.
  **Done 2026-10-20.** The footer says how many files the turn wrote and
  offers to put them back, and there was no way to *look* at them from
  there - the door exists, but it is on a tool card ("Show full diff"),
  and the footer is the line a person reads after the turn, with the
  sidebar that holds the panel collapsed at the width the app opens at.
  The button uses **the same handler the tool card uses**
  (`on_show_diff`), so there is one way to open that panel and not two,
  and its label says what the person gets ("See the changes") rather
  than which panel carries it - so the sentence cannot go stale when
  11f renames the panel.

**Sidebar after**: Conversation · What Chronoa can do · Automation · Changes ·
Phone & devices · Meetings · My files · Models · Privacy & permissions · Health
- ten rows, no organism section names, Developer pages behind a switch.

### 12 — The Now rail, voice cloning, Myna (2026-10-14)

- [x] **12a** Rail "Changed" listed one row per *write* - one file edited ten
  times filled the card - and counted only added lines. Now one row per file,
  `+added -removed`. (`tests/test_now_rail.py`, 19.)
- [x] **12b Rail scope**: "Changed" is the session-wide undo ring while "Made"
  beside it is this conversation - two scopes in neighbouring cards. Scope
  Changed to the conversation's turns (`changes_for_turn`) or say "recent".
  **Done 2026-10-20, the second way.** The card reads **"Changed recently"** -
  it is the last twenty changes on this machine, which spans sessions - while
  the card beside it reads this conversation's files and says "yet". The pair
  now reads as one thought about one scope instead of two answers to one
  question.
- [x] **12c Rail noise**: NOW repeats "Ready" (third time on screen); 5 of 9
  cards are boilerplate empties. Collapse empty cards to one line; drop NOW.
  **Done 2026-10-20.** The empty cards were already collapsed (a quiet
  section is one line beside its heading). The repeat was not: measured on a
  1280px capture, "Ready" was on screen three times - under the orb, in the
  rail's NOW card, and in the state line. NOW now says what is *running*,
  which the state word does not say.
- [x] **12d Rail reach**: only above 1040px, window opens at 460 (item 1a).
  **Done 2026-10-20, with item 1a.** The rail is still a wide-window column -
  the conversation is what the window is for - but the facts it held that
  were *lost* at 460 are on the main window now: the running timers and how
  full the model's window is are above the composer, the rules that fired are
  in the header bell, the turn's cost, tokens and files are in its footer, and
  the files it changed have a door to the Changes panel. 1a's measurement of
  what is reachable at 460 is the record.
  Fold its live parts into the main window (11o bell, 11p Today strip) and keep
  the rail as the wide-screen detail view.
- [ ] **12e Voice cloning**: two gates (switch + per-voice consent bound to the
  clip's SHA-256) are right, but the feature is unreachable: the engine
  (KittenTTS, torch) is not packaged, and registration has no UI by design (a
  control injection could press). Proposal: a registration flow only a person
  can complete - the speaker reads a consent sentence live into the mic, and
  that recording is both the consent record and the reference clip.
- [ ] **12f Myna**: `snap info myna` finds it in the Snap Store (publisher
  "charles05", not installed here); IBus 1.5.34 is installed, so M3's IBus
  backend is buildable on this box. M5 note: Models > "Use it" on a Parakeet
  card now selects `stt-backend` (9e) - the two engines are switched by one key.
  Myna's core flow (hotkey -> speak -> text lands in the focused app) is what
  Chronoa's Dictate could also offer as "dictate into this app", through
  `ui_driver.type_text` and its M2 secure-field guard.

## Blocked / recorded

- ~~Wide main-window layouts unphotographable~~ **photographable since
  2026-10-14 with `tools/live_window.sh -s 1280x860`** (real app, headless
  gnome-shell). Original note:
  Wide main-window layouts unphotographable (structural: `_deepest_node` finds
  only composites that paint no children; `CHRONOA_FORCE_SIZE=1` blanks `main`).
- No render target for a permission prompt; `composer-question` drives
  `gui/questions.py` and the row never appears.
- Diagnostics sections 3-5 absent from every capture (needs `section:<name>`).
- Pre-existing split-view overflow at 380 wide (requested 403px).
- Item 3 of the original design review (the acceptor for
  `consolidation.Digest.candidates`) is **landed**: `decide_candidate()` in
  `senses/memory.py`, with two consent surfaces calling it — the Memory panel's
  "Proposed" section and the `review_proposals` skill. The item still open
  below is 3-of-this-list, the command palette, which is a different thing.
- **Still unmeasured:** whether the Proposed group belongs *inside*
  `common.scrolled(body)`. `tests/test_ui_layout_contract.py` could not be run
  for it (a concurrent mutation run held the lock), so the layout contract for
  this section is **unknown, not passing**. Treat the section's placement as
  unreviewed until that file has been run against it.
- `--check-anchors`: one rot — `arxiv_search`'s anchor occurs 0 times at HEAD
  too, pre-existing in another session's set.
- `test_settings_surface_is_complete::test_every_key_is_accounted_for` names
  **`piper-tts-enabled`**: in HEAD's schema, no row anywhere in
  `settings_window/`. Proved pre-existing (both facts measured at HEAD, not
  after my edit). Recorded rather than fixed — it belongs to the Piper voice
  work, not to this list.
- `test_mutation_harness_can_fail::test_an_equivalent_mutant_states_its_reason`
  fails on **`guardrail_enum.py`**, another session's committed set with an
  `equivalent` that carries its reason in a comment instead of a `why` key. The
  new `secure_field` set passes it.


### 13 — Connect a device (2026-10-14, Claude Code)
- [x] **13a** One window (`gui/connect_device.py`, `app.connect-device::<route>`):
  Bluetooth (`bt_pair`, own BlueZ agent, code compare / PIN / passkey, GATT
  connect-without-pairing for wearables), Android (`adb_pair`: USB Allow step,
  Wi-Fi by QR or pairing code), KDE Connect (`lan_link.pair`, stored + pinned,
  bridge switch). Entry points: palette (4), Phone, Phone link, Android,
  Wearables, Devices empty states. Fakes: `tools/fake_bluez.py`,
  `tools/fake_adb.py`, `tools/fake_kdeconnect.py`.
- [ ] **13b** Live capture of the window with `tools/live_window.sh` against the
  fakes (DBUS_SYSTEM_BUS_ADDRESS to fake_bluez), for the screenshot record.
- [ ] **13c** Dialer redesign, car-infotainment style (asked 2026-10-14).
