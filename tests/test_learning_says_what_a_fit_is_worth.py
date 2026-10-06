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
