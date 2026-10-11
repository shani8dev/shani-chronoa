"""`sysfs.finite` and `sysfs.read_float`: one implementation, not two.

The second pass over the previous one's own work. On 2026-10-11
`senses/location.py` gained a finite-number check, written by reading
`skills/media_control.py` - which had carried the *same three lines* since
before. So the check was **duplicated while its original existed**, which is the
failure `files.py` records in its own docstring:

> the earlier `wpctl` triplication in this repo showed exactly what happens when
> the same logic is copied per skill: three copies, three timeouts, and a
> divergence nobody notices until one of them is wrong.

Two copies of a three-line test is two chances to get one of them wrong, and the
honest finding here is that the author of the second copy had read the first and
duplicated it anyway.

**What is measured rather than asserted:** `float("nan")` and `float("inf")` both
succeed, `isinstance(True, int)` is True so `float(True)` is `1.0`, and a NaN
latitude reaching `maps` builds the Overpass query `around:5000,nan,nan` - which
returns nothing and reads as "there is no petrol pump near you" rather than as a
broken position. `read_uptime` is asserted against the real `/proc/uptime`, and
the field argument against the file's own two-column shape.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

import math  # noqa: E402

import pytest  # noqa: E402

from shani_chronoa import sysfs  # noqa: E402


class TestTheSharedFiniteCheck:
    """`sysfs.finite`, and the four shapes it refuses."""

    @pytest.mark.parametrize("value", [
        0, 1, -1, 18.5204, -33.87, 90, 180, 10 ** 18, 0.5,
    ])
    def test_a_real_number_is_one(self, value):
        assert sysfs.finite(value) is True

    @pytest.mark.parametrize("value,why", [
        (float("nan"), "NaN compares unequal to itself, so `value == value` is the test"),
        (float("inf"), "`float(\"inf\")` succeeds"),
        (float("-inf"), "as is its negative"),
        (True, "`isinstance(True, int)` is True, so `float(True)` is 1.0"),
        (False, "and `float(False)` is 0.0 - a temperature of zero degrees"),
        ("18.5", "a JSON string coerces under `float()`; it is not a number"),
        ("nan", "the *string* 'nan' also coerces"),
        (None, "gpsd's own allowed TPV shape"),
        ([18.5], "a list is what a broken capture produces"),
    ])
    def test_a_thing_that_is_not_a_number_is_not_one(self, value, why):
        assert sysfs.finite(value) is False, why

    def test_nan_is_not_refused_by_being_NaN_looking(self):
        """A named constant, so the reason survives the refactor.

        `math.nan == math.nan` is False - which is why `value == value` is the
        test and not an identity test. **Written as a separate assertion on
        purpose:** the first version chained it (`math.nan == math.nan is
        False`), which Python reads as `(math.nan == math.nan) and (math.nan is
        False)` and which passes for the wrong reason.
        """
        assert sysfs.finite(math.nan) is False
        assert (math.nan == math.nan) is False
        assert math.nan is math.nan  # same object, still not equal


class TestBothCallSitesUseTheSharedOne:
    """The whole reason this file exists: two copies became one.

    Each test names the call site, and each is confirmed to fail when that site
    grows its own copy back - which is the only thing that can tell a shared
    helper from a third duplicate.
    """

    def test_location_uses_the_shared_check(self, monkeypatch):
        """`senses/location._coordinate` must not have its own finite test.

        **This is a source-level assertion, and it is the point of the file**: a
        behavioural test passes whether the check is shared or local, so only
        reading the source can tell them apart.
        """
        from shani_chronoa.senses import location

        source = _source_of(location)
        assert "sysfs.finite" in source, (
            "location grew its own finite check instead of using the shared one")
        assert "value != value" not in source and "!= value or value in" not in source, (
            "location carries a second copy of the finite test")

    def test_media_control_uses_the_shared_check(self, monkeypatch):
        """The original copy, which predates `location`'s."""
        from shani_chronoa.skills import media_control

        source = _source_of(media_control)
        assert "sysfs.finite" in source, (
            "media_control grew its own finite check back")
        assert "value != value" not in source and "!= value or value in" not in source

    def test_no_third_copy_exists_anywhere(self):
        """A grep for the pattern this consolidation removed.

        Sixteen senses and skills carried their own copies of *two* functions,
        and `sysfs.py` exists because of it. This is the same check applied to
        the check.

        **The pattern is the exact duplicated pair, not any NaN test.** A third
        module does test for NaN - `triggers/common.py:_clamped_seconds`, whose
        `if number != number: return default` is a *different contract* (it
        returns the rule's default, not a refusal, and lets the `min`/`max` pair
        below handle an infinity by pinning it). Folding that one in would
        change trigger behaviour, so it is recorded in AGENTS.md and left alone,
        and this test does not pretend it is the same thing.
        """
        import pathlib
        import re

        package = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa")
        pattern = re.compile(
            r"!=\s*\w+\s+or\s+\w+\s+in\s*\(\s*float\(['\"]inf['\"]\)")
        offenders = []
        for path in package.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            if pattern.search(path.read_text(errors="replace")):
                offenders.append(str(path))
        assert offenders == [], (
            f"another copy of the finite test exists in {offenders}")


class TestReadFloat:
    """The float sibling `read_int` never had."""

    def _file(self, tmp_path, name, text):
        path = tmp_path / name
        path.write_text(text)
        return path

    def test_a_single_value_file(self, tmp_path):
        path = self._file(tmp_path, "temp", "45000\n")
        assert sysfs.read_float(path) == 45000.0

    def test_a_two_column_file_takes_the_first(self, tmp_path):
        """`/proc/uptime` is `"<uptime> <idle>"`, and only the first is uptime."""
        path = self._file(tmp_path, "uptime", "19798.65 106050.13\n")
        assert sysfs.read_float(path) == 19798.65

    def test_a_field_other_than_zero(self, tmp_path):
        path = self._file(tmp_path, "uptime", "19798.65 106050.13\n")
        assert sysfs.read_float(path, 1) == 106050.13

    def test_a_missing_file_is_none(self, tmp_path):
        assert sysfs.read_float(tmp_path / "absent") is None

    def test_an_empty_file_is_none_not_zero(self, tmp_path):
        """Same rule as `read_int`: empty is unmeasurable, not zero."""
        path = self._file(tmp_path, "empty", "")
        assert sysfs.read_float(path) is None
        path.write_text("   \n  ")
        assert sysfs.read_float(path) is None

    def test_text_that_is_not_a_number_is_none(self, tmp_path):
        path = self._file(tmp_path, "bad", "angstrom\n")
        assert sysfs.read_float(path) is None

    def test_a_nan_in_a_file_is_none(self, tmp_path):
        """`float("nan")` succeeds, so a file can hold one."""
        path = self._file(tmp_path, "nan", "nan\n")
        assert sysfs.read_float(path) is None

    def test_an_infinity_in_a_file_is_none(self, tmp_path):
        path = self._file(tmp_path, "inf", "inf\n")
        assert sysfs.read_float(path) is None

    def test_a_field_that_does_not_exist_is_none(self, tmp_path):
        path = self._file(tmp_path, "one", "45000\n")
        assert sysfs.read_float(path, 3) is None

    def test_the_real_proc_uptime(self):
        """**Against the real file, not a fixture.** This is the one call site
        `read_float` was extracted for, so it is worth checking it still reads
        what it read before the extraction.

        /proc is not always mountable in a container, so this is a skip and not
        a failure when it is absent - and the reason names which one it is.
        """
        path = pathlib.Path("/proc/uptime")
        if not path.exists():
            pytest.skip("/proc/uptime is not mounted here")
        value = sysfs.read_float(path)
        assert isinstance(value, float) and value > 0, (
            f"read_uptime's own file stopped parsing: {value!r}")
        # The file's second column is idle time; taking it would double the age.
        raw = path.read_text().split()
        assert value == float(raw[0]), (
            "read_float took the wrong column of /proc/uptime")


class TestTheCallSiteItWasExtractedFor:
    """`cpu.read_uptime` now goes through `sysfs`, and still answers."""

    def test_uptime_against_the_real_file(self, monkeypatch):
        from shani_chronoa.senses import cpu

        if not pathlib.Path("/proc/uptime").exists():
            pytest.skip("/proc/uptime is not mounted here")
        assert cpu.read_uptime() == float(
            pathlib.Path("/proc/uptime").read_text().split()[0])

    def test_uptime_is_none_when_the_file_cannot_be_read(self, monkeypatch):
        from shani_chronoa.senses import cpu

        monkeypatch.setattr(sysfs, "read_text", lambda p: None)
        assert cpu.read_uptime() is None, (
            "an unreadable /proc/uptime must be None, not a crash and not 0.0 - "
            "an uptime of zero says the machine just booted")

    def test_uptime_is_none_when_the_file_is_garbage(self, monkeypatch):
        from shani_chronoa.senses import cpu

        monkeypatch.setattr(sysfs, "read_text", lambda p: "not a number\n")
        assert cpu.read_uptime() is None

    def test_uptime_reads_through_the_shared_reader(self):
        """A source-level assertion, and the point of it.

        The behavioural test above passes whether `read_uptime` calls
        `sysfs.read_float` or holds its own copy of the read-and-None contract -
        which is exactly what it did before this change. **Only a source check
        can tell a shared helper from a fourth duplicate**, and the mutation
        that restores the local copy is what proves this test can fail.
        """
        import inspect

        from shani_chronoa.senses import cpu

        source = inspect.getsource(cpu.read_uptime)
        assert "sysfs.read_float" in source, (
            "read_uptime grew its own copy of the read-and-None contract back")
        assert "float(raw.split(" not in source, (
            "read_uptime parses /proc/uptime by hand instead of asking sysfs")


import pathlib  # noqa: E402


def _source_of(module):
    import inspect

    return inspect.getsource(module)
