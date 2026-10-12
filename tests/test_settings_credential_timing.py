"""A pasted cloud credential does not take effect now, and two of the three
groups that ask for one did not say so.

`CloudLLMChain.__init__` reads `cloud_llm_api_keys()` once and bakes each key
into the backend it constructs (`cloud_llm.py:906-935`). Exactly four things
rebuild that chain, all found by reading the callers rather than assumed:

- application startup (`app/application.py:566`),
- the privacy toggle, and only when no local model answers
  (`app/brain.py:60`),
- switching the cloud fallback on (`app/brain.py:325`),
- `set_cloud_choice("cloud")`, which does re-read the keys
  (`app/brain.py:272-289`).

Typing a key into Settings is not on that list. The row saves it, the Settings
window shows it saved, and the running chain keeps whatever it was built with.

**The group that was already right is the reason this is a copy defect and not a
new requirement.** "Free cloud providers" has always said *"Applies when the
cloud fallback next activates, not to a running session."* The two groups below
it said *"when set"* and *"when the cloud fallback is on"*, which read as
immediate - and the gap is widest on the BYOK group, because a key there is the
difference between the chain having an Anthropic/OpenAI/Google/Groq member and
having none at all.

Built against the **real** `SettingsWindow` in a child process, because a GTK
bug that only appears at construction is invisible to a unit test that reads the
file, and this repo has shipped that class - a `Gtk.Button` never wired to
anything, an API removed in GTK4, a handler that silently never ran. The child
process is also what keeps a `Gtk.Application` from leaving a main loop and a
display connection behind that upset every later test.
"""

import json
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("gi")

_HARNESS = textwrap.dedent(
    """
    import json, sys
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
    # From this file, never an absolute path: a hardcoded /home/<developer>/...
    # made a sibling subprocess unrunnable on any other machine and nothing
    # noticed, because it always worked on the machine that wrote it.
    sys.path.insert(0, sys.argv[1])
    from shani_chronoa.config import ChronoaConfig

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="test.chronoa.credentialtiming")
            self.config = ChronoaConfig()
            self.window = None
            self._wake_word_active = False
        def activate_action(self, name, arg):
            pass

    def walk(node, out):
        child = node.get_first_child()
        while child:
            walk(child, out)
            out.append(child)
            child = child.get_next_sibling()
        return out

    app = App()
    result = {}

    def on_activate(a):
        from shani_chronoa.settings_window import SettingsWindow
        w = SettingsWindow(a)
        groups = [g for g in walk(w.get_child(), [])
                  if isinstance(g, Adw.PreferencesGroup)]
        result["group_descriptions"] = {
            g.get_title(): (g.get_description() or "") for g in groups}
        a.quit()
        return False

    app.connect("activate", on_activate)
    GLib.timeout_add(20000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(result))
    """
)

#: The clause. Named, because all three descriptions carry it verbatim and a
#: rewrite that dropped it from one of them should read as a deliberate change.
EFFECT_CLAUSE = "not to a running session"

#: The groups that ask for a credential the cloud chain reads. Hand-kept, and
#: `test_the_named_groups_are_the_ones_that_were_found` checks both directions -
#: so it can fail by being incomplete *and* by having stopped matching, which is
#: what happened to the first version of the word list below.
CREDENTIAL_GROUPS = (
    "Free cloud providers",
    "Cloud providers that require a key",
    "Your own model server",
)

#: Words that make a description a credential *offer*. Read off the built
#: window's 28 groups rather than guessed: the two key groups both say "a key"
#: (never "API key" - that is the row title, not the description), and the
#: custom-server group is named by its own title.
_CREDENTIAL_WORDS = ("a key", "your own model server")

#: The one group the scan above deliberately leaves out, and why.
#:
#: "Test the keys" says *"Sends one one-token question to each provider and reads
#: what the server itself says back"* - so it contains "keys" and "server" and a
#: word list broad enough to catch the other three would catch this one too. It
#: asks for nothing: it exercises keys already stored. Telling a person that a
#: *button* takes effect on the next activation would be as wrong as the original
#: defect, in the other direction.
TESTING_GROUP = "Test the keys"


@pytest.fixture(scope="module")
def descriptions():
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS, "usr/lib/shani-chronoa"],
        capture_output=True, text=True, timeout=300)
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])["group_descriptions"]
    pytest.fail(
        "the settings window built no groups, so nothing here was measured.\n"
        f"stdout: {proc.stdout[-600:]}\nstderr: {proc.stderr[-600:]}")


class TestACredentialSaysWhenItTakesEffect:
    """An option whose label promises something the program does not do.

    This file's own recurring class. Here the promise is about a *rotated* key
    specifically: the paste succeeds, the row shows the new value, and a session
    that is already running never sees it.
    """

    def test_every_credential_group_says_when_it_takes_effect(self, descriptions):
        for title in CREDENTIAL_GROUPS:
            assert title in descriptions, (
                f"{title!r} is not on the settings window any more, so rename it "
                f"in CREDENTIAL_GROUPS. Groups that are there: "
                f"{sorted(descriptions)}")
            description = descriptions[title]
            assert EFFECT_CLAUSE in description, (
                f"{title!r} asks for a credential and does not say when it takes "
                f"effect. It reads: {description!r}. Pasted here it is saved, and "
                "the running chain keeps the key it was built with, so the copy "
                "has to say so rather than leave it to be discovered by a reply "
                "that never arrives.")

    def test_the_named_groups_are_the_ones_that_were_found(self, descriptions):
        """The reverse direction, so `CREDENTIAL_GROUPS` cannot rot.

        Both directions are needed and the first run proved it. Asserting only
        that nothing *outside* the list was found passes trivially when the scan
        finds nothing - replacing `_CREDENTIAL_WORDS` with a phrase no
        description uses left it green. An absence presented as a pass is the
        same defect as an absence presented as a failure.
        """
        found = {
            title for title, description in descriptions.items()
            if any(word in (title + " " + description).lower()
                   for word in _CREDENTIAL_WORDS)}

        unnamed = found - set(CREDENTIAL_GROUPS)
        assert not unnamed, (
            "these groups offer a cloud credential but are not in "
            f"CREDENTIAL_GROUPS, so nothing checks whether they say when it "
            f"takes effect: {sorted(unnamed)}")

        missed = set(CREDENTIAL_GROUPS) - found
        assert not missed, (
            "CREDENTIAL_GROUPS names groups the credential scan does not find, so "
            "the words above have stopped matching the real descriptions and the "
            f"check above is now vacuous: {sorted(missed)}")

    def test_the_group_that_already_said_it_is_still_saying_it(self, descriptions):
        """One group's wording is not load-bearing for the other two.

        Without this, the clause could be present only in "Free cloud
        providers" and the test above would still pass, because it reads the
        same string out of all three - so a second edit that copied it into
        just that one group would go unnoticed.
        """
        assert EFFECT_CLAUSE in descriptions["Free cloud providers"]

    def test_the_group_that_tests_keys_is_not_a_credential_group(self, descriptions):
        """The scan discriminates, rather than matching every group about keys.

        This is the false positive the word list was measured against:
        "Test the keys" contains "keys" and "server", and a word list loose
        enough to catch all three credential groups by title would catch it as
        well. It asks for nothing - it exercises keys already stored - so
        demanding the activation clause of it would be the same defect in the
        opposite direction: a copy change that is wrong in its own way.
        """
        haystack = (TESTING_GROUP + " " + descriptions[TESTING_GROUP]).lower()
        assert not any(word in haystack for word in _CREDENTIAL_WORDS), (
            f"{TESTING_GROUP!r} now matches the credential scan, so the exclusion "
            "recorded above is stale and one of the two is now wrong. Read: "
            f"{descriptions[TESTING_GROUP]!r}")