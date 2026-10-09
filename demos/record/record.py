#!/usr/bin/env python3
"""Record a Chronoa demo video, on a private headless GNOME Shell.

    python3 demos/record/record.py real-app OUT_DIR [--prompt FILE]
    python3 demos/record/record.py scripted-flight OUT_DIR
    python3 demos/record/record.py scripted-trip OUT_DIR

Nothing appears on, or reads from, the desktop you run it on: the shell has its
own D-Bus, HOME, runtime dir and virtual monitor (the same `_Shell` that
`tests/test_windows_gnome_live.py` uses), and Chronoa runs with a throwaway
settings profile and no keyring.

The windows record themselves (`Gtk.WidgetPaintable` rendered to PNG ~6x a
second) and `compose.py` joins the frames at their real timestamps. GNOME's own
screencast was tried first and produced one frame for a minute of activity in
the headless shell. Needs gnome-shell, gdbus, ffmpeg, Pillow and WebKitGTK 6.

`real-app` is the real `ChronoaApplication`: the prompt is typed into its chat
box and sent with its Send button (through GTK, not a keyboard), and every step
after that is the model's own choice. It uses Kilo Gateway's free model through
the "your own model server" settings, so it needs the network and the result
varies run to run. When Chronoa asks a question ("Allow"/"Let it continue"),
this harness presses the answer after 3 seconds, standing in for the person;
the log says so each time.
"""

import argparse
import json
import os
import re
import importlib.util
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCENARIOS = {"real-app": "real_app.py", "voice": "voice_app.py",
             "scripted-flight": "scripted_flight.py", "scripted-trip": "scripted_trip.py"}


def _whisper_model() -> str:
    for c in (os.environ.get("CHRONOA_TEST_WHISPER_MODEL", ""),
              str(Path.home() / ".local/share/whisper/models/ggml-base-q5_1.bin"),
              str(REPO.parent / "shani-install-media/test-env/cache/whisper-models/ggml-base-q5_1.bin")):
        if c and os.path.isfile(c):
            return c
    return ""


def _say_as_person(text: str, wav: Path) -> None:
    """The person's voice: Piper Amy from the cache, so it is natural speech.

    Not Chronoa's own voice (Lessac), so the two can be told apart. With the
    robotic espeak-ng voice used before, whisper heard "at nine" as "time" and
    "blaze demo" as "BlazeBemo"; with Amy every line was heard as said. espeak-ng
    is the fallback when Piper is not in the cache.
    """
    cache = REPO / "cache" / "data-home"
    piper = cache / "shani-chronoa" / "piper" / "piper" / "piper"
    voice = cache / "piper" / "voices" / "en_US-amy-medium.onnx"
    if piper.exists() and voice.exists():
        subprocess.run([str(piper), "--model", str(voice), "--output_file", str(wav)],
                       input=text.encode(), check=True, capture_output=True)
    else:
        subprocess.run(["espeak-ng", "-v", "en-us+m3", "-s", "150", "-w", str(wav), text], check=True)


def _running_model_file() -> "Path | None":
    """The -m file of a llama-server already running as this user, resolved, or None."""
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if argv and argv[0].endswith(b"llama-server") and b"-m" in argv:
            i = argv.index(b"-m")
            if i + 1 < len(argv):
                return Path(argv[i + 1].decode()).resolve()
    return None


def _local_brain(shell, work, reuse: bool = False):
    """llama-server with the cached model, as Chronoa's systemd user unit would run it.

    Chronoa starts the local model through `systemctl --user`, and the private
    session has no systemd user instance ("Failed to connect to user scope
    bus"). The arguments come from `local_llm.server_args()` itself, read in the
    session's own environment, so they cannot drift from what the app uses. The
    port is the real one, shared with the desktop: if something already answers
    there, this refuses rather than run a model it would talk to by accident.
    """
    import urllib.request
    lib = REPO / "usr" / "lib" / "shani-chronoa"
    probe = subprocess.run(
        [sys.executable, "-c", "import json; from shani_chronoa import local_llm as l; "
         "print(json.dumps([l.server_binary(), l.server_args(), l.HOST, l.PORT]))"],
        env={**shell.env, "PYTHONPATH": str(lib)}, capture_output=True, text=True, check=True)
    binary, argv, host, port = json.loads(probe.stdout.strip().splitlines()[-1])
    url = f"http://{host}:{port}/health"
    try:
        urllib.request.urlopen(url, timeout=2)
        if reuse:
            # Reuse it only when it is provably the same model: byte-identical to
            # the cached GGUF this demo would have started. Anything else could be
            # a different model answering "as" Chronoa's local brain.
            import filecmp
            running = _running_model_file()
            cached = sorted((REPO / "cache" / "data-home" / "shani-chronoa" / "llm").glob("*.gguf"))
            if running and cached and filecmp.cmp(running, cached[0], shallow=False):
                print(f"reusing the running local model ({running.name}, identical to the cache)", flush=True)
                return None
            raise SystemExit(f"something answers on {url}, but its model ({running}) is not the cached one")
        raise SystemExit(f"something already answers on {url}; not starting a second model there "
                         "(--reuse-local-model uses it when it serves the same model file)")
    except OSError:
        pass
    proc = subprocess.Popen([binary, *argv], env=shell.env,
                            stdout=open(work / "llama-server.log", "w"), stderr=subprocess.STDOUT)
    end = time.monotonic() + 180
    while time.monotonic() < end:
        try:
            if urllib.request.urlopen(url, timeout=2).status == 200:
                return proc
        except OSError:
            time.sleep(1)
    proc.terminate()
    raise SystemExit("the local model did not come up within 3 minutes; see llama-server.log")


def _audio_graph(shell, work):
    """PipeWire inside the private session: a virtual mic and a virtual speaker."""
    procs = []
    for argv, settle in ((["pipewire"], 1.5), (["wireplumber"], 2.0),
                         (["pw-loopback", "-n", "demo-mic",
                           "--capture-props=media.class=Audio/Sink node.name=mic_feed",
                           "--playback-props=media.class=Audio/Source node.name=demo_mic"], 0.5),
                         (["pw-loopback", "-n", "demo-speaker",
                           "--capture-props=media.class=Audio/Sink node.name=demo_speaker",
                           "--playback-props=media.class=Audio/Source node.name=speaker_tap"], 1.5)):
        procs.append(subprocess.Popen(argv, env=shell.env, stdout=open(work / f"{argv[0]}-{len(procs)}.log", "w"),
                                      stderr=subprocess.STDOUT))
        time.sleep(settle)
    return procs


def _shell_class():
    spec = importlib.util.spec_from_file_location("live", REPO / "tests" / "test_windows_gnome_live.py")
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    return live._Shell


def main() -> int:
    # SIGTERM (e.g. from `timeout`) must still run the `finally` that stops the
    # private session; by default Python dies without it and left a whole
    # headless GNOME session running (2026-10-08).
    import signal as _signal
    _signal.signal(_signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("scenario", choices=sorted(SCENARIOS))
    ap.add_argument("out", type=Path)
    ap.add_argument("--prompt", type=Path, default=HERE / "prompts" / "trip-booking.txt")
    ap.add_argument("--turns", type=Path, default=HERE / "prompts" / "voice-turns.txt",
                    help="voice: one spoken request per line")
    ap.add_argument("--answers", type=Path, default=HERE / "prompts" / "voice-answers.txt",
                    help="voice: what the person says, in order, when Chronoa asks a question")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--reuse-local-model", action="store_true",
                    help="local: use a llama-server already running on the port when its model file is "
                         "byte-identical to the cached one, instead of refusing")
    ap.add_argument("--brain", choices=("cloud", "local"), default="cloud",
                    help="real-app/voice: the free cloud chain, or the local model from cache/data-home")
    args = ap.parse_args()

    out = args.out.resolve()
    work = out / "run"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    schemas = out / "schemas"
    schemas.mkdir(exist_ok=True)
    for xml in (REPO / "usr" / "share" / "glib-2.0" / "schemas").glob("*.gschema.xml"):
        shutil.copy(xml, schemas)
    subprocess.run(["glib-compile-schemas", str(schemas)], check=True)

    Shell = _shell_class()
    shell = Shell(work, monitor="1780x800")
    log = open(out / "demo.log", "w")

    def gd(*a):
        return subprocess.run(["gdbus", "call", "--session", *a], env=shell.env,
                              capture_output=True, text=True, timeout=30)

    audio, recorders, t0, scripts = [], [], None, []
    try:
        if args.scenario == "voice":
            model = _whisper_model()
            if not model:
                raise SystemExit("voice needs a whisper model (set CHRONOA_TEST_WHISPER_MODEL)")
            models = Path(shell.env["XDG_DATA_HOME"]) / "whisper" / "models"
            models.mkdir(parents=True, exist_ok=True)
            shutil.copy(model, models / "ggml-base-q5_1.bin")
            # Voices installed once into the repo's git-ignored cache (laid out like
            # a data home) are linked in: Piper speaks instead of espeak-ng, and
            # nothing is downloaded. Kokoro stays out - it is opt-in and, measured,
            # slower than real time on a laptop CPU.
            cache = REPO / "cache" / "data-home"
            for rel in ("piper", "shani-chronoa/piper") + (("shani-chronoa/llm",) if args.brain == "local" else ()):
                if (cache / rel).exists():
                    target = Path(shell.env["XDG_DATA_HOME"]) / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(cache / rel, target)
            audio = _audio_graph(shell, work)
            turns = out / "turns"
            shutil.rmtree(turns, ignore_errors=True)
            turns.mkdir()
            scripts = [l.strip() for l in args.turns.read_text().splitlines() if l.strip()]
            for n, line in enumerate(scripts):
                _say_as_person(line, turns / f"turn{n:02d}.wav")
            answers = [l.strip() for l in args.answers.read_text().splitlines() if l.strip()] \
                if args.answers and args.answers.exists() else []
            for n, line in enumerate(answers):
                _say_as_person(line, turns / f"answer{n:02d}.wav")
            t0 = time.time()
            for node, name in (("speaker_tap", "chronoa.wav"), ("demo_mic", "you.wav")):
                recorders.append(subprocess.Popen(
                    ["pw-record", "--target", node, "--rate", "48000", "--channels", "1", str(out / name)],
                    env=shell.env))
        if args.brain == "local":
            brain = _local_brain(shell, work, reuse=args.reuse_local_model)
            if brain is not None:
                audio.append(brain)
        shell.extension(True)  # Chronoa's own window control, used to lay the windows out
        gd("--dest", "org.gnome.Shell", "--object-path", "/org/gnome/Shell", "--method",
           "org.freedesktop.DBus.Properties.Set", "org.gnome.Shell", "OverviewActive", "<false>")
        env = {**shell.env, "WAYLAND_DISPLAY": "wl-0", "GDK_BACKEND": "wayland",
               "GSETTINGS_BACKEND": "keyfile", "GSETTINGS_SCHEMA_DIR": str(schemas),
               "SHANI_CHRONOA_KEYRING": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
               # The scenario scripts' own switches (CHRONOA_DEMO_*) pass through.
               **{k: v for k, v in os.environ.items() if k.startswith("CHRONOA_DEMO_")},
               "CHRONOA_DEMO_BRAIN": args.brain}
        go, done, frames = work / "go", work / "done", out / "frames"
        shutil.rmtree(frames, ignore_errors=True)
        argv = [sys.executable, str(HERE / SCENARIOS[args.scenario]), str(go), str(done), str(frames)]
        if args.scenario == "real-app":
            argv.append(str(args.prompt.resolve()))
        elif args.scenario == "voice":
            argv.append(str(out / "turns"))
        shell.apps["demo"] = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT)
        Shell._wait(lambda: bool(shell.windows()), 60, "no window appeared")
        time.sleep(4)
        go.touch()

        def arrange():
            # Side by side: a fully covered GTK window on Wayland stops being drawn.
            placed = set()
            while not done.exists():
                try:
                    for title, w in shell.windows().items():
                        if w["id"] in placed:
                            continue
                        x, wd = (0, 470) if title == "Shani Chronoa" else (480, 1290)
                        for verb, a in (("Move", (x, 32)), ("Resize", (wd, 760))):
                            gd("--dest", "org.shani.Chronoa.Windows", "--object-path",
                               "/org/shani/Chronoa/Windows", "--method",
                               f"org.shani.Chronoa.Windows.{verb}", w["id"], *map(str, a))
                        placed.add(w["id"])
                except Exception as exc:  # noqa: BLE001 - layout is cosmetic
                    print("arrange:", exc, file=sys.stderr)
                time.sleep(1)

        threading.Thread(target=arrange, daemon=True).start()
        Shell._wait(done.exists, args.timeout, "the demo did not finish")
    finally:
        for proc in recorders + list(reversed(audio)):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        session = sorted((Path(shell.env["XDG_DATA_HOME"]) / "shani-chronoa" / "sessions").glob("*.jsonl"))
        if session:
            shutil.copy(session[-1], out / "session.jsonl")
        shell.stop()
        log.close()

    sys.path.insert(0, str(HERE))
    import compose
    first_frame = compose.compose(out)
    video = out / f"{args.scenario}.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(out / "concat.txt")]
    if t0 is not None:
        # Both voices, started at t0; the video starts at its first frame.
        skip = max(0.0, first_frame - t0)
        cmd += ["-ss", f"{skip:.3f}", "-i", str(out / "chronoa.wav"),
                "-ss", f"{skip:.3f}", "-i", str(out / "you.wav"),
                "-filter_complex", "[1:a][2:a]amix=inputs=2:normalize=0[a]", "-map", "0:v", "-map", "[a]",
                "-c:a", "aac", "-b:a", "96k", "-shortest"]
    cmd += ["-vf", "fps=12,format=yuv420p", "-c:v", "libx264", "-crf", "23", "-movflags", "+faststart", str(video)]
    subprocess.run(cmd, check=True, cwd=out)
    if args.scenario == "voice":
        _verify_voice(out, scripts)
    print(video)
    return 0


def _words(text):
    small = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
             "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
    return [small.get(w, w) for w in re.findall(r"[a-z0-9']+", text.lower())]


def _wer(reference, heard):
    ref, hyp = _words(reference), _words(heard)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[len(hyp)] / max(1, len(ref))


def _verify_voice(out, scripts):
    """Score both directions and write `verify.txt`.

    In: each request Chronoa transcribed (its own user turns in the session)
    against the line that was spoken. Out: Chronoa's recorded voice, transcribed
    by whisper, against the replies it showed - recall of their words, since one
    recording holds every reply.
    """
    lines = []
    said = [json.loads(l) for l in (out / "session.jsonl").read_text().splitlines()
            if l.strip().startswith("{")] if (out / "session.jsonl").exists() else []
    heard = [m.get("content") or "" for m in said if m.get("role") == "user"]
    for n, script in enumerate(scripts):
        got = heard[n] if n < len(heard) else ""
        got = got.rsplit("\n---\n", 1)[-1]  # context folded into the turn, if any
        lines.append(f"IN  turn {n + 1}: WER {_wer(script, got):.2f}\n    said:  {script}\n    heard: {got}")
    replies = " ".join(m.get("content") or "" for m in said
                       if m.get("role") == "assistant" and not m.get("tool_calls"))
    spoken = subprocess.run(["whisper-cli", "-m", _whisper_model(), "-f", str(out / "chronoa.wav"), "-nt", "-np"],
                            capture_output=True, text=True).stdout.strip()
    reply_words = set(w for w in _words(replies) if len(w) > 3)
    caught = reply_words & set(_words(spoken))
    lines.append(f"OUT Chronoa's voice: {len(caught)}/{len(reply_words)} of the replies' words "
                 f"(>3 letters) heard back ({len(caught) / max(1, len(reply_words)):.0%})\n"
                 f"    heard: {spoken[:600]}")
    (out / "verify.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
