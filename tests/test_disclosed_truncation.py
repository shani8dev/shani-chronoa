"""A shortened list must never read as a complete one.

Chronoa's skills mostly got this right on their own - `find_files`,
`list_directory`, `read_logs`, `read_text_file` and `search_file_contents` all
carry an explicit `truncated` flag and say how much was withheld. Two did not,
and both were recent: `manage_mount` capped the mount table at 40 and
`toggle_bluetooth` capped paired devices at 20, neither saying so. A machine
with 57 mounts produced an answer listing 40 of them with no indication that 17
existed, which the model reads as the whole filesystem.

So this is not "add truncation" - it is "make disclosure the path of least
resistance", via a helper that returns the withheld count *with* the slice, so
a caller cannot cap a list without also learning what it dropped.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import files  # noqa: E402
from shani_chronoa.senses import filesystems as filesystems_sense  # noqa: E402
from shani_chronoa.skills import manage_mount  # noqa: E402


class TestCapList:
    def test_under_the_limit_keeps_everything(self):
        assert files.cap_list([1, 2, 3], 10) == ([1, 2, 3], 0)

    def test_over_the_limit_reports_what_it_dropped(self):
        shown, withheld = files.cap_list(list(range(55)), 40)
        assert len(shown) == 40
        assert withheld == 15

    def test_exactly_at_the_limit_is_not_truncation(self):
        assert files.cap_list(list(range(40)), 40) == (list(range(40)), 0)

    def test_the_counts_always_add_up(self):
        rows = list(range(57))
        shown, withheld = files.cap_list(rows, 40)
        assert len(shown) + withheld == len(rows)

    def test_a_zero_limit_means_no_cap(self):
        """Documented convention, not an accident: 0 hides nothing.

        Asserted so the meaning cannot quietly change - a zero that suddenly
        showed an empty list would look like data loss.
        """
        shown, withheld = files.cap_list([1, 2, 3], 0)
        assert shown == [1, 2, 3]
        assert withheld == 0

    def test_a_negative_limit_also_means_no_cap(self):
        shown, withheld = files.cap_list([1, 2, 3], -5)
        assert shown == [1, 2, 3] and withheld == 0

    def test_it_accepts_any_iterable(self):
        shown, withheld = files.cap_list(iter(range(9)), 4)
        assert len(shown) == 4 and withheld == 5


class TestWithheldNote:
    def test_it_says_nothing_when_nothing_was_withheld(self):
        assert files.withheld_note("mount", 0) == ""

    def test_it_names_the_count(self):
        assert "17" in files.withheld_note("mount", 17)

    def test_it_says_they_were_not_shown(self):
        note = files.withheld_note("mount", 17)
        assert "not shown" in note

    def test_it_can_say_how_to_get_the_rest(self):
        note = files.withheld_note("mount", 3, "Use the 'filesystems' sense.")
        assert "Use the 'filesystems' sense." in note

    def test_one_item_is_not_pluralised(self):
        assert "1 more mount not shown" in files.withheld_note("mount", 1)

    def test_several_are(self):
        assert "2 more mounts not shown" in files.withheld_note("mount", 2)


class TestSkillsThatCapActuallyDisclose:
    def test_manage_mount_says_how_many_mounts_it_hid(self, monkeypatch):
        rows = [{"target": f"/mnt/{i}", "fstype": "ext4", "source": f"/dev/sd{i}"}
                for i in range(57)]
        monkeypatch.setattr(filesystems_sense, "read_mounts", lambda: rows)
        out = manage_mount._run({"action": "list"})
        assert "not shown" in out, (
            "57 mounts were capped to 40 with no disclosure, so the answer "
            "reads as the whole filesystem"
        )
        assert "17" in out

    def test_manage_mount_says_nothing_extra_when_it_fits(self, monkeypatch):
        rows = [{"target": f"/mnt/{i}", "fstype": "ext4", "source": f"/dev/sd{i}"}
                for i in range(3)]
        monkeypatch.setattr(filesystems_sense, "read_mounts", lambda: rows)
        assert "not shown" not in manage_mount._run({"action": "list"})

    def test_listing_mounts_is_not_gated(self, monkeypatch):
        """Reading what is mounted must stay ungated, or the user cannot even
        find out what they would be unmounting."""
        monkeypatch.setattr(filesystems_sense, "read_mounts", lambda: [])
        out = manage_mount._run({"action": "list"})
        assert "Refusing" not in out
