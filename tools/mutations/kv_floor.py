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
        "old": 'KV_CACHE_FLOOR: str = "q5_0"\n',
        "new": 'KV_CACHE_FLOOR: str = "q4_0"\n',
        # The floor drops to the type that measured +6.1%. The clamp then lets a
        # caller's longer ladder buy `q4_0` back, which is the whole thing the
        # floor exists to prevent.
        "label": "the floor drops to the type that measured 6.1% worse",
        "expect": ["test_the_floor_is_the_cheapest_type_that_measured_free",
                   "test_the_ladder_stops_at_the_floor"],
    },
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
    {
        "file": _CTX,
        "old": 'CACHE_TYPE_LADDER: "tuple[str, ...]" = ("f16", "q8_0", "q5_0")',
        "new": 'CACHE_TYPE_LADDER: "tuple[str, ...]" = ("f16", "q8_0", "q5_0", "q4_0")',
        # The ladder reaches below the floor while the constant still says q5_0,
        # so the two disagree - and the ladder is what the walk uses, so the
        # measured 6.1% is paid.
        "label": "the ladder reaches past the floor it declares",
        "expect": ["test_the_ladder_stops_at_the_floor"],
    },
]
