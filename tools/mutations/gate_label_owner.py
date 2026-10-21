"""Mutation set for the one owner of a switch's wording.

**A switch is named by the name the Settings row shows.** `gate_label()`
is where that wording lives - the Settings row's, then the sense panel's
title, then the key itself - and it was not the only answer: the
`Capability` method beside it was a second, narrower copy of the rule
(the tool table alone), so a capability gated by a *sense*'s key fell
back to the raw key while every other reader of the same question got
the sense's title.

The set is the ways the second answer can come back.
"""

TEST_FILE = "tests/test_capabilities.py"

_CAPS = "usr/lib/shani-chronoa/shani_chronoa/capabilities.py"

MUTATIONS = [
    {
        "file": _CAPS,
        "old": "        return gate_label(self.consent_key)\n",
        "new": "        return GATE_NAMES.get(self.consent_key, self.consent_key)\n",
        # The method is a second, narrower implementation again: a
        # sense-gated capability falls back to the raw key.
        "label": "the Capability method is a second, narrower answer",
        "expect": ["test_a_capability_names_its_gate_by_the_switch_not_the_key"],
    },
]
