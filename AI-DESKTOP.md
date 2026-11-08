# How an AI desktop should be

Research and design notes, and how ShaniOS measures against them.

## Thesis

The desktop's job is not to be intelligent. It is to make intelligence safe,
reversible, and optional. Every product that failed in this space failed on one
of those three words, not on model quality.

## The three shapes people keep building

1. **Assistant-in-a-box** — a chat window bolted onto a normal OS (Cortana,
   Copilot). It dies because the OS never needed a chat window; the assistant
   is a destination you have to remember to visit.
2. **Agent-on-top** — an agent drives the GUI for you (Rabbit's "large action
   model", computer-use demos). It dies on reliability, latency, and trust: an
   agent that fails 1 in 5 times is worse than a menu.
3. **Ambient OS** — the machine watches and speaks (Recall, Humane AI Pin). It
   dies on consent and context: right often enough to be creepy, wrong often
   enough to be useless.

The shape that survives is a fourth: **a normal desktop with a well-defined,
revocable, auditable assist layer.**

## What the prior art proved

| Product | The promise | What happened | The rule it proves |
|---|---|---|---|
| **Windows Recall** | Screenshot every few seconds, ask later | No opt-out at launch; a tool (`Total Recall`) pulled the "local, encrypted" data because it wasn't encrypted; delayed from June 2024, reworked to opt-in, encrypted, uninstallable. Signal later shipped screen-security DRM *because of it*. | Ambient capture is a permission, not a feature. "Local" ≠ "safe" — other processes, other users, and malware are local too. |
| **Copilot+ PC** | AI needs a 40 TOPS NPU, 16 GB RAM | A hardware spec most installed machines can't meet, sold as a brand rather than a capability. | Never gate the assistant on new hardware. Ship tiers, degrade gracefully. |
| **Humane AI Pin** | Replace the phone; $699 + $24/mo | Poor reviews from April 2024; HP bought the team and IP in Feb 2025 for $116M; the device business ended and the cloud service that ran it went away, leaving the hardware inert. | An assistant that is the *only* interface is a single point of failure. Cloud-tethered hardware is a subscription to your own device. |
| **Rabbit R1** | An agent that operates any app | A small set of integrations presented as a general agent. | Agents must be honest about their action space: enumerate what they can do, log what they did. |
| **Apple Intelligence** | On-device + private cloud | Staged delivery, the rebuilt Siri slipped past its promised window, Intel Macs excluded outright. | Staged delivery is fine; silent capability gating is not. |
| **Cortana / Clippy** | Proactive help | Interrupted, guessed wrong, couldn't be turned off. | Proactivity is a budget the user grants, not a personality trait. |

Horvitz's mixed-initiative work (1999) still describes the failure mode better
than any launch deck: weigh the *value* of helping against the *cost of
interrupting*, consider uncertainty explicitly, keep the user in control.

## The rules

### A. The AI is a layer, not the OS

1. **Everything works with the model deleted.** Files, windows, keyboard, apps,
   updates, login. Test it: remove `~/.local/share/shani-chronoa/models` and
   reboot — the desktop must be boring, not broken.
2. **Nothing AI is in the boot or update path.** The assistant is a user
   service, not an init dependency.
3. **Idle cost is zero.** No daemon holding a model in RAM, no screenshot loop,
   no home-directory crawl. Wake on intent, unload on idle.
4. **Deterministic core, probabilistic edge.** The model *proposes*; a
   deterministic layer *executes* after checking preconditions.

### B. Consent is per-capability, not per-app

5. **Separate "see" from "act."** Screen, clipboard, file, mic are four grants;
   acting on them is a fifth; destructive actions get their own confirmation.
6. **Show the sensor while it's on**, with a **per-app exclusion API** enforced
   by the compositor (Signal's screen-security lesson).
7. **Every byte that leaves is attributable** — egress log with host, bytes,
   and the user action that caused it; privacy mode verifiable with
   `tcpdump`, not just a label.

### C. Actions must be legible and reversible

8. **The action ledger.** Every step: what it read, what it plans, what it
   changed, with a diff and an undo. Pair it with filesystem snapshots.
9. **Untrusted text can never become a command.** Content from the browser,
   mail, or a PDF is *tainted*; a tool call whose arguments derive from tainted
   text requires explicit confirmation. MCP servers are attack surface too.
10. **Proactivity is a queue, not a popup.** Default silent; suggestions
    accumulate somewhere you visit, rate-limited, each with "why now" and a
    one-key dismiss.

### D. Multilingual and local are architecture, not features

11. **Language data is content, not code** — downloaded into the home directory,
    pinned and verified, no root, survives image updates.
12. **The UI works before the model does.** RTL, Indic shaping, fonts correct
    with no AI installed; never silently fall back to English.
13. **Voice is a modality, not the product.** Push-to-talk + wake word + text
    always available; every voice action reachable by keyboard.

## How ShaniOS measures

### The niri profile

Already the minimal-image decision made concrete: `shani-desktop-niri`,
`shani-chronoa`, `shani-piper-tts`, one `shani-piper-voice`, one
`shani-whisper-model`, `tesseract-data-eng`. Everything else is data the
wizard fetches into the home directory. That enforces "language as content" and
"idle cost" at the image layer. The one gap: no model *tiering* — one size for
a weak laptop and a workstation. `hardware_profile.py` / `modelfit.py` already
compute what fits; the profile ships only one tier.

### shani-shell

The substrate, not the assistant: Quickshell bar/launcher/panel over a bridge
socket. The panel is a *client* — kill Chronoa and the desktop still works.
`shani-shell.service` uses `Wants=`/`After=` for the bridge, not `Requires=`,
so the shell runs if the bridge dies. The missing piece belongs here: a
persistent mic/screen indicator is the shell's concern, reflecting
compositor/PipeWire truth rather than what Chronoa believes.

### Chronoa

- `permissions.py` — two-dimensional `(action, pattern)` scoped grants with
  `CANCEL ≠ DENY_ONCE`: finer-grained than any commercial assistant.
- `provenance.py` — per-turn nonce fence around untrusted content, deliberately
  excluded from the override registry ("no door" rather than a locked door).
- `approvals.py` — `notify-send --action --wait`; silence/expiry/no-server
  means **deny**.
- `undo_last_change.py` — pre-image ring, honest about its scope ("not a
  version control system", bounded, reports checkpoints remaining).
- `reflex.py` — a finite, test-asserted table of notifications that works with
  no model loaded and *cannot consume a consent key by construction*. The best
  idea in the codebase for this question.
- `egress.py` — destination + bytes, no payloads; privacy-mode breach is an
  **alarm**.
- `model_status.py`, `surfaces/listening.py`, `file_index.py` — distinguish
  "missing" from "broken" from "empty"; the most common lie in AI products.

## The gaps, and what closed them

1. **Provenance stopped at the prompt.** The fence made untrusted text visible
   to the model, but nothing propagated its origin to `tools.execute_tool` —
   a page could still talk the model into a `delete_file`. *Closed:*
   `provenance.fence_and_note` records the sources a turn has read (per turn,
   cleared when the person speaks); `tools._taint_refuses` routes destructive
   or overwriting calls through `approvals.ask()`. Only an explicit **Allow**
   proceeds. Reads, talking, and undo are never gated; unattended rules keep
   their own `ask_first` gate.
2. **No per-app screen exclusion.** *Closed:* `screen_guard.check()` in front of
   `capture_screen()` refuses the whole frame while an excluded app is on
   screen — a redaction rectangle computed from compositor geometry looks
   applied and is not. niri via `msg --json windows` filtered to the active
   workspace, X11 via `wmctrl`. Fail-closed when the desktop can't be asked;
   zero cost when nothing is configured. Settings > Privacy entry row.
3. **Undo covered files, not the machine.** *Closed:* entries carry a `kind`
   in the shared journal (`undo_ring.py`); `ChronoaConfig.set` journals
   before/after (restores pass `record=False`); `RuleStore._write` journals
   the armed-rule files; secret keys are refused by construction.
4. **Proactivity was notifications, not a queue.** *Closed:* `notices.py`
   (bounded 100, deduplicated, 7-day expiry), fed by `reflex.notify` and
   `approvals.ask`; the Notices surface lists them newest first with
   why-now, outcome, and a working Dismiss.

## From Omarchy (Nov 2026): what we took, and what we did not

Omarchy (DHH's Arch + Hyprland + Quickshell distro, 46.5k stars) bets the OS
should be drivable by an agent: one CLI exposing every menu function, a
one-click agent handoff on crashes, a unified theme, and a first-boot agent
pick. The toolkit choice (Quickshell) matches ours. What we took:

- **The agent CLI.** `shani` (`shani-shell/bin/shani`, installed as
  `/usr/bin/shani`): grouped commands over the shell IPC we already had
  (`qs -c shani ipc call shani <fn>`), `shani state --json` for live state,
  `shani doctor` forwarded, and a raw `shani call` for anything ungrouped.
  Every command maps 1:1 to an IPC function verified against `shell.qml`.
  The bet is Omarchy's; the wire is ours (local IPC, auditable, no cloud).
- **Theme-everything** — partially done: `shani system theme-reload` drives
  Matugen through the existing `themeReload` IPC. A one-command restyle of
  terminal + bar + wallpaper is the remaining work.
- **Agent on crash** — Chronoa has `crash_report` and the notices queue
  now records every proactive ask; the one-click "diagnose this crash"
  handoff is the next step, not yet built.
- **First-boot agent pick** — ours is the opposite by design: the wizard
  installs a *local* model (Qwen3.5-2B, preinstalled via `shani-llm-model`)
  instead of picking a cloud agent. Sarvam is the BYOK cloud option for
  Indian users. We did not take Omarchy's cloud-agent default.

Not taken: Hyprland (niri stays — input-method-v2, scrolling layout),
mutable pacman (immutable image stays), TUI-first app set (shell-native
QML surfaces instead).

## How to know it's working

Ship these as acceptance criteria, not aspirations:

- Idle RSS of the assistant: **0 MB** (not loaded), < 2% CPU wakeups.
- Egress bytes while idle: **0**; every non-zero byte maps to a log entry
  naming the user action.
- Boot, login, update, and file manager all work with models deleted.
- Every AI action has a non-AI path reachable by keyboard.
- Undo succeeds for every destructive action in the ledger — measured, not
  assumed.
- A destructive call made after reading untrusted content requires an
  explicit Allow — tested (`tests/test_taint_gate.py`).
- An excluded app on screen makes a capture refuse, not redact
  (`tests/test_screen_guard.py`).
- Time-to-first-response on the reference machine, per model tier.
- Language coverage: text/voice/OCR/model for each of the 15, with no silent
  English fallback.

## Caveats

Live web search was unavailable during this research (HTTP 402), so the
evidence is the documented prior art — Recall's announcement-to-rework
timeline, the Copilot+ hardware spec, Humane's acquisition and shutdown,
Rabbit's LAM criticism, Apple's staged rollout — plus Horvitz (1999) and this
codebase, not fresh 2026 market data. The principles don't depend on this
quarter's announcements; the hardware tiers might.
