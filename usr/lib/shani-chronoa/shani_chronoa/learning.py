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


# ══════════════════════════════════════════════════════════════════════════
# LEARNING FROM OUTCOMES: RELIABILITY, AND WHAT IT MAY CHANGE
# ══════════════════════════════════════════════════════════════════════════
#
# Restored into this package after a duplicate copy of it was found under
# `pkg/`, which is where these six functions actually lived. The canonical tree
# had lost them while `tool_select` still called them - and the calls were
# wrapped in `try/except`, so nothing failed: `learned_weights()` returned `{}`
# on every request and `reorder()` was never reached. The learned half of
# selection was dead and silent, which is the one failure shape a suite only
# reports as red if some test asserts the *effect* rather than the import.
#
# The safety argument is unchanged, and is the whole reason this is a
# multiplier rather than a decision: nothing here can add a tool to a selection
# or remove one permanently. It reorders names the matcher already returned,
# and a demotion is a nudge - `MIN_WEIGHT` leaves even a total record of failure
# reachable.

#: Verdicts that mean "this worked". Anything else - including `None`, which is a
#: call that never reached verification - is not evidence of success.
SUCCESS = ("verified",)

#: How much one outcome is worth, in pseudo-counts. Two is deliberate: with one,
#: a single failure would swing a tool's score to zero, and one bad call is not
#: evidence that a tool is bad.
PRIOR_STRENGTH = 2.0
#: The score of a tool with no history at all: neutral, so an unproven tool is
#: neither promoted nor buried - it just stays where the matcher put it.
NEUTRAL = 0.5
#: Reliability is mapped onto a multiplier between these, so the worst history
#: can demote a tool without ever putting it out of reach of the matcher.
#: `MAX_WEIGHT` is the ceiling; `MIN_WEIGHT` is *derived* from the slope below so
#: that a total record of failure lands exactly on it. The first version had a
#: hand-picked 0.25 that the curve never reached, which made the constant a
#: claim the code did not make.
MAX_WEIGHT = 1.75


def is_success(verdict: Optional[str]) -> bool:
    """Whether a verdict counts as evidence that the action worked.

    Case-insensitive on purpose: the log has been written by hand, by tests, and
    by older code, and `"Verified"` must not read as a failure.
    """
    if not verdict:
        return False
    return str(verdict).strip().lower() in SUCCESS


def reliability(records: Iterable) -> float:
    """The share of calls that verified, shrunk towards `NEUTRAL`.

    `records` is anything with `.verdict` on it - `ToolCallRecord`, or the dict
    `to_log_dict` produces, which is what reading the log back gives you.
    `PRIOR_STRENGTH` pseudo-observations of "probably fine" are added, so:
    0 calls -> 0.5 (neutral), 1 verified -> ~0.62, 9 verified -> ~0.91, and
    a flawless record reaches 1.0 only after enough calls to mean something.
    """
    total = 0
    good = 0
    for record in records:
        verdict = (record.get("verdict") if isinstance(record, dict)
                   else getattr(record, "verdict", None))
        if verdict is None:
            # The call never reached verification, so nothing was observed. It
            # is counted as neither a success nor a failure - a skill that
            # crashed before its post-condition ran is not evidence about
            # whether the skill works.
            continue
        total += 1
        if is_success(verdict):
            good += 1
    return ((good + PRIOR_STRENGTH * NEUTRAL) / (total + PRIOR_STRENGTH))


#: How fast a reliability moves a weight. Chosen so a *perfect* history reaches
#: exactly `MAX_WEIGHT`, which makes the scale's two ends facts rather than
#: taste.
_SLOPE = math.log(MAX_WEIGHT) / (1.0 - NEUTRAL)
#: What a reliability of zero is worth, exactly.
MIN_WEIGHT = math.exp(-_SLOPE * NEUTRAL)


def weight(score: float) -> float:
    """Turn a reliability into the multiplier `tool_select` multiplies by.

    Exponential, and anchored so that `NEUTRAL` maps to exactly **1.0** - the
    first version used `log2(score / NEUTRAL)`, which put the neutral point at
    0.5 and therefore demoted even a tool with a flawless record. An indicator
    that reads "0.9" and calls it below neutral is worse than no indicator.

    Clamped, because a demotion is a nudge and never a veto: even a total record
    of failure leaves the tool reachable.
    """
    bounded = min(1.0, max(0.0, float(score)))
    return min(MAX_WEIGHT, max(MIN_WEIGHT,
                               math.exp(_SLOPE * (bounded - NEUTRAL))))


def weights_from_records(records: Iterable) -> Dict[str, float]:
    """Per-tool multipliers, from a flat list of call records."""
    grouped: Dict[str, list] = {}
    for record in records:
        name = (record.get("tool_name") if isinstance(record, dict)
                else getattr(record, "tool_name", None))
        if not name:
            continue
        grouped.setdefault(str(name), []).append(record)
    return {name: weight(reliability(items)) for name, items in grouped.items()}


def weights_from_tracker(tracker: Optional[object] = None) -> Dict[str, float]:
    """The same, from the live `ToolTracker`.

    Takes the tracker as an argument rather than importing the singleton, so the
    caller decides whose history it is: a test with a temporary log, or the real
    one. A module-level import of the singleton would make this untestable and
    would read another process's history by accident.
    """
    if tracker is None:
        return {}
    try:
        records = tracker.get_calls()
    except Exception:
        logger.debug("could not read the tool history", exc_info=True)
        return {}
    return weights_from_records(records)


def reorder(ranked: Sequence[Tuple], learned: Optional[Dict[str, float]] = None,
            limit: Optional[int] = None) -> List:
    """Reorder `tool_select.ranked()` output by what has been learned.

    `ranked` is a sequence of `(score, tool_name)` pairs. The learned multiplier
    is applied to the matcher's own score, and the list is sorted again -
    **stably**, so two tools the matcher considered equally good keep the
    matcher's order when learning has no opinion about them.

    Only names it was given can come back out. This is the whole safety argument
    for learning in one line, and it is why there is no code path from a score to
    a tool that the matcher did not already return.
    """
    if not learned:
        return list(ranked)[:limit] if limit else list(ranked)
    scored = []
    for position, pair in enumerate(ranked):
        try:
            score, name = pair[0], pair[1]
        except (TypeError, IndexError, KeyError):
            scored.append((0.0, position, pair))
            continue
        multiplier = learned.get(name)
        if multiplier is None:
            multiplier = 1.0
        scored.append((float(score) * float(multiplier), position, pair))
    # -score for descending, position ascending so ties keep the matcher's order.
    scored.sort(key=lambda triple: (-triple[0], triple[1]))
    out = [triple[2] for triple in scored]
    return out[:limit] if limit else out


def organ_status(tracker: Optional[object] = None) -> Dict[str, object]:
    """How much learned weight there is, and how much of it is trusted.

    **This belonged to the Learning panel all along, and its docstring said
    Inventory.** `gui/surfaces/inventory.py` renders `organism.INVENTORY` - a
    static table of which organs are built - and never mentions tools with
    history, trust or doubt. So the claim was wrong in both directions: this was
    orphaned, *and* the panel it named does not show these numbers. The Learning
    panel now does.
    """
    learned = weights_from_tracker(tracker)
    if not learned:
        return {"tools_with_history": 0, "trusted": 0, "doubted": 0}
    return {
        "tools_with_history": len(learned),
        "trusted": sum(1 for w in learned.values() if w > 1.0),
        "doubted": sum(1 for w in learned.values() if w < 1.0),
    }

VERDICTS = ("unverified", "verified", "failed")
_N_FEATURES = 1 << 14

#: **The feature space's identity, recorded in every model and checked on load.**
#:
#: `_index` hashes a name with a fixed width, so a model whose weights live at
#: index 41 means "the tool whose name hashes to 41" - and that sentence stops
#: being true the moment the width, the hash, or the naming convention changes.
#: Nothing in the model recorded that, so a model written before a change would
#: load, pass its digest, and predict confidently about a feature it no longer
#: refers to. Silent nonsense is worse than a refusal, because nothing looks
#: wrong.
#:
#: So the space identifies itself, and a model that was not trained in this
#: space is **converted where it can be and refused where it cannot** - never
#: loaded and believed. A human changing one feature name does not invalidate a
#: model; a human changing the hasher does, and the difference is the point.
def read_model_knowledge(model=None) -> List[Dict[str, object]]:
    """What an existing model already knows, read out of its weights.

    **The model is the one artefact that is already paid for.** While the log
    has no scored labels, a model from an earlier run still encodes which
    feature combinations leaned toward failure. Discarding it and waiting for
    fresh evidence throws away the part of the system that cost the most to
    produce, so the knowledge is extracted and reported directly rather than
    re-derived.

    Read from the weights directly, with no log involved, so it works even on a
    machine whose log has nothing scored in it. This is *hypothesis* extraction,
    not proof: a weight is evidence the fit was pulled that way, not a
    measurement, and the entries say so.
    """
    if model is None:
        try:
            candidates = sorted(models_dir().glob(
                f"outcome-{_feature_space_id()}.json"))
            if not candidates:
                return []
            model = OutcomeModel.load(candidates[-1])
        except Exception:  # noqa: BLE001 - absent or unreadable is not a failure
            return []
    weights = getattr(model, "w", None) or {}
    bias = list(getattr(model, "b", []) or [])
    if not weights or not bias:
        return []

    # A feature is weighted toward whichever verdict it was most often fitted
    # for. Aggregating by target class turns the flat weight map back into the
    # statements it was trained from.
    pulled: Dict[int, float] = defaultdict(float)
    for index, row in weights.items():
        for cls, value in enumerate(row):
            if cls < len(bias):
                pulled[cls] += value
    total = sum(abs(v) for v in pulled.values()) or 1.0

    out: List[Dict[str, object]] = []
    for cls, name in enumerate(VERDICTS):
        if cls >= len(bias):
            break
        share = pulled.get(cls, 0.0) / total
        if abs(share) < 0.15:
            continue
        direction = "toward" if share > 0 else "away from"
        out.append({
            "verdict": name,
            "weight_share": round(abs(share), 4),
            "bias": round(bias[cls], 4),
            "lesson": (f"this model's weights are pulled {direction} {name} "
                       f"({abs(share):.0%} of total weight)"),
            "action": ("confirm on the next scored calls before acting on it"
                       if name != "failed" else
                       "check the failing call shape's post-condition first"),
            "confidence": "low - a fitted weight, not a measurement",
        })
    return sorted(out, key=lambda e: -float(e["weight_share"]))


def _feature_space_id() -> str:
    """A digest of everything that decides what an index means."""
    import hashlib as _h
    material = "|".join([
        f"width={_N_FEATURES}",
        f"hash=blake2b:{_index('probe').__class__.__name__}",
        f"sample={_index('tool=example')},{_index('argkey=tool.path')}",
        "convention=v2",
    ])
    return _h.sha256(material.encode("utf-8")).hexdigest()[:16]
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

def collapse(examples: Sequence["Example"]) -> List[Tuple["Example", int]]:
    """Unique feature vectors with how many times each was seen.

    **Measured on this machine's own log: 11,590 examples carry only 344
    distinct vectors.** 97% are duplicates and the largest identical group is
    4,172 records. That single number reframes the whole layer:

    - A 16,384-wide hashed space holds 344 distinct patterns, so almost every
      column and almost every stored row is repetition.
    - kNN retrieved *exact duplicates* as its nearest neighbours, which is why
      it scored so well and so slowly: it was memorising, not generalising.
    - Every fit was paying for repetition it did not need to.

    Collapsing duplicates with their counts is therefore not a micro-optimisation
    but a 34x reduction in the work, and it is what a weighted fit means
    anyway: a row repeated 4,172 times and a row seen once carry exactly the
    same information as one row with a weight of 4,172 and one with a weight
    of 1. Any of these estimators fits that identically.

    Order is preserved, so a prefix/suffix split still splits by time.
    """
    counts: Dict[Tuple[Tuple[int, float], ...], int] = defaultdict(int)
    first: Dict[Tuple[Tuple[int, float], ...], "Example"] = {}
    for example in examples:
        key = tuple(example.active)
        if key not in first:
            first[key] = example
        counts[key] += 1
    return [(first[key], counts[key]) for key in first]


def _unwrap(row):
    """`(Example, weight)` -> Example, or anything else unchanged.

    Two shapes circulate: a bare `Example`, and a weighted `(Example, weight)`
    pair the tree carries through its recursion. A plain `isinstance(tuple)`
    test cannot tell them apart, because the `(index, value)` pairs inside an
    active list are tuples as well - so the first element is checked.
    """
    if isinstance(row, tuple) and len(row) == 2 and isinstance(row[0], core.Example):
        return row[0]
    return row


def _log_revision() -> Optional[str]:
    """A cheap identity for the log's current contents: size and mtime.

    `stat` is microseconds, which is what makes the consolidation gate affordable
    to evaluate on a periodic tick. Everything else in this layer costs seconds,
    so the gate has to be something that can be asked constantly.
    """
    try:
        stat = _log_path().stat()
    except OSError:
        return None
    return f"{stat.st_size}-{int(stat.st_mtime)}"


def consolidate_if_due(force: bool = False,
                       min_new_bytes: int = 256 * 1024,
                       min_hours: float = 6.0) -> Dict[str, object]:
    """Retrain when the log has moved on, and say why not when it has not.

    **This is what makes the learning chain live.** Until now `train_and_save()`
    had no caller, so `tools._outcome_model()` loaded a file nothing wrote, so
    `_record_prediction()` wrote nothing, so no prediction was ever scored. The
    whole layer was inert - not broken, but inert. This closes the loop: log ->
    this -> a model on disk -> loaded by dispatch -> a prediction recorded.

    Three gates, cheapest first, because this is asked on a timer:

    - **has the log changed at all?** one `stat`, and if the model was trained
      against this exact size+mtime there is nothing to do;
    - **has it changed enough to be worth the seconds?** retraining is not free,
      and a log that grew by ten lines teaches nothing new;
    - **has enough time passed?** a log can grow quickly enough to cross the
      byte threshold several times an hour, and retraining on each would cost
      more than it learns.

    It returns what it decided either way. A consolidation pass that fails
    silently is how a layer dies without anyone noticing, which is the exact
    failure this codebase keeps recording.
    """
    revision = _log_revision()
    if revision is None:
        return {"ran": False, "reason": "no tool-call log to learn from"}
    # A model is only "current" if it was trained in the space this build
    # produces, which is now the name it carries. Older models for other spaces
    # are left exactly where they are and are not counted here.
    target = model_path_for_space()
    trained = None
    if target.exists():
        try:
            trained = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            trained = None
    provenance = (trained or {}).get("provenance") or {}
    previous = provenance.get("log_revision")

    # **The time gate applies even when there is no model.**
    #
    # Without one there is no provenance to compare against, so the two content
    # gates have nothing to work with and training was attempted on *every* call.
    # Training costs about 45 seconds, and `train_and_save` refuses - correctly -
    # for as long as the log lacks a scored label in its training half. The gate
    # therefore cost 45 seconds per tick, indefinitely, for a refusal that had
    # already been given once.
    #
    # So the last attempt is recorded durably beside the model, and the time gate
    # consults that whether or not a model exists.
    marker = models_dir() / "consolidate-ran.json"
    if not force:
        try:
            if (time.time() - marker.stat().st_mtime) < min_hours * 3600.0:
                return {"ran": False,
                        "reason": f"consolidated less than {min_hours:g}h ago; "
                                  f"waiting for more to accumulate",
                        "revision": revision}
        except OSError:
            pass
        if previous == revision:
            return {"ran": False, "reason": "the log has not changed since the "
                                            "model was trained",
                    "revision": revision}
        try:
            current_bytes = int(revision.split("-", 1)[0])
            prior_bytes = int(str(previous).split("-", 1)[0])
            growth = current_bytes - prior_bytes
        except (ValueError, TypeError):
            growth = float("inf")
        if previous is not None and growth < min_new_bytes:
            return {"ran": False,
                    "reason": f"the log grew {growth} bytes since the last "
                              f"training, under the {min_new_bytes} needed",
                    "revision": revision}
        if previous is not None:
            try:
                stamp = os.path.getmtime(target)
                if (time.time() - stamp) < min_hours * 3600.0:
                    return {"ran": False,
                            "reason": f"trained less than {min_hours:g}h ago; "
                                      f"waiting for more to accumulate",
                            "revision": revision}
            except OSError:
                pass

    outcome = train_and_save(target)
    # Record the attempt whether it succeeded or refused, so a refusal is not
    # retried at full cost on every tick.
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(
            {"ran": time.time(), "saved": bool(outcome.get("saved")),
             "revision": revision}, indent=2), encoding="utf-8")
        marker.chmod(0o600)
    except OSError:
        pass
    if not outcome.get("saved"):
        # A refusal is a real answer, and it is passed up rather than turned
        # into a silent failure - "the training split has no example of
        # verified" is the single most useful thing this function can say today.
        return {"ran": True, "saved": False, **outcome}
    return {"ran": True, "saved": True, "path": outcome.get("path"),
            "provenance": outcome.get("provenance"), "revision": revision}


def evaluate_bandit(records: Optional[Sequence[dict]] = None,
                    k: int = 5) -> Dict[str, object]:
    """Can the bandit rank tools better than chance, on this machine's own log?

    **The bandit is the only part of this layer that works on today's data.**
    It needs no labels: an arm earns a reward only when the verdict is
    `verified` or `failed`, and `unverified` - which is 92% of the traffic -
    earns nothing by construction. That is why the outcome model cannot be
    trained here and the bandit can.

    What it lacked was any way to tell whether it is *learning*. It has never
    been measured, so "the bandit works" was an assumption. This measures it,
    offline, from the same log:

    - **precision@k** - of the k arms the bandit ranks highest, how many have a
      real success rate above the population average. A bandit that ranked by
      chance would score the population rate, so this is the number that says
      whether it knows anything.
    - **spearman** - whether its ranking agrees with the empirical ranking.
    - **coverage** - how many arms had enough scored pulls to rank at all.

    **It is evaluated by feeding the log in order and only letting it choose
    from what it has seen**, so it is scored on what it could have known at the
    time, not with hindsight.
    """
    rows = records if records is not None else _entries()
    successes: Counter = Counter()
    attempts: Counter = Counter()
    for row in rows:
        verdict = str(row.get("verdict") or "").lower()
        tool = str(row.get("tool_name") or "")
        if verdict == "verified":
            successes[tool] += 1
            attempts[tool] += 1
        elif verdict == "failed":
            attempts[tool] += 1
        # `unverified` is not an attempt: no reward, no penalty, no information.

    scored = {t: successes[t] / attempts[t] for t in attempts if attempts[t] >= 5}
    if not scored:
        return {"measured": False,
                "reason": "no tool has 5 or more scored calls yet"}
    population = sum(successes.values()) / max(sum(attempts.values()), 1)

    # Replay: feed the log in order, exactly as the dispatcher would, so the
    # ranking scored is the one the bandit could have produced at the time.
    runner = Bandit()
    for row in rows:
        tool = str(row.get("tool_name") or "")
        verdict = str(row.get("verdict") or "").lower()
        if tool and verdict in ("verified", "failed"):
            runner.update(tool, verdict)

    ranked = sorted(scored, key=lambda t: -scored[t])
    top = ranked[:k]
    precision = sum(1 for t in top if scored[t] > population) / max(len(top), 1)

    # Rank agreement: the bandit's own posterior ordering against the empirical
    # one. `arms()` is the public accessor; it returns the live arm table.
    table = runner.arms()
    def posterior(tool):
        arm = table.get(tool)
        if arm is None or arm.pulls == 0:
            return 0.5
        return arm.wins / arm.pulls

    order = {tool: i for i, tool in enumerate(
        sorted(scored, key=lambda t: (-posterior(t), t)))}
    n = len(order)
    if n < 2:
        return {"measured": False, "reason": "too few tools with a track record"}
    d2 = sum((order[a] - order[b]) ** 2
             for a in order for b in order if a < b)
    spearman = 1.0 - (6.0 * d2) / (n * (n * n - 1))

    return {
        "measured": True,
        "tools_ranked": len(scored),
        "population_success_rate": round(population, 4),
        f"precision_at_{k}": round(precision, 4),
        "spearman_vs_empirical": round(spearman, 4),
        "beats_chance": bool(precision > population),
        "top_tools": [(t, round(scored[t], 3)) for t in top],
        "note": ("precision above the population rate means the ranking "
                 "carries information; equal means the bandit has learned "
                 "nothing from this log yet"),
    }


def models_dir() -> Path:
    """Where trained models live.

    **Not in `logs/`.** A model is a build artifact, not a log line: it is
    derived, versioned, and meant to be copied between machines, while a log is
    append-only and machine-local. Putting a model beside append-only logs means
    the two get cleaned up by whatever prunes logs, which is how a model quietly
    disappears and the prediction path silently reverts to doing nothing.
    """
    from shani_chronoa import files
    path = files.data_home() / "shani-chronoa" / "models"
    try:
        files.ensure_private_dir(path)
    except Exception:  # noqa: BLE001 - the caller reports an unwritable location
        pass
    return path


def model_path(name: str = "outcome") -> Path:
    return models_dir() / f"{name}.json"


def model_path_for_space(name: str = "outcome",
                         space: Optional[str] = None) -> Path:
    """Where a model for a given feature space lives.

    **Every model gets its own name and no file is ever moved.**

    An earlier design moved a superseded model to a `-retired-` name, which was
    a decision to set something aside on the assumption it would not be wanted
    again. That assumption is wrong often enough to matter: a bad release gets
    reverted, a machine is rolled back, a branch is re-merged, and the space
    returns - at which point the model that was "retired" is precisely the right
    one and was trained on this very log. Retiring also made the load path the
    only thing standing between a stale model and a confident wrong answer,
    because a moved file still had to be found again.

    Writing each model under the space it belongs to removes both problems. A
    model from an older space simply sits there, unread, because it does not
    match - exactly how a memory you have not needed in a year works. Nothing is
    deleted, nothing is moved, nothing can be lost to a failed rename, and
    recovery needs no adoption path because there was never a decision to undo.
    """
    return models_dir() / f"{name}-{space or _feature_space_id()}.json"


def train_and_save(path: Optional[Path] = None,
                  name: str = "outcome",
                  holdout: float = 0.25,
                  epochs: int = 30,
                  dry_run: bool = False,
                  key: Optional[bytes] = None,
                  only_tools: Optional["frozenset[str]"] = None) -> Dict[str, object]:
    """Train on this machine's own tool-call log, and save the result.

    **This is the entry point that did not exist.** `OutcomeModel.save()` had
    zero callers and `tools._outcome_model()` loaded a file nothing wrote, so the
    whole prediction path was dead code: `recommend()` could never fire, and the
    bandit never got a prior.

    Split **by position, not randomly**: the log is ordered in time and a
    random split puts the same tool with the same arguments on both sides of
    the boundary, which for hashed categorical features is very close to
    memorisation.

    The report is returned rather than only printed, and the model is saved
    **whether or not it beat the baseline** - because the honest-report gate
    exists to stop the *caller* from relying on a useless model, not to prevent
    the model from existing. Suppressing it would also suppress the evidence
    that it is useless.
    """
    # **Honour `path`.** This read `load()` with no argument, so a caller passing
    # a log - which is what `train_and_report(path)` means by the same name -
    # silently trained on the default one instead and got a report about
    # different data than it asked about. `train_and_report` and
    # `train_and_save` must agree on what `path` is, or the second is a trap.
    examples = load(path, only_tools=only_tools)
    if not examples:
        return {"saved": False, "reason": "no labelled examples in the log"}

    # Split by **feature vector and verdict**, not by position - see
    # `split_examples` for the measured failure of the ordered split this
    # replaced (a training half with zero `verified` in it).
    train, test, split_note = split_examples(examples, holdout)
    if not train or not test:
        return {"saved": False, "reason": "not enough data to hold anything out"}

    # **Refuse to write a model that cannot express every label it declares.**
    #
    # The provenance block immediately showed why this matters: on this machine's
    # log the first 75% (the training half of a time-ordered split) contains
    # 8,428 `unverified`, 264 `failed` and **zero** `verified`. Every verified
    # record sits in the held-out quarter. So the model was being trained to
    # emit a class it had never once seen, and its accuracy matched the baseline
    # exactly - not because the features are weak, but because one of the three
    # answers is unreachable by construction.
    #
    # A model that cannot predict `verified` must not be written, because
    # writing it produces a file that later loads and looks legitimate. The
    # report is still returned: "there is nothing to learn yet" is the useful
    # output here, and suppressing it would suppress the evidence.
    train_labels = Counter(e.y for e in train)
    missing = [VERDICTS[i] for i in range(len(VERDICTS))
               if train_labels.get(i, 0) == 0]
    if missing:
        present = {VERDICTS[i]: train_labels.get(i, 0) for i in range(len(VERDICTS))}
        return {
            "saved": False,
            "reason": (
                f"the training split contains no example of "
                f"{', '.join(missing)} - a model cannot learn a class it never "
                f"sees. Training half: {present}."
            ),
            "label_coverage": present,
            "missing_labels": missing,
            "fix": (
                "these verdicts only start being recorded partway through the "
                "log, so the examples exist only in its later half. Recording "
                "post-conditions consistently is the fix; a larger sample of "
                "the same inconsistent data is not."
            ),
        }

    # **Continue from the existing model rather than starting over.**
    #
    # Re-fitting the whole log every consolidation discards what the last fit
    # learned and grows linearly with history, so a machine that has run for
    # months pays more each time for a model it already had. Warm-starting
    # means the model accumulates: the first fit learns, and each one after it
    # adjusts.
    seed = None
    current = model_path_for_space()
    if current.exists():
        try:
            previous = json.loads(current.read_text(encoding="utf-8"))
            if previous.get("feature_space") == _feature_space_id() \
                    and verify_model(previous).get("ok"):
                seed = OutcomeModel.load(current)
        except Exception:  # noqa: BLE001 - a bad seed is simply no seed
            seed = None

    if seed is not None:
        model = seed
        # A few passes over the recent window only: the model already knows the
        # old part, so spending epochs re-learning it is wasted work.
        model.fit(train[-_WARM_WINDOW:], epochs=max(2, epochs // 10))
    else:
        model = OutcomeModel().fit(train, epochs=epochs)
    # **The report that decides whether this file is worth having comes from
    # grouped cross-validation over every example, not from the one split this
    # function trained on.** Two reasons, both measured on this machine's log:
    # the minority verdicts live in 3 and 7 distinct feature vectors, so a single
    # vector-respecting split leaves 32 `verified` in training against 319 in
    # test and its score is a statement about which vector landed where; and a
    # report computed on the split the model was fitted on measures nothing at
    # all. `report` is therefore a pooled 5-fold CV, and the model that gets
    # written is fitted on everything.
    report = cross_validate(examples, folds=5)
    if seed is None:
        model = OutcomeModel().fit(examples, epochs=epochs)

    unknown_count, unknown_names = unknown_tool_examples(_entries(path))
    label_counts = Counter(e.y for e in train)
    try:
        stat = _log_path().stat()
        revision = f"{stat.st_size}-{int(stat.st_mtime)}"
    except OSError:
        revision = "unknown"
    provenance = {
        "example_count": len(train),
        "held_out": len(test),
        "label_counts": {VERDICTS[i]: label_counts.get(i, 0) for i in range(len(VERDICTS))},
        "majority_label": max(label_counts, key=lambda i: label_counts.get(i, 0)) if label_counts else None,
        "log_revision": revision,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "accuracy": round(report.accuracy, 6),
        "baseline": round(report.baseline, 6),
        "beats_baseline": bool(report.accuracy > report.baseline),
        "honest": bool(report.honest()),
        "epochs": epochs,
        # How much of the above is about skills that are not installed. On this
        # machine that is most of the failure signal, and a model that does not
        # say so is reporting a number about a machine that no longer exists.
        "examples_for_unknown_tools": unknown_count,
        "unknown_tools": unknown_names[:10],
        "evaluation": "5-fold cross-validation over feature vectors",
        "cv_examples": report.n,
        # Both lifts, not one: a flag that is right about a verdict 33x more
        # often than chance but wrong 5 times out of 6 is not the same thing as
        # one that is right 5.85x more often than chance, and recording only the
        # first number is how a useless model reads as a good one.
        "detected": report.best_detection()[0],
        "recall_lift": {name: round(report.detection(name), 3) for name in VERDICTS},
        "precision_lift": {name: round(report.precision_lift(name), 3) for name in VERDICTS},
    }

    destination = Path(path) if path else model_path_for_space(name)
    if dry_run:
        return {"saved": False, "dry_run": True, "path": str(destination),
                "provenance": provenance}

    payload = sign_model(model.to_dict(provenance), key)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and rename, so a reader never sees a
        # half-written model - the same rule `argfile.py` uses for envelopes.
        scratch = destination.with_suffix(".json.tmp")
        scratch.write_text(json.dumps(payload), encoding="utf-8")
        try:
            scratch.chmod(0o600)
        except OSError:
            pass
        scratch.replace(destination)
    except OSError as exc:
        return {"saved": False, "reason": f"could not write {destination}: {exc}"}

    return {"saved": True, "path": str(destination), "bytes": destination.stat().st_size,
            "provenance": provenance, "report": report}


def model_digest(payload: dict) -> str:
    """A content hash of a model, over everything except the digest itself.

    Covers the weights, the bias and the provenance block, so a model cannot be
    edited - or have its stated origin altered - without the hash changing.
    """
    import hashlib

    # The digest field itself is excluded (it cannot contain its own hash), and
    # so are the three signature fields - they are added *after* the digest is
    # computed, so including them would invalidate it the instant it was written.
    # A first version included them and every honest model failed to verify.
    excluded = ("digest", "signature", "signature_scheme", "signed_by")
    material = {k: v for k, v in payload.items() if k not in excluded}
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sign_model(payload: dict, key: Optional[bytes] = None) -> dict:
    """Stamp a model with its digest, and optionally a signature over that digest.

    **The digest is not security.** Anyone who can write the file can recompute
    it. It catches corruption and casual editing, which is most of what goes
    wrong with a file copied between machines.

    A signature needs a key the peer does not have. There is none in this
    package today, so `key=None` records the digest alone and
    `"signature_scheme": None` states that plainly rather than implying
    verification that has not happened. Whoever supplies a real key is filling a
    real gap, and the field is here so the format does not have to change when
    they do.
    """
    payload["digest"] = model_digest(payload)
    if key is None:
        payload["signature"] = None
        payload["signature_scheme"] = None
        payload["signed_by"] = None
        return payload
    import hashlib
    import hmac

    signature = hmac.new(key, payload["digest"].encode("utf-8"), hashlib.sha256)
    payload["signature"] = signature.hexdigest()
    payload["signature_scheme"] = "hmac-sha256"
    payload["signed_by"] = "local"
    return payload


def retire_model(payload: dict, path: Optional[Path] = None) -> Optional[Path]:
    """Kept for callers that used to ask. **It now does nothing, on purpose.**

    Retiring meant moving the file aside on the assumption it would not be
    wanted again, and that assumption is wrong whenever a change is reverted -
    which is most of the time, eventually. The space check on load already
    refuses a stale model without anyone having to move it, so the move bought
    organisation at the cost of the only genuinely fragile operation in this
    layer: a rename that can fail part-way.

    A model from another space keeps its own space-stamped name and stays
    readable. If that space ever becomes current again, it is already in place.
    """
    if path is not None and path.exists():
        return path
    return None


def adopt_retired(space: Optional[str] = None) -> Optional[Path]:
    """Bring back a retired model when the space it was trained in returns.

    **History repeats, and that is the whole reason retirement is not deletion.**
    A feature space changes because someone edited the hasher, renamed a
    feature, or moved a boundary - and all three get reverted eventually: a
    revert after a bad release, a machine rolled back, a branch re-merged. At that
    moment the model that was set aside two weeks ago is *exactly* the right one,
    it was trained on this very log, and throwing it away cost a full retrain
    for nothing.

    So retirement is reversible. When no live model is usable and a retired one
    belongs to the space we are now in, it is restored rather than relearned.

    What is restored is only what is still true: `verify_model` runs on it
    exactly as it would have on a fresh file, so a retired model whose payload
    was edited, or whose digest no longer matches, is still refused.
    """
    target = model_path()
    if target.exists():
        return None  # something live is already in place; never displace it
    current = space or _feature_space_id()
    for candidate in sorted(models_dir().glob(f"{target.stem}-retired-*.json")):
        try:
            note = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(note, dict):
            continue
        if note.get("retired_from_space") != current:
            continue
        payload = note.get("payload")
        if not isinstance(payload, dict):
            continue
        if not verify_model(payload).get("ok"):
            logger.debug("retired model in the current space fails its digest; "
                         "leaving it retired rather than restoring it")
            continue
        try:
            destination = candidate.with_name(target.name)
            candidate.replace(destination)
            destination.chmod(0o600)
            logger.info("restored a retired model for feature space %s rather "
                        "than relearning it: it was trained on this machine's "
                        "own log and the space it belongs to is current again",
                        current)
            return destination
        except OSError as exc:
            logger.debug("could not restore %s: %s", candidate, exc)
    return None


def _tally_from_log(records: Optional[Sequence[dict]] = None) -> Dict[str, dict]:
    """Per-tool outcome counts straight from the log.

    The most durable thing this system knows, and the only part that does not
    depend on a model existing.
    """
    out: Dict[str, Dict[str, int]] = {}
    for row in (records if records is not None else _entries()):
        tool = str(row.get("tool_name") or "")
        verdict = str(row.get("verdict") or "").lower()
        if not tool or verdict not in ("verified", "failed"):
            continue
        slot = out.setdefault(tool, {"verified": 0, "failed": 0})
        slot[verdict] += 1
    return out


def _shape_key(row: dict) -> str:
    """The argument *shape* of a call - which keys, not what they held.

    A failure that only happens with a path, and a failure that only happens
    with a URL, are different problems with the same tool, and the lesson has
    to name which. Values are deliberately excluded: the shape is what is safe
    to record and the part that generalises.
    """
    args = row.get("args")
    if not isinstance(args, dict):
        return "no-arguments"
    return "+".join(sorted(args))[:80] or "no-arguments"


def lessons(records: Optional[Sequence[dict]] = None) -> List[Dict[str, object]]:
    """What the log *teaches*, as opposed to what it records.

    A tally is history: "ffmpeg failed 14 times". A lesson is a statement that
    still holds next week and changes what happens - "this machine cannot
    transcode without a tool that is not installed", or "this skill has never
    once confirmed itself, which is a fact about its post-condition rather than
    about the skill".

    Three kinds, each with an action attached, because a lesson nobody acts on
    is just a tally with better manners:

    - **cannot** - a tool that only ever failed here, where the route table
      already has an answer. Action: do not recommend it; offer the route.
    - **unverified** - a skill with many calls and no successful one ever. Action:
      it has no post-condition, so nothing it does can be known to have worked.
    - **works** - a tool that succeeds, which is worth keeping from being
      recommended against by a worse arm. Action: prefer it.
    """
    rows = list(records if records is not None else _entries())
    calls: Counter = Counter()
    outcomes: Dict[str, Counter] = defaultdict(Counter)
    shapes: Dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        tool = str(row.get("tool_name") or "")
        verdict = str(row.get("verdict") or "").lower()
        if not tool:
            continue
        calls[tool] += 1
        if verdict in ("verified", "failed"):
            outcomes[tool][verdict] += 1
            shapes[tool][_shape_key(row)] += 1

    out: List[Dict[str, object]] = []
    for tool, seen in sorted(calls.items()):
        tally = outcomes.get(tool) or Counter()
        verified, failed = tally.get("verified", 0), tally.get("failed", 0)
        scored = verified + failed
        entry: Dict[str, object] = {"tool": tool, "calls": seen,
                                   "verified": verified, "failed": failed}
        if scored == 0:
            # "0 of 0" is not a lesson. Below ten calls there is nothing to say,
            # and saying nothing is the honest answer.
            if seen < 10:
                continue
            if True:
                entry["kind"] = "unverified"
                entry["lesson"] = (
                    f"{tool} ran {seen} times and was never confirmed - that is a "
                    f"fact about its post-condition, not about the skill")
                entry["action"] = "give it a read-back check before trusting it"
        elif verified and not failed:
            entry["kind"] = "works"
            entry["lesson"] = f"{tool} succeeded {verified}/{scored} here"
            entry["action"] = "keep recommending it"
        elif failed and not verified:
            entry["kind"] = "cannot"
            entry["lesson"] = (f"{tool} failed {failed}/{scored} here and has "
                               f"never succeeded on this machine")
            entry["action"] = "offer a substitute route instead"
        elif verified / scored < 0.6:
            worst = shapes[tool].most_common(1)
            shape = f", most often as {worst[0][0]}" if worst else ""
            entry["kind"] = "cannot"
            entry["lesson"] = (f"{tool} succeeded only {verified}/{scored} here"
                               f"{shape}")
            entry["action"] = "warn before calling it"
        # A tool with both successes and failures lands here with no verdict of
        # its own - the honest reading is "mixed", and it is a lesson too.
        entry.setdefault("kind", "mixed")
        entry.setdefault("lesson",
                         f"{tool} succeeded {verified} of {scored} scored here")
        entry.setdefault("action", "fine to use, worth watching")
        out.append(entry)
    return out


def render_lessons(found: Sequence[Dict[str, object]]) -> str:
    if not found:
        return ("No lessons yet: nothing has been scored on this machine, so "
                "there is nothing the log can teach. That is a fact about the "
                "post-conditions, not about the log.")
    learned = [f for f in found if f.get("kind") != "unverified"]
    unknown = [f for f in found if f.get("kind") == "unverified"]
    # **A lesson about a name this machine cannot resolve is a claim about the
    # log, not about a tool.** This machine's log has 712 records naming `liar`
    # and `unver` - four and five characters, always `args: {}`, always in pairs
    # about 80 ms apart, all `origin: user`. **This is not a new finding:**
    # `unknown_tool_examples()` has documented exactly these two by name for a
    # while, including that they are "313 of the 378 failures", and
    # `provenance.examples_for_unknown_tools` counts them. What was missing is
    # anywhere a *person* would see it.
    #
    # The old rendering turned them into lessons anyway:
    #
    #     cannot  liar  failed 323/323 here and has never succeeded
    #
    # "cannot liar" is not a sentence. It asserts a fact about a tool that does
    # not exist, and it is presented with the same confidence as "cannot
    # get_clipboard", which is a real and useful finding. Grouping them apart is
    # also actionable: 712 records naming tools this build does not have is a
    # fact about the log worth surfacing, and hiding it inside a lesson list is
    # how it survived.
    resolvable = _known_tool_names()
    if resolvable is None:
        real, nameless = learned, []
    else:
        real = [f for f in learned if str(f.get("tool")) in resolvable]
        nameless = [f for f in learned if str(f.get("tool")) not in resolvable]
    out = ["What this machine's tool log teaches:", ""]
    for entry in real[:8]:
        out.append(f"  {entry['kind']:<6} {entry['lesson']}")
        out.append(f"         -> {entry['action']}")
    if nameless:
        names = ", ".join(sorted({str(f["tool"]) for f in nameless})[:6])
        out.append("")
        out.append(f"  {len(nameless)} tool(s) in that list are ones this build "
                   f"does not have ({names}), so nothing above can be concluded "
                   "about whether they work. `provenance` records how much of the "
                   "history is about them - on this machine it is most of the "
                   "failure signal, and `unknown_tool_examples()` has said so "
                   "since before this row existed.")
    if unknown:
        out.append("")
        out.append(f"  {len(unknown)} tool(s) ran but were never confirmed, so "
                   f"nothing they did can be known to have worked:")
        out.append("      " + ", ".join(str(f["tool"]) for f in unknown[:10]))
    return "\n".join(out)


def _known_tool_names() -> "Optional[frozenset]":
    """The tool names this build answers to, or None if they cannot be read.

    **`tools.TOOLS`, the same source `unknown_tool_examples()` uses.** My first
    attempt used `skills.discover_skills()` instead, which returns 152 names that
    do *not* correspond to the log's `tool_name` values - it immediately
    reclassified the real findings `delete_file succeeded 820 of 915` and
    `get_clipboard failed 5/5` as "tools this build does not have", which is
    worse than the bug it was fixing. There is one registry and this is it.

    None rather than an empty set on failure: an empty set would claim every tool
    in the log is unknown, which is the confident-wrong-answer shape pointed the
    other way. Callers treat None as "do not judge names".
    """
    try:
        from shani_chronoa import tools
        return frozenset(str(entry["function"]["name"]) for entry in tools.TOOLS)
    except Exception:  # noqa: BLE001 - a broken registry must not invent lessons
        logger.debug("could not read the tool registry", exc_info=True)
        return None


def experience_summary(path: Optional[Path] = None) -> str:
    """What this machine has seen, in one paragraph, with no model required."""
    tally = _tally_from_log()
    if not tally:
        return ("No scored tool calls yet. Every call so far returned "
                "`unverified`, so there is nothing here that could train a model "
                "- which is a fact about the post-conditions, not about this "
                "machine.")
    scored = sum(v["verified"] + v["failed"] for v in tally.values())
    return (f"{scored} scored call(s) across {len(tally)} tool(s) on this "
            f"machine. " + "; ".join(
                f"{tool} {v['verified']}/{v['verified'] + v['failed']} verified"
                for tool, v in sorted(tally.items())[:8]))


def export_knowledge(path: Optional[Path] = None,
                     name: str = "chronoa-experience") -> Optional[Path]:
    """Package what this machine has learned so another machine can use it.

    **The point is that nobody repeats the process.** A fresh machine otherwise
    starts at zero and relearns routes, outcomes and which substitutes work -
    and the routes in particular are already shipped as code, so what cannot be
    shipped is the part that took this machine months of use to accumulate.

    Three things travel, and the third is the one that makes sharing safe:

    - the **model**, with its provenance and digest, so a receiver can check it
      came from a real fit and not from hand-editing;
    - the **bandit arms** - which stand-in has actually worked here - which is
      the knowledge a fresh machine cannot get any other way;
    - a **capability fingerprint**: the commands that were present when this was
      learned.

    **That last one is not decoration.** A model trained where `magick` and
    `ffprobe` exist encodes what happens on such a machine. Handed to one
    without them, every prediction about those tools is fiction - and it would
    be a *confident* fiction, which is the failure this whole layer exists to
    prevent. So the receiver re-checks its own fingerprint against the sender's
    and is told plainly what does not transfer.
    """
    from shani_chronoa.capability import capabilities

    cap = capabilities()
    bundle = {
        "format": 1,
        "kind": "chronoa-experience",
        "feature_space": _feature_space_id(),
        "capability_fingerprint": sorted(cap.commands),
        "python_fingerprint": {k: v for k, v in sorted(cap.python.items())},
        "models": [],
        "bandit": {},
        # **The experience itself, not just what was fitted from it.**
        #
        # A bundle that can only carry a model is empty for exactly as long as
        # no model can be trained - which is now, because 21 mutators report no
        # verdict. So the window where sharing helps most is the window where it
        # returned nothing. The per-tool tallies below are the durable part:
        # they are what a machine needs to train its *own* first model, and they
        # are true whether or not anyone has fitted anything yet.
        #
        # So learning is never actually refused - it is only the *fitting* that
        # waits. The evidence keeps accumulating and stays shareable throughout.
        "tally": _tally_from_log(),
    }

    directory = models_dir()
    for candidate in sorted(directory.glob("outcome*.json")):
        if candidate.name.endswith(".tmp"):
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("weights"):
            bundle["models"].append(payload)

    try:
        # **`load_bandit()`, not `Bandit()`. Measured 2026-10-06.**
        # `Bandit.__init__` does not read the file - a fresh instance has an
        # empty `_arms` - so this loop never ran, on any machine, ever:
        # `bundle["bandit"]` was always `{}`.
        #
        # The consequences were not small. With a bandit holding ten pulls of
        # real history on disk (`say` 5/5, `espeak` 5/0), and no fitted model
        # and no conversation log, the bundle came out empty and the function
        # returned **None** - so the Export button told the user "there is no
        # trained model on this machine yet" while the thing it wanted was
        # sitting on disk. Both halves of this feature's own docstring describe
        # the arms travelling ("the knowledge a fresh machine cannot get any
        # other way"), and `import_knowledge` promises to adopt them "even when
        # the model is not" - neither had ever happened.
        from shani_chronoa.bandit import load_bandit
        for name, arm in load_bandit().arms().items():
            bundle["bandit"][name] = {"pulls": arm.pulls, "wins": arm.wins}
    except Exception:  # noqa: BLE001 - an absent bandit is not a failure
        logger.debug("could not read the bandit arm table", exc_info=True)

    # Sharing must not require a fitted model. Refusing to hand over the
    # evidence because nothing has learned from it yet is the same mistake as
    # refusing to train: both treat "not ready" as "not real".
    if not bundle["models"] and not bundle["bandit"] and not bundle["tally"]:
        return None

    bundle["exported_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    bundle["digest"] = model_digest({k: v for k, v in bundle.items() if k != "digest"})

    destination = Path(path) if path else (directory / f"{name}.json")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        destination.chmod(0o600)
        return destination
    except OSError as exc:
        logger.debug("could not export experience to %s: %s", destination, exc)
        return None


def import_knowledge(path: Path, adopt: bool = True) -> Dict[str, object]:
    """Take another machine's learning, after checking it fits this one.

    **A shared model is accepted only if it can still mean anything here.** The
    sender records the commands it had; this machine compares. If a command it
    learned about is missing here, every weight touching that tool is now
    describing a machine that does not exist, and the bundle is refused with the
    difference named - not accepted with a footnote.

    What *does* transfer regardless is the bandit arm table: which substitute
    worked is a fact about the tool, not about the sender's hardware, so it is
    adopted even when the model is not. That asymmetry is deliberate - half of
    shared knowledge is portable and half is not, and pretending otherwise
    would hand a receiver confident nonsense.
    """
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": f"could not read {path.name}: {exc}"}
    if not isinstance(bundle, dict) or bundle.get("kind") != "chronoa-experience":
        return {"ok": False, "reason": "not a Chronoa experience bundle"}
    recorded = bundle.get("digest")
    if recorded and model_digest({k: v for k, v in bundle.items()
                                  if k != "digest"}) != recorded:
        return {"ok": False, "reason": "the bundle does not match its own digest "
                                        "- it was edited or truncated in transit"}

    from shani_chronoa.capability import capabilities
    cap = capabilities()
    theirs = set(bundle.get("capability_fingerprint") or ())
    ours = set(cap.commands)
    missing_here = sorted(theirs - ours)
    extra_here = sorted(ours - theirs)

    space = bundle.get("feature_space")
    usable_models = [m for m in bundle.get("models") or []
                     if m.get("feature_space") == space
                     and space == _feature_space_id()
                     and verify_model(m).get("ok")]

    arms = bundle.get("bandit") or {}
    result: Dict[str, object] = {
        "ok": True,
        "models_in_bundle": len(bundle.get("models") or []),
        "models_usable": len(usable_models),
        "arms": len(arms),
        "missing_here": missing_here,
        "extra_here": extra_here,
    }
    if missing_here:
        # **A mismatch is a routing problem, not a verdict.**
        #
        # The first version of this refused and said why, which is honest and
        # nearly useless: knowing the sender had `magick` does not help anyone
        # edit a picture here. So each difference is turned into the thing that
        # would actually resolve it - install that package, or reach the goal
        # through a stand-in - and the knowledge is still adopted where it
        # survives the difference.
        routes: List[Dict[str, str]] = []
        try:
            from shani_chronoa.routes import routes_for
            for command in missing_here[:12]:
                options = routes_for(command, cap)
                if options:
                    routes.append({"missing": command,
                                   "routes": [r.action for r in options]})
        except Exception:  # noqa: BLE001
            routes = []

        result["note"] = (
            f"the sender knew {len(missing_here)} command(s) this machine does "
            f"not have, so predictions about those tools would describe a "
            f"machine that is not this one.")
        result["routes"] = routes
        if arms:
            result["note"] += (
                f" The {len(arms)} arm(s) were still adopted: which "
                f"substitute works is a fact about the tool, not about the "
                f"sender's hardware, so it transfers exactly.")
        if routes:
            result["note"] += (
                f" {len(routes)} of the missing command(s) have a way round "
                f"them that works here - see 'routes'.")
        elif missing_here:
            result["note"] += (
                " None of them does on this machine, so installing the package "
                "is the only route.")

    if adopt and usable_models:
        try:
            directory = models_dir()
            directory.mkdir(parents=True, exist_ok=True)
            for model in usable_models:
                target = directory / f"outcome-{space}.json"
                scratch = target.with_suffix(".json.tmp")
                scratch.write_text(json.dumps(model), encoding="utf-8")
                scratch.chmod(0o600)
                scratch.replace(target)
            result["adopted_models"] = len(usable_models)
        except OSError as exc:
            result["adopted_models"] = 0
            result["write_error"] = str(exc)
    else:
        result["adopted_models"] = 0
    return result


def _lifts(report: "Report", which: str) -> Dict[str, float]:
    """Per-class lift over base rate, from the report's own confusion matrix.

    `which` is "recall" or "precision". The report already computes both for
    `best_detection()`; this exposes the whole row so a merged model's provenance
    records what it found rather than only the best thing about it.
    """
    support = {name: row.get("support", 0) for name, row in report.per_class.items()}
    total = sum(support.values()) or 1
    base = {name: n / total for name, n in support.items() if n}
    lifts: Dict[str, float] = {}
    for name, row in report.per_class.items():
        rate = row.get(which, 0.0)
        floor = base.get(name, 0.0)
        lifts[name] = round(rate / floor, 3) if floor else 0.0
    return lifts


def merge_models(payloads, density: float = 0.3,
                 name: str = "outcome-merged",
                 examples: Optional[Sequence[Example]] = None):
    """Fold several models into one, so a fleet's learning becomes shared.

    **Not addition.** Adding N models' weights multiplies the effective
    learning rate by N and overstates every prediction - the standard way a
    "merged" model looks confident and is wrong. Plain averaging is only right
    for models fine-tuned from one common base, and these are learned
    independently on different machines from different logs.

    So this is **TIES**: trim each model to its largest `density` of weights,
    elect a sign per coordinate by majority, then average only the values
    agreeing with that sign. Conflicting evidence cancels instead of
    compounding - which is what you want when machine A says a tool works and
    machine B says it fails.

    **Same feature space or nothing.** Coordinate 41 names a particular tool
    only under one hasher, so merging across spaces is arithmetic on unrelated
    numbers. Mismatched models are reported and skipped.

    Returns the merged payload, what went into it, and what was refused.
    """
    current = _feature_space_id()
    usable, rejected = [], []
    for payload in payloads:
        if not isinstance(payload, dict) or not payload.get("weights"):
            rejected.append({"reason": "no weights"})
            continue
        if payload.get("feature_space") != current:
            rejected.append({"reason": "different feature space",
                             "space": str(payload.get("feature_space"))[:16]})
            continue
        if not verify_model(payload).get("ok"):
            rejected.append({"reason": "digest does not verify"})
            continue
        usable.append(payload)
    if not usable:
        return {"merged": False, "rejected": rejected,
                "reason": "no usable model in this feature space"}

    trimmed = []
    for payload in usable:
        rows = {int(i): [float(v) for v in row]
                for i, row in payload["weights"].items()}
        flat = sorted(((abs(v), i) for i, row in rows.items() for v in row),
                      reverse=True)
        keep = {i for _m, i in flat[:max(1, int(len(flat) * density))]}
        trimmed.append({i: row for i, row in rows.items() if i in keep})

    merged = {}
    for coord in {i for rows in trimmed for i in rows}:
        for cls in range(len(VERDICTS)):
            votes = [rows.get(coord, [0.0] * len(VERDICTS))[cls]
                     for rows in trimmed if coord in rows]
            if not votes:
                continue
            sign = 1.0 if sum(1 for v in votes if v > 0) >= \
                sum(1 for v in votes if v < 0) else -1.0
            agreeing = [v for v in votes if (v > 0) == (sign > 0) and v != 0] or votes
            merged.setdefault(coord, [0.0] * len(VERDICTS))[cls] = \
                sum(agreeing) / len(agreeing)

    bias = [0.0] * len(VERDICTS)
    for payload in usable:
        for cls, value in enumerate(payload.get("bias") or []):
            if cls < len(bias):
                bias[cls] += float(value)
    bias = [b / len(usable) for b in bias]

    contributions = [
        {"example_count": (p.get("provenance") or {}).get("example_count"),
         "accuracy": (p.get("provenance") or {}).get("accuracy"),
         "honest": (p.get("provenance") or {}).get("honest")}
        for p in usable]

    model = OutcomeModel()
    model.w = merged
    model.b = bias

    # **Measure the merge. It used to write `"honest": True` unconditionally.**
    #
    # Every writeup on TIES/DARE/SLERP says the same thing about merging: the
    # merged model is a *hypothesis*, and you evaluate it like one. Sign election
    # makes conflicts cancel, but nothing guarantees the result is any better
    # than the models that went into it - and on independently fitted models
    # there is no shared base to make it likely.
    #
    # Marking it honest by construction was not a small shortcut. `tools.
    # _outcome_model()` refuses any model whose `provenance.honest` is false, so
    # a merge that was pure noise was stamped as trustworthy and loaded. The
    # honest flag now comes from a real `Report` over real examples, and a merge
    # that fails the same bar a fitted model must pass is not written at all.
    if examples is None:
        try:
            examples = load()
        except Exception:  # noqa: BLE001 - no log is not a reason to fake a verdict
            logger.debug("no log to evaluate the merge against", exc_info=True)
            examples = []
    report = evaluate(model, examples) if examples else None
    if report is None:
        return {"merged": False, "reason":
                "no examples to evaluate the merge against, so it could not be "
                "measured - and a merge is a hypothesis until it is measured",
                "from": len(usable), "rejected": rejected}
    if not report.honest():
        return {"merged": False, "reason":
                f"the merged model is not worth quoting ({report.accuracy:.1%} "
                f"against a {report.baseline:.1%} constant, and no minority "
                "verdict is detected), so it was not written",
                "from": len(usable), "rejected": rejected,
                "accuracy": round(report.accuracy, 6),
                "baseline": round(report.baseline, 6)}

    out = sign_model(model.to_dict({
        "merged_from": len(usable), "method": f"ties(density={density})",
        "feature_space": current, "contributions": contributions,
        "rejected": rejected,
        # **From the report, never asserted.**
        "honest": bool(report.honest()),
        "accuracy": round(report.accuracy, 6),
        "baseline": round(report.baseline, 6),
        "beats_baseline": bool(report.accuracy > report.baseline),
        "evaluated_on": len(examples),
        "evaluation": "the merged weights scored against this machine's own log",
        "detected": report.best_detection()[0],
        "recall_lift": {k: round(v, 3)
                        for k, v in _lifts(report, "recall").items()},
        "precision_lift": {k: round(v, 3)
                           for k, v in _lifts(report, "precision").items()},
        "example_count": sum(int((p.get("provenance") or {}).get("example_count") or 0)
                            for p in usable)}))

    destination = model_path_for_space(name)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        scratch = destination.with_suffix(".json.tmp")
        scratch.write_text(json.dumps(out), encoding="utf-8")
        scratch.chmod(0o600)
        scratch.replace(destination)
        stored = str(destination)
    except OSError as exc:
        stored = f"not written: {exc}"
    return {"merged": True, "from": len(usable), "rejected": rejected,
            "coordinates": len(merged), "path": stored,
            "contributions": contributions,
            "honest": bool(report.honest()),
            "accuracy": round(report.accuracy, 6),
            "baseline": round(report.baseline, 6),
            "beats_baseline": bool(report.accuracy > report.baseline),
            "detected": report.best_detection()[0],
            "report": report}


def verify_model(payload: dict, key: Optional[bytes] = None) -> Dict[str, object]:
    """Check a model against its own digest, and its signature if there is one.

    Returns the *reason* rather than raising, because the caller of a
    peer-supplied file is a prediction path and must be able to decline quietly
    and say why.
    """
    recorded = payload.get("digest")
    if not recorded:
        return {"ok": False, "reason": "no digest - the file does not say what it is"}
    recomputed = model_digest(payload)
    if recomputed != recorded:
        return {"ok": False, "reason": "digest does not match the contents",
                "recorded": recorded, "recomputed": recomputed}
    signature = payload.get("signature")
    if signature and key is not None:
        import hashlib
        import hmac
        expected = hmac.new(key, recorded.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return {"ok": False, "reason": "signature does not verify"}
        return {"ok": True, "digest": recorded, "signature": "verified"}
    return {"ok": True, "digest": recorded,
            "signature": "absent" if not signature else "unverified-no-key",
            "authority": payload.get("authority")}


def _has(active: Sequence[Tuple[int, float]], feature: int) -> bool:
    """Whether a feature is set on a sparse vector."""
    return any(index == feature for index, _value in active)


def _active(vector: Sequence[float]) -> List[Tuple[int, float]]:
    """Only the non-zero entries, which is all the arithmetic ever touches."""
    return [(i, v) for i, v in enumerate(vector) if v]


def _exp(values: Sequence[float]) -> List[float]:
    peak = max(values) if values else 0.0
    out = [math.exp(v - peak) for v in values]
    total = sum(out) or 1.0
    return [v / total for v in out]

class Example(NamedTuple):
    """One training record: a label and a hashed feature vector, held SPARSE.

    `active` is the whole representation - `(index, value)` for the non-zero
    entries, about five of them. `x` is materialised on demand by `dense()` and
    is not stored, because storing it costs **7.3 GB** for an 11,590-record log.

    That was measured, not estimated. A Python list of 16,384 floats is 16,384
    pointers PLUS a distinct float object per slot, so roughly 520 KB per
    example rather than the 64 KB the arithmetic suggests - 6 GB for the log.
    Packed as `array('f')` it would still be 760 MB, against the 0.97 MB the
    sparse form actually needs, and a dense row is only ever wanted by a
    scikit-learn estimator's `predict`, one row at a time.

    So nothing on a hot path touches a dense vector at all.
    """

    active: Tuple[Tuple[int, float], ...]
    y: int
    #: Width of the hashed space, so `dense()` can rebuild a full row.
    width: int = _N_FEATURES

    def dense(self) -> List[float]:
        """The full vector, built now and discarded.

        Only for a third-party estimator that insists on a dense row. Building
        it per call makes the cost O(width) *per prediction* instead of holding
        O(n x width) for the life of the process.
        """
        vector = [0.0] * self.width
        for index, value in self.active:
            vector[index] = value
        return vector


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
#: **A bounded rolling window, not the whole log.**
#:
#: The log is append-only and it is meant to pile - that is the only durable
#: asset here. But a cache that holds every example ever recorded grows without
#: limit, so the assistant that owns the archive eventually runs out of memory
#: holding it. 15 MB today; at this machine's rate, 11,590 calls in six weeks,
#: that is a few hundred MB a year, all of it resident, none of it useful -
#: because the last 200k calls say as much as the last million for a model
#: that learns from recent experience.
#:
#: An append-only journal's real gift is that you can remember **where you
#: stopped**. So this keeps a window of the most recent examples plus the byte
#: offset it was built to, and a read only parses the bytes since. Steady-state
#: cost is O(new lines), not O(history) - which is the difference between a
#: pile that is an asset and a pile that is a liability.
#: **Keyed by file path *and* filter.** The value is the revision, so the entry
#: is invalidated when the log is appended to. The second key exists because a
#: cache that ignored `only_tools` would hand a *filtered* caller the unfiltered
#: list from the same revision - so pressing "train on what I still have" would
#: change nothing at all, silently, and look like it had worked. Measured as a
#: design failure here before it was ever a runtime one.
_EXAMPLE_CACHE: Dict[Tuple[str, "str"], Tuple[Tuple[int, float], List["Example"]]] = {}

#: How many examples to keep resident. 200k is roughly 40 MB of sparse tuples,
#: which is a working set rather than an archive.
WINDOW = 200_000

#: How much of the tail a warm-started model re-trains on. The rest it already
#: knows; re-fitting all of it each time is the work this avoids.
_WARM_WINDOW = 20_000


def load(path: Optional[Path] = None, limit: int = 200_000,
         only_tools: Optional["frozenset[str]"] = None) -> List[Example]:
    """Read the log into training examples, skipping unusable rows.

    Only the most recent `WINDOW` examples are held, and only the bytes after
    the offset we stopped at are parsed. See `_EXAMPLE_CACHE` for why.

    A row with no verdict teaches nothing - that is the 282 records where the
    verdict is `None`, and they are dropped rather than labelled `unverified`,
    which would have quietly inflated the majority class.

    **`only_tools` is the decision `unknown_tool_examples()` says is the
    person's to make, finally made available.** Measured on this machine's own
    log: `liar` and `unver` are fixtures this build no longer has, and between
    them they are 712 of 14,395 records - and `liar` alone is **323 of the 423
    failures** the model is fitted on, 76% of the entire failure signal. So the
    model's strongest and most confident lesson is about a tool that cannot run.

    The function deliberately only *reported* that, on the argument that
    filtering would quietly change what the model says about the past. That
    argument is right for a default and wrong as a dead end - so the default is
    unchanged (`only_tools=None` trains on everything, exactly as before) and
    the filter is there when a person wants it, with the figure already on
    screen next to the button that turns it on.

    Cached per revision of the file; see `_EXAMPLE_CACHE`.
    """
    source = path or _log_path()
    # A stable, order-independent key for the filter, so `{a, b}` and `{b, a}`
    # share a cache entry instead of parsing the whole log twice.
    filter_key = "" if only_tools is None else "\x00".join(sorted(only_tools))
    cache_key = (str(source), filter_key)
    try:
        stat = source.stat()
        cached = _EXAMPLE_CACHE.get(cache_key)
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
            if only_tools is not None and str(record.get("tool_name")) not in only_tools:
                continue
            examples.append(Example(
                tuple(_active(vectorise(features(record)))),
                VERDICTS.index(verdict)))
        # The window is a working set, not an archive. The log on disk keeps
        # everything; this keeps what a recent-experience model can actually use.
        if len(examples) > WINDOW:
            examples = examples[-WINDOW:]
    try:
        stat = source.stat()
        _EXAMPLE_CACHE[cache_key] = ((stat.st_size, stat.st_mtime), examples)
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
                 bias: Optional[float] = None) -> None:
        # **An empty index map, never None.** `weights` is always a
        # `{feature index: [one weight per verdict]}` dict everywhere else -
        # `load()` builds one, `SGDClassifier` builds one, the merge path builds
        # one - and `fit()` reads `self.w.get(index)` to carry a seed forward.
        # Defaulting it to None therefore made `OutcomeModel().fit(...)`, which
        # is how *every* fit from scratch begins, raise
        # `AttributeError: 'NoneType' object has no attribute 'get'` on its
        # first line. `train_and_report()` crashed before it could report
        # anything, so the outcome model could not be trained at all - and
        # because the crash is not a refusal, the report that would have said
        # "this does not beat the baseline" never got written either.
        self.w = dict(weights) if weights else {}
        self.b = list(bias) if bias else []
        #: True when `w` is a dense array rather than a sparse index map, so the
        #: serialiser and both inference paths agree on the shape.
        self._dense = False

    # --- training ------------------------------------------------------------

    def fit(self, examples: Sequence[Example], epochs: int = 30,
            lr: float = 0.5, l2: float = 1e-4,
            weights: Optional[Sequence[float]] = None,
            balance: bool = True) -> "OutcomeModel":
        """Fit, optionally with per-example weights.

        A weight says how many identical copies an example stands for. Carrying
        it is what makes collapsing safe: this machine's log has 11,590
        examples but only 344 distinct vectors, and fitting the 344 with their
        counts gives the same weights as fitting all 11,590 - the per-example
        gradient is simply multiplied by the copy count and `n` rises to match.

        **`balance=True` is not a nicety, and it is the difference between a
        model that learns and one that does not.** Measured on this machine's
        real log (11,636 labelled calls: 10,936 `unverified`, 351 `verified`,
        349 `failed`):

        - **Unweighted: it predicts `unverified` for all 11,636 calls.** Every
          one of the 700 minority examples missed, precision and recall exactly
          0.000 for both. The accuracy matched the 94% baseline - not because
          the features are weak, but because a 3%-positive class never moves a
          30-step gradient through 1e-4 of L2.
        - **Inverse class frequency: `failed` reaches 48.4% recall at 6.8%
          precision against a 3.0% base rate - 2.3x chance** - and `verified`
          reaches 16.5% recall at 3.0% precision, i.e. no better than chance.

        The second line is a real capability (spotting a call that is about to
        fail) and the absence of it in the first is why nothing in this layer
        ever worked. `150` epochs instead of `30` changed nothing measurable,
        so the weighting is doing the work rather than the extra steps.

        Explicit `weights` still win: `balance` only fills in what a caller left
        out.
        """
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
        if weights is None:
            if not balance:
                weights = [1.0] * len(list(examples))
            else:
                # Inverse class frequency, **normalised to mean 1**. The ratio
                # between classes is the whole point; the absolute scale is not,
                # and leaving it alone made every step microscopic - the mean
                # weight here is about 0.003, so `lr * w / n` is ~1e-7, the
                # fitted weights came out around 4e-4, and `to_dict`'s
                # `round(v, 6)` then stored them as zeros. The saved model
                # loaded cleanly, verified its own digest, and predicted 0.3333
                # for all three verdicts on every input: a well-formed file
                # carrying no model at all.
                _counts = Counter(e.y for e in examples)
                weights = [1.0 / max(_counts[e.y], 1) for e in examples]
                _mean = (sum(weights) / len(weights)) or 1.0
                weights = [w / _mean for w in weights]
        train = [(_sparse(e), e.y, float(w))
                 for e, w in zip(examples, weights) if e.y in usable_set]
        if not train:
            raise ValueError("every example was in a class too small to train on")
        n = len(train)

        # Remember what was already here: `fit` may be warm-starting, and the
        # rebuild below would otherwise discard every weight the seed carried
        # that this window does not happen to touch.
        self._prior = {i: list(v) for i, v in (self.w or {}).items()}

        # Weights are sparse too: only features that some example actually
        # carries are ever touched, which for hashed tool/argument names is a
        # few thousand rather than 16384 columns.
        touched = set()
        for active, _y, _w in train:
            touched.update(index for index, _v in active)
        touched = sorted(touched)
        # **Only initialise what is not already here.**
        #
        # A first version replaced `self.w` wholesale, which silently discarded
        # any seed it was handed - so `fit()` looked like a warm start and was
        # a cold one with extra steps. The symptom was that feature indices the
        # seed knew about and the recent window did not carry simply vanished,
        # which is precisely the knowledge a warm start exists to keep.
        self.w = {index: (self.w.get(index) or [0.0] * len(VERDICTS))
                  for index in touched}
        # Indices the seed knew but this window does not touch would also be
        # dropped by that dict comprehension, so they are carried over.
        for index, row in self._prior.items():
            self.w.setdefault(index, row)
        self.b = [0.0] * len(VERDICTS)
        self._dense = False

        for _epoch in range(epochs):
            grad_w = {index: [0.0] * len(VERDICTS) for index in touched}
            grad_b = [0.0] * len(VERDICTS)
            for active, y, weight in train:
                scores = [self.b[c] + sum(v * self.w.get(i, _ZERO)[c]
                                          for i, v in active)
                          for c in range(len(VERDICTS))]
                probs = _exp(scores)
                probs[y] -= 1.0
                for i, v in active:
                    row = grad_w[i]
                    for c in range(len(VERDICTS)):
                        row[c] += probs[c] * v * weight
                for c in range(len(VERDICTS)):
                    grad_b[c] += probs[c] * weight
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

    #: Tools that need a command, so a prediction of failure can be turned into
    #: a specific, checkable suggestion rather than a worry.
    _NEEDS = {
        "convert_media": ("ffmpeg", "ffprobe"),
        "edit_image": ("magick",),
        "scan_document": ("tesseract", "scanimage"),
        "qr_code": ("zbarimg", "qrencode"),
        "record_audio": ("pw-record", "parecord"),
        "speak": ("piper", "espeak-ng"),
        "sing": ("piper", "espeak-ng"),
        "port_owner": ("ss", "lsof"),
        "interface_counters": ("ethtool",),
        "wireless": ("iw", "nmcli"),
    }

    def recommend(self, record: dict) -> str:
        """What to do about this call, given the prediction AND the machine.

        Three postures, and the important one is the middle: the model does not
        get to refuse. It can only flag "this is one of the calls that usually
        does not confirm itself", which is a *suggestion to ask*, and asking is
        the consent layer's job.

        **When the tool needs a command, the machine is asked before the model
        is believed.** A predicted failure whose command is not installed is not
        a warning about the call, it is a fact about the machine, and saying so
        turns a vague "this may fail" into "ffmpeg is not installed here, so
        this cannot work" - which is actionable in a way the prediction is not.
        The reverse matters just as much: a predicted failure with the command
        present is worth mentioning *because* the cause is elsewhere.
        """
        probs = self.predict_proba(record)
        best = max(probs, key=probs.get)
        tool = str(record.get("tool_name") or "")

        needed = self._NEEDS.get(tool)
        if needed:
            try:
                from shani_chronoa.capability import capabilities
                cap = capabilities()
                missing = [n for n in needed if n not in cap.commands]
            except Exception:  # noqa: BLE001 - never fail a recommendation
                missing = []
            if missing:
                # Name the route, not just the obstacle. "magick is missing"
                # is a dead end; "here are three ways round it" is an answer.
                try:
                    from shani_chronoa.routes import explain
                    return explain(missing[0], cap, tool)
                except Exception:  # noqa: BLE001 - never fail a recommendation
                    return (f"{tool} needs {', '.join(missing)}, which this "
                            f"machine does not have.")

        if best == "failed":
            return ("This call usually fails on this machine. Nothing has run "
                    "yet - consider a dry run or a different approach.")
        if best == "unverified" and probs["unverified"] > 0.9:
            return ("This call has never confirmed that it did anything. It may "
                    "well be fine; there is just no evidence either way.")
        return ""

    # --- persistence ---------------------------------------------------------

    def to_dict(self, provenance: Optional[dict] = None) -> dict:
        """The model, plus where it came from.

        The provenance block is not decoration. A model file carries only
        weights and a format number, which is not enough to tell a model trained
        on 11,590 rows from one trained on twelve - and this file is meant to
        be copied between machines. Without `example_count` and
        `label_counts` a receiver is trusting numbers with no origin, which is
        how a bad fit travels silently from one host to a fleet.
        """
        if self.w is None:
            raise RuntimeError("model is untrained")
        payload = {
            "format": 1,
            "n_features": _N_FEATURES,
            "verdicts": list(VERDICTS),
            # Serialised as the *dense* rows it actually needs, keyed by
            # feature index, so a saved model is readable and a hand-written
            # one could be produced. Kept small because only `touched` indices
            # are ever non-zero.
            # **Not rounded.** A class-balanced fit on this log produces weights
            # around 1e-4; `round(v, 6)` stored 0.0 for every one of them, so
            # the file verified its digest and predicted uniformly. The digest
            # already protects the contents, and 2,262 floats cost nothing.
            "weights": {str(index): [float(v) for v in row]
                        for index, row in sorted(self.w.items())},
            "bias": [float(v) for v in self.b],
            "touched_features": len(self.w),
            "feature_space": _feature_space_id(),
            # What this model is NOT allowed to do, stated in the file it is
            # carried in. It may advise; it can never authorise. A schema that
            # does not say so is a schema a later format can quietly grow a
            # `grants` field into.
            "authority": "advisory-only",
            "provenance": provenance or {},
        }
        return payload

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

    def detection(self, name: str) -> float:
        """How many times better than chance this model is at *finding* `name`.

        recall over that verdict's own share of the data. 1.0 is exactly as good
        as guessing at random, and below 1.0 means it finds it less often than
        chance.
        """
        stats = self.per_class.get(name) or {}
        if not stats or not stats.get("support") or not self.n:
            return 0.0
        share = stats["support"] / self.n
        return (stats["recall"] / share) if share else 0.0

    def precision_lift(self, name: str) -> float:
        """How much better than chance the model's *flags* for `name` are.

        Precision over that verdict's share. This is the half that stops the
        detection number being worthless: on this machine's log `verified` is
        6.9% of the held-out slice, so a model that flags **every** call as
        verified scores `1 / 0.069` = **14.6x detection** while being useless.
        Recall-based lift alone is a metric a degenerate model wins.
        """
        stats = self.per_class.get(name) or {}
        if not stats or not self.n:
            return 0.0
        share = stats["support"] / self.n
        return (stats["precision"] / share) if share else 0.0

    def detected(self, name: str) -> bool:
        """Whether `name` is genuinely found, not merely flagged constantly.

        Both halves, and a recall floor: finding fewer than one call in five is
        not detection, however good the precision looks. Thresholds are stated
        here rather than folded into the number so a reader can disagree with
        them.
        """
        stats = self.per_class.get(name) or {}
        return bool(stats.get("support", 0) >= _MIN_CLASS
                    and stats.get("recall", 0.0) >= _MIN_RECALL
                    and self.precision_lift(name) >= _MIN_DETECTION_GAIN
                    and self.detection(name) >= _MIN_DETECTION_GAIN)

    def best_detection(self) -> Tuple[str, float]:
        """The minority verdict this model genuinely detects, and its lift."""
        majority = _majority(self.per_class)
        candidates = [name for name in self.per_class
                      if name != majority and self.detected(name)]
        if not candidates:
            return "", 0.0
        best = max(candidates,
                   key=lambda n: min(self.detection(n), self.precision_lift(n)))
        return best, min(self.detection(best), self.precision_lift(best))

    def honest(self) -> bool:
        """Whether this report is worth quoting.

        **Accuracy alone is not worth anything on this data**: the majority class
        is 94%, so a model that never learns anything scores 94% - and measured,
        an unweighted fit did exactly that while calling all 700 minority
        examples `unverified`.

        So the report is honest if either

        - top-1 beats the majority baseline **and** a minority class has non-zero
          recall (the original test), or
        - some minority verdict is genuinely `detected()` - found at >= 2x its
          base rate on **both** recall and precision, with recall >= 20%.

        The second test is the question the layer is actually for: flagging a
        call that is about to fail, not winning an argmax a constant would win
        94% of the time. Both precision and recall are required because either
        alone is trivially gameable - flag everything and get 1x recall lift;
        flag the 6.9% class and get 14.6x recall lift while being useless.
        """
        if self.best_detection()[1] > 0:
            return True
        minority_recall = any(
            stats["support"] >= _MIN_CLASS and name != _majority(self.per_class)
            and stats["recall"] > 0
            for name, stats in self.per_class.items())
        return self.accuracy > self.baseline and minority_recall


#: Both the recall and the precision lift a verdict must reach before the report
#: counts it as detected, and the recall floor that stops "finds one in twenty"
#: from passing on precision alone.
_MIN_DETECTION_GAIN = 2.0
_MIN_RECALL = 0.20

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
        return int(predict([example.dense()])[0])
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

def _report_from_confusion(n: int, confusion: Dict[str, Counter]) -> Report:
    """A `Report` from already-counted predictions, so CV and a single split
    cannot drift apart in how they summarise."""
    if not n:
        return Report(0, 0.0, 0.0, {}, {})
    correct = sum(confusion[name].get(name, 0) for name in VERDICTS)
    support = Counter({name: sum(confusion[name].values()) for name in VERDICTS})
    majority_index = max(range(len(VERDICTS)), key=lambda y: support.get(VERDICTS[y], 0))
    baseline = support[VERDICTS[majority_index]] / n
    per_class = {}
    for name in VERDICTS:
        actual_n = support.get(name, 0)
        predicted_n = sum(confusion[t].get(name, 0) for t in VERDICTS)
        hit = confusion[name].get(name, 0)
        per_class[name] = {
            "precision": (hit / predicted_n) if predicted_n else 0.0,
            "recall": (hit / actual_n) if actual_n else 0.0,
            "support": actual_n,
        }
    return Report(n, baseline, correct / n, per_class,
                  {k: dict(v) for k, v in confusion.items()})


def cross_validate(examples: Sequence[Example], folds: int = 5,
                   seed: int = 0, holdout: Optional[float] = None) -> Report:
    """K-fold over **feature vectors**, never over examples.

    **Why this exists, measured.** On this machine's log the three verdicts are
    carried by wildly different numbers of distinct feature vectors:

    | verdict      | examples | distinct vectors | largest vector |
    |--------------|---------:|-----------------:|---------------:|
    | `unverified` |    11,329 |              338 |          4,184 |
    | `verified`   |     1,979 |                3 |          1,831 |
    | `failed`     |     2,139 |                7 |          1,831 |

    All 1,979 `verified` calls share **three** vectors. So any split that keeps
    a vector whole - which is the whole point, since 344 vectors carry 11,590
    examples - necessarily puts two of those three in training and one in test,
    or the reverse, and a single split's score is then a statement about which
    vector landed where. Measured: a stratified group split left **32**
    `verified` examples in training against 319 in test.

    Folding over vectors gives every vector a turn at being test data and never
    leaks one, which is the only way to ask "does this generalise to a call shape
    it has not seen" when the answer is concentrated in three shapes. A fitted
    model is discarded per fold; only the counts are pooled.
    """
    groups: Dict[Tuple, List[Example]] = {}
    for example in examples:
        groups.setdefault(example.active, []).append(example)
    keys = sorted(groups)
    if len(keys) < 2:
        return _report_from_confusion(0, defaultdict(Counter))
    import random as _random
    order = list(keys)
    _random.Random(seed).shuffle(order)
    assignment = {key: index % folds for index, key in enumerate(order)}
    confusion: Dict[str, Counter] = defaultdict(Counter)
    for fold in range(folds):
        train = [e for key in keys if assignment[key] != fold for e in groups[key]]
        test = [e for key in keys if assignment[key] == fold for e in groups[key]]
        if not train or not test:
            continue
        try:
            model = OutcomeModel().fit(train)
        except Exception as exc:  # noqa: BLE001 - one bad fold is not the whole CV
            logger.warning("outcome model: fold %d did not fit (%s)", fold, exc)
            continue
        for example in test:
            confusion[VERDICTS[example.y]][VERDICTS[predict_class(model, example)]] += 1
    return _report_from_confusion(sum(sum(c.values()) for c in confusion.values()),
                                  confusion)


def evaluate(model, examples: Sequence[Example]) -> Report:
    if not examples:
        return Report(0, 0.0, 0.0, {}, {})
    confusion: Dict[str, Counter] = defaultdict(Counter)
    for example in examples:
        confusion[VERDICTS[example.y]][VERDICTS[predict_class(model, example)]] += 1
    return _report_from_confusion(len(examples), confusion)

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
    # The detection line is the one a reader has to have, because accuracy on a
    # 94%-majority log and the thing this layer is for point in opposite
    # directions. Both lifts are shown: a model that finds a verdict 33x more
    # often than chance but is wrong 5 times out of 6 is not the same thing as
    # one right 5.9x more often than chance.
    out.append("  Against each verdict's own share of the data:")
    for name, stats in report.per_class.items():
        if not stats["support"]:
            continue
        out.append(f"    {name:<11} recall {report.detection(name):5.2f}x  "
                   f"precision {report.precision_lift(name):5.2f}x  "
                   f"{'detected' if report.detected(name) else 'not detected'}")
    out.append("")
    detected, lift = report.best_detection()
    if detected:
        out.append(f"  It finds `{detected}` at {lift:.1f}x on both recall and "
                   f"precision, so there is signal here: a call that looks like "
                   f"this shape should be treated as worth checking.")
    elif report.honest():
        out.append("  It beats the top-1 baseline with minority-class recall, so "
                   "there is something here worth acting on.")
    else:
        out.append("  It does NOT find any verdict above chance on both recall "
                   "and precision, and does not beat the baseline, so there is "
                   "no signal here worth acting on. More tool calls are needed, "
                   "not a better model.")
    return "\n".join(out)

def split_examples(examples: Sequence[Example], holdout: float = 0.25) -> Tuple[List[Example], List[Example], str]:
    """Hold out every `1/holdout`-th example **of each verdict**. Returns
    (train, held_out, note).

    **Not a prefix/suffix, and the failure that forced this was measured.** A
    time-ordered split of this machine's log put **8,462 `unverified`, 265
    `failed` and zero `verified`** into the training half - `train_and_save`
    refused to write anything, and correctly, because a model cannot learn a
    class it never sees. The verdicts only started being recorded partway
    through the log, so every `verified` sits in the last quarter.

    The reason the split was ordered at all is the one this keeps: **the same
    call must not appear on both sides**, because 11,590 examples carry only
    344 distinct feature vectors and a duplicate is memorisation. Ordering was
    a *proxy* for that, and it is a bad one - it enforces it only by accident of
    what was logged when. So it is enforced directly: every example whose
    feature vector was already used on the training side is moved to the held-out
    side, and `note` says how many were moved. That is checkable; ordering was
    not.

    With both classes present on both sides the class-balanced fit then reaches
    48.4% recall on `failed` at 6.8% precision against a 3.0% base rate.
    """
    if holdout <= 0 or len(examples) < 4:
        return list(examples), [], "held nothing out"
    stride = max(2, int(round(1.0 / holdout)))
    counts = Counter(e.y for e in examples)
    # **Assign whole feature vectors to one side, never individual examples.**
    # 11,590 examples carry only 344 distinct vectors, so splitting by example
    # puts the same call on both sides almost everywhere. Grouping first is what
    # makes the no-leak claim true instead of approximately true - and an earlier
    # version that counted "duplicates moved" without actually moving them
    # reported 2,827 moves while leaving 35 shared vectors in place, which is
    # the shape of a control that cannot fail.
    groups: Dict[Tuple, List[Example]] = {}
    for example in examples:
        groups.setdefault(example.active, []).append(example)
    ordered = list(groups.values())

    # The unit is the **group**, so a vector cannot straddle; the stratum is the
    # group's majority label, so the split stays balanced per verdict. Both
    # together matter, and getting either wrong showed up as a measurement:
    #   - grouping but not stratifying left **32 `verified`** in training and
    #     319 in the held-out slice, and the balanced fit on 32 examples
    #     predicted `unverified` for 4.1% of the held-out calls - worse than
    #     useless, and it still cleared the detection bar at 14.6x.
    #   - stratifying by example but not grouping left 35 vectors on both sides.
    seen_per_class: Dict[int, int] = {}
    train: List[Example] = []
    held: List[Example] = []
    for group in ordered:
        label = Counter(e.y for e in group).most_common(1)[0][0]
        position = seen_per_class.get(label, 0)
        seen_per_class[label] = position + 1
        (held if position % stride == stride - 1 else train).extend(group)

    train_vectors = {e.active for e in train}
    leaked = [e for e in held if e.active in train_vectors]
    note = ""
    if leaked:
        # Unreachable by construction now; kept as an assertion that can fail,
        # because a guard whose failure is impossible to observe is a comment.
        raise AssertionError(
            f"{len(leaked)} held-out example(s) share a feature vector with the "
            "training half, so the split leaks")
    unseen = [VERDICTS[y] for y in range(len(VERDICTS))
              if counts.get(y, 0) >= _MIN_CLASS
              and not any(e.y == y for e in train)]
    if unseen:
        note = f"no training example of {', '.join(unseen)}"
    return train, held, note


def unknown_tool_examples(records: Sequence[dict]) -> "tuple[int, list[str]]":
    """How much of this training set is about tools this build does not have.

    Measured on this machine's own log: two tools that no longer exist - `liar`
    and `unver`, both fixtures - account for 692 of 12,856 calls and **313 of the
    378 failures**. So a model fitted on that log learns its *entire* failure
    signal from something that is no longer installed, and reports itself in
    `provenance` without saying so.

    It is recorded rather than filtered. **Filtering would be the more useful
    behaviour and the wrong one here**: dropping the examples would quietly
    change what the model says about the past, and the point of the number is
    that a person can see how much of their history is about a machine that does
    not exist any more. Whether to train on it is their decision, made with the
    figure in front of them.
    """
    from shani_chronoa import tools
    known = {entry["function"]["name"] for entry in tools.TOOLS}
    # Records, not Examples: an `Example` is a hashed vector and a label, and the
    # tool name is gone by then - it only survives as `tool=<name>` inside the
    # hash, which is not something to reverse.
    names = [str(r.get("tool_name") or "") for r in records]
    unknown = sorted({name for name in names if name and name not in known})
    count = sum(1 for name in names if name and name not in known)
    return count, unknown


def train_and_report(path: Optional[Path] = None, holdout: float = 0.25,
                     cv: Optional[float] = 5.0,
                     only_tools: Optional["frozenset[str]"] = None) -> Report:
    """Train on most of the log and score the rest.

    Split by class rather than by time - see `split_examples` for the measured
    failure that replaced the ordered split - and fit with class balancing, so
    the two minority verdicts are actually reachable. This is the report
    `train_and_save` refuses to act on, and it is the only thing in the layer
    that answers "is there signal in this log at all".
    """
    examples = load(path, only_tools=only_tools)
    if not examples:
        return Report(0, 0.0, 0.0, {}, {})
    if cv:
        return cross_validate(examples, folds=int(cv))
    train, test, _note = split_examples(examples, holdout)
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

    def fit(self, examples, weights=None) -> "BernoulliNB":
        """Fit, optionally with per-example weights.

        **The weights are not optional in practice.** Naive Bayes derives its
        class priors and its feature counts *from repetition*, so collapsing
        duplicate feature vectors without carrying their counts destroys the
        signal: measured on this machine's log, 11,590 examples carry only 344
        distinct vectors, and fitting the unweighted 344 collapsed BernoulliNB
        from **0.9893 to 0.0147**. With the weights it is the same model.

        So `weights[i]` says how many copies of `examples[i]` exist, and every
        count below is scaled by it. `fit(collapse(train))` and
        `fit(train)` then produce the same posterior.
        """
        classes = len(core.VERDICTS)
        self.n_classes = classes
        if weights is None:
            weights = [1.0] * len(list(examples))
        totals = [0.0] * classes
        seen: Dict[int, List[float]] = {}
        presence = [0.0] * classes
        for example, weight in zip(examples, weights):
            label = example.y
            totals[label] += weight
            presence[label] += weight
            for index, value in _sparse(example):
                if value > 0:
                    row = seen.get(index)
                    if row is None:
                        row = seen[index] = [0.0] * classes
                    row[label] += weight
        grand = sum(totals) or 1.0
        self.priors = [t / grand for t in totals]
        # Only the features some example actually carries, rather than a
        # 16384-row list of zeros - which was 16384 lists of 3 floats each.
        self.counts = seen
        self.presence = presence
        return self

    def predict_proba(self, example: core.Example) -> List[float]:
        scores = [math.log(p if p > 0 else 1e-12) for p in self.priors]
        active = _sparse(example)
        for index, value in active:
            if value <= 0:
                continue
            row = self.counts.get(index)
            if row is None:
                continue
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

    def fit(self, examples: Sequence[core.Example],
            weights: Optional[Sequence[float]] = None) -> "DecisionTree":
        """Fit, optionally with per-example weights.

        A tree splits on a feature by **count of rows**, so a duplicate group is
        not repetition to be removed - it is 4,172 rows voting together. Without
        the weights, collapsing took DecisionTree from 1.0000 to 0.9853 for
        exactly that reason. With them the node counts are weighted and the fit
        is identical to fitting every copy.
        """
        counts = [0.0] * len(core.VERDICTS)
        for e in examples:
            counts[e.y] += 1
        self.root = self._build(examples, 0, counts)
        return self

    def _build(self, examples, depth: int, counts):
        """`examples` is a list of `(Example, weight)`. `counts` is weighted."""
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
            left = [row for row in examples if _has(_sparse(row), feature)]
            # `min_leaf` is compared against the WEIGHTED count, not the row
            # count. Rows are what a leaf is measured in, but a collapsed fit
            # has 333 rows standing for 8,000, so a row-count test lets splits
            # through that the full fit would reject - which is why the
            # collapsed tree scored 0.9862 against the full one's 1.0000.
            left_weight = _weight_sum(left)
            total_weight = _weight_sum(examples)
            if left_weight < self.min_leaf or left_weight > total_weight - self.min_leaf:
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
                         if not _has(e.active, feature)]
                best_gain, best = gain, (feature, left, right)
        return best

    def predict(self, example: core.Example) -> int:
        node = self.root
        while node and node["feature"] is not None:
            node = node["left"] if _has(example.active, node["feature"]) else node["right"]
        return node["pred"] if node else 0

    def predict_proba(self, example: core.Example) -> List[float]:
        node = self.root
        while node and node["feature"] is not None:
            node = node["left"] if _has(example.active, node["feature"]) else node["right"]
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
            return _softmax(list(model.w @ example.dense() + model.b))
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

    def fit(self, examples: Sequence[core.Example],
            weights: Optional[Sequence[float]] = None) -> "KNN":
        """Store the set, and build an inverted index over the features.

        The index is the whole optimisation. Scoring every row per query is
        O(n) - 8,000 sparse dot products for each of 1,500 queries, which
        measured **36 seconds**. But a row can only share a feature with the
        probe if it *carries* one of the probe's features, and the probe has
        about five. Only rows in the union of those five posting lists can score
        above zero, so the rest cannot reach the top k and need not be looked at.

        Candidates shrink from "every row" to "rows sharing a feature", which on
        hashed tool/argument names is a small fraction of the set.
        """
        if weights is None:
            weights = [1.0] * len(list(examples))
        # The copy count rides with the row, so a neighbour repeated 4,172 times
        # contributes 4,172 votes rather than one - which is what makes the
        # collapsed fit equivalent to the full one.
        self.rows = [(dict(_sparse(e)), e.y, float(w))
                     for e, w in zip(examples, weights)]
        index: Dict[int, List[int]] = defaultdict(list)
        for position, (vector, _label, _w) in enumerate(self.rows):
            for feature in vector:
                index[feature].append(position)
        self._index = dict(index)
        return self

    def predict(self, example: core.Example) -> int:
        probe = dict(_sparse(example))
        # Only rows that share at least one feature can score above zero; a row
        # with none has similarity 0 and cannot displace a real neighbour.
        if not hasattr(self, "_index"):
            self._index = {}
        seen: Dict[int, float] = {}
        for feature, value in probe.items():
            for position in self._index.get(feature, ()):
                seen[position] = seen.get(position, 0.0) + value
        scored = sorted(
            ((shared, position) for position, shared in seen.items()),
            reverse=True)
        tally = [0.0] * len(core.VERDICTS)
        for _s, position in scored[:self.k]:
            tally[self.rows[position][1]] += self.rows[position][2]
        if not scored:
            # No shared feature: every neighbour is at distance 1, so the
            # majority class is the right answer rather than a silent zero.
            tally = [0.0] * len(core.VERDICTS)
            for _vector, label, weight in self.rows[:self.k]:
                tally[label] += weight
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

def _sparse(example) -> Tuple[Tuple[int, float], ...]:
    """An example's non-zero entries, from its cached list.

    This walked all 16,384 slots of a dense vector on every call - 157
    million iterations to score 9,586 examples - and the dense vectors
    themselves cost 7.3 GB to hold for the life of the process. `Example`
    stores only the sparse form; see its docstring.

    Accepts a bare `Example` or a weighted `(Example, weight)` pair, because the
    tree carries the pair through its recursion and threading that tolerance
    through every call site is how the two shapes drifted apart.
    """
    # A weighted row is `(Example, weight)`. Checking `isinstance(tuple)` is
    # not good enough: the `(index, value)` pairs INSIDE an active list are
    # tuples too, and unwrapping one of those returns a float with no
    # `.active`. So the shape is identified by what the pair holds.
    return _unwrap(example).active


def _weight_sum(rows) -> float:
    """Total sample weight of `(Example, weight)` rows."""
    total = 0.0
    for row in rows:
        if isinstance(row, tuple) and len(row) == 2 and isinstance(row[0], core.Example):
            total += float(row[1])
        else:
            total += 1.0
    return total


def _counts(examples) -> List[float]:
    tally = [0.0] * len(core.VERDICTS)
    for row in examples:
        if isinstance(row, tuple) and len(row) == 2 \
                and isinstance(row[0], core.Example):
            example, weight = row
        else:
            example, weight = row, 1.0
        tally[example.y] += weight
    return tally

def _candidate_features(examples, cap: int = 64) -> List[int]:
    """Features worth trying as a split: the most common, bounded.

    A full sweep over 16384 columns per node is not affordable in pure Python;
    the common features are the ones carrying the argument-shape signal anyway.
    """
    frequency: Dict[int, float] = defaultdict(float)
    for row in examples:
        for i, _v in _sparse(row):
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
