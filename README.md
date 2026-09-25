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
- **MCP server** — Model Context Protocol support so external tools and
  data sources can be plugged in.
- **GTK4 native UI** — integrates into the Shanios shell alongside
  shani-cassini.
- **Privacy-first** — everything runs locally; nothing leaves the machine.

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

### Optional

- `ollama` — local LLM runtime (must be installed separately; not a declared package dependency)
- `python-openwakeword` — wake-word detection
- `python-mcp` — Model Context Protocol support

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