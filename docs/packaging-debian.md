# Debian packaging notes

`DEBIAN/control` must be plain deb822: `dpkg-deb` rejects `#` comment lines,
line continuations, and prose in parentheses (which it reads as a version
constraint). The reasoning that used to sit inside the file lives here.

## Build

```bash
mkdir -p build/deb && cp -r usr DEBIAN build/deb/
find build/deb -name __pycache__ -prune -exec rm -rf {} +
dpkg-deb --root-owner-group --build build/deb shani-chronoa.deb
sudo apt install ./shani-chronoa.deb
```

## Text to speech

The TTS chain is piper, rhvoice, espeak-ng, and kokoro only when the user asks for it
(kokoro-tts-enabled, default false - real-time factor 1.2). Kokoro, like Piper, is a
pinned download into the user's home from setup (sherpa-onnx bundles onnxruntime).
sox is the timbre pass tts.py applies to whichever engine produced the WAV: pitch,
tempo and rate. Optional like every other optional engine - without it the reply is
spoken unchanged and the reason is said rather than guessed at.

## Why each suggested package

- **soundtouch**
- **rubberband** - higher-quality pitch transposition for singing.py; without them a pitch shift falls back to sox
- **whisper.cpp** - voice input (speech recognition) and the wake phrase
- **piper-tts** - a better TTS voice; espeak-ng is used when absent
- **sox** - voice pitch plus the tempo and rate controls in Settings
- **python3-mcp**
- **translate-shell**
- **gir1.2-webkit-6.0** - the in-app browser and attaching a page to a conversation
- **python3-onnxruntime** - remove_background for any object, not only people
- **avahi-utils** - discover_hosts - devices that announce themselves by name over mDNS
- **libnss-mdns** - resolving .local names, which is what mDNS service discovery hands out
- **iproute2** - check_internet and neighbour_table - the route and the ARP cache
- **fwupd** - firmware_updates - which firmware this machine is running
