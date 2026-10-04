"""The model zoo for the outcome predictor, and an honest account of when to use it.

`outcome_model.py` ships a multinomial logistic regression written in the
standard library, and that is what runs on a default install. This module holds
the wider zoo the user asked for - `SGDClassifier`, `SGDRegressor`,
`LogisticRegression`, `RandomForest`, `HistGradientBoosting` - behind the same
optional-import rule.

**The zoo is opt-in because the data does not yet justify it, and that is a
measurement rather than an opinion.** On the 11,868 recorded calls: only **1 of
15** tools has a mixed verdict rate. `liar` fails 302 times out of 302. So
outcome is very nearly a per-tool constant, and the cheapest possible model -
a lookup table - already beats everything here. A `RandomForest` on that data
returns the same 94% majority-class answer the linear model returns, while
overfitting 346 minority examples with far more confidence, which is worse
rather than better: a confidently wrong failure prediction is what would make
somebody stop trusting the tool.

**So the ordering is deliberate and worth stating: post-conditions first, models
second.** 21 of 32 mutating actuators return `UNVERIFIED` always, which is why
94% of the labels are identical. Adding read-back probes to those actuators
would make the labels informative *and* make the system more honest at the same
time. A gradient-boosted model on the current labels is 235 MB of image weight
to learn nothing; the same model on post-conditioned labels is the right tool.

**What is here, then, is the capability plus the measurement that will tell you
when to switch it on.** `compare()` trains every available estimator on a
time-ordered split and reports each against the majority-class baseline, with
minority recall beside it - so "should I use the zoo" becomes a number rather
than an argument, and it is re-runnable after post-conditions land.

**Nothing here imports at module level.** Every third-party import is inside a
function, so a machine with none of these installed imports this module fine and
gets a clear message instead of an ImportError.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from shani_chronoa import outcome_model as core

logger = logging.getLogger(__name__)

#: Below this, a package is treated as absent rather than half-working.
_MIN_CLASS = core._MIN_CLASS


class Candidate(NamedTuple):
    name: str
    makes: Callable[[], Optional[object]]   # None if the dependency is missing
    note: str


def _sklearn():
    try:
        import sklearn  # noqa: F401
        return sklearn
    except ImportError:
        return None


def _sklearn_model(path: str):
    """Build one sklearn estimator by name, or None if sklearn is absent."""
    if _sklearn() is None:
        return None
    try:
        from sklearn.linear_model import LogisticRegression, SGDClassifier
        from sklearn.ensemble import (HistGradientBoostingClassifier,
                                      RandomForestClassifier)
    except ImportError:
        return None
    factories = {
        "LogisticRegression": lambda: LogisticRegression(max_iter=1000),
        "SGDClassifier": lambda: SGDClassifier(loss="log_loss", max_iter=1000,
                                               random_state=0),
        "SGDRegressor": lambda: _sgd_regressor(),
        "RandomForest": lambda: RandomForestClassifier(
            n_estimators=200, random_state=0, n_jobs=-1),
        "HistGradientBoosting": lambda: HistGradientBoostingClassifier(
            max_iter=200, random_state=0),
    }
    return factories.get(path, lambda: None)()


def _sgd_regressor():
    from sklearn.linear_model import SGDRegressor
    return SGDRegressor(max_iter=1000, random_state=0)


#: The zoo, in the order worth trying. Linear first on purpose: when the signal
#: is weak it is also the least able to overfit it, so starting with a tree
#: model would flatter the data and mislead whoever reads the report.
CANDIDATES: Tuple[Candidate, ...] = (
    Candidate("builtin-logistic",
              lambda: core.OutcomeModel(),
              "the standard-library model that ships; no dependency at all"),
    Candidate("LogisticRegression",
              lambda: _sklearn_model("LogisticRegression"),
              "sklearn's proper solver; the linear baseline done rigorously"),
    Candidate("SGDClassifier",
              lambda: _sklearn_model("SGDClassifier"),
              "online, so it can be refit as new calls arrive"),
    Candidate("SGDRegressor",
              lambda: _sklearn_model("SGDRegressor"),
              "regression, for predicting duration or a numeric outcome"),
    Candidate("RandomForest",
              lambda: _sklearn_model("RandomForest"),
              "captures interaction between tool and argument shape"),
    Candidate("HistGradientBoosting",
              lambda: _sklearn_model("HistGradientBoosting"),
              "strongest tabular model here, and the most able to overfit"),
)


class Result(NamedTuple):
    name: str
    available: bool
    note: str
    accuracy: float = 0.0
    baseline: float = 0.0
    minority_recall: float = 0.0
    beats_baseline: bool = False
    #: True when the model could not be evaluated at all. A failed model must
    #: never be shown as a row of zeros: that reads as "scored 0%" when the
    #: truth is "was never run", which is how five scikit-learn candidates came
    #: to be reported as failures when they had in fact never been evaluated.
    failed: bool = False


def _fit(model, examples) -> None:
    """Fit any candidate from the same Example objects.

    The two model families disagree about what `fit` takes - the built-in one
    reads `.x`/`.y` off each `Example`, sklearn wants two plain sequences - and
    picking the wrong shape raises `AttributeError: 'list' object has no
    attribute 'y'` at training time. Detected here rather than by a try/except
    around the whole comparison, so a genuinely broken model still reports as
    broken instead of being silently retried with the other calling convention.
    """
    module = type(model).__module__ or ""
    if module.startswith("sklearn"):
        model.fit([list(e.x) for e in examples], [e.y for e in examples])
    else:
        model.fit(examples)


def _minority_recall(report: core.Report) -> float:
    """Recall on the classes that are not the majority."""
    if not report.per_class:
        return 0.0
    majority = max(report.per_class,
                   key=lambda k: report.per_class[k]["support"])
    recalls = [stats["recall"] for name, stats in report.per_class.items()
               if name != majority and stats["support"] >= _MIN_CLASS]
    return sum(recalls) / len(recalls) if recalls else 0.0


def compare(path: Optional[object] = None,
            holdout: float = 0.25) -> List[Result]:
    """Train every available candidate on a time-ordered split and score it.

    The split is by position, not at random. A random split would put the same
    tool with the same arguments on both sides, and with hashed categorical
    features that is very close to memorisation - which would make every
    candidate look brilliant and tell nobody anything.
    """
    examples = core.load(path)
    if not examples:
        return []
    cut = int(len(examples) * (1.0 - holdout))
    train, test = examples[:cut], examples[cut:]
    if not train or not test:
        return []

    results: List[Result] = []
    for candidate in CANDIDATES:
        try:
            model = candidate.makes()
        except Exception as exc:  # noqa: BLE001
            results.append(Result(candidate.name, False,
                                  f"could not be built: {type(exc).__name__}",
                                  failed=True))
            continue
        if model is None:
            results.append(Result(candidate.name, False,
                                  "dependency not installed"))
            continue
        try:
            _fit(model, train)
            report = core.evaluate(model, test)
            results.append(Result(
                candidate.name, True, candidate.note, report.accuracy,
                report.baseline, _minority_recall(report),
                report.accuracy > report.baseline))
        except Exception as exc:  # noqa: BLE001
            results.append(Result(candidate.name, True,
                                  f"failed while training: {type(exc).__name__}: {exc}",
                                  failed=True))
    return results


def render(results: List[Result]) -> str:
    """A comparison that cannot be read as a leaderboard."""
    if not results:
        return ("Nothing to compare: no labelled examples were found. The log is "
                "either empty or predates the verdict being recorded.")
    out = [f"{len(results)} candidate model(s).", ""]
    available = [r for r in results if r.available]
    if not available:
        out.append("None could be built - the standard-library model should have "
                   "been among them, so this is worth investigating.")
        return "\n".join(out)

    scored = [r for r in available if not r.failed]
    broken = [r for r in available if r.failed]
    if scored:
        out.append("  model                    accuracy   baseline   minority recall  beats baseline")
        for r in sorted(scored, key=lambda r: -r.accuracy):
            out.append(f"  {r.name:<24} {r.accuracy * 100:7.1f}%  "
                       f"{r.baseline * 100:7.1f}%   {r.minority_recall * 100:13.1f}%  "
                       f"{'yes' if r.beats_baseline else 'NO'}")
    if broken:
        out.append("")
        out.append("  ** These could not be evaluated, and are NOT scores of zero:")
        for r in broken:
            out.append(f"    {r.name:<22} {r.note}")
    missing = [r for r in results if not r.available]
    if missing:
        out.append("")
        out.append("  Not available (optional dependency absent):")
        for r in missing:
            out.append(f"    {r.name:<22} {r.note}")
    out.append("")
    out.append("  Read this with the baseline column open. The majority class is "
               "overwhelming, so accuracy alone rewards guessing it. A model "
               "only earns its dependency when it beats the baseline AND has "
               "non-zero minority recall.")
    return "\n".join(out)


def recommend(results: List[Result]) -> str:
    """Which model to actually use, if any.

    Prefers the cheapest model that clears the bar. A 184 MB dependency has to
    earn its place by being *better*, not by being newer.
    """
    useful = [r for r in results
              if r.available and r.beats_baseline and r.minority_recall > 0]
    if not useful:
        return ("None of the available models beats the majority-class baseline "
                "with non-zero minority recall. That is the expected result on "
                "the current labels, because most mutators have no "
                "post-condition and so return UNVERIFIED every time. The fix is "
                "post-conditions, not a bigger model - add read-back probes, "
                "and re-run this comparison.")
    order = {c.name: i for i, c in enumerate(CANDIDATES)}
    best = sorted(useful, key=lambda r: (order.get(r.name, 99), -r.accuracy))[0]
    return (f"Use {best.name}: {best.note}. It is the cheapest model that "
            f"clears the bar ({best.accuracy * 100:.1f}% against a "
            f"{best.baseline * 100:.1f}% baseline, minority recall "
            f"{best.minority_recall * 100:.1f}%).")


def best_available() -> Optional[object]:
    """A trained model from the recommended candidate, or the built-in one.

    Falls back to the standard-library model whenever the zoo is absent or
    useless, so a caller never has to know which dependencies exist.
    """
    try:
        results = compare()
        useful = [r for r in results
                  if r.available and r.beats_baseline and r.minority_recall > 0]
        if useful:
            order = {c.name: i for i, c in enumerate(CANDIDATES)}
            chosen = sorted(useful, key=lambda r: order.get(r.name, 99))[0]
            examples = core.load()
            cut = int(len(examples) * 0.75)
            for candidate in CANDIDATES:
                if candidate.name != chosen.name:
                    continue
                model = candidate.makes()
                if model is not None:
                    _fit(model, examples[:cut])
                    return model
    except Exception as exc:  # noqa: BLE001
        logger.debug("model zoo: falling back to the built-in model: %s", exc)
    return None