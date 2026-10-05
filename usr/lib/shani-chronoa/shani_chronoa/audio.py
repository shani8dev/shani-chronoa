"""Microphone capture and audio playback for voice input/output.

Shells out to PipeWire's pw-record/pw-play (falling back to ALSA's
arecord/aplay) rather than adding a GStreamer or PortAudio dependency -
these tools are already present on any PipeWire desktop and handle device
selection and format conversion for us.

Every capture and playback loop here runs on its own thread and hands its
result to a caller-supplied callback, so each loop can outlive the turn that
started it: `pw-record` blocks in `read()`, the callback is delivered after
the loop's own cleanup, and a `stop()` that cannot join its thread lets a
second one start alongside the first. Two integers per owner - a
*generation* bumped whenever a newer capture/playback supersedes an older
one, and a *stop epoch* bumped whenever a capture is asked to end - make
that structurally impossible rather than a matter of timing. A callback whose
generation is no longer current is dropped, and a frame that arrived across a
stop boundary is discarded, because the read it came from was already in
flight when the user asked to stop. See each loop for the specific race it
closes. The integer counters need no overflow guard: Python ints are
arbitrary precision, so the 64-bit wrap Codex has to defend against cannot
occur here.

Those two children are supervised through `child_supervisor.py`
(`_SUPERVISOR` below), because a subprocess nobody watches is the one failure
this module cannot report on its own. Scoped honestly: the recorder's capture
and the player's playback are tracked; `BargeInMonitor` spawns the same
`pw-record` and `wakeword.py` spawns it again, and neither is watched yet. The
barge-in path is deliberately untouched here, so the gap is stated rather than
half-closed.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from typing import Callable, Optional

from shani_chronoa.child_supervisor import ChildState, ChildSupervisor, HeartbeatStatus
from shani_chronoa.vad import SilenceDetector, calibrate_noise_floor, normalize_level, rms

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_CHANNELS = 1
_SAMPLE_WIDTH = 2
_FRAME_SECONDS = 0.08
_FRAME_BYTES = int(_SAMPLE_RATE * _FRAME_SECONDS) * _SAMPLE_WIDTH
_CALIBRATION_FRAMES = 4  # ~320ms of ambient audio sampled before listening for speech

# --------------------------------------------------------------------------------------
# Supervision of the children this module actually runs
#
# `pw-record` and `pw-play` are the only two subprocesses Chronoa keeps alive for
# minutes at a time, and until this was wired they were the only two processes in the
# app that nothing was watching. A capture whose child wedges is invisible from inside
# the read loop - that loop is blocked in `read()` waiting for the bytes that stopped
# coming, and it only tests its deadline *between* reads - so the turn never finalises,
# the orb stays "listening", and every later attempt to start one is refused as
# "already recording". The user's microphone is simply dead and the app cannot say so.
#
# Names, not pids, identify the children: `start_auto_stop` refuses to run two captures
# at once, so there is at most one capture and at most one playback, and a name is what
# a caller can ask about without having kept the `Popen` handle.
# --------------------------------------------------------------------------------------

_CAPTURE_CHILD = "microphone-capture"
_PLAYBACK_CHILD = "speaker-playback"

#: Set on a playback process this class deliberately signalled. See `play_file`'s
#: teardown for why the exit status alone cannot answer that question.
_SIGNALLED = "_chronoa_signalled"

#: How long a live capture may deliver no bytes before it is called hung.
#:
#: A capture streams silence as bytes exactly as it streams speech - the silence
#: detector judges *energy within* frames that have already arrived - so a quiet room
#: is not a stall and only a wedged child is. 2.0s is 25 frame times, far enough past
#: the 80ms frame that ordinary scheduling jitter cannot reach it, and far enough below
#: `max_seconds` (20s) that a wedge is reported long before the turn would have ended
#: on its own.
#:
#: HONEST LIMITATION, and it is the same one as everywhere else in this file: there is
#: no microphone and no PipeWire graph on the machine this was written on, so the number
#: was not measured against a real capture stream. What it is *not* is a guess about
#: audio - it is a bound on pipe delivery, which is the one thing here that is a
#: filesystem-independent property of `Popen.stdout`. Two unknowns remain, and both
#: need real hardware: whether a `pw-record` stream ever legitimately stops writing
#: while its client still holds it (a suspended PipeWire graph would do it, and this
#: cannot tell), and how long a real wedge takes to be noticed. A false positive costs
#: a warning and, at a turn boundary, one abandoned turn - never a truncated one, see
#: `_release_unresponsive_capture`.
_CAPTURE_STALL_SECONDS = 2.0

#: How often the watchdog re-reads the supervisor. It only exists to make a hang
#: observable at all, so it is fast enough that "the assistant went quiet" and "the
#: watchdog noticed" are the same moment from the user's point of view, and slow enough
#: to be invisible in a process that is otherwise idle.
_WATCHDOG_INTERVAL_SECONDS = 0.5

# One registry for the whole process, for the reason the names above are names.
_SUPERVISOR = ChildSupervisor()


def audio_status() -> list[HeartbeatStatus]:
    """Every audio child under supervision right now, healthy ones included.

    Returns the statuses rather than a boolean for the reason `check_heartbeats`
    documents: a caller has to be able to tell "not started yet" from "hung" from
    "gone", because only one of those three is worth waiting for.
    """
    return _SUPERVISOR.check_heartbeats()

# `pw-record`'s capture ring, pinned rather than inherited from whatever
# PipeWire version happens to be installed.
#
# Codex pins the same knob for the same reason, recording
# (`codex-rs/voice-host/src/devices.rs`): "ALSA allocates two periods. A 20 ms
# ring can be smaller than one PipeWire graph cycle (e.g. 2048 frames at
# 48 kHz), silently losing capture samples every cycle." It gives Linux a
# 100 ms ring and other platforms 10 ms. 100 ms is the right value for a Linux
# capture, and it is also the documented default of the `pw-record` this was
# written against - libpipewire 1.0.5, whose `--help` reads
# "--latency Set node latency (default 100ms)".
#
# HONESTY, because this still does not read as the bug fix it is not. An earlier
# version of this note said nothing here could be captured at all - "no microphone,
# no speaker and no PipeWire graph" - and that was false. What is actually true,
# each line measured on the dev box rather than reasoned about:
#
# - Two `Audio/Source` and four `Audio/Sink` nodes exist and a real `pw-record`
#   capture returns audio. 20s at 100ms latency: 638918 bytes = 99.83% of
#   realtime, 249 whole `_FRAME_BYTES` frames. At 20ms: 637894 = 99.67%, the
#   same 249 frames. No sample loss is visible at this length, at either value.
# - `pw-record --help` here still reads "(default 100ms)", so the pin currently
#   restates the default it was written to protect against: the value is
#   unproven, not wrong. And the direction of the risk, which an earlier version
#   of this note had backwards - a *smaller* ring means smaller buffers and MORE
#   xruns, not fewer.
# - Playback does reach the microphone, weakly and variably. A 440Hz tone through
#   `pw-play` raised the 440Hz component of a concurrent capture 3.5x on one run
#   and 8.2x on the next, against 2.4x at 800Hz in the same window, with the
#   component back at the ambient baseline once playback stopped. So the barge-in
#   acoustic path is demonstrable here after all - which is the reason
#   `barge-in-vad-enabled` is off by default, and a pure tone is not the speech
#   that would decide whether that default is right.
#
# Still unestablished: whether 100ms is optimal under load, and BargeInMonitor's
# real false-positive rate against actual TTS output.
_CAPTURE_LATENCY = "100ms"


def _stream_capture_cmd(backend: str, target: Optional[str] = None) -> list:
    """Raw s16le PCM to stdout - confirmed live (see wakeword.py's docstring)
    that both backends stream from byte zero with no WAV header when piped.

    `target` is a PipeWire node name, applied as `--target` so a chosen
    microphone is actually the one being recorded. ALSA's `arecord` takes a
    different kind of identifier (`-D hw:0,0`), which a PipeWire node name is
    not, so the setting is only honoured on the pw-* backends and the caller
    is expected to have said so rather than have it silently do nothing.

    `--latency` is passed only to `pw-record`, which is the backend that
    exposes it; `arecord`'s equivalent is its period/buffer-size pair, which
    this does not attempt to second-guess. See `_CAPTURE_LATENCY`.
    """
    if backend == "pw-record":
        cmd = [
            "pw-record",
            "--rate", str(_SAMPLE_RATE),
            "--channels", str(_CHANNELS),
            "--format", "s16",
            "--latency", _CAPTURE_LATENCY,
        ]
        if target:
            cmd += ["--target", target]
        cmd.append("-")
        return cmd
    return ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(_SAMPLE_RATE), "-c", str(_CHANNELS), "-"]


def _light_organ(organ: str, what: str, detail: str = ""):
    """Tell the body an organ started. Never raises into the caller.

    Audio paths run inside signal-driven reads and barge-in handlers; an
    indicator that can take the microphone down is the opposite of the point.
    """
    try:
        from shani_chronoa import body

        return body.body.use(organ, what, detail)
    except Exception:                                   # noqa: BLE001
        return None


def _put_organ(activity) -> None:
    try:
        from shani_chronoa import body

        body.body.done(activity)
    except Exception:                                   # noqa: BLE001
        pass


class AudioRecorder:
    """Microphone recorder producing 16kHz mono WAV files.

    Auto-stops on silence (`start_auto_stop`) rather than requiring an
    explicit "stop recording" call - see that method's docstring.
    """

    # Class-level defaults so an instance built with `__new__` (as the barge-in
    # gate's tests do for `BargeInMonitor`) still has working counters. Each
    # instance shadows them on first `+=`, so two recorders never share one.
    _generation: int = 0
    _stop_epoch: int = 0

    def __init__(self, target: Optional[str] = None) -> None:
        self._proc: Optional[subprocess.Popen] = None
        # The body activity for the capture in progress, so the ears light can
        # be turned off at the stop rather than at a deadline. Initialised here
        # because a stop that arrives before the first capture must not raise.
        self._organ = None
        self._backend = self._detect_backend()
        self._auto_stop_thread: Optional[threading.Thread] = None
        self._auto_stop_cancel = threading.Event()
        self._target = target or None
        self._generation = 0
        self._stop_epoch = 0
        self._watchdog_stop: Optional[threading.Event] = None
        self._watchdog: Optional[threading.Thread] = None
        self._capture_failure: Optional[HeartbeatStatus] = None

    def set_target(self, target: Optional[str]) -> None:
        """Point capture at a specific device, or back to the default."""
        self._target = target or None

    def _detect_backend(self) -> Optional[str]:
        if shutil.which("pw-record"):
            return "pw-record"
        if shutil.which("arecord"):
            return "arecord"
        return None

    def is_available(self) -> bool:
        """Check if a recording backend is installed."""
        return self._backend is not None

    def start_auto_stop(
        self,
        on_done: Callable[[Optional[str]], None],
        max_seconds: float = 20.0,
        silence_seconds: float = 1.2,
        on_level: Optional[Callable[[float], None]] = None,
    ) -> bool:
        """Start recording; auto-stops once the user stops talking (or times out).

        Borrowed from how `aside` and `loquivox` avoid a second explicit
        "stop listening" action - the whole point of wake-word activation
        is hands-free, so requiring a manual click to end the turn defeats
        that. Runs on a background thread; `on_done(path_or_None)` fires
        from that thread, not the GTK main thread - callers must marshal
        back via `GLib.idle_add`, same convention as `WakeWordListener`.

        Starting a capture bumps the generation, which is what makes the
        `on_done` below a *this-capture* callback rather than a *some-capture*
        one. Without it a single turn can be finalised twice: this loop
        clears `_proc`/`_auto_stop_thread` in its own `finally` but delivers
        `on_done` afterwards, and anything that starts a new capture in that
        window - a wake word, a barge-in interrupt, the orb - is accepted,
        because the recorder already looks idle. The superseded turn's WAV
        then arrives after the new one, and the caller finalises over the top
        of a turn that is still recording. See `_auto_stop_loop`.

        A capture that has stopped responding is released first
        (`_release_unresponsive_capture`), because the alternative is the bug this
        supervision exists for: the recorder looks busy forever and the user is told
        "Failed to start audio recording" on every press, which is a plausible-looking
        wrong answer to "my microphone is dead".
        """
        released = self._release_unresponsive_capture()
        if self._proc is not None or self._auto_stop_thread is not None or not self._backend:
            return False
        try:
            proc = subprocess.Popen(
                _stream_capture_cmd(self._backend, self._target),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error(f"Failed to start auto-stop recording ({self._backend}): {e}")
            return False

        self._proc = proc
        self._generation += 1
        self._auto_stop_cancel.clear()
        # The ears light here, at the spawn, because that is the moment the
        # machine's microphone starts being read by *something* - which is the
        # only moment that matters to the person in the room. Cleared by the
        # body's deadline rather than only on a tidy stop, so a `pw-record` that
        # dies on its own does not leave the light on.
        self._organ = _light_organ("ears", "listening", f"{self._backend} capture")
        self._watch_capture(proc)
        self._capture_failure = released
        self._auto_stop_thread = threading.Thread(
            target=self._auto_stop_loop,
            args=(proc, on_done, max_seconds, silence_seconds, on_level, self._generation),
            daemon=True,
        )
        self._auto_stop_thread.start()
        return True

    def capture_status(self) -> Optional[HeartbeatStatus]:
        """The current capture's supervised state, or None if none is tracked.

        `None` and every state in it are different answers, and a caller that cannot
        tell them apart will wait forever on a child that has already exited.
        """
        for status in _SUPERVISOR.check_heartbeats():
            if status.name == _CAPTURE_CHILD:
                return status
        return None

    def capture_failure(self) -> Optional[HeartbeatStatus]:
        """The most recent fault seen on a capture, kept after that capture is over.

        Retained rather than cleared, because the fault is discovered at a moment when
        nothing is looking - the release that lets a new turn start, or a watchdog tick
        on a capture that is still wedged. Its `pid` says which capture it was, so a
        caller can tell a fault about this turn from a fault about the last one. A new
        capture that needed no release resets it, so "did starting this turn have to
        throw one away, and why" has an answer.
        """
        return self._capture_failure

    def _watch_capture(self, proc: subprocess.Popen) -> None:
        """Register a freshly spawned capture child and start watching it for a hang.

        The watchdog cannot live in `_auto_stop_loop`, because the failure it exists to
        catch is that loop being blocked in `read()`: it is waiting for the very bytes
        that stopped arriving, so it cannot notice them stopping. A second thread is
        the only observer that can.
        """
        if self._watchdog_stop is not None:
            self._watchdog_stop.set()
        stop = threading.Event()
        self._watchdog_stop = stop
        _SUPERVISOR.track_child(
            _CAPTURE_CHILD,
            proc.poll,
            # `Popen.pid` is always set, so this getattr only ever yields None for a
            # stand-in child. It is spelled out because the pid is reporting detail
            # only: nothing below branches on one being present, and
            # `test_child_supervisor_live.py` pins that a pid-less child is still
            # supervised correctly, so this can never become load-bearing by accident.
            pid=getattr(proc, "pid", None),
            stall_after=_CAPTURE_STALL_SECONDS,
        )
        self._watchdog = threading.Thread(
            target=self._watchdog_loop,
            args=(stop,),
            daemon=True,
            name="chronoa-capture-watchdog",
        )
        self._watchdog.start()

    def _watchdog_loop(self, stop: threading.Event) -> None:
        """Report an unresponsive capture once per transition into a bad state.

        Once per transition, not once per tick: a wedged capture that nobody recovers
        would otherwise log two lines a second for as long as the app is open, which
        is how a real fault gets scrolled past unread.

        Takes no handle on the child it is watching, so a watchdog left over from a
        wedged capture cannot report on the capture that replaced it - every capture
        goes through `_watch_capture`, which stops the previous watchdog on its way
        past.
        """
        reported: Optional[ChildState] = None
        while not stop.wait(_WATCHDOG_INTERVAL_SECONDS):
            status = self.capture_status()
            if status is None:
                return
            if status.state is reported:
                continue
            reported = status.state
            if status.state in (ChildState.DISCONNECTED, ChildState.EXITED):
                logger.error("Microphone capture is not delivering audio: %s", status.detail)
                self._capture_failure = status

    def _release_unresponsive_capture(self) -> Optional[HeartbeatStatus]:
        """Free a capture whose child is gone or hung, so a new turn can start.

        Returns the fault that forced the release, or None. Called only from
        `start_auto_stop`, i.e. only when a *new* turn is being asked for, which is what
        makes the following two properties true rather than merely intended:

        - It acts only on a child that is unresponsive - `EXITED`, or `DISCONNECTED`
          for having delivered nothing at all across the whole stall window. A capture
          that is still delivering bytes is left strictly alone, so this cannot cut off
          a live utterance: there is no audio in the pipe to cut it off from.
        - The generation is bumped, so the abandoned turn finalises as *nothing* rather
          than racing the new turn with an `on_done(None)`. That callback is the
          caller's cue that the turn is over, and the turn being abandoned is not the
          same event as the turn that is starting.

        It never restarts anything. Restarting a capture would mean cutting the user
        off mid-sentence and handing them a transcript of the remainder, which is worse
        than an honest error; the turn is dropped and the reason is reported instead.
        """
        proc = self._proc
        if proc is None:
            return None
        status = self.capture_status()
        if status is None or status.state not in (ChildState.DISCONNECTED, ChildState.EXITED):
            return None

        logger.error("Releasing an unresponsive microphone capture: %s", status.detail)
        self._capture_failure = status
        self._generation += 1
        if self._watchdog_stop is not None:
            self._watchdog_stop.set()
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        thread, self._auto_stop_thread = self._auto_stop_thread, None
        if thread is not None:
            thread.join(timeout=2)
        self._proc = None
        # The microphone has stopped, so the ears light goes off *now*. Relying
        # on the 30-second deadline instead would say "listening" for half a
        # minute after the room went quiet, which is the one thing an indicator
        # in this position must never do - it would train a person to distrust
        # the light that exists precisely so they can trust it.
        _put_organ(self._organ)
        self._organ = None
        return status

    def cancel_auto_stop(self) -> None:
        """Signal an in-progress auto-stop capture to end early (e.g. the user pressed the orb).

        Deliberately does *not* bump the generation: cancelling ends the
        capture but still finalises the turn, so the audio said up to the
        press is exactly what `on_done` should deliver. What it does bump is
        the stop epoch, so the loop can tell a frame that was already in
        flight when the press landed from one that was not - see below.
        """
        self._stop_epoch += 1
        self._auto_stop_cancel.set()

    def auto_stop_reason(self) -> str:
        """Why the last auto-stopped recording ended.

        `"limit"` means the clock ran out mid-speech - the transcript is
        incomplete, and anything that shows it has to say so. `"silence"` means the
        person finished. Anything else is an error path.
        """
        return getattr(self, "_auto_stop_reason", "unknown")

    def _auto_stop_loop(
        self,
        proc: subprocess.Popen,
        on_done: Callable[[Optional[str]], None],
        max_seconds: float,
        silence_seconds: float,
        on_level: Optional[Callable[[float], None]] = None,
        generation: int = 0,
    ) -> None:
        stdout = proc.stdout
        chunks: list = []
        detector: Optional[SilenceDetector] = None
        try:
            calib_frames = []
            for _ in range(_CALIBRATION_FRAMES):
                chunk = stdout.read(_FRAME_BYTES) if stdout else b""
                if not chunk:
                    break
                _SUPERVISOR.update_heartbeat(_CAPTURE_CHILD)
                calib_frames.append(chunk)
                chunks.append(chunk)
            threshold = calibrate_noise_floor(calib_frames)
            detector = SilenceDetector(threshold=threshold, silence_seconds=silence_seconds, frame_seconds=_FRAME_SECONDS)

            deadline = time.monotonic() + max_seconds
            # **Why it stopped, not just that it stopped.** A recording that ends
            # because the person finished and one that ends because the clock ran
            # out are different facts, and only the second one is a transcript
            # that stops mid-sentence. The caller cannot tell them apart from an
            # audio file, so it is recorded here and handed over.
            self._auto_stop_reason = "cancelled"
            while time.monotonic() < deadline and not self._auto_stop_cancel.is_set():
                # The read below blocks for up to one frame, so it can complete
                # after the user pressed stop. A frame that straddles the stop
                # boundary is dropped rather than kept: `read()` returning does
                # not mean the samples were captured before the press, and they
                # may be the user already starting their next sentence. Dropping
                # costs at most one frame (80ms) of audio the user had finished
                # saying; keeping it can silently change `heard_speech` and so
                # turn a "didn't catch that" into a transcript, or vice versa.
                boundary = self._stop_epoch
                chunk = stdout.read(_FRAME_BYTES) if stdout else b""
                if self._stop_epoch != boundary:
                    break
                if not chunk:
                    break
                # A frame arrived, so the child is demonstrably alive and streaming. This
                # is the heartbeat the watchdog measures silence against, and it can only
                # be taken here: a wedge means no frame, which means this line is never
                # reached, which is the condition being watched for.
                _SUPERVISOR.update_heartbeat(_CAPTURE_CHILD)
                chunks.append(chunk)
                detector.feed(chunk)
                if on_level is not None and self._generation == generation:
                    on_level(normalize_level(rms(chunk)))
                if detector.is_done():
                    self._auto_stop_reason = "silence"
                    break
            else:
                # The loop ended on its condition rather than a `break`, which is
                # only possible for the deadline - the cancel case sets the event
                # and the loop simply stops, so both are checked explicitly.
                self._auto_stop_reason = ("cancelled"
                                          if self._auto_stop_cancel.is_set()
                                          else "limit")
            if on_level is not None and self._generation == generation:
                on_level(0.0)
        except Exception as e:
            logger.error(f"Auto-stop recording loop crashed: {e}")
        finally:
            # A capture that ended the way it was meant to - silence, the deadline, or
            # the user pressing stop - terminates its own child, so that is not a fault
            # and the child is forgotten. A child that is *already* gone when the loop
            # ends is the fault, and stays tracked on purpose: forgetting it here would
            # erase the exit status at the same moment it becomes the only evidence,
            # and `pw-record` dying mid-turn is indistinguishable from a quiet room for
            # every other line of this module (`on_done(None)` either way).
            if proc.poll() is None:
                _SUPERVISOR.forget_child(_CAPTURE_CHILD)
            if self._watchdog_stop is not None:
                self._watchdog_stop.set()
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            self._proc = None
            self._auto_stop_thread = None
            _put_organ(self._organ)
            self._organ = None

        if self._generation != generation:
            logger.info("Dropping a superseded capture's result; a newer turn has started")
            return

        raw = b"".join(chunks)
        if not raw or detector is None or not detector.heard_speech:
            on_done(None)
            return

        fd, path = tempfile.mkstemp(suffix=".wav", prefix="chronoa-rec-")
        os.close(fd)
        try:
            with wave.open(path, "wb") as wf:
                wf.setnchannels(_CHANNELS)
                wf.setsampwidth(_SAMPLE_WIDTH)
                wf.setframerate(_SAMPLE_RATE)
                wf.writeframes(raw)
        except Exception as e:
            logger.error(f"Failed to write captured audio: {e}")
            os.remove(path) if os.path.exists(path) else None
            on_done(None)
            return

        # Re-checked after the write, not just before it. Writing the WAV is
        # the longest stretch between the loop finishing and the callback, so
        # it is the window in which a superseding capture is most likely to
        # have been accepted. One check would leave exactly the race it was
        # added to close.
        if self._generation != generation:
            logger.info("Dropping a superseded capture's WAV; a newer turn has started")
            os.remove(path) if os.path.exists(path) else None
            return
        on_done(path)


class AudioPlayer:
    """Plays WAV audio through the default output device.

    Playback runs as a subprocess tracked on `self._proc` so a concurrent
    call to `stop()` (from the GTK main thread, e.g. the user pressing the
    orb again or a wake word firing while the assistant is still talking)
    can interrupt it early - this is Chronoa's barge-in mechanism. It's a
    "the user started talking again, stop" interrupt, not full-duplex
    simultaneous listen+speak (that would need a live VAD loop during
    playback, a bigger undertaking not attempted here).
    """

    # See `AudioRecorder`. Bumped by both `play_file` (a newer reply supersedes
    # an older one) and `stop()` (barge-in), so a playback can tell whether it
    # finished on its own terms or was cut short, and report the difference.
    _generation: int = 0

    def __init__(self, target: Optional[str] = None) -> None:
        self._backend = self._detect_backend()
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._target = target or None
        self._generation = 0
        # The mouth activity for the reply being spoken. Held for the whole
        # playback rather than flashed at synthesis: "the voice was prepared" and
        # "the room heard it" are different claims, and only the second one is
        # what somebody in the room is entitled to.
        self._organ = None

    def set_target(self, target: Optional[str]) -> None:
        """Point playback at a specific device, or back to the default."""
        self._target = target or None

    def _detect_backend(self) -> Optional[str]:
        if shutil.which("pw-play"):
            return "pw-play"
        if shutil.which("aplay"):
            return "aplay"
        return None

    def is_available(self) -> bool:
        """Check if a playback backend is installed."""
        return self._backend is not None

    def _playback_cmd(self, path: str) -> list:
        """Playback argv, with `--target` applied when a device is chosen.

        As with capture, the setting is only meaningful on `pw-play`; ALSA's
        `aplay -D` wants a different identifier than a PipeWire node name.
        """
        if self._backend == "pw-play" and self._target:
            return ["pw-play", "--target", self._target, path]
        return [self._backend, path]

    def play_file(self, path: str) -> bool:  # noqa: D401
        """Play a WAV file, blocking until playback finishes or is stopped.

        Returns False when this playback did not get to finish on its own -
        interrupted by `stop()`, or superseded by a newer `play_bytes`. The
        distinction matters because playback runs in a worker thread
        (`app._speak` uses `run_in_executor`) and the async bridge does not
        serialise those calls, so two replies really can overlap. `self._proc`
        names only the newest one, which used to leave the older subprocess
        running with nothing able to reach it: `stop()` found the newer
        process, the older voice kept talking over the new turn, and it ran to
        completion (or the 120s timeout). Starting a playback now terminates
        the one it replaces, and the generation records that this one is the
        the one that finished.
        """
        if not self._backend:
            return False
        superseded: Optional[subprocess.Popen] = None
        superseded_organ = None
        try:
            with self._lock:
                superseded = self._proc
                superseded_organ = self._organ
                proc = subprocess.Popen(
                    self._playback_cmd(path),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self._proc = proc
                self._generation += 1
                generation = self._generation
        except Exception as e:
            logger.error(f"Playback failed ({self._backend}): {e}")
            return False
        # Opened here, at the spawn, because that is when the speaker starts making
        # noise. Cleared in the `finally` below, on every exit including barge-in.
        self._organ = _light_organ("mouth", "speaking", os.path.basename(path)[:60])

        # Tracked for its exit status only. There is no byte stream to starve here -
        # this class waits on the child with one bounded `wait()` - so `stall_after` is
        # explicitly `None` rather than the default budget, which would call every
        # playback longer than that budget hung and invent a fault that cannot happen.
        _SUPERVISOR.track_child(
            _PLAYBACK_CHILD, proc.poll, pid=getattr(proc, "pid", None), stall_after=None
        )
        _SUPERVISOR.update_heartbeat(_PLAYBACK_CHILD)

        if superseded is not None:
            # The reply being replaced was talking a moment ago and is not any
            # more, so its mouth light closes here. Without this the new playback
            # overwrites `self._organ` and the old activity is unreachable: no
            # later `done()` can ever find it, and the indicator would sit on
            # "speaking" for the rest of the session.
            _put_organ(superseded_organ)
            self._terminate(superseded)

        finished_on_its_own = False
        try:
            proc.wait(timeout=120)
            finished_on_its_own = True
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        finally:
            with self._lock:
                if self._proc is proc:
                    # The speaker has stopped either way - it finished on its own
                    # or it was cut short - so the mouth light goes off here, in
                    # the one place every exit from a playback passes through.
                    _put_organ(self._organ)
                    self._organ = None
                    self._proc = None
                finished_on_its_own = finished_on_its_own and self._generation == generation
            # Whether a positive exit status means "the child failed on its own" cannot
            # be decided from the status. Measured against the real binary on this
            # machine: `pw-play` exits 1 when sent SIGTERM, and exits 1 when it cannot
            # read the file it was given - the same value for "the user interrupted
            # this" and "the audio device vanished", so any rule keyed on the number
            # alone reports every barge-in as a fault. The one thing that does know the
            # difference is this class, which is why `_terminate` records the signal on
            # the process it sent it to. With that, a positive status from a playback
            # nobody interfered with is a real fault, and it is the only place in the
            # codebase that can learn it: `play_file` returns True for a `pw-play` that
            # died in its first second, so the caller is told the user heard a reply
            # they never heard. Left tracked, so the status survives the caller moving on.
            if getattr(proc, _SIGNALLED, False) or (proc.returncode or 0) <= 0:
                _SUPERVISOR.forget_child(_PLAYBACK_CHILD)
        return finished_on_its_own

    def playback_status(self) -> Optional[HeartbeatStatus]:
        """The outstanding playback's supervised state, or None if there is none.

        `EXITED` here means `pw-play` ended on its own with an error status - the
        failure `play_file`'s `True` cannot express.
        """
        for status in _SUPERVISOR.check_heartbeats():
            if status.name == _PLAYBACK_CHILD:
                return status
        return None

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        """Stop a playback subprocess, escalating to SIGKILL if it ignores SIGTERM."""
        if proc.poll() is not None:
            return
        # Recorded on the process rather than on `self`, because `stop()` and a
        # superseding `play_file` both reach here for a process that is not the current
        # one. It is the only evidence that separates "this class ended the playback"
        # from "the playback ended", which the exit status cannot do - see `play_file`.
        setattr(proc, _SIGNALLED, True)
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    def stop(self) -> None:
        """Interrupt any in-progress playback (barge-in). No-op if idle."""
        with self._lock:
            proc = self._proc
            self._proc = None
            # Barge-in: the reply was cut off, so the mouth closes now. Without
            # this the light would stay on until the playback thread's own
            # `finally` ran, which for an interrupted turn can be seconds of
            # silence with the indicator still claiming Chronoa is speaking.
            if proc is not None:
                _put_organ(self._organ)
                self._organ = None
            # Bumped even when there is nothing to stop, so a `play_file` that
            # is between Popen and wait() still learns that it was superseded.
            self._generation += 1
        if proc is None:
            return
        self._terminate(proc)

    def play_bytes(self, data: bytes) -> bool:
        """Play in-memory WAV bytes, blocking until playback finishes."""
        if not data:
            return False
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        try:
            return self.play_file(path)
        finally:
            os.unlink(path)


_BARGE_IN_FRAMES = 3


class BargeInMonitor:
    """Continuously listens for real speech while TTS is playing and fires a
    callback the instant it's detected - "start talking to interrupt",
    the Pipecat/continuous-VAD pattern, rather than only interrupting when
    the user presses the orb or says the wake word again (that's
    `AudioPlayer.stop()`, used unconditionally by `app.py`'s
    `_begin_listening`; this is the additional, opt-in "just start talking"
    path).

    Deliberately opt-in and off by default (see the `barge-in-vad-enabled`
    gsetting): there is no acoustic echo cancellation here, so on
    loudspeakers - as opposed to headphones - the mic also picks up
    Chronoa's own TTS output bleeding back in, which a plain energy
    threshold cannot tell apart from the user actually talking. The
    threshold uses an extra margin over the calibrated ambient floor to
    reduce (not eliminate) false self-interruption; it will still misfire
    on loud enough speaker output on some hardware. Works best with
    headphones or a room/mic with weak speaker-to-mic coupling.
    """

    # Extra margin on top of the ambient-noise calibration, since playback
    # itself raises the effective noise floor further than plain silence.
    _PLAYBACK_MARGIN = 1.5

    #: How long the monitor waits for `begin_playback()` before deciding the speaker
    #: is audible on its own. Long enough for `start()` and the first samples of
    #: audio to have happened; short enough that a caller who never plays anything
    #: does not leave the thread parked.
    _PRE_PLAYBACK_GUARD_SECONDS = 1.0

    # Class-level defaults, so a monitor built with `__new__` (which
    # `tests/test_barge_in_gate.py` does, to avoid spawning a microphone) still
    # has working counters. Each instance shadows them on first `+=`.
    _generation: int = 0
    _stop_epoch: int = 0

    def begin_playback(self) -> None:
        """Mark the speaker as audible; from here frames may be the assistant's voice.

        Until this is called, every frame is the room, because nothing has been played
        yet. That is the window the noise floor has to be calibrated from: sampled after
        playback starts it is Chronoa's own voice, which measured 25x the floor of a
        quiet room, and every later frame is then compared against the assistant
        speaking to itself.
        """
        self._playing.set()

    def __init__(self, target: Optional[str] = None) -> None:
        self._backend = self._detect_backend()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._playing = threading.Event()
        # Must match the recorder's device: barge-in that listens to a
        # different microphone than the one recording would fire on the room
        # rather than on the user.
        self._target = target or None
        self._generation = 0
        self._stop_epoch = 0

    def _detect_backend(self) -> Optional[str]:
        if shutil.which("pw-record"):
            return "pw-record"
        if shutil.which("arecord"):
            return "arecord"
        return None

    def is_available(self) -> bool:
        """Check if a mic capture backend is installed."""
        return self._backend is not None

    def start(self, on_interrupt: Callable[[], None]) -> bool:
        """Start monitoring on a background thread; fires `on_interrupt()` at most once.

        Bumps the generation, which is the only thing that keeps `on_interrupt`
        bound to *this* playback. `stop()` gives up on its thread after a
        two-second join, and if the read in that thread is still blocked it
        leaves `self._thread` cleared, so the next `start()` is accepted and two
        monitor threads run at once. The older one then holds a `proc` nobody
        can reach, and its `on_interrupt` fires for a reply that has already
        finished - barge-in acting on a turn that is over, which is the one
        thing a barge-in gate must never do.
        """
        if not self.is_available() or self._thread is not None:
            return False
        try:
            proc = subprocess.Popen(
                _stream_capture_cmd(self._backend, self._target),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error(f"Failed to start barge-in monitor ({self._backend}): {e}")
            return False

        self._proc = proc
        self._stop.clear()
        self._playing.clear()
        self._generation += 1
        self._thread = threading.Thread(
            target=self._monitor_loop, args=(proc, on_interrupt, self._generation), daemon=True
        )
        self._thread.start()
        return True

    def _monitor_loop(
        self, proc: subprocess.Popen, on_interrupt: Callable[[], None], generation: int = 0
    ) -> None:
        stdout = proc.stdout
        try:
            calib_frames = []
            for _ in range(_CALIBRATION_FRAMES):
                if self._stop.is_set():
                    return
                chunk = stdout.read(_FRAME_BYTES) if stdout else b""
                if not chunk:
                    return
                calib_frames.append(chunk)
            threshold = calibrate_noise_floor(calib_frames) * self._PLAYBACK_MARGIN

            # Re-read the floor once the speaker is live. The frames above are only
            # the room if playback had not already started, and `start()` does not
            # wait for this thread before the audio begins. Calibrating on the
            # assistant's own voice measures the assistant, and then every frame it
            # hears is judged against itself - which is the false self-interruption
            # this whole class is off by default to avoid.
            self._playing.wait(timeout=self._PRE_PLAYBACK_GUARD_SECONDS)

            loud_run = 0
            while not self._stop.is_set():
                # A frame already in flight when `stop()` arrived was captured
                # before the monitor was stopped, so it is dropped instead of
                # judged. Judging it would let a stopped monitor interrupt a
                # reply that had already ended.
                #
                # Redundant with the generation check below, and deliberately
                # left in: `stop()` advances both, so through the public path the
                # two are not separable (a mutation removing only this one leaves
                # `tests/test_audio_generation_guard.py` green - measured, and
                # recorded there). It states the boundary at the read, which is
                # where the reasoning lives, and it is the only guard if the
                # generation bump is ever moved out of `stop()`.
                boundary = self._stop_epoch
                chunk = stdout.read(_FRAME_BYTES) if stdout else b""
                if self._stop_epoch != boundary:
                    return
                if not chunk:
                    break
                if self._generation != generation:
                    logger.info("Barge-in: dropping a monitor superseded by a newer playback")
                    return
                # three loud frames in a row (~240 ms), not one: a single peak
                # is a cough, a click or the reply's own loudest syllable
                # leaking back, and it cut the reply off (assistd confirms onset
                # over consecutive frames for the same reason)
                loud_run = loud_run + 1 if rms(chunk) >= threshold else 0
                if loud_run >= _BARGE_IN_FRAMES:
                    logger.info("Barge-in: speech detected during playback")
                    on_interrupt()
                    return
        except Exception as e:
            logger.error(f"Barge-in monitor loop crashed: {e}")

    def stop(self) -> None:
        """Stop monitoring. Safe to call whether or not it's running."""
        self._stop.set()
        self._stop_epoch += 1
        self._generation += 1
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
