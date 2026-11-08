"""The number under a running download must be about the file it is fetching.

One plan item can download several files (a language's OCR data, then its
Piper voice). `_progress` is one hook reused for all of them, and it used to
add every chunk to one counter while `total` stayed per-file: the voice
download read "68 of 56 MB" and the bar sat at 100% from the OCR file's last
chunk to the end.
"""

import threading

from shani_chronoa import setup_wizard


def test_progress_starts_over_at_each_file():
    seen = []
    hook = setup_wizard._progress(lambda f, t: seen.append((round(f, 2), t)),
                                  threading.Event(), "Hindi")
    hook(30 << 20, 60 << 20)          # first file, half
    hook(30 << 20, 60 << 20)          # first file, done
    hook(30 << 20, 60 << 20)          # second file, half - not "90 of 60"
    assert seen == [(0.5, "Hindi: 30 of 60 MB"),
                    (1.0, "Hindi: 60 of 60 MB"),
                    (0.5, "Hindi: 30 of 60 MB")]


def test_progress_never_reads_past_the_file_it_is_fetching():
    seen = []
    hook = setup_wizard._progress(lambda f, t: seen.append(f), threading.Event(), "voice")
    for _ in range(4):
        hook(20 << 20, 60 << 20)      # three chunks fill it, the fourth starts a new file
    assert max(seen) == 1.0 and seen[-1] < 1.0
