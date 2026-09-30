"""The settings window shows the approval policy; it must not *be* the prompt.

Two halves, and both are about the same risk - a window that describes a policy
the app does not enforce, and a second place where Escape could mean something
different from what it means everywhere else.

- **Wired, not present.** `AGENTS.md` records two settings rows that looked
  complete and did nothing, because their gschema defaults were never read. The
  only control added here is one that revokes, and it is driven through the real
  GTK signal: fire `activated`, answer the confirmation, and check the rules are
  actually gone. A control that cannot fail is worse than no control.
- **No competing dialog.** The runtime prompt belongs to the presenter in
  `gui.py`, which resolves on the GTK thread via `GLib.idle_add`. This window
  asserts it installs no presenter and asks no question of its own, so there can
  only ever be one place where dismissal means "no".

Built in a child process against a real `Gtk.Application`, because constructing
GTK in-process leaves a main loop and a display connection behind.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

def _child(case: str, seed: str, out: dict, tmp_path, schemas, pkg: Path):
    code = (
        "import json, sys\n"
        "import gi\n"
        "gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')\n"
        "from gi.repository import Gtk, Adw, GLib\n"
        "sys.path.insert(0, %r)\n"
        "from shani_chronoa import ask_bridge, capabilities, permissions\n"
        "from shani_chronoa.config import ChronoaConfig\n"
        "def _groups(w):\n"
        "    found=[]\n"
        "    def walk(n):\n"
        "        c=n.get_first_child()\n"
        "        while c:\n"
        "            if isinstance(c, Adw.PreferencesGroup): found.append(c)\n"
        "            walk(c); c=c.get_next_sibling()\n"
        "    walk(w.get_child()); return found\n"
        "def _titles(w):\n"
        "    found=[]\n"
        "    def walk(n):\n"
        "        c=n.get_first_child()\n"
        "        while c:\n"
        "            if isinstance(c, Adw.PreferencesRow): found.append(c.get_title())\n"
        "            walk(c); c=c.get_next_sibling()\n"
        "    walk(w.get_child()); return found\n"
        "def _rows(w):\n"
        "    found={}\n"
        "    def walk(n):\n"
        "        c=n.get_first_child()\n"
        "        while c:\n"
        "            if isinstance(c, Adw.PreferencesRow):\n"
        "                sub = getattr(c, 'get_subtitle', None)\n"
        "                found[c.get_title()] = sub() if sub else None\n"
        "            walk(c); c=c.get_next_sibling()\n"
        "    walk(w.get_child()); return found\n"
        "class App(Gtk.Application):\n"
        "    def __init__(s):\n"
        "        super().__init__(application_id='test.chronoa.appr.settings')\n"
        "        s.config=ChronoaConfig(); s.window=None; s._wake_word_active=False\n"
        "    def activate_action(s,n,a): pass\n"
        "app=App(); out={'case': %r}\n"
        "def presenter(answer):\n"
        "    def show(q,o):\n"
        "        d,r=ask_bridge.make_event(); r(answer); return d\n"
        "    return show\n"
        "def on_activate(a):\n"
        "    from shani_chronoa.settings_window import SettingsWindow\n"
        "    %s\n"
        "    w = SettingsWindow(a)\n"
        "    %s\n"
        "    a.quit(); return None\n"
        "app.connect('activate', on_activate)\n"
        "GLib.timeout_add(30000, lambda: (app.quit(), False)[1])\n"
        "app.run([])\n"
        "print('RESULT'+json.dumps(out))\n"
        % (str(pkg), case, seed, out["body"])
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(pkg)
    env["GSETTINGS_SCHEMA_DIR"] = str(schemas)
    env["GSETTINGS_BACKEND"] = "keyfile"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["XDG_DATA_HOME"] = str(tmp_path / "data")
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=180, env=env)
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    # An exception inside `on_activate` is swallowed by GTK and still leaves a
    # RESULT line carrying only the keys set before it, so a short payload is a
    # crash rather than a missing assertion - and reporting it as "KeyError"
    # sends the reader looking in the wrong file.
    assert payload is not None, (
        f"harness produced no result:\n{proc.stdout}\n{proc.stderr[-3000:]}")
    assert set(payload) != {"case"}, (
        "the child raised before finishing the case:\n"
        f"{proc.stdout}\n{proc.stderr[-3000:]}")
    return payload


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """A compiled schema in a temp dir, built from the repo's own XML."""
    import shutil
    import subprocess as sp
    schemas = tmp_path_factory.mktemp("approval-settings")
    src = _REPO / "usr" / "share" / "glib-2.0" / "schemas"
    for xml in src.glob("*.xml"):
        shutil.copy2(xml, schemas)
    assert sp.run(["glib-compile-schemas", str(schemas)],
                  capture_output=True).returncode == 0
    return schemas, _REPO / "usr" / "lib" / "shani-chronoa"


def run(case, seed, body, tmp_path, env):
    schemas, pkg = env
    return _child(case, seed, {"body": body}, tmp_path, schemas, pkg)


def test_the_window_has_an_approvals_group(tmp_path, env):
    payload = run(
        "group", "pass",
        "out['groups']=[g.get_title() for g in _groups(w)]\n"
        "    out['titles']=_titles(w)\n",
        tmp_path, env)
    assert "Approvals" in payload["groups"], payload["groups"]
    assert "Answers given this session" in payload["groups"]


def test_it_asks_no_question_and_installs_no_presenter(tmp_path, env):
    """No competing dialog: this window cannot answer a permission question."""
    payload = run(
        "no-presenter", "pass",
        "out['presenter']=ask_bridge.has_presenter()\n"
        "    out['pending']=ask_bridge.pending_count()\n",
        tmp_path, env)
    assert payload["presenter"] is False, (
        "the settings window installed a presenter, so it competes with the "
        "main window for permission questions - there would then be two places "
        "where Escape could mean different things"
    )
    assert payload["pending"] == 0


def test_the_policy_it_shows_is_the_policy_that_is_enforced(tmp_path, env):
    """The row on screen must be the composed prompt, not a description of one.

    Asserted against the *rendered widget's* subtitle. An earlier version of this
    compared `_example_request()` with a rebuild of itself, which passed even
    when the page had been changed to hand-write different prose - a mutation
    confirmed it, because nothing on screen was ever read.
    """
    payload = run(
        "example", "pass",
        "req = w._example_request()\n"
        "    out['shown'] = _rows(w).get('What a question looks like')\n"
        "    out['composed'] = req.question\n",
        tmp_path, env)
    assert payload["shown"] is not None, (
        "the page no longer has a 'What a question looks like' row")
    assert payload["shown"] == payload["composed"], (
        "the settings page is showing text that is not the prompt Chronoa "
        f"actually asks:\n  shown:   {payload['shown']!r}\n"
        f"  composed: {payload['composed']!r}"
    )
    for fragment in ("permission", "session", "reason", "counts as no"):
        assert fragment in payload["shown"], (
            f"the settings page no longer shows the {fragment!r} stage: "
            f"{payload['shown']!r}"
        )


def test_every_gated_capability_says_what_a_session_grant_would_cover(tmp_path, env):
    payload = run(
        "scope", "pass",
        "out['scope']={a: w._scope_sentence(a, k) for a, k in capabilities.GATED.items()}\n",
        tmp_path, env)
    scoped = {
        "delete_file", "control_service", "manage_mount", "find_and_replace",
        # Gained a resource argument (so a scoped rule can pre-filter them),
        # which makes their session grant a grant against one path.
        "edit_file", "undo_last_change", "git_inspect",
    }
    for action, sentence in payload["scope"].items():
        assert sentence, f"no sentence for {action}"
        assert "'" in sentence and "'" in sentence
        if action in scoped:
            assert "written against" in sentence, (
                f"{action} names a target, so its grant is not 'every {action}': "
                f"{sentence!r}"
            )
        else:
            assert f"every {action}" in sentence, (
                f"{action} names no target, so its grant covers all of them and "
                f"the page says otherwise: {sentence!r}"
            )


def test_live_answers_are_shown_and_the_forget_control_actually_forgets(tmp_path, env):
    """The wiring proof: signal in, state actually changed."""
    payload = run(
        "forget",
        "ask_bridge.set_presenter(presenter(permissions.ALLOW_SESSION_CHOICE))\n"
        "    permissions.decide('delete_file', '/tmp/opencode/one.txt', 'file-delete-enabled')\n"
        "    permissions.decide('kill_process', None, 'process-kill-enabled')\n",
        "forget = [r for r in w._forget_group._rows if r.get_title()=='Forget these answers']\n"
        "    out['forget_rows'] = len(forget)\n"
        "    out['titles_before'] = [r.get_title() for r in w._forget_group._rows]\n"
        "    forget[0].emit('activated')\n"
        "    dlg = w._forget_dialog\n"
        "    out['dialog'] = type(dlg).__name__\n"
        "    out['dialog_body'] = dlg.get_body()\n"
        "    dlg.emit('response', 'cancel')\n"
        "    out['rules_after_cancel'] = [list(r) for r in permissions.rules()]\n"
        "    dlg.emit('response', 'forget')\n"
        "    out['rules_after_forget'] = [list(r) for r in permissions.rules()]\n"
        "    out['titles_after'] = [r.get_title() for r in w._forget_group._rows]\n",
        tmp_path, env)
    assert payload["forget_rows"] == 1, (
        f"the revoke control is missing: {payload['titles_before']}")
    assert len(payload["rules_after_cancel"]) == 2, (
        "cancelling the confirmation already changed the rules - the dialog is "
        "not asking anything")
    assert payload["rules_after_forget"] == [], (
        f"answering 'forget' left {payload['rules_after_forget']} on record: "
        f"the control is present but not wired")
    assert "delete_file" in payload["dialog_body"], (
        "the confirmation does not enumerate what it is about to discard")
    assert "Nothing was written to disk" in payload["dialog_body"]
    assert any("Nothing has been allowed" in t for t in payload["titles_after"]), (
        f"the list did not go back to its empty state: {payload['titles_after']}")


def test_there_is_no_revoke_control_when_nothing_was_answered(tmp_path, env):
    payload = run(
        "empty", "pass",
        "out['titles']=[r.get_title() for r in w._forget_group._rows]\n",
        tmp_path, env)
    assert not any("Forget" in t for t in payload["titles"]), payload["titles"]
    assert any("Nothing has been allowed" in t for t in payload["titles"])


def test_a_refusal_reason_is_shown_next_to_the_answer(tmp_path, env):
    payload = run(
        "reason",
        "ask_bridge.set_presenter(presenter(\n"
        "        permissions.encode_rejection('use the trash instead')))\n"
        "    permissions.decide('delete_file', '/tmp/opencode/junk.txt',\n"
        "                        'file-delete-enabled')\n",
        "out['subtitles']=[r.get_subtitle() for r in w._forget_group._rows]\n",
        tmp_path, env)
    assert any("use the trash instead" in (s or "") for s in payload["subtitles"]), (
        f"the user's reason is not on screen: {payload['subtitles']}")


def test_reopening_the_window_re_reads_the_answers(tmp_path, env):
    """The view must not be a snapshot taken once at construction.

    Driven through `set_visible()`, which is what emits the signal the window
    actually connects to, rather than by calling the handler directly.
    """
    payload = run(
        "revalidate",
        "ask_bridge.set_presenter(presenter(permissions.ALLOW_ONCE_CHOICE))\n"
        "    permissions.decide('delete_file', '/tmp/opencode/late.txt',\n"
        "                        'file-delete-enabled')\n",
        "out['before']=[r.get_title() for r in w._forget_group._rows]\n"
        "    w.set_visible(True)\n"
        "    permissions.clear(session_only=True)\n"
        "    w.set_visible(False); w.set_visible(True)\n"
        "    out['after']=[r.get_title() for r in w._forget_group._rows]\n",
        tmp_path, env)
    assert any("delete_file" in t for t in payload["before"])
    assert not any("delete_file" in t for t in payload["after"]), (
        f"the answers list still shows a rule that is no longer on record: "
        f"{payload['after']}")
    assert any("Nothing has been allowed" in t for t in payload["after"])
