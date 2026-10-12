"""The fourth model, and the shape of the cost it settled.

`q5_0`'s cost on three models read as a **size trend** (0.11% at 135M, 1.35% at
8B), and the 0.6B at +8.53% had already broken monotonicity without saying what
the cost followed. Qwen3-1.7B-Q4_K_M settles it by holding the weight
quantization constant against the 8B: both `Q4_K_M`, **6.3x apart** in `q5_0`
cost. So the cost is per-model and bimodal, not derived from size or from the
file's quantization - which is what makes the floor a per-model question.

These mutations are the fourth record being removed, and the two claims it
supports being weakened.
"""

TEST_FILE = "tests/test_ctxfit.py"

_CTX = "usr/lib/shani-chronoa/shani_chronoa/ctxfit.py"

MUTATIONS = [
    {
        "file": _CTX,
        "old": '    "qwen3-1.7b-q4_k_m": {"f16": 0.0, "q8_0": 0.30, "q5_0": 8.54, "q4_0": 38.9},\n',
        "new": "",
        # The fourth record removed. The count guard is what catches it: with
        # three models the suite is green *and* the bimodality reading is back to
        # a size trend, so this is the silent failure mode this file records most
        # often. The count is four for exactly this reason.
        "label": "the fourth model's measurement is deleted",
        "expect": ["test_the_ordering_holds_on_every_model"],
    },
    {
        "file": _CTX,
        "old": 'KV_CACHE_FLOOR: str = "q8_0"',
        "new": 'KV_CACHE_FLOOR: str = "q5_0"',
        # The floor reverts to the two-model value. The 1.7B is the *default*
        # tier (current.gguf), so this is the regression that would apply an
        # 8.54% cost to the model a default install runs.
        "label": "the floor reverts to q5_0, which costs 8.54% on the default tier",
        "expect": ["test_the_floor_still_holds_with_the_fourth_model",
                   "test_the_same_weight_quantization_costs_six_times_apart"],
    },
    {
        "file": _CTX,
        "old": '"qwen3-1.7b-q4_k_m": {"f16": 0.0, "q8_0": 0.30, "q5_0": 8.54, "q4_0": 38.9}',
        "new": '"qwen3-1.7b-q4_k_m": {"f16": 0.0, "q8_0": 0.30, "q5_0": 1.4, "q4_0": 38.9}',
        # The 1.7B's q5_0 dropped to the 8B's figure, so the two models agree and
        # the "6.3x apart" reading disappears. The bimodality claim is what this
        # pins: two models near zero and two near 8.5%, which no size trend
        # predicts.
        "label": "the 1.7B's q5_0 cost is made to agree with the 8B",
        "expect": ["test_the_same_weight_quantization_costs_six_times_apart"],
    },
    {
        "file": _CTX,
        "old": '    "smolvlm2-500m-video-instruct-q8_0": {"f16": 0.0, "q8_0": 0.13, "q5_0": 0.74, "q4_0": 2.16},\n',
        "new": "",
        # The fifth record removed. With four models the suite is green *and*
        # weight quantization looks like it might predict the cost - Q4_K_M would
        # be cheap, cheap, expensive, and Q8_0 just expensive - which is a reading
        # the fifth model refutes. Only the count catches it.
        "label": "the fifth model's measurement is deleted",
        "expect": ["test_the_ordering_holds_on_every_model"],
    },
    {
        "file": _CTX,
        "old": '"smolvlm2-500m-video-instruct-q8_0": {"f16": 0.0, "q8_0": 0.13, "q5_0": 0.74, "q4_0": 2.16}',
        "new": '"smolvlm2-500m-video-instruct-q8_0": {"f16": 0.0, "q8_0": 0.13, "q5_0": 8.5, "q4_0": 2.16}',
        # The 500M moved to the expensive side. Then Q8_0 is expensive in both its
        # models, so weight quantization appears to predict the cost again - which
        # is the exact reading the fifth model refutes, and the reason a model
        # nobody measured is a model whose side of the split is unknown.
        "label": "the fifth model is moved to the expensive side",
        "expect": ["test_each_weight_quantization_appears_on_both_sides"],
    },
]
