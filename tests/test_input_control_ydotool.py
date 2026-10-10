"""`input_control`: the ydotool backend was built against a binary that does not exist.

The module called `dotool` and `dotoold`. Neither exists. Arch's `ydotool`
package (1.0.4-2, extra) installs **`ydotool`** and **`ydotoold(8)`** — read
from the Arch man page for `ydotool(1)`, not from memory.

So `shutil.which("dotool")` returned None on **every** machine, the branch was
unreachable code on a system that had the tool installed, and nothing anywhere
checked — because a `which()` that finds nothing is indistinguishable from a
tool that is not installed, which is the *expected* case on a Wayland desktop.

**The command forms were wrong too**, and each would have failed if the name
had been right:

| this built | ydotool(1) takes | what would have happened |
|---|---|---|
| `move X Y` | `mousemove [--absolute] X Y` | **relative** movement, so a click at (400,300) lands 400,300 from wherever the pointer already was |
| `click BUTTON` | `click [-r/--repeat N] [button...]` | no `--repeat`, and no `0xC0` down/up mask |
| `type -- TEXT` | `type ... "text"` | ydotool has **no `--`**; it would type the literal string "--" |

Found by `tools/audit_missing_tool_hints.py`, which walks the AST for every
`which()`/`tool_missing()` call and listed `dotool` as a binary nothing can
resolve.

**Unverified by execution, and said so:** ydotool needs a `ydotoold` daemon
over `/dev/uinput`, and there is neither here. These assert the argv the
module *builds*, against the man page it was written against.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import input_control as IC  # noqa: E402


class _FakePath:
    """A PATH holding a chosen set of binaries, on top of the real one."""

    def __init__(self, monkeypatch, bindir):
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("wtype", "ydotool", "xdotool", "dotool"):
        (bindir / name).write_text("#!/bin/sh\nexit 0\n")
        (bindir / name).chmod(0o755)
    _FakePath(monkeypatch, bindir)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    return bindir


def _only(bindir, *names):
    """Remove the fake binaries we do not want on PATH for this test."""
    for path in bindir.iterdir():
        if path.name not in names:
            path.unlink()


# --- the binary name ------------------------------------------------------------

def test_the_ydotool_binary_is_the_one_that_exists(fake_bin):
    """`dotool` is not a binary on any system; `ydotool` is, and Arch's package
    installs exactly that name. A `which("dotool")` returning None is why no
    test ever caught this: it looks like the tool being absent, which is the
    normal state of a Wayland desktop.
    """
    _only(fake_bin, "ydotool")
    monkey = os.environ
    monkey["XDG_SESSION_TYPE"] = "wayland"
    name, prefix = IC._detect_backend()
    assert name == "ydotool", name
    assert prefix == ["ydotool"], prefix


def test_there_is_no_binary_called_dotool_in_the_source(tmp_path):
    """Asserted on the module's own source rather than on `which`, because the
    point is that the *string* was wrong and a `which()` that returns None for
    a bad name is indistinguishable from a tool being absent.
    """
    import re
    text = pathlib.Path(IC.__file__).read_text()
    # **Comments stripped first**, or the prose explaining the bug matches the
    # pattern the bug left behind - which is how the first version of this test
    # reported the very comment it had just added.
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    for match in re.finditer(r"[A-Za-z-]*dotool", code):
        assert match.group(0) in ("xdotool", "ydotool"), \
            f"{match.group(0)!r} is not a binary that exists"
    # And the daemon's name is right.
    assert "ydotoold" in text


def test_the_dead_backend_is_reachable_now(fake_bin):
    """Before the fix this returned `None` and the skill refused, while
    `ydotool` sat installed. A `None` here is the defect.
    """
    _only(fake_bin, "ydotool")
    os.environ["XDG_SESSION_TYPE"] = "wayland"
    assert IC._detect_backend() is not None


# --- the three command forms ---------------------------------------------------

def _ydotool(monkeypatch):
    """Build a command for the ydotool backend - `_build_command` takes the
    backend tuple `_detect_backend` returns, so it is called with the pair the
    code actually produces rather than with a name in isolation."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    return lambda action, params: IC._build_command(
        ("ydotool", ["ydotool"]), action, params)


def test_move_is_mousemove_and_it_is_absolute(monkeypatch):
    """**The dangerous one.** ydotool's `mousemove` without `--absolute` moves
    by a *delta*, so the old `move X Y` would have put a click at (400,300)
    from wherever the pointer already happened to be - not at (400,300).
    """
    build = _ydotool(monkeypatch)
    cmd = build("move", {"x": 400, "y": 300})
    assert cmd[:2] == ["ydotool", "mousemove"], cmd
    assert "--absolute" in cmd, cmd
    assert cmd[-2:] == ["400", "300"], cmd


def test_click_masks_a_complete_press_and_release(monkeypatch):
    """ydotool's `click` takes hexadecimal with an optional down/up bitmask;
    `0xC0` is press-down plus mouse-up, i.e. an actual click. Sending a bare
    button number is a **press with no release** - the mouse button stays held
    down until something else releases it.
    """
    build = _ydotool(monkeypatch)
    cmd = build("click", {"button": "left", "count": 2})
    assert cmd[:2] == ["ydotool", "click"], cmd
    # The man page's short form is `-r`; the module uses the long spelling,
    # which ydotool accepts. Asserted on the long one because a test that
    # pins the short form fails here for a reason that is not a defect.
    assert "--repeat" in cmd and "2" in cmd, cmd
    assert cmd[-1] == "0xC0:left", cmd


def test_type_takes_the_text_positionally_with_no_double_dash(monkeypatch):
    """ydotool's `type` has no `--` separator. Passing one types the literal
    string "--" in front of whatever the caller asked for.
    """
    build = _ydotool(monkeypatch)
    cmd = build("type", {"text": "hello"})
    assert cmd == ["ydotool", "type", "hello"], cmd
    assert "--" not in cmd, cmd


def test_text_beginning_with_a_dash_is_not_read_as_a_flag(monkeypatch):
    """`-rf` through the double-dash form is the injection-shaped case; the
    positional form is safe because argv carries it whole, but the flag is the
    one to keep watching.
    """
    build = _ydotool(monkeypatch)
    cmd = build("type", {"text": "--repeat 5"})
    assert cmd[-1] == "--repeat 5", cmd


def test_a_single_click_needs_no_repeat_flag(monkeypatch):
    """`--repeat 1` is a redundant flag, and it is not sent."""
    build = _ydotool(monkeypatch)
    cmd = build("click", {"button": "left"})
    assert "--repeat" not in cmd, cmd
    assert cmd[-1] == "0xC0:left", cmd
