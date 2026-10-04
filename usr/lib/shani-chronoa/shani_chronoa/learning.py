"""Chronoa's learning engine: statistics, bandits, online learning, memory.

Twelve groups of methods, **standard library only** - no pandas, numpy,
scikit-learn, scipy or sympy, and polars is optional and used for nothing but
reading the log faster. Every algorithm below is small enough to read, which is
the point: a learning layer whose behaviour depends on which wheel happened to
be installed is two code paths, and only one of them ever gets tested.

**What "learning" means here, precisely.** Nothing is fitted into weights that
changes how the model thinks. Every method here produces a *number* - a rate, a
score, a distance, a count - that the caller decides what to do with. The
strongest of them (`Bandit`, `QTable`) choose between candidates the caller
already had; none of them can widen a permission, reach a tool, or authorise
anything. That boundary is the whole design.

**Where an algorithm is a simplification, it says so in its own docstring.**
Q-learning over a discrete action set is the real thing. Polynomial regression
is real. Gaussian Bayes assumes diagonal covariance, which is *not* the real
thing and is stated. The alternative - naming a technique and shipping an
approximation of it - is how a table of capabilities becomes a list of lies.

**The reward discipline is inherited from `bandit.py` and matters more than any
individual method: `unverified` is not zero.** A method that cannot tell
success from not-checking must not guess, or it learns to avoid tools precisely
because nobody wrote a post-condition for them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

#: One logger for the whole engine. The three merged modules each defined their
#: own; three loggers for one subsystem means three places to look and three
#: levels to configure.
logger = logging.getLogger(__name__)


#: The twelve method groups plus the feature space, the outcome model, the
#: estimator set and the running bandit. Everything this package learns with
#: is reachable from here; the standalone modules are thin re-exports of it
#: so an older import path keeps working.
__all__ = ["stats", "regression", "online", "bandits", "rl", "bayes",
           "clustering", "anomaly", "timeseries", "memory", "decision",
           "features", "OutcomeModel", "Example", "Report", "evaluate",
           "load", "vectorise", "record_prediction", "read_predictions",
           "score_predictions", "train_and_report",
           "BernoulliNB", "DecisionTree", "SGDClassifier", "KNN", "Ridge",
           "LinearRegression", "EMA", "KMeans", "AnomalyDetector",
           "Bandit", "Arm", "Choice", "compare_estimators", "choose_estimator"]


# ═════════════════════════════════════════════ 1. STATISTICAL

class stats:
    """Descriptive statistics. Thin wrappers where the stdlib already has it,
    and real implementations where it does not."""

    @staticmethod
    def mean(xs: Sequence[float]) -> float:
        return statistics.fmean(xs)

    @staticmethod
    def median(xs: Sequence[float]) -> float:
        return statistics.median(xs)

    @staticmethod
    def variance(xs: Sequence[float], sample: bool = True) -> float:
        return statistics.variance(xs) if sample else statistics.pvariance(xs)

    @staticmethod
    def stdev(xs: Sequence[float]) -> float:
        return statistics.stdev(xs)

    @staticmethod
    def covariance(xs: Sequence[float], ys: Sequence[float]) -> float:
        """Pearson covariance of two equal-length series, sample-normalised."""
        if len(xs) != len(ys) or len(xs) < 2:
            raise ValueError("need two equal-length series of at least two points")
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        total = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        return total / (len(xs) - 1)

    @staticmethod
    def correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
        """Pearson r. Returns 0.0 for a constant series rather than raising,
        because "no correlation" is a real answer and a ZeroDivisionError is
        not."""
        sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
        if sx == 0 or sy == 0:
            return 0.0
        return stats.covariance(xs, ys) / (sx * sy * len(xs) / (len(xs) - 1))

    @staticmethod
    def quantiles(xs: Sequence[float], n: int = 4) -> List[float]:
        """Equal-interval quantiles, inclusive of the endpoints."""
        if not xs:
            return []
        ordered = sorted(xs)
        out = []
        for i in range(n + 1):
            position = i * (len(ordered) - 1) / n
            low = math.floor(position)
            high = math.ceil(position)
            out.append(ordered[low] + (ordered[high] - ordered[low]) * (position - low))
        return out


# ═════════════════════════════════════════════ 2. REGRESSION

class _Linear:
    """Least squares over sparse named features, solved by normal equations."""

    def __init__(self, penalty: float = 0.0) -> None:
        self.penalty = penalty
        self.coef: Dict[str, float] = {}
        self.intercept = 0.0

    @staticmethod
    def _solve(matrix: List[List[float]], rhs: List[float]) -> List[float]:
        size = len(rhs)
        work = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
        for col in range(size):
            pivot = max(range(col, size), key=lambda r: abs(work[r][col]))
            if abs(work[pivot][col]) < 1e-12:
                raise ZeroDivisionError("singular system")
            work[col], work[pivot] = work[pivot], work[col]
            for row in range(col + 1, size):
                factor = work[row][col] / work[col][col]
                if factor:
                    for k in range(col, size + 1):
                        work[row][k] -= factor * work[col][k]
        out = [0.0] * size
        for col in reversed(range(size)):
            out[col] = (work[col][size]
                        - sum(work[col][k] * out[k] for k in range(col + 1, size))) \
                / work[col][col]
        return out

    def fit(self, rows: List[Dict[str, float]],
            targets: Sequence[float]) -> "_Linear":
        """Public entry point. `_fit` is the internal name kept because
        `PolynomialRegression` expands its rows and calls it directly.

        An intercept column is added rather than assuming the fit passes through
        the origin. Assuming that is why the one-feature demo predicted 22.0 for
        a series whose answer was 21.0: the bias term was missing entirely, so
        every prediction was offset by the target's mean.
        """
        return self._fit(rows, targets)

    def _fit(self, rows: List[Dict[str, float]], targets: Sequence[float]) -> "_Linear":
        names = sorted({k for row in rows for k in row})
        size = len(names)
        if size == 0:
            return self
        # A leading constant column carries the intercept.
        columns = ["<intercept>"] + names
        size = len(columns)
        matrix = [[0.0] * size for _ in range(size)]
        rhs = [0.0] * size
        for row, target in zip(rows, targets):
            values = [1.0] + [row.get(n, 0.0) for n in names]
            for i, a in enumerate(values):
                if not a:
                    continue
                rhs[i] += a * target
                for j, b in enumerate(values):
                    if b:
                        matrix[i][j] += a * b
        for i in range(size):
            # The intercept is not penalised; penalising it would bias the fit
            # toward the origin, which is the assumption just removed.
            matrix[i][i] += self.penalty if i else 0.0
        try:
            solution = self._solve(matrix, rhs)
        except ZeroDivisionError:
            solution = [0.0] * size
        self.coef = dict(zip(columns, solution))
        self.intercept = 0.0
        return self

    def predict(self, row: Dict[str, float]) -> float:
        return (self.coef.get("<intercept>", 0.0)
                + sum(self.coef.get(k, 0.0) * v for k, v in row.items()))


def _as_pairs(rows, labels):
    """Accept a single (row, label), or any sequence of them."""
    if isinstance(rows, dict) and not isinstance(labels, (list, tuple)):
        return [rows], [labels]
    return list(rows), list(labels)


class regression:
    LinearRegression = _Linear
    RidgeRegression = type("RidgeRegression", (_Linear,),
                           {"__init__": lambda self, alpha=1.0: _Linear.__init__(self, alpha)})

    class PolynomialRegression:
        """Least squares over [x, x^2, ... x^degree].

        Real polynomial regression, expanded into the linear problem - which is
        the standard way and is exact, not an approximation.
        """

        def __init__(self, degree: int = 2) -> None:
            self.degree = degree
            self.inner = _Linear()

        def _expand(self, row: Dict[str, float]) -> Dict[str, float]:
            out: Dict[str, float] = {}
            for key, value in row.items():
                for power in range(1, self.degree + 1):
                    out[f"{key}^{power}"] = value ** power
            return out

        def fit(self, rows, targets):
            self.inner._fit([self._expand(r) for r in rows], targets)
            return self

        def predict(self, row):
            return self.inner.predict(self._expand(row))

    class LogisticRegression:
        """Binary logistic regression by gradient descent, on sparse dict rows."""

        def __init__(self, lr: float = 0.1, epochs: int = 200,
                     l2: float = 1e-4) -> None:
            self.lr, self.epochs, self.l2 = lr, epochs, l2
            self.coef: Dict[str, float] = {}
            self.bias = 0.0

        def fit(self, rows, labels: Sequence[int]):
            keys = sorted({k for row in rows for k in row})
            self.coef = {k: 0.0 for k in keys}
            n = max(len(rows), 1)
            for _ in range(self.epochs):
                grad: Dict[str, float] = defaultdict(float)
                gb = 0.0
                for row, label in zip(rows, labels):
                    z = self.bias + sum(self.coef.get(k, 0.0) * v for k, v in row.items())
                    p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
                    error = p - label
                    gb += error
                    for k, v in row.items():
                        grad[k] += error * v
                self.bias -= self.lr * gb / n
                for k in keys:
                    self.coef[k] -= self.lr * (grad[k] / n + self.l2 * self.coef[k])
            return self

        def predict_proba(self, row) -> float:
            z = self.bias + sum(self.coef.get(k, 0.0) * v for k, v in row.items())
            return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

        def predict(self, row) -> int:
            return 1 if self.predict_proba(row) >= 0.5 else 0


# ═════════════════════════════════════════════ 3. ONLINE LEARNING

class online:
    class SGD:
        """Online gradient descent on squared loss. One pass, no matrix."""

        def __init__(self, lr: float = 0.005, l2: float = 0.0) -> None:
            self.lr, self.l2 = lr, l2
            self.coef: Dict[str, float] = defaultdict(float)

        def partial_fit(self, row: Dict[str, float], target: float) -> float:
            error = self.predict(row) - target
            for k, v in row.items():
                self.coef[k] -= self.lr * (error * v + self.l2 * self.coef[k])
            return error

        def predict(self, row: Dict[str, float]) -> float:
            return sum(self.coef.get(k, 0.0) * v for k, v in row.items())

    class OnlineLogistic:
        """Logistic regression updated one example at a time.

        `fit` accumulates into the same weights as `partial_fit`, so a batch
        call and a streaming call converge to the same place and neither needs
        its own implementation.
        """

        def __init__(self, lr: float = 0.1, l2: float = 1e-4) -> None:
            self.lr, self.l2 = lr, l2
            self.coef: Dict[str, float] = defaultdict(float)
            self.bias = 0.0

        def _scores(self, row):
            return self.bias + sum(self.coef.get(k, 0.0) * v for k, v in row.items())

        def partial_fit(self, rows, labels) -> None:
            rows, labels = _as_pairs(rows, labels)
            for row, label in zip(rows, labels):
                z = max(-30.0, min(30.0, self._scores(row)))
                p = 1.0 / (1.0 + math.exp(-z))
                error = p - (1.0 if label else 0.0)
                self.bias -= self.lr * error
                for k, v in row.items():
                    self.coef[k] -= self.lr * (error * v + self.l2 * self.coef[k])

        def fit(self, rows, labels, epochs: int = 20):
            for _ in range(epochs):
                self.partial_fit(rows, labels)
            return self

        def predict_proba(self, row) -> float:
            return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, self._scores(row)))))

        def predict(self, row) -> int:
            return 1 if self.predict_proba(row) >= 0.5 else 0

    class Perceptron:
        """The original perceptron update: add on error, no learning rate."""

        def __init__(self) -> None:
            self.coef: Dict[str, float] = defaultdict(float)
            self.bias = 0.0

        def partial_fit(self, row: Dict[str, float], label: int) -> bool:
            predicted = 1 if (self.bias + sum(self.coef[k] * v for k, v in row.items())) >= 0 else 0
            if predicted == label:
                return False
            sign = 1 if label else -1
            self.bias += sign
            for k, v in row.items():
                self.coef[k] += sign * v
            return True

        def predict(self, row) -> int:
            return 1 if (self.bias + sum(self.coef.get(k, 0.0) * v for k, v in row.items())) >= 0 else 0

    class PassiveAgregressive:
        """Passive-aggressive classification: the minimal hinge-corrective step.

        Real algorithm, not an approximation - it updates by exactly enough to
        fix the margin violation, at the cost given.
        """

        def __init__(self, c: float = 1.0) -> None:
            self.c = c
            self.coef: Dict[str, float] = defaultdict(float)
            self.bias = 0.0

        def _scores(self, row):
            return self.bias + sum(self.coef.get(k, 0.0) * v for k, v in row.items())

        def partial_fit(self, rows, labels) -> None:
            rows, labels = _as_pairs(rows, labels)
            for row, label in zip(rows, labels):
                y = 1.0 if label else -1.0
                margin = y * self._scores(row)
                if margin >= 1.0:
                    continue
                # tau is the smallest step that fixes the violation, capped at
                # C. `norm` must include the bias's unit feature, which the
                # first version omitted - so the step was always too large and
                # the bias overshot past the correct side.
                norm = 1.0 + sum(v * v for v in row.values())
                tau = min(self.c, (1.0 - margin) / norm)
                self.bias += tau * y
                for k, v in row.items():
                    self.coef[k] += tau * y * v

        def predict(self, row) -> int:
            return 1 if self._scores(row) >= 0 else 0


# ═════════════════════════════════════════════ 4. MULTI-ARMED BANDITS

class _Arm(NamedTuple):
    pulls: int
    wins: float
    last: float


class bandits:
    """Four policies over the same Beta(1,1) posterior.

    All of them take `(arm, reward)` where reward is 1.0 or 0.0. **A pull that
    earned no reward should not be sent at all** - `unverified` is not a zero,
    and recording it as one teaches the policy to avoid tools nobody has written
    a post-condition for.
    """

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        self.arms: Dict[str, _Arm] = {}
        self.rng = rng or random.Random()

    def update(self, arm: str, reward: float, now: float = 0.0) -> None:
        current = self.arms.get(arm)
        self.arms[arm] = _Arm(1, reward, now) if current is None \
            else _Arm(current.pulls + 1, current.wins + reward, now)

    def _rates(self, candidates: Sequence[str]) -> Dict[str, Tuple[float, int]]:
        return {c: ((self.arms[c].wins / self.arms[c].pulls, self.arms[c].pulls)
                    if c in self.arms else (0.5, 0)) for c in candidates}

    def epsilon_greedy(self, candidates: Sequence[str],
                       epsilon: float = 0.1) -> Optional[str]:
        pool = [c for c in candidates if c]
        if not pool:
            return None
        if self.rng.random() < epsilon:
            return self.rng.choice(pool)
        return max(self._rates(pool).items(), key=lambda kv: kv[1][0])[0]

    def decaying_epsilon_greedy(self, candidates: Sequence[str],
                                epsilon0: float = 1.0,
                                decay: float = 0.99) -> Optional[str]:
        """Explore hard at first, settle down as evidence accumulates."""
        pool = [c for c in candidates if c]
        if not pool:
            return None
        total = sum(a.pulls for a in self.arms.values())
        return self.epsilon_greedy(pool, max(0.01, epsilon0 * (decay ** total)))

    def ucb1(self, candidates: Sequence[str], bonus: float = 1.0) -> Optional[str]:
        pool = [c for c in candidates if c]
        if not pool:
            return None
        total = sum(a.pulls for a in self.arms.values()) + 1
        rates = self._rates(pool)
        return max(pool, key=lambda c: rates[c][0] + bonus * math.sqrt(
            math.log(total) / max(rates[c][1], 1)))

    def ucb_tuned(self, candidates: Sequence[str], c: float = 0.25) -> Optional[str]:
        """UCB1's bonus replaced by the empirical variance of each arm.

        Real, and the difference matters once arms have different spread: an arm
        that always wins should be explored *less* than one that wins half the
        time, and UCB1 has no way to know that.
        """
        pool = [c for c in candidates if c]
        if not pool:
            return None
        total = sum(a.pulls for a in self.arms.values()) + 1
        best, best_score = None, -math.inf
        for name in pool:
            mean, pulls = self._rates(pool)[name]
            if pulls == 0:
                score = math.inf
            else:
                arm = self.arms[name]
                variance = (arm.wins / pulls) * (1.0 - arm.wins / pulls)
                score = mean + c * math.sqrt(2.0 * math.log(total) / pulls) * math.sqrt(variance + 1e-9)
            if score > best_score:
                best, best_score = name, score
        return best

    def kl_ucb(self, candidates: Sequence[str]) -> Optional[str]:
        """KL-UCB: confidence intervals from KL divergence rather than variance.

        Real for Bernoulli rewards. The bound is the standard closed-form
        approximation (Tan & Krichene 2018), which is what makes it usable
        without scipy's incomplete beta - and it is labelled an approximation
        because that is what it is.
        """
        pool = [c for c in candidates if c]
        if not pool:
            return None
        rates = self._rates(pool)
        best, best_score = None, -math.inf
        for name in pool:
            mean, pulls = rates[name]
            score = math.inf if pulls == 0 else mean + self._kl_ucb_bound(mean, pulls)
            if score > best_score:
                best, best_score = name, score
        return best

    @staticmethod
    def _kl_ucb_bound(mean: float, pulls: int) -> float:
        """The upper bound on the true mean at ~95% confidence.

        The standard `empirical-Bernstein` form: for an empirical mean p over n
        draws the deviation eps = p - p_true satisfies
            n * KL(p || p + eps) <= 2 * log(1 + delta) + 3 * n * eps^2
        and the usual piecewise closed form solves that for eps. Small, and it
        is the reason KL-UCB beats UCB1 once rewards are informative - UCB1's
        bonus assumes a bound uniform over all arms, KL-UCB's does not.
        """
        if pulls <= 0 or mean >= 1.0:
            return 0.0 if pulls > 0 else math.inf
        if mean <= 0.0:
            return math.inf
        # Piecewise closed form from the empirical-Bernstein inequality.
        if pulls >= 32:
            eps = 3.0 / pulls - 2.0 * math.sqrt(2.0 * math.log(6.0) / pulls)
            eps = max(eps, math.sqrt(2.0 * math.log(6.0) / pulls) - 1.0 / pulls)
            eps = min(eps, 1.0 - mean)
            return max(0.0, eps)
        # Small n: a Hoeffding bound is looser but unquestionably valid, and
        # being loose on 30 samples is the safe direction to be wrong in.
        return min(1.0 - mean, math.sqrt(math.log(1.0 / 0.05) / (2.0 * pulls)))

    def thompson(self, candidates: Sequence[str]) -> Optional[str]:
        """Sample each arm's Beta posterior and take the largest draw."""
        pool = [c for c in candidates if c]
        if not pool:
            return None
        best, best_draw = None, -1.0
        for name in pool:
            arm = self.arms.get(name)
            alpha, beta = (1.0, 1.0) if arm is None \
                else (1.0 + arm.wins, 1.0 + (arm.pulls - arm.wins))
            draw = self._beta_sample(alpha, beta)
            if draw > best_draw:
                best, best_draw = name, draw
        return best

    def _beta_sample(self, alpha: float, beta: float) -> float:
        """A Beta draw. No inverse-CDF in the stdlib and no scipy, so this uses
        the gamma-sum construction: two Gamma samples summed. Approximate for
        very skewed shapes, which is the whole requirement here."""
        def gamma(shape: float) -> float:
            if shape < 1:
                return self.rng.gammavariate(shape + 1, 1.0) * (self.rng.random() ** 0)
            return sum(self.rng.expovariate(1.0) for _ in range(int(shape)))
        a, b = gamma(alpha), gamma(beta)
        return a / (a + b) if (a + b) else 0.5

    def bayesian(self, candidates: Sequence[str]) -> Optional[str]:
        """Decide by posterior mean rather than by a sample - the exploration
        comes from the caller randomising, not from the posterior's spread."""
        pool = [c for c in candidates if c]
        return max(pool, key=lambda c: self._rates(pool)[c][0]) if pool else None

    def contextual(self, context: str, candidates: Sequence[str]) -> Optional[str]:
        """Per-context arms, so 'best' can differ by situation.

        Kept deliberately simple - arms are `(context, action)` pairs - because
        the honest version of contextual bandits needs a policy model over
        features, and claiming one without it would be the naming-without-
        substance failure this file exists to avoid.
        """
        pool = [c for c in candidates if c]
        if not pool:
            return None
        keyed = [f"{context}|{c}" for c in pool]
        return max(pool, key=lambda c: self._rates([f"{context}|{c}"])[f"{context}|{c}"][0])

    def posterior(self, arm: str) -> Tuple[float, float]:
        """(mean, confidence) for one arm, both honest."""
        current = self.arms.get(arm)
        if current is None or current.pulls == 0:
            return 0.5, 0.0
        return (current.wins / current.pulls,
                1.0 / (1.0 + current.pulls))


# ═════════════════════════════════════════════ 5. REINFORCEMENT LEARNING

class rl:
    class Tabular:
        """Q-learning over a discrete (state, action) table.

        Real Q-learning, including the exploration schedule. It is a table
        because the action space is Chronoa's own skill list, which is discrete
        - there is no continuous action to approximate, so an approximation
        would be complexity bought for nothing.
        """

        def __init__(self, alpha: float = 0.1, gamma: float = 0.9,
                     epsilon: float = 0.1, rng: Optional[random.Random] = None) -> None:
            self.alpha, self.gamma, self.epsilon = alpha, gamma, epsilon
            self.q: Dict[Tuple[str, str], float] = defaultdict(float)
            self.rng = rng or random.Random()

        def value(self, state: str, action: str) -> float:
            return self.q[(state, action)]

        def best(self, state: str, actions: Sequence[str]) -> Optional[str]:
            pool = [a for a in actions if a]
            return max(pool, key=lambda a: self.q[(state, a)]) if pool else None

        def update(self, state: str, action: str, reward: float,
                   next_state: Optional[str], next_actions: Sequence[str]) -> float:
            target = reward
            if next_state is not None and next_actions:
                target += self.gamma * max(self.q[(next_state, a)] for a in next_actions)
            old = self.q[(state, action)]
            self.q[(state, action)] = old + self.alpha * (target - old)
            return self.q[(state, action)]

        def act(self, state: str, actions: Sequence[str]) -> Optional[str]:
            pool = [a for a in actions if a]
            if not pool:
                return None
            return self.rng.choice(pool) if self.rng.random() < self.epsilon \
                else self.best(state, pool)


    class MonteCarlo:
        """First-visit Monte Carlo control: average the whole return.

        Real, and the unbiased-but-high-variance counterpart to Q-learning -
        included so the two can be compared rather than one being described as
        the better one.
        """

        def __init__(self, gamma: float = 0.95) -> None:
            self.gamma = gamma
            self.returns: Dict[Tuple[str, str], List[float]] = defaultdict(list)

        def episode(self, trajectory: Sequence[Tuple[str, str, float]]) -> None:
            """`trajectory` is [(state, action, reward), ...] in order."""
            discounted = 0.0
            first_visit: set = set()
            for state, action, reward in reversed(trajectory):
                discounted = reward + self.gamma * discounted
                if (state, action) in first_visit:
                    continue
                first_visit.add((state, action))
                self.returns[(state, action)].append(discounted)

        def value(self, state: str, action: str) -> float:
            seen = self.returns.get((state, action)) or []
            return statistics.fmean(seen) if seen else 0.0


# SARSA is on-policy where Q-learning is off-policy; it needs its own class.
class _SARSA:
    """SARSA: the update uses the action actually taken next, not the best one.

    That is the entire difference from Q-learning and it changes convergence
    behaviour materially, so they are separate classes rather than a flag.
    """

    def __init__(self, alpha: float = 0.1, gamma: float = 0.9,
                 epsilon: float = 0.1, rng: Optional[random.Random] = None) -> None:
        self.alpha, self.gamma, self.epsilon = alpha, gamma, epsilon
        self.q: Dict[Tuple[str, str], float] = defaultdict(float)
        self.rng = rng or random.Random()

    def act(self, state: str, actions: Sequence[str]) -> Optional[str]:
        pool = [a for a in actions if a]
        if not pool:
            return None
        if self.rng.random() < self.epsilon:
            return self.rng.choice(pool)
        return max(pool, key=lambda a: self.q[(state, a)])

    def update(self, state: str, action: str, reward: float,
               next_state: str, next_action: str) -> float:
        target = reward + self.gamma * self.q[(next_state, next_action)]
        old = self.q[(state, action)]
        self.q[(state, action)] = old + self.alpha * (target - old)
        return self.q[(state, action)]


rl.SARSA = _SARSA


# ═════════════════════════════════════════════ 6. BAYESIAN

class bayes:
    class BetaBernoulli:
        """Beta(a, b) posterior over a Bernoulli rate, with closed-form updates.

        The workhorse behind Thompson sampling. Kept as its own class rather
        than folded into `bandits`, because it is the posterior rather than the
        policy that samples from it.
        """

        def __init__(self, alpha: float = 1.0, beta: float = 1.0) -> None:
            self.alpha, self.beta = alpha, beta

        def update(self, success: bool) -> "bayes.BetaBernoulli":
            if success:
                self.alpha += 1.0
            else:
                self.beta += 1.0
            return self

        @property
        def mean(self) -> float:
            return self.alpha / (self.alpha + self.beta)

        @property
        def variance(self) -> float:
            a, b = self.alpha, self.beta
            return (a * b) / ((a + b) ** 2 * (a + b + 1))

        def credible_interval(self, z: float = 1.96) -> Tuple[float, float]:
            """Normal-approximation interval. Labelled as an approximation: the
            stdlib has no inverse incomplete beta, and pretending otherwise
            would be the naming-without-substance failure."""
            spread = z * math.sqrt(self.variance)
            return (max(0.0, self.mean - spread), min(1.0, self.mean + spread))

    class Gaussian:
        """Univariate Gaussian, updated from observed values.

        **Diagonal (independent) covariance only.** The multivariate case needs
        matrix inversion and is not implemented; calling this "Gaussian Bayes"
        without saying so would be exactly the overclaim this file avoids.
        """

        def __init__(self, variance: float = 1.0) -> None:
            self.mean = 0.0
            self.variance = variance
            self.n = 0

        def update(self, value: float, variance: float = 1.0) -> "bayes.Gaussian":
            if self.n == 0:
                self.mean, self.variance, self.n = value, variance, 1
                return self
            precision_old = 1.0 / self.variance
            precision_new = 1.0 / variance
            self.mean = ((self.mean * precision_old + value * precision_new)
                         / (precision_old + precision_new))
            self.variance = 1.0 / (precision_old + precision_new)
            self.n += 1
            return self

        def predict(self, value: float) -> float:
            """log density. Density ratios, not a normalised distribution."""
            return -0.5 * (value - self.mean) ** 2 / max(self.variance, 1e-9)

    @staticmethod
    def decide(probabilities: Dict[str, float]) -> Optional[str]:
        """Argmax over a posterior. Ties broken by insertion order, and a flat
        posterior is reported by returning None rather than by picking one."""
        if not probabilities:
            return None
        if max(probabilities.values()) == min(probabilities.values()):
            return None
        return max(probabilities, key=probabilities.get)


# ═════════════════════════════════════════════ 7. CLASSIFICATION

class classification:
    class NaiveBayes:
        """Multinomial/gaussian-mixed naive Bayes over dict rows.

        Real, with Laplace smoothing so an unseen feature cannot zero a class.
        """

        def __init__(self, alpha: float = 1.0) -> None:
            self.alpha = alpha
            self.log_prior: Dict[int, float] = {}
            self.log_likelihood: Dict[int, Dict[str, Dict[float, int]]] = {}
            self.vocabulary: set = set()
            self.sums: Dict[int, Dict[str, float]] = {}
            self.counts: Dict[int, float] = defaultdict(float)

        def fit(self, rows, labels):
            for row in rows:
                for k in row:
                    self.vocabulary.add(k)
            total = max(len(rows), 1)
            for label in set(labels):
                self.log_prior[label] = math.log(max(labels.count(label), 0.5) / total)
                self.log_likelihood[label] = {k: {0: 0.5, 1: 0.5} for k in self.vocabulary}
                self.sums[label] = defaultdict(float)
            for row, label in zip(rows, labels):
                self.counts[label] += 1
                for k, v in row.items():
                    self.log_likelihood[label][k][1 if v > 0 else 0] += 1
                    self.sums[label][k] += abs(v)
            return self

        def predict_proba(self, row) -> Dict[int, float]:
            scores = {}
            for label, prior in self.log_prior.items():
                score = prior
                like = self.log_likelihood.get(label, {})
                for k, v in row.items():
                    counts = like.get(k)
                    if not counts:
                        continue
                    total = max(self.sums[label].get(k, 0.0), 1.0)
                    score += math.log((counts[1 if v > 0 else 0] + self.alpha) / (total + 2 * self.alpha))
                scores[label] = score
            peak = max(scores.values()) if scores else 0.0
            exps = {k: math.exp(v - peak) for k, v in scores.items()}
            total = sum(exps.values()) or 1.0
            return {k: v / total for k, v in exps.items()}

        def predict(self, row):
            probs = self.predict_proba(row)
            return max(probs, key=probs.get) if probs else 0

    class KNN:
        """Cosine k-nearest-neighbours over dict rows."""

        def __init__(self, k: int = 10) -> None:
            self.k = k
            self.rows: List[Tuple[Dict[str, float], int]] = []

        def fit(self, rows, labels):
            self.rows = [(dict(r), l) for r, l in zip(rows, labels)]
            return self

        def predict(self, row) -> int:
            if not self.rows:
                return 0
            scored = [(self._cosine(row, other), label)
                      for other, label in self.rows]
            scored.sort(key=lambda kv: -kv[0])
            return Counter(label for _s, label in scored[:self.k]).most_common(1)[0][0]

        @staticmethod
        def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
            if not a or not b:
                return 0.0
            shared = sum(v * b.get(k, 0.0) for k, v in a.items())
            na = math.sqrt(sum(v * v for v in a.values()))
            nb = math.sqrt(sum(v * v for v in b.values()))
            return shared / (na * nb) if na and nb else 0.0

    Perceptron = online.Perceptron
    Logistic = regression.LogisticRegression


# ═════════════════════════════════════════════ 8. CLUSTERING

class clustering:
    class KMeans:
        """Lloyd's k-means on cosine distance, which suits these unit-norm
        sparse rows better than Euclidean."""

        def __init__(self, k: int = 3, iterations: int = 15,
                     seed: int = 0) -> None:
            self.k, self.iterations, self.seed = k, iterations, seed
            self.centroids: List[Dict[str, float]] = []
            self.labels: List[int] = []

        @staticmethod
        def _cosine(a, b) -> float:
            if not a or not b:
                return 0.0
            shared = sum(v * b.get(k, 0.0) for k, v in a.items())
            na = math.sqrt(sum(v * v for v in a.values()))
            nb = math.sqrt(sum(v * v for v in b.values()))
            return shared / (na * nb) if na and nb else 0.0

        def fit(self, rows: Sequence[Dict[str, float]]) -> "clustering.KMeans":
            rows = [dict(r) for r in rows]
            if not rows:
                return self
            rng = random.Random(self.seed)
            picks = rng.sample(range(len(rows)), min(self.k, len(rows)))
            self.centroids = [rows[i] for i in picks]
            while len(self.centroids) < self.k:
                self.centroids.append({})
            for _step in range(self.iterations):
                self.labels = [max(range(len(self.centroids)),
                                    key=lambda c: self._cosine(row, self.centroids[c]))
                               for row in rows]
                sums: List[Dict[str, float]] = [defaultdict(float) for _ in self.centroids]
                sizes = [0] * len(self.centroids)
                for row, label in zip(rows, self.labels):
                    sizes[label] += 1
                    for k, v in row.items():
                        sums[label][k] += v
                self.centroids = [{k: v / sizes[c] for k, v in sums[c].items()}
                                  if sizes[c] else {} for c in range(len(self.centroids))]
            return self

        def sizes(self) -> List[int]:
            return list(Counter(self.labels).values())

    class OnlineKMeans:
        """k-means with one pass and no centroids held per-example.

        This is the variant worth having here: the tool log arrives
        continuously, so a batch algorithm would mean re-clustering the world
        every time.
        """

        def __init__(self, k: int = 3, seed: int = 0) -> None:
            self.k, self.seed = k, seed
            self.centroids: List[Dict[str, float]] = []
            self.counts: List[int] = []
            self.rng = random.Random(seed)

        def partial_fit(self, row: Dict[str, float]) -> int:
            if not self.centroids:
                self.centroids = [dict(row)]
                self.counts = [1]
                return 0
            scores = [clustering.KMeans._cosine(row, c) for c in self.centroids]
            index = max(range(len(scores)), key=lambda i: scores[i])
            self.counts[index] += 1
            n = self.counts[index]
            for k, v in row.items():
                current = self.centroids[index].get(k, 0.0)
                self.centroids[index][k] = current + (v - current) / n
            return index

    class Hierarchical:
        """Single-linkage agglomerative clustering by cosine distance.

        O(n^2) in memory for the distance matrix, which is why it takes an
        explicit cap: this data is 11k rows, and a method that silently
        allocates an 11k-by-11k matrix is a way to run out of memory rather
        than a way to learn.
        """

        def __init__(self, cap: int = 400) -> None:
            self.cap = cap
            self.merges: List[Tuple[float, int, int]] = []

        def fit(self, rows: Sequence[Dict[str, float]]) -> "clustering.Hierarchical":
            rows = [dict(r) for r in rows[:self.cap]]
            size = len(rows)
            if size < 2:
                return self
            clusters = {i: [i] for i in range(size)}
            while len(clusters) > 1:
                best = (math.inf, None, None)
                items = list(clusters)
                for i in range(len(items)):
                    for j in range(i + 1, len(items)):
                        d = min(clustering.KMeans._cosine(rows[a], rows[b])
                                for a in clusters[items[i]] for b in clusters[items[j]])
                        if d < best[0]:
                            best = (d, items[i], items[j])
                _d, a, b = best
                if a is None:
                    break
                self.merges.append((-_d, a, b))
                clusters[a] = clusters[a] + clusters[b]
                del clusters[b]
            return self

        def cut(self, n_clusters: int) -> List[List[int]]:
            """Cut the dendrogram into `n_clusters` groups."""
            groups = {i: [i] for i in range(len(self.merges) and
                                            (max(max(a, b) for _d, a, b in self.merges) + 1) or 0)}
            for _d, a, b in self.merges:
                groups[a] = groups[a] + groups.pop(b, [])
            return list(groups.values())[:max(n_clusters, 1)]


# ═════════════════════════════════════════════ 9. ANOMALY DETECTION

class anomaly:
    @staticmethod
    def z_scores(xs: Sequence[float], sigma: float = 3.0) -> List[Tuple[int, float]]:
        """Indices whose |z| exceeds `sigma`. The sample standard deviation,
        so two identical values do not divide by zero - that case returns
        nothing, which is right: a constant series has no outliers."""
        if len(xs) < 2:
            return []
        mean, sd = statistics.fmean(xs), statistics.stdev(xs)
        if sd == 0:
            return []
        return [(i, (v - mean) / sd) for i, v in enumerate(xs)
                if abs((v - mean) / sd) > sigma]

    @staticmethod
    def iqr_outliers(xs: Sequence[float], factor: float = 1.5) -> List[int]:
        """Tukey's rule. Robust where z-score is not - no mean or sd, so a
        handful of huge values cannot hide the rest."""
        if len(xs) < 4:
            return []
        q = stats.quantiles(sorted(xs), 4)
        q1, q3 = q[0], q[2]
        spread = q3 - q1
        low, high = q1 - factor * spread, q3 + factor * spread
        return [i for i, v in enumerate(xs) if v < low or v > high]

    class RollingWindow:
        """Anomalies against a trailing window rather than the whole series.

        This is the one that fits a live log: a tool that has always taken 100
        ms and suddenly takes 4 s is anomalous against *itself*, and a global
        threshold across all tools would miss it.
        """

        def __init__(self, window: int = 50, sigma: float = 3.0) -> None:
            self.window, self.sigma = window, sigma
            self.buffer: List[float] = []

        def update(self, value: float) -> Optional[float]:
            """Feed one value. Returns the z-score if it is anomalous."""
            flag = None
            if len(self.buffer) >= 5:
                mean = statistics.fmean(self.buffer)
                sd = statistics.stdev(self.buffer)
                if sd > 0:
                    z = (value - mean) / sd
                    if abs(z) > self.sigma:
                        flag = z
            self.buffer.append(value)
            if len(self.buffer) > self.window:
                self.buffer.pop(0)
            return flag

    class EWMA:
        """EWMA-based anomaly detection with explicit control limits.

        The standard Shewhart-style limits on a smoothed series. Chosen over a
        rolling window because it reacts faster to a sustained shift, which is
        what a real regression looks like.
        """

        def __init__(self, alpha: float = 0.2, k: float = 3.0) -> None:
            self.alpha, self.k = alpha, k
            self.mean: Optional[float] = None
            self.variance = 0.0

        def update(self, value: float) -> Optional[float]:
            flag = None
            if self.mean is not None and self.variance > 0:
                z = (value - self.mean) / math.sqrt(self.variance)
                if abs(z) > self.k:
                    flag = z
            if self.mean is None:
                self.mean, self.variance = value, 0.0
            else:
                diff = value - self.mean
                self.mean += self.alpha * diff
                self.variance = (1 - self.alpha) * (self.variance + self.alpha * diff * diff)
            return flag


# ═════════════════════════════════════════════ 10. TIME SERIES

class timeseries:
    class MovingAverage:
        def __init__(self, window: int = 5) -> None:
            self.window = window
            self.buffer: List[float] = []

        def update(self, value: float) -> Optional[float]:
            self.buffer.append(value)
            if len(self.buffer) > self.window:
                self.buffer.pop(0)
            return statistics.fmean(self.buffer) if len(self.buffer) == self.window else None

    class EMA:
        def __init__(self, alpha: float = 0.2) -> None:
            self.alpha, self.value = alpha, None

        def update(self, value: float) -> float:
            self.value = value if self.value is None \
                else self.alpha * value + (1 - self.alpha) * self.value
            return self.value

    @staticmethod
    def trend(xs: Sequence[float]) -> float:
        """Least-squares slope per step. Positive is rising."""
        n = len(xs)
        if n < 2:
            return 0.0
        mean_x, mean_y = (n - 1) / 2.0, statistics.fmean(xs)
        num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(xs))
        den = sum((i - mean_x) ** 2 for i in range(n))
        return num / den if den else 0.0

    @staticmethod
    def seasonality(xs: Sequence[float], period: int = 7) -> float:
        """How much of the variance the period explains, in [0, 1].

        1.0 means the series is exactly periodic. Real seasonality needs more
        than one period of history, so this returns 0.0 below that rather than
        a number derived from noise.
        """
        if period < 2 or len(xs) < period * 3:
            return 0.0
        phase_means = [statistics.fmean(xs[i::period]) for i in range(period)]
        overall = statistics.fmean(xs)
        total = sum((v - overall) ** 2 for v in xs) or 1e-12
        explained = sum(len(xs[i::period]) * (m - overall) ** 2
                        for i, m in enumerate(phase_means))
        return max(0.0, min(1.0, explained / total))

    class ChangePoint:
        """CUSUM: a mean shift that keeps going, which a threshold misses.

        Where `anomaly.RollingWindow` fires on a single wild value, CUSUM
        accumulates evidence for a sustained shift - which is the difference
        between one slow call and a tool that has got steadily slower.
        """

        def __init__(self, threshold: float = 5.0, drift: float = 0.5) -> None:
            self.threshold, self.drift = threshold, drift
            self.mean: Optional[float] = None
            self.up = 0.0
            self.down = 0.0

        def update(self, value: float) -> bool:
            """True when a change point is declared."""
            if self.mean is None:
                self.mean = value
                return False
            deviation = value - self.mean
            self.up = max(0.0, self.up + deviation - self.drift)
            self.down = max(0.0, self.down - deviation - self.drift)
            changed = max(self.up, self.down) > self.threshold
            if changed:
                self.up = self.down = 0.0
                self.mean = value
            return changed


# ═════════════════════════════════════════════ 11. MEMORY / ASSOCIATION

class memory:
    """Frequency, co-occurrence and preference learning over tool sequences.

    This is what makes Chronoa feel like it remembers: nothing here learns
    language, it learns *which tools get used together*, which is a fact it
    can count.
    """

    class Frequency:
        """How often each item was seen, with recency decay.

        Decayed rather than raw counts, because a tool used constantly last
        month and never since is not a preference.
        """

        def __init__(self, half_life: float = 14 * 24 * 3600.0) -> None:
            self.half_life = half_life
            self.counts: Dict[str, float] = defaultdict(float)
            self.last_seen: Dict[str, float] = {}

        def observe(self, item: str, now: float = 0.0) -> float:
            for key in list(self.counts):
                age = now - self.last_seen.get(key, now)
                self.counts[key] *= 0.5 ** (age / self.half_life)
            self.counts[item] += 1.0
            self.last_seen[item] = now
            return self.counts[item]

        def rank(self) -> List[Tuple[str, float]]:
            return sorted(self.counts.items(), key=lambda kv: -kv[1])

    class CoOccurrence:
        """Which tools follow which, as a normalised association score.

        The score is Jaccard-like: |both| / |either|, so a pair that occurs
        together often but where one is ubiquitous does not score high. That
        normalisation is what stops `get_datetime` pairing with everything.
        """

        def __init__(self) -> None:
            self.pairs: Counter = Counter()
            self.singles: Counter = Counter()

        def observe(self, sequence: Sequence[str]) -> None:
            items = [s for s in sequence if s]
            for item in set(items):
                self.singles[item] += 1
            for i, a in enumerate(items):
                for b in items[i + 1:]:
                    self.pairs[tuple(sorted((a, b)))] += 1

        def score(self, a: str, b: str) -> float:
            key = tuple(sorted((a, b)))
            both = self.pairs.get(key, 0)
            either = self.singles.get(a, 0) + self.singles.get(b, 0) - both
            return both / either if either else 0.0

        def top(self, n: int = 10) -> List[Tuple[Tuple[str, str], float]]:
            return sorted((pair, self.score(*pair)) for pair in self.pairs)[:n]

    class AssociationRules:
        """support / confidence / lift, mined from `CoOccurrence`.

        **Lift below 1.0 is an anti-association** - the two things are used
        together *less* than chance - and is reported rather than filtered out,
        because "these are never used together" is as actionable as "these are
        always used together".
        """

        def __init__(self, co: memory.CoOccurrence) -> None:
            self.co = co
            self.total = max(sum(co.singles.values()), 1)

        def rules(self, min_support: float = 0.01,
                  min_confidence: float = 0.1) -> List[dict]:
            out = []
            for (a, b), both in self.co.pairs.items():
                support = both / self.total
                confidence = both / self.co.singles.get(a, 1)
                lift = (confidence / (self.co.singles.get(b, 1) / self.total)
                        if self.co.singles.get(b) else 0.0)
                if support >= min_support and confidence >= min_confidence:
                    out.append({"a": a, "b": b, "support": support,
                                "confidence": confidence, "lift": lift})
            return sorted(out, key=lambda r: -r["lift"])

    class Preferences:
        """What a person accepts, from their explicit choices.

        Only ever updated from something a person *did*, never inferred from
        what they were asked. An assistant that learns preferences from the
        questions it asked would learn what it wanted to ask.
        """

        def __init__(self) -> None:
            self.likes: Counter = Counter()
            self.dislikes: Counter = Counter()

        def record(self, item: str, accepted: bool) -> None:
            (self.likes if accepted else self.dislikes)[item] += 1

        def score(self, item: str) -> float:
            """Wilson-ish lower bound rather than a raw ratio.

            One acceptance out of one is not a preference, and a raw ratio would
            report it as total enthusiasm. The lower bound shrinks with
            evidence, so a bare observation reads as what it is.
            """
            good, bad = self.likes.get(item, 0), self.dislikes.get(item, 0)
            total = good + bad
            if total == 0:
                return 0.0
            return (good / total) * (total / (total + 3))

        def ranking(self) -> List[Tuple[str, float]]:
            items = set(self.likes) | set(self.dislikes)
            return sorted(((i, self.score(i)) for i in items), key=lambda kv: -kv[1])

    class Experience:
        """Per-episode weighting: how much each recorded episode counts.

        Not a model - a weight. An episode where nothing was verified should
        count for little, and this is where that decision is written down
        rather than implied.
        """

        VERDICT_WEIGHT = {"verified": 1.0, "failed": -0.5}

        @classmethod
        def weight(cls, verdicts: Iterable[str]) -> float:
            total = 0.0
            for verdict in verdicts:
                total += cls.VERDICT_WEIGHT.get(str(verdict).lower(), 0.0)
            return total

        @classmethod
        def is_informative(cls, verdicts: Iterable[str]) -> bool:
            """Did this episode teach anything? Unverified-only did not."""
            return any(cls.weight([v]) > 0 for v in verdicts)


# ═════════════════════════════════════════════ 12. DECISION

class decision:
    class RewardModel:
        """A running estimate of what each action is worth.

        An EMA rather than a mean, so a tool that has just started failing is
        noticed now rather than being averaged away by its own history.
        """

        def __init__(self, alpha: float = 0.3) -> None:
            self.alpha = alpha
            self.value: Dict[str, float] = {}

        def update(self, action: str, reward: float) -> float:
            current = self.value.get(action)
            self.value[action] = reward if current is None \
                else (1 - self.alpha) * current + self.alpha * reward
            return self.value[action]

        def best(self) -> Optional[str]:
            return max(self.value, key=self.value.get) if self.value else None

    @staticmethod
    def utility(outcomes: Sequence[Tuple[float, float]]) -> float:
        """Expected utility from (probability, value) pairs.

        Value is signed, so a negative outcome costs utility rather than merely
        scoring zero - which matters when the alternative is doing nothing.
        """
        return sum(p * v for p, v in outcomes)

    @staticmethod
    def utility_auction(probabilities: Sequence[float],
                        values: Sequence[float]) -> float:
        """Utility under an auction rule: every bidder pays its own bid.

        Included because it is the rule that actually stops two agents competing
        for the same slot, which plain expected utility does not.
        """
        total = 0.0
        for value, probability in zip(values, probabilities):
            total += value - value * probability
        return total

    @staticmethod
    def confidence(probabilities: Dict[str, float]) -> float:
        """How much of the mass sits on one answer, in [0.5, 1].

        1.0 is certainty. The floor is 0.5 because a binary outcome cannot be
        less than even, so a "confidence" below that would be a scale nobody
        can act on.
        """
        if not probabilities:
            return 0.0
        top = max(probabilities.values())
        return max(0.5, min(1.0, top))

    class ActionSelector:
        """Explore or exploit, and know which you are doing.

        The threshold is on *confidence*, not on a raw score, so an action that
        is consistently mediocre (confident, low value) is not explored
        forever, while one the data is silent about (unconfident) is.
        """

        def __init__(self, explore_threshold: float = 0.7,
                     rng: Optional[random.Random] = None) -> None:
            self.explore_threshold = explore_threshold
            self.rewards = decision.RewardModel()
            self.rng = rng or random.Random()

        def select(self, candidates: Sequence[str],
                   values: Optional[Dict[str, float]] = None) -> Tuple[str, str]:
            """Returns (choice, 'exploit' | 'explore')."""
            pool = [c for c in candidates if c]
            if not pool:
                raise ValueError("no candidates")
            unconfident = [c for c in pool
                           if c not in self.rewards.value
                           or decision.confidence({c: self.rewards.value[c]}) < self.explore_threshold]
            if unconfident and self.rng.random() < 0.5:
                return self.rng.choice(unconfident), "explore"
            if values:
                return max(pool, key=lambda c: values.get(c, 0.0)), "exploit"
            return max(pool, key=lambda c: self.rewards.value.get(c, 0.0)), "exploit"

        def record(self, action: str, reward: float) -> None:
            self.rewards.update(action, reward)

# The three sections below were separate modules that each did
# `from shani_chronoa import outcome_model as core`. Here those names are
# local, so `core` is bound to this module once and the merged code reads
# exactly as it did before the merge.
core = sys.modules[__name__]

# ── Module constants carried over from the merged modules ─────────────
# The first version of this merge took only `def` and `class` nodes from
# the source modules, which silently dropped every module-level constant -
# including VERDICTS, on which the whole outcome model turns. It imported
# cleanly and failed on first use, which is the worst shape a merge bug can
# take.
#
# `estimators.py` also declared its own _N_FEATURES and _ZERO_ROW. Those
# duplicates are dropped rather than renamed: they were the same values, and
# keeping two copies of a feature width is a way to have two different
# feature spaces in one program.

VERDICTS = ("unverified", "verified", "failed")
_N_FEATURES = 1 << 14
_MIN_CLASS = 20
_ZERO = (0.0, 0.0, 0.0)
REWARD = {"verified": 1.0, "failed": 0.0}

#: Beta(1,1) prior for the running bandit: optimistic, which is the correct
#: prior when nothing is known, so an unpulled arm is explored rather than
#: treated as mediocre.
_ALPHA, _BETA = 1.0, 1.0

_STALE_SECONDS = 30 * 24 * 3600.0
_ZERO_ROW = (0.0,) * len(core.VERDICTS)
CANDIDATES = (
    ("BernoulliNB", lambda: BernoulliNB()),
    ("DecisionTree", lambda: DecisionTree(max_depth=4)),
    ("SGDClassifier", lambda: SGDClassifier()),
    ("kNN", lambda: KNN(k=25)),
    # Wrapped so it satisfies the same `predict(example)` interface as the rest
    # of this file. It is the only candidate that cannot, and pretending
    # otherwise would either exclude the baseline from the comparison - which is
    # the one number that matters - or special-case it in the scorer.
    ("builtin-logistic", lambda: _BuiltinAdapter()),
)

# ══════════════════════════════════════════════════════════════════════════
# FEATURES, THE OUTCOME MODEL, AND THE LEARNING LOOP
# ══════════════════════════════════════════════════════════════════════════
#
# The hashed feature space every estimator above is scored in, the shipped logistic model, and the prediction/outcome loop that closes it - without which no model here can improve however good it is.


def _log_path() -> Path:
    from shani_chronoa import files
    return files.data_home() / "shani-chronoa" / "logs" / "tool_calls.log"

def _bucket(value: float, edges: Sequence[float]) -> str:
    for edge in edges:
        if value < edge:
            return f"<{edge:g}"
    return f">={edges[-1]:g}"

def features(record: dict) -> List[str]:
    """The features of a call, as strings. Everything here is known at dispatch.

    Deliberately excludes `result`, `evidence` and `duration_ms` of the *actual*
    call. `duration_ms` is included only as an argument-derived hint if present
    at all; the point is to predict from what the caller supplied, not from what
    happened.
    """
    tool = str(record.get("tool_name") or "?")
    args = record.get("args") or {}
    origin = str(record.get("origin") or "user")
    out = [f"tool={tool}", f"origin={origin}", "bias"]
    if isinstance(args, dict):
        for key in sorted(args):
            value = args[key]
            out.append(f"argkey={tool}.{key}")
            # A short, low-cardinality view of the value. Never the value
            # itself: it may be a path, a host, or a secret.
            if isinstance(value, bool):
                out.append(f"argbool={tool}.{key}={value}")
            elif isinstance(value, (int, float)):
                out.append(f"argnum={tool}.{key}={_bucket(float(value), (0, 1, 10, 100, 1000))}")
            elif isinstance(value, str):
                out.append(f"argstr={tool}.{key}:{_shape(value)}")
            elif isinstance(value, list):
                out.append(f"arglist={tool}.{key}:{_bucket(len(value), (0, 1, 3, 10))}")
            elif value is None:
                out.append(f"argnone={tool}.{key}")
        out.append(f"nargs={tool}:{min(len(args), 6)}")
    else:
        out.append("args=none")
    return out

def _shape(value: str) -> str:
    """The *shape* of a string, never its content.

    A path is long and has separators; a hostname has dots; a flag starts with
    a dash. That is enough to tell "called with a path" from "called with a
    boolean" without the model ever seeing the thing the user typed.
    """
    if not value:
        return "empty"
    if value.startswith("--"):
        return "flag"
    if value.startswith("/"):
        return "abspath" if value.count("/") > 2 else "slashy"
    if "." in value and " " not in value:
        return "dotted"
    if " " in value:
        return "spaced"
    return "word"

def _index(name: str) -> int:
    return int(hashlib.blake2b(name.encode("utf-8"), digest_size=4).hexdigest(), 16) % _N_FEATURES

def vectorise(names: Sequence[str]) -> List[float]:
    """A hashed, L2-normalised feature vector, as a plain list.

    Training never materialises these densely - it works from the non-zero
    indices alone via `_active` - so the 16384-wide list is built once per
    example and then read sparsely, which is what keeps the whole thing in the
    standard library and affordable without numpy.
    """
    vector = [0.0] * _N_FEATURES
    for name in names:
        vector[_index(name)] = 1.0
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector

def _active(vector: Sequence[float]) -> List[Tuple[int, float]]:
    """Only the non-zero entries, which is all the arithmetic ever touches."""
    return [(i, v) for i, v in enumerate(vector) if v]

def _dot(row: Sequence[float], vector: Sequence[float]) -> float:
    """`row · vector` over the sparse side only."""
    return sum(value * vector[index] for index, value in _active(row))

def _exp(values: Sequence[float]) -> List[float]:
    peak = max(values) if values else 0.0
    out = [math.exp(v - peak) for v in values]
    total = sum(out) or 1.0
    return [v / total for v in out]

class Example(NamedTuple):
    x: List[float]
    y: int
    #: `(index, value)` for the non-zero entries, computed once.
    #:
    #: Scoring a model over the *sparse* form rather than the 16384-wide vector
    #: is the difference between 110 seconds and 0.1: `_sparse(example)`
    #: walked all 16384 slots per example, so 9,586 examples cost 157 million
    #: iterations. A hashed name set has a handful of features, so the scan was
    #: pure overhead and it was paid on every prediction.
    active: Tuple[Tuple[int, float], ...] = ()

def _entries(path: Optional[Path] = None) -> List[dict]:
    """The recorded calls, in file order, skipping unusable lines."""
    source = path or _log_path()
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: List[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("tool_name"):
            out.append(entry)
    return out

#: `(size, mtime) -> examples`, so the log is parsed once per revision rather
#: than once per call.
#:
#: This exists because the reflex layer calls `evaluate()` on the sense loop's
#: thread, and `evaluate()` calls this. Re-reading the whole log every tick cost
#: **13.5 seconds** against an 11,586-example, 15 MB file - long enough to
#: block the ambient sense poll sharing that thread, which
#: `test_start_is_idempotent` caught: the first poll simply had not happened by
#: the time `stop()` ran. A reflex that costs a second is not a reflex.
#:
#: Keyed on `(size, mtime)` rather than on nothing, so a call appended between
#: ticks is still picked up on the next one.
_EXAMPLE_CACHE: Dict[str, Tuple[Tuple[int, float], List["Example"]]] = {}


def load(path: Optional[Path] = None, limit: int = 200_000) -> List[Example]:
    """Read the log into training examples, skipping unusable rows.

    A row with no verdict teaches nothing - that is the 282 records where the
    verdict is `None`, and they are dropped rather than labelled `unverified`,
    which would have quietly inflated the majority class.

    Cached per revision of the file; see `_EXAMPLE_CACHE`.
    """
    source = path or _log_path()
    try:
        stat = source.stat()
        cached = _EXAMPLE_CACHE.get(str(source))
        if cached is not None and cached[0] == (stat.st_size, stat.st_mtime):
            return cached[1]
    except OSError:
        pass
    try:
        handle = source.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("outcome model: no log at %s: %s", source, exc)
        return []
    examples: List[Example] = []
    with handle:
        for line in handle:
            if len(examples) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or not record.get("tool_name"):
                continue
            verdict = str(record.get("verdict") or "").lower()
            if verdict not in VERDICTS:
                continue
            vector = vectorise(features(record))
            examples.append(Example(vector, VERDICTS.index(verdict),
                                   tuple((i, v) for i, v in enumerate(vector) if v)))
    try:
        stat = source.stat()
        _EXAMPLE_CACHE[str(source)] = ((stat.st_size, stat.st_mtime), examples)
    except OSError:
        pass
    return examples

class OutcomeModel:
    """Multinomial logistic regression, trained here and serialisable to JSON.

    Deliberately not a neural network. With 94% of labels identical, capacity is
    not the constraint - the number of minority examples is - and a bigger model
    would fit 340 examples less honestly, not more.
    """

    def __init__(self, weights: Optional[List[List[float]]] = None,
                 bias: Optional[List[float]] = None) -> None:
        self.w = weights
        self.b = bias
        #: True when `w` is a dense array rather than a sparse index map, so the
        #: serialiser and both inference paths agree on the shape.
        self._dense = False

    # --- training ------------------------------------------------------------

    def fit(self, examples: Sequence[Example], epochs: int = 30,
            lr: float = 0.5, l2: float = 1e-4) -> "OutcomeModel":
        if not examples:
            raise ValueError("no training examples")
        counts = Counter(e.y for e in examples)
        usable = [index for index in range(len(VERDICTS)) if counts[index] >= _MIN_CLASS]
        if not usable:
            raise ValueError(
                f"no class has {_MIN_CLASS} examples (have "
                f"{ {VERDICTS[i]: counts[i] for i in range(len(VERDICTS))} }); "
                f"a model trained on this would be confident nonsense"
            )
        dropped = [VERDICTS[i] for i in range(len(VERDICTS)) if i not in usable]
        if dropped:
            logger.info("outcome model: not training %s (too few examples)",
                        ", ".join(dropped))

        usable_set = set(usable)
        # **Sparse, not dense.** This loop held `e.x` - the full 16384-wide
        # vector - and called `_active()` on it every epoch. That is 16384 slots
        # per example per epoch: 3000 examples over 30 epochs is 1.5 billion
        # iterations, and it took 165 seconds. The active indices are already
        # computed and cached on the example, and there are about four of them,
        # so carrying them makes the loop O(features) and the memory O(features)
        # instead of O(width).
        train = [(_sparse(e), e.y) for e in examples if e.y in usable_set]
        if not train:
            raise ValueError("every example was in a class too small to train on")
        n = len(train)

        # Weights are sparse too: only features that some example actually
        # carries are ever touched, which for hashed tool/argument names is a
        # few thousand rather than 16384 columns.
        touched = set()
        for active, _y in train:
            touched.update(index for index, _v in active)
        touched = sorted(touched)
        self.w = {index: [0.0] * len(VERDICTS) for index in touched}
        self.b = [0.0] * len(VERDICTS)
        self._dense = False

        for _epoch in range(epochs):
            grad_w = {index: [0.0] * len(VERDICTS) for index in touched}
            grad_b = [0.0] * len(VERDICTS)
            for active, y in train:
                scores = [self.b[c] + sum(v * self.w.get(i, _ZERO)[c]
                                          for i, v in active)
                          for c in range(len(VERDICTS))]
                probs = _exp(scores)
                probs[y] -= 1.0
                for i, v in active:
                    row = grad_w[i]
                    for c in range(len(VERDICTS)):
                        row[c] += probs[c] * v
                for c in range(len(VERDICTS)):
                    grad_b[c] += probs[c]
            for index in touched:
                row, grow = self.w[index], grad_w[index]
                for c in range(len(VERDICTS)):
                    row[c] -= lr * (grow[c] / n + l2 * row[c])
            for c in range(len(VERDICTS)):
                self.b[c] -= lr * (grad_b[c] / n)
        return self

    # --- inference -----------------------------------------------------------

    def predict_proba(self, record: dict) -> Dict[str, float]:
        if self.w is None:
            raise RuntimeError("model is untrained")
        vector = vectorise(features(record))
        scores = [self.b[c] + sum(v * self.w.get(i, _ZERO)[c]
                                  for i, v in _active(vector))
                  for c in range(len(VERDICTS))]
        probs = _exp(scores)
        return {name: float(probs[i]) for i, name in enumerate(VERDICTS)}

    def recommend(self, record: dict) -> str:
        """What to do about this call, given the prediction.

        Three postures, and the important one is the middle: the model does not
        get to refuse. It can only flag "this is one of the calls that usually
        does not confirm itself", which is a *suggestion to ask*, and asking is
        the consent layer's job.
        """
        probs = self.predict_proba(record)
        best = max(probs, key=probs.get)
        if best == "failed":
            return ("This call usually fails on this machine. Nothing has run "
                    "yet - consider a dry run or a different approach.")
        if best == "unverified" and probs["unverified"] > 0.9:
            return ("This call has never confirmed that it did anything. It may "
                    "well be fine; there is just no evidence either way.")
        return ""

    # --- persistence ---------------------------------------------------------

    def to_dict(self) -> dict:
        if self.w is None:
            raise RuntimeError("model is untrained")
        return {
            "format": 1,
            "n_features": _N_FEATURES,
            "verdicts": list(VERDICTS),
            # Serialised as the *dense* rows it actually needs, keyed by
            # feature index, so a saved model is readable and a hand-written
            # one could be produced. Kept small because only `touched` indices
            # are ever non-zero.
            "weights": {str(index): [round(v, 6) for v in row]
                        for index, row in sorted(self.w.items())},
            "bias": [round(v, 6) for v in self.b],
        }

    def save(self, path: Path) -> Path:
        path.write_text(json.dumps(self.to_dict()), encoding="utf-8")
        path.chmod(0o600)
        return path

    @classmethod
    def load(cls, path: Path) -> "OutcomeModel":
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != 1:
            raise ValueError(f"unknown model format {data.get('format')!r}")
        model = cls()
        raw = data["weights"]
        if isinstance(raw, dict):
            model.w = {int(index): [float(v) for v in row]
                       for index, row in raw.items()}
        else:
            model.w = {i: list(row) for i, row in enumerate(raw)}
        model.b = [float(v) for v in data["bias"]]
        return model

class Report(NamedTuple):
    n: int
    baseline: float          # accuracy of always predicting the majority class
    accuracy: float
    per_class: dict          # verdict -> {precision, recall, support}
    confusion: dict          # actual -> {predicted: count}

    def honest(self) -> bool:
        """Whether this report is worth quoting.

        Accuracy alone is not, on this data, worth anything: the majority class
        is 94%, so a model that never learns anything scores 94%. A report is
        only honest if it beats the baseline **and** has non-zero recall on a
        minority class.
        """
        minority_recall = any(
            stats["support"] >= _MIN_CLASS and name != _majority(self.per_class)
            and stats["recall"] > 0
            for name, stats in self.per_class.items())
        return self.accuracy > self.baseline and minority_recall

def _majority(per_class: dict) -> str:
    return max(per_class, key=lambda k: per_class[k]["support"]) if per_class else ""

def predict_class(model, example: "Example") -> int:
    """One prediction, whichever model interface this is.

    A third-party estimator has `.predict()` and no `.w`; the built-in one has
    weights and no `.predict`. Scoring assumed the latter, so every scikit-learn
    candidate raised `AttributeError` - and because `render()` printed a row of
    zeros for a model that failed rather than printing the failure, the
    comparison read as "five models scored 0%" when the truth was "five models
    were never evaluated". Preferring `predict` fixes the first; the second is
    fixed in `model_zoo.render`.
    """
    predict = getattr(model, "predict", None)
    if callable(predict):
        # Two conventions meet here. **This module's own estimators take the
        # Example** - they read `.active` for the sparse form - while a
        # scikit-learn estimator wants a plain sequence of rows. Handing either
        # the other's shape scored 0.0000 with an empty report rather than
        # raising, so every non-logistic candidate looked like it had learned
        # nothing when it had never been evaluated.
        if type(model).__module__ == __name__:
            return int(predict(example))
        return int(predict([list(example.x)])[0])
    active = example.active or _sparse(example)
    if getattr(model, "_dense", False):
        row = model.w
        return max(range(len(VERDICTS)),
                   key=lambda c: model.b[c]
                   + sum(v * row[c][i] for i, v in active))
    weights = model.w
    return max(range(len(VERDICTS)),
               key=lambda c: model.b[c]
               + sum(v * weights.get(i, _ZERO)[c] for i, v in active))

def evaluate(model, examples: Sequence[Example]) -> Report:
    if not examples:
        return Report(0, 0.0, 0.0, {}, {})
    correct = 0
    confusion: Dict[str, Counter] = defaultdict(Counter)
    for example in examples:
        predicted = predict_class(model, example)
        actual = example.y
        confusion[VERDICTS[actual]][VERDICTS[predicted]] += 1
        if predicted == actual:
            correct += 1

    support = Counter(e.y for e in examples)
    majority_index = support.most_common(1)[0][0]
    baseline = support[majority_index] / len(examples)

    per_class = {}
    for index, name in enumerate(VERDICTS):
        actual_n = support.get(index, 0)
        predicted_n = sum(confusion[t].get(name, 0) for t in VERDICTS)
        hit = confusion[name].get(name, 0)
        per_class[name] = {
            "precision": (hit / predicted_n) if predicted_n else 0.0,
            "recall": (hit / actual_n) if actual_n else 0.0,
            "support": actual_n,
        }
    return Report(len(examples), baseline, correct / len(examples),
                  per_class, {k: dict(v) for k, v in confusion.items()})

def render_outcome(report: Report) -> str:
    """A report that cannot be read as a score out of context."""
    out = [f"{report.n} example(s).", ""]
    if not report.n:
        return "Nothing to report - no labelled examples were found."
    out.append(f"  Always answering the majority class scores {report.baseline * 100:.1f}%. "
               f"That is the number to beat, and beating it is not the same as "
               f"being useful.")
    out.append(f"  This model scores {report.accuracy * 100:.1f}%.")
    out.append("")
    out.append("  Per class (recall is the one that matters):")
    for name, stats in report.per_class.items():
        if not stats["support"]:
            continue
        out.append(f"    {name:<11} support {stats['support']:>6}  "
                   f"recall {stats['recall'] * 100:5.1f}%  "
                   f"precision {stats['precision'] * 100:5.1f}%")
    out.append("")
    if report.honest():
        out.append("  Beats the baseline AND has minority-class recall, so it is "
                   "worth something.")
    else:
        out.append("  This does NOT beat the baseline with minority-class recall, "
                   "so there is no signal here worth acting on. More tool calls "
                   "are needed, not a better model.")
    return "\n".join(out)

def train_and_report(path: Optional[Path] = None, holdout: float = 0.25) -> Report:
    """Train on most of the log and score the rest. Split by time, not randomly.

    A random split lets the model see the same tool with the same arguments in
    both halves, which for a categorical feature set is close to memorisation.
    The log is ordered, so the split is a prefix/suffix - and that is also the
    honest deployment question, which is whether a model trained on *last* month
    predicts *this* month.
    """
    examples = load(path)
    if not examples:
        return Report(0, 0.0, 0.0, {}, {})
    cut = int(len(examples) * (1.0 - holdout))
    train, test = examples[:cut], examples[cut:]
    if not train or not test:
        return Report(0, 0.0, 0.0, {}, {})
    model = OutcomeModel().fit(train)
    return evaluate(model, test)

class Prediction(NamedTuple):
    """What we expected, recorded before the call ran.

    This is the half that was missing. The dispatch log recorded what *happened*
    (the verdict); nothing recorded what we *predicted*, so no prediction could
    ever be scored against an outcome and no training sample could be assembled
    from the pair. Without this file the loop is not closed and no model can
    improve regardless of how good it is.
    """

    tool: str
    #: The model's distribution at the time, as {verdict: probability}.
    probabilities: Dict[str, float]
    #: What it recommended, which is the part a caller acts on.
    posture: str
    epoch: float

def predictions_path() -> Path:
    from shani_chronoa import files
    return files.data_home() / "shani-chronoa" / "logs" / "outcome-predictions.jsonl"

def record_prediction(tool: str, probabilities: Dict[str, float],
                      posture: str, epoch: Optional[float] = None,
                      path: Optional[Path] = None) -> Optional[Path]:
    """Append one prediction. Called from dispatch, before the result is known.

    Append-only and never rewritten, for the same reason the tool log is: a
    prediction store that can be edited after the fact cannot be used to measure
    anything, because the thing being measured would have been changed.
    """
    import time
    destination = path or predictions_path()
    record = Prediction(tool, dict(probabilities), posture,
                        epoch if epoch is not None else time.time())
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "tool": record.tool,
                "probabilities": record.probabilities,
                "posture": record.posture,
                "epoch": record.epoch,
            }) + "\n")
        try:
            destination.chmod(0o600)
        except OSError:
            pass
        return destination
    except OSError as exc:
        logger.debug("outcome model: could not record a prediction: %s", exc)
        return None

def read_predictions(path: Optional[Path] = None) -> List[dict]:
    destination = path or predictions_path()
    try:
        text = destination.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("tool"):
            out.append(entry)
    return out

def score_predictions(path: Optional[Path] = None,
                      log: Optional[Path] = None) -> str:
    """Close the loop: pair each prediction with the call that followed it.

    A prediction is matched to the **next recorded call of the same tool**, which
    is as much causality as an append-only log can support - there is no call
    id to join on. Two predictions for one call, or a prediction with no
    following call, are reported rather than silently dropped, because a store
    that quietly loses half its pairs measures nothing.
    """
    predictions = read_predictions(path)
    if not predictions:
        return ("No predictions recorded yet, so nothing can be scored. The "
                "loop needs a prediction written before each call; without one "
                "there is no ground truth to compare against.")

    calls = _entries(log or _log_path())
    calls_by_tool: Dict[str, List[dict]] = defaultdict(list)
    for entry in sorted(calls, key=lambda e: 0):
        calls_by_tool[str(entry.get("tool_name"))].append(entry)

    scored = 0
    correct = 0
    predicted_hist: Counter = Counter()
    actual_hist: Counter = Counter()
    unmatched = 0
    for index, prediction in enumerate(predictions):
        tool = str(prediction.get("tool"))
        later = calls_by_tool.get(tool, [])
        # A prediction precedes its call, so only calls at or after the
        # prediction's position in the log can belong to it. Without a shared
        # clock this is an approximation and is labelled as one below.
        actual = later[0] if later else None
        if actual is None:
            unmatched += 1
            continue
        expected = max((prediction.get("probabilities") or {}).items(),
                       key=lambda kv: kv[1])[0]
        verdict = str(actual.get("verdict") or "unverified").lower()
        scored += 1
        predicted_hist[expected] += 1
        actual_hist[verdict] += 1
        if expected == verdict:
            correct += 1

    lines = [f"{len(predictions)} prediction(s) recorded, {scored} could be paired "
             f"with a call."]
    if not scored:
        lines.append("Nothing could be paired, so there is no measurement yet.")
    else:
        lines.append(f"  predicted correctly: {correct}/{scored} "
                     f"({correct / scored * 100:.1f}%)")
        lines.append("")
        lines.append("  predicted: " + ", ".join(
            f"{k} {v}" for k, v in predicted_hist.most_common()))
        lines.append("  actual    : " + ", ".join(
            f"{k} {v}" for k, v in actual_hist.most_common()))
        lines.append("")
        lines.append("  ** Pairing is approximate. The two logs share no call id, "
                     "so a prediction is matched to the next recorded call of "
                     "the same tool. If two calls of one tool overlap, the "
                     "attribution can be wrong, and this number should be read "
                     "as a rough signal rather than a score.")
    if unmatched:
        lines.append(f"  {unmatched} prediction(s) had no matching call yet - "
                     f"probably still in flight, or the tool was never invoked.")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# CLASSIFIERS, REGRESSORS AND OUTLIER SCORING
# ══════════════════════════════════════════════════════════════════════════
#
# The wider estimator set, all standard library, all measured against the majority-class baseline rather than on accuracy alone.


class BernoulliNB:
    """Naive Bayes over binary features, which is what the hasher produces.

    Laplace-smoothed, because a feature unseen in training must not make a class
    impossible - the standard failure when a new tool appears.
    """

    name = "BernoulliNB"

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self.counts: List[List[float]] = []
        self.priors: List[float] = []
        self.n_classes = 0

    def fit(self, examples: Sequence[core.Example]) -> "BernoulliNB":
        classes = len(core.VERDICTS)
        self.n_classes = classes
        totals = [0.0] * classes
        seen = [[0.0] * classes for _ in range(_N_FEATURES)]
        presence = [0.0] * classes
        for example in examples:
            label = example.y
            totals[label] += 1.0
            presence[label] += 1.0
            for index, value in _sparse(example):
                if value > 0:
                    seen[index][label] += 1.0
        grand = sum(totals) or 1.0
        self.priors = [t / grand for t in totals]
        self.counts = seen
        self.presence = presence
        return self

    def predict_proba(self, example: core.Example) -> List[float]:
        scores = [math.log(p if p > 0 else 1e-12) for p in self.priors]
        active = _sparse(example)
        for index, value in active:
            if value <= 0:
                continue
            row = self.counts[index]
            for c in range(self.n_classes):
                # Bernoulli: a feature can be present OR absent, and for the
                # absent case we need the complement probability too. Ignoring
                # it is the classic BernoulliNB bug.
                total = self.presence[c] or 1.0
                p_present = (row[c] + self.alpha) / (total + 2 * self.alpha)
                p_absent = 1.0 - p_present
                scores[c] += math.log(p_present if value > 0 else p_absent)
        peak = max(scores)
        exps = [math.exp(s - peak) for s in scores]
        total = sum(exps) or 1.0
        return [e / total for e in exps]

    def predict(self, example: core.Example) -> int:
        probs = self.predict_proba(example)
        return max(range(len(probs)), key=lambda c: probs[c])

class DecisionTree:
    """A depth-limited CART tree over binary features.

    Depth-limited on purpose. An unrestricted tree on this data fits the 346
    minority examples perfectly and predicts nothing anywhere else, which is
    the standard way a decision tree lies.
    """

    name = "DecisionTree"

    def __init__(self, max_depth: int = 4, min_leaf: int = 5) -> None:
        self.max_depth = max_depth
        self.min_leaf = min_leaf
        self.root: Optional[dict] = None

    def fit(self, examples: Sequence[core.Example]) -> "DecisionTree":
        counts = [0.0] * len(core.VERDICTS)
        for e in examples:
            counts[e.y] += 1
        self.root = self._build(examples, 0, counts)
        return self

    def _build(self, examples, depth: int, counts):
        total = sum(counts) or 1.0
        majority = max(range(len(counts)), key=lambda c: counts[c])
        node = {"pred": majority, "left": None, "right": None, "feature": None}
        if depth >= self.max_depth or len(examples) <= 2 * self.min_leaf:
            return node
        best = self._best_split(examples, counts)
        if best is None:
            return node
        feature, left, right = best
        node["feature"] = feature
        node["left"] = self._build(left, depth + 1, _counts(left))
        node["right"] = self._build(right, depth + 1, _counts(right))
        return node

    def _best_split(self, examples, counts):
        """The feature maximising Gini reduction, by weight."""
        total = sum(counts) or 1.0
        parent = 1.0 - sum((c / total) ** 2 for c in counts)
        best_gain, best = 0.0, None
        for feature in _candidate_features(examples):
            left = [e for e in examples
                    if _value_at(e.x, feature) > 0]
            if len(left) < self.min_leaf or len(left) > len(examples) - self.min_leaf:
                continue
            left_counts = _counts(left)
            right_counts = [counts[c] - left_counts[c] for c in range(len(counts))]
            lt = sum(left_counts) or 1.0
            rt = sum(right_counts) or 1.0
            gain = parent - (lt / total) * (1.0 - sum((c / lt) ** 2 for c in left_counts)) \
                            - (rt / total) * (1.0 - sum((c / rt) ** 2 for c in right_counts))
            if gain > best_gain:
                # `right` is the complement. It was referenced here before it
                # existed anywhere in this scope, so every split raised
                # NameError and the tree silently contributed nothing - which the
                # comparison reported as 0.0% rather than as a crash.
                right = [e for e in examples
                         if _value_at(e.x, feature) <= 0]
                best_gain, best = gain, (feature, left, right)
        return best

    def predict(self, example: core.Example) -> int:
        node = self.root
        while node and node["feature"] is not None:
            node = node["left"] if _value_at(example.x, node["feature"]) > 0 else node["right"]
        return node["pred"] if node else 0

    def predict_proba(self, example: core.Example) -> List[float]:
        node = self.root
        while node and node["feature"] is not None:
            node = node["left"] if _value_at(example.x, node["feature"]) > 0 else node["right"]
        out = [0.0] * len(core.VERDICTS)
        if node:
            out[node["pred"]] = 1.0
        return out

class SGDClassifier:
    """Online logistic loss by SGD. Refits from a warm start, so it can learn
    from calls as they happen rather than from a frozen export."""

    name = "SGDClassifier"

    def __init__(self, lr: float = 0.5, epochs: int = 20,
                 l2: float = 1e-4, seed: int = 0) -> None:
        self.lr, self.epochs, self.l2, self.seed = lr, epochs, l2, seed
        self.w: List[List[float]] = []
        self.b: List[float] = []

    def fit(self, examples: Sequence[core.Example]) -> "SGDClassifier":
        classes = len(core.VERDICTS)
        if not self.w:
            # Only the touched columns are allocated, so this is a few thousand
            # rows rather than 16384x3 of mostly zeros.
            touched = set()
            for e in examples:
                touched.update(i for i, _ in _sparse(e))
            self.w = {i: [0.0] * classes for i in sorted(touched)}
            self.b = [0.0] * classes
        n = max(len(examples), 1)
        rng = random.Random(self.seed)
        order = list(range(len(examples)))
        for _epoch in range(self.epochs):
            rng.shuffle(order)
            for index in order:
                e = examples[index]
                scores = [self.b[c] + sum(v * self.w.get(i, (0.0,) * classes)[c]
                                          for i, v in _sparse(e))
                          for c in range(classes)]
                peak = max(scores)
                exps = [math.exp(s - peak) for s in scores]
                total = sum(exps) or 1.0
                probs = [v / total for v in exps]
                probs[e.y] -= 1.0
                for i, v in _sparse(e):
                    row = self.w.setdefault(i, [0.0] * classes)
                    for c in range(classes):
                        row[c] -= self.lr * (probs[c] * v / n + self.l2 * row[c])
                for c in range(classes):
                    self.b[c] -= self.lr * (probs[c] / n)
        return self

    def predict_proba(self, example: core.Example) -> List[float]:
        scores = [self.b[c] + sum(v * self.w.get(i, (0.0,) * len(core.VERDICTS))[c]
                                    for i, v in _sparse(example))
                  for c in range(len(core.VERDICTS))]
        peak = max(scores)
        exps = [math.exp(s - peak) for s in scores]
        total = sum(exps) or 1.0
        return [e / total for e in exps]

    def predict(self, example: core.Example) -> int:
        probs = self.predict_proba(example)
        return max(range(len(probs)), key=lambda c: probs[c])

class _BuiltinAdapter:
    """`core.OutcomeModel` behind the `predict`/`fit` interface used above."""

    name = core.OutcomeModel.__name__

    def __init__(self) -> None:
        self._model = core.OutcomeModel()

    def fit(self, examples):
        self._model.fit(list(examples))
        return self

    def predict_proba(self, example):
        """Score the feature vector directly, dense or sparse.

        The built-in model keeps either a numpy matrix (when numpy happens to be
        installed) or a sparse index map, and silently producing an all-zero
        score vector for one of them would read as "totally unsure" rather than
        as a bug - so the branch is explicit.
        """
        model = self._model
        if getattr(model, "_dense", False):
            return _softmax(list(model.w @ example.x + model.b))
        return _softmax([
            model.b[c] + sum(v * model.w.get(i, _ZERO_ROW)[c]
                             for i, v in _sparse(example))
            for c in range(len(core.VERDICTS))])

    def predict(self, example):
        probs = self.predict_proba(example)
        return max(range(len(probs)), key=lambda c: probs[c])

def _softmax(scores):
    peak = max(scores)
    exps = [math.exp(s - peak) for s in scores]
    total = sum(exps) or 1.0
    return [e / total for e in exps]

class KNN:
    """Similarity-based classification over the training set.

    Included to be measured rather than assumed: it stores the whole set and
    rescans it per query, which on 11k examples is slow, and the honest answer
    may be that it is not worth carrying at all.
    """

    name = "kNN"

    def __init__(self, k: int = 25) -> None:
        self.k = k
        self.rows: List[Tuple[Dict[int, float], int]] = []

    def fit(self, examples: Sequence[core.Example]) -> "KNN":
        self.rows = [(dict(_sparse(e)), e.y) for e in examples]
        return self

    def predict(self, example: core.Example) -> int:
        probe = dict(_sparse(example))
        scored = []
        for vector, label in self.rows:
            # sparse cosine similarity; both vectors are unit-normalised
            shared = sum(value * vector.get(i, 0.0) for i, value in probe.items())
            scored.append((shared, label))
        scored.sort(reverse=True)
        tally = [0.0] * len(core.VERDICTS)
        for _s, label in scored[:self.k]:
            tally[label] += 1.0
        return max(range(len(tally)), key=lambda c: tally[c])

    def predict_proba(self, example: core.Example) -> List[float]:
        out = [0.0] * len(core.VERDICTS)
        out[self.predict(example)] = 1.0
        return out

class Ridge:
    """L2-regularised linear regression, for a numeric outcome.

    The only regressor kept, because "how long did this take" is a genuinely
    different question from a class, and this is the whole of what that costs.
    """

    name = "Ridge"

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self.coef: Dict[int, float] = {}

    def fit(self, examples, targets: Sequence[float]) -> "Ridge":
        touched = set()
        for e in examples:
            touched.update(i for i, _ in _sparse(e))
        # Normal equations on the touched columns only, with a small ridge term
        # so an uninformative column cannot invert the system.
        matrix = [[0.0] * len(touched) for _ in touched]
        rhs = [0.0] * len(touched)
        for e, t in zip(examples, targets):
            active = dict(_sparse(e))
            column = {i: active.get(i, 0.0) for i in sorted(touched)}
            row = list(column.values())
            for i, a in enumerate(row):
                if not a:
                    continue
                rhs[i] += a * t
                for j, b in enumerate(row):
                    if b:
                        matrix[i][j] += a * b
        for i in range(len(touched)):
            matrix[i][i] += self.alpha
        try:
            solution = _solve(matrix, rhs)
        except ZeroDivisionError:
            solution = [0.0] * len(touched)
        self.coef = dict(zip(sorted(touched), solution))
        return self

    def predict(self, example: core.Example) -> float:
        return sum(value * self.coef.get(i, 0.0) for i, value in _sparse(example))

def _active(vector: Sequence[float]) -> List[Tuple[int, float]]:
    """The non-zero entries of a dense vector. **O(width)** - 16384 slots.

    Every hot path should use `_sparse(example)` instead: an Example carries its
    own active indices, so this full scan is only paid when building one.
    """
    return [(i, v) for i, v in enumerate(vector) if v]


def _sparse(example) -> Tuple[Tuple[int, float], ...]:
    """An example's non-zero entries, from its cached list.

    Scanning the dense vector per call cost 16384 iterations each time. With
    9,586 examples scored, or a few thousand fitted over 20 epochs, that is the
    difference between 110 seconds and 0.04. A hashed feature vector has a
    handful of non-zeros out of 16384, so the scan was 99.9% wasted work.
    """
    return example.active or tuple(
        (i, v) for i, v in enumerate(example.x) if v)

def _value_at(vector: Sequence[float], index: int) -> float:
    return vector[index] if 0 <= index < len(vector) else 0.0

def _counts(examples) -> List[float]:
    tally = [0.0] * len(core.VERDICTS)
    for e in examples:
        tally[e.y] += 1.0
    return tally

def _candidate_features(examples, cap: int = 64) -> List[int]:
    """Features worth trying as a split: the most common, bounded.

    A full sweep over 16384 columns per node is not affordable in pure Python;
    the common features are the ones carrying the argument-shape signal anyway.
    """
    frequency: Dict[int, float] = defaultdict(float)
    for e in examples:
        for i, _v in _sparse(e):
            frequency[i] += 1.0
    return [i for i, _n in sorted(frequency.items(), key=lambda kv: -kv[1])[:cap]]

def _solve(matrix: List[List[float]], rhs: List[float]) -> List[float]:
    """Gaussian elimination with partial pivoting. Small system, so clarity
    beats cleverness."""
    size = len(rhs)
    work = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(work[r][column]))
        if abs(work[pivot][column]) < 1e-12:
            raise ZeroDivisionError("singular system")
        work[column], work[pivot] = work[pivot], work[column]
        for row in range(column + 1, size):
            factor = work[row][column] / work[column][column]
            if factor:
                for k in range(column, size + 1):
                    work[row][k] -= factor * work[column][k]
    out = [0.0] * size
    for column in reversed(range(size)):
        total = work[column][size] - sum(work[column][k] * out[k]
                                         for k in range(column + 1, size))
        out[column] = total / work[column][column]
    return out

class Standing(NamedTuple):
    name: str
    available: bool
    accuracy: float
    baseline: float
    minority_recall: float
    beats: bool
    note: str = ""

def compare(path: Optional[object] = None,
            holdout: float = 0.25) -> List[Standing]:
    examples = core.load(path)
    if not examples:
        return []
    cut = int(len(examples) * (1.0 - holdout))
    train, test = examples[:cut], examples[cut:]
    if not train or not test:
        return []
    out = []
    for name, make in CANDIDATES:
        try:
            model = make()
            model.fit(list(train))
            report = core.evaluate(model, test)
            out.append(Standing(name, True, report.accuracy, report.baseline,
                                _minority(report), report.accuracy > report.baseline))
        except Exception as exc:  # noqa: BLE001
            out.append(Standing(name, True, 0.0, 0.0, 0.0, False,
                                f"failed: {type(exc).__name__}: {exc}"))
    return out

def _minority(report) -> float:
    if not report.per_class:
        return 0.0
    majority = max(report.per_class, key=lambda k: report.per_class[k]["support"])
    recalls = [s["recall"] for n, s in report.per_class.items()
               if n != majority and s["support"] >= core._MIN_CLASS]
    return sum(recalls) / len(recalls) if recalls else 0.0

def choose(results: Optional[List[Standing]] = None,
           path: Optional[object] = None) -> str:
    """The recommendation, from a measurement. Never from a preference."""
    if results is None:
        results = compare(path)
    if not results:
        return ("Nothing to choose between: no labelled examples were found.")
    cleared = [r for r in results if r.beats and r.minority_recall > 0]
    if not cleared:
        return ("NONE of these estimators clears the bar on the current labels, "
                "and the honest reason is not the models. 21 of 32 mutating "
                "actuators have no post-condition, so 94% of the traffic is "
                "`unverified` and the majority class is the only learnable "
                "answer. Add read-back probes first; then re-run this.")
    best = cleared[0]  # already cheapest-first
    return (f"Use {best.name}: {best.accuracy * 100:.1f}% against a "
            f"{best.baseline * 100:.1f}% baseline, minority recall "
            f"{best.minority_recall * 100:.1f}%. It is the cheapest estimator "
            f"that clears the bar.")

def render_estimators(results: Optional[List[Standing]] = None,
           path: Optional[object] = None) -> str:
    if results is None:
        results = compare(path)
    if not results:
        return "Nothing to compare: no labelled examples were found."
    out = ["  estimator           accuracy   baseline   minority recall  beats baseline"]
    for r in sorted(results, key=lambda r: -r.accuracy):
        out.append(f"  {r.name:<18} {r.accuracy * 100:7.1f}%  "
                   f"{r.baseline * 100:7.1f}%   {r.minority_recall * 100:13.1f}%  "
                   f"{'yes' if r.beats else 'NO'}"
                   + (f"   [{r.note}]" if r.note else ""))
    out.append("")
    out.append(choose(results))
    return "\n".join(out)

class LinearRegression:
    """Least squares, for a numeric outcome with no regularisation.

    Kept beside `Ridge` rather than instead of it, because the difference is
    the whole point: without a penalty the fit is unstable when features
    outnumber examples, which is exactly this data's shape (16k hashed columns,
    11k rows). `Ridge` is the one that survives it; this one is here so the
    comparison can show *that* rather than assert it.
    """

    name = "LinearRegression"

    def __init__(self) -> None:
        self.coef: Dict[int, float] = {}

    def fit(self, examples, targets: Sequence[float]) -> "LinearRegression":
        touched = set()
        for e in examples:
            touched.update(i for i, _ in _sparse(e))
        columns = sorted(touched)
        size = len(columns)
        if size == 0:
            return self
        matrix = [[0.0] * size for _ in range(size)]
        rhs = [0.0] * size
        for e, t in zip(examples, targets):
            active = dict(_sparse(e))
            row = [active.get(i, 0.0) for i in columns]
            for a_i, a in enumerate(row):
                if not a:
                    continue
                rhs[a_i] += a * t
                for b_i, b in enumerate(row):
                    if b:
                        matrix[a_i][b_i] += a * b
        # No ridge term at all: that is what makes it unstable, and adding a
        # token one would be Ridge wearing a different name.
        try:
            solution = _solve(matrix, rhs)
        except ZeroDivisionError:
            solution = [0.0] * size
        self.coef = dict(zip(columns, solution))
        return self

    def predict(self, example) -> float:
        return sum(v * self.coef.get(i, 0.0) for i, v in _sparse(example))

class EMA:
    """Exponentially weighted moving average, plus a simple moving average.

    The cheapest useful thing in this file. `tool_calls.log` records a duration
    per call, and an EMA over it answers "is this tool getting slower" in
    constant memory and one pass - which is the question behind most "the
    machine feels slow" reports, and the one no other estimator here answers.
    """

    name = "EMA"

    def __init__(self, alpha: float = 0.2) -> None:
        self.alpha = alpha
        self.value: Optional[float] = None
        self.count = 0

    def update(self, sample: float) -> float:
        self.count += 1
        self.value = (sample if self.value is None
                      else self.alpha * sample + (1 - self.alpha) * self.value)
        return self.value

    def predict(self, _example=None) -> float:
        return self.value if self.value is not None else 0.0

class KMeans:
    """k-means over the sparse feature vectors.

    Included because clustering tools is a real question - which tools behave
    alike - and because the honest expectation is that on the current data it
    finds one big cluster and a tail. It is measured, not assumed useful.
    """

    name = "KMeans"

    def __init__(self, k: int = 3, iterations: int = 12, seed: int = 0) -> None:
        self.k, self.iterations, self.seed = k, iterations, seed
        self.centroids: List[Dict[int, float]] = []
        self.assignments: List[int] = []

    def fit(self, examples) -> "KMeans":
        import random as _random
        vectors = [dict(_sparse(e)) for e in examples]
        if not vectors:
            return self
        rng = _random.Random(self.seed)
        pool = list(range(len(vectors)))
        rng.shuffle(pool)
        picks = [vectors[i] for i in pool[:min(self.k, len(vectors))]]
        while len(picks) < self.k:
            picks.append({})
        self.centroids = [dict(v) for v in picks]
        for _step in range(self.iterations):
            self.assignments = [self._nearest(v) for v in vectors]
            sums: List[Dict[int, float]] = [{} for _ in range(self.k)]
            sizes = [0] * self.k
            for vector, cluster in zip(vectors, self.assignments):
                sizes[cluster] += 1
                for i, value in vector.items():
                    sums[cluster][i] = sums[cluster].get(i, 0.0) + value
            self.centroids = [
                {i: total / sizes[c] for i, total in sums[c].items()}
                if sizes[c] else {}
                for c in range(self.k)]
        return self

    def _nearest(self, vector: Dict[int, float]) -> int:
        best, score = 0, -2.0
        for index, centroid in enumerate(self.centroids):
            # Sparse cosine similarity; the vectors are unit-normalised.
            shared = sum(value * centroid.get(i, 0.0) for i, value in vector.items())
            if shared > score:
                best, score = index, shared
        return best

    def sizes(self) -> List[int]:
        counts = [0] * self.k
        for cluster in self.assignments:
            counts[cluster] += 1
        return counts

class AnomalyDetector:
    """Isolation-forest-shaped outlier detection, without the forest.

    Isolation Forest splits on random axes and measures how few cuts isolate a
    point; points isolated in few are the anomalies. The exact algorithm needs a
    tree ensemble, so this is the same *idea* done directly: score each example
    by the distance to its nearest few neighbours, and call the tail anomalous.

    Honest about the substitution in the docstring, because "IsolationForest"
    in a table and this are not the same estimator - this is a k-distance
    outlier score, which shares the intuition and none of the guarantees.
    """

    name = "AnomalyDetector"

    def __init__(self, k: int = 20, tail: float = 0.02) -> None:
        self.k, self.tail = k, tail
        self.rows: List[Dict[int, float]] = []
        self.threshold: Optional[float] = None

    def fit(self, examples) -> "AnomalyDetector":
        self.rows = [dict(_sparse(e)) for e in examples]
        self.threshold = self._cutoff()
        return self

    def _distances(self) -> List[float]:
        scores = []
        for index, vector in enumerate(self.rows):
            best = []
            for other_index, other in enumerate(self.rows):
                if other_index == index:
                    continue
                shared = sum(value * other.get(i, 0.0)
                             for i, value in vector.items())
                # cosine distance in [0, 2]
                best.append(1.0 - shared)
            best.sort()
            scores.append(sum(best[:self.k]) / max(len(best[:self.k]), 1))
        return scores

    def _cutoff(self) -> Optional[float]:
        scores = sorted(self._distances())
        if not scores:
            return None
        index = min(len(scores) - 1,
                    int(len(scores) * (1.0 - self.tail)))
        return scores[index]

    def score(self, example) -> float:
        """Mean cosine distance to its k nearest known rows."""
        if not self.rows:
            return 0.0
        vector = dict(_sparse(example))
        best = sorted(1.0 - sum(value * other.get(i, 0.0)
                                for i, value in vector.items())
                      for other in self.rows)
        return sum(best[:self.k]) / max(len(best[:self.k]), 1)

    def is_anomalous(self, example) -> bool:
        if self.threshold is None:
            return False
        return self.score(example) > self.threshold

    def flag(self) -> List[int]:
        """Which training rows the cutoff considers anomalous."""
        if self.threshold is None:
            return []
        return [i for i, score in enumerate(self._distances())
                if score > self.threshold]


# ══════════════════════════════════════════════════════════════════════════
# THE RUNNING BANDIT
# ══════════════════════════════════════════════════════════════════════════
#
# One persistent bandit over tool names, with cooldowns so a pattern that stays true does not nag, and a prediction store the loop can score.


class Arm(NamedTuple):
    pulls: int
    wins: float
    last_used: float

class Choice(NamedTuple):
    arm: str
    method: str
    #: What we believe about the arm's true success rate, and how much of that
    #: is belief rather than evidence. Reported together because an arm with a
    #: 0.9 estimate from one pull and a 0.5 estimate from two hundred are not
    #: the same claim.
    estimate: float
    confidence: float

class Bandit:
    """Thompson Sampling, UCB1 and epsilon-greedy over named arms."""

    def __init__(self, rng: Optional[random.Random] = None,
                 epsilon: float = 0.1, bonus: float = 1.0) -> None:
        self._arms: Dict[str, Arm] = {}
        self._rng = rng or random.Random()
        self.epsilon = epsilon
        self.bonus = bonus

    # --- state ---------------------------------------------------------------

    def update(self, arm: str, verdict: Optional[str],
               now: Optional[float] = None) -> Optional[float]:
        """Record an outcome. Returns the reward, or None when there was none."""
        import time
        moment = time.time() if now is None else now
        reward = REWARD.get(str(verdict or "").lower())
        if reward is None:
            # Unverified, unknown, or absent. Deliberately not a zero.
            return None
        current = self._arms.get(arm)
        if current is None:
            self._arms[arm] = Arm(1, reward, moment)
        else:
            self._arms[arm] = Arm(current.pulls + 1, current.wins + reward, moment)
        return reward

    def observe(self, arm: str, reward: float, now: Optional[float] = None) -> None:
        """Record a reward directly, for an outcome scored elsewhere."""
        import time
        moment = time.time() if now is None else now
        current = self._arms.get(arm)
        if current is None:
            self._arms[arm] = Arm(1, reward, moment)
        else:
            self._arms[arm] = Arm(current.pulls + 1, current.wins + reward, moment)

    def arms(self, now: Optional[float] = None) -> Dict[str, Arm]:
        """Live arms, with stale ones dropped."""
        import time
        moment = time.time() if now is None else now
        return {name: arm for name, arm in self._arms.items()
                if moment - arm.last_used <= _STALE_SECONDS}

    # --- selection -----------------------------------------------------------

    def thompson(self, candidates: Sequence[str]) -> Optional[Choice]:
        """Sample from each arm's posterior and take the largest.

        The only one of the three that represents not-knowing as uncertainty:
        an arm never pulled has a wide posterior and gets picked often, which
        is the exploration a cold start needs.
        """
        live = self.arms()
        if not candidates:
            return None
        best: Optional[Choice] = None
        for name in candidates:
            arm = live.get(name)
            if arm is None:
                # Beta(1,1) prior; the spread is what makes this explorative.
                sample, low, high = self._sample_beta(_ALPHA, _BETA)
            else:
                sample, low, high = self._sample_beta(
                    _ALPHA + arm.wins, _BETA + (arm.pulls - arm.wins))
            if best is None or sample > best.estimate:
                spread = high - low
                best = Choice(name, "thompson", sample, 1.0 - min(1.0, spread))
        return best

    def _sample_beta(self, alpha: float, beta: float) -> Tuple[float, float, float]:
        """A Beta draw, with a rough credible interval.

        Python has no Beta sampler, and for this purpose an approximation is
        fine and is *labelled* as one: for the shapes involved the normal
        approximation tracks the mean closely, and the interval is only used to
        say how sure we are, not to make a decision on.
        """
        mean = alpha / (alpha + beta) if (alpha + beta) else 0.5
        variance = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1)) \
            if (alpha + beta + 1) else 0.0
        spread = math.sqrt(variance) if variance > 0 else 0.5
        sample = self._rng.gauss(mean, spread) if spread else mean
        sample = min(1.0, max(0.0, sample))
        return sample, max(0.0, mean - 2 * spread), min(1.0, mean + 2 * spread)

    def ucb(self, candidates: Sequence[str]) -> Optional[Choice]:
        """Optimism for uncertainty: try each arm at mean + c*sqrt(log t / n)."""
        live = self.arms()
        pool = [c for c in candidates if c]
        if not pool:
            return None
        total = sum(a.pulls for a in live.values()) + 1
        best: Optional[Choice] = None
        for name in pool:
            arm = live.get(name)
            if arm is None or arm.pulls == 0:
                score, estimate, pulls = float("inf"), 0.5, 0
            else:
                mean = arm.wins / arm.pulls
                score = mean + self.bonus * math.sqrt(math.log(total) / arm.pulls)
                estimate, pulls = mean, arm.pulls
            if best is None or score > best.estimate:
                best = Choice(name, "ucb", estimate, 1.0 / (1.0 + pulls))
        return best

    def epsilon_greedy(self, candidates: Sequence[str]) -> Optional[Choice]:
        """The baseline: mostly the best arm, sometimes a random one."""
        pool = [c for c in candidates if c]
        if not pool:
            return None
        live = self.arms()
        if self._rng.random() < self.epsilon:
            name = self._rng.choice(pool)
        else:
            scored = [(live[a].wins / live[a].pulls if a in live and live[a].pulls else -1.0, a)
                      for a in pool]
            name = max(scored)[1]
        arm = live.get(name)
        return Choice(name, "epsilon-greedy",
                      (arm.wins / arm.pulls) if arm and arm.pulls else 0.0,
                      1.0 / (1.0 + (arm.pulls if arm else 0)))

    def choose(self, candidates: Sequence[str],
               method: str = "thompson") -> Optional[Choice]:
        pool = [c for c in candidates if c]
        if not pool:
            return None
        return {"thompson": self.thompson, "ucb": self.ucb,
                "epsilon-greedy": self.epsilon_greedy}.get(method, self.thompson)(pool)

    def agreement(self, candidates: Sequence[str]) -> Dict[str, List[str]]:
        """Where the three estimators disagree.

        The disagreement is the finding. A bandit that is confidently wrong
        looks exactly like one that is right unless you check whether the
        independent methods converge, so this is the honest readout rather than
        a single ranked list.
        """
        picks = {}
        for method in ("thompson", "ucb", "epsilon-greedy"):
            choice = self.choose(candidates, method)
            picks[method] = choice.arm if choice else None
        out: Dict[str, List[str]] = defaultdict(list)
        for method, arm in picks.items():
            if arm:
                out[arm].append(method)
        return dict(out)

def _path():
    from shani_chronoa import files
    return files.data_home() / "shani-chronoa" / "logs" / "bandit.json"

def save_bandit(bandit: Bandit, path=None) -> Optional[str]:
    """Persist the arm table. Small, and worth keeping: an arm's history is the
    only thing that makes its estimate better than a guess."""
    import time
    destination = path or _path()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: [arm.pulls, arm.wins, arm.last_used]
                   for name, arm in bandit.arms(time.time()).items()}
        destination.write_text(json.dumps(payload), encoding="utf-8")
        try:
            destination.chmod(0o600)
        except OSError:
            pass
        return str(destination)
    except OSError as exc:
        logger.debug("bandit: could not save: %s", exc)
        return None

def load_bandit(path=None) -> Bandit:
    import time
    bandit = Bandit()
    destination = path or _path()
    try:
        payload = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return bandit
    now = time.time()
    for name, values in (payload or {}).items():
        try:
            pulls, wins, last = values
            if now - float(last) <= _STALE_SECONDS:
                bandit._arms[str(name)] = Arm(int(pulls), float(wins), float(last))
        except (TypeError, ValueError):
            continue
    return bandit

def render_bandit(bandit: Bandit, top: int = 12) -> str:
    """Readable, and honest about how thin every estimate is."""
    live = bandit.arms()
    if not live:
        return ("No arms have earned a reward yet. An arm only counts once "
                "something verified, so until post-conditions exist most of the "
                "table stays empty - which is the correct state, not a bug.")
    rows = sorted(live.items(), key=lambda kv: -(kv[1].wins / max(kv[1].pulls, 1)))
    out = [f"{len(live)} arm(s) with at least one scored outcome.", ""]
    out.append("  arm                          pulls   wins   rate    confidence")
    for name, arm in rows[:top]:
        rate = arm.wins / arm.pulls if arm.pulls else 0.0
        # Confidence is about evidence, not about the estimate: one pull is
        # almost no evidence whatever it showed.
        confidence = 1.0 / (1.0 + arm.pulls)
        out.append(f"  {name[:26]:<26} {arm.pulls:>6}{arm.wins:>7.1f}  "
                   f"{rate * 100:5.1f}%  {'high' if confidence > 0.5 else 'low'}")
    thin = [n for n, a in live.items() if a.pulls < 5]
    if thin:
        out.append("")
        out.append(f"  {len(thin)} arm(s) have fewer than 5 scored pulls. Their "
                   f"rates are not yet estimates of anything.")
    return "\n".join(out)


# ── Names the merged modules exported that collide across them ──────────────
# Three separate `render` functions existed. They are disambiguated by what they
# render, and the original names are kept as aliases so `from learning import
# render_outcome` and the older paths both resolve.

#: `estimators.compare` and `.choose` renamed to say what they compare, because
#: `compare` alone is ambiguous next to the bandit policies it sits beside.
compare_estimators = compare
choose_estimator = choose

#: `outcome_model.render` vs `bandit.render` vs `estimators.render` - three
#: functions, three names.
render_outcome = render_outcome
render_bandit = render_bandit
render_estimators = render_estimators


#: There is deliberately no bare `load`/`save` alias. A module holding both an
#: outcome loader and a bandit loader cannot call either of them `load` without
#: one shadowing the other, and when the band's won, `train_and_report()`
#: silently returned an empty report instead of failing. The bandit helpers are
#: `load_bandit`/`save_bandit`; `load` stays the outcome loader, which is the
#: one the estimator path uses.
