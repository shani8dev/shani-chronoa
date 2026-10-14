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

- [ ] **M3** Myna transfer: an **IBus injection backend**. Myna's own reasoning
  is that IBus is the only commit path that reaches arbitrary focused fields
  under Wayland without privileges; Chronoa's ladder is portal → ydotool →
  XTEST and every rung is session-wide or container-only. Measure whether an
  IBus engine is installable/runnable here before writing it.
- [ ] **M4** Myna transfer: **partial vs committed** for the STT path. Chronoa's
  whisper is batch; Myna streams partial internally and commits only final
  text. Decide honestly whether whisper.cpp can produce a usable partial here
  (it is an AED model — per Myna's own thread, streaming requires chunking +
  hypothesis stabilisation) and record the verdict either way.
- [ ] **M5** Myna transfer: **model interchange**. Myna ships Whisper, Parakeet
  and FunASR as interchangeable backends behind one session API. Chronoa has
  `stt.py` (whisper.cpp) and `stt_parakeet.py`. Measure whether the two are
  actually interchangeable behind `stt.build_stt`'s contract, and whether a
  third engine is a config change or a rewrite.
- [ ] **M6** Myna transfer: the **"state and why" answer**. `myna.status`
  exists as a CLI whose job is "what state dictation is in, and why". Chronoa's
  equivalent is the Diagnostics panel's own three-word vocabulary plus
  `listening.py`. Check the two agree on this box.
- [ ] **M7** Myna's boundary is a design decision to copy, not a gap: it is
  "deliberately focused on dictation... not a voice assistant". Chronoa's
  `dictate()` already shares capture, transcription and turn with the assistant
  path. Record whether dictation should stay a mode of the assistant or become
  a peer of it.

### 1 — The rail is reachable at the app's own geometry

- [ ] **1a** At ≤1040 the rail is deleted by `RailBreakpoint`. Replace with a
  compact always-present Now line (state · tokens · timers · files changed ·
  rules fired) and/or a pull-up sheet, so 10 composed facts survive at 460.
- [ ] **1b** Decide and record: rail-first at narrow widths (chat becomes the
  sheet) versus a one-line ticker. Allocation-measured at 380/460/760/1024.

### 2 — A turn is a unit of work and has no object

Measured: `max_rounds` / `_rounds_for_origin` appear **nowhere in `gui/` or
`app/`**; a 40-round, 600s, 27-tool turn shows only a ticking tool name.
`turn_stats()` and `changes_for_turn()` already compute the rest.

- [ ] **2a** Surface round *n*/40, clock, tools run, files written, prompts
  answered, whether it left the machine, and what was elided — in the rail's
  Now section and in a transcript turn card.
- [ ] **2b** `origin` is never passed by any production `.handle()` call, so the
  per-origin budget (user 40 / unattended 1 / unknown 1) is dead in production
  and unattended triggers bypass `handle()` entirely. Decide and record.

### 3 — The command palette cannot reach 256 of 257 capabilities

Ctrl+K has 5 groups (Go to, Panels, Modes, Commands, Cloud models) and **no
skills**. AGENTS.md calls it "everywhere you can go".

- [ ] **3a** A skills group in the palette.
- [ ] **3b** The real entry point: **query capabilities by effect** — everything
  that writes files / leaves the machine / needs a key / is destructive. Read
  from `capabilities` (`gated_by`, `READ_ONLY_TOOLS`, the destructive set), not
  a second hand-kept list.

### 4 — The Activity panel is a graveyard

`_row(record)` builds `common.row(...)` plus a tooltip. **No button, no
action.** It reads a 19,111-record log of everything this machine did.

- [ ] **4a** Make it a workbench: re-run a call through `execute_tool` so
  consent, sandbox and verification still apply; diff the arguments; show the
  verdict.

### 5 — Consent is per-call in a 40-call turn, with no forecast

- [ ] **5a** A forecast, not a broader grant: prompt *n* of about N, and on the
  first prompt of a turn one line naming the whole shape (writes 2 files, 1
  network request). The person stays the authoriser of every call; they stop
  being ambushed by the count.

### 6 — Nothing shows what the model was offered, or why it picked

35% of the tools actually sent share no word with the request; `ranked()`
reorders by a dict lookup plus a learned multiplier, all in silence.

- [ ] **6a** A "why that tool" inspector on a tool card: request → offered
  tools with scores → learned multiplier → winner.

### 7 — Small measured ones

- [ ] **7a** `gui/rail.py:349` renders `segment_rows()[:4]` of a **dynamic**
  list, so on a real turn 2+ context segments are always silently dropped from
  the rail's Context section.
- [ ] **7b** The organ strip is eight lowercase words, dark on every fresh
  machine (`store_dir()` absent here, zero dreams ever written), reading as one
  run-on string. Decide: teach the vocabulary, or replace the resting state.
- [ ] **7c** The panel count in AGENTS.md is 34/39 against a measured **36**.
  Correct the record.

## Blocked / recorded

- Wide main-window layouts unphotographable (structural: `_deepest_node` finds
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
