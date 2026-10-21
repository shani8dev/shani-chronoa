"""Mutation set for the desktop panel's keyring answer.

**The keyring is the thing, not the module that talks to it.** A
module's name is the code's word, and a person reading the answer
cannot act on it.

**This branch is not reached on a machine with libsecret** - the
typelib is there, so the row answers "running" and the sentence about a
missing typelib is never rendered. **A sweep of every panel cannot see
it either**, because the sweep drives the panel the way this machine
happens to be. So the witness is the desktop's own test, which makes
the typelib absent on purpose.

That is the general shape worth keeping: **a branch a machine never
reaches is a branch no panel-wide sweep can watch.**
"""

TEST_FILE = "tests/test_surface_desktop.py"

_DESKTOP = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/desktop.py"

MUTATIONS = [
    {
        "file": _DESKTOP,
        "old": '                "available, so there is nothing here to ask with whatever "\n',
        "new": '                "available, so secret_store.py has nothing to ask with whatever "\n',
        # The module's name is back in the answer of the branch that only a
        # machine without libsecret ever reaches.
        "label": "the keyring's missing-typelib answer names the module",
        "expect": ["test_a_missing_typelib_is_not_installed_whatever_the_bus_says"],
    },
]
