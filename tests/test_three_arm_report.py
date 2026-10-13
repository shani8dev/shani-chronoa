"""`tools/three_arm.py`'s report: the form half and the choice half, kept apart.

**Why they must not be combined.** The enforcement probe measured that a grammar
whose schema allows exactly one value makes the model return that value *even
when it has already decided against it* - a question about the weather came back
as `get_datetime`. So the constraint moves **whether a tool was called** and
leaves **which tool** where it was.

A single "score" cannot show that, and the two readings imply opposite decisions:
"the grammar did nothing" argues against adopting it, while "the grammar made the
answer well-formed without making it right" argues that adopting it is safe and
pointless. Reporting one number loses the distinction that the whole experiment
exists to make.

So `score_arm` returns four values and the two halves are printed as columns.
These tests hold it to that, including the part it is easiest to get wrong: a
row the *service* failed is not a row the model got wrong.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("three_arm", ROOT / "tools" / "three_arm.py")
three_arm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(three_arm)


@pytest.fixture(autouse=True)
def _no_server_ever_starts(monkeypatch):
    """**A test file that can accidentally start the five-hour run is a hazard
    for whoever adds the next test to it.**

    Found by mutation: the mutation that makes `--report` start a server instead
    of reading a file was *caught* - by a 300-second timeout, because the
    mutated path went on to launch a real `llama-server` and run the whole eval.
    A witness that can only fire by hanging is a witness nobody can act on, and
    a file where that is one keystroke away is worse.

    So every path that would start a server returns immediately instead. The
    `test_it_starts_no_server` witness is unaffected: it patches `Popen` and
    asserts on whether it was called, which is decided long before this.
    """
    monkeypatch.setattr(three_arm, "_wait", lambda *a, **k: False)


def row(tool=False, args=False, seen="no tool", unavailable=False):
    return {"id": "c", "tool": tool, "args": args, "seen": seen,
            "unavailable": unavailable}


class TestFormAndChoiceAreSeparateNumbers:
    def test_a_call_that_was_wrong_counts_as_form_but_not_choice(self):
        acted, tool, args, n = three_arm.score_arm(
            [row(seen="get_weather({})")])
        assert acted == 1, "the model called a tool; that is form"
        assert tool == 0, "but the wrong one, so it is not choice"
        assert n == 1

    def test_a_correct_call_counts_as_both(self):
        acted, tool, args, n = three_arm.score_arm(
            [row(tool=True, args=True, seen="get_datetime({})")])
        assert (acted, tool, args) == (1, 1, 1)

    def test_no_call_is_neither(self):
        assert three_arm.score_arm([row()]) == (0, 0, 0, 1)

    def test_form_can_rise_while_choice_stays_flat(self):
        """**The finding, reproduced on rows rather than on a model.** Three
        rows the constrained arm would answer with a wrong-but-well-formed call
        and three it would leave unanswered: form goes 0 -> 3 and choice does
        not move at all. A combined score calls that a gain."""
        base = [row() for _ in range(3)]
        constrained = [row(seen="get_weather({})") for _ in range(3)]
        b = three_arm.score_arm(base)
        c = three_arm.score_arm(constrained)
        assert c[0] - b[0] == 3, "form should have risen"
        assert c[1] - b[1] == 0, "choice should not have moved"
        assert (c[2] - b[2]) == 0


class TestTheDenominatorIsCasesTheModelActuallySaw:
    """A throttled provider is not a wrong answer, and counting it as one makes
    a broken arm look like a weak one."""

    def test_an_unavailable_row_leaves_the_denominator(self):
        acted, tool, args, n = three_arm.score_arm(
            [row(tool=True, args=True, seen="get_datetime({})"), row(unavailable=True)])
        assert n == 1, "the failed row is still in the denominator"

    def test_an_unavailable_row_is_not_counted_as_a_miss(self):
        acted, tool, args, n = three_arm.score_arm([row(unavailable=True)])
        assert (acted, tool, args, n) == (0, 0, 0, 0), (
            "a service failure was scored as the model failing")

    def test_all_unavailable_is_an_empty_population_not_a_zero_score(self):
        """Reported as 0/0 rather than 0/N, so a run that never reached the model
        cannot be read as a model that answered nothing."""
        assert three_arm.score_arm([row(unavailable=True)] * 4)[3] == 0


class TestTheArmsAndThePopulationAreDeclared:
    def test_the_three_arms_are_the_ones_the_entry_prescribes(self):
        assert three_arm.ARMS == ("+greedy", "+greedy+constrained", "+greedy+request")

    def test_the_arms_are_runnable_configs(self):
        header = (ROOT / "tools" / "task_eval.py").read_text(encoding="utf-8")
        header = header.split("def load_cases")[0]
        for arm in three_arm.ARMS:
            assert arm in header, f"{arm} is not documented in the task_eval header"

    def test_the_population_is_read_from_the_file_not_from_the_spec(self):
        """The defect this file exists alongside: `--cases` was split and the
        *strings* counted, so a run printed `5 cases` against a 225-case file
        with three of those ids real."""
        import json as _json
        cases = _json.loads((ROOT / "tools" / "eval_cases.json").read_text())["cases"]
        assert len(cases) > 100, "the eval file shrank; this test needs the real one"
        chosen = [c for c in cases if c["id"] in three_arm.cases_of(three_arm.CASES)]
        assert len(chosen) < len(three_arm.cases_of(three_arm.CASES)), (
            "every id in the default case list now exists, so the "
            "strings-vs-cases defect cannot show - keep this honest")
        assert chosen, "the default case list matches nothing"

class TestAFinishedRunCanBeReReportedWithoutReRunningIt:
    """**A per-arm JSON file is worth nothing if reading it costs the run.**

    The arms are run sequentially into per-arm files precisely so a timeout in
    the last one does not lose the first two - and the process that is running
    right now loaded this module *before* the paired comparison existed, so its
    own console output is the old report. Improving the analysis must not cost
    five hours of inference to apply.

    So `--report PATH` loads a finished run and prints from it: no server, no
    model, no question asked of anything. The refusal cases matter as much as
    the happy one, because a re-report tool that cannot say "I could not read
    that" would print a header over nothing.
    """

    def _run_file(self, tmp_path, arms=("+greedy", "+greedy+constrained",
                                        "+greedy+request"), n=20):
        import json as _json
        merged = {}
        for i, arm in enumerate(arms):
            merged[arm] = {"rows": [
                {"id": "c%d" % k, "tool": k < 10 + i, "args": k < 10 + i,
                 "seen": "" if k < 10 + i else "no tool", "unavailable": False,
                 "seconds": 1.0, "schemas": 8, "reply": "x"}
                for k in range(n)]}
        path = tmp_path / "run.json"
        path.write_text(_json.dumps(merged))
        return path

    def test_a_finished_run_is_reported(self, tmp_path, capsys):
        path = self._run_file(tmp_path)
        assert three_arm.main(["--report", str(path)]) == 0
        out = capsys.readouterr().out
        assert "+greedy+constrained" in out and "+greedy+request" in out, out
        assert "re-reporting" in out, out

    def test_the_population_is_derived_from_the_rows_not_remembered(self, tmp_path,
                                                                   capsys):
        """A header that says 220 because something once said 220 is a stale
        label on a measurement."""
        path = self._run_file(tmp_path, n=37)
        three_arm.main(["--report", str(path)])
        out = capsys.readouterr().out
        assert "37 cases" in out, out
        assert "220" not in out, out

    def test_it_starts_no_server(self, tmp_path, monkeypatch, capsys):
        """The property the whole flag exists for: asking the model again is the
        thing it must not do."""
        started = []

        class _Proc:
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass

        monkeypatch.setattr(three_arm.subprocess, "Popen",
                            lambda *a, **k: started.append(a) or _Proc())
        three_arm.main(["--report", str(self._run_file(tmp_path))])
        assert not started, "a server was started to re-read a finished run"

    def test_a_missing_file_is_refused_by_name(self, tmp_path, capsys):
        path = tmp_path / "never-written.json"
        assert three_arm.main(["--report", str(path)]) == 2
        assert str(path) in capsys.readouterr().out

    def test_a_file_holding_no_arm_of_this_run_says_which_arms_it_knew(self,
                                                                      tmp_path,
                                                                      capsys):
        path = tmp_path / "other.json"
        path.write_text('{"something-else": {"rows": []}}')
        assert three_arm.main(["--report", str(path)]) == 2
        out = capsys.readouterr().out
        assert "+greedy+constrained" in out, (
            "the refusal must name the arms it was looking for, or the reader "
            "has to guess what shape the file should have:\n" + out)

    def test_a_two_arm_run_is_still_called_a_two_arm_run(self, tmp_path, capsys):
        path = self._run_file(tmp_path, arms=("+greedy", "+greedy+constrained"))
        assert three_arm.main(["--report", str(path)]) == 0
        assert "only 2 of 3 arms ran" in capsys.readouterr().out

    def test_it_does_not_need_the_cases_file_to_exist(self, tmp_path, monkeypatch,
                                                      capsys):
        """`--cases` resolution reads eval_cases.json; a re-report must not, or
        it inherits a dependency on a file the run did not produce."""
        monkeypatch.setattr(three_arm, "REPO", tmp_path / "nowhere")
        assert three_arm.main(["--report", str(self._run_file(tmp_path))]) == 0
