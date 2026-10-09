# Chronoa demos

Short recordings of Chronoa doing real work, and the scripts that made them.
Every video was recorded on a **private, headless GNOME Shell**, not on
anybody's desktop, with a throwaway settings profile. The windows record
themselves, and the frames are joined at their real timing, so the videos play
at the speed things happened.

| Video | What it shows | Who decided the steps |
|---|---|---|
| [`videos/real-app-trip-booking-kilo.mp4`](videos/real-app-trip-booking-kilo.mp4) (4.5 min) | The **real Chronoa app**. One typed request: book the cheapest Boston → London flight on blazedemo.com, then check the weather, convert the price to rupees, write an itinerary and set a check-in reminder. The chat window is on the left and the in-app browser on the right. 27 tool calls end with the booking confirmation and a summary. | **The model.** Kilo Gateway's free model (`kilo-auto/free`), set through Settings → "Your own model server". |
| [`videos/scripted-trip-plan.mp4`](videos/scripted-trip-plan.mp4) (1 min) | The same trip, with each skill call and its result listed on the left. It ends with the generated itinerary open in the browser. | A script (no model). It shows the skills, not the reasoning. |
| [`videos/scripted-flight-booking.mp4`](videos/scripted-flight-booking.mp4) (50 s) | Just the browser: the "Chronoa" cursor, the ring around each element, the activity strip, and typing as it happens. | A script (no model). |

**What to know before showing these:**

- **blazedemo.com is a practice site for browser automation.** Nothing is paid
  or booked. It always "charges" 555 USD and shows a masked test card number.
  The card number typed in the real-app run is `4111 1111 1111 1111`, the
  standard fake Visa test number. The model chose to enter it.
- **Before payment details went in, Chronoa asked.** Typing into a card field,
  or pressing a Pay or Purchase button, puts a question to the person. In the
  recording, the harness pressed "Allow" after 3 seconds, standing in for the
  person; `demo.log` records each time it did.
- **In the real-app run, the request was typed into the chat box and sent with
  its Send button through GTK, not a physical keyboard.** Everything after that
  was the model's choice through Chronoa's normal path: tool selection,
  consent gates, the sandbox, and the network policy on every page the model
  opened.
- **These three videos have no audio.** Their requests were typed. The voice
  demo below is spoken in both directions.
- **Free cloud models vary from run to run.** Re-recording the real-app demo
  gives a different sequence of calls, and sometimes a provider is overloaded
  and the chain moves on to the next one.

## The voice demo

`videos/voice-*.mp4` is the real app driven **by voice**: three spoken requests
go into a virtual microphone, and Chronoa's spoken replies are recorded from a
virtual speaker. Both voices are mixed into the video. Everything runs in the
private session: PipeWire with a `demo_mic` and a `demo_speaker`, and nothing
reaches the real microphone or speakers.

- **The person's voice is synthetic** (espeak-ng `+m3`). **Chronoa's is its
  default** (espeak-ng `+f3`, because no Piper voice is installed in that
  profile). Both are robotic; a Piper voice sounds far better.
- **Hearing is local** (whisper.cpp `base`), the same path as a real
  microphone: the app's own listen action, silence auto-stop, then
  transcription.
- **`verify.txt` scores both directions.**
  - **IN:** each request's word error rate, comparing what Chronoa transcribed
    with what was said.
  - **OUT:** how many of the words in Chronoa's replies are heard back when its
    recorded voice is transcribed.
- Wording matters with these voices. whisper `base` hears espeak's "at nine"
  as "time", so turn 2 says "at nine o'clock", which it hears exactly. When it
  was misheard, Chronoa asked aloud "What time tomorrow?". That is the right
  behaviour, but the harness does not answer questions, so the run tangled.

## Checking that voice works (not just that it runs)

`tests/test_voice_loop_live.py` runs the same private audio graph with no
window and no model, and scores Chronoa's own capture, speech-to-text and
text-to-speech code:

| check | what it proves | measured 2026-10-08 |
|---|---|---|
| `test_chronoa_hears_what_was_said` | a spoken sentence comes back as the right words | WER 0.13 |
| `test_silence_is_not_a_request` | nothing spoken does not become words (the control) | passes |
| `test_what_chronoa_says_is_audible_and_recognisable` | the reply is audible, and its key words come through | RMS 0.08, all key words |
| `test_a_neural_voice_is_understood_word_for_word` | WER ≤ 0.30 for Chronoa's voice | **skipped with espeak-ng** (0.35–0.47 measured, numbers lost); runs once Piper or Kokoro is installed |
| `test_espeak_settings_measured`, `test_voice_styles_measured` | which espeak variant, speed and style is easiest to understand | `+m3` at 160 wpm 0.27; default `+f3` 0.39; styles 0.33–0.42 |

It needs PipeWire, whisper-cli, espeak-ng and a whisper model
(`CHRONOA_TEST_WHISPER_MODEL`, or the shani-install-media test cache).

## Recording one again

```sh
python3 demos/record/record.py real-app        /tmp/demo-real   # ~5-10 min, needs the network
python3 demos/record/record.py voice           /tmp/demo-voice  # ~4-10 min, network + a whisper model
python3 demos/record/record.py scripted-trip   /tmp/demo-trip   # ~1 min
python3 demos/record/record.py scripted-flight /tmp/demo-flight # ~1 min
```

It needs `gnome-shell`, `gdbus`, `glib-compile-schemas`, `ffmpeg`, Pillow and
WebKitGTK 6 (`webkitgtk-6.0` on Arch, `gir1.2-webkit-6.0` on Debian/Ubuntu).
The output folder holds the video, every frame, and `demo.log` with each tool
call and question answered.
`demos/record/prompts/trip-booking.txt` is the request the real-app demo types.

How it works, and why: see `demos/record/record.py`'s docstring and the
"A real task end to end in the real app" section of `AGENTS.md`.
