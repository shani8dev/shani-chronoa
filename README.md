# shani-chronoa

Local-first voice/text AI assistant, integrated into the Shanios desktop.

Chronoa is a privacy-preserving AI assistant that runs entirely on your
machine. It listens for a wake-word, transcribes speech locally with
[whisper.cpp](https://github.com/ggerganov/whisper.cpp), routes the text
through an on-device LLM (Ollama) with tool-calling, and speaks the reply
with [Piper TTS](https://github.com/rhasspy/piper) — no cloud, no API
keys, no telemetry.

## Features

- **Wake-word detection** — optional hands-free wake-word activation via
  `python-openwakeword` (AUR); the default wake-word model is "hey_jarvis"
  (no dedicated "hey chronoa" model has been trained yet).
- **Local speech-to-text** — whisper.cpp; runs on CPU or CUDA.
- **On-device LLM** — Ollama integration with tool-calling, so Chronoa can
  act on your system (files, calendar, shell) instead of just chatting.
- **Local text-to-speech** — Piper voices, gender/age configurable.
- **Barge-in support** — interrupt a long reply mid-sentence.
- **Senses** — a perception layer beside `skills/`. Six senses deposit
  *percepts* the assistant can reason about: `memory` (the only durable
  one), `filesystem`, `web`, `ocr`, `vision` (screen, described by a
  separate local vision model) and `hearing`. Every sense has its own
  `<name>-sense-enabled` consent key and all default to **off** except
  memory, so nothing starts watching your screen or listening to your room
  until you say so.
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
- **MCP server** — Model Context Protocol support so external tools and
  data sources can be plugged in.
- **GTK4 native UI** — integrates into the Shanios shell alongside
  shani-cassini.
- **Privacy-first** — everything runs locally; nothing leaves the machine.

## Senses, percepts and consent

A **sense** perceives; a **skill** acts. `senses/` is inbound and deposits
**percepts**; `skills/` is outbound and is the whitelist the LLM can call.
Neither is a "tool" in the shell-exec sense — there is no generic command
escape hatch, and that is the point.

**Percepts are not conversation.** They are rebuilt fresh each turn and are
never appended to the chat history, because history is trimmed to
`MAX_HISTORY_MESSAGES` and a percept that got trimmed away would silently
stop informing the assistant.

**Two lifetimes.** `ttl_seconds is None` means durable and is exclusive to
`memory`; everything else is transient and expires. So the assistant can
forget a screenshot the moment it is no longer relevant, while a fact you
asked it to remember survives across sessions.

**Consent is per-sense and fail-closed.** `web` is additionally denied
while privacy mode is on, because it is the only sense that reaches off the
machine. Pointer and keyboard control sit behind `input-control-enabled`,
kept deliberately separate from `vision`: seeing a screen and controlling
it are different risks, and together they would let any prompt injection in
a web page reach the machine.

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

## What leaves this machine

Local-first is a claim, so it gets checked rather than asserted. Every outbound
request is appended to a local log with its destination, classified as *local*
(loopback, `.local`, `.internal`) or *remote*, and readable with one command:

```bash
shani-chronoa-sense egress
```

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

```
┌─────────────────────────────────────────────────┐
│                   shani-cassini                      │
│  (settings / chat / status chrome)               │
└────────────┬──────────────┬──────────────────────┘
             │              │
     ┌───────▼──────┐  ┌───▼──────────────────┐
     │  Chronoa UI  │  │  MCP server          │
     │  (GTK4)      │  │  (exposes tools to   │
     │              │  │   any MCP client)    │
     └──────┬───────┘  └───┬──────────────────┘
            │              │
     ┌──────▼──────────────▼───────┐
     │     chronoa daemon         │
     │  (state machine, routing)  │
     └──────┬──────┬──────┬───────┘
            │      │      │
     whisper.cpp  Ollama  Piper TTS
     (STT)        (LLM)   (TTS)
```

The daemon owns the conversation state machine. A request arrives as
audio → whisper.cpp transcribes → Ollama generates a response (possibly
calling tools) → Piper speaks the reply. The GTK4 UI is a thin client
over the daemon; the MCP server exposes the same tool surface to any
MCP-compatible client.

## Dependencies

See [`PKGBUILD`](PKGBUILD) for the full dependency list.

### Required

- `whisper.cpp` — speech-to-text
- `piper-tts` — text-to-speech
- `gtk4`, `python-gobject` — native UI
- `bubblewrap` — the skill sandbox. **Not optional in practice:** without it
  a skill that promises isolation has no way to be isolated, and Chronoa
  refuses to run such a command rather than pretending it was confined.
  Landlock (kernel 5.13+) is used as well, and is what confines on hosts
  where bubblewrap cannot create user namespaces.

### Optional

- `ollama` — local LLM runtime (must be installed separately; not a declared package dependency)
- `python-openwakeword` — wake-word detection
- `python-mcp` — Model Context Protocol support
- `tesseract` + `tesseract-data-eng` — the `ocr` sense. Both halves are
  required: the binary without language data cannot read anything, and
  `ocr` reports itself unavailable rather than returning empty text.
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
rest of Shanios:

```bash
# List all Chronoa keys
dconf dump /org/shanios/chronoa/

# Example: change the wake-word model
gsettings set org.shanios.chronoa wake-word-model "hey_jarvis"

# Example: select a Piper voice
gsettings set org.shanios.chronoa piper-voice "en_US-lessac-medium"
```

### Consent keys

Each sense is granted separately, so "may I remember things?" and "may I
look at your screen?" stay independent questions:

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

A missing key **denies** for every capture sense, so an older installed
schema cannot silently switch one on. The single exception is `memory`,
which stays enabled when its key is absent — it is local-only, and
remembering is the point of an assistant.

## Development

Chronoa is packaged for Arch (PKGBUILD) and Debian (DEBIAN) — there is
no pip-installable `setup.py`/`pyproject.toml` in this repo. To run from
source:

```bash
cd usr/lib/shani-chronoa
PYTHONPATH=. python3 -m shani_chronoa.app
```

Run the test suite:
```bash
pytest
```

## Verification

Per the Shanios contribution guide, changes are verified by running the
real thing, not by reading the diff:

```bash
# Syntax-check every module you touched
python3 -m py_compile usr/lib/shani-chronoa/shani_chronoa/*.py usr/lib/shani-chronoa/shani_chronoa/skills/*.py

# Run the test suite (tests/ covers config/GSettings, the async bridge,
# the gateway supervisor, IPC, packaging, and the privacy-ollama path)
pytest
```

`py_compile` catches syntax and import-time errors but is not sufficient
alone — the chat pipeline has shipped dead code that only a real run
surfaced. For anything touching the STT/LLM/TTS pipeline, run Chronoa for
real with a microphone and speakers rather than trusting the unit tests.

## License

See [`LICENSE`](LICENSE) for details.