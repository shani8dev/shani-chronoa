"""Mutation set for no panel naming the code's own files at the person.

**A module's name is the code's word.** "so secret_store.py can read
and store API keys" tells a person nothing they can act on - the keyring
is the thing, and the keyring is what the Settings row and the desktop
are about. The file belongs in the evidence, which every panel already
puts behind its own **Details** button.

The same holds for a model file: "links it as current.gguf" is the
loader's private name for a symlink, and nobody reading "why is my
model not loading?" needs it.

Measured 2026-10-21 by building every panel: three panels named a
module in a sentence a person reads.

The set is the ways a file's name can come back.
"""

TEST_FILE = "tests/test_no_panel_names_a_file.py"

_DIAGNOSTICS = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/diagnostics.py"
_DESKTOP = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/desktop.py"
_ORGANISM = "usr/lib/shani-chronoa/shani_chronoa/organism.py"

MUTATIONS = [
    {
        "file": _DESKTOP,
        "old": '        "Chronoa can read and store API keys in the desktop keyring",\n',
        "new": '        "secret_store.py can read and store API keys in the desktop keyring",\n',
        # The module's name is back in the answer, and a person reading it
        # cannot act on it.
        "label": "the desktop panel names the module at the person",
        "expect": ["test_no_panel_names_a_file_at_the_person[desktop]"],
    },
    {
        "file": _DIAGNOSTICS,
        "old": '        "the AT-SPI typelib imports, so Chronoa can read another app\'s "\n',
        "new": '        "the AT-SPI typelib imports, so skills/ui_elements.py can read another app\'s "\n',
        "label": "the diagnostics panel names the module at the person",
        "expect": ["test_no_panel_names_a_file_at_the_person[diagnostics]"],
    },
    {
        "file": _DIAGNOSTICS,
        "old": '            "setup downloads one and makes it the one that answers"\n',
        "new": '            "setup downloads one and links it as current.gguf"\n',
        # A model file's name is the same class of thing: the code's word.
        "label": "the diagnostics panel names the model file at the person",
        "expect": ["test_no_panel_names_a_file_at_the_person[diagnostics]"],
    },
    {
        "file": _ORGANISM,
        "old": '                  "(on the mode strip, and it reaches SLEEPING), "\n',
        "new": '                  "(presence.py, on the mode strip, and it reaches SLEEPING), "\n',
        "label": "the anatomy page names the module at the person",
        "expect": ["test_no_panel_names_a_file_at_the_person[inventory]"],
    },
]
