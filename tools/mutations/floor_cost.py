"""What the `q8_0` floor costs, and the two ways of getting that wrong.

The floor was justified on quality: `q5_0` measured **+8.53%** perplexity on
Qwen3-0.6B, worse than `q4_0` costs on either other model, so `q8_0` is the only
step cheap on all three. The other side of that trade - what VRAM headroom the
floor gives up by excluding `q5_0` - was unquantified until the table in
`TestTheFloorsCost` was measured:

    vram     f16    q8_0    q5_0    q4_0
    10G     32768   32768   32768   32768
     9G     16384   32768   32768   32768
     8G     16384   32768   32768   32768
     7G      8192   16384   32768   32768
     6G      4096    8192   16384   16384
     5G      2048    2048    2048    2048

These two mutations break the measurement in opposite directions. The docstring
first written here claimed they were "covered by *different* tests - which is the
point of having two", and the mutation run disproved that on the first pass: the
floor-step mutation fails **both** cost tests, so only the `f16`-reversion is the
second test's alone. Corrected here rather than left standing.
"""

TEST_FILE = "tests/test_ctxfit.py"

_CTX = "usr/lib/shani-chronoa/shani_chronoa/ctxfit.py"

MUTATIONS = [
    {
        "file": _CTX,
        "old": '    "q8_0": 34 / 32,',
        "new": '    "q8_0": 1.9,',
        # The floor step stops compressing. At 34/32 bytes per element q8_0 saves
        # 1.88x over f16; at 1.9 it saves 1.05x, so the floor buys a sliver
        # instead of the doubling it exists for. The table still orders
        # correctly, the ladder still stops at q8_0, and the quality tests are
        # untouched - only the cost test notices.
        "label": "the floor step stops buying the doubling",
        # Three tests, because the first version of this entry was caught by a
        # test it did not name and reported as a survivor. The one that was
        # missing, `..._actually_compresses`, is the assertion whose absence made
        # `..._keeps_the_doubling` vacuous: with q8_0 at 1.9 the floor step equals
        # f16 at every band, the "f16 fits, nothing to recover" guard skips all
        # four rows, and that test passed having asserted nothing.
        "expect": ["test_the_floor_step_actually_compresses",
                   "test_the_floor_step_keeps_the_doubling_where_f16_fails",
                   "test_the_excluded_steps_only_buy_where_the_floor_already_doubled"],
    },
    {
        "file": _CTX,
        "old": 'KV_CACHE_FLOOR: str = "q8_0"',
        "new": 'KV_CACHE_FLOOR: str = "f16"',
        # The floor reverts to "no compression at all" - the ladder offers
        # nothing. This is the regression the *second* test exists for and the
        # first cannot see: with the floor at f16, `at_floor == f16` everywhere,
        # so the doubling loop skips every row and passes while the ladder is
        # empty. This one *is* the second test's alone, which is the asymmetry
        # the first entry's comment had backwards.
        "label": "the floor reverts to f16, i.e. no compression at all",
        "expect": ["test_the_excluded_steps_only_buy_where_the_floor_already_doubled"],
    },
    {
        "file": _CTX,
        "old": '    "q8_0": 34 / 32,',
        "new": '    "q8_0": 1.5,',
        # Still declares itself a compression step, so the ladder looks right and
        # the ordering test passes - it just compresses 1.33x instead of 1.88x, and
        # quietly halves the headroom the floor is supposed to buy back.
        "label": "the floor step compresses only a third of the way",
        "expect": ["test_the_floor_step_actually_compresses"],
    },
]
