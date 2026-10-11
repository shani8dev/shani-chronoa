"""Mutation set for the sparse feature vector.

`vectorise()` allocated a 16,384-wide list, filled it, normalised it into a
*second* such list, and every caller then walked all 16,384 slots to find the
handful that were set. Measured on this machine's own log: 19,103 examples took
**54.7 s** that way, and `predict_proba()` - the live inference path, called on
every tool call - paid 1.43 ms for the same shape.

`active_vector()` produces the sparse answer directly: 0.18 s, 297x faster,
byte-identical. This is `airllm/chunked_ce.py`'s discipline - never materialise
the wide intermediate when the arithmetic only ever touches the sparse form.

These mutations are the three ways a sparse rewrite silently stops matching the
dense route it replaced: counting occurrences instead of distinct indices (a
collision bug the naive inputs never hit), losing the normalisation, and losing
the deterministic order that keeps the `Example` cache hitting.
"""

TEST_FILE = "tests/test_learning_chain.py"

_ML = "usr/lib/shani-chronoa/shani_chronoa/learning.py"

MUTATIONS = [
    {
        "file": _ML,
        "old": "    seen = {_index(name) for name in names}\n    norm = math.sqrt(float(len(seen)))\n",
        "new": "    seen = {_index(name) for name in names}\n    norm = math.sqrt(float(len(names)))\n",
        # `len(names)` is occurrences, not distinct indices. With four identical
        # feature names the norm becomes 2.0 instead of 1.0, so every value is
        # halved - and the outputs still match `_active(vectorise())` for every
        # input that has no collisions, which is why the collision test exists.
        "label": "the norm counts occurrences instead of distinct columns",
        # `test_the_norm_is_the_square_root_of_distinct_columns` is NOT a witness:
        # its input is ["a","b","c"], where occurrences and distinct indices are
        # the same number, so the mutation is invisible to it. A witness that
        # passes for both the fixed and the broken code is the "a control that
        # cannot fail" trap. The collision test is the one that discriminates.
        "expect": ["test_identical_to_the_dense_route_on_hand_built_inputs",
                   "test_identical_including_hash_collisions"],
    },
    {
        "file": _ML,
        "old": "    if not norm:\n        return []\n    return [(i, 1.0 / norm) for i in sorted(seen)]\n",
        "new": "    if not norm:\n        return []\n    return [(i, 1.0) for i in sorted(seen)]\n",
        # Dropping the normalisation. A sparse vector of ones is not L2-normalised,
        # so the logistic scores change - and `predict_proba` is the live path, so
        # every advice the outcome model gives would change with it.
        "label": "the normalisation is dropped",
        "expect": ["test_identical_to_the_dense_route_on_hand_built_inputs",
                   "test_the_norm_is_the_square_root_of_distinct_columns"],
    },
    {
        "file": _ML,
        "old": "    return [(i, 1.0 / norm) for i in sorted(seen)]\n",
        "new": "    return [(i, 1.0 / norm) for i in seen]\n",
        # The index order becomes the set's iteration order. Same values, so every
        # value-level assertion passes - but `collapse()` keys a dict on the tuple,
        # so a repeated identical pattern stops colliding and the "11,590 examples
        # carry only 344 distinct vectors" measurement this whole layer rests on
        # silently breaks.
        "label": "the deterministic index order is lost",
        "expect": ["test_the_order_is_by_index_so_the_output_is_deterministic",
                   "test_identical_to_the_dense_route_on_hand_built_inputs"],
    },
]
