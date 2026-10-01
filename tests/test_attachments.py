"""Attaching files: the paperclip and drag-and-drop, shown as chips, sent as paths.

The window part drives a real constructed CajitaWindow in a child process,
as test_window_input_and_copy does: a control created but not connected
cannot be told apart by reading the source.
"""

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

from shani_chronoa import attachments


def test_the_block_names_path_kind_and_size(tmp_path):
    img = tmp_path / "holiday.jpg"
    img.write_bytes(b"x" * 2048)
    block = attachments.block([img])
    assert str(img) in block and "(image, 2.0 KB)" in block and block.startswith("\n\n[Attached files")


def test_only_regular_files_no_duplicates_and_a_limit(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("a")
    got = attachments.accept([a, a, tmp_path, tmp_path / "missing.png"], [])
    assert got == [a.resolve()]
    many = [tmp_path / f"f{i}.txt" for i in range(30)]
    for f in many:
        f.write_text("x")
    assert len(attachments.accept(many, [])) == attachments.MAX_FILES


def test_nothing_attached_sends_nothing_extra():
    assert attachments.take([]) == ([], "")


_HARNESS = textwrap.dedent('''
    import json, gi, pathlib
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib
    from shani_chronoa.gui import CajitaWindow
    app = Gtk.Application(application_id="test.attach.harness")
    out = {}
    def on_activate(a):
        w = CajitaWindow(a)
        img = pathlib.Path("photo.png"); img.write_bytes(b"\\x89PNG" + b"0" * 100)
        doc = pathlib.Path("notes.pdf"); doc.write_bytes(b"%PDF" + b"0" * 50)
        out["button_label"] = w._attach_button.get_tooltip_text()
        out["bar_hidden_at_start"] = not w._attach_bar.get_visible()
        w.add_attachments([img, doc, img])
        chips = []
        c = w._attach_bar.get_first_child()
        while c:
            chips.append(c.get_child().get_label()); c = c.get_next_sibling()
        out["chips"] = chips
        out["bar_shown"] = w._attach_bar.get_visible()
        # the chip for photo.png removes it when clicked
        w._attach_bar.get_first_child().get_child().emit("clicked")
        out["after_remove"] = [p.name for p in w._attachments]
        seen = []
        w.connect("user-input", lambda _w, t: seen.append(t))
        w._input_entry.set_text("summarise this")
        w._send_button.emit("clicked")
        out["sent"] = seen[-1] if seen else None
        out["cleared"] = [p.name for p in w._attachments]
        out["bar_hidden_after_send"] = not w._attach_bar.get_visible()
        a.quit()
    app.connect("activate", on_activate)
    GLib.timeout_add(25000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
''')


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    work = tmp_path_factory.mktemp("attach")
    runtime = work / "runtime"
    runtime.mkdir()
    os.chmod(runtime, 0o700)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", XDG_RUNTIME_DIR=str(runtime),
               PYTHONPATH=str(pathlib.Path("usr/lib/shani-chronoa").resolve()))
    proc = subprocess.run([sys.executable, "-c", _HARNESS], capture_output=True, text=True,
                          timeout=120, env=env, cwd=str(work))
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[len("RESULT"):])
    pytest.fail(f"harness produced no result:\n{proc.stdout}\n{proc.stderr[-2000:]}")


def test_the_paperclip_is_there_and_the_bar_starts_hidden(win):
    assert "Attach files" in win["button_label"] and win["bar_hidden_at_start"]


def test_attached_files_show_as_chips_once_each(win):
    assert win["chips"] == ["photo.png  ✕", "notes.pdf  ✕"] and win["bar_shown"]


def test_a_chip_click_removes_that_file(win):
    assert win["after_remove"] == ["notes.pdf"]


def test_sending_carries_the_paths_then_clears_them(win):
    assert win["sent"].startswith("summarise this\n\n[Attached files")
    assert "notes.pdf (application/pdf" in win["sent"] and "photo.png" not in win["sent"]
    assert win["cleared"] == [] and win["bar_hidden_after_send"]
