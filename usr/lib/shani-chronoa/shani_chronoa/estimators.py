"""Classifiers, regressors, clustering and outlier scoring.

Merged into `learning.py`; kept as a re-export so existing imports work.
"""

from shani_chronoa.learning import (  # noqa: F401
    AnomalyDetector, BernoulliNB, DecisionTree, EMA, KNN, KMeans,
    LinearRegression, Ridge, SGDClassifier, Standing,
    CANDIDATES, choose as choose, compare as compare,
    render_estimators as render,
)
