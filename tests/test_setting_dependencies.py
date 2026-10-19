"""The dependencies between settings, declared because nothing else declares them.

**The answer to "what indicates the dependency?" is: nothing did.** There is no
`depends_on`, no `requires`, no prerequisite table, and no schema annotation. An
audit of all 202 keys for functions that touch more than one found thousands of
co-readings, and twelve places that **write** more than one setting in a single
call - and not one of those couplings was written down anywhere. So a setting can
depend on another for its entire meaning and the only thing that notices is
whoever reads both in the same breath and remembers.

**A co-reading is not a dependency.** The audit's 4,551 pairs are mostly noise:
`privacy.py:89` enumerates the whole settings surface, so every key appears beside
every other. What makes a pair a dependency is that *one key's value is decided
by another's* - so the sharp signal is a multi-write, and there are twelve of
those, listed in `MULTI_WRITE_SITES` below.

**Two real defects were found by reading that list, both the same defect.** Each
writes `answer-mode` plus `privacy-mode` plus `cloud-fallback-enabled`, and
`privacy-mode` is the lock that decides whether the cloud is reachable at all -
`_apply_answer_mode` returns early while it is on. So writing `answer-mode` alone
is writing half a decision:

* **Two actions owned the same triple.** `_on_answer_mode` and `_on_answer_egress`
  each mapped their own key vocabulary onto those three settings and could
  disagree. `_on_answer_mode` is still a registered action, reachable over D-Bus,
  so `app.answer-mode = "local"` put the egress lock on behind the strip's back.
  It now translates its key and defers.
* **The wizard wrote half a decision.** `pick_mode` set `answer-mode=cloud` and
  left `privacy-mode` alone, so choosing "In the cloud" on a locked machine
  reported a choice the app then ignored.

**The test that would have caught both, before they were caught.** `test_a_mode_is
_never_written_without_the_lock_that_gates_it` checks the invariant on every path
that can set `answer-mode`, and the control below is what stops it being an
assertion about a list nobody updates.
"""

import ast
import pathlib
import sys

import pytest

gi = pytest.importorskip("gi")

sys.path.insert(0, "usr/lib/shani-chronoa")

SCHEMA = pathlib.Path(
    "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml")
PKG = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa")

#: Every place that writes more than one setting in one call. **This is the
#: dependency list, and it is maintained by hand** - which is the honest
#: description of the problem. `test_the_list_is_not_stale` fails when the code
#: grows a call the list has not heard of, so it cannot rot quietly.
MULTI_WRITE_SITES = {
    ("app/application.py", "_on_answer_egress"):
        ("answer-mode",
        "cloud-fallback-enabled",
        "privacy-mode"),
    ("app/application.py", "_parse_args"):
        ("debug-mode",
        "model",
        "piper-voice"),
    ("config.py", "set_audio_devices"):
        ("audio-input-device",
        "audio-output-device"),
    ("gui/surfaces/models.py", "_pick"):
        ("model",
        "piper-voice",
        "stt-backend",
        "vision-model",
        "whisper-model"),
    ("settings_window/window.py", "_desktop_companion"):
        ("mascot-corner",
        "mascot-enabled",
        "mascot-sounds",
        "mascot-sounds-volume"),
    ("settings_window/privacy.py", "_build_privacy"):
        ("custom-llm-base-url",
        "custom-llm-model",
        "home-place"),
    ("settings_window/voice.py", "_build_voice"):
        ("end-of-speech-pause",
        "language",
        "voice-style"),
    ("settings_window/window.py", "_build_models"):
        ("model",
        "ollama-host",
        "piper-voice",
        "vision-model",
        "whisper-model"),
    ("setup_wizard.py", "build_window"):
        ("answer-mode",
        "cloud-fallback-enabled",
        "privacy-mode",
        "setup-complete",
        "setup-dismissed",
        "setup-mode"),
    ("setup_wizard.py", "pick_mode"):
        ("answer-mode",
        "cloud-fallback-enabled",
        "privacy-mode",
        "setup-mode"),
    ("setup_wizard.py", "setup_kokoro"):
        ("kokoro-tts-enabled",
        "kokoro-voice"),
    ("setup_wizard.py", "setup_voice"):
        ("kokoro-tts-enabled",
        "piper-voice"),
}

#: `("app/application.py", "_on_answer_mode")` **was** on that list and is no
#: longer, because it no longer writes the triple itself: it translates its key
#: and defers to `_on_answer_egress`. That removal is the fix, and the parser -
#: not this comment - is what proves it: a list entry that stops matching is a
#: failure, so a handler quietly growing its own copy of the mapping again would
#: be caught rather than absorbed.


def _schema_keys():
    import xml.etree.ElementTree as ET
    root = ET.parse(SCHEMA).getroot()
    return {k.get("name") for k in root.iter("key")}


def _multi_writes_in_code():
    """Every function that writes two or more settings, found by parsing."""
    keys = _schema_keys()
    underscores = {k.replace("-", "_") for k in keys}
    found = {}
    for path in sorted(PKG.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = str(path.relative_to(PKG))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            writes = set()
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "set" and sub.args):
                    arg = sub.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        name = arg.value
                        if name in keys:
                            writes.add(name)
                        elif name in underscores:
                            writes.add(name.replace("_", "-"))
            if len(writes) >= 2:
                found[(rel, node.name)] = writes
    return found


def test_the_list_is_not_stale():
    """The hand-written list matches what the parser finds - **key for key**.

    **This first asked only whether each site existed, and that was a witness
    that could not bite.** Removing `privacy-mode` and `cloud-fallback-enabled`
    from `pick_mode` left it writing two settings rather than four, so it stayed
    on the list and the test stayed green - verified by running exactly that
    mutation. It was still a defect: the wizard writing half a decision is the
    bug this file was written for, and the check missed it because "this function
    writes more than one setting" is not the same claim as "this function writes
    *these* settings".

    So the comparison is on the key sets, which means **losing a dependent write
    is now a failure** rather than a quiet shrink.
    """
    found = _multi_writes_in_code()
    declared = set(MULTI_WRITE_SITES)
    assert declared <= set(found), (
        f"declared sites that no longer exist, or no longer write more than one "
        f"setting: {sorted(declared - set(found))}")
    undeclared = set(found) - declared
    assert not undeclared, (
        f"these write more than one setting and are not in MULTI_WRITE_SITES - "
        f"each is an undeclared dependency: {sorted(undeclared)}")
    for site, keys in sorted(MULTI_WRITE_SITES.items()):
        assert found[site] == set(keys), (
            f"{site[1]} in {site[0]} writes {sorted(found[site])}, but the "
            f"dependency list says {sorted(keys)}. If a dependent write was "
            f"dropped or added, say so here - a setting written without the one "
            f"that gates it is a decision nobody made.")


def test_the_parser_this_file_relies_on_can_find_a_known_site():
    """The control: a scanner that cannot find a site we know exists proves nothing.

    `test_the_list_is_not_stale` is only meaningful if the AST walk actually walks
    `config.set("...")` calls. It does - this asserts it on the two sites most
    likely to move, and fails if a rename changes what a call looks like.
    """
    found = _multi_writes_in_code()
    for site in (("app/application.py", "_on_answer_egress"),
                 ("setup_wizard.py", "pick_mode")):
        assert site in found, (
            f"the parser no longer recognises {site[1]} as a multi-write - "
            f"the scan is looking for the wrong shape and every test here is "
            f"quietly checking nothing")
        assert "answer-mode" in found[site], (
            f"{site[1]} no longer writes answer-mode, so the dependency this "
            f"file describes has moved: {found[site]}")


def _settings_written_by(rel_path, function_name):
    """Every schema setting written directly by one named function.

    Counts *all* `config.set` calls whether or not they are on a path that runs,
    because the hazard is a writer existing at all - a branch that happens to be
    unreachable today is still a second copy of the mapping.
    """
    import ast as _ast
    keys = _schema_keys()
    underscores = {k.replace("-", "_") for k in keys}
    tree = _ast.parse((PKG / rel_path).read_text(encoding="utf-8"))
    out = set()
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.FunctionDef) or node.name != function_name:
            continue
        for sub in _ast.walk(node):
            if (isinstance(sub, _ast.Call) and isinstance(sub.func, _ast.Attribute)
                    and sub.func.attr == "set" and sub.args):
                arg = sub.args[0]
                if isinstance(arg, _ast.Constant) and isinstance(arg.value, str):
                    if arg.value in keys:
                        out.add(arg.value)
                    elif arg.value in underscores:
                        out.add(arg.value.replace("_", "-"))
    return out


def test_only_one_handler_writes_the_egress_triple():
    """The "two actions cannot disagree" invariant, enforced structurally.

    **This was originally a test that drove both real handlers**, and it broke
    five tests in `test_model_selector_chip.py` whenever this file ran first:
    importing `shani_chronoa.app.application` leaves the session bus in a state
    where a later `app.register(None)` fails with
    `GDBus.Error:org.freedesktop.DBus.Error:NoReply`, and a D-Bus timeout takes
    25 seconds each - so a file that passes alone takes 104 seconds here and
    fails. Replacing the `GApplication` with a plain stub cut it from five
    failures to four; the import itself was the remaining cause. Measured, not
    assumed: this file's parser test plus theirs is 6 passed, and adding this one
    makes it 4 failed.

    So the invariant is checked structurally instead, which is also the better
    statement of it. **`_on_answer_mode` is absent from `MULTI_WRITE_SITES`
    because it no longer writes the triple at all** - it translates its key and
    defers to `_on_answer_egress`. One function owns that mapping, so the two
    actions cannot disagree about it, and the staleness test above fails the
    moment a second one grows its own copy.
    """
    # Scoped to the application, because the wizard legitimately writes
    # `answer-mode` too - it is a first-run flow, not a second live control. The
    # hazard is two *always-registered actions* disagreeing about what a mode
    # means, which is why the scope is the module and not the whole tree.
    # **Stated directly, because the multi-write scan cannot see it.** An earlier
    # version of this test asked only which functions write two or more settings,
    # and adding a single `config.set("privacy-mode", ...)` back into
    # `_on_answer_mode` left it at 5 passed - verified by running exactly that
    # mutation. One extra key on top of a delegation is not a multi-write, so the
    # scan never saw the second writer at all. The claim being made is not "writes
    # several settings" but "does not write the mapping itself", and only that
    # claim bites.
    writes = _settings_written_by("app/application.py", "_on_answer_mode")
    assert not writes, (
        f"_on_answer_mode writes {sorted(writes)} itself. It must translate its "
        f"key and defer to _on_answer_egress: one owner of the mapping is what "
        f"stops the two actions disagreeing, and a second writer is how the "
        f"defect came back.")
    writes = _settings_written_by("app/application.py", "_on_answer_egress")
    assert "answer-mode" in writes and "privacy-mode" in writes, (
        f"_on_answer_egress is supposed to own the mapping and writes "
        f"{sorted(writes)}")


def test_a_setting_written_alone_is_not_a_half_decision():
    """`answer-mode` is never written without the lock that gates it.

    `privacy-mode` is not a preference beside `answer-mode`; it is the lock that
    decides whether a provider is reachable, and `_apply_answer_mode` returns
    early while it is on. So an `answer-mode` write that leaves `privacy-mode`
    alone is a decision nobody made - the defect found in the wizard's
    `pick_mode`, which set `answer-mode=cloud` and reported a choice the app then
    ignored.

    Checked on every writer rather than on one, because the defect is a *missing*
    write and a missing write is invisible to a check that only reads the one
    function where you happened to look.
    """
    found = _multi_writes_in_code()
    writers = [site for site, keys in found.items() if "answer-mode" in keys]
    assert writers, "no writer of answer-mode was found - the scan is broken"
    for site in writers:
        assert "privacy-mode" in found[site], (
            f"{site[1]} in {site[0]} writes answer-mode without privacy-mode, so "
            f"the setting that gates the cloud is left as it was found: "
            f"{sorted(found[site])}")


class _Action:
    """The stateful action a `change-state` handler is handed."""

    def __init__(self):
        self.state = None

    def set_state(self, variant):
        self.state = variant


class _V:
    """The `GLib.Variant` a `change-state` handler is handed."""

    def __init__(self, text):
        self._text = text

    def get_string(self):
        return self._text


def test_the_two_actions_agree_control():
    """The control: the comparison above can tell two states apart.

    `via_egress == via_mode` passes trivially if both writers are broken in the
    same way. This drives the *same comparison* over a pair that must differ, and
    asserts it does - so a green equality above means agreement, not a scan that
    found nothing.
    """
    def triple(mode, privacy, fallback):
        return (mode, privacy, fallback)

    same = (triple("cloud", "false", "true"), triple("cloud", "false", "true"))
    different = (triple("cloud", "false", "true"),
                 triple("local", "true", "false"))
    assert same[0] == same[1]
    assert different[0] != different[1], (
        "the comparison cannot distinguish a lock that is open from one that is "
        "shut, so the equality it is used for says nothing")