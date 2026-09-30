# shani-chronoa

Local-first voice/text AI assistant, integrated into the Shanios desktop.

Chronoa listens for a wake-word, transcribes speech locally with
[whisper.cpp](https://github.com/ggerganov/whisper.cpp), routes the text
through an on-device LLM (Ollama) with tool-calling, and speaks the reply
with [Piper TTS](https://github.com/rhasspy/piper). It runs as a single GTK4
process — no API keys, no telemetry, and no daemon behind it. Nothing leaves
the machine unless you switch it twice on purpose.

## Features

- **Wake-word detection** — optional hands-free wake-word activation via
  `python-openwakeword` (AUR); the default wake-word model is "hey_jarvis"
  (no dedicated "hey chronoa" model has been trained yet).
- **Local speech-to-text** — whisper.cpp; runs on CPU or CUDA.
- **On-device LLM** — Ollama integration with tool-calling, so Chronoa can
  act on your machine rather than just chatting about it. **80 callable
  skills**, every one a named, schema-typed module under `skills/`. There is
  no generic shell-exec tool and there is no calendar skill; the whitelist is
  the design, not a starting point.
- **Local text-to-speech** — Piper voices, gender/age configurable.
- **Barge-in support** — interrupt a long reply mid-sentence.
- **Senses** — a perception layer beside `skills/`. **44 senses** deposit
  *percepts* the assistant can reason about: **24 default on, 20 default
  off**. The split is deliberate and machine-readable, see
  [the table below](#senses-percepts-and-consent).
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
| `git` | machine-state | off |
| `hwmon` | machine-state | off |
| `idle` | machine-state | off |
| `listeners` | machine-state | off |
| `modelfit` | machine-state | off |
| `printing` | machine-state | off |
| `privilege` | machine-state | off |
| `rfsense` | machine-state | off |
| `sessions` | machine-state | off |
| `stale` | machine-state | off |
| `thermalgrid` | machine-state | off |
| `filesystem` | text-file | off |
| `hearing` | utterance | off |
| `ocr` | image_text | off |
| `vision` | image_description | off |
| `web` | page | off |

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
**100 keys**: 44 `<sense>-sense-enabled`, 5 more for event triggers that are
not senses, and the rest being non-consent settings. **Five retired sense
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
**6 event types** — `containerrun`, `expiry`, `failure`, `fswatch`, `git`,
`unithealth` — each with its own consent key. Both are built by
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

## Architecture

There is no daemon. `app.py` defines `ChronoaApplication(Gtk.Application)`,
so the UI, the assistant and its tool loop, and the senses scheduler are all
one process with one main loop. Anything that needs another process is an
external engine, and each of those is optional:

┌──────────────────────────────────────────────────────────┐
│  shani-chronoa — one GTK4 process, one main loop         │
├──────────────────────────────────────────────────────────┤
│                                                          │
│    GTK4 UI   ·   assistant + tool loop  ·   senses       │
│    settings  ·   consent checks         scheduler        │
│    transcript ·  tool dispatch           percepts        │
│                                                          │
└───────────────────────┬──────────────────────────────────┘
                          │ subprocess IPC — every skill is a child
                          │ process, so a bad one cannot take the
                          │ assistant down with it
       ┌──────────────────┬──────────────────┐
                          ▼                  ▼
           whisper.cpp          Ollama            Piper TTS
              (STT)              (LLM)              (TTS)
```

A turn is audio → whisper.cpp transcribes → Ollama generates a response
(possibly calling skills) → Piper speaks the reply. The MCP server is a
*second entry point to the same skill set*, started on demand by
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

> `[NEEDS VERIFYING: Piper TTS has no declared Arch dependency anywhere — not in the in-repo PKGBUILD, not in the shipping shani-pkgbuilds/shani-chronoa/PKGBUILD. Is that intentional (Piper is not packaged for Arch, so the Arch build is meant to run espeak-ng/rhvoice), or an oversight? The `piper-voice` gsetting exists regardless, so the choice of voice is only meaningful if a Piper binary exists.]`

- `ollama` — local LLM runtime, optdepends on Arch and undeclared on the
  Debian side, so it is a separate install either way. Default model is
  Qwen3: `qwen3:4b` on capable hardware, `qwen3:1.7b` on the lowest tier.
  `config.model` and `config.whisper_model` both default to `""`, which means
  auto-detect rather than pinning a name.
- `python-openwakeword` (plus `python-numpy`) — wake-word detection
- `python-mcp` — needed only to run `shani-chronoa-mcp`; the MCP server is an
  optional second entry point, not part of the assistant itself
- a local Ollama **vision** model (for example `qwen3-vl:2b`) — the
  `vision` sense. Chosen independently of the text model, because a text
  model has no vision tower; see `chronoa-config set vision-model`.

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

# Example: change the wake-word model
gsettings set org.shani.chronoa wake-word-model "hey_jarvis"

# Example: select a Piper voice
gsettings set org.shani.chronoa piper-voice "en_US-lessac-medium"
```

Both key names above (`wake-word-model`, `piper-voice`) exist in the schema,
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

Current state: **2968 passed, 8 skipped**, and no `.pyc` in the payload.

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