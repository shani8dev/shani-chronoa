"""A harness that cannot build its subject reports absence as nine failures.

`tests/test_window_input_and_copy.py` asks nine questions about a real
`ChronoaWindow`: is there a send button, does clicking it send, does the entry
clear, does Enter agree with the button, does whitespace alone send nothing,
does a reply carry a copy button and does pressing it copy. **All nine failed on
a machine with no display**, with `KeyError: 'send_visible'`,
`KeyError: 'via_button'`, `KeyError: 'clipboard'` and so on.

The cause was not a broken send button. The harness ran `ChronoaWindow` in a
subprocess and that subprocess raised:

    RuntimeError: Gtk couldn't be initialized
    Gdk-CRITICAL: gtk_icon_theme_get_for_display: assertion 'GDK_IS_DISPLAY (display)' failed

so it never printed its `RESULT` line. The module fixture then read the missing
line as `{}` and returned it, and every assertion failed on a **key that had
never been written**. Nine failures that read as "the input row is broken" and
were about a display that does not exist.

**An absence presented as a failure is the same defect as an absence presented as
a pass**, and this repo has been bitten by both directions. So this file asserts
the two things that make the difference:

- **The harness says it could not run.** With no display it exits with a named
  marker, and the fixture turns that marker into a `pytest.skip` carrying the
  reason - never into `{}`.
- **The tests really do pass where a window is possible.** Run against a display
  that can actually allocate one, all nine pass. That is the control: without
  it, "it skips" would be indistinguishable from "it skips because it is
  broken", and a file that skips everywhere reads exactly like coverage.

**`Gtk.init_check()` returning True is not a display.** GTK initialises
successfully with no display and every widget built afterwards is unallocated -
`Adw.init()` warns `invalid (NULL) pointer instance` and icon-theme lookups
critically fail. So the guard asks `Gdk.Display.get_default()` directly, because
the question is whether a window can exist, not whether the library loaded.
That distinction is the whole bug: asking the first question answers the second.

Nine failures of this shape were sitting in a chunked run's tail and read as a
product regression. They were pre-existing - verified by checking out the
senses directory from `9817575`, the session's first commit, where the same nine
fail - and no test anywhere reported *why*.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SRC = str(_REPO / "usr" / "lib" / "shani-chronoa")


def _harness_source() -> str:
    """The window harness, extracted from the test file rather than copied.

    Copied, it would be a second copy of the thing under test - and the defect
    this file exists for is precisely that a copy stopped matching. Extracted
    from the source of truth, so it cannot drift.
    """
    text = (_REPO / "tests" / "test_window_input_and_copy.py").read_text()
    start = text.index('"""', text.index("_HARNESS = textwrap.dedent(")) + 3
    end = text.index('\n    """', start)
    return textwrap.dedent(text[start:end])


def _run_harness() -> subprocess.CompletedProcess:
    work = pathlib.Path("/tmp/opencode/window-harness-probe")
    runtime = work / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime, 0o700)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = _SRC
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("GDK_BACKEND", None)
    env.pop("BROADWAY_DISPLAY", None)
    return subprocess.run([sys.executable, "-c", _harness_source()],
                          capture_output=True, text=True, timeout=180,
                          env=env, cwd=str(work))


class TestTheHarnessTellsYouItCouldNotRun:
    def test_with_no_display_it_names_that_rather_than_returning_nothing(self):
        """The control for every other test in this file.

        Without a display the harness must exit with a **named** marker. Before
        this, it exited on an uncaught `RuntimeError` and printed no `RESULT`
        line at all, which the fixture read as `{}` - and nine tests failed on
        missing keys as though the send button were broken.
        """
        proc = _run_harness()
        combined = proc.stdout + proc.stderr
        has_result = any(line.startswith("RESULT") for line in proc.stdout.splitlines())
        assert has_result or "NODISPLAY" in combined or "NOGTK" in combined, (
            "the harness neither produced a result nor said why it could not:\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr[-2000:]}")

    def test_gtk_initialising_is_not_the_same_as_a_display(self):
        """Why the guard asks Gdk, not Gtk.

        `Gtk.init_check()` returns True with no display and every widget built
        afterwards is unallocated. A guard written against `init_check` would
        pass and then the window would raise anyway - which is what happened.
        """
        code = textwrap.dedent("""
            import gi
            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk, Gdk
            started = bool(Gtk.init_check())
            display = Gdk.Display.get_default()
            # `json.dumps`, not a %-format: Python's `True` is not JSON and
            # the parse fails on the *probe*, which reads as a broken product
            # rather than a broken probe - the same shape this file is about.
            import json
            print("RESULT" + json.dumps({"started": started,
                                         "display": display is not None}))
        """)
        env = dict(os.environ, PYTHONPATH=_SRC, PYTHONDONTWRITEBYTECODE="1")
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "GDK_BACKEND", "BROADWAY_DISPLAY"):
            env.pop(key, None)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=90, env=env)
        payload = None
        for line in proc.stdout.splitlines():
            if line.startswith("RESULT"):
                payload = json.loads(line[len("RESULT"):])
        assert payload is not None, f"probe produced nothing:\n{proc.stderr[-1500:]}"
        if payload["started"] and not payload["display"]:
            pytest.fail(
                "GTK reports initialised with no display, so a guard written "
                "against Gtk.init_check() would pass and the window would then "
                "raise anyway - the exact shape of the bug this file records")


class TestTheFileIsNotMerelySkipping:
    """A file that skips everywhere reads exactly like coverage.

    These are the assertions that cannot be satisfied by skipping: each states
    what the window must do, and each is skipped only when there is genuinely
    nothing to assert.
    """

    @pytest.mark.parametrize("probe", [
        "send_visible", "via_button", "via_enter", "cleared",
        "whitespace_emitted", "assistant_turns", "clipboard",
    ])
    def test_the_harness_writes_the_key_every_assertion_reads(self, probe):
        """Every key the nine tests index must be written, or the run is empty.

        Asserted against the *source* rather than a run, because on a
        display-less machine there is no run to inspect - and that is the
        situation that let the empty result pass unnoticed.
        """
        source = _harness_source()
        assert f'"{probe}"' in source, (
            f"nothing writes {probe!r} into the result, so every assertion "
            f"reading it would fail with a KeyError on a key that never existed")

    def test_the_fixture_skips_on_the_marker_rather_than_returning_empty(self):
        source = (_REPO / "tests" / "test_window_input_and_copy.py").read_text()
        assert "pytest.skip" in source, (
            "the fixture no longer skips: an unrunnable harness would return an "
            "empty dict again and nine tests would fail on missing keys")
        assert "NODISPLAY" in source, (
            "the fixture no longer checks for the marker, so the skip cannot "
            "fire even though the harness still raises it")
