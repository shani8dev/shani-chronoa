"""Speak a reply sentence by sentence while the rest is still being written or synthesised.

Before this, a reply was spoken only after the model had produced all of it
AND the speech engine had turned all of it into one WAV - on a CPU model,
many seconds of silence before the first word. Harvested from assistd
(`assistd-voice/src/sentence.rs`, `assistd-core/src/state/query.rs`): split the
text into sentences as it streams in, synthesise each one, and play them in
order, synthesising sentence N+1 while sentence N plays.

Two pieces, both independent of GTK:

- `SentenceBuffer` - push text deltas in, get finished sentences out; a pause
  in the stream (`flush_idle`) or the end of the reply (`flush`) releases the
  rest. Splits at `.`/`!`/`?` before a space and a capital or digit, at a
  blank line and at a bullet; not inside "e.g.", "Dr.", "3.5" or a URL.
- `SpeechQueue` - a synthesis thread and a playback thread joined by a small
  queue. `stop()` (Esc, barge-in) empties both and stops the player.

What is spoken goes through `markdown_lite.to_speech` per sentence, so markup
and long code blocks are handled exactly as for a whole reply.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Callable, List, Optional

from shani_chronoa import markdown_lite

logger = logging.getLogger(__name__)

_ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "prof", "st", "no", "approx", "fig",
                  "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
_BOUNDARY = re.compile(r"([.!?]+[\"')\]]*)(\s+)(?=[A-Z0-9\"'(\[*_-])")
#: A sentence longer than this is cut at its last comma or space: a run-on
#: list must not hold the voice back for the whole paragraph.
MAX_SENTENCE_CHARS = 280
#: Below this, a "sentence" waits for more: "1." or "Hi." alone sounds clipped.
MIN_SENTENCE_CHARS = 12


def _is_abbreviation(text: str, end: int) -> bool:
    word = re.search(r"([A-Za-z.]+)\.?$", text[:end].rstrip(".!?"))
    if not word:
        return False
    w = word.group(1).lower().rstrip(".")
    return w in _ABBREVIATIONS or (len(w) == 1 and w.isalpha())


class SentenceBuffer:
    """Accumulates streamed text; hands back whole sentences as soon as they are complete."""

    def __init__(self) -> None:
        self._buf = ""
        self._in_fence = False
        self.last_push = time.monotonic()

    def push(self, delta: str) -> "List[str]":
        self._buf += delta or ""
        self.last_push = time.monotonic()
        return self._drain(final=False)

    def flush(self) -> "List[str]":
        out = self._drain(final=True)
        rest, self._buf = self._buf.strip(), ""
        return out + ([rest] if rest else [])

    def flush_idle(self) -> "List[str]":
        """After a pause in the stream: release up to the last whole word (assistd's partial_flush)."""
        if self._in_fence or len(self._buf.strip()) < MIN_SENTENCE_CHARS:
            return []
        cut = self._buf.rstrip().rfind(" ")
        if cut <= 0:
            return []
        head, self._buf = self._buf[:cut], self._buf[cut:]
        return [head.strip()] if head.strip() else []

    def _drain(self, final: bool) -> "List[str]":
        out: List[str] = []
        while True:
            piece = self._next(final)
            if piece is None:
                return out
            if piece.strip():
                out.append(piece.strip())

    def _next(self, final: bool) -> "Optional[str]":
        lead = len(self._buf) - len(self._buf.lstrip(" \n\t"))
        if lead:
            self._buf = self._buf[lead:]
        buf = self._buf
        # a code fence is one unit: spoken (or summarised) whole, never split inside
        fence = buf.find("```")
        if fence != -1:
            close = buf.find("```", fence + 3)
            if fence > 0 and buf[:fence].strip():
                self._buf = buf[fence:]
                return buf[:fence]
            if close == -1:
                self._in_fence = True
                return None
            self._in_fence = False
            end = buf.find("\n", close + 3)
            end = len(buf) if end == -1 else end + 1
            if end == len(buf) and not final:
                return None
            self._buf = buf[end:]
            return buf[:end]
        candidates = []
        para = buf.find("\n\n")
        if para != -1:
            candidates.append(para + 2)
        for bullet in re.finditer(r"\n[ \t]*(?:[-*•]|\d+[.)])\s", buf):
            if bullet.start() > 0:
                candidates.append(bullet.start() + 1)
                break
        for m in _BOUNDARY.finditer(buf):
            if m.group(1) == "." and (_is_abbreviation(buf, m.start(1) + 1)
                                      or re.search(r"\d$", buf[:m.start(1)]) and buf[m.end():m.end() + 1].isdigit()):
                continue
            if re.search(r"https?://\S*$", buf[:m.start(1)]):
                continue
            candidates.append(m.end(1))
            break
        candidates = [c for c in candidates if len(buf[:c].strip()) >= MIN_SENTENCE_CHARS]
        if candidates:
            cut = min(candidates)
            self._buf = buf[cut:]
            return buf[:cut]
        if len(buf) > MAX_SENTENCE_CHARS:
            cut = max(buf.rfind(", ", 0, MAX_SENTENCE_CHARS), buf.rfind(" ", 0, MAX_SENTENCE_CHARS))
            if cut > MIN_SENTENCE_CHARS:
                self._buf = buf[cut + 1:]
                return buf[:cut + 1]
        return None


def split_sentences(text: str) -> "List[str]":
    """A whole reply cut into the pieces `SpeechQueue` speaks one after another."""
    buf = SentenceBuffer()
    return buf.push(text) + buf.flush()


class SpeechQueue:
    """Synthesise-ahead, play-in-order speech for one reply.

    `synthesize(text) -> wav bytes` and `play(wav) -> bool` are the TTS and the
    player (blocking). `on_start` fires once, as the first sentence starts
    playing; `on_done` once, after the last one or after `stop()`.
    """

    def __init__(self, synthesize: Callable[[str], bytes], play: Callable[[bytes], bool],
                 stop_player: Callable[[], None] = lambda: None,
                 on_start: Callable[[], None] = lambda: None, on_done: Callable[[], None] = lambda: None) -> None:
        self._synthesize, self._play, self._stop_player = synthesize, play, stop_player
        self._on_start, self._on_done = on_start, on_done
        self._texts: "queue.Queue[Optional[str]]" = queue.Queue()
        self._wavs: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=2)
        self._stopped = threading.Event()
        self._closed = False
        self._started = False
        self.spoken: List[str] = []
        self._threads = [threading.Thread(target=self._synth_loop, daemon=True, name="chronoa-tts-synth"),
                         threading.Thread(target=self._play_loop, daemon=True, name="chronoa-tts-play")]
        for t in self._threads:
            t.start()

    def speak(self, sentences) -> None:
        if self._closed or self._stopped.is_set():
            return
        for sentence in sentences:
            spoken = markdown_lite.to_speech(sentence)
            if spoken.strip():
                self._texts.put(spoken)

    def close(self) -> None:
        """No more sentences are coming; what is queued still plays."""
        if not self._closed:
            self._closed = True
            self._texts.put(None)

    def stop(self) -> None:
        """Silence now: drop everything queued and stop what is playing."""
        self._stopped.set()
        self._closed = True
        self._stop_player()
        for q in (self._texts, self._wavs):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
        self._texts.put(None)
        try:
            self._wavs.put_nowait(None)
        except queue.Full:
            pass

    @property
    def active(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def wait(self, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        for t in self._threads:
            t.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
        return not self.active

    def _synth_loop(self) -> None:
        while not self._stopped.is_set():
            text = self._texts.get()
            if text is None or self._stopped.is_set():
                break
            try:
                wav = self._synthesize(text)
            except Exception as exc:  # noqa: BLE001 - one bad sentence must not silence the rest
                logger.error("Speech synthesis failed for one sentence: %s", exc)
                continue
            if wav:
                while not self._stopped.is_set():
                    try:
                        self._wavs.put((text, wav), timeout=0.2)
                        break
                    except queue.Full:
                        continue
        # the end marker for the player - unless stopped, when stop() already
        # sent one and the player may be gone (a blocking put would hang here)
        while not self._stopped.is_set():
            try:
                self._wavs.put(None, timeout=0.2)
                break
            except queue.Full:
                continue

    def _play_loop(self) -> None:
        try:
            while not self._stopped.is_set():
                item = self._wavs.get()
                if item is None or self._stopped.is_set():
                    break
                text, wav = item
                if not self._started:
                    self._started = True
                    self._on_start()
                self._play(wav)
                self.spoken.append(text)
        finally:
            self._on_done()
