"""Mutation set for the cross-check between the two KV-width routes.

`kv_width_from_shapes` was written as "a second, independent derivation of the
number `kv_bytes_per_token` computes from metadata", documented and tested - and
with **no caller in `usr/` at all**. A cross-check nobody performs is not a
cross-check: the module could have been misreading every convention in the GGUF
header for its whole life and nothing would have noticed, because the two routes
never met.

`ctxfit.kv_width_disagreement*` is the meeting, and `local_llm.context_fit()` is
the caller that logs what it says. These mutations are the check firing never,
firing on disagreement as if it were agreement, losing either route, and the
wiring dropping the result on the floor.

Run with `CHRONOA_KV_PARITY_MODEL=<a .gguf>` to exercise the real-file route
too; without it those cases skip and say so.
"""

TEST_FILE = "tests/test_kv_parity_is_wired.py"

_CTX = "usr/lib/shani-chronoa/shani_chronoa/ctxfit.py"
_LLM = "usr/lib/shani-chronoa/shani_chronoa/local_llm.py"

MUTATIONS = [
    {
        "file": _CTX,
        "old": "    if read == truth:\n        return None\n",
        "new": "    if True:\n        return None\n",
        # The comparison always answering "agree". This is the original dead
        # state reached through the code rather than through an absent caller -
        # and it is worse than no check, because it reads as one.
        "label": "the comparison always reports agreement",
        "expect": ["test_a_width_that_disagrees_is_named"],
    },
    {
        "file": _CTX,
        "old": "def metadata_kv_width(model_info: dict) -> Optional[int]:\n",
        "new": "def metadata_kv_width(model_info: dict) -> Optional[int]:\n    return None\n",
        # Losing the metadata route. Every comparison then returns None, which a
        # caller may read as agreement - the "a check that cannot fail" trap.
        "label": "the metadata route is gone, so nothing can be compared",
        "expect": ["test_a_width_that_agrees_says_nothing",
                   "test_a_width_that_disagrees_is_named"],
    },
    {
        "file": _CTX,
        "old": "    read = kv_width_from_shapes(shapes or ())\n",
        "new": "    read = 0\n",
        # Losing the tensor route. `kv_width_from_shapes` returns 0 for "nothing
        # to read", and `kv_width_disagreement_from_shapes` treats 0 as "cannot
        # compare" - so the disagreement is silently unreportable.
        "label": "the tensor route is not read at all",
        "expect": ["test_a_width_that_disagrees_is_named"],
    },
    {
        "file": _LLM,
        "old": "                disagreement = ctxfit.kv_width_disagreement(current_link(), info)\n",
        "new": "                disagreement = None\n",
        # The wiring drops the result on the floor. The check exists, runs, and
        # its answer goes nowhere - which is the state the whole fix came from,
        # restored at the call site instead of at the definition.
        "label": "the caller discards what the check said",
        "expect": ["test_a_disagreement_reaches_the_log",
                   "test_it_runs_on_a_machine_with_no_vram"],
    },
]
