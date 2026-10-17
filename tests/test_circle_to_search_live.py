"""Circle to Search's Chronoa actions, pressed in a private headless GNOME Shell.

Print opens the screenshot UI with the overlay's row: Ask Chronoa, Translate,
Fix this error, Add to calendar, Google, Copy. A stand-in `shani-chronoa` on
the shell's PATH records what each button launches - the question, the
selection as a PNG, and for "Fix this error" `--rules=build`, so it is asked in
Build mode (measured with tests/test_launch_verbs.py on the app's side).
"""
import importlib.util
import json
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("live", _REPO / "tests" / "test_windows_gnome_live.py")
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)

pytestmark = live.pytestmark

_C2S = _REPO / "usr/share/gnome-shell/extensions/shani-circle-to-search@inled.es"
_STANDIN = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["HOME"] + "/launched.jsonl", "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
"""

#: Where each button sits at 1280x720 (measured from a shot of the overlay).
_BUTTONS = {"Ask Chronoa": (407, 608), "Translate": (497, 608),
            "Fix this error": (581, 608), "Add to calendar": (665, 608)}


@pytest.fixture
def overlay(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "shani-chronoa").write_text(_STANDIN)
    (bin_dir / "shani-chronoa").chmod(0o755)
    (tmp_path / "shell").mkdir()
    shell = live._Shell(tmp_path / "shell", monitor="1280x720", extra=[_C2S], bin_dir=bin_dir)
    try:
        shell.extension(True)
        live.subprocess.run(["gnome-extensions", "enable", _C2S.name], env=shell.env,
                            capture_output=True, timeout=20, check=True)
        time.sleep(4)  # past the startup animation

        def press(button):
            launched = shell.home / "launched.jsonl"
            launched.unlink(missing_ok=True)
            win = live.subprocess.run
            call = ["gdbus", "call", "--session", "--dest", "org.shani.Chronoa.Windows",
                    "--object-path", "/org/shani/Chronoa/Windows", "--method"]
            win(call + ["org.shani.Chronoa.Windows.KeyPress", "Print", "1"], env=shell.env,
                capture_output=True, timeout=20, check=True)
            time.sleep(2)
            x, y = _BUTTONS[button]
            win(call + ["org.shani.Chronoa.Windows.PointerMove", str(x), str(y)], env=shell.env,
                capture_output=True, timeout=20, check=True)
            win(call + ["org.shani.Chronoa.Windows.PointerClick", "left", "1"], env=shell.env,
                capture_output=True, timeout=20, check=True)
            live._Shell._wait(launched.exists, 10, f"{button} launched nothing")
            time.sleep(0.5)
            return [json.loads(line) for line in launched.read_text().splitlines()]
        yield press
    finally:
        shell.stop()


def test_each_action_asks_chronoa_about_the_selection(overlay):
    for button, words, rules in (("Translate", "Translate the text", None),
                                 ("Fix this error", "fix it", "--rules=build"),
                                 ("Add to calendar", "to my calendar", None)):
        [argv] = overlay(button)
        ask = next(a for a in argv if a.startswith("--ask="))
        assert words in ask, (button, argv)
        assert argv[-1].endswith(".png") and Path(argv[-1]).exists(), (button, argv)
        assert ("--rules=build" in argv) == (rules is not None), (button, argv)
