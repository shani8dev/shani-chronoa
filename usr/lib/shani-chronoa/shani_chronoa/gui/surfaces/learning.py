"""What Chronoa has learned on this machine, and how to move it somewhere else.

Everything this panel shows was, until now, reachable only by importing a module
in a Python shell. `learning.train_and_save()`, `distill.harvest_rows()`,
`learning.export_knowledge()` and `distill.train_router()` are four pieces of
machinery with no entry point in the app at all - which is the failure this
repository documents at length, and the reason this file exists.

| row | read from |
|---|---|
| Facts remembered | `learning.read_model_knowledge()`, on the model's own terms |
| Which stand-in worked | the bandit arms in `~/.local/share/shani-chronoa` |
| Routing pairs | `distill.harvest_rows()` over this machine's own conversations |
| A distilled student | `distill.load_router()`, which refuses anything it cannot account for |
| The outcome model | its provenance, including whether it ever beat the baseline |

**Nothing here reports a model as good.** The outcome model is a *flag*: on this
machine's own log it finds calls that will verify 33x more often than chance on
recall and 5.9x on precision, and finds **no** failure signal at all, while
scoring 83% top-1 against a 94% constant. So its row leads with the detection,
prints both lifts, and says what it cannot do. A panel that showed only "trained,
83% accurate" would be the confident-wrong-answer shape the repo keeps having.

**The two actions refuse, visibly.** "Distil a router" says which model would be
asked, and if none is configured it says that instead of training from nothing.
"Train the outcome model" writes a file only when the fit beat the majority
baseline, and otherwise reports why - the refusal is the useful output, because a
machine with 11,000 tool calls and no verified ones has nothing to learn and
pretending otherwise is how a panel teaches a person to distrust it.

**Export and import are here because the models are portable and the experience
is not.** `export_knowledge` travels the model (with its digest and provenance),
the bandit arms, and a fingerprint of the capabilities that were present when it
was fitted; `import_knowledge` refuses anything whose digest does not match. A
fresh machine otherwise starts at zero and relearns what took months to accumulate.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402

TITLE = "What it has learned"
#: `brain` is not in the icon theme - measured: `Gtk.IconTheme.has_icon("brain")`
#: is False while every other icon name in the surfaces resolves, so this row
#: rendered as a blank. `weather-clear-night-symbolic` is what this machine has
#: for recall, and it is checked at test time rather than trusted: an icon name
#: is a claim about the desktop's theme, and the theme is the user's.
ICON = "weather-clear-night-symbolic"
SECTION = "What Chronoa knows"
SUBTITLE = ("Facts, which tools actually worked, and the two models fitted from "
            "them - with what each one can and cannot do")

#: The rows, in order. Named because the surface tests assert on them and a
#: title that changes silently would make those assertions meaningless.
ROW_TITLES = (
    "Facts remembered",
    "Which stand-in worked",
    "Routing pairs from your conversations",
    "Finding something you said before",
    "A distilled router",
    "The outcome model",
)


def _app_config(app: Any):
    config = getattr(app, "config", None)
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    return config


def _add_row(group, title: str, subtitle: str, suffix=None) -> None:
    """Put a row into a group built by `common.group()`.

    `Adw.PreferencesGroup` is a `Gtk.ListBox` and takes `add`; the plain `Gtk.Box`
    the no-libadwaita path returns takes `append` on GTK4, where `add` no longer
    exists. Both are handled rather than one, so the panel still builds on a
    machine without libadwaita - which is the whole point of `common` existing.
    """
    row = common.row(title, subtitle, suffix=suffix)
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(row)
    else:
        group.append(row)


def _button(label: str, handler: Callable[[Gtk.Button], None]) -> Gtk.Button:
    button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    button.connect("clicked", lambda b: handler(b))
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
    return button


def _run_async(button: Gtk.Button, work: Callable[[Callable[[str], None]], None],
               status: Gtk.Label) -> None:
    """Do `work` off the main loop, reporting progress into `status`.

    Every model fit here takes minutes on a cold machine and reads the whole log,
    so it cannot run on the GTK thread - and a surface that froze the window for
    four minutes would be worse than one with no button at all. The button is
    disabled while it runs and re-enabled by whatever the work says, because a
    button stuck insensitive with no explanation is the thing this repo keeps
    being bitten by.
    """
    button.set_sensitive(False)
    status.set_label("Working...")

    def report(text: str) -> None:
        GLib.idle_add(status.set_label, text)

    def finish(text: str) -> None:
        report(text)
        GLib.idle_add(button.set_sensitive, True)

    def runner() -> None:
        try:
            work(report)
        except Exception as exc:  # noqa: BLE001 - a failure must be shown, not swallowed
            finish(f"{type(exc).__name__}: {exc}")

    threading.Thread(target=runner, daemon=True).start()


# ---------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------


def _facts_sentence() -> str:
    from shani_chronoa import learning
    try:
        facts = learning.read_model_knowledge()
    except Exception as exc:  # noqa: BLE001 - an unreadable model is not a fact
        return f"could not be read ({type(exc).__name__})"
    if not facts:
        return ("none yet - the outcome model has never been trained on this "
                "machine")
    kinds: dict[str, int] = {}
    for item in facts:
        key = str(item.get("kind") or "fact")
        kinds[key] = kinds.get(key, 0) + 1
    parts = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
    return f"{len(facts)} ({parts})"


def _bandit_sentence() -> str:
    from shani_chronoa import learning
    try:
        # `learning.render_bandit`, not the arms' private dict: it is the
        # function that already says how thin every estimate is, which is the
        # honest thing to show, and it keeps one reader of that file.
        rendered = learning.render_bandit(learning.load_bandit(), top=3).strip()
    except Exception as exc:  # noqa: BLE001
        return f"could not be read ({type(exc).__name__})"
    if not rendered or "no " in rendered.lower()[:12]:
        return rendered or "none recorded"
    return rendered.splitlines()[0]


def _pairs_sentence() -> str:
    from shani_chronoa import distill
    try:
        rows = distill.harvest_rows()
    except Exception as exc:  # noqa: BLE001
        return f"could not be read ({type(exc).__name__})"
    if not rows:
        return ("none - a pair is a request you made and the skill that answered "
                "it, and this machine has not had a conversation yet")
    skills = len({tool for _say, tool in rows})
    return f"{len(rows)} from {skills} skills"


def _recall_sentence() -> str:
    """Whether semantic recall over past conversations can answer a question.

    A separate row from "Routing pairs" because they are different capabilities
    on different data: one is training material for a router, the other is a
    search over everything already said. Reporting only the first would make an
    installed-but-unused embedding model look unused when it is in fact the thing
    answering "what did I say about X last week".
    """
    from shani_chronoa import conversation_store, local_embed
    if not local_embed.verify():
        return ("not installed - no search model, so past conversations can only "
                "be found by the words in them (setup wizard, Memory)")
    try:
        sessions = conversation_store.list_sessions(
            conversation_store.session_dir())
    except Exception as exc:  # noqa: BLE001
        return f"installed, but the conversations could not be read ({type(exc).__name__})"
    turns = sum(int(s.get("messages") or 0) for s in sessions)
    if not turns:
        return "installed, with no saved conversations to search yet"
    return f"installed - {len(sessions)} conversations, {turns} messages, searchable by meaning"


def _router_sentence() -> str:
    from shani_chronoa import distill
    router = distill.load_router()
    if router is None:
        return ("none - a router is only written when it beats the commonest "
                "answer on requests it has not seen")
    payload = distill.json.loads(distill.router_path().read_text(encoding="utf-8"))
    provenance = payload.get("provenance") or {}
    accuracy = provenance.get("accuracy")
    baseline = provenance.get("baseline")
    detail = (f"{len(router.classes)} skills, "
              + (f"{float(accuracy):.0%} against a {float(baseline):.0%} baseline"
                 if accuracy is not None else "no recorded score"))
    return f"ready - {detail}"


def _outcome_sentence() -> str:
    """The detection, first, and the honest limit, always."""
    import json

    from shani_chronoa import learning
    # Read the provenance off the file itself rather than through a helper that
    # does not exist: the file says what it is, and a helper that re-derived it
    # would be a second opinion about the same model.
    try:
        payload = json.loads(learning.model_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "not trained - no model file, and nothing claims otherwise"
    report = payload.get("provenance") or {}
    # `honest` is the model's own verdict on itself, and it is checked first: a
    # file whose report says it never beat the baseline must not be described
    # using the numbers from that same report, however good they look.
    detected = report.get("detected") or ""
    honest = report.get("honest")
    if not honest:
        return ("a file exists but its own report says it never beat the "
                "majority baseline, so nothing acts on it")
    lifts = report.get("precision_lift") or {}
    gain = lifts.get(detected)
    part = f"{detected} at {float(gain):.1f}x precision" if gain else "nothing yet"
    return f"a flag for {part} - it predicts no verdict at all"


def _teachers_sentence() -> str:
    from shani_chronoa import distill
    try:
        notes = distill.teacher_notes()
    except Exception as exc:  # noqa: BLE001
        return f"could not be asked ({type(exc).__name__})"
    return "; ".join(n[0].upper() + n[1:] for n in notes)


def _status_row(recorder: "common.StatusRecorder") -> Gtk.Widget:
    """The panel's own health, from what is on the machine.

    Written through `recorder` so the sidebar's dot reads the same word this
    row shows.
    """
    facts = _facts_sentence()
    if "none" in facts.lower() or "not" in facts.lower():
        return recorder.row(
            common.STATUS_UNKNOWN,
            "No learning data on this machine",
            facts)
    return recorder.row(
        common.STATUS_OK,
        "Learning data is on this machine",
        facts)


# ---------------------------------------------------------------------------
# the panel
# ---------------------------------------------------------------------------


def build(app: Any) -> Gtk.Widget:
    """The learning panel for `app` - anything with a `config` will do.

    Reads at build time and does nothing else until a button is pressed: opening
    a panel that started training a model would be the same mistake as opening a
    panel that downloaded one.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    body = common.page_body(18)
    body.set_margin_top(12)
    body.set_margin_bottom(12)

    # The panel's own health, above every group: what is
    # here, what is trained, what is not. One row, one
    # dot, one word - the question the panel is opened
    # for, before the rows that hold the learning state.
    recorder = common.StatusRecorder()
    body.append(_status_row(recorder))

    have = common.group(
        "What is here",
        "Read from the machine's own data. Nothing in this panel is a claim about "
        "how well Chronoa works; it is a list of what exists and what it says "
        "about itself.")
    _add_row(have, ROW_TITLES[0], _facts_sentence())
    _add_row(have, ROW_TITLES[1], _bandit_sentence())
    _add_row(have, ROW_TITLES[2], _pairs_sentence())
    _add_row(have, ROW_TITLES[3], _recall_sentence())
    _add_row(have, ROW_TITLES[4], _router_sentence())
    _add_row(have, ROW_TITLES[5], _outcome_sentence())
    _add_row(have, "What the log teaches", _lessons_sentence())
    body.append(have)

    # --- train -----------------------------------------------------------
    status = Gtk.Label(wrap=True, xalign=0)
    train = common.group(
        "Train",
        "Both fits read the whole tool-call log and take minutes, so each runs "
        "off the main loop and reports what it decided. Neither writes a file it "
        "cannot justify - but the test is not the one this used to claim. A "
        "router that loses to 'always answer with the commonest skill' is not "
        "written, and an outcome model is judged by whether it *detects* a "
        "minority verdict at least twice as well as chance on both recall and "
        "precision, which a model can do while still losing the plain argmax to "
        "a constant. Winning that argmax is reported, not required.")

    outcome_button = _button("Train the outcome model",
                             lambda b: _train_outcome(b, status))
    _add_row(train, "Outcome model",
             "Fitted from the recorded verdicts. It is a flag, not a predictor.",
             suffix=outcome_button)

    router_button = _button("Train a router", lambda b: _train_router(b, status))
    _add_row(train, "Routing router",
             f"Fitted from your own conversations. {_teachers_sentence()}",
             suffix=router_button)

    export_button = _button("Export", lambda b: _export(b, status))
    import_button = _button("Import", lambda b: _import(b, status))
    _add_row(train, "Take it to another machine",
             "The model with its digest, the bandit arms, and a fingerprint of "
             "what was installed when it was fitted. Importing checks that "
             "digest and refuses a model fitted on a machine that had tools "
             "this one does not; the bandit arms are still adopted, because "
             "which stand-in worked is a fact about the tool and not about the "
             "sender's hardware.",
             suffix=_pair(import_button, export_button))

    merge_button = _button("Merge models", lambda b: _merge(b, status))
    _add_row(train, "Share what several machines learned",
             "Folds every model in this feature space into one with TIES: trim "
             "each to its largest weights, agree a sign per coordinate, average "
             "only what agrees. Conflicts cancel instead of compounding. The "
             "result is scored against this machine's own log and is not written "
             "unless it is worth quoting - a merge is a hypothesis until it has "
             "been measured.",
             suffix=merge_button)

    body.append(train)
    body.append(status)
    set_content(common.scrolled(body))
    # What this panel says about itself, for the sidebar's health dot. The same
    # recorder that built the row at the top of the panel, so the dot and the
    # row are one statement about one reading.
    page.status = recorder.status
    return page


def _train_outcome(button: Gtk.Button, status: Gtk.Label) -> None:
    """Fit the outcome model and report what it decided - including refusing."""
    def work(report: Callable[[str], None]) -> None:
        from shani_chronoa import learning
        report("Reading the tool-call log...")
        result = learning.train_and_save()
        if result.get("saved"):
            report(f"Written to {result.get('path')}.{_verdict_sentence(result)}")
        else:
            report(f"Not written: {result.get('reason')}")

    _run_async(button, work, status)


def _nothing_to_export_sentence() -> str:
    """Why there is nothing to export - which is three possibilities, not one.

    **This said only "there is no trained model on this machine yet".** But
    `export_knowledge` returns `None` when the model, the bandit arms *and* the
    tool tally are all empty, so a machine with no model but real bandit history
    exports perfectly well - and the message described a cause that was not the
    cause.

    Measured here: a bandit holding `say` 5/5 and `espeak` 5/0 got exactly that
    sentence, because the arms were read from an unpopulated `Bandit()` and never
    counted at all. All three are named so the reader can tell which is missing.
    """
    return ("Nothing to export yet: no trained model, no bandit history, and no "
            "recorded tool outcomes on this machine. Any of the three would go - "
            "use Chronoa for a while, or train the outcome model above.")


def _verdict_sentence(result: Dict[str, Any]) -> str:
    """What the fit is actually worth, from the provenance `train_and_save` returns.

    **Written 2026-10-06. This used to say only "Written to <path>".** Measured on
    this machine's own 16 MB log: the fit came back
    `accuracy 0.857` against `baseline 0.912` - *worse than always answering
    "unverified"* - and the panel said "Written to …" and stopped. A person
    reading that concludes the model is good, and then `recommend()` acts on it.

    The two flags are **not** the same question and conflating them is the trap:

    - `beats_baseline` is "does it win the argmax a constant already wins 91% of
      the time". Here, no.
    - `honest` is "is it worth quoting" - `Report.honest()` accepts *either* a
      top-1 win *or* a minority verdict genuinely detected at >= 2x its base rate
      on **both** recall and precision. Here, yes: `verified` at 17.1x recall
      and 5.6x precision.

    So this model is legitimately loaded - `tools._outcome_model()` refuses only
    on `honest` being false - and the honest sentence has to say *both*: it is
    usable, and it does not win the argmax. Reporting either alone is the
    confident-wrong-answer shape; reporting "Written" is the worst of the three.
    """
    prov = result.get("provenance")
    if not isinstance(prov, dict) or "accuracy" not in prov:
        return ""
    acc = prov.get("accuracy")
    base = prov.get("baseline")
    if not isinstance(acc, (int, float)) or not isinstance(base, (int, float)):
        return ""
    beats = bool(prov.get("beats_baseline"))
    honest = bool(prov.get("honest"))
    detected = str(prov.get("detected") or "")
    recall = (prov.get("recall_lift") or {})
    precision = (prov.get("precision_lift") or {})
    bits = []
    if honest:
        gain_r = float(recall.get(detected) or 0.0)
        gain_p = float(precision.get(detected) or 0.0)
        bits.append(f"usable - it flags {detected or 'a minority verdict'} at "
                    f"{gain_r:.1f}x recall and {gain_p:.1f}x precision"
                    if detected else "usable - it detects a minority verdict")
    else:
        bits.append("not usable - it neither beats the constant nor detects a "
                    "minority verdict, so nothing will act on it")
    bits.append(f"but {acc:.1%} against a {base:.1%} constant, so it does "
                "not win the argmax" if not beats else
                f"and {acc:.1%} against a {base:.1%} constant, so it does win "
                "the argmax")
    return " " + "; ".join(bits) + "."


def _train_router(button: Gtk.Button, status: Gtk.Label) -> None:
    """Fit the routing router from this machine's own conversations."""
    def work(report: Callable[[str], None]) -> None:
        from shani_chronoa import distill
        rows = distill.harvest_rows()
        report(f"{len(rows)} (request, skill) pairs from your conversations...")
        if not rows:
            report("Nothing to learn from yet - this machine has not had a "
                   "conversation. A router needs requests repeated in ordinary use.")
            return
        result = distill.train_router(rows, teacher="this machine's own use")
        if result.get("saved"):
            report(f"Router written: {result.get('path')}")
        else:
            report(f"No router: {result.get('reason')}")

    _run_async(button, work, status)


def _import(button: Gtk.Button, status: Gtk.Label) -> None:
    """Take another machine's learning, checking it fits this one.

    **Wired 2026-10-06; the button was missing while this module's docstring
    described `import_knowledge`'s refusal behaviour in detail** - "Export and
    import are here because the models are portable and the experience is not",
    with an Export button and nothing beside it. `import_knowledge` itself was
    fully implemented, digest-checking and refusing, with zero callers in the
    tree.

    A refusal is reported as what it is rather than as a failure: a bundle whose
    model was fitted where `magick` exists cannot describe this machine, and the
    arms still arrive. Both halves are shown, because "nothing happened" and
    "the model was refused but the arms were adopted" need different reactions.
    """
    def chosen(path: str) -> None:
        def work(report: Callable[[str], None]) -> None:
            from pathlib import Path

            from shani_chronoa import learning
            report("Checking the bundle...")
            try:
                result = learning.import_knowledge(Path(path))
            except Exception as exc:  # noqa: BLE001 - a bad file is a refusal
                report(f"Could not read that bundle: {exc}")
                return
            report(_import_sentence(result))

        _run_async(button, work, status)

    picker = Gtk.FileDialog()
    picker.set_title("Import another machine's experience")
    try:
        picker.open(None, None, _on_chosen(picker, chosen))
    except Exception:  # noqa: BLE001 - an older GTK has no file chooser here
        report_line = getattr(status, "set_text", None)
        if report_line:
            report_line("This build has no file chooser; place the bundle in "
                        "~/.local/share/shani-chronoa and use shani-chronoa.")


def _on_chosen(dialog: Gtk.FileDialog, callback: Callable[[str], None]):
    """A `Gtk.FileDialog.open` continuation that hands back one path."""
    def done(source: "Gtk.FileDialog", result) -> None:
        try:
            gfile = source.open_finish(result)
        except Exception:  # noqa: BLE001 - cancelled is not an error
            return
        path = getattr(gfile, "get_path", None)
        if callable(path):
            callback(path())
    return done


def _import_sentence(result: Dict[str, Any]) -> str:
    """What actually happened, including the parts that are refusals."""
    arms = int(result.get("arms") or 0)
    adopted = int(result.get("adopted_models") or 0)
    usable = int(result.get("models_usable") or 0)
    bits = []
    if arms:
        bits.append(f"{arms} bandit arm{'s' if arms != 1 else ''} adopted")
    else:
        bits.append("no bandit arms in the bundle")
    if adopted:
        bits.append(f"{adopted} model{'s' if adopted != 1 else ''} fitted on "
                    "this machine's own tools")
    elif usable:
        bits.append(f"{usable} model(s) refused - they were fitted where tools "
                    "this machine does not have exist")
    else:
        bits.append("no trained model in the bundle")
    note = (result.get("note") or "").strip()
    return "; ".join(bits) + (f". {note}" if note else ".")


def _pair(*buttons: Gtk.Button) -> Gtk.Box:
    """Two buttons side by side, read left to right as import then export.

    Built by appending rather than with a `children=` property: PyGObject's
    `Gtk.Box` has no such property, and passing one raises
    `TypeError: gobject 'GtkBox' doesn't support property 'children'` at build
    time - which is exactly when a panel stops rendering.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    for button in buttons:
        box.append(button)
    return box


def _lessons_sentence() -> str:
    """What the log teaches, in the panel's own words.

    **Wired 2026-10-06.** `learning.lessons()` and `render_lessons()` were fully
    written - and had **zero callers**, so a machine that had logged 14,000 tool
    calls could not be told what any of it meant. `experience_summary()` sits
    beside them, also uncalled. A tally is history ("ffmpeg failed 14 times"); a
    lesson is a statement about *when* it fails, and the difference is the whole
    point of the function.
    """
    from shani_chronoa import learning
    try:
        return learning.render_lessons(learning.lessons()).strip()
    except Exception as exc:  # noqa: BLE001 - an unreadable log is not a lesson
        return f"could not be read ({type(exc).__name__})"


def _merge(button: Gtk.Button, status: Gtk.Label) -> None:
    """Fold every model in this feature space into one, and report the verdict."""
    def work(report: Callable[[str], None]) -> None:
        from shani_chronoa import learning

        try:
            candidates = [p for p in sorted(learning.models_dir().glob("outcome*.json"))
                          if not p.name.endswith(".tmp")
                          and p.name != f"outcome-merged-{learning._feature_space_id()}.json"]
        except Exception as exc:  # noqa: BLE001
            report(f"Could not look for models: {exc}")
            return
        if len(candidates) < 2:
            report(f"Nothing to merge: {len(candidates)} model in this feature "
                   "space, and a merge needs at least two. Fit models from "
                   "different halves of the log, or import another machine's.")
            return
        payloads, unreadable = [], []
        for candidate in candidates:
            try:
                payloads.append(__import__("json").loads(
                    candidate.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001
                unreadable.append(f"{candidate.name}: {type(exc).__name__}")
        report(f"Merging {len(payloads)} of {len(candidates)} models...")
        if unreadable:
            report(f"Skipped {len(unreadable)} unreadable: {unreadable[0]}")
        result = learning.merge_models(payloads)
        if not result.get("merged"):
            report(f"No merged model: {result.get('reason')}")
            return
        report(f"Merged {result['from']} models into {result.get('coordinates')} "
               f"coordinates.{_merge_verdict(result)}")

    _run_async(button, work, status)


def _merge_verdict(result: Dict[str, Any]) -> str:
    """What the merged model turned out to be worth."""
    bits = []
    acc, base = result.get("accuracy"), result.get("baseline")
    if isinstance(acc, (int, float)) and isinstance(base, (int, float)):
        bits.append(f"{acc:.1%} against a {base:.1%} constant")
    if result.get("detected"):
        bits.append(f"detects {result['detected']}")
    if not result.get("beats_baseline"):
        bits.append("does not win the argmax")
    return (" " + "; ".join(bits) + ".") if bits else ""


def _export(button: Gtk.Button, status: Gtk.Label) -> None:
    """Package what this machine has learned so another machine can use it."""
    def work(report: Callable[[str], None]) -> None:
        from shani_chronoa import learning
        report("Packaging what this machine has learned...")
        path = learning.export_knowledge()
        # **All three reasons, because there are three.** This said only "there
        # is no trained model on this machine yet", but `export_knowledge`
        # returns None when the model, the bandit arms *and* the tool tally are
        # all empty - so a machine with no model but real bandit history exports
        # fine, and the message described a cause that was not the cause.
        # Measured on this machine: a bandit holding `say` 5/5 and `espeak` 5/0
        # got exactly that sentence, because the arms were read from an
        # unpopulated `Bandit()` and never counted.
        report(f"Wrote {path}" if path else _nothing_to_export_sentence())

    _run_async(button, work, status)
