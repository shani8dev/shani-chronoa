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
        "old": '    if not per_token_at_f16:\n        return ladder[0]\n    f16 = cache_bytes_per_elem(ladder[0])\n    for cache_type in ladder:\n        scaled = int(per_token_at_f16 * cache_bytes_per_elem(cache_type) / f16)\n        if estimate_fit(weights_bytes, context, total_bytes=total_bytes,\n                        per_token=scaled).fits:\n            return cache_type\n    return ladder[-1]\n',
        "new": '    if not per_token_at_f16:\n        return ladder[0]\n    return ladder[-1]\n',
        # Always the cheapest, ignoring whether a better type already fits. The
        # least-lossy-that-fits ordering is the whole design: `q8_0` at 0.5625 is
        # near-lossless and is preferred wherever it fits, so reaching for `q4_0`
        # by default trades quality it did not need to trade.
        "label": "the lever always picks the cheapest type, ignoring the ordering",
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
