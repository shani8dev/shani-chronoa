"""The KV dial's speed axis, and the two claims its noise permits.

Recorded as unmeasured until 2026-10-12, because `llama-bench` refuses a quantized
cache type on this build. It was measurable all along from `llama-server`, and the
recorded expectation - a quantized cache "pays a dequantization cost" - came out
backwards: q8_0 is +31% and q4_0 +48% at a ~3213-token cache, and the advantage
**vanishes** at a ~220-token cache, which is what makes it a cache-traffic effect
rather than a property of the quantized kernel.

These mutations break the two claims worth pinning, and both expect lists are the
property rather than the percentage - the within-type spread was 6-15%, so a
pinned number would be unreproducible.
"""

TEST_FILE = "tests/test_ctxfit.py"

_CTX = "usr/lib/shani-chronoa/shani_chronoa/ctxfit.py"

MUTATIONS = [
    {
        "file": _CTX,
        "old": '    "q8_0": {"long": 31.2, "short": -0.09},',
        "new": '    "q8_0": {"long": -12.0, "short": -0.09},',
        # The ladder's only compressed rung made slower than f16. That is the
        # regression the property exists to catch: the dial would then be buying
        # VRAM headroom with the decode rate `min_tps` gates the tiers on.
        "label": "the ladder's rung is made slower than f16",
        "expect": ["test_the_rung_the_ladder_offers_is_not_slower_than_f16"],
    },
    {
        "file": _CTX,
        "old": '    "q8_0": {"long": 31.2, "short": -0.09},',
        "new": '    "q8_0": {"long": 31.2, "short": 25.0},',
        # The effect no longer vanishes at a short cache, so it stops being a
        # traffic effect and the table reads as "quantized caches are faster" -
        # a different and much stronger claim. The control is what decides which
        # reading is true, so it has to be able to fail.
        "label": "the effect no longer vanishes when the cache is small",
        "expect": ["test_the_effect_vanishes_when_the_cache_is_small"],
    },
    {
        "file": _CTX,
        "old": '    "q4_0": {"long": 47.9, "short": -1.97},',
        "new": '    "q5_0": {"long": 47.9, "short": -1.97},',
        # q4_0's spread entry gone, so the type is keyed q5_0 twice: q4_0 then
        # has a speed figure and no spread to judge it by, and q5_0's real
        # spread is silently overwritten. A figure nobody can judge is the
        # invariant this table rests on.
        "label": "a measured type loses its spread entry",
        "expect": ["test_every_measured_type_carries_its_own_spread"],
    },
    {
        "file": _CTX,
        "old": '    "f16": {"long": 0.0, "short": 0.0},',
        "new": '    "f16": {"long": 4.0, "short": 0.0},',
        # The baseline stops being zero, so every other figure in the table is
        # relative to something other than f16 and the deltas are wrong by a
        # constant nobody recorded.
        "label": "the f16 baseline stops being zero",
        "expect": ["test_every_measured_type_carries_its_own_spread"],
    },
]
