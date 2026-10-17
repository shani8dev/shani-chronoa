"""`run_command`: the loop-closer, and the isolation it runs under.

The isolation tests drive the real `SandboxExecutor` with the config
`tools._run_command_sandbox` builds - bubblewrap plus Landlock - and read what
the kernel allowed. They were written after measuring three defects that only
showed up once something ran confined (no skill had, before this one):

- Landlock's write set had no `MAKE_REG`, so nothing could create a file
  anywhere in a confined call, the sandbox's own workspace included;
- `/dev` was read-only, so `subprocess.DEVNULL` (O_RDWR) failed every command;
- the bwrap path every confined call takes ignored `allow_network=False`.
"""
import os
import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import run_command as rc  # noqa: E402


class _On:
    def get_bool(self, key, default=False):
        return key == rc.CONSENT_KEY


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setattr(rc, "ChronoaConfig", lambda: _On())


# --- refusals, no process started -------------------------------------------

def test_off_by_default_says_which_switch(monkeypatch):
    class _Off:
        def get_bool(self, key, default=False):
            return False
    monkeypatch.setattr(rc, "ChronoaConfig", lambda: _Off())
    out = rc.run_command({"command": "true"})
    assert out.startswith("Not run") and "run commands" in out, out


@pytest.mark.parametrize("command", ["ls | wc -l", "make && make install", "echo x > f"])
def test_shell_syntax_is_refused(allowed, command):
    assert "not a shell line" in rc.run_command({"command": command})


@pytest.mark.parametrize("command", ["sudo rm -rf /", "pkexec true", "/usr/bin/sudo ls"])
def test_privilege_escalation_is_refused(allowed, command):
    assert "is not something this skill runs" in rc.run_command({"command": command})


def test_a_container_needs_an_image(allowed):
    assert "needs an `image`" in rc.run_command({"command": "true", "where": "container"})


def test_output_keeps_head_and_tail():
    text = "H" * 4000 + "MIDDLE" + "T" * 9000
    clipped = rc._clip(text)
    assert clipped.startswith("H") and clipped.endswith("T") and "left out" in clipped


# --- the sandbox, measured -------------------------------------------------

def _confined_executor():
    from shani_chronoa.sandbox import SandboxExecutor, landlock
    if landlock.abi_version() <= 0:
        pytest.skip("this kernel has no Landlock")
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is not installed")
    return SandboxExecutor()


@pytest.fixture
def project():
    home = pathlib.Path.home()
    proj = pathlib.Path(tempfile.mkdtemp(prefix="chronoa-rc-proj-", dir=home))
    outside = pathlib.Path(tempfile.mkdtemp(prefix="chronoa-rc-out-", dir=home))
    (outside / "secret.txt").write_text("TOP SECRET")
    yield proj, outside
    shutil.rmtree(proj, ignore_errors=True)
    shutil.rmtree(outside, ignore_errors=True)


def _run(executor, project_dir, code, network=False):
    from shani_chronoa import buildmode, tools
    buildmode.set_enabled(True)
    buildmode.set_project(str(project_dir))
    try:
        config = tools._run_command_sandbox({"network": network})
    finally:
        buildmode.set_project("")
        buildmode.set_enabled(False)
    return executor.execute(["python3", "-c", code], config)


def test_the_project_is_writable_in_place(project):
    proj, _ = project
    code, out, _ = _run(_confined_executor(), proj,
                        f"open({str(proj / 'new.txt')!r}, 'w').write('ok'); print('W')")
    assert code == 0 and (proj / "new.txt").read_text() == "ok", out
    assert not (proj / ".chronoa-landlock-wrapper.py").exists(), "the wrapper landed in the project"


def test_the_rest_of_home_cannot_be_written(project):
    proj, outside = project
    code, out, _ = _run(_confined_executor(), proj,
                        f"open({str(outside / 'evil.txt')!r}, 'w').write('x')")
    assert code != 0 and not (outside / "evil.txt").exists(), out


def test_the_rest_of_home_cannot_be_read(project):
    proj, outside = project
    code, out, _ = _run(_confined_executor(), proj,
                        f"print(open({str(outside / 'secret.txt')!r}).read())")
    assert code != 0 and "TOP SECRET" not in out, out


def test_the_network_is_cut_unless_asked_for(project):
    proj, _ = project
    code, out, _ = _run(_confined_executor(), proj,
                        "import socket; socket.create_connection(('1.1.1.1', 53), 3); print('UP')")
    assert code != 0 and "UP" not in out, out


def test_dev_null_can_be_written(project):
    proj, _ = project
    code, out, _ = _run(_confined_executor(), proj,
                        "open('/dev/null', 'w').write('x'); print('OK')")
    assert code == 0 and "OK" in out, out


# --- "Allow for this turn" ------------------------------------------------------

def test_allow_for_this_turn_covers_the_turn_and_lapses_after(monkeypatch):
    from shani_chronoa import ask_bridge, permissions, tools
    permissions.clear()
    monkeypatch.setattr(ask_bridge, "has_presenter", lambda: True)
    monkeypatch.setattr(ask_bridge, "ask", lambda *a, **k: permissions.ALLOW_TURN_CHOICE)
    tools.set_current_turn("turn-1")
    try:
        assert permissions.decide("edit_file", "/x/a.py", "file-edit-enabled") == \
            permissions.Decision.ALLOW_TURN
        # Same turn, another file: covered, without asking.
        assert permissions.session_grant("edit_file", "/x/b.py") == permissions.Decision.ALLOW_TURN
        # A different action is not.
        assert permissions.session_grant("run_command", "*") is None
        # Next turn: gone.
        tools.set_current_turn("turn-2")
        assert permissions.session_grant("edit_file", "/x/a.py") is None
    finally:
        tools.clear_current_turn()
        permissions.clear()


def test_this_turn_is_offered_even_where_session_is_not():
    from shani_chronoa import permissions
    request = permissions.approval_request("edit_file", "/x/a.py", "file-edit-enabled", "edit")
    assert permissions.ALLOW_TURN_CHOICE in request.options


# --- "Revert N files": a whole turn, back to before it ---------------------------

def test_revert_turn_goes_back_past_every_edit_of_that_turn(tmp_path, monkeypatch):
    from shani_chronoa.skills import undo_last_change as ul
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("A0")
    monkeypatch.setenv(ul.TURN_ENV, "turn-7")
    ul.record_preimage(a, b"A0"); a.write_text("A1")      # first edit of a
    ul.record_preimage(a, b"A1"); a.write_text("A2")      # second edit of a
    ul.record_preimage(b, b"");   b.write_text("B1")      # b created
    monkeypatch.setenv(ul.TURN_ENV, "turn-8")
    ul.record_preimage(a, b"A2"); a.write_text("A3")      # a later turn
    monkeypatch.delenv(ul.TURN_ENV)
    restored, skipped = ul.revert_turn("turn-7")
    assert sorted(restored) == sorted([str(a), str(b)]), (restored, skipped)
    assert a.read_text() == "A0" and b.read_text() == "", "not back to before the turn"
    # The revert is itself on the ring, so it can be undone.
    befores = [before for path, before, _after in ul.recent_changes()
               if str(path) == str(a)]
    assert "A3" in befores, f"the revert recorded no pre-image of what it replaced: {befores}"


def test_the_footer_offers_revert_only_when_the_turn_changed_files():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    from shani_chronoa.gui.widgets import TranscriptView

    def buttons(stats):
        view = TranscriptView.__new__(TranscriptView)
        view._on_revert_turn = lambda turn: ([], [])
        row = Gtk.Box()
        TranscriptView._footer(view, row, handler=lambda: stats)
        child, found = row.get_first_child(), []
        while child is not None:
            if isinstance(child, Gtk.Button):
                found.append(child.get_label())
            child = child.get_next_sibling()
        return found

    base = {"model_calls": 1, "turn": "turn-3"}
    assert buttons({**base, "changed": ["/x/a.py", "/x/b.py"]}) == ["Revert 2 files"]
    assert buttons({**base, "changed": []}) == []


# --- IPC: the layer the confined path never had --------------------------------
#
# Measured before the fix, from a confined `run_command`: it connected to the
# session bus and `systemd-run --user` wrote a file on the host outside every
# write rule; it saw 485 host pids and could signal one; it shared SysV IPC and
# saw the Wayland and X11 sockets.

def test_the_session_bus_is_not_reachable(project):
    proj, _ = project
    bus = f"/run/user/{os.getuid()}/bus"
    if not os.path.exists(bus):
        pytest.skip("no session bus on this machine to hide")
    code, out, _ = _run(_confined_executor(), proj,
                        f"import socket; s = socket.socket(socket.AF_UNIX); s.connect({bus!r}); print('CONNECTED')")
    assert "CONNECTED" not in out, "a confined command reached the session bus - systemd-run escapes"


def test_host_processes_are_neither_seen_nor_signalled(project):
    proj, _ = project
    parent = os.getpid()
    code, out, _ = _run(_confined_executor(), proj,
                        "import os; print('PIDS', len([p for p in os.listdir('/proc') if p.isdigit()]));"
                        f"os.kill({parent}, 0); print('SIGNALLED')")
    assert "PIDS" in out, f"the command never ran, so this measured nothing: {out}"
    assert "SIGNALLED" not in out, out
    seen = int(out.split("PIDS", 1)[1].split()[0])
    assert seen < 10, f"{seen} pids visible - the host's process table is shared"


def test_the_hostname_and_sysv_ipc_are_its_own(project):
    proj, _ = project
    code, out, _ = _run(_confined_executor(), proj,
                        "import socket; print('HOST', socket.gethostname());"
                        "print('SHM', open('/proc/sysvipc/shm').read().count(chr(10)) - 1)")
    assert "HOST chronoa-sandbox" in out, out
    assert "SHM 0" in out, out


@pytest.mark.parametrize("network", [False, True])
def test_abstract_sockets_are_out_of_reach_even_with_network(project, network):
    """With the network shared, only Landlock's scoping stands between the
    command and X11's abstract socket (and any D-Bus broker listening on one)."""
    import socket
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.connect("\0/tmp/.X11-unix/X0")
    except OSError:
        pytest.skip("no abstract X11 socket on this machine to test against")
    finally:
        probe.close()
    proj, _ = project
    code, out, _ = _run(_confined_executor(), proj,
                        "import socket; s = socket.socket(socket.AF_UNIX);"
                        "s.connect(chr(0) + '/tmp/.X11-unix/X0'); print('REACHED')",
                        network=network)
    assert "REACHED" not in out, out


# --- Diff panel: Revert on a written change ------------------------------------

def test_the_diff_panel_reverts_a_file_and_the_revert_is_undoable(tmp_path, monkeypatch):
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    Adw.init()
    from shani_chronoa.gui.surfaces import diff
    from shani_chronoa.skills import undo_last_change as ul
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    target = tmp_path / "app.py"
    target.write_text("old\n")
    ul.record_preimage(target, b"old\n")
    target.write_text("new\n")
    view = diff._DiffView(None)
    assert view.load_recent_changes() == 1
    item = view._files_box.get_first_child()
    assert item.on_revert is not None, "the row's Revert is wired to nothing"
    item._on_revert(None)
    assert target.read_text() == "old\n"
    befores = [b for p, b, _a in ul.recent_changes() if str(p) == str(target)]
    assert "new\n" in befores, "the revert recorded no pre-image, so it cannot be undone"


def test_a_bad_cwd_in_build_mode_runs_in_the_project(allowed, monkeypatch):
    """Measured with Qwen3-1.7B: an invented cwd and `where: machine` with no
    machine, refused three times, and the tests never ran. With a project set,
    the project is the obvious folder and the sandbox the obvious place."""
    from shani_chronoa import files
    project = pathlib.Path.home() / "proj"
    project.mkdir(exist_ok=True)
    monkeypatch.setenv(files.PROJECT_ENV, str(project))
    out = rc.run_command({"command": "true", "cwd": "/tmp/somewhere/else", "where": "machine"})
    assert "succeeded" in out, out
    assert "ran in the project folder" in out and "no machine was named" in out, out
