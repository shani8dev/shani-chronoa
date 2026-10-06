# Chronoa target architecture: the "digital organism"

**What this is:** the architecture Chronoa is aiming for (the diagram in Part 1,
adopted 2026-10-02), with every box mapped against what the code does today
(Part 2) and the gaps put in order (Part 3). Part 4 lists where the diagram
conflicts with decisions already made in `AGENTS.md`. Those decisions win until a
person changes them.

**Keep it current.** When a change fills in or moves a box, update its row in
Part 2 in the same commit. A row's status is a claim about the code, so it is
held to the same standard as everything else here: confirm it by running the
code, not by reading it.

Status key:

- ✅ **have**: implemented and exercised by tests or a slot run.
- 🟡 **partial**: part of the box exists, or it exists in a narrower form.
- ❌ **missing**: nothing yet.
- 🚫 **deliberately not**: ruled out for a recorded reason (see Part 4).

Mapped against the tree on 2026-10-05: 152 skills, 49 senses, 19 trigger event
types, and 2 user units (`shani-chronoa-daemon`, `shani-chronoa-llm`).

Those counts are measured, not remembered - `skills.discover_skills()`,
`senses.discover_senses()` and `triggers.EVENT_TYPES` - so re-run them rather
than incrementing a number by hand when adding one.

---

## Part 1: the target (verbatim, as given)

```
                                      SHANI-CHRONOA
                           ┌─────────────────────────────────┐
                           │       DIGITAL ORGANISM          │
                           │  Multimodal • Local • Persistent│
                           └────────────────┬────────────────┘
                                            │
 ╔══════════════════════════════════════════╪══════════════════════════════════════════╗
 ║                           🧬 INSTINCT / SAFETY                                      ║
 ║                                                                                     ║
 ║  systemd • polkit • AppArmor • seccomp • firewalld • udev rules                    ║
 ║  Policy Engine • Capability ACL • Confirmation Manager                              ║
 ╚══════════════════════════════════════════╪══════════════════════════════════════════╝
                                            │
                                            ▼
╔══════════════════════════════════════════════════════════════════════════════════════╗
║                                  👁 PERCEPTION                                       ║
╠═══════════════════════╦══════════════════════╦══════════════════════╦════════════════╣
║ 👁 EYES               ║ 👂 EARS              ║ 👃 SMELL             ║ 🖐 TOUCH       ║
║ Vision                ║ Speech / Audio       ║ Environment          ║ Human Input    ║
║                       ║                      ║                      ║                ║
║ OpenCV                ║ whisper.cpp          ║ systemd              ║ libinput       ║
║ ONNX Runtime          ║ Parakeet             ║ journald              ║ evdev          ║
║ OpenVINO              ║ Vosk                 ║ D-Bus                 ║ udev           ║
║ Tesseract             ║ Silero VAD           ║ lm-sensors            ║ BlueZ          ║
║ RapidOCR              ║ openWakeWord         ║ smartmontools         ║ USB/HID        ║
║ Qwen-VL / VLM         ║ PipeWire             ║ NetworkManager        ║ touchscreen    ║
║ Screenshot Portal     ║ ALSA                 ║ firewalld             ║ keyboard       ║
║ Camera / Webcam       ║                      ║ AppArmor/audit        ║ mouse/gamepad  ║
╚═══════════════╤═══════╩══════════════╤═══════╩══════════════╤═══════╩═══════╤════════╝
                │                      │                      │                │
                └──────────────────────┴──────────┬───────────┴────────────────┘
                                                   │
                                                   ▼
                                          🍽 EAT / INGEST
                                  ┌──────────────────────────┐
                                  │ DATA ACQUISITION         │
                                  ├──────────────────────────┤
                                  │ filesystem watcher       │
                                  │ inotify / fanotify       │
                                  │ journald                 │
                                  │ D-Bus                   │
                                  │ PipeWire                │
                                  │ udev                    │
                                  │ Network APIs            │
                                  │ MCP                     │
                                  │ REST / GraphQL           │
                                  │ Web / feeds             │
                                  │ PDFs / Office / images  │
                                  └────────────┬─────────────┘
                                               │
                                               ▼
                                     🧠 PERCEPTION FUSION
                                  ┌──────────────────────────┐
                                  │ Event Bus                │
                                  │ Context Fusion           │
                                  │ Entity Extraction        │
                                  │ Temporal Context        │
                                  │ Multimodal Context       │
                                  ├──────────────────────────┤
                                  │ Rust / Tokio             │
                                  │ D-Bus                   │
                                  │ SQLite                  │
                                  └────────────┬─────────────┘
                                               │
                    ┌──────────────────────────┼──────────────────────────┐
                    │                          │                          │
                    ▼                          ▼                          ▼
             🎯 ATTENTION                  🧠 MEMORY                 🧍 SELF-MODEL
             ─────────────                 ──────────                 ────────────
             Salience Engine              SQLite                     Capability DB
             Priority Queue               SQLite FTS5                 Hardware DB
             Interrupt Manager            Qdrant                     Model Registry
             Context Window               LanceDB/Chroma              Tool Registry
             Event Priority               Embeddings                  Permission State
                    │                     RAG                          OS State
                    │                     Episodic                     systemd
                    │                     Semantic                     D-Bus
                    │                     Procedural
                    │
                    └──────────────────────────┬─────────────────────────┘
                                               │
                                               ▼
                              ╔══════════════════════════════╗
                              ║          🧠 BRAIN            ║
                              ║       REASONING LAYER        ║
                              ╠══════════════════════════════╣
                              ║                              ║
                              ║ llama.cpp                   ║
                              ║ Ollama                      ║
                              ║ ONNX Runtime                ║
                              ║                              ║
                              ║ Model Router                ║
                              ║ Context Manager             ║
                              ║ Prompt Manager              ║
                              ║ Task Planner                ║
                              ║ Agent Runtime               ║
                              ║ Tool Router                 ║
                              ║ MCP                         ║
                              ║ Event Bus                   ║
                              ║                              ║
                              ╚══════════════╤═══════════════╝
                                             │
             ┌───────────────────────────────┼───────────────────────────────┐
             │                               │                               │
             ▼                               ▼                               ▼
      💭 IMAGINATION                  🧭 CURIOSITY                     ❤️ AFFECT
      ───────────────                 ──────────                      ─────────
      Scenario Engine                 Unknown Detector                State Machine
      What-if Simulation              Information Gain                Urgency
      Prediction                      Search Planner                   Satisfaction
      Counterfactual                  Exploration                      Frustration
      Sandbox                         Question Generator               Calm/Alert
             │                               │                               │
             │                         Web / Search                         │
             │                         MCP / RAG                             │
             │                                                               │
             └───────────────────────────────┼───────────────────────────────┘
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                         🎯 MOTIVATION    🗣 LANGUAGE    🧠 LEARNING
                         ─────────────    ───────────    ───────────
                         Goal Manager     LLM            Feedback Loop
                         Task Queue       STT            Experience
                         Priorities       TTS            Adaptation
                         Scheduler        Translation    Evaluation
                         Reward Model     NLU/NLG        Preference Store
                         Persistence      Embeddings     Skill Learning
                              │                │               │
                              └────────────────┼───────────────┘
                                               │
                                               ▼
                                        👅 TASTE / EVALUATE
                                  ┌──────────────────────────┐
                                  │ Output Evaluator         │
                                  │ Confidence Checker       │
                                  │ Schema Validator         │
                                  │ Test Runner              │
                                  │ Action Verifier          │
                                  │ LLM-as-Judge             │
                                  │ User Feedback            │
                                  │ Result Comparison        │
                                  └────────────┬─────────────┘
                                               │
                                               ▼
                                         ⚖️ DECISION
                                  ┌──────────────────────────┐
                                  │ Planner                  │
                                  │ Policy Engine            │
                                  │ Risk Engine              │
                                  │ Permission Manager       │
                                  │ Confirmation Manager     │
                                  └────────────┬─────────────┘
                                               │
                       ┌───────────────────────┼───────────────────────┐
                       │                       │                       │
                       ▼                       ▼                       ▼
                 🧬 INSTINCT              ⚡ REFLEX                🛡 SAFETY
                 Hard Rules              Immediate Events          Security
                 systemd                 udev                      polkit
                 Policy                  D-Bus                      AppArmor
                 Priorities              event handlers             seccomp
                 Emergency               wake word                  firewalld
                       │                       │                       │
                       └───────────────────────┼───────────────────────┘
                                               │
                                               ▼
                                          ✋ HANDS
                                     ACTION / EXECUTION
                                  ┌──────────────────────────┐
                                  │ D-Bus                   │
                                  │ systemd                 │
                                  │ GNOME APIs              │
                                  │ KDE / KWin              │
                                  │ XDG Portals             │
                                  │ AT-SPI                  │
                                  │ libinput / uinput       │
                                  │ Shell / CLI              │
                                  │ MCP                     │
                                  │ REST / GraphQL           │
                                  │ Application APIs        │
                                  └────────────┬─────────────┘
                                               │
                 ┌─────────────────────────────┼─────────────────────────────┐
                 │                             │                             │
                 ▼                             ▼                             ▼
          🖥 DESKTOP CONTROL             🗣 VOICE OUTPUT               🎨 CREATION
          ────────────────              ───────────────               ──────────
          GNOME Shell                    Piper                         TEXT
          KDE/KWin                       Kokoro                        LLM
          AT-SPI                         eSpeak-ng
          XDG Portals                    PipeWire                      IMAGE
          xdg-utils                                                     Stable Diffusion
          wlroots APIs                   🎧 AUDIO                       stable-diffusion.cpp
                                         MusicGen                      ComfyUI
          FILES                          AudioCraft
          POSIX tools                    Demucs                        AUDIO
          fs APIs                                                       MusicGen
                                                                        AudioCraft
          APPS                           🎬 VIDEO                      AudioLDM
          D-Bus                          Wan
          portals                        HunyuanVideo                  VIDEO
          MCP                            CogVideoX                      Wan
                                                                        HunyuanVideo
                                         🧊 3D                         CogVideoX
                                         TripoSR
                                         InstantMesh                    3D
                                                                        TripoSR
                                                                        InstantMesh
                 │                             │                             │
                 └─────────────────────────────┼─────────────────────────────┘
                                               │
                                               ▼
                              ╔══════════════════════════════╗
                              ║          🖥 SHANIOS           ║
                              ╠══════════════════════════════╣
                              ║ systemd                    ║
                              ║ D-Bus                      ║
                              ║ PipeWire                   ║
                              ║ Wayland                    ║
                              ║ GNOME / KDE                ║
                              ║ NetworkManager             ║
                              ║ udev                       ║
                              ║ firewalld                  ║
                              ║ AppArmor                   ║
                              ║ Btrfs                      ║
                              ║ Applications               ║
                              ║ Hardware                   ║
                              ╚══════════════╤═══════════════╝
                                             │
                                             ▼
                                      🌍 ENVIRONMENT
                              ┌──────────────────────────┐
                              │ CPU / GPU                │
                              │ RAM / Storage            │
                              │ Temperature              │
                              │ Battery                  │
                              │ Network                  │
                              │ Devices                  │
                              │ Applications             │
                              │ User                     │
                              └────────────┬─────────────┘
                                           │
                  ┌────────────────────────┼────────────────────────┐
                  ▼                        ▼                        ▼
              😖 PAIN                  🎁 REWARD                 👅 TASTE
              ──────                   ────────                  ─────
              Errors                   Success                   Validation
              Crashes                  Completion                Quality
              Threats                  Goal achieved             Correctness
              Overheating              User approval              Confidence
              Resource pressure        Useful result              Consistency
                  │                        │                        │
                  └────────────────────────┼────────────────────────┘
                                           │
                                           ▼
                                      🧠 LEARNING
                              ┌──────────────────────────┐
                              │ Experience Store         │
                              │ Feedback Store            │
                              │ Skill Registry            │
                              │ Preference Store          │
                              │ Error Knowledge           │
                              │ Environment Model        │
                              └────────────┬─────────────┘
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    │                      │                      │
                    ▼                      ▼                      ▼
                 💤 SLEEP              🌙 DREAM             🧠 CONSOLIDATE
                 ─────────              ─────────             ─────────────
                 systemd                Dream Engine           Memory replay
                 idle scheduler         Scenario generator     Summarization
                 background workers     LLM simulation         Embedding
                 maintenance            Counterfactuals        indexing
                 low-power mode         Synthetic situations   knowledge graph
                    │                      │                      │
                    └──────────────────────┼──────────────────────┘
                                           │
                                           ▼
                                    🧠 SUBCONSCIOUS
                              ┌──────────────────────────┐
                              │ Background Event Workers │
                              │ Anomaly Detection        │
                              │ Prediction               │
                              │ Pre-fetching             │
                              │ Memory Association       │
                              │ Maintenance              │
                              │ Monitoring               │
                              │ Preparation              │
                              └────────────┬─────────────┘
                                           │
                                           ▼
                                     🫀 HOMEOSTASIS
                              ┌──────────────────────────┐
                              │ CPU / GPU                │
                              │ RAM                      │
                              │ Storage                  │
                              │ Thermals                 │
                              │ Battery                  │
                              │ Network                  │
                              │ Services                 │
                              │ Security                 │
                              ├──────────────────────────┤
                              │ systemd                  │
                              │ lm-sensors               │
                              │ smartmontools             │
                              │ NetworkManager            │
                              │ journald                 │
                              │ firewalld                │
                              └────────────┬─────────────┘
                                           │
                                           ▼
                                      ⏰ RHYTHM
                              ┌──────────────────────────┐
                              │ Awake                    │
                              │ Active                   │
                              │ Idle                     │
                              │ Sleep                    │
                              │ Dream                    │
                              │ Maintenance              │
                              ├──────────────────────────┤
                              │ systemd timers           │
                              │ cron / scheduler         │
                              │ power-profiles-daemon    │
                              └────────────┬─────────────┘
                                           │
                                           └──────────────────────────↺
```

---

## Part 2: where Chronoa stands today, box by box

Paths are relative to `usr/lib/shani-chronoa/shani_chronoa/`.

### At a glance

| Layer | Status | One line |
|---|---|---|
| Instinct / Safety | ✅ | consent keys, approvals, sandbox levels and per-origin profiles, Landlock, opt-in seccomp, redaction, egress log |
| Perception: Eyes | ✅ | capture, Tesseract OCR (12 extra languages), a llama.cpp vision model (setup's Eyes), OpenCV faces/objects/people (setup's Photos) |
| Perception: Ears | ✅ | whisper.cpp (CLI and persistent server), Parakeet, energy VAD, wake word, PipeWire/ALSA; sounds identified (CED-tiny, on demand via the `heard-sound` sense or the `sound` trigger) and speakers told apart (diarization) as extras |
| Perception: Smell | ✅ | about 40 machine-state senses: systemd, journald, D-Bus, hwmon, NetworkManager, firewall, LSMs |
| Perception: Touch | 🟡 | idle time, USB/Bluetooth/devices inventory; no raw input events (on purpose) |
| Eat / Ingest | 🟡 | file-watch (polling), journald, D-Bus, udev, web, office/PDF; no feeds, no MCP client |
| Perception fusion | 🟡 | per-turn percept assembly and trigger engine; no general event bus, no entity extraction |
| Attention | 🟡 | tool selection, context fitting, compression; no salience or priority queue |
| Memory | ✅ | durable facts with links and history; conversations and photos searched by words (FTS5) and by meaning (embeddings, setup's Memory) |
| Self-model | ✅ | capabilities, hardware tier, model choice, model fit, child supervisor |
| Brain | ✅ | llama.cpp (default), Ollama, cloud fallback chain, tool loop, MCP server |
| Imagination / Curiosity / Affect | ❌ | only fragments (plan mode, loop detection, ask_user) |
| Motivation | 🟡 | todo list, reminders, timers, scheduled triggers; no goal manager |
| Language | ✅ | LLM, STT, TTS, translation; **cloud STT as a fallback** (`cloud_voice.CloudSTT`, opt-in, and **only when no local model can listen**) - every cloud speech route measured needs a key, so it is never anonymous |
| Learning | 🟡 | memory facts and history; verification verdicts reorder the tools already allowed (`learning.py`), and a **distilled routing student** (`distill.py`) can narrow them further - measured 62% on held-out requests vs a 12% baseline, from a teacher that agreed with the human label 47/54. Still no preference learning from what you liked, and no skill learning |
| Taste / Evaluate | 🟡 | post-condition verification, guardrail, history repair, schema refusal; no judge or confidence |
| Decision | ✅ | permissions, approvals, plan mode, presets, guardrail |
| Reflex | ✅ | 18 event types, wake word, barge-in |
| Hands | ✅ | D-Bus, systemd, GNOME and Plasma backends, portals, AT-SPI, typed input; no generic shell (on purpose) |
| Voice output | ✅ | Piper (English + six Indian-language voices), Kokoro (sherpa-onnx, six voices, opt-in), espeak-ng floor, streaming sentence by sentence; **cloud synthesis as the last link** (`cloud_voice.CloudTTS`, opt-in per capability, checked on every reply) |
| Creation | 🟡 | text, documents, sheets, slides, image looks and upscale; pictures from a description and photo edits by description (stable-diffusion.cpp); no audio/video/3D generation |
| Pain / Reward | 🟡 | faults, coredumps, resource pressure, loop detection; no reward signal |
| Sleep / Dream / Consolidate | 🟡 | idle consolidation of finished conversations (`consolidation.py`, extractive digest plus a queue of proposed facts, from the daemon's tick); no dream engine |
| Subconscious | 🟡 | ambient polling and unattended rules; no anomaly detection or prediction |
| Homeostasis | 🟡 | senses cover every listed vital; correcting needs a rule the user arms |
| Rhythm | 🟡 | daemon and user units, idle sense; no awake/idle/sleep state machine |

### 🧬 Instinct / Safety

| Box | Status | Where |
|---|---|---|
| systemd | ✅ | `skills/control_service.py`, `senses/services.py`; user units `shani-chronoa-daemon`, `shani-chronoa-llm` |
| polkit | 🟡 | privileged skills go through pkexec (`manage_mount`, `set_sleep_inhibit`, `control_service`); no polkit rules of Chronoa's own |
| AppArmor | ❌ | not Chronoa's layer; the OS profile lives in `shani-settings`. `senses/security.py` reports which LSMs are active |
| seccomp | ✅ | `sandbox/seccomp.py`, opt-in through `sandbox-seccomp-enabled`, off by default |
| firewalld | 🟡 | read only: `senses/firewall.py` |
| udev rules | ❌ | Chronoa ships none; it reads udev events (USB plug) |
| Policy engine | ✅ | `sandbox/profiles.py` (profile per origin), `capabilities.py` presets, `<name>-sense-enabled` consent keys |
| Capability ACL | ✅ | the fixed skill whitelist in `tools.py`; `tool_tracking.py` tags each call with its origin |
| Confirmation manager | ✅ | `approvals.py` (approve from a notification), `permissions.py` (scoped answers), `ask_bridge.py` |
| (extra, not in the diagram) | ✅ | `sandbox/landlock.py`, bwrap levels, `redaction.py`, `egress.py` audit log, `guardrail.py` |

### 👁 Perception

**Eyes**

| Box | Status | Where |
|---|---|---|
| OpenCV / ONNX Runtime / OpenVINO | ✅ | OpenCV (user-local wheel, setup's Photos) for faces, objects, people masks and page flattening; onnxruntime arrives bundled in sherpa-onnx |
| Tesseract | ✅ | `senses/ocr.py`, `skills/scan_document.py` |
| RapidOCR | ❌ | needs onnxruntime |
| Qwen-VL / VLM | ✅ | `local_vision.py` on llama.cpp (`--mmproj`, sleeps when idle: 813 -> 131 MB measured), SmolVLM2/Qwen3-VL by RAM; Ollama as fallback |
| Screenshot portal | ✅ | `screengrab.py`, `skills/screenshot.py` |
| Camera / webcam | ✅ | `screengrab.py` camera capture behind consent; `senses/capture.py` reports who else holds the camera |

**Ears**

| Box | Status | Where |
|---|---|---|
| whisper.cpp | ✅ | `stt.py`, `stt_server.py` (persistent `whisper-server`: 0.35 s against 1.15 s for the CLI) |
| Parakeet | ✅ | `stt_parakeet.py` |
| Vosk | ❌ | not needed while whisper covers it |
| Silero VAD | 🚫 | `vad.py` is a dependency-free energy VAD by choice; Silero needs onnxruntime |
| openWakeWord | 🚫 | not in the Arch repos; `wakeword.py` matches the phrase with whisper.cpp instead |
| PipeWire / ALSA | ✅ | `audio.py` (`pw-record`/`pw-play`, falling back to `arecord`/`aplay`), `pipewire.py`, `senses/audio.py` |

**Smell (environment)**

| Box | Status | Where |
|---|---|---|
| systemd | ✅ | `senses/services.py`, unit-health event |
| journald | ✅ | `senses/faults.py`, `skills/read_logs.py`, journal-match event |
| D-Bus | ✅ | D-Bus property event, `phone.py`, `eds_calendar.py` |
| lm-sensors | ✅ | `senses/hwmon.py` reads the same hwmon sysfs directly; `senses/thermalgrid.py` |
| smartmontools | 🟡 | `senses/storage.py` reads sysfs health only; no `smartctl` |
| NetworkManager | ✅ | `senses/network.py`, `skills/network_check.py`, net-state event |
| firewalld | ✅ | `senses/firewall.py` |
| AppArmor / audit | 🟡 | `senses/security.py` (LSMs, Secure Boot, TPM, lockdown); no audit log |

**Touch (human input)**

| Box | Status | Where |
|---|---|---|
| libinput / evdev / keyboard / mouse | 🚫 | raw input events are keystroke capture. `senses/idle.py` reads only how long since the last input |
| udev / USB/HID | ✅ | `senses/usb.py`, `senses/devices.py`, USB-plug event |
| BlueZ | ✅ | `senses/bluetooth.py`, `skills/bluetooth_devices.py`, BT-connect event |
| touchscreen / gamepad | ❌ | nothing specific |

### 🍽 Eat / Ingest

| Box | Status | Where |
|---|---|---|
| Filesystem watcher | 🟡 | `triggers/sources.py` `PollingDirWatcher`, a stdlib snapshot diff. Designed so an inotify backend is a subclass |
| inotify / fanotify | ❌ | see above; fanotify needs root |
| journald, D-Bus, udev | ✅ | trigger event sources (`triggers/sources.py`, `triggers/desktop_sources.py`) |
| PipeWire | ✅ | audio-device event |
| Network APIs / REST | 🟡 | `netjson.py` (one JSON GET, used by weather, currency and similar); no GraphQL |
| MCP (as a client) | 🚫 | Chronoa is an MCP server only; a client needs a human decision |
| Inbound channels (WhatsApp, Telegram, anything D-Bus) | ✅ | `gateway.py`: one method, `Submit(gateway, text)`, on the session bus at `dev.shani.chronoa.Gateways`. Configured by the `gateways` setting and editable in Settings → Privacy, which reloads live. Ask-only by default; the text goes through the window's own `_submit`, so it meets the same whitelist, consent keys and post-conditions as anything typed. Bounded (4,000 chars, 20/min), off unless asked for. **No channel integration itself** - a WhatsApp or Telegram bridge is a separate component with its own credentials |
| Web / feeds | 🟡 | `webtext.py`, `senses/web.py`, `skills/web_search.py`; no RSS/Atom |
| PDFs / Office / images | ✅ | `skills/read_document.py`, `office/` (docx/xlsx/pptx/odf), `skills/analyze_table.py`, OCR |

### 🧠 Perception fusion

| Box | Status | Where |
|---|---|---|
| Event bus | 🟡 | the trigger engine (`triggers/`) is the only bus: sources produce events and rules consume them. Nothing else subscribes |
| Context fusion | ✅ | `senses/context.py` assembles live percepts into one turn; `local_llm.normalize_messages` |
| Entity extraction | ❌ | nothing; memory links are stated explicitly, not extracted |
| Temporal context | 🟡 | `senses/timebase.py`, `senses/latch.py` (change detection), timestamps on percepts |
| Multimodal context | 🟡 | attachments, OCR and vision text go into the turn as text |
| Rust / Tokio | 🚫 | Chronoa is Python with GLib and asyncio (`asyncbridge.py`); a rewrite is not justified |
| SQLite | ✅ | `conversation_store.py` (FTS5); the percept store is JSONL |

### 🎯 Attention · 🧠 Memory · 🧍 Self-model

| Box | Status | Where |
|---|---|---|
| Salience engine / event priority | ❌ | percepts are not scored |
| Priority queue | ❌ | — |
| Interrupt manager | 🟡 | barge-in (`audio.py` `BargeInMonitor`) is the only interrupt |
| Context window | ✅ | `local_llm.fit_to_context` (n_ctx from `/props`), `compression.py`, `history_repair.py`, `tool_select.py` (sends only the schemas a request could use) |
| SQLite / FTS5 | ✅ | `conversation_store.py`: bm25, incremental index |
| Qdrant / LanceDB / Chroma | ❌ | — (Part 4: prefer one SQLite file) |
| Embeddings / RAG | ✅ | `local_embed.py` (nomic-embed, llama.cpp `--embedding`); hybrid search in `conversation_store` and `photo_library` |
| Episodic | ✅ | saved conversations, searchable (`skills/conversations.py`) |
| Semantic | ✅ | `senses/memory.py`: facts, subject–relation–object links, `about`, change history, `forget` |
| Procedural | 🟡 | `user_prompts.py` (rules file, `/commands`); nothing learned |
| Capability DB / tool registry | ✅ | `capabilities.py`, `tools.py`, `skills/list_capabilities.py` |
| Hardware DB | ✅ | `hardware_profile.py`, `senses/hardware.py`, `senses/gpu.py`, `senses/devices.py` |
| Model registry | ✅ | `model_choice.py`, `local_llm.TIERS`, `senses/modelfit.py`, `skills/model_manager.py` |
| Permission state | ✅ | `permissions.py`, consent keys in GSettings |
| OS state | ✅ | the machine-state senses; `child_supervisor.py` for Chronoa's own children |

### 🧠 Brain

| Box | Status | Where |
|---|---|---|
| llama.cpp | ✅ | `local_llm.py`, `shani-chronoa-llm.service`; CPU (`-ngl 0 -t N`) and Vulkan GPU (`-ngl 99`), chosen from `llama-server --list-devices` |
| Ollama | ✅ | `ollama_llm.py`, optional |
| ONNX Runtime | ❌ | not in the repos |
| Model router | 🟡 | `model_choice.py` (one model per job), cloud fallback chain in `cloud_llm.py`, and every backend reachable behind one `chat_message()` interface (llama.cpp, Ollama, Anthropic's native adapter, OpenAI, Gemini, Groq, OpenRouter, opencode-zen, keyless gateways) - but no routing per request by difficulty |
| Context manager | ✅ | see Attention |
| Prompt manager | ✅ | `user_prompts.py`; rules go in the head system message, changing percepts in the last user message |
| Task planner | 🟡 | `planmode.py` is look-but-don't-touch, not a planner; `todo_list` tracks steps |
| Agent runtime | ✅ | `assistant.py` tool loop with streaming, `loops.py` |
| Tool router | ✅ | `tool_select.py`, `tools.py`, plus a distilled student (`distill.py`) that may only re-rank and narrow names already on offer - it cannot add a skill, and `distill.fallback()` will not run one without arguments or one that deletes something |
| MCP | ✅ (server) | `mcp.py`, `shani-chronoa-mcp` |
| Event bus | 🟡 | see Fusion |

### 💭 Imagination · 🧭 Curiosity · ❤️ Affect

| Box | Status | Where |
|---|---|---|
| Scenario engine / what-if / counterfactual | ❌ | — |
| Sandbox | 🟡 | `planmode.py`, the dry-run flags on `find_and_replace` and `edit_file` previews; the execution sandbox (`sandbox/`) |
| Prediction | ❌ | — |
| Unknown detector / question generator | 🟡 | `skills/ask_user.py` lets the model ask; nothing detects a gap by itself |
| Information gain / search planner / exploration | ❌ | single-shot `web_search` |
| Affect state machine (urgency, frustration, calm/alert) | 🟡 | `loops.py` notices a turn that repeats itself; `cues.py` sounds; no state is kept |

### 🎯 Motivation · 🗣 Language · 🧠 Learning

| Box | Status | Where |
|---|---|---|
| Goal manager / priorities / reward model | ❌ | — |
| Task queue / persistence | 🟡 | `skills/todo_list.py` (statuses, across sessions), `add_reminder`, `timer` |
| Scheduler | ✅ | schedule event, `senses/scheduler.py` ambient polling, `daemon.py` |
| LLM / STT / TTS | ✅ | see Brain, Ears, Voice output |
| Translation | ✅ | `skills/translate_text.py` |
| NLU / NLG | ✅ | through the LLM; `markdown_lite.py` shapes spoken output |
| Embeddings | ❌ | — |
| Feedback loop / evaluation | ❌ | approvals and denials are not recorded as a signal |
| Experience / adaptation / preference store | 🟡 | `senses/memory.py` stores stated preferences ("my editor is vim"); nothing inferred |
| Skill learning | 🚫 | new skills are code a person adds (`~/.config/shani-chronoa/skills/`); a self-written skill would break the whitelist |

### 👅 Taste / Evaluate · ⚖️ Decision

| Box | Status | Where |
|---|---|---|
| Output evaluator / result comparison | 🟡 | `verification.py` post-conditions (an actuator checks its own effect, e.g. `open_application`, `set_scaling`) |
| Confidence checker | ❌ | — |
| Schema validator | ✅ | bad JSON arguments are refused in the turn (`assistant.py`); `local_llm.recover_tool_calls` |
| Test runner | ❌ | — |
| Action verifier | ✅ | `verification.py`, `guardrail.py` |
| LLM-as-judge | ❌ | — |
| User feedback | ❌ | — |
| Planner | 🟡 | see Brain |
| Policy / risk / permission / confirmation | ✅ | `sandbox/profiles.py`, `guardrail.py`, `permissions.py`, `approvals.py`, `capabilities.py` presets |

### 🧬 Instinct · ⚡ Reflex · 🛡 Safety (the action side)

| Box | Status | Where |
|---|---|---|
| Hard rules / emergency | 🟡 | refusals are hard-coded per skill (`DANGEROUS_BINARIES`, consent gates); no emergency mode |
| udev / D-Bus / event handlers | ✅ | 18 event types: audio device, BT connect, calendar, container run, D-Bus property, expiry, failure, file watch, git, journal match, net state, phone, power state, schedule, screen lock, sleep/wake, unit health, USB plug |
| Wake word | ✅ | `wakeword.py` |
| polkit / AppArmor / seccomp / firewalld | see Instinct / Safety above | |

### ✋ Hands

| Box | Status | Where |
|---|---|---|
| D-Bus | ✅ | many skills (media, power, notifications, Bluetooth) |
| systemd | ✅ | `control_service`, `list_services` |
| GNOME APIs / KDE-KWin | ✅ | both backends exist and are run on both images (`desktop_session.py`, appearance and window skills) |
| XDG portals | ✅ | `portal.py` (RemoteDesktop input, GlobalShortcuts), screenshot |
| AT-SPI | ✅ | `skills/ui_elements.py` (inspect, press, menu paths, with an X11 click fallback where GTK4 lacks GrabFocus); `senses/accessibility.py` |
| libinput / uinput | 🟡 | `skills/input_control.py`, `press_key`: typed actions through the portal, `xdotool` or `wtype`. No uinput |
| Shell / CLI | 🚫 | no generic shell exec; every action is a typed skill |
| MCP | 🟡 | server only (see Ingest) |
| REST / GraphQL / application APIs | 🟡 | narrow per skill (`netjson.py`); `android_device`, `phone.py` |

### 🖥 Desktop control · 🗣 Voice output · 🎨 Creation

| Box | Status | Where |
|---|---|---|
| GNOME Shell / KDE / AT-SPI / portals / xdg-utils | ✅ | see Hands; `open_file`, `open_application` |
| wlroots APIs | ❌ | ShaniOS ships GNOME and Plasma only |
| Files: POSIX / fs APIs | ✅ | about 20 file skills, all through `files.py`; `undo_last_change` |
| Apps: D-Bus / portals | ✅ | |
| Piper | ✅ | `voices.py` (user-local release, four pinned female voices), `tts.py` |
| Kokoro | ✅ | through sherpa-onnx's program, like Piper's; opt-in (RTF 1.2 on a CPU) |
| eSpeak-ng | ✅ | fallback in `tts.py` |
| Streaming speech | ✅ | `speech.py`: first audio 0.21 s, against 0.6–0.8 s before |
| Audio tools (MusicGen, AudioCraft, Demucs) | ❌ | GPU-class; see Part 4 |
| Video (Wan, HunyuanVideo, CogVideoX) | ❌ | GPU-class |
| 3D (TripoSR, InstantMesh) | ❌ | GPU-class |
| Text creation | ✅ | `write_text_file`, `edit_file`, `office_document` (docx/xlsx/pptx), `conversations` export |
| Image creation (Stable Diffusion, stable-diffusion.cpp, ComfyUI) | ✅ | `imagegen.py` + `generate_image` (txt2img and img2img), SD-Turbo on `sd-server` |

### 🖥 ShaniOS · 🌍 Environment

Every box is read by a sense: `cpu`, `gpu`, `resources`, `storage`,
`filesystems`, `hwmon`, `thermalgrid`, `power`, `network`, `devices`, `usb`,
`snapshots` (Btrfs), `updates`, `sessions` (users), `services`. Wayland and the
two desktops are detected in `desktop_session.py`. ✅

### 😖 Pain · 🎁 Reward · 👅 Taste (feedback from the world)

| Box | Status | Where |
|---|---|---|
| Errors / crashes | ✅ | `senses/faults.py`, `senses/coredumps.py`, failure event, `child_supervisor.py` |
| Threats | 🟡 | `senses/privilege.py`, `senses/listeners.py`, `senses/security.py`, `senses/stale.py` |
| Overheating / resource pressure | ✅ | `senses/hwmon.py`, `senses/resources.py`, `senses/cgroup.py` |
| Success / completion / goal achieved | 🟡 | post-conditions say whether an action worked; nothing is recorded |
| User approval | 🟡 | `approvals.py` collects it; it is not kept as a signal |
| Validation / quality / confidence | 🟡 | see Taste / Evaluate |

### 🧠 Learning · 💤 Sleep · 🌙 Dream · 🧠 Consolidate · 🧠 Subconscious

| Box | Status | Where |
|---|---|---|
| Experience / feedback / error knowledge store | 🟡 | `tool_tracking.py` records every call with its verdict; `learning.py` reads it back to reorder tool selection, and fits an outcome model that detects `verified` calls at 33x recall / 5.9x precision lift over chance while detecting **no** failure signal at all |
| Skill registry | ✅ | `skills/__init__.py`, user skills directory |
| Preference store | 🟡 | `senses/memory.py` |
| Environment model | 🟡 | the senses give the current state; no history or baseline |
| Sleep: systemd / background workers | ✅ | `daemon.py` + `shani-chronoa-daemon.service` (rules and senses with no window), `runner_lock.py` (window or daemon, never both) |
| Idle scheduler / maintenance / low-power mode | 🟡 | the daemon consolidates on its own timer and skips conversations still in progress; there is no low-power mode |
| Dream engine / scenario generator / synthetic situations | ❌ | — |
| Memory replay / summarisation | 🟡 | `consolidation.py` writes an extractive digest per finished conversation; nothing replays it into a turn yet |
| Embedding / indexing | 🟡 | the FTS5 index updates incrementally; no embeddings |
| Knowledge graph | 🟡 | memory links are a one-hop graph (`about`) |
| Background event workers / monitoring | ✅ | trigger engine in the daemon, `senses/scheduler.py` |
| Anomaly detection / prediction | ❌ | thresholds only (`latch.py`); no baselines |
| Pre-fetching / preparation | 🟡 | `stt_server.py` and the LLM unit keep models warm; prompt-cache reuse (209 of 246 prompt tokens reused in the slot measurement) |
| Memory association | 🟡 | links and `about` |

### 🫀 Homeostasis · ⏰ Rhythm

| Box | Status | Where |
|---|---|---|
| Vitals (CPU/GPU, RAM, storage, thermals, battery, network, services, security) | ✅ sensed | one sense per vital (see Environment) |
| Keeping them in range | 🟡 | only if the user arms a trigger rule; there is no built-in setpoint |
| systemd / lm-sensors / smartmontools / NM / journald / firewalld | as in Smell | |
| Awake / active / idle / sleep / dream / maintenance states | ❌ | no state machine; window-or-daemon is the only mode switch |
| systemd timers / cron / scheduler | 🟡 | schedule event inside the trigger engine; no systemd timers of Chronoa's own |
| power-profiles-daemon | ✅ | `skills/power_profile.py`, power-state event |

---

## Part 3: gap roadmap, in order

The order follows the repo's rules: make things work first, prefer what the OS
and Arch repos already ship, keep hardening light, and keep everything
reversible and consented. Each item names the box it fills and the smallest
real version of it.

**Measure first (2026-10-02 - the user's goal: the smallest model beating bigger ones).** `tools/task_eval.py` scores tool and argument choice per harness lever, against free cloud models as the baseline; each item below is worth what it moves that number.

**Next: high value, buildable on CPU, no new architecture**

1. **Rhythm state machine** (Rhythm, Sleep). Track awake, active, idle and
   sleep from `senses/idle.py`, screen lock, sleep/wake and power events in the
   daemon, and expose it as a percept. Everything below that runs "when idle"
   depends on it.
2. **Feedback and experience store** (Learning, Reward, Taste). One SQLite
   table recording each tool call's outcome: post-condition result, approval or
   denial, the user undoing it, the user correcting it ("no, the other one").
   It is a record only; nothing changes behaviour yet. It joins the
   conversations database.
3. **Embeddings and RAG in SQLite** (Memory). `llama-server --embedding` with a
   small GGUF embedding model, vectors stored next to FTS5, with hybrid
   bm25-plus-vector search over conversations, memory facts and opted-in
   folders. Check that `sqlite-vec` is packaged in Arch before using it;
   otherwise brute-force cosine search in Python is enough at this scale. No
   Qdrant or Chroma (Part 4).
4. **Idle-time consolidation** (Consolidate, Dream: the useful part). While
   idle and on AC power: summarise long conversations, extract memory-link
   candidates for the user to confirm (entity extraction), and refresh the
   index. Uses the local model only and runs under the daemon.
5. **Salience and an interrupt policy** (Attention). Score percepts and events
   by change, severity and the user's own rules; decide speak now, notify, or
   hold for the next turn. Do-not-disturb and the rhythm state gate it.
6. **Homeostasis setpoints** (Homeostasis). Ship built-in, off-by-default rule
   presets (disk nearly full, overheating, a failed unit, battery wear) that
   use the existing trigger engine and the approval flow. They are proposals
   for the user to arm, not autonomous fixes.

**Then: medium cost**

7. **Task planner** (Brain, Motivation). Break a request into `todo_list`
   steps, run them one at a time with plan mode for the look-only steps, and
   resume after a restart. A goal manager is this plus priorities.
8. **Confidence and a judge** (Taste). A cheap self-check pass on the local
   model for actions above a risk level, and "I'm not sure" when it fails.
   Measure the latency cost first on CPU; it may need to be opt-in.
9. **An inotify backend** for `PollingDirWatcher` (Ingest), as the subclass
   its docstring already plans for. Do it only if polling measurably costs
   something.
10. **Vision on llama.cpp** (Eyes). Qwen-VL GGUF with its mmproj through
    `llama-server`, so vision needs no Ollama, the same way text does not.
11. **Feeds** (Ingest). RSS/Atom as a trigger source.
12. **smartctl** (Smell). SMART attributes in `senses/storage.py` when
    `smartmontools` is installed.

**Later: GPU-class, or needs a decision first**

13. **Image generation** with `stable-diffusion.cpp`, which is the only Creation
    item realistic on CPU, and slow there. Audio, video and 3D generation need a
    GPU this machine does not have; revisit once the Vulkan path is the norm.
14. **Kokoro, Silero, RapidOCR, openWakeWord.** All wait on onnxruntime being
    packaged for the images.
15. **Curiosity and imagination engines.** Revisit after 2, 4 and 7 exist,
    because a "what-if" engine without a feedback store has nothing to learn
    from.

---

## Part 4: where the diagram conflicts with standing decisions

These stay as they are until a person decides otherwise. Ask before building
any of them.

| Diagram says | Standing decision | Why |
|---|---|---|
| Hands: **Shell / CLI** | 🚫 no generic shell exec | The fixed whitelist is the safety model (`AGENTS.md`). The sandbox exists for typed skills, not free-form commands |
| Ingest and Hands: **MCP** client | 🚫 server only; ask before adding a client | A client imports someone else's tools and bypasses the whitelist's reasoning |
| Learning: **Skill learning** | 🚫 skills are code a person writes | A self-written skill is generic exec by another route |
| Fusion: **Rust / Tokio** | stay on Python with GLib and asyncio | A rewrite buys nothing the measurements need; latency is model-bound (first word about 5 s on CPU, set by prefill) |
| Memory: **Qdrant, LanceDB, Chroma** | one SQLite file | A vector server is a new daemon to keep alive for a personal-scale index; SQLite with FTS5 (and vectors) covers it |
| Touch: **libinput / evdev** | 🚫 no raw input capture | Reading every keystroke is a keylogger whatever the intent; `idle` reads only the time since the last input |
| Safety: **AppArmor / udev rules** | OS layer, owned by `shani-settings` | Chronoa ships no system policy; minimal hardening, and things must work first |
| Ears and Eyes: **ONNX-based** models (Silero, openWakeWord, RapidOCR, Kokoro) | not until onnxruntime is packaged | It is not in Arch's official repos; both images build from them |
| Creation: **video, 3D, audio generation** | out of scope on this hardware | No GPU on the development machine; CPU generation takes minutes per item |
| Affect, Curiosity, Dream acting by themselves | everything unattended goes through consent and approvals | Unattended capability is the riskiest origin (`sandbox/profiles.py`); any new background behaviour is off by default and asks first |

## Part 3: the anatomy, and which of it you can see

Part 2 answers "which boxes are done". This part answers a different
question - **what is Chronoa, in the vocabulary of a body** - and the
answer is machine-readable, in `organism.py`, because a table in a
document cannot be kept true and one sitting next to the code can.

26 functions: **17 built, 9 part, 0 absent.**

Eight of them have a light in the window's organ strip; the rest have
no indicator, and the Inventory panel says so rather than implying they
are idle. The anatomy here is *standard physiology*, not a poetic gloss:
the useful property of "amygdala" is that it means fast evaluation of
danger, which is exactly what `guardrail.py` and `permissions.py` do.
Where a metaphor would be flattering and wrong, no organ is used at all -
there is no left pinky, because nothing in here has one.

| System | Organ | What it is for | State | Light | Code |
|---|---|---|---|---|---|
| Boundary | Skin | the outside edge: everything that leaves this machine, and the log of it. | built | `skin` | `egress.py`, `senses/network.py` |
| Boundary | Immune system | what defends against a hostile world - untrusted input, privilege, secrets. | built | - | `guardrail.py`, `redaction.py` |
| Boundary | Scar tissue | the wall between a skill and the machine it runs on. | built | - | `sandbox/executor.py`, `sandbox/profiles.py` |
| Senses | Ears | hearing: the microphone, speech recognition, wake phrase, and what is heard. | built | `ears` | `audio.py`, `stt.py` |
| Senses | Eyes | sight: the camera, the screen, OCR, faces, objects, and the vision model. | built | `eyes` | `screengrab.py`, `local_vision.py` |
| Senses | Nose | smell: the machine's own state - power, heat, disks, services, network. | built | `nose` | `senses/power.py`, `senses/thermalgrid.py` |
| Senses | Touch | contact: idle time, and what is plugged into it. | part | `nose` | `senses/idle.py`, `senses/devices.py` | no raw input events, on purpose - it watches devices, not you
| Senses | Tongue | taste: judging whether an action actually worked. | built | - | `verification.py`, `history_repair.py` |
| Senses | Vestibular sense | balance: what deserves attention now, out of everything that happened. | part | - | `tool_select.py`, `compression.py` | no salience or priority queue; selection is by tool relevance only
| Action | Mouth | speech: saying things, in the voice that was chosen. | built | `mouth` | `tts.py`, `voices.py` |
| Action | Hands | acting: changing the world - files, windows, services, settings, devices. | built | `hands` | `tools.py`, `skills/` |
| Action | Legs | getting about without being asked: rules that fire on their own. | built | - | `triggers/rules.py`, `triggers/event_rules.py` |
| Action | Diaphragm | breath: the rhythm of a day - awake, active, idle, asleep. | part | - | `daemon.py`, `senses/idle.py` | no awake/idle/sleep state machine; the daemon runs, nothing sleeps
| Core | Brain | thinking: the language model and the tool loop it drives. | built | `brain` | `assistant.py`, `local_llm.py` |
| Core | Prefrontal cortex | judgement before acting: permissions, plan mode, and the refusals. | built | - | `capabilities.py`, `planmode.py` |
| Core | Cerebellum | checking its own work: post-conditions, and repairing a broken turn. | built | - | `verification.py`, `history_repair.py` |
| Core | Amygdala | fast evaluation of danger, before the slow part of the brain is asked. | built | - | `guardrail.py`, `permissions.py` |
| Core | Reticular formation | attention: deciding what is worth interrupting for. | part | - | `triggers/events.py`, `cues.py` | no interrupt policy; everything notable is announced equally
| Regulation | Heart | pulse: is it alive, is the model up, is the clock sane. | part | - | `model_service.py`, `local_llm.py` | a liveness signal exists per subsystem; nothing consumes it yet
| Regulation | Thermoregulation | keeping itself at a working temperature, and noticing when it cannot. | part | `nose` | `senses/thermalgrid.py`, `senses/hwmon.py` | it reads its temperature; it does not act on it unasked
| Regulation | Hypothalamus | setpoints: the conditions it should notice on its own. | part | - | `triggers/rules.py`, `approvals.py` | no built-in presets; a rule has to be armed by the user
| Regulation | Circadian rhythm | rhythm: knowing what time it is and whether that matters now. | part | - | `senses/timebase.py`, `senses/idle.py` | the clock is read; nothing is scheduled *because* of the hour
| Memory and learning | Hippocampus | what it was told, and what it perceived. | built | `memory` | `senses/store.py`, `conversation_store.py` |
| Memory and learning | Sleep | consolidation: turning a long conversation into something usable. | built | - | `consolidation.py`, `compression.py` |
| Memory and learning | Synaptic plasticity | learning from how a call went - the outcome, whether it was approved, undone. | built | `brain` | `learning.py`, `tool_tracking.py`, `distill.py` - the outcome model is a **flag**, not a predictor: 83% top-1 against a 94% constant |
| Memory and learning | Endocrine system | slow signals about how things are going: pain and reward. | part | - | `tool_tracking.py`, `cues.py` | faults and pressures are reported; there is no reward signal at all

### The gaps, in one place

The two `absent` ones first, because they are the ones worth building:


And the nine that are part-built, which is a different problem: each one
works and stops short of something specific.

- **Touch** - no raw input events, on purpose - it watches devices, not you.
- **Vestibular sense** - no salience or priority queue; selection is by tool relevance only.
- **Diaphragm** - no awake/idle/sleep state machine; the daemon runs, nothing sleeps.
- **Reticular formation** - no interrupt policy; everything notable is announced equally.
- **Heart** - a liveness signal exists per subsystem; nothing consumes it yet.
- **Thermoregulation** - it reads its temperature; it does not act on it unasked.
- **Hypothalamus** - no built-in presets; a rule has to be armed by the user.
- **Circadian rhythm** - the clock is read; nothing is scheduled *because* of the hour.
- **Endocrine system** - faults and pressures are reported; there is no reward signal at all.

### How to keep this true

- ``tests/test_body_and_organs.py::TestTheInventoryIsTrue`` fails if
  an organ names a file that is not in the tree, invents a state, omits
  what is missing, points at a light that does not exist, or gets dropped
  when the panel groups it. It also fails if the inventory stops being
  larger than the strip, which is what would make it redundant.
- `body.ORGAN_NOUNS` and `body.ORGAN_SOURCE` are *derived* from the
  inventory, so the strip cannot describe itself in words that have
  stopped matching the code lighting it.
- The strip answers **"is it doing something now"**. The Inventory panel
  (sidebar, What Chronoa knows, Inventory) answers **"what is it"**. Two
  pages on purpose: they disagree whenever something is half-built, and
  that disagreement is the useful part.

