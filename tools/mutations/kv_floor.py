"""Mutation set for the KV-cache quality floor.

`KV_CACHE_FLOOR` is measured, not preferred. On 2026-10-12 with this repo's own
`llama-perplexity` path and its fixed corpus, on SmolLM2-135M-Q4_K_M, both cache
types set, two identical runs each:

    f16  59.1719     q8_0  59.1499   (-0.022)
    q5_0 59.2380     q4_0  62.7689   (+3.597, +6.1%)

So `q8_0` and `q5_0` are free - inside the run-to-run noise - and `q4_0` costs
6.1%. PersonaPlex's RVQ has exactly this control on its compression dial:
`set_num_codebooks` asserts `n >= self.n_q_semantic`, so acoustic fidelity can be
traded away and meaning never can.

These mutations are the floor being removed from the ladder, silenced in the walk,
and the ladder straying back down to `q4_0`.
"""

TEST_FILE = "tests/test_ctxfit.py"

_CTX = "usr/lib/shani-chronoa/shani_chronoa/ctxfit.py"

MUTATIONS = [
    
    {
        "file": _CTX,
        "old": '    ladder = tuple(ladder[:ladder.index(KV_CACHE_FLOOR) + 1]) \\\n        if KV_CACHE_FLOOR in ladder else ladder\n',
        "new": "",
        # The clamp gone while the ladder is still built to the floor - so the
        # default call is unchanged and only a caller passing an extended ladder
        # can buy past it. That is the exact attack the clamp exists to stop, and
        # it is silent: the default ladder never shows the difference.
        "label": "the clamp on the walked ladder is removed",
        "expect": ["test_a_longer_ladder_cannot_buy_past_the_floor"],
    },
    
]

# --- the second model's measurement, and the class that pins both ------------

SECOND_MODEL = {
    "file": _CTX,
    "old": '    "qwen3-8b-q4_k_m": {"f16": 0.0, "q8_0": 0.32, "q5_0": 1.35, "q4_0": 5.81},\n',
    "new": '    "qwen3-8b-q4_k_m": {"f16": 0.0, "q8_0": 0.32, "q5_0": 1.35, "q4_0": 0.9},\n',
    # The 8B's q4_0 figure dropped below the cliff the class asserts. That is the
    # one number the whole floor rests on, so a measurement that contradicts it
    # has to be caught rather than quietly weakening the cliff.
    "label": "the 8B stops showing q4_0 as the cliff",
    "expect": ["test_q4_0_is_the_cliff_on_both_models"],
}

SECOND_MODEL_ORDER = {
    "file": _CTX,
    "old": '    "qwen3-8b-q4_k_m": {"f16": 0.0, "q8_0": 0.32, "q5_0": 1.35, "q4_0": 5.81},\n',
    "new": '    "qwen3-8b-q4_k_m": {"f16": 0.0, "q8_0": 3.20, "q5_0": 1.35, "q4_0": 5.81},\n',
    # q8_0 made worse than q5_0 - i.e. a *lighter* compression costing more than a
    # heavier one, which the ladder's whole walk assumes cannot happen.
    "label": "a lighter compression is recorded as costing more",
    "expect": ["test_the_ordering_is_the_same_on_both_models"],
}

MUTATIONS = MUTATIONS + [SECOND_MODEL, SECOND_MODEL_ORDER]

# --- the third model, and the floor it moved ------------------------------------

THIRD_MODEL = {
    "file": _CTX,
    "old": '    "qwen3-0.6b-q8_0": {"f16": 0.0, "q8_0": 0.40, "q5_0": 8.53, "q4_0": 457.58},\n',
    "new": "",
    # The third measurement removed. The floor then rests on two models again -
    # and the two that looked like evidence put it at q5_0, so this mutation
    # silently revives a floor the third model overturned.
    "label": "the third model's measurement is deleted",
    "expect": ["test_the_floor_is_the_only_step_cheap_on_every_model",
               "test_the_ordering_is_the_same_on_both_models"],
}

BACK_TO_Q5 = {
    "file": _CTX,
    "old": 'KV_CACHE_FLOOR: str = "q8_0"',
    "new": 'KV_CACHE_FLOOR: str = "q5_0"',
    # The floor this repo held before the third model was measured. It is not a
    # hypothetical regression: it is what the code said until 2026-10-12.
    "label": "the floor reverts to q5_0",
    "expect": ["test_the_floor_is_the_only_step_cheap_on_every_model",
               "test_the_floor_is_the_cheapest_type_that_measured_free"],
}

Q5_BACK_ON_LADDER = {
    "file": _CTX,
    "old": 'CACHE_TYPE_LADDER: "tuple[str, ...]" = ("f16", "q8_0")',
    "new": 'CACHE_TYPE_LADDER: "tuple[str, ...]" = ("f16", "q8_0", "q5_0")',
    # The ladder reaches past the floor, so the +8.53% step becomes reachable
    # again while the constant still says the floor is q8_0 - the constant and
    # the walk disagreeing.
    "label": "the ladder reaches past the q8_0 floor it declares",
    "expect": ["test_the_ladder_stops_at_the_floor"],
}

# Two earlier entries tested the floor while it was q5_0; their anchors
# moved when it did, and BACK_TO_Q5 covers that same regression with an
# anchor that is live.
MUTATIONS = MUTATIONS + [SECOND_MODEL, SECOND_MODEL_ORDER, THIRD_MODEL,
                      BACK_TO_Q5, Q5_BACK_ON_LADDER]
