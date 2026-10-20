"""Mutation set for the palette reaching the setup pages and the browser.

**The palette could open the wizard but not a page of it.** The Settings
group lists every Settings page the same way, so a person who wanted the
hearing model had to open the wizard and walk to it - on the one surface
whose whole job is to be the door to everything.

**The in-app browser was a window reachable only by a key.** It is not a
panel, so it is not in the sidebar, and nothing on screen said it
existed.

The set is the two halves of the door, and the two ways each can be
broken: an entry that is never offered, and an entry that is offered and
asks for the wrong thing.
"""

TEST_FILE = "tests/test_the_palette_reaches_the_setup_pages.py"

_WINDOW = "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"

MUTATIONS = [
    {
        "file": _WINDOW,
        "old": "        entries.extend(self._palette_setup_entries())\n",
        "new": "",
        # The group is never built, so the palette offers the wizard and not
        # one of its pages - which is the shape it had.
        "label": "the palette never offers the setup pages",
        "expect": ["test_every_setup_page_is_offered"],
    },
    {
        "file": _WINDOW,
        "old": '        ("In-app browser", "open a page beside the conversation", "web-browser-symbolic",\n'
               '         "open-browser"),\n',
        "new": "",
        # The browser is a window, so it is in no sidebar and in no panel -
        # and with this row gone it is reachable only by a key.
        "label": "the palette does not offer the in-app browser",
        "expect": ["test_the_browser_is_offered"],
    },
    {
        "file": _WINDOW,
        "old": '        ("In-app browser", "open a page beside the conversation", "web-browser-symbolic",\n'
               '         "open-browser"),\n',
        "new": '        ("In-app browser", "open a page beside the conversation", "web-browser-symbolic",\n'
               '         "open-the-browser"),\n',
        # **The palette's action entries are filtered by what the
        # application really registers**, so an action spelled wrongly is
        # not an entry that fails - it is an entry that is not offered at
        # all. Which is the quieter of the two failures, and the one the
        # wording of the row cannot reveal.
        "label": "the browser entry names an action the app does not have",
        "expect": ["test_the_browser_is_offered"],
    },
    {
        "file": _WINDOW,
        "old": "                (lambda t=tag: common.open_setup(getattr(self, \"_app\", None),\n"
               "                                                  None, page=t)),\n",
        "new": "                (lambda t=tag: common.open_setup(getattr(self, \"_app\", None),\n"
               "                                                  None, page=\"welcome\")),\n",
        # The row is offered and says the right thing, and opens the wrong
        # page - which is the failure a title cannot reveal.
        "label": "every setup row opens the wizard on the first page",
        "expect": ["test_pressing_one_asks_for_that_page"],
    },
]
