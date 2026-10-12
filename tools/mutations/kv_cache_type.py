"""Mutation set for the KV-cache-type lever in `ctxfit`.

`kv_bytes_per_token` hardcoded `* 2` with a comment saying it assumed llama.cpp's
f16 default, and `context_that_fits()` walked a ladder of *contexts* with the KV
cost fixed - so the only way to fit more context was to shrink it.

unsloth treats the same quantity as a budget variable:
`min(_planned_main_cache_types(...), key=_kv_bytes_per_elem)`, with an exact
bytes-per-element table for every llama.cpp cache type. The KV cache is one of
only two things a context decision can trade away - shrink the context, or
compress the cache the context is made of - and Chronoa had never used the second.

These mutations are the two things that must not regress: the default staying
exactly what it was, and the lever being *least-lossy-that-fits* rather than
always-cheapest.
"""

TEST_FILE = "tests/test_model_requirements.py"

_CTX = "usr/lib/shani-chronoa/shani_chronoa/ctxfit.py"

MUTATIONS = [
    {
        "file": _CTX,
        "old": '    return KV_CACHE_BYTES_PER_ELEM.get(\n        (cache_type or "f16").strip().lower(), 2.0)\n',
        "new": '    return KV_CACHE_BYTES_PER_ELEM.get(\n        (cache_type or "f16").strip().lower(), 0.5625)\n',
        # The default for an *unknown* type becomes the cheapest. That is the
        # unsafe direction: an unrecognised name would promise a context the
        # VRAM cannot hold, and the whole point of the fallback is to over-count.
        "label": "an unknown cache type is charged the cheapest rate",
        "expect": ["test_an_unknown_type_costs_the_f16_default"],
    },
    {
        "file": _CTX,
        # Re-anchored 2026-12: the floor clamp was inserted between
        # `return ladder[0]` and the walk, so the old anchor was no longer
        # contiguous and reported as rotted. It is now the **walk alone**,
        # which is narrower than what it replaced - the clamp is left
        # intact, so this still removes one defect rather than two.
        "old": '    f16 = cache_bytes_per_elem(ladder[0])\n    for cache_type in ladder:\n        scaled = int(per_token_at_f16 * cache_bytes_per_elem(cache_type) / f16)\n        if estimate_fit(weights_bytes, context, total_bytes=total_bytes,\n                        per_token=scaled).fits:\n            return cache_type\n    return ladder[-1]\n',
        "new": '    return ladder[0]\n',
        # **The walk never runs.** The least-lossy-that-fits ordering is the whole
        # design and the ladder is best-first, so returning `ladder[0]`
        # unconditionally promises a context that does not fit in f16 in f16.
        #
        # It used to be the other end of the ladder - always the cheapest,
        # ignoring whether a better type already fits - and that is **no longer
        # expressible**, which is why this was re-anchored rather than repaired.
        # `CACHE_TYPE_LADDER` is `("f16", "q8_0")` now, so `ladder[-1]` *is* the
        # floor, and the floor is already what the function returns when nothing
        # fits: a mutation that returned it was a restatement of the no-op path
        # and a SURVIVOR against both witnesses it named. A two-entry ladder has
        # no "cheapest" distinguishable from "nothing fitted".
        "label": "the lever never steps down from the first type, ignoring whether a cheaper one is needed",
        "expect": ["test_the_least_lossy_type_that_fits_is_the_one_chosen",
                   "test_when_nothing_fits_the_cheapest_tried_is_returned"],
    },
    {
        "file": _CTX,
        "old": "    return int(total * cache_bytes_per_elem(cache_type))\n",
        "new": "    return int(total * 2)\n",
        # The cache type is accepted and ignored - which is worse than not having
        # the parameter, because the caller now believes it has a lever it does
        # not have.
        "label": "the cache type is accepted and ignored (f16 always)",
        "expect": ["test_quantising_the_cache_frees_real_room",
                   "test_the_least_lossy_type_that_fits_is_the_one_chosen",
                   "test_the_lever_stays_down_when_f16_fits"],
    },
]
