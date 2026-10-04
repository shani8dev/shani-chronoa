"""Feature space, outcome model and the learning loop.

Everything here now lives in `learning.py`, which is the single place this
package learns anything. This module remains as a re-export so an existing
import path keeps resolving, and so the module a caller already had still
means what it meant.
"""

from shani_chronoa.learning import (  # noqa: F401
    Example, OutcomeModel, Prediction, Report, VERDICTS,
    evaluate, features, load, predict_class as _predict_class,
    record_prediction, read_predictions, render_outcome as render,
    score_predictions, train_and_report, vectorise,
)
from shani_chronoa.learning import (  # noqa: F401
    _MIN_CLASS, _N_FEATURES, _ZERO, _log_path as _log_path,
    _entries as _entries,
)
