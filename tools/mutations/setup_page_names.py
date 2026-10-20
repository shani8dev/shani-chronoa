"""Mutation set for the setup wizard's pages being named for people.

**"Brain", "Ears", "Eyes", "Imagine"** are the inside of this app - the
organ names in `organism.py`, which the Inventory panel is *about*. The setup
wizard is the first thing a new person reads, and it called its pages by
them.

The set is the rename itself, the **article** the rename introduced, the
**second copy** of the titles the rename was supposed to remove, and the two
places the same group names live in the Help window - because a title is a
word and a group is a heading, and the same rename had to reach both.

**The article is here because the rename's own words were wrong.** "The
assistant" reads correctly on its own and becomes "Open the The assistant
page" in the sentence the wizard puts it into. Nothing about the rename
failed; the sentence round it did.
"""

TEST_FILE = "tests/test_the_setup_pages_are_named_for_people.py"

_WIZARD = "usr/lib/shani-chronoa/shani_chronoa/setup_wizard.py"
_CAPS = "usr/lib/shani-chronoa/shani_chronoa/capabilities.py"

MUTATIONS = [
    {
        "file": _WIZARD,
        "old": '    ("brain", "Assistant"), ("model-picker", "Model"), ("ears", "Hearing"),\n'
               '    ("voice", "Voice"), ("review", "Review"), ("extras", "Optional extras"),\n'
               '    ("eyes", "Seeing"), ("imagine", "Pictures"), ("memory", "Search"),\n',
        "new": '    ("brain", "Brain"), ("model-picker", "Model"), ("ears", "Ears"),\n'
               '    ("voice", "Voice"), ("review", "Review"), ("extras", "Optional extras"),\n'
               '    ("eyes", "Eyes"), ("imagine", "Imagine"), ("memory", "Memory"),\n',
        # The shipped names, verbatim. The wizard is the first thing a new
        # person reads, and it called its pages by the inside of the app.
        "label": "the pages are called by the organ names again (the shipped wording)",
        "expect": ["test_no_page_is_called_an_organ",
                   "test_the_pages_say_what_they_are_for"],
    },
    {
        "file": _WIZARD,
        "old": '    ("brain", "Assistant"), ("model-picker", "Model"), ("ears", "Hearing"),\n',
        "new": '    ("brain", "The assistant"), ("model-picker", "Model"), ("ears", "Hearing"),\n',
        # **The rename's own words were wrong.** The wizard says "Open the
        # {title} page", so a title carrying its own article reads as "Open
        # the The assistant page" - and the title is fine on its own, which
        # is why reading the table did not catch it.
        "label": "a title carries its own article, and the wizard says 'the {title} page'",
        "expect": ["test_no_title_carries_its_own_article"],
    },
    {
        "file": _WIZARD,
        "old": '    win = Adw.Window(application=application, title="Set up Chronoa", default_width=560, default_height=620)\n'
               '    win.set_modal(False)\n',
        "new": '    win = Adw.Window(application=application, title="Set up Chronoa", default_width=560, default_height=620)\n'
               '    win.set_modal(False)\n'
               '    #: A second copy of the page titles, which is the copy that drifts.\n'
               '    _titles = {"welcome": "Welcome", "mode": "Mode", "cloud-keys": "Cloud keys",\n'
               '               "brain": "Brain", "model-picker": "Model", "ears": "Ears",\n'
               '               "voice": "Voice", "extras": "Optional extras", "review": "Review",\n'
               '               "eyes": "Eyes", "imagine": "Imagine", "memory": "Memory",\n'
               '               "photos": "Photos and videos", "sounds": "Sounds",\n'
               '               "speakers": "Who said what", "languages": "Languages",\n'
               '               "done": "Done"}\n',
        # The shape the tree actually had: the forward button's titles were a
        # hand-kept map beside the page list, and a rename that changed one
        # and not the other produced a button saying "Next: Brain" on a page
        # called "Assistant".
        "label": "the builder holds a second copy of the page titles",
        "expect": ["test_the_builder_holds_no_second_table"],
    },
    {
        "file": _CAPS,
        "old": '    "Screen",\n    "Seeing",\n    "Photos and video",\n    "Pictures",\n',
        "new": '    "Screen",\n    "Eyes",\n    "Photos and video",\n    "Imagine",\n',
        # The Help window's headings are read by a person too, and they carry
        # the same names as the pages.
        "label": "the Help groups are called by the organ names again",
        "expect": ["test_no_group_is_called_an_organ"],
    },
    {
        "file": _CAPS,
        "old": '    "capture_video": ("Seeing", "Record a short video from the camera"),\n',
        "new": '    "capture_video": ("Eyes", "Record a short video from the camera"),\n',
        # **The other half of the group rename.** A group name lives in two
        # places - the display order and the tool that is filed under it -
        # so renaming one and not the other puts a tool under a heading the
        # Help does not show.
        "label": "a tool is filed under a heading the Help does not show",
        "expect": ["test_every_group_a_tool_names_is_in_the_order"],
    },
]
