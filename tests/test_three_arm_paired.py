"""The paired comparison, and the denominator it is allowed to use.

**Why this file exists at all.** The three-arm run answers one question: does
the grammar do the work, or does the format request? The marginal table answers
"what fraction was right", which on 220 cases carries roughly six points of
noise regardless of the arms - so a real four-point effect reads as a tie and a
tie reads as a result, and those are opposite decisions about adopting
constrained decoding.

Every arm sees **the same cases**, so the evidence is the handful that changed
hands. That is a paired comparison, and this file is its witnesses.

**The trap this file is shaped around.** The obvious way to report a difference
between two arms is to subtract their fractions. That is not the same number
whenever the two arms have different scorable denominators - a service failure in
one arm and not the other changes the marginal and changes nothing about the
cases both arms actually answered. `test_the_paired_difference_is_not_the_difference_of_marginals`
is the control for that, and it is the reason the other tests mean anything.
"""

import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "three_arm_paired", ROOT / "tools" / "three_arm.py")
ta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ta)


def rows(spec_):
    """Build an arm from `id -> correct?`, all scorable."""
    out = []
    for cid, ok in spec_.items():
        out.append({"id": cid, "tool": ok, "args": ok,
                    "seen": "" if ok else "no tool",
                    "unavailable": False})
    return out


def correct(n, upto):
    """Arm correct on ids `c0 .. c(upto-1)`, wrong on the rest."""
    return {("c%d" % i): (i < upto) for i in range(n)}


class TestThePairedDifferenceIsNotTheDifferenceOfMarginals:
    """**The control that makes the rest of this file mean something.**"""

    def _fixture(self):
        # 100 cases both arms answered; A right on 60, B right on 70.
        # B additionally answered 90 cases A could not score.
        a = rows(correct(100, 60))
        b = rows(dict(correct(100, 70),
                      **{("x%d" % i): (i < 90) for i in range(90)}))
        return a, b

    @staticmethod
    def _marginal(rows_):
        """The method this file exists to distinguish: subtract two fractions."""
        scorable = [r for r in rows_ if not r["unavailable"]]
        return sum(r["args"] for r in scorable) / len(scorable)

    def test_the_two_numbers_really_do_differ(self):
        a, b = self._fixture()
        paired = ta.paired(a, b, "args")["diff"]
        marginal = self._marginal(b) - self._marginal(a)
        assert abs(paired - marginal) > 0.05, (
            "the fixture stopped separating the two methods, so it no longer "
            "tests that pairing is not subtraction (paired %.3f, marginal %.3f)"
            % (paired, marginal))

    def test_pairing_uses_only_the_cases_both_arms_answered(self):
        a, b = self._fixture()
        r = ta.paired(a, b, "args")
        assert r["n"] == 100, r
        assert r["dropped"] == 90, (
            "the 90 cases only B answered must be reported as dropped, not "
            "quietly folded into a denominator")

    def test_a_case_one_arm_failed_is_dropped_not_counted_as_a_wrong_answer(self):
        a = rows(correct(10, 5))
        b = rows(correct(10, 5))
        b[3]["unavailable"] = True
        r = ta.paired(a, b, "args")
        assert r["n"] == 9, r
        assert r["dropped"] == 1, r
        # and it is *not* a disagreement: dropping is not failing
        assert r["a_only"] == 0 and r["b_only"] == 0, r


class TestTheDiscordanceIsTheEvidence:
    def test_four_points_of_real_effect_is_visible_and_significant(self):
        a = rows(correct(220, 190))
        b = rows(correct(220, 200))
        r = ta.paired(a, b, "args")
        assert (r["a_only"], r["b_only"]) == (0, 10), r
        assert r["diff"] == 10 / 220, r
        assert r["p"] < 0.01, r

    def test_a_tie_reports_a_unit_interval_not_a_narrow_one(self):
        """**The result this experiment is most likely to produce.** A grammar
        that changes nothing must not look like a small effect with a tight
        interval, which is what happens if the interval is computed over the
        marginals instead of the disagreements."""
        a = rows(correct(220, 190))
        b = rows(correct(220, 190))
        r = ta.paired(a, b, "args")
        assert r["a_only"] == 0 and r["b_only"] == 0, r
        assert r["p"] == 1.0, r
        assert r["lo"] == 0.0 and r["hi"] == 0.0, r
        assert r["diff"] == 0.0, r

    def test_gains_and_losses_are_counted_in_opposite_directions(self):
        """A net of +5 out of 5 wins and 0 losses is a different finding from
        5 wins and 5 losses, and both are a net of zero."""
        a = rows(correct(100, 50))
        # c50..c54 are *wrong* in A, so flipping them is a gain. My first
        # version flipped c5..c9, which `correct(100, 50)` already has right,
        # so the arm under test was byte-identical to the baseline and the
        # assertion below failed for a reason that had nothing to do with it.
        b = rows({**correct(100, 50), **{("c%d" % i): True for i in range(50, 55)}})
        net_zero = ta.paired(a, b, "args")
        assert (net_zero["a_only"], net_zero["b_only"]) == (0, 5), net_zero
        assert net_zero["p"] < 0.1, net_zero

        both_ways = rows({**correct(100, 50),
                          **{("c%d" % i): True for i in range(50, 55)},
                          **{("c%d" % i): False for i in range(0, 5)}})
        traded = ta.paired(a, both_ways, "args")
        assert (traded["a_only"], traded["b_only"]) == (5, 5), traded
        assert traded["diff"] == 0.0, traded
        assert traded["p"] == 1.0, (
            "5 wins and 5 losses is not evidence of a difference")
        assert traded["a_only"] + traded["b_only"] == 10, (
            "the discordance is what carries the information and must be "
            "printed even when the net difference is zero")


class TestTheExactTestIsArithmetic:
    def test_ten_clean_wins_has_the_p_two_over_two_to_the_ten(self):
        assert math.isclose(ta.mcnemar_p(0, 10), 2 * 0.5 ** 10,
                            rel_tol=1e-12)

    def test_no_discordance_is_the_strongest_null_not_an_error(self):
        assert ta.mcnemar_p(0, 0) == 1.0

    def test_the_p_is_symmetric_in_its_two_arguments(self):
        for b, c in ((1, 9), (3, 4), (7, 2), (10, 10)):
            assert math.isclose(ta.mcnemar_p(b, c), ta.mcnemar_p(c, b),
                                rel_tol=1e-12)

    def test_a_p_is_never_above_one(self):
        """The doubling of the smaller tail can exceed one for balanced counts,
        and a probability above 1 is not a probability."""
        for b in range(0, 12):
            for c in range(0, 12):
                assert 0.0 <= ta.mcnemar_p(b, c) <= 1.0, (b, c)


class TestTheIntervalIsSomethingYouCanQuote:
    def test_it_is_the_same_every_time(self):
        """**An interval that moves when the process restarts is a number nobody
        can quote**, and a seed derived from the clock produces exactly that."""
        # **Irregular and asymmetric, and that is the whole point.** My first
        # fixture alternated (True, False) with (False, True), so every
        # resample of it converged to the same percentiles whichever draws it
        # got - and a perfectly balanced population cannot tell a seeded
        # bootstrap from an unseeded one. This one has a real net effect, so the
        # tail quantiles move with the seed.
        pairs = [(i % 7 == 0, i % 11 == 0) for i in range(200)]
        assert ta.paired_ci(pairs) == ta.paired_ci(pairs)

    def test_the_seed_is_insurance_not_something_the_numbers_depend_on(self):
        """**Why removing the seed is an equivalent mutant, measured.**

        The seed exists so an interval can be quoted and compared with a
        previous run. It does not change *this* function's answer: at 2000 draws
        the 2.5th percentile is the 51st order statistic, and that is a stable
        functional of the dataset. Measured by drawing the interval twelve times
        from an unseeded generator on five datasets chosen to sit on the cliff -
        including one whose zero-mass is 2.4% against a 2.5% percentile - and
        getting **one distinct answer every time**.

        So `random.Random()` in place of `random.Random(seed)` is recorded as an
        equivalent mutant with this measurement as its reason, not deleted and
        not counted as a survivor. What the seed buys is reproducibility of the
        *procedure*, and this is the test that says the procedure's output is
        not what it is keeping stable.
        """
        fixtures = {
            "n=200, mixed": [(i % 7 == 0, i % 11 == 0) for i in range(200)],
            "n=80, 3 gains on the cliff":
                [(False, True)] * 3 + [(True, True)] * 77,
            "n=40, 5 clean gains": [(False, True)] * 5 + [(True, True)] * 35,
            "n=20, 2 gains": [(False, True)] * 2 + [(True, True)] * 18,
            "n=190, no ties": [(False, True)] * 190 + [(True, False)] * 10,
        }
        import random as _random
        original = ta.random.Random
        try:
            ta.random.Random = _random.Random      # no seed -> fresh entropy
            for label, pairs in fixtures.items():
                seeded = ta.paired_ci(pairs)
                unseeded = {ta.paired_ci(pairs) for _ in range(12)}
                assert unseeded == {seeded}, (
                    "%s: unseeded draws produced %d distinct intervals %r "
                    "against the seeded %r - so the seed IS observable here and "
                    "removing it is a real behaviour change"
                    % (label, len(unseeded), sorted(unseeded), seeded))
        finally:
            ta.random.Random = original

    def test_two_different_datasets_do_not_get_the_same_interval(self):
        """**The control for the seed.** A fixed seed makes the interval
        reproducible; it must not make it *constant*. My first version tried to
        show this by reversing the pairs, which cannot work - a bootstrap over a
        permutation is the same distribution, so the percentiles may coincide
        and nothing is proven either way."""
        steady = [(i % 2 == 0, i % 2 == 0) for i in range(200)]
        moved = [(i % 2 == 0, (i + 1) % 2 == 0) for i in range(200)]
        assert ta.paired_ci(steady) != ta.paired_ci(moved)

    def test_the_interval_is_oriented_low_to_high(self):
        """**The property that catches a resampled list that was never sorted.**

        Bracketing the measured difference is *not* enough: on right-skewed data
        a reversed list still brackets zero (my first fixture returned
        `(0.0, 0.0095)`, and `0 <= 0 <= 0.0095` holds). An interval reported as
        `(low, high)` with `low > high` is simply wrong, whatever it contains.
        """
        # **Left-skewed and with no mass at exactly zero**, because that is what
        # makes the reversal visible. Measured on the reversed list:
        #   right-skew with tie-mass  -> (0.0000, 0.0095)   lo <= hi, hides it
        #   all-discordant, balanced  -> (-0.0200, 0.0500)  lo <= hi, hides it
        #   left-skew, no ties        -> (+0.9200, 0.8300)  lo >  hi, caught
        # A reversal takes `lo` from the *upper* tail and `hi` from the *lower*,
        # so it only shows when the distribution's own extremes do not overlap
        # at the same value.
        pairs = [(False, True)] * 190 + [(True, False)] * 10
        lo, hi = ta.paired_ci(pairs)
        assert lo <= hi, (lo, hi)

    def test_it_brackets_the_measured_difference(self):
        pairs = [(True, False)] * 5 + [(False, True)] * 5 + \
                [(True, True)] * 100 + [(False, False)] * 100
        lo, hi = ta.paired_ci(pairs)
        assert lo <= 0.0 <= hi, (lo, hi)

    def test_no_cases_is_no_interval_rather_than_a_crash(self):
        assert ta.paired_ci([]) == (0.0, 0.0)


class TestTheReportRefusesToPrintAMissingArm:
    """**A row of zeros for an arm that never ran reads as a measurement of
    nothing.** That is the `skills_total` failure: a figure that cannot
    distinguish 'did not happen' from 'was zero'."""

    def _run(self, results, arms, capsys):
        ta.paired_table(results, arms)
        return capsys.readouterr().out

    def test_an_arm_with_no_rows_is_named_as_missing(self, capsys):
        a = rows(correct(20, 10))
        out = self._run({"+greedy": {"rows": a}, "+greedy+request": {"rows": []}},
                        ["+greedy", "+greedy+request"], capsys)
        assert "no result to pair" in out, out
        assert "+0.0 pts" not in out, (
            "an arm with no rows printed a number:\n" + out)

    def test_a_populated_comparison_does_print_numbers(self, capsys):
        """The mirror, so 'no result to pair' cannot be satisfied by a table that
        prints nothing at all."""
        a = rows(correct(20, 10))
        b = rows(correct(20, 15))
        out = self._run({"+greedy": {"rows": a}, "+greedy+constrained": {"rows": b}},
                        ["+greedy", "+greedy+constrained"], capsys)
        assert "no result to pair" not in out, out
        assert "+25.0 pts" in out, out

    def test_a_dropped_case_is_said_aloud_on_the_line_it_happened(self, capsys):
        a = rows(correct(20, 10))
        b = rows(correct(20, 10))
        b[2]["unavailable"] = True
        out = self._run({"+greedy": {"rows": a}, "+greedy+constrained": {"rows": b}},
                        ["+greedy", "+greedy+constrained"], capsys)
        assert "could not be paired" in out, out


class TestTheReportPairsAllThreeOutcomes:
    """**The split is the finding**, and a table that collapsed it into one
    number would read as "the grammar did nothing" where the honest reading is
    "it made answers well-formed and no more likely to be right" - which are
    opposite decisions about adopting it."""

    def test_all_three_sections_are_printed(self, capsys):
        a = rows(correct(40, 20))
        b = rows(correct(40, 25))
        ta.paired_table({"+greedy": {"rows": a},
                         "+greedy+constrained": {"rows": b}},
                        ["+greedy", "+greedy+constrained"])
        out = capsys.readouterr().out
        for label in ("acted (form)", "right tool", "tool+args"):
            assert label in out, "no %r section in the report:\n%s" % (label, out)

    def test_each_outcome_gets_its_own_pairing(self, capsys):
        """Three labels could be printed over one number; three numbers cannot."""
        a = rows(correct(40, 20))
        b = rows(correct(40, 25))
        ta.paired_table({"+greedy": {"rows": a},
                         "+greedy+constrained": {"rows": b}},
                        ["+greedy", "+greedy+constrained"])
        out = capsys.readouterr().out
        assert out.count("exact p = ") == 3, out


class TestFormAndChoiceArePairedSeparately:
    """**The form/choice split is the finding**, so pairing must preserve it:
    an arm that made every answer well-formed and no more of them right is a
    different result from one that changed nothing."""

    def _mixed(self):
        # Arm B calls a tool on every case; 10 of them are the right tool.
        a = [{"id": "c%d" % i, "tool": i < 50, "args": i < 50,
              "seen": "" if i < 50 else "no tool", "unavailable": False}
             for i in range(100)]
        b = [{"id": "c%d" % i, "tool": i < 60, "args": i < 50,
              "seen": "", "unavailable": False} for i in range(100)]
        return a, b

    def test_acted_moves_while_args_does_not(self):
        a, b = self._mixed()
        form = ta.paired(a, b, "acted")
        choice = ta.paired(a, b, "args")
        assert form["diff"] == 0.5, form
        assert choice["diff"] == 0.0, choice
        assert form["p"] < 0.001 and choice["p"] == 1.0, (form["p"], choice["p"])

    def test_acted_is_read_the_way_the_marginal_table_reads_it(self):
        """Two definitions of 'acted' would let the paired table and the
        marginal table report different things about the same run.

        The discriminating row is **one that carries an error**: `score_arm`
        asks `seen != "no tool"`, so a row that said `ERROR: 429` counts as
        acted, and an implementation that instead asked for an empty `seen`
        would report a different number for the same run."""
        a = rows(correct(20, 10))
        b = rows(correct(20, 10))
        assert sum(1 for r in b if r.get("seen") == "no tool") == 10
        # on a row that currently reads "no tool": under `!= "no tool"` this
        # becomes acted, under `== ""` it does not - so this row is the one that
        # separates the two definitions. On an already-acted row it would not.
        b[15]["seen"] = "ERROR: 429"
        marginal_acted = sum(1 for r in b if r.get("seen") != "no tool")
        assert marginal_acted == 11, marginal_acted
        # exactly one case changed hands, in arm B's favour: A still reads
        # "no tool" there, B reads an error - and `!= "no tool"` calls that acted.
        r = ta.paired(a, b, "acted")
        assert (r["a_only"], r["b_only"]) == (0, 1), r
