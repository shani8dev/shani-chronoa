"""Mutation set for the notch being *reachable*: the Shell's interface, the strip
it draws, the manifest it needs, and the seam the app goes through.

`mascot.available()` is `False` on every GNOME machine - Mutter has never
implemented `zwlr_layer_shell_v1` - so the extension `chronoa-mascot@shani.dev`
is the only companion surface this desktop can show. Three ordinary-looking
facts carry that, and each is silent when it rots:

- a method the Python client calls and the Shell's `IFACE` does not declare is
  not a feature that degrades, it is a `call_sync` that returns `None` forever;
- `NOTCH_H` is *derived*, so it cannot drift, **provided it is still derived** -
  and a typed literal describes a strip that no longer fits while every
  measurement still passes;
- `metadata.json` with no `settings-schema` is not a smaller manifest, it is a
  `getSettings()` that throws during `enable()`, so the extension never comes up.

And the seam itself: `application.py` used to ask *"is there a `_win`?"*, which is
a question about the window rather than about the companion - and is unanswerable
for a surface that has no window, which is why the extension was never started on
the desktop it exists for.

**Both directions are mutated where the decision could be read the other way.**
`create()` prefers the window (mutations 1 and 6 here) because a draggable
window is strictly more capable than a strip with no input region, and
`_NEITHER` is mutated twice - handing back `None` *and* logging only one of the
two reasons - because "no companion surface on this machine" and "nobody built
one" are different facts a reader has to be able to tell apart. The gate is
mutated back to the private attribute it replaced, and the construction site back
to the class it used to pick directly.

## Five facts here are UNMEASURABLE through this harness, and the measurement is why

**Five of the eleven mutations this set once carried could not be measured, and
they were dropped rather than patched.** All five aimed at
`usr/share/gnome-shell/extensions/chronoa-mascot@shani.dev/extension.js`, and
the refusal is `mutate.py`'s own splice guard: `_apply` calls `ast.parse(text)`
on the **whole spliced file** before writing it, so a JavaScript target cannot
pass. Measured 2026-10-17, `_apply` driven directly on a scratch copy of the real
file with each of the five mutation pairs applied:

    1  .py   APPLIED          4  .js   MalformedMutation: does not parse
    2  .py   APPLIED          5  .js   MalformedMutation: does not parse
    3  .py   APPLIED          6  .js   MalformedMutation: does not parse
                               7  .js   MalformedMutation: does not parse
                               8  .js   MalformedMutation: does not parse
    9  .json APPLIED          10 .py   APPLIED
                              11 .py   APPLIED

**The cause is the file, not the splices.** Line 1 of `extension.js` is
`// Chronoa's notch: ...`, and `//` is a comment in JavaScript and a division
in Python - the apostrophe in *Chronoa's* opens a string Python never closes, so
`ast.parse` reports `unterminated string literal (detected at line 1)` on the
**unmutated** file too. Any `.js` target is therefore unmeasurable whether or not
the mutation is well formed, and the guard's own comment already anticipates the
extensionless-Python case (`usr/bin/shani-chronoa`) while explicitly *not*
gating on the suffix - the parse is unconditional.

`metadata.json` parses as Python (a JSON object literal is a dict literal), so
mutation 9 below is measured and stays. **Editing the guard to let JavaScript
through is not available here**: it would be a change to `tools/mutate.py`
inside a notch commit, and the guard it relaxes is itself mutation-verified by
`tools/mutations/harness.py`. So the five are recorded as a gap rather than
worked around.

**What carries the facts instead: negative controls in
`tests/test_the_notch_is_reachable.py`.** Each of the five planted facts is put
into a scratch copy of `extension.js`, and the real test method is driven against
it and required to go red - so a witness is still shown to bite without the
runner being able to measure it. `TestEachDroppedMutationWouldHaveBeenCaught`
holds those five, and **each of those five** also asserts the same method passes
on the unmutated copy, which is what makes "goes red" mean something: same file,
same plumbing, only the planted fact differs. Two further cases in that class
check the helper itself in both directions - that it **raises** when a witness
does not bite, and that it does not swallow a check which is broken against the
shipped file - because five cases green for the reason this gap exists to
prevent would read as coverage of five facts nothing had measured.

**One survivor was found by the first run of this set, and it was a test of
mine rather than a gap in the code.** The "is it still derived" check first
asked only whether the expression *names* `SIZE` and `NOTCH_PAD`, so
`const NOTCH_H = SIZE + 2 * NOTCH_PAD + 12;` passed it - an expression that
mentions both terms, is still tall enough, and is a strip 12px taller than the
sprite-plus-padding it is supposed to be. That mutation is one of the five
dropped below, so the finding survives as a **negative control** rather than as a
run verdict: the same `+ 12` text is planted into a scratch copy and the check is
required to reject it. The check itself was corrected to assert the arithmetic -
the padding is on **both** edges, so the height must be the sprite plus *twice*
the padding - which is a specification of the strip rather than a copy of the
line, and survives a rewrite of it. Recorded because it is the shape this
repository keeps meeting: a check that is not vacuous, passes truthfully, and
still does not protect the thing.

**Witnesses are bare method names, not `Class::method`.** `mutate.py` builds its
verdict set from `line.split("::")[-1]`, so a qualified name compares a full id
against a bare one and marks every witness in the set stale - the
two-representations-of-one-thing trap `gaze_iris.py` records.
"""

TEST_FILE = "tests/test_the_notch_is_reachable.py"

_MSC = "usr/lib/shani-chronoa/shani_chronoa/mascot.py"
_APP = "usr/lib/shani-chronoa/shani_chronoa/app/application.py"
_META = "usr/share/gnome-shell/extensions/chronoa-mascot@shani.dev/metadata.json"

#: `create()`'s preference, as the whole two-line decision.
_PREFER_WINDOW = (
    "    ok, why = available()\n"
    "    if ok:\n"
    "        return Mascot(app)")

#: The line that makes absence a fact about the machine rather than a bug, and
#: the only place both unavailable reasons meet.
_NEITHER = (
    '    logger.info("mascot: no companion - %s; %s", why, notch_why)\n'
    "    return Mascot(app)")

#: The manifest's one line. A wrong id is worse than no id in a specific way: it
#: resolves on a user's machine and resolves nothing here.
#: **`metadata.json` is the only non-Python target this set still aims at**, and
#: it is measurable because a JSON object literal is a Python dict literal - the
#: same `ast.parse` that refuses `extension.js` accepts this. Measured, not
#: assumed: `ast.parse(open("metadata.json").read())` succeeds and `_apply`
#: writes the spliced file.
_SCHEMA = '  "settings-schema": "org.shani.chronoa"'

#: The one construction site.
_BUILD = "        self.mascot = mascot.create(self)"

#: The gate, whole line: the question the seam replaced, and the exact spelling
#: of the private attribute it must never ask again.
#: Read out of the file, not out of memory. The first version of this anchor
#: said `is not None`, which is how the *old* gate read; the shipped one says
#: `is None`, because the whole clause is negated. `--check-anchors` caught it
#: before a single test ran, which is the only reason the mistake was a
#: two-second fix rather than a mutation reported as a survivor - a needle that
#: matches nothing measures nothing and prints nothing.
_GATE = ('            if not getattr(self, "mascot", None) is None'
         ' and not self.mascot.running:')

_MUTATIONS = [

    # --- create(): which surface, and whether it returns anything -------------
    {
        "label": "the window is not preferred where a layer shell exists",
        "file": _MSC,
        "old": _PREFER_WINDOW,
        "new": (
            "    ok, why = available()\n"
            "    if ok:\n"
            "        pass"),
        "why": "The decision, reversed. The window can be dragged, clicked and "
               "moved; the notch is a strip in the Shell's composite with no "
               "input region of its own, so it is strictly less capable and "
               "wins nowhere the window can run. A `pass` here falls through "
               "to the notch whenever the extension happens to be enabled, "
               "which on this desktop is always.",
        # **One name was here and was removed:
        # `test_both_surfaces_answer_the_same_methods`.** The runner reported it
        # `NOT caught by`, correctly. It asks whether *both classes* carry the
        # shared surface, and says nothing about which one `create()` hands back
        # - so it passes with the preference fully reversed, the same shape as
        # two readers of one seam that agree because both doors run the mutated
        # line. Correct the name rather than write a test: the assertion it
        # makes is a real one and it is simply not a witness for this mutation.
        "expect": [
            "test_the_window_wins_where_a_layer_shell_exists",
        ],
    },
    {
        "label": "create() hands back nothing when neither surface is available",
        "file": _MSC,
        "old": _NEITHER,
        "new": (
            '    logger.info("mascot: no companion - %s; %s", why, notch_why)\n'
            "    return None"),
        "why": "`None` would make 'this machine has no companion surface' and "
               "'nobody built one' indistinguishable at every reader - the first "
               "is a fact about the machine, the second is a bug - and "
               "`do_shutdown`, `do_activate` and the level feed all treat "
               "absence as normal, so nothing downstream would say so.",
        "expect": [
            "test_neither_available_still_returns_an_object",
        ],
    },
    {
        "label": "only the window's refusal reason is logged",
        "file": _MSC,
        "old": _NEITHER,
        "new": (
            '    logger.info("mascot: no companion - %s", why)\n'
            "    return Mascot(app)"),
        "why": "Two different facts about the machine, and a reader who is told "
               "only that Mutter has no layer shell goes looking for a "
               "compositor plugin, while the extension being switched off is "
               "the half they could have acted on.",
        "expect": [
            "test_both_reasons_are_kept_rather_than_one",
        ],
    },

    # --- the manifest ----------------------------------------------------------
    # The five `extension.js` mutations that used to sit between here and
    # `create()` are gone: UNMEASURABLE, measured, and the docstring says so
    # with the number. What they covered is carried by the negative controls in
    # tests/test_the_notch_is_reachable.py.
    {
        "label": "the manifest names a schema this repository does not ship",
        "file": _META,
        "old": _SCHEMA,
        "new": '  "settings-schema": "org.shani.other"',
        "why": "The worse of the two failures, and the one that survives a real "
               "install: a wrong id resolves nothing on this tree and resolves "
               "*the app's real settings* on a user's machine, so `_corner()` "
               "would read a key that may not exist and the failure would look "
               "like a default rather than a manifest mistake.",
        "expect": [
            "test_it_names_a_settings_schema",
            "test_the_schema_it_names_is_the_one_this_repository_ships",
        ],
    },

    # --- the seam the app goes through -----------------------------------------
    {
        "label": "the window is constructed directly, so the notch is never chosen",
        "file": _APP,
        "old": _BUILD,
        "new": "        self.mascot = mascot.Mascot(self)",
        "why": "The state before the seam. On Mutter `Mascot.start()` returns "
               "False every time, so the one surface that works is the one that "
               "was skipped - and nothing says so, because a refused companion "
               "is the module's ordinary answer on this desktop.",
        "expect": [
            "test_it_builds_through_create",
        ],
    },
    {
        "label": "the gate asks the window's private attribute again",
        "file": _APP,
        "old": _GATE,
        "new": ('            if getattr(self, "mascot", None) is not None'
                ' and getattr(self.mascot, "_win", None) is None:'),
        "why": "The shipped gate, restored verbatim. It reads a private "
               "attribute of one of the two surfaces, so on a machine holding a "
               "`Notch` - which has no window - it answers 'not running' for "
               "ever and `start()` is never called. This is the exact line the "
               "`running` property exists to remove.",
        "expect": [
            "test_it_asks_running_rather_than_reading_the_window",
        ],
    },
]


MUTATIONS = _MUTATIONS
