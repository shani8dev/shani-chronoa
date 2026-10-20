"""Mutation set for the work chip naming its project and its state.

**The chip already named the project's folder** - so "which code is it
changing" was answered without opening anything. **The branch and the
count of changed files were the rest of it**, and neither had an answer
anywhere on the screen.

So the chip's **tooltip** carries them, read in **one** subprocess -
`git status --short --branch` gives both, and running it twice for two
numbers is twice the wait.

**And the Diff panel became an addressable page**, so the chip's menu
has a "See what changed" row with a real target: the panel was
reachable only by holding the mouse, from a sidebar that is collapsed at
the width the app opens at.

The set is the four ways each of those can be lost.
"""

TEST_FILE = "tests/test_the_work_chip_says_which_project_it_is_on.py"

_WINDOW = "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"
_VCS = "usr/lib/shani-chronoa/shani_chronoa/skills/vcs_status.py"

MUTATIONS = [
    {
        "file": _WINDOW,
        "old": "        said = f\" Branch: {branch}.\"\n",
        "new": "        said = \"\"\n",
        # The branch is a coder's first question about a checkout, and it
        # is gone.
        "label": "the tooltip does not name the branch",
        "expect": ["test_the_tooltip_carries_the_branch_and_the_count"],
    },
    {
        "file": _WINDOW,
        "old": "            said += (f\" {changed} file\" + (\"\" if changed == 1 else \"s\")\n"
               "                     + \" changed.\")\n",
        "new": "            said += \"\"\n",
        # How much is uncommitted - the other half of the same question.
        "label": "the tooltip does not count the changed files",
        "expect": ["test_the_tooltip_carries_the_branch_and_the_count"],
    },
    {
        "file": _WINDOW,
        "old": "        menu.append(\"See what changed\", \"app.show-page::diff\")\n",
        "new": "",
        # The door is gone, so the count and the branch are the only way
        # to the changes.
        "label": "the chip has no door to what changed",
        "expect": ["test_the_chip_offers_a_door_to_it"],
    },
    {
        "file": _WINDOW,
        "old": "         (\"diff\", \"Changes\"),\n",
        "new": "",
        # **The panel is addressable again only by holding the mouse** -
        # the sidebar row, which is collapsed at the width the app opens
        # at.
        "label": "the panel of changes is not a page any more",
        "expect": ["test_it_is_a_registered_page"],
    },
    {
        "file": _VCS,
        "old": "        elif key == \"changed\":\n            changed += 1\n",
        "new": "        elif key == \"changed\":\n            pass\n",
        # The count of uncommitted files, read from the same `git status`
        # that names the branch.
        "label": "the changed files are never counted",
        "expect": ["test_a_changed_file_is_counted"],
    },
]
