"""What the Learning panel says after it writes a model.

Three defects found by running the real training path over this machine's own
16 MB `tool_calls.log`, all of them in one feature: **portability and training
feedback**.
"""

import json

import pytest


# -- the export dropped the one thing it says it carries ---------------------


class TestTheExportCarriesTheBandit:
    """**`bundle["bandit"]` was always empty, on every machine, ever.**

    `export_knowledge()` read the arms with `Bandit().arms()`. `Bandit.__init__`
    does not read the file - a fresh instance has an empty `_arms` - so the loop
    never ran. `load_bandit()` is the function that loads.

    Measured, with a bandit holding real history written to disk
    (`say` 5/5, `espeak` 5/0) and no fitted model and no conversation log:
    `load_bandit()` read both arms, a fresh `Bandit().arms()` was `{}`, and so the
    bundle came out empty and **`export_knowledge` returned `None`** - so the
    Export button told the user "there is no trained model on this machine yet"
    while the thing it wanted was sitting on disk.

    Both halves of the feature's docstring describe the arms travelling - "the
    knowledge a fresh machine cannot get any other way" - and `import_knowledge`
    promises to adopt them "even when the model is not". Neither had happened.
    """

    @staticmethod
    def _machine(tmp_path, monkeypatch):
        from shani_chronoa import learning

        monkeypatch.setattr(learning, "_path", lambda: tmp_path / "bandit.json")
        monkeypatch.setattr(learning, "models_dir", lambda: tmp_path / "models")
        return learning

    def _armed(self, learning, pulls=5):
        bandit = learning.Bandit()
        for _ in range(pulls):
            bandit.update("say", "verified")
        for _ in range(pulls):
            bandit.update("espeak", "failed")
        learning.save_bandit(bandit)
        return bandit

    def test_the_arms_are_in_the_bundle(self, tmp_path, monkeypatch):
        learning = self._machine(tmp_path, monkeypatch)
        self._armed(learning)
        bundle = tmp_path / "bundle.json"

        # First the control: the data really is readable, so a passing export
        # cannot be because there was nothing to export.
        on_disk = {n: (a.pulls, a.wins)
                   for n, a in learning.load_bandit().arms().items()}
        assert on_disk, "the fixture wrote nothing, so this test would prove nothing"

        assert learning.export_knowledge(bundle), (
            "export returned None although the bandit has arms on disk")
        carried = json.loads(bundle.read_text())["bandit"]
        assert set(carried) == set(on_disk), (
            f"the bundle carries {sorted(carried)} but the machine has "
            f"{sorted(on_disk)} - the arms did not travel")
        assert carried["say"]["pulls"] == on_disk["say"][0], carried
        assert carried["say"]["wins"] == on_disk["say"][1], carried

    def test_importing_it_receives_the_arms(self, tmp_path, monkeypatch):
        learning = self._machine(tmp_path, monkeypatch)
        self._armed(learning)
        bundle = tmp_path / "bundle.json"
        learning.export_knowledge(bundle)

        result = learning.import_knowledge(bundle, adopt=False)
        assert result.get("arms") == 2, (
            f"import saw {result.get('arms')!r} arms; the export side claims the "
            "arm table travels")

    def test_a_bare_bandit_class_still_reports_no_arms(self, tmp_path, monkeypatch):
        """**The control that would have caught it.**"""
        from shani_chronoa.learning import Bandit

        assert dict(Bandit().arms()) == {}, (
            "Bandit() now loads from disk, so the shape this bug relied on has "
            "changed - re-check whether the export still needs load_bandit()")


# -- the export's refusal names one of three reasons -------------------------


class TestTheExportRefusalNamesAllThreeReasons:
    """The Export button said one thing; there are three possible causes."""

    def test_the_message_does_not_claim_only_a_missing_model(self):
        """Asserted on the message, not on the source.

        The first version read `inspect.getsource(_export)` and failed against my
        own explanatory *comment*, which quotes the old wording verbatim. A test
        that greps a source string cannot tell a message from a note about the
        message - so the wording lives in a function and is asserted by calling it.
        """
        from shani_chronoa.gui.surfaces import learning as surface

        said = surface._nothing_to_export_sentence()
        assert "no trained model on this machine yet" not in said, (
            f"the message claims a missing model is the only reason: {said!r}")

    def test_it_names_the_three(self):
        from shani_chronoa.gui.surfaces import learning as surface

        said = surface._nothing_to_export_sentence()
        for reason in ("trained model", "bandit", "tool outcomes"):
            assert reason in said, f"the refusal never mentions {reason!r}: {said!r}"


# -- the training feedback says what the fit is worth -----------------------


class TestTrainingFeedbackCarriesTheVerdict:
    """**"Written to <path>" and nothing else, on a model that lost.**

    Measured on this machine's own log: `accuracy 0.857` against
    `baseline 0.912` - worse than always answering "unverified" - and the panel
    reported only that a file had been written. The reader concludes the model is
    good, and then `recommend()` acts on it.

    The two flags answer different questions and conflating them is the trap:
    `beats_baseline` is "wins the argmax a constant already wins 91% of the
    time"; `Report.honest()` accepts *either* that *or* a minority verdict
    detected at >= 2x on both recall and precision. Here `honest` is true
    (`verified` at 17.1x recall, 5.6x precision) and `beats_baseline` is false,
    so the model is legitimately loaded - `tools._outcome_model()` refuses only
    on `honest` being false - and the sentence has to say both.
    """

    BASE = {"accuracy": 0.857, "baseline": 0.912, "beats_baseline": False,
            "honest": True, "detected": "verified",
            "recall_lift": {"verified": 17.146},
            "precision_lift": {"verified": 5.619}}

    def test_it_says_both_the_detection_and_the_lost_argmax(self):
        from shani_chronoa.gui.surfaces import learning as surface

        said = surface._verdict_sentence({"saved": True, "provenance": self.BASE})
        assert "usable" in said, said
        assert "17.1x" in said and "5.6x" in said, said
        assert "91.2%" in said and "85.7%" in said, said
        assert "not win the argmax" in said, (
            f"the sentence omits that it lost the argmax: {said!r} - the reader "
            "is left thinking a model that loses to a constant is a good one")

    def test_a_model_that_wins_the_argmax_says_so(self):
        from shani_chronoa.gui.surfaces import learning as surface

        said = surface._verdict_sentence({"provenance": {
            **self.BASE, "accuracy": 0.95, "beats_baseline": True}})
        assert "win the argmax" in said and "not win" not in said, said

    def test_an_unusable_model_says_nothing_will_act_on_it(self):
        from shani_chronoa.gui.surfaces import learning as surface

        said = surface._verdict_sentence({"provenance": {
            "accuracy": 0.5, "baseline": 0.91, "beats_baseline": False,
            "honest": False, "detected": "", "recall_lift": {},
            "precision_lift": {}}})
        assert "not usable" in said, said

    def test_no_provenance_means_no_claim_rather_than_a_confident_one(self):
        from shani_chronoa.gui.surfaces import learning as surface

        assert surface._verdict_sentence({"saved": True}) == ""
        assert surface._verdict_sentence({}) == ""

    def test_the_written_path_is_still_reported(self):
        """The fix must not lose the part that was already right."""
        from shani_chronoa.gui.surfaces import learning as surface

        import inspect
        assert "Written to" in inspect.getsource(surface._train_outcome)


# -- the button the docstring promised ---------------------------------------


class TestImportIsWired:
    """The module docstring said "Export and import are here" beside an Export."""

    def test_the_panel_has_both_buttons(self):
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk

        Adw.init()
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.gui.surfaces import learning as surface

        app = type("App", (), {"config": ChronoaConfig()})()
        widget = surface.build(app)
        labels = []

        def walk(node):
            if isinstance(node, Gtk.Button):
                labels.append(node.get_label())
            child = node.get_first_child() if hasattr(node, "get_first_child") else None
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(widget)
        assert "Import" in labels, f"buttons are {labels}"
        assert "Export" in labels, f"buttons are {labels}"

    @pytest.mark.parametrize("result,expected", [
        ({"arms": 2, "adopted_models": 1}, "2 bandit arms adopted"),
        ({"arms": 1}, "1 bandit arm adopted"),
        ({"arms": 0, "models_usable": 3}, "refused"),
        ({"arms": 2, "note": "magick is missing here"}, "magick is missing here"),
    ])
    def test_the_refusal_is_reported_as_what_it_is(self, result, expected):
        from shani_chronoa.gui.surfaces import learning as surface

        said = surface._import_sentence(result)
        assert expected in said, f"{expected!r} missing from {said!r}"

    def test_arms_alone_is_not_reported_as_a_failure(self):
        """The arms are portable even when the model is refused - said as success."""
        from shani_chronoa.gui.surfaces import learning as surface

        said = surface._import_sentence({"arms": 2, "models_usable": 0})
        assert "2 bandit arms adopted" in said, said
        assert "no trained model" in said, said


# -- the panel can now merge, and can say what the log teaches ----------------


class TestMergeAndLessonsAreReachable:
    """Three more of the 15 never-called public names in `learning.py`.

    An AST scan for public names with no caller anywhere in `usr/` or `tests/`
    found 15 of 111. `merge_models`, `lessons` and `render_lessons` were among
    them: all fully written, all unreachable, so a machine that had logged 14,000
    tool calls could neither share what it learned nor be told what any of it
    meant.
    """

    def test_the_panel_offers_merging(self):
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk

        Adw.init()
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.gui.surfaces import learning as surface

        app = type("App", (), {"config": ChronoaConfig()})()
        labels = []

        def walk(node):
            if isinstance(node, Gtk.Button):
                labels.append(node.get_label())
            child = node.get_first_child() if hasattr(node, "get_first_child") else None
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(surface.build(app))
        assert "Merge models" in labels, f"buttons are {labels}"

    def test_a_merge_of_one_model_is_refused_with_a_reason(self):
        """Merging is not a no-op that reports success on one input."""
        from shani_chronoa.gui.surfaces import learning as surface
        import inspect

        source = inspect.getsource(surface._merge)
        assert "needs at least two" in source, (
            "merging a single model must be refused by name, not silently "
            "produce a copy of it")
        assert "No merged model" in source, source

    def test_the_merge_verdict_reports_the_lost_argmax(self):
        from shani_chronoa.gui.surfaces import learning as surface

        said = surface._merge_verdict({"from": 2, "coordinates": 268,
                                       "accuracy": 0.88, "baseline": 0.912,
                                       "beats_baseline": False,
                                       "detected": "failed"})
        assert "88.0%" in said and "91.2%" in said, said
        assert "does not win the argmax" in said, said
        assert "failed" in said, said

    def test_the_lessons_row_is_on_the_panel(self):
        """**Asserted through `build()`, not by calling the helper directly.**

        My first version called `_lessons_sentence()` and passed even with the
        row deleted from `build()` - a helper can be correct and still be
        unreachable, which is the defect this whole batch is about. So the row's
        *title* has to appear in the built widget.
        """
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw

        Adw.init()
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.gui.surfaces import learning as surface

        app = type("App", (), {"config": ChronoaConfig()})()
        texts = []

        def walk(node):
            for getter in ("get_label", "get_text"):
                fn = getattr(node, getter, None)
                if callable(fn):
                    try:
                        value = fn()
                    except Exception:  # noqa: BLE001
                        value = None
                    if isinstance(value, str) and value.strip():
                        texts.append(value)
            child = node.get_first_child() if hasattr(node, "get_first_child") else None
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(surface.build(app))
        assert any("What the log teaches" in t for t in texts), (
            "the lessons row is not on the panel; the panel's texts were "
            f"{sorted(set(texts))[:8]}")

    def test_the_lessons_row_reads_the_log(self, tmp_path, monkeypatch):
        """Both honest shapes: lessons, or the reason there are none.

        My first version asserted the word "teaches", which the empty state does
        not contain - it says "nothing the log can teach". Both are correct
        outputs, and the row must produce one of them rather than nothing.
        """
        from shani_chronoa.gui.surfaces import learning as surface

        said = surface._lessons_sentence()
        assert said, "the row said nothing at all"
        assert ("teaches" in said.lower() or "teach" in said.lower()), said

    def test_it_renders_real_lessons_from_a_real_log(self, tmp_path, monkeypatch):
        import json

        from shani_chronoa import learning
        from shani_chronoa.gui.surfaces import learning as surface

        monkeypatch.setattr(learning, "_log_path", lambda: tmp_path / "calls.jsonl")
        rows = []
        for i in range(10):
            rows.append({"tool_name": "get_clipboard", "verdict": "failed",
                         "args": {"x": 1}, "result": ""})
            rows.append({"tool_name": "calculate", "verdict": "verified",
                         "args": {}, "result": "4"})
        (tmp_path / "calls.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        said = surface._lessons_sentence()
        assert "teaches" in said.lower(), said
        assert "get_clipboard" in said, said


class TestALessonAboutAToolThisBuildDoesNotHaveIsNotALesson:
    """**`cannot liar failed 323/323` is not a sentence.**

    This machine's log has 712 records naming `liar` and `unver` - four and five
    characters, always `args: {}`, always in pairs ~80 ms apart, all
    `origin: user`. `unknown_tool_examples()` has documented exactly those two by
    name for a while ("313 of the 378 failures"), so this is **not** a new finding
    about the log; what was missing is anywhere a *person* would see it, and the
    lessons row is the first place it appears.

    The renderer turned them into lessons with the same confidence as "cannot
    get_clipboard", which is a real and useful finding. They are now named apart,
    with the reason.
    """

    FOUND = [
        {"kind": "cannot", "tool": "liar",
         "lesson": "cannot  liar  failed 323/323 here and has never succeeded",
         "action": "offer a substitute route instead"},
        {"kind": "cannot", "tool": "get_clipboard",
         "lesson": "cannot  get_clipboard  failed 5/5 here and has never succeeded",
         "action": "offer a substitute route instead"},
    ]

    def test_a_name_the_build_lacks_is_grouped_apart(self):
        from shani_chronoa import learning

        said = learning.render_lessons(self.FOUND)
        # The real finding survives.
        assert "get_clipboard" in said, said
        assert "offer a substitute route instead" in said, said
        # The nonsense does not stand as a lesson.
        lesson_lines = [ln for ln in said.splitlines()
                        if "cannot" in ln and "->" not in ln]
        assert not any("liar" in ln for ln in lesson_lines), (
            f"'cannot liar' is still presented as a lesson: {lesson_lines}")
        assert "does not have" in said, said

    def test_it_names_where_the_number_already_lives(self):
        from shani_chronoa import learning

        said = learning.render_lessons(self.FOUND)
        assert "provenance" in said, said

    def test_the_registry_is_tools_TOOLS_not_discover_skills(self):
        """**My first attempt used the wrong registry and made things worse.**

        `skills.discover_skills()` returns 152 names that do not correspond to the
        log's `tool_name` values, so it immediately reclassified the real findings
        (`delete_file`, `get_clipboard`) as unknown. There is one registry and it
        is `tools.TOOLS` - the same one `unknown_tool_examples()` uses.
        """
        from shani_chronoa import learning, tools

        known = learning._known_tool_names()
        assert known is not None
        expected = {str(e["function"]["name"]) for e in tools.TOOLS}
        assert known == expected, (
            "the resolver drifted from tools.TOOLS, which is the registry the "
            "training path and unknown_tool_examples() both use")
        for real in ("delete_file", "get_clipboard", "write_text_file"):
            assert real in known, (
                f"{real!r} is a real tool and must not be reclassified as unknown")

    def test_an_unreadable_registry_does_not_invent_lessons(self):
        """None, not an empty set - an empty set calls every tool unknown."""
        import shani_chronoa.tools as tools_module
        from shani_chronoa import learning

        original = tools_module.TOOLS
        tools_module.TOOLS = None
        try:
            assert learning._known_tool_names() is None
        finally:
            tools_module.TOOLS = original

    def test_render_lessons_still_handles_the_empty_case(self):
        from shani_chronoa import learning

        assert "No lessons yet" in learning.render_lessons([])


# -- four more of the orphaned fifteen, wired ---------------------------------


class TestTheRemainingOrphansAreReachable:
    """An AST scan for public names with no caller in `usr/` or `tests/` found 15
    of 111 in `learning.py`. Four have now been wired; these are the last three.

    Each was **fully written** - not a stub - and none was reachable, so a machine
    that had logged 14,000 tool calls could not be told whether its bandit was
    learning, how much of its history was real, or how much learned weight it
    could trust.
    """

    @staticmethod
    def _panel():
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk

        Adw.init()
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.gui.surfaces import learning as surface

        app = type("App", (), {"config": ChronoaConfig()})()
        buttons, texts = [], []

        def walk(node):
            if isinstance(node, Gtk.Button):
                buttons.append(node.get_label())
            for getter in ("get_label", "get_text"):
                fn = getattr(node, getter, None)
                if callable(fn):
                    try:
                        value = fn()
                    except Exception:  # noqa: BLE001
                        value = None
                    if isinstance(value, str) and value.strip():
                        texts.append(value)
            child = node.get_first_child() if hasattr(node, "get_first_child") else None
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(surface.build(app))
        return buttons, texts

    @pytest.mark.parametrize("title", [
        "What the log teaches",            # lessons / render_lessons
        "What this machine has seen",      # experience_summary
        "Does the bandit beat chance",     # evaluate_bandit
        "How much is trusted",             # organ_status
    ])
    def test_the_row_is_on_the_panel(self, title):
        """Asserted through `build()`, not by calling the helper."""
        _buttons, texts = self._panel()
        assert any(title in t for t in texts), f"{title!r} is not on the panel"

    def test_restoring_a_retired_model_has_a_button(self):
        buttons, _texts = self._panel()
        assert "Restore a retired model" in buttons, f"buttons are {buttons}"

    def test_the_bandit_row_reports_a_negative_result_honestly(self, monkeypatch):
        """**This machine's bandit does not beat chance, and the row must say so.**

        `evaluate_bandit()` measures it and returns `beats_chance: False` with a
        rank correlation of -0.5 over 3 ranked tools. The tempting row shows the
        arms without this, and three thin estimates then read as a ranking - which
        is the claim `evaluate_bandit` exists to stop being an assumption.
        """
        from shani_chronoa import learning
        from shani_chronoa.gui.surfaces import learning as surface

        monkeypatch.setattr(learning, "evaluate_bandit", lambda **k: {
            "measured": True, "tools_ranked": 3, "precision_at_5": 0.3333,
            "spearman_vs_empirical": -0.5, "beats_chance": False,
            "top_tools": [("delete_file", 0.896)]})
        said = surface._bandit_quality_sentence()
        assert "does NOT rank them better than chance" in said, said
        assert "-0.5" in said, said
        assert "33%" in said, said

    def test_a_bandit_that_does_beat_chance_is_told_so(self, monkeypatch):
        from shani_chronoa import learning
        from shani_chronoa.gui.surfaces import learning as surface

        monkeypatch.setattr(learning, "evaluate_bandit", lambda **k: {
            "measured": True, "tools_ranked": 12, "precision_at_5": 0.8,
            "spearman_vs_empirical": 0.6, "beats_chance": True, "top_tools": []})
        said = surface._bandit_quality_sentence()
        assert "better than chance" in said, said
        assert "does NOT" not in said, said

    def test_an_unmeasurable_bandit_is_not_a_pass(self, monkeypatch):
        from shani_chronoa import learning
        from shani_chronoa.gui.surfaces import learning as surface

        monkeypatch.setattr(learning, "evaluate_bandit",
                            lambda **k: {"measured": False, "tools_ranked": 0})
        assert "not measurable" in surface._bandit_quality_sentence()

    def test_nothing_to_trust_says_so_rather_than_zero(self, monkeypatch):
        from shani_chronoa import learning
        from shani_chronoa.gui.surfaces import learning as surface

        monkeypatch.setattr(learning, "organ_status",
                            lambda *a, **k: {"tools_with_history": 0,
                                             "trusted": 0, "doubted": 0})
        said = surface._organ_status_sentence()
        assert "none" in said.lower(), said
        assert "0 tool" not in said, (
            f"{said!r} reads as a measurement of zero rather than an absence")

    def test_organ_status_belongs_to_learning_not_inventory(self):
        """Its docstring claimed Inventory, which never mentioned it."""
        import pathlib

        from shani_chronoa import learning

        # `.parent`, not `.parents[1]`: learning.py sits at
        # <pkg>/shani_chronoa/learning.py, so the package directory is its parent.
        # My first version climbed one level too far and read a path that does not
        # exist - and `read_text` on a missing file is not the assertion I wanted.
        inventory = pathlib.Path(learning.__file__).resolve().parent / "gui" / "surfaces" / "inventory.py"
        assert inventory.is_file(), inventory
        text = inventory.read_text(encoding="utf-8")
        for claim in ("tools_with_history", "trusted", "doubted", "organ_status"):
            assert claim not in text, (
                f"inventory.py mentions {claim!r}, so the docstring's claim is "
                "now true and the row may belong there instead")

    def test_nothing_retired_says_nothing_was_retired(self, monkeypatch):
        """**Reports the absence instead of claiming success.**

        Retirement was removed on purpose, so on an ordinary machine there is
        nothing set aside and this action has nothing to do. Saying "restored" or
        staying silent would both be wrong; what it must say is that nothing was
        set aside.
        """
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        from shani_chronoa import learning
        from shani_chronoa.gui.surfaces import learning as surface

        monkeypatch.setattr(learning, "adopt_retired", lambda *a, **k: None)
        reported = []
        button = Gtk.Button()
        status = Gtk.Label()
        monkeypatch.setattr(surface, "_run_async",
                            lambda b, work, label: work(reported.append))
        surface._restore_retired(button, status)
        assert reported, "the action said nothing at all"
        assert "Nothing was set aside" in reported[0], reported

    def test_a_restored_model_is_named(self, monkeypatch, tmp_path):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        from shani_chronoa import learning
        from shani_chronoa.gui.surfaces import learning as surface

        restored = tmp_path / "outcome-abc.json"
        monkeypatch.setattr(learning, "adopt_retired", lambda *a, **k: restored)
        reported = []
        button = Gtk.Button()
        status = Gtk.Label()
        monkeypatch.setattr(surface, "_run_async",
                            lambda b, work, label: work(reported.append))
        surface._restore_retired(button, status)
        assert str(restored) in reported[0], reported
        assert "relearning" in reported[0], reported
