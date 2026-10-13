"""Nothing public in the package is called by nothing, without a written reason.

`AGENTS.md` holds the repo's standard: **no *undocumented* orphans**. This file is
what makes that checkable, and it exists because three separate blind spots in a
hand-run version of this audit produced four wrong answers in a row.

## The three blind spots, each now a control

**1. Dotted calls were discarded.** A candidate was declared unreached because the
audit searched `[^a-z_.]name(` — and that character class *excludes the dot*, so
`screen_stream.gstreamer_ready()` looked exactly like a definition. Every
module-qualified call in the tree was invisible.

**2. `usr/bin/` was invisible.** The launchers have **no `.py` extension**, so a
`--include=*.py` scan skipped all of them. Four functions were reported unreached
that the launchers call: `gstreamer_ready`, `render_for_log`, `evaluate_many`,
`fire_all`. The correct count for `gstreamer_ready` is one caller, in
`usr/bin/shani-chronoa-screen-stream`.

**3. The defining file was subtracted.** Subtracting `{own_file}` from the
references throws away *intra-module* calls, which is where most of a small
module's helpers are used. This was the largest single error: **267 apparent
orphans collapsed to 40** once it was dropped, because an `ast.FunctionDef` keeps
its name as a string and is therefore never an `ast.Name` node — so any hit at all
is a real reference, and no subtraction is needed.

A fourth was found by running this: **code embedded in a string**. A test that
executes a child process passes the child's source as a triple-quoted string, so
`prctl_set_seccomp_attempt` was called at `tests/test_sandbox_seccomp.py:384` and
invisible to AST walking. String constants are now recorded twice — whole, for
table dispatch and `getattr` — and tokenised, for code inside them. That removed
thirteen more false positives, finishing at **26**.

## What this does and does not claim

It does not claim the list is complete. It claims that **this set** is the set of
unreferenced public callables **this method** finds, and that a new one fails. A
dispatch mechanism outside AST reach — `exec`, a plugin looked up by string at
runtime in another process, C code — would still hide here.

**The scan method is itself tested.** `test_the_scan_can_still_see_*` below are
negative controls: each one *fails* if the corresponding blind spot comes back. A
guard that silently stops detecting is worse than no guard, because it reads as
green.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "usr/lib/shani-chronoa"

sys.path.insert(0, str(PKG))

#: Directories that are not this project's source.
_SKIP = ("cache/", ".git/", "__pycache__", "node_modules", "harness-study/")

_TOKEN = re.compile(r"[A-Za-z_]\w*")

#: A `def NAME` inside an embedded script is a *definition*, not a reference.
#: Found by running this: two tests embed a child that defines its own
#: `log_message`, the tokeniser counted it, and `log_message` stopped being an
#: orphan for no reason. The same rule that makes a real `FunctionDef` invisible
#: has to apply inside a string.
_DEFINITION = re.compile(r"\b(?:def|class)\s+([A-Za-z_]\w*)")

#: A string is treated as **code** - and therefore tokenised - only when it carries
#: a marker that prose does not have. Found by running this: `distill.distil()` is
#: called by nothing anywhere, and the guard did not report it, because the runtime
#: message `"...there is no teacher to distil from"` is an ordinary string constant
#: and tokenising it produced a reference to `distil`.
#:
#: So tokenising *every* string trades a false negative for prose that says a
#: function's name out loud. The markers below are what an embedded script or a
#: table lookup actually contains; a sentence to a person contains none of them.
_CODE_MARKER = re.compile(r"(?:\b(?:def|class|import|lambda)\s|\(\s*\)"
                          r"|\w+\s*=\s*\w+\s*\(|^\s*\w+\.\w+\()")


def _tokens_of(source):
    """Identifier tokens in a string, minus any that are *only* definitions.

    Returns nothing for a string with no code marker in it, so prose naming a
    function is not a reference to it.

    "Only" matters: a name that is both defined and used in the same embedded
    script is a reference, and dropping it would be a false negative.
    """
    if not _CODE_MARKER.search(source):
        return set()
    counts = {}
    for match in _TOKEN.finditer(source):
        counts[match.group()] = counts.get(match.group(), 0) + 1
    defined = {}
    for match in _DEFINITION.finditer(source):
        defined[match.group(1)] = defined.get(match.group(1), 0) + 1
    return {name for name, seen in counts.items()
            if defined.get(name, 0) < seen}

#: Decorators whose function body is not the interesting part.
_SKIP_DECORATORS = ("property", "cached_property", "abstractmethod", "override")

#: Unreferenced public callables, each with why it is allowed to stay.
#:
#: **Every entry carries a reason, and a reason of "" is a failure.** An allowlist
#: of bare names is how an audit becomes a rubber stamp: the names stop being
#: examined, and the next genuine orphan joins them by being copied.
#:
#: `learning.py` belongs to another session working in parallel. Its cluster is
#: listed here so the guard is green today and so the list is *visible* rather than
#: being an unexplained block of failures.
ALLOWED = {
    # --- dispatched by a framework, never by name -----------------------
    "do_GET": "http.server.BaseHTTPRequestHandler calls it for every GET; no code "
              "in this tree can, by construction.",
    "do_POST": "the same - BaseHTTPRequestHandler's POST hook.",
    "log_message": "the same - BaseHTTPRequestHandler's logging hook.",

    # --- another session's area: learning.py ---------------------------
    "bayesian": "learning.py - the concurrent session's active area.",
    "contextual": "learning.py - the concurrent session's active area.",
    "credible_interval": "learning.py - the concurrent session's active area.",
    "decaying_epsilon_greedy": "learning.py - the concurrent session's active area.",
    "episode": "learning.py - the concurrent session's active area.",
    "iqr_outliers": "learning.py - the concurrent session's active area.",
    "is_anomalous": "learning.py - the concurrent session's active area.",
    "is_informative": "learning.py - the concurrent session's active area.",
    "kl_ucb": "learning.py - the concurrent session's active area.",
    "observe": "learning.py - the concurrent session's active area.",
    "ranking": "learning.py - the concurrent session's active area.",
    "retire_model": "learning.py - the concurrent session's active area.",
    "seasonality": "learning.py - the concurrent session's active area.",
    "trend": "learning.py - the concurrent session's active area.",
    "ucb1": "learning.py - the concurrent session's active area.",
    "ucb_tuned": "learning.py - the concurrent session's active area.",
    "utility": "learning.py - the concurrent session's active area.",
    "utility_auction": "learning.py - the concurrent session's active area.",
    "z_scores": "learning.py - the concurrent session's active area.",

    # --- examined and deliberately left unwired -------------------------
    "default_teacher": "distill.py: 'Kept as the historical entry point'. The "
                       "live entry is `distil()`, which `tools/distill_run.py` "
                       "calls - the measurement runner, deliberately off-session "
                       "rather than a GUI action.",
    "dependents_of": "goals.py: superseded by `downstream_of`.",
    "fence_all": "provenance.py: the only bulk-ish site, assistant.py:483, "
                 "fences per message conditionally (role check, already-fenced "
                 "check) and rebuilds the dict; `(content, source) -> Fenced` "
                 "cannot express that.",
    "highpass": "fmdsp.py: a filter primitive beside `bandpass`, which is used. "
                "Library surface with no caller yet.",
    "list_voices": "tts.py: the Voice panel's `_voice_catalogue` does this.",
    "load_dict": "event_rules.py: `BackoffPolicy` is a stateless calculator - "
                 "`EventRule._policy()` rebuilds it from the rule's own counters "
                 "on every call. Loading into it would write to a throwaway.",
    "resample_to": "fmdsp.py: `resample()` already reduces the fraction by gcd "
                   "itself (fmdsp.py:32), so this wrapper adds nothing.",
    "section_ids": "settings_window/window.py: all 20 SETTINGS_TARGETS name "
                   "*families*, which `family_ids()` already serves; nothing "
                   "addresses a group by id.",
    "style_clause": "prompt_pieces.py: style assembly goes through "
                    "`get_prompt`/`ALL_PIECES`.",
    "verify_digest": "model_registry.py: the concurrent session's in-flight "
                     "work. AGENTS.md calls it 'the only enforcement-shaped "
                     "orphan left'.",
}


#: This file. Excluded from the *reference* pass, for a reason found by running it:
#: `ALLOWED` is a dict of string keys, so every name on the list appeared
#: "referenced" by the list itself, and `test_the_allowlist_is_not_stale` failed
#: with all 26 names in it. An audit that counts itself is not an audit.
_SELF = "test_no_unwired_public_helper.py"



#: Unreferenced **private** callables. Kept separate from `ALLOWED` because the
#: bar is different: a private name is allowed to be spare far more often, and
#: merging the two lists would make the public one look arbitrary.
#:
#: Probed by asking what the guard deliberately skips: **0 unreferenced module
#: constants** of 3557, and 14 unreferenced private functions of 3652.
PRIVATE_ALLOWED = {

    "_as_declared": "merge.py: remains of `gguf-py`'s typed writer, which this "
        "module deliberately stopped using - its own docstring records that the "
        "typed path 'lost 15 of 31 keys on a real Qwen3 file'. The metadata "
        "section is now copied verbatim via `kv_section_bytes`.",
    "_probe_writer": "merge.py: the same removed typed-writer path. A throwaway "
        "GGUFWriter to validate key packing, for a writer nothing calls.",

    "_is_integer_rational": "fmdsp.py: `resample()` chooses its decimation path "
        "on `up == 1`, not on `down % up == 0` - and it is right to. A 1/2 ratio "
        "is a rational number but is not pure decimation, so using this would be "
        "a bug.",

    "_tool_options": "tool_select.py: a one-line accessor for "
        "`_tool_facts(tool).options`. `ranked()` scores against `facts.options` "
        "directly (with the `option_df <= 3` rarity guard), so nothing needs the "
        "accessor. Checked, not assumed: the enum words are read.",
    "_read_plan_mode": "gui/window.py: one line wrapping `planmode.is_enabled()`, "
        "which the module already calls directly at three other sites - so the "
        "reason the docstring gives, 'not through ChronoaConfig', is being "
        "honoured without the wrapper.",

    "_forward_delay": "skills/bridge_topology.py: reads a bridge's STP forward "
        "delay. Nothing in that module reads it, and `_read_int` exists there "
        "only to serve it. STP timers are not meaningful for the single bridge a "
        "lab network has, so surfacing one would be noise.",

    "_remote": "skills/cloud_files.py: parses `remote:path/to/dir`. No call site "
        "uses that spelling.",
    "_udisks": "skills/manage_mount.py: runs `udisksctl status`; the module "
        "invokes udisksctl itself at its own three sites.",
    "_lsusb": "senses/devices.py: runs `lsusb`; the module shells out to lsusb at "
        "its own sites.",
    "_characteristics": "skills/bluetooth_gatt.py: filters GATT rows to "
        "characteristics; the module builds its own row lists.",
    "_numeric": "skills/solve_math.py: result formatting against `_digits()`.",
    "_type_keys": "ui_driver.py: types text on the best route without checking "
        "it arrived. Its own docstring says `type_text` does not use it 'because "
        "it must *verify*', and no caller that only wants 'the keys were sent' "
        "exists - so this is a spare entry point, not a missing capability.",
    "_sidecars": "skills/apply_patch.py: reports `.rej` and `.orig`. The report "
        "is written inline at the call site and is **narrower** - it lists `.rej` "
        "only, deliberately: a `.orig` is the pre-patch backup, which is good "
        "news rather than something to worry about.",
}


#: Unreferenced **public classes**, with the same two properties as `ALLOWED`.
#:
#: Probed after the callables and the constants: **0 unreferenced enum members** of
#: 33, and 22 unreferenced classes of 380 - **19 of them in `learning.py`**, the
#: concurrent session's area. So three were real candidates and one of those
#: (`speech_gate.Busy`) turned out to document a raise nothing performed.
CLASS_ALLOWED = {
    "IndexError_": "file_index.py: 'A file we will not index, with the reason.' The "
        "behaviour exists and is better than an exception would be - the walk "
        "records `problems` (with the OSError text) and `declined` (named "
        "directories), so a skipped file is reported rather than raised about.",
    "Task": "goals.py: 'CAMEL's Task shape, reduced to what a local model will "
        "actually use.' `Step` is the shape the goal engine uses; this is the "
        "vestige of the upstream framework it was written against.",
}


#: `learning.py`'s unreferenced classes. Listed by name, and the test asserts the
#: file still yields some, so a rename cannot make the exclusion silently empty.
#: Same category as `verify_digest`: the concurrent session's active area.
_LEARNING_NAMES = {
    "AssociationRules",
    "BetaBernoulli",
    "ChangePoint",
    "EWMA",
    "Experience",
    "Frequency",
    "Gaussian",
    "Hierarchical",
    "MonteCarlo",
    "MovingAverage",
    "NaiveBayes",
    "OnlineKMeans",
    "OnlineLogistic",
    "PassiveAgregressive",
    "PolynomialRegression",
    "Preferences",
    "RollingWindow",
    "Tabular",
    "classification",
}


def _sources():
    """Every file that could hold a reference.

    **`.py` *and* the extension-less `usr/bin/` launchers.** Blind spot 2.
    """
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        text = str(path)
        if any(skip in text for skip in _SKIP):
            continue
        if path.name == _SELF:
            continue
        if path.suffix == ".py" or (path.parent.name == "bin" and "usr/" in text):
            yield path


def _docstring_lines(tree):
    """Line numbers occupied by a docstring, so prose is not a reference."""
    spans = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                spans.update(range(body[0].lineno, body[0].end_lineno + 1))
    return spans


def _referenced_names():
    """`{name: {files}}` for every name the tree refers to."""
    used = {}
    for path in _sources():
        try:
            text = path.read_text(errors="replace")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError):
            names = set(_TOKEN.findall(path.read_text(errors="replace")))
            for name in names:
                used.setdefault(name, set()).add(str(path))
            continue

        skip = _docstring_lines(tree)
        names = set()
        for node in ast.walk(tree):
            if getattr(node, "lineno", None) in skip:
                continue
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, ast.alias):
                names.add((node.asname or node.name).split(".")[-1])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)                    # getattr / table dispatch
                names.update(_tokens_of(node.value))  # code inside a string
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                names.update(node.names)
        for name in names:
            used.setdefault(name, set()).add(str(path))
    return used


def _defined_public(used):
    """`{name: "file:line"}` for public callables nothing refers to."""
    orphans = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            if any((getattr(d, "id", None)
                    or getattr(getattr(d, "func", None), "id", None))
                   in _SKIP_DECORATORS for d in node.decorator_list):
                continue
            # **No own-file subtraction.** Blind spot 3.
            if used.get(node.name):
                continue
            orphans.setdefault(node.name, []).append(
                f"{path.relative_to(ROOT)}:{node.lineno}")
    return orphans


def _defined_private(used):
    """`{name: "file:line"}` for private callables nothing refers to.

    **One underscore, dunders excluded.** `_read_int` appears in three unrelated
    modules, so a private name is only ever a candidate within its own file - the
    collision is the point of the convention.
    """
    orphans = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if not name.startswith("_") or name.startswith("__"):
                continue
            if any((getattr(d, "id", None)
                    or getattr(getattr(d, "func", None), "id", None))
                   in _SKIP_DECORATORS for d in node.decorator_list):
                continue
            if used.get(name):
                continue
            orphans.setdefault(name, []).append(
                f"{path.relative_to(ROOT)}:{node.lineno}")
    return orphans


def _defined_classes(used):
    """`{name: "file:line"}` for public classes nothing refers to."""
    orphans = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name.startswith("_") or used.get(node.name):
                continue
            orphans.setdefault(node.name, []).append(
                f"{path.relative_to(ROOT)}:{node.lineno}")
    return orphans


def _orphans():
    return _defined_public(_referenced_names())


def _class_orphans():
    return _defined_classes(_referenced_names())


def _private_orphans():
    return _defined_private(_referenced_names())


# --- the audit ----------------------------------------------------------------

def test_no_public_helper_is_called_by_nothing():
    orphans = _orphans()
    unexpected = sorted(set(orphans) - set(ALLOWED))

    assert not unexpected, (
        f"{len(unexpected)} public callable(s) that nothing in the tree "
        f"references:\n"
        + "\n".join(f"  {name}  ({', '.join(orphans[name])})" for name in unexpected)
        + "\n\nEither wire it, delete it, or add it to ALLOWED with a reason.")


def test_every_allowed_name_carries_a_reason():
    """A bare-name allowlist is a rubber stamp: the names stop being examined and
    the next genuine orphan joins them by being copied."""
    without = sorted(name for name, why in ALLOWED.items() if not why.strip())

    assert not without, (
        f"ALLOWED entries with no reason: {without}. An allowlist entry is a claim "
        f"that someone looked; an empty string is not that.")


def test_the_allowlist_is_not_stale():
    """**And in the other direction.** A name that has become reachable should
    leave the list, or the list stops being evidence of anything."""
    orphans = _orphans()
    gone = sorted(set(ALLOWED) - set(orphans))

    assert not gone, (
        f"ALLOWED names that are now referenced again: {gone}. Remove them - an "
        f"allowlist that only ever grows stops tracking reality.")


# --- the controls: each fails if its blind spot returns ----------------------

def test_the_scan_can_still_see_a_dotted_call():
    """Blind spot 1. `gstreamer_ready` is called as
    `screen_stream.gstreamer_ready()` - dotted, so a `[^a-z_.]name(` search misses
    it and reports it unreached, which is exactly what happened."""
    orphans = _orphans()

    assert "gstreamer_ready" not in orphans, (
        "a dotted call is invisible again: the scan is treating `module.name(` as "
        "a definition")


def test_the_scan_can_still_see_the_launchers():
    """Blind spot 2. The `usr/bin/` launchers have no `.py` extension."""
    sources = [str(p) for p in _sources()]
    launchers = [s for s in sources if s.endswith("shani-chronoa-screen-stream")]

    assert launchers, (
        "the extension-less launchers are not being scanned; four functions are "
        "called only from there")


def test_the_scan_can_still_see_code_embedded_in_a_string():
    """Blind spot 4, found by running this. A test executes a child whose source
    is a triple-quoted string, so `prctl_set_seccomp_attempt` is called from
    `tests/test_sandbox_seccomp.py:384` and AST walking alone cannot see it."""
    orphans = _orphans()

    assert "prctl_set_seccomp_attempt" not in orphans, (
        "code inside a string is invisible again: string constants are no longer "
        "being tokenised")


def test_the_scan_does_not_count_prose_for_a_reference():
    """Blind spot 5, found by running this. `distill.distil()` is called by
    `tools/distill_run.py`, and before the string tokeniser learned to look for a
    code marker the guard reported `distil` as *uncalled* - because the runtime
    message `"...there is no teacher to distil from"` is an ordinary string
    constant, and every token in a string was being read as a reference.

    So tokenising every string trades a false negative for prose that happens to
    say a function's name. `distil` is the case that proved it.

    **This tests the tokeniser, not `distil`.** Asserting "`distil` is not an
    orphan" has the wrong polarity: with the marker gate removed, prose would
    manufacture a reference for `distil` and the assertion would still pass -
    which is precisely the failure it is meant to catch.
    """
    assert _tokens_of("no language model is configured, so there is no teacher "
                      "to distil from. ") == set(), (
        "a sentence naming a function is being read as a reference to it")

    embedded = ("\nimport ctypes, errno\n"
                "from shani_chronoa.sandbox import seccomp\n"
                "rc, err = seccomp.prctl_set_seccomp_attempt()\n")
    tokens = _tokens_of(embedded)

    assert "prctl_set_seccomp_attempt" in tokens, tokens
    assert "seccomp" in tokens, tokens

    # A definition inside an embedded script is not a reference to it - which is
    # what let `log_message` hide behind seven test-local definitions.
    only_defining = "class H:\n    def log_message(self, fmt):\n        pass\n"

    assert "log_message" not in _tokens_of(only_defining), \
        _tokens_of(only_defining)

    # ... but a name that is both defined and used still counts.
    both = "def log_message(self):\n    pass\nhandler.log_message = log_message\n"

    assert "log_message" in _tokens_of(both), _tokens_of(both)


def test_the_scan_reaches_the_rest_of_the_repository():
    """`distil()` is called from `tools/distill_run.py` - outside `usr/` and
    `tests/`, which is where the hand-run version of this audit kept stopping."""
    assert "distil" in _referenced_names(), (
        "the reference pass no longer sees tools/, so it would report `distil` - "
        "which distil_run.py calls - as uncalled")


def test_the_scan_can_still_see_an_intra_module_call():
    """Blind spot 3. `modelfmt.role_for` is called only by `modelfmt.catalog`, in
    its own module - which is why subtracting the defining file reported 267
    orphans instead of 26."""
    orphans = _orphans()

    for name in ("role_for", "backend_for", "dominant_tone", "read_iq_wav"):
        assert name not in orphans, (
            f"{name} is referenced only from its own module, and the scan is "
            f"discarding intra-module references again")

def test_no_private_helper_is_called_by_nothing():
    """**The gap this audit found.** Probing what the public check deliberately
    skips: 0 unreferenced module constants of 3557, and 14 unreferenced private
    functions of 3652. The first of those 14 was the bwrap/seccomp decision whose
    answer was carried by indentation alone."""
    orphans = _private_orphans()
    unexpected = sorted(set(orphans) - set(PRIVATE_ALLOWED))

    assert not unexpected, (
        f"{len(unexpected)} private callable(s) that nothing references:\n"
        + "\n".join(f"  {name}  ({', '.join(orphans[name])})" for name in unexpected)
        + "\n\nEither wire it, delete it, or add it to PRIVATE_ALLOWED with a "
          "reason.")


def test_every_private_allowlist_entry_carries_a_reason():
    without = sorted(name for name, why in PRIVATE_ALLOWED.items()
                     if not why.strip())

    assert not without, f"PRIVATE_ALLOWED entries with no reason: {without}"


def test_both_audits_actually_find_their_allowlisted_orphans():
    """**The control for a scan that goes blind.**

    `test_no_private_helper_is_called_by_nothing` computes `unexpected` inside the
    test and asserts it is empty - so neutering that computation to `[]` made the
    test pass. Worse, the realistic version of the same failure is a *scan* that
    quietly returns `{}`: the test would then be asserting nothing at all and read
    as green.

    So both scans are required to find the orphans they are known to contain. If
    the reference pass loses a directory, the definition walk stops matching, or
    the dunder filter changes shape, this fails instead of passing quietly.
    """
    public, private = _orphans(), _private_orphans()

    missing_public = sorted(set(ALLOWED) - set(public))
    assert not missing_public, (
        f"the public scan no longer finds {missing_public}, so it has gone blind - "
        f"the 'no unwired helper' test would now pass on an empty result")

    missing_private = sorted(set(PRIVATE_ALLOWED) - set(private))
    assert not missing_private, (
        f"the private scan no longer finds {missing_private}, so it has gone "
        f"blind for the same reason")

    # And both are non-trivial: an audit that found nothing at all has stopped
    # reading the tree, which is indistinguishable from a clean tree.
    assert len(public) >= 20, f"the public scan found only {len(public)}"
    assert len(private) >= 10, f"the private scan found only {len(private)}"


def test_the_private_allowlist_is_not_stale():
    orphans = _private_orphans()
    gone = sorted(set(PRIVATE_ALLOWED) - set(orphans))

    assert not gone, (
        f"PRIVATE_ALLOWED names that are referenced again: {gone}. Remove them.")

def test_no_public_class_is_referenced_by_nothing():
    """**The class the speech-gate fix came from.** 22 unreferenced of 380, 19 of
    them in `learning.py` - so the list is small, and one of the three real
    candidates (`speech_gate.Busy`) was a docstring promising a raise that nothing
    performed."""
    orphans = _class_orphans()
    unexpected = sorted(set(orphans) - set(CLASS_ALLOWED) - _LEARNING_NAMES)

    assert not unexpected, (
        f"{len(unexpected)} public class(es) nothing references:\n"
        + "\n".join(f"  {name}  ({', '.join(orphans[name])})"
                    for name in unexpected))


def test_every_class_allowlist_entry_carries_a_reason():
    without = sorted(name for name, why in CLASS_ALLOWED.items() if not why.strip())

    assert not without, f"CLASS_ALLOWED entries with no reason: {without}"


def test_the_class_allowlist_is_not_stale():
    orphans = _class_orphans()
    gone = sorted(set(CLASS_ALLOWED) - set(orphans))

    assert not gone, f"CLASS_ALLOWED names referenced again: {gone}. Remove them."


def test_the_class_audit_can_still_see_learning_py():
    """`learning.py` is excluded by name, so the exclusion is asserted rather than
    assumed - a rename would otherwise quietly stop excluding it, or start
    excluding something else."""
    orphans = _class_orphans()
    in_learning = {n for n, where in orphans.items()
                   if any("learning.py" in w for w in where)}

    assert in_learning, (
        "no unreferenced class in learning.py at all - either the concurrent "
        "session wired them, or this audit has stopped reading that file")

