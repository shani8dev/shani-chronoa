# shani-chronoa

Local-first voice/text AI assistant, integrated into the Shanios desktop.

> **What can it actually do? → [`CAPABILITIES.md`](CAPABILITIES.md)** — all 205
> skills, grouped by what they are for, with each one's consent switch and
> whether the assistant asks before running it. Generated from the same table
> the Help window renders, so it cannot claim something the build does not have.

Chronoa listens for a wake-word, transcribes speech locally with
[whisper.cpp](https://github.com/ggerganov/whisper.cpp), routes the text through an on-device LLM — by default `llama.cpp`'s
`llama-server`, with Ollama optional — with tool-calling, and speaks the reply
with Piper TTS (or espeak-ng when no Piper voice is provisioned). It runs as a
single GTK4
process — no API keys, no telemetry, and no daemon behind it. Nothing leaves
the machine unless you switch it twice on purpose.

## Features

- **Wake phrase** — optional hands-free activation: say "hey chronoa" (or
  any phrase you set). whisper.cpp transcribes each short utterance locally,
  biased toward the phrase, and the phrase must begin what you said; the
  utterance is discarded. Needs only what speech input needs (`whisper-cpp`
  and a model) - no wake-word engine and nothing to train.
- **Local speech-to-text** — whisper.cpp; runs on CPU or CUDA.
- **On-device LLM** — tool-calling via llama.cpp `llama-server` by default
  (the `ollama` package is also supported), so Chronoa can act on your machine
  rather than just chatting about it. **205 callable
  skills**, every one a named, schema-typed module under `skills/`. There is
  no generic shell-exec tool; the whitelist is the design, not a starting point.
  **They are all listed in [`CAPABILITIES.md`](CAPABILITIES.md)**, grouped by
  what they are for, each with the consent switch it needs and whether the
  assistant asks before running it — 81 are consent-gated and 16 are
  destructive. That file is generated from `capabilities._GROUPS`, the same
  table the Help window renders, so it cannot drift from what the build does;
  `tests/check_capabilities_doc.py` fails if it does.
- **Local text-to-speech** — espeak-ng by default; a Piper voice is used
  verbatim when one is provisioned — the voice directory, down to the voice
  name, is configurable.
- **Barge-in support** — interrupt a long reply mid-sentence.
- **Senses** — a perception layer beside `skills/`. **49 senses** deposit
  *percepts* the assistant can reason about: **24 default on, 25 default
  off**. The split is deliberate and machine-readable, see
  [the table below](#senses-percepts-and-consent).

  Those two numbers are about the **49 senses that are modules today**, which is
  what the table lists. The schema holds **74** `<name>-sense-enabled` keys
  because 18 belong to the *trigger event* types (`screenlock`, `usbplug`,
  `schedule`, … — `triggers.EVENT_TYPES`) and 7 more are **retired** keys kept
  because a rename would silently revoke somebody's permission; they still grant
  their successor through `config._SENSE_CONSENT_ALIASES`. Only 18 of the 74
  default on, and those 18 are all senses. So "24 on / 25 off" and "24 on / 50
  off" are both true of different sets, which is worth stating once here rather
  than leaving a reader to work out which one a given number describes.
- **Actuators** — the outbound side, as ordinary whitelisted skills:
  `speak`, `notify`, `clipboard`, `screenshot`, plus pointer/click/type
  behind their own `input-control-enabled` key. There is deliberately no
  generic "run this shell command" tool; each action is a narrow, named,
  schema-typed skill.
- **Triggers** — arm a rule like *"when a hearing percept contains
  'doorbell', send a notification"*. A rule can only name a whitelisted
  skill with fixed arguments, consent is checked on both the sensing and
  the acting side, and every unattended action is logged with
  `origin="unattended"` so it is distinguishable from something you asked
  for.
- **MCP server** — Chronoa *exposes* its own skills as a Model Context
  Protocol server (`shani-chronoa-mcp`), so Claude Desktop, Claude Code or
  Cursor can call them. It is deliberately **not** an MCP *client*: consuming
  external servers would hand third parties a way to propose actions against
  a fixed whitelist, which conflicts with the whole safety rationale and
  needs its own trust story before it can be built. That is a property, not
  a gap.
- **GTK4 native UI** — integrates into the Shanios shell alongside
  shani-cassini.
- **Privacy-first** — everything runs locally by default. Nothing leaves the
  machine unless you turn privacy mode off *and* separately enable
  `cloud-fallback-enabled`; two switches, so it never activates from one.
- **Learning from outcomes** — every skill call is recorded with the verdict
  its post-condition produced, and two models are fitted from that record.
  Both are deliberately conservative about what they are allowed to claim, and
  both numbers below were measured on a real machine's own log rather than
  argued:
  - **A routing student, distilled from a teacher.** `distill.py` asks the
    configured model which skill answers a request, keeps only the answers a
    human already agreed with, and fits a small router on those. Against
    llama.cpp/Qwen3-0.6B on a booted ShaniOS slot: the teacher agreed with
    the human label on **47 of 54 requests (87%)**, and the student scored
    **62% on held-out requests against a 12% majority baseline**. The student
    can only *reorder and narrow* skills that were already on offer and
    checked the whitelist at call time; it cannot add one, and it cannot run
    one. Run it yourself with `tools/distill_run.py`.
  - **An outcome model that predicts what happened.** It is a flag, not a
    predictor: on 5-fold cross-validation over feature vectors it finds
    calls that will verify **33× more often than chance on recall and 5.9×
    on precision**, and finds **nothing at all** in failures. It will say so,
    and the report prints both lifts next to the accuracy, because a 94%
    accuracy on this data is what a model that learned nothing also scores.

  The refusals are part of the behaviour, not gaps in it. Distilling from the
  bundled `tools/eval_cases.json` **declines to produce a student** and says
  why: that file asks one request per skill, so any held-out request names a
  skill the training half never saw. The outcome model will not write a file
  unless it finds a verdict above chance on both recall *and* precision —
  flagging everything as the 3% class scores 33× on recall alone and is
  worthless.

## Senses, percepts and consent

A **sense** perceives; a **skill** acts. `senses/` is inbound and deposits
**percepts**; `skills/` is outbound and is the whitelist the LLM can call.
Neither is a "tool" in the shell-exec sense — there is no generic command
escape hatch, and that is the point.

**The default split is a rule, not a listing.** Machine-state facts about
*this* machine default on, because knowing your disks are full is not
surveillance. Anything that reads the *user's world* or another person's
presence defaults off: `sessions`, `git`, `web`, `capture`, `vision`,
`listeners`, `containers`, `stale`, and the rest. `filesystem` is off while
`memory` is on, and the `kind` column below is what separates them.

| Sense | Kind | Default |
|---|---|---|
| Sense | Kind | Default |
|---|---|---|
| `audio` | machine-state | on |
| `boots` | machine-state | on |
| `cgroup` | machine-state | on |
| `coredumps` | machine-state | on |
| `cpu` | machine-state | on |
| `devices` | machine-state | on |
| `display` | machine-state | on |
| `faults` | machine-state | on |
| `filesystems` | machine-state | on |
| `firewall` | machine-state | on |
| `gpu` | machine-state | on |
| `hardware` | machine-state | on |
| `kernel` | machine-state | on |
| `memory` | fact (durable) | on |
| `network` | machine-state | on |
| `power` | machine-state | on |
| `resources` | machine-state | on |
| `security` | machine-state | on |
| `services` | machine-state | on |
| `snapshots` | machine-state | on |
| `storage` | machine-state | on |
| `timebase` | machine-state | on |
| `updates` | machine-state | on |
| `usb` | machine-state | on |
| `accessibility` | machine-state | off |
| `bluetooth` | machine-state | off |
| `capture` | machine-state | off |
| `containers` | machine-state | off |
| `dnsresolvers` | machine-state | off |
| `filesystem` | text-file | off |
| `git` | machine-state | off |
| `heard-sound` | machine-state | off |
| `hearing` | utterance | off |
| `hwmon` | machine-state | off |
| `idle` | machine-state | off |
| `labnetworks` | machine-state | off |
| `listeners` | machine-state | off |
| `location` | machine-state | off |
| `modelfit` | machine-state | off |
| `ocr` | image_text | off |
| `printing` | machine-state | off |
| `privilege` | machine-state | off |
| `rfsense` | machine-state | off |
| `sessions` | machine-state | off |
| `stale` | machine-state | off |
| `thermalgrid` | machine-state | off |
| `vision` | image_description | off |
| `web` | page | off |
| `wirelesslink` | machine-state | off |

**The table is generated, and the numbers in the prose above are checked against it.** The 49 rows are `senses.discover_senses()`; the Default column is each `<name>-sense-enabled` key's own `default` in the compiled schema, not a reading of intent. A row here that disagrees with the schema is a bug in the documentation, which is why the two are compared rather than trusted.


`filesystem` (one named text file, confined to `$HOME`) and `filesystems`
(what is mounted, and real room per filesystem) are **not** duplicates. The
names differ by one letter, the settings labels are deliberately distinct
("Files and folders" against "Filesystems and room"), and neither may be
renamed: the consent key is derived from the name, so a rename silently makes
a sense permanently ungrantable.

**Percepts are not conversation.** They are rebuilt fresh each turn and are
never appended to the chat history, because history is trimmed to
`MAX_HISTORY_MESSAGES` (40) and a percept that got trimmed away would
silently stop informing the assistant. Consent is re-checked when a percept is
*sent*, not only when it was recorded, and revoking consent **withholds**
rather than deletes — the percept stays in the local store and reappears if
you grant the key again.

**Two lifetimes.** `ttl_seconds is None` means durable and is exclusive to
`memory`; everything else is transient and expires. So the assistant can
forget a screenshot the moment it is no longer relevant, while a fact you
asked it to remember survives across sessions. That durable tier carries more
than the string you asked for. A fact is a value object of `text`, `span`,
`key`, `source`, `confidence` and `valid_until`: `text` is what the model
reads back, `span` is your original wording (keyword search needs it), `key`
**supersedes** an earlier fact about the same subject, `valid_until` is an
absolute instant at which the claim itself stops being true and is
orthogonal to how long the record is kept, and `confidence` breaks ties
between two records that both match. Both tiers are **bounded** — a capped
in-memory deque in front of the on-disk file — so the memory sense has a size
you can state rather than one that grows with your session.

**Consent is per-sense and fail-closed.** A missing key **denies**, so an
older installed schema cannot silently switch a sense on. The schema carries
**100+ keys**: 74 `<sense>-sense-enabled` (49 live senses, 24 on and 25 off;
the rest are retired aliases kept honoured), the event triggers that are not
senses, and the rest being non-consent settings. **Retired sense
aliases are still honoured** — when a sense was merged away its old key keeps
granting the successor, because a rename that silently revokes a grant is a
permission revocation wearing a refactor's clothes.

`web` is additionally denied while privacy mode is on, because it is the only
sense that reaches off the machine. Pointer and keyboard control sit behind
`input-control-enabled`, kept deliberately separate from `vision`: seeing a
screen and controlling it are different risks, and together they would let any
prompt injection in a web page reach the machine.

Try the senses without the GUI:

```bash
# what is available, and what is currently permitted
shani-chronoa-sense list

# consent is explicit; ask before anything perceives
shani-chronoa-sense enable ocr
shani-chronoa-sense run ocr path=~/screenshot.png

# durable memory, readable from a later process
shani-chronoa-sense run memory operation=remember fact="I prefer flat white"
shani-chronoa-sense run memory operation=recall query=coffee

# what has actually left this machine, and what stayed on loopback
shani-chronoa-sense egress
```

## Triggers

There are two kinds, and they are gated the same way.

**Percept rules** match a deposited percept. **Event rules** fire on one of
**19 event types** — `audiodevice`, `btconnect`, `calendar`, `containerrun`,
`dbusprop`, `expiry`, `failure`, `fswatch`, `git`, `journalmatch`, `netstate`,
`phone`, `powerstate`, `schedule`, `screenlock`, `sleepwake`, `sound`,
`unithealth`, `usbplug` — each with its own `<type>-sense-enabled` consent key.
The names are deliberately different from the machine-state *senses* above, so
one switch is never shared between "the desktop is locked" and "something in
the user's world changed". Both kinds are built by
`triggers.build_rule`, which validates the rule's shape and refuses one that
names a skill outside the whitelist. **No event type is default-on**, and
arming either kind requires `trigger-control-enabled`, so nothing can watch
for something and then act on it in a machine you have not agreed to.

## What leaves this machine

Local-first is a claim, so it gets checked rather than asserted. Every outbound
request is appended to a local log with its destination, classified as *local*
(loopback, `.local`, `.internal`) or *remote*, and readable with one command:

```bash
shani-chronoa-sense egress
```

On a machine with no recorded traffic, that is the whole output:

```
no outbound requests recorded
(log: /home/you/.local/share/shani-chronoa/egress.jsonl)
```

Once something has been sent, it reads like this. **(Illustrative — this is
the shape, not this machine's current numbers.)**

```
5 request(s): 2 stayed local, 3 left this machine
bytes sent: 15439
remote destinations:
  api.llm7.io
  en.wikipedia.org
!! 1 request(s) left the machine while privacy mode was ON
```

The distinction is the whole point: to `httpx` a call to `127.0.0.1:11434` and
a call to a cloud LLM provider look identical, and that difference is what a
user of a local-first tool actually needs to see. Classification is
**conservative** — an unparseable or schemeless target counts as remote, so a
malformed URL can never be mistaken for a safe one.

Two further properties:

- **A remote call while privacy mode is on is an alarm, not a log line.** Privacy
  mode is Chronoa's documented local-only guarantee, so a request that breaks it
  is logged at `ERROR` and counted as a violation in the summary above.
- **The log holds metadata, never payloads.** Destination, method, status and
  byte count only, at mode `600`. An audit log that captured bodies would
  become the very leak it exists to prevent — `test_the_log_never_stores_a_payload`
  pins that.

A request that was sent and then failed still counts as having left the machine,
and is still recorded.

The README's architecture section, refreshed against the tree.

*Every number is read out of the code rather than typed, and
`tests/check_readme_numbers.py` re-derives all of them - so these cannot
rot the way the previous set did.*

## Architecture

There is no daemon. `app/` is a package whose `application.py` defines
`ChronoaApplication(Gtk.Application)`, so the UI, the assistant and its tool
loop, and the senses scheduler are all one process with one main loop.
Anything that needs another process is an external engine, and each of those is
optional:

```
┌──────────────────────────────────────────────────────────┐
│  shani-chronoa — one GTK4 process, one main loop         │
├──────────────────────────────────────────────────────────┤
│                                                          │
│    GTK4 UI   ·   assistant + tool loop  ·   senses       │
│    settings  ·   consent checks         scheduler        │
│    transcript ·  tool dispatch           percepts        │
│    distilled router (prior only) ·  outcome model (flag) │
│                                                          │
└───────────────────────┬──────────────────────────────────┘
                          │ subprocess IPC — every skill is a child
                          │ process, so a bad one cannot take the
                          │ assistant down with it
       ┌──────────────────┴──────────────────┐
                          ▼                  ▼
           whisper.cpp        llama-server / Ollama   Piper TTS
              (STT)              (LLM)                  (TTS)
```

A turn is audio → whisper.cpp transcribes → the local LLM generates a response
(possibly calling skills) → espeak-ng or Piper speaks the reply.

**The tool loop is what the small model depends on, and it is measured.** All
205 schemas sent with every request is ~36,000 tokens (~48,000 at
`local_llm.CHARS_PER_TOKEN`, the 3 the tree actually uses) — past the window of
every hardware tier, so the request is rejected before the model reads a word.
`tool_select` narrows the offer to what a request could plausibly use. Measured
on llama.cpp/Qwen3-1.7B over the 68-case `tools/eval_cases.json`:

| config | score |
|---|---|
| every schema | **0/68** — the request overflows the context |
| `tool_select` | **67/68**, 12.6 s mean |

The single miss is instructive rather than a bug: *"explain what a black hole is
in two sentences"* once failed because the harness read `two` as arithmetic and
offered `web_search`. A length instruction is not a sum.

Three layers exist because each was measured earning its place, and two of them
are deliberately *not* defaults:

- **`select` beats `select+recover`** on both accuracy and speed, so recovering
  a call a model wrote as text is a fallback, not the main path.
- **Shrinking the schemas further made it worse** (52/62 against 56/62), so
  `COMPACT_TOOLS` is off. The obvious guess was wrong and the measurement
  overrode it.
- **An arriving cloud turn is announced** in the transcript. Privacy mode
  existing is not the same as a person knowing their sentence left.

`llama-server` and Ollama are interchangeable behind one `chat_message()`
interface, and a cloud provider (Anthropic, OpenAI, Gemini, Groq, OpenRouter,
opencode-zen, and keyless gateways) is reachable behind the same interface
**only** when privacy mode is off *and* `cloud-fallback-enabled` is on. The MCP
server is a *second entry point to the same skill set*, started on demand by
`shani-chronoa-mcp`; it is not a service the running app depends on.

## Confinement

Every skill command goes through the sandbox executor, which takes `argv` and
inspects the resolved program rather than scanning a command string. That
ordering is load-bearing: `shlex.quote` wraps a program in single quotes but
does not strip `$`, a backtick or `\` out of the payload, so a blocklist that
matched on command *text* was defeated by argument data it never saw. On top
of it sit four layers:

- **Landlock (kernel 5.13+), fd-pinned.** This is the confinement that
  actually applies on hosts where bubblewrap cannot create user namespaces,
  and it is pinned to file descriptors rather than paths so a swapped symlink
  cannot redirect a rule to a different inode between the check and the open.
- **bubblewrap** is a declared dependency and does the filesystem isolation
  wherever user namespaces are available.
- **Per-origin profiles with enforced rlimits.** An unattended trigger action
  gets a stricter ceiling than a user-initiated one. No profile field can
  loosen a guard: the ceiling only tightens, no allowlist widens, and
  `RLIMIT_CPU`/`RLIMIT_AS` are applied with `setrlimit` rather than left as
  decorative config.
- **A seccomp filter, off by default** behind `sandbox-seccomp-enabled`,
  denying **28 syscalls** — the mount and new-mount API, `memfd_create`, the
  module and kexec calls, `bpf`, `perf_event_open`, `io_uring`, `ptrace`
  onto another process, `process_vm_readv`/`writev`, `pidfd_getfd`,
  `unshare`, `setns`, and `seccomp`/`prctl(PR_SET_SECCOMP)` so a command
  cannot widen or replace its own filter. It is off by default because a
  filter that denies one syscall too many breaks skills with no visible cause.
  With it on, a machine that cannot install the filter **refuses every
  command** rather than running it unfiltered.

An explicit `sh -c` stays reachable as a visible opt-in, and one containing
`$(`, a backtick or `${` is refused rather than guessed at.

## Channels

`shani-chronoa-bridge` carries messages from a chat channel to the assistant's
inbound gateway and prints the reply back. Two channels are implemented:
**Telegram** (Bot API long polling - the bridge dials out, so no public URL is
needed) and **WhatsApp** (a Cloud API webhook, the one adapter with a port,
looping back on `127.0.0.1` by default, every POST checked against
`X-Hub-Signature-256` before it is believed).

The bridge is a separate process with its own credentials
(`CHRONOA_TELEGRAM_TOKEN`, `CHRONOA_WHATSAPP_TOKEN`,
`CHRONOA_WHATSAPP_PHONE_NUMBER_ID`, `CHRONOA_WHATSAPP_VERIFY_TOKEN`,
`CHRONOA_WHATSAPP_APP_SECRET`); Chronoa never sees a token. A channel cannot
lower a gate: the text it submits goes through the window's own `_submit`, so
the same consent keys, whitelist and post-conditions apply, and each channel
is bounded (4,000 characters, 20 messages a minute) and ask-only unless it is
marked `:execute` in the `gateways` setting.

```
shani-chronoa-bridge telegram [--gateway NAME] [--once]
shani-chronoa-bridge whatsapp [--host 127.0.0.1] [--port 8765]
```

## Untrusted text

Everything the model reads or emits is treated as hostile, because most of it
arrived from somewhere the model does not control. Two passes run over the
messages on their way to a backend, and they are both transport savings, never
record that something was lost: `_history` and the transcript keep every byte.

- **Tool output is bounded, with spill-to-file.** `compression.compress()`
  elides an oversized tool result into a note that names the original size and
  writes the untruncated text under `<state dir>/tool-output/`, so an elision
  is recoverable. Every elision reports the size of the *original* result, so
  compressing a message twice cannot quietly under-report what it replaced.
  Past 8 MiB it stops spilling rather than trading a bounded context problem
  for an unbounded disk one, and degrades to "no path in the note" — which is
  still honest.
- **History is repaired before every request, and repair runs first.**
  `history_repair.clean_history()` runs before compression, and the order is
  not arbitrary: compression elides by position against the last
  `KEEP_RECENT_MESSAGES` (6), while a repair *adds* a message, so compressing
  first would charge a repair against a window it is about to invalidate.
  It sits in the assistant rather than in a backend because it is a property
  of the conversation, which means the cloud fallbacks get it too.
- **Approval is three stages, not a yes/no** — `permission`, `always`,
  `reject`. Each exists because the stage before it leaves something specific
  unsaid: what is about to happen, exactly what "always" would persist and for
  how long, and the fact that declining can carry a reason the model can act
  on. The reason travels beside the decision rather than inside it, and
  anything unrecognised is a rejection — including the empty answer that a
  dismissal, a timeout, or the absence of a presenter all produce. Escape
  takes the same path as pressing "no", and that mapping exists in exactly
  one place.
- **Secrets are redacted before any provider call.** Configured cloud keys are
  registered into the in-memory sanitiser at startup. The vault file itself
  stays absent from disk, because GSettings remains the single source of
  truth — a second copy would be a second thing to leak.

## Dependencies

See [`PKGBUILD`](PKGBUILD) for the full dependency list.

### Required

- `gtk4`, `python-gobject` — native UI
- `bubblewrap` — the skill sandbox, a hard `depends` in both manifests, and
  used wherever user namespaces are available. Without it a skill that
  promises isolation has no way to be isolated, and Chronoa refuses to run
  such a command rather than pretending it was confined; Landlock (kernel
  5.13+) is what confines on hosts where bubblewrap cannot create user
  namespaces.
- `tesseract` + `tesseract-data-eng` — the `ocr` sense, a hard `depends`
  on both sides. Both halves are required: the binary without language data
  cannot read anything, and `ocr` reports itself unavailable rather than
  returning empty text.

### Speech and language model

- **STT** — `whisper.cpp` on Debian (`DEBIAN/control` `Depends`), and
  `whisper-cpp` as an *optdepend* on Arch, absent from the in-repo
  `PKGBUILD` entirely.
- **TTS** — `piper-tts` on Debian. Declared nowhere on the Arch side, which
  ships `espeak-ng` as a hard `depends` and offers `rhvoice-language-english`
  as an optdepend instead.

> Piper TTS (like sherpa-onnx/kokoro) is a pinned download provisioned by the
> setup wizard, not a system package, so it is intentionally absent from both
> packaging manifests: `espeak-ng` (and `rhvoice-language-english` on Arch) is
> the always-present floor, and a chosen Piper voice layered on top of that
> floor. The `piper-voice` gsetting holds the one we reach for, once a voice
> has been provisioned; empty means "the default floor is enough".

- `llama-server` (via the `local_llm` user unit, port 8765) — the default
  local LLM runtime. `ollama` is also supported as an optdepend on Arch and
  undeclared on the Debian side. Default model is Qwen3: `qwen3:4b` on capable
  hardware, `qwen3:1.7b` on the lowest tier.
  `config.model` and `config.whisper_model` both default to `""`, which means
  auto-detect rather than pinning a name.
- `python-mcp` — needed only to run `shani-chronoa-mcp`; the MCP server is an
  optional second entry point, not part of the assistant itself
- a local **vision** model — a llama.cpp vision model with its projector,
  served by `local_vision` on `127.0.0.1:8767`. Chosen independently of the
  text model, because a text model has no vision tower; see
  `chronoa-config set vision-model`.

## Installation

Chronoa is packaged for both Arch (PKGBUILD) and Debian (DEBIAN)
within this repository.

### Arch

```bash
# Build and install from source
git clone https://github.com/shani8dev/shani-chronoa
cd shani-chronoa
makepkg -si
```

### Debian

```bash
# Build the .deb
dpkg-buildpackage -uc -us
sudo dpkg -i ../shani-chronoa_*.deb
```

## Configuration

Chronoa is configured through GSettings (dconf), consistent with the
rest of Shanios. The schema id is `org.shani.chronoa`:

```bash
# List all Chronoa keys
dconf dump /org/shani/chronoa/

# Example: change the wake phrase
gsettings set org.shani.chronoa wake-phrase "hey computer"

# Example: select a Piper voice
gsettings set org.shani.chronoa piper-voice "en_US-lessac-medium"
```

Both key names above (`wake-phrase`, `piper-voice`) exist in the schema,
and so do the three that let you override the models (`model`,
`whisper-model`, `vision-model`), all defaulting to `""`.

### Consent keys

Each sense is granted separately, so "may I remember things?" and "may I
look at your screen?" stay independent questions. The key is always
`<sense-name>-sense-enabled`:

| Key | Default | Grants |
|---|---|---|
| `memory-sense-enabled` | `true` | keeping durable notes across sessions |
| `vision-sense-enabled` | `false` | capturing and describing the screen |
| `hearing-sense-enabled` | `false` | turning an utterance into a percept |
| `ocr-sense-enabled` | `false` | reading text out of images |
| `filesystem-sense-enabled` | `false` | reading files you name, inside `$HOME` |
| `web-sense-enabled` | `false` | fetching pages (also needs privacy mode off) |
| `input-control-enabled` | `false` | pointer, clicks and typing |
| `vision-model` | `""` | pins the local vision model; empty auto-selects by hardware |

```bash
# the same keys, without a GUI
shani-chronoa-sense enable vision
shani-chronoa-sense disable web
shani-chronoa-sense list          # shows what is permitted and why not
```

## Development

Chronoa is packaged for Arch (PKGBUILD) and Debian (DEBIAN) — there is
no pip-installable `setup.py`/`pyproject.toml` in this repo. To run from
source:

```bash
cd usr/lib/shani-chronoa
PYTHONPATH=. python3 -m shani_chronoa.app
```

**Do not run bare `pytest`.** Without `XDG_STATE_HOME` set, a full run writes
fixture data into your real `~/.local/state` and `~/.local/share`, because
`skills/timer.py` resolves its store from `XDG_STATE_HOME` and falls back to
`~/.local/state`. Use temp dirs:

```bash
XDG_STATE_HOME=$(mktemp -d) XDG_DATA_HOME=$(mktemp -d) PYTHONDONTWRITEBYTECODE=1 pytest
```

`PYTHONDONTWRITEBYTECODE=1` is not optional either: a stray `.pyc` in the
package tree fails `test_no_pycache_in_packaged_payload` on the *next* run,
and it is the previous run that put it there. If a packaging test goes red,
check whether the bytecode is yours before assuming the repo is broken.

Current state: **4911 passed, 36 skipped, 11 failed** (2026-10-04). All 11 are
outside the learning and routing layers: four read a `PKGBUILD` path that lives
in `shani-pkgbuilds/` rather than this repo, two assert dependencies the
in-repo manifest is not the source of, one reads `solve_math.sp` after the
`sympy`→`symengine` swap, two are timer notifications, and two are environment
(`mtr` needs privileges the dev box lacks, and the GTK clipboard is empty in a
headless session). Numbers here are from a real run — if you change one, run it.

## Verifying the learning layers for real

The unit suite cannot answer "does it learn", so two things exist that can:

```bash
# Distil whatever model is configured and print what was learned.
# No model? It says so, and says why, rather than training from nothing.
XDG_STATE_HOME=$(mktemp -d) python3 tools/distill_run.py --fresh

# The real thing, on a booted ShaniOS slot against a real local model.
# Needs --repo-pkg=llama-cpp and the model cache bound at /mnt/llm-models;
# slot-tests/chronoa-distill.sh in shani-testbed prints the invocation.
```

The second one is the only place the distillation question can be answered,
because a stubbed teacher cannot. It also runs the *negative* control — the
one-request-per-skill corpus — and requires that corpus to be **refused**, so a
gate that can no longer say no shows up as a failure rather than as a pass.

For the outcome model, the numbers are printed rather than asserted:

```bash
PYTHONPATH=usr/lib/shani-chronoa python3 -c \
  "from shani_chronoa import learning; print(learning.render_outcome(learning.train_and_report()))"
```

It reports accuracy *and* each verdict's recall lift and precision lift against
its own base rate, because the majority class is 94% and a model that learned
nothing scores 94%.

## Verification

Per the Shanios contribution guide, changes are verified by running the
real thing, not by reading the diff:

```bash
# Syntax-check every module you touched
python3 -m py_compile usr/lib/shani-chronoa/shani_chronoa/*.py usr/lib/shani-chronoa/shani_chronoa/skills/*.py

# Run the test suite (tests/ covers config/GSettings, the sandbox argv
# policy, the senses registry and scheduler, the percept store, triggers,
# the egress privacy alarm, history repair, and packaging)
XDG_STATE_HOME=$(mktemp -d) XDG_DATA_HOME=$(mktemp -d) PYTHONDONTWRITEBYTECODE=1 pytest
```

`py_compile` catches syntax and import-time errors but is not sufficient
alone — the chat pipeline has shipped dead code that only a real run
surfaced. For anything touching the STT/LLM/TTS pipeline, run Chronoa for
real with a microphone and speakers rather than trusting the unit tests.

Constructing a GTK widget is not the same as it being legible: three separate
bugs in this UI (`hwmon` and `thermalgrid` shown to users as module names,
reply markdown rendered as literal `**38G**`, body text too close to the
background on a light theme) all passed a suite that built the widgets and
walked the tree. `render_ui.py` in the repo root renders the main, empty,
settings and help windows to a PNG — measure the pixels, don't trust a
description of them. The measurements behind those fixes are in
[`AGENTS.md`](AGENTS.md).

## License

[`LICENSE`](LICENSE) is present.