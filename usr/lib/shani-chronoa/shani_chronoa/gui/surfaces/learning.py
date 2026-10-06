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
from typing import Any, Callable

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
    body.append(have)

    # --- train -----------------------------------------------------------
    status = Gtk.Label(wrap=True, xalign=0)
    train = common.group(
        "Train",
        "Both fits read the whole tool-call log and take minutes, so each runs "
        "off the main loop and reports what it decided. Neither writes a file it "
        "cannot justify: a router that loses to 'always answer with the "
        "commonest skill' is not written, and an outcome model that does not beat "
        "the majority baseline is not either.")

    outcome_button = _button("Train the outcome model", lambda b: _run_async(
        b, _train_outcome, status))

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
    _add_row(train, "Take it to another machine",
             "The model with its digest, the bandit arms, and a fingerprint of "
             "what was installed when it was fitted.", suffix=export_button)

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
            report(f"Written to {result.get('path')}")
        else:
            report(f"Not written: {result.get('reason')}")

    _run_async(button, work, status)


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


def _export(button: Gtk.Button, status: Gtk.Label) -> None:
    """Package what this machine has learned so another machine can use it."""
    def work(report: Callable[[str], None]) -> None:
        from shani_chronoa import learning
        report("Packaging what this machine has learned...")
        path = learning.export_knowledge()
        report(f"Wrote {path}" if path else
               "Nothing to export - there is no trained model on this machine yet")

    _run_async(button, work, status)
