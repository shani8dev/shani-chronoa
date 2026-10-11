"""Mutation set for the residency-regime reason in `requirements_met`.

Before this, a machine short on RAM was told *"20 GB of RAM (this machine has
6.0)"*, which reads as "cannot run". airllm's thesis is that this is the wrong
reading - `air_llm/airllm/airllm_base.py`: *"AirLLM only ever keeps one layer on
the GPU at a time, so the VRAM you need depends on the model's layer size - not
its total size"*. Chronoa's own `bytes_per_token()` already computes the
comparable number, and the tier where the two diverge most is `qwen3-30b-a3b`:
`ram=20` against `active_bytes` of 2.05 GiB, so a 6 GB box is short of the
*resident* figure while its per-token cost fits.

The sentence now names what the RAM figure is for. It deliberately does not claim
a decode rate - nobody has measured a >RAM model on a small box, and an invented
tok/s is the confident wrong answer this repo keeps recording.

These mutations are the clause firing always, never, and quietly inventing the
speed it refuses to claim.
"""

TEST_FILE = "tests/test_model_requirements.py"

_LLM = "usr/lib/shani-chronoa/shani_chronoa/local_llm.py"

MUTATIONS = [
    {
        "file": _LLM,
        "old": "        if active_gib is not None and active_gib <= ram:\n",
        "new": "        if active_gib is not None:\n",
        # The clause firing on every tier, including one whose per-token cost
        # genuinely does not fit. That is the softer-than-the-truth failure: it
        # tells a 6 GB box a 9 GiB-per-token model is "not a cap on whether it
        # runs", when each token reads more RAM than the machine has.
        "label": "the residency clause fires even when the per-token cost does not fit",
        "expect": ["test_a_tier_whose_per_token_cost_does_not_fit_keeps_the_plain_reason"],
    },
    {
        "file": _LLM,
        "old": "        if active_gib is not None and active_gib <= ram:\n",
        "new": "        if False:\n",
        # The clause never firing, which is the original wording - the whole
        # finding, restored.
        "label": "the residency clause never fires (the original sentence)",
        "expect": ["test_a_short_of_residence_but_not_of_per_token_says_which_limit_it_is",
                   "test_a_dense_tier_names_the_regime_too"],
    },
    {
        "file": _LLM,
        "old": '                f"{active_gib:.2f} GiB, which fits, so this is a residency limit "\n                f"and not a cap on whether it runs.")\n',
        "new": '                f"{active_gib:.2f} GiB, which fits; it would run at about "\n                f"{ram / active_gib:.1f} tok/s here.")\n',
        # Found by writing the test, not by reading the clause. The refusal is
        # silent on speed on purpose - nobody has measured a model larger than
        # RAM on a small box - and `ram / active_bytes` is not a decode rate, it
        # is a ratio of two unrelated quantities. A refusal that invents a number
        # is the failure this whole sentence was written to avoid.
        "label": "the refusal invents a decode rate it cannot measure",
        "expect": ["test_the_reason_never_claims_a_decode_rate"],
    },
    {
        "file": _LLM,
        "old": "        active = bytes_per_token(key)\n        active_gib = None if active is None else active / (1024 ** 3)\n",
        "new": "        active = None\n        active_gib = None\n",
        # Reading the module's own per-token figure is the whole basis of the
        # distinction. Without it every short-of-RAM machine gets the original
        # sentence, which is the pre-fix behaviour reached through a lookup
        # rather than through the condition.
        "label": "the per-token figure is not read at all",
        "expect": ["test_a_short_of_residence_but_not_of_per_token_says_which_limit_it_is",
                   "test_a_dense_tier_names_the_regime_too"],
    },
]
