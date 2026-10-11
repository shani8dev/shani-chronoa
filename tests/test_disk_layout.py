"""Tests for `disk_layout` - the partition layout nothing else in the tree answers.

The fixture is the **real** `lsblk --json` captured on this machine, not a
hand-typed approximation, because the shape is the whole point: four levels
(`nvme0n1` -> `p3` -> `dm_crypt-0` -> `ubuntu--vg-ubuntu--lv` -> `/`), 32 of 33
block devices being snap `loop` layers, and one partition whose GPT type GUID
says "Linux filesystem" while its `fstype` is `crypto_LUKS`. A hand-written
fixture would have had none of those, and a parser written against it would
have been written against the wrong machine.

The GUID names were each checked against
`https://en.wikipedia.org/wiki/GUID_Partition_Table`, the reference the kernel
and `gdisk` both follow.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from shani_chronoa.skills import disk_layout

#: The real capture, trimmed to the fields the reader asks for.
REAL_DEVICE = {
    "name": "nvme0n1", "kname": "nvme0n1", "type": "disk", "size": "476.9G",
    "fstype": None, "label": None, "partlabel": None, "parttype": None,
    "mountpoint": None, "pkname": None,
    "children": [
        {"name": "nvme0n1p1", "kname": "nvme0n1p1", "type": "part",
         "size": "1G", "fstype": "vfat", "label": None, "partlabel": None,
         "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
         "mountpoint": "/boot/efi", "pkname": "nvme0n1"},
        {"name": "nvme0n1p2", "kname": "nvme0n1p2", "type": "part",
         "size": "2G", "fstype": "ext4", "label": None, "partlabel": None,
         "parttype": "0fc63daf-8483-4772-8e79-3d69d8477de4",
         "mountpoint": "/boot", "pkname": "nvme0n1"},
        {"name": "nvme0n1p3", "kname": "nvme0n1p3", "type": "part",
         "size": "473.9G", "fstype": "crypto_LUKS", "label": None,
         "partlabel": None, "parttype": "0fc63daf-8483-4772-8e79-3d69d8477de4",
         "mountpoint": None, "pkname": "nvme0n1",
         "children": [
             {"name": "dm_crypt-0", "kname": "dm_crypt-0", "type": "crypt",
              "size": "473.9G", "fstype": "LVM2_member", "label": None,
              "partlabel": None, "parttype": None, "mountpoint": None,
              "pkname": "nvme0n1p3",
              "children": [
                  {"name": "ubuntu--vg-ubuntu--lv",
                   "kname": "ubuntu--vg-ubuntu--lv", "type": "lvm",
                   "size": "473.9G", "fstype": "ext4", "label": None,
                   "partlabel": None, "parttype": None, "mountpoint": "/",
                   "pkname": "dm-0"},
              ]},
         ]},
    ],
}


def _loop(name="loop0", mountpoint="/snap/core22/2437"):
    return {"name": name, "kname": name, "type": "loop", "size": "74M",
            "fstype": "squashfs", "label": None, "partlabel": None,
            "parttype": None, "mountpoint": mountpoint, "pkname": None}


@pytest.fixture
def lsblk(monkeypatch):
    """Patch the process the reader actually shells to, and record its argv.

    A callable so a test can set the reply *and* assert on the argv the reader
    built: a fixture that answers any call cannot tell a correct
    `lsblk --json -o ...` from a wrong one, and the field list is exactly what
    silently reads None if the `-o` is wrong.
    """
    state = {"argv": None}

    def install(devices=None, returncode=0, stderr="", stdout=None):
        def run(argv, **kwargs):
            state["argv"] = list(argv)
            payload = (stdout if stdout is not None
                       else json.dumps({"blockdevices": devices or []}))
            return subprocess.CompletedProcess(argv, returncode, payload, stderr)
        monkeypatch.setattr(subprocess, "run", run)
        monkeypatch.setattr(disk_layout.shutil, "which",
                            lambda name: "/usr/bin/lsblk")
        return state

    return install


# --------------------------------------------------------------------------
# The reader's own contract with lsblk
# --------------------------------------------------------------------------

class TestTheLsblkCall:

    def test_it_asks_lsblk_for_json_and_the_fields_it_reads(self, lsblk):
        state = lsblk([REAL_DEVICE])
        disk_layout._measure()
        assert state["argv"][0] == "lsblk"
        assert "--json" in state["argv"]
        assert "-o" in state["argv"]
        fields = state["argv"][state["argv"].index("-o") + 1].split(",")
        # Every field the reader reads must be requested, or it silently reads
        # None for it - the absence-shaped wrong answer.
        for needed in ("TYPE", "SIZE", "FSTYPE", "PARTLABEL", "PARTTYPE",
                       "MOUNTPOINT"):
            assert needed in fields, f"{needed} is read but never asked for"

    def test_a_missing_lsblk_names_the_package(self, monkeypatch):
        monkeypatch.setattr(disk_layout.shutil, "which", lambda name: None)
        out = disk_layout._measure()
        assert "not installed" in out
        assert "util-linux" in out, "the refusal must name what provides it"

    def test_non_zero_exit_reports_lsblks_own_reason_not_just_the_number(
            self, lsblk):
        lsblk([], returncode=1, stderr="lsblk: /dev/sdb: failed to stat\n")
        out = disk_layout._measure()
        assert "failed to stat" in out
        assert "exited 1" not in out, "the number is the fallback, not the answer"

    def test_an_empty_read_is_not_a_disk_with_no_partitions(self, lsblk):
        # lsblk answering successfully with an empty list is a machine with no
        # disks; unreadable stdout is a machine where we could not look. Those
        # are opposite claims.
        lsblk([], stdout="")
        out = disk_layout._measure()
        assert "not the same as" in out

    def test_output_that_is_not_json_is_unknown_not_an_empty_disk(self, lsblk):
        lsblk([], stdout="lsblk: journal error\n")
        out = disk_layout._measure()
        assert "unknown" in out.lower()

    def test_a_missing_blockdevices_key_is_reported_not_treated_as_empty(
            self, lsblk):
        lsblk([], stdout=json.dumps({"nope": 1}))
        assert "no blockdevice list" in disk_layout._measure()


# --------------------------------------------------------------------------
# The tree, which is four levels deep on the machine it was measured on
# --------------------------------------------------------------------------

class TestTheTreeIsWalked:

    def test_all_four_levels_appear(self, lsblk):
        lsblk([REAL_DEVICE])
        out = disk_layout._measure()
        for name in ("nvme0n1", "nvme0n1p3", "dm_crypt-0",
                     "ubuntu--vg-ubuntu--lv"):
            assert name in out, f"{name} is missing from the layout"

    def test_the_root_mount_is_found_two_levels_below_its_partition(self, lsblk):
        # A parser that assumed disk -> partition -> mount would report the root
        # filesystem as unmounted, because it is partition -> crypt -> lvm.
        lsblk([REAL_DEVICE])
        out = disk_layout._measure()
        assert "mounted at /" in out

    def test_each_level_is_indented_below_its_parent(self, lsblk):
        lsblk([REAL_DEVICE])
        lines = [l for l in disk_layout._measure().splitlines()
                 if l.strip().startswith("- ")]

        def indent_of(prefix):
            line = next(l for l in lines if l.strip().startswith(prefix))
            return len(line) - len(line.lstrip())

        assert indent_of("- nvme0n1  ") < indent_of("- nvme0n1p3")
        assert indent_of("- nvme0n1p3") < indent_of("- dm_crypt-0")
        assert indent_of("- dm_crypt-0") < indent_of("- ubuntu--vg-ubuntu--lv")


# --------------------------------------------------------------------------
# The GUID table, checked against the published values
# --------------------------------------------------------------------------

GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                     r"[0-9a-f]{12}$")


class TestTheGptTypes:

    def test_the_two_guids_measured_on_this_machine_are_named_correctly(self):
        # These two are what `lsblk` printed here; a wrong name is a confident
        # wrong answer about what a partition is for.
        assert disk_layout._GPT_TYPES[
            "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"] == "EFI System Partition"
        assert disk_layout._GPT_TYPES[
            "0fc63daf-8483-4772-8e79-3d69d8477de4"] == "Linux filesystem"

    def test_a_luks_guid_is_named_luks(self):
        assert "LUKS" in disk_layout._GPT_TYPES[
            "e6d6d379-f507-44c2-a23c-238e2a3df928"]

    def test_every_guid_is_well_formed_and_named(self):
        for key, value in disk_layout._GPT_TYPES.items():
            assert GUID_RE.match(key), f"{key} is not a well-formed GUID"
            assert value, f"{key} maps to an empty name"

    def test_an_unknown_guid_is_printed_as_the_guid_never_guessed(self):
        node = {"name": "sdX9", "type": "part", "size": "8G", "fstype": "ntfs",
                "parttype": "deadbeef-0000-1111-2222-333344445555",
                "mountpoint": None}
        line = disk_layout._describe(node)
        assert "deadbeef-0000-1111-2222-333344445555" in line
        for guess in ("Linux", "data", "EFI"):
            assert guess not in line, f"{line!r} guesses a purpose"

    def test_a_guid_that_disagrees_with_the_filesystem_names_both(self):
        # Measured: p3 carries the Linux-filesystem GUID and is crypto_LUKS. The
        # GUID says what it was created as; the fstype says what it holds.
        node = {"name": "p3", "type": "part", "size": "473.9G",
                "fstype": "crypto_LUKS",
                "parttype": "0fc63daf-8483-4772-8e79-3d69d8477de4",
                "mountpoint": None}
        line = disk_layout._describe(node)
        assert "Linux filesystem" in line and "crypto_LUKS" in line
        assert "created as" in line

    def test_a_guid_that_agrees_is_not_announced_as_a_contradiction(self):
        node = {"name": "p2", "type": "part", "size": "8G", "fstype": "swap",
                "parttype": "bc13c2ff-59e6-4262-a352-b275fd6f7172",
                "mountpoint": None}
        line = disk_layout._describe(node)
        assert "Linux swap" in line
        assert "created as" not in line, (
            "a partition that agrees with its own GUID must not be reported as "
            "a contradiction")

    def test_an_uppercase_guid_still_matches(self):
        node = {"name": "p9", "type": "part", "size": "8G", "fstype": "ext4",
                "parttype": "0FC63DAF-8483-4772-8E79-3D69D8477DE4",
                "mountpoint": None}
        assert "Linux filesystem" in disk_layout._describe(node)


# --------------------------------------------------------------------------
# Sizes: a missing size must not become 0 B
# --------------------------------------------------------------------------

class TestSizes:

    @pytest.mark.parametrize("text,expect", [
        ("1G", 1024 ** 3),
        ("473.9G", int(473.9 * 1024 ** 3)),
        ("4.0K", 4096),
        ("512", 512),
    ])
    def test_human_sizes_convert(self, text, expect):
        assert disk_layout._size_bytes(text) == expect

    @pytest.mark.parametrize("bad", [None, "", "   ", "not a size", {}])
    def test_an_unreadable_size_is_none_not_zero(self, bad):
        # "0 B" is the shape this repository refuses to report as an answer.
        assert disk_layout._size_bytes(bad) is None

    def test_a_device_with_no_size_does_not_print_zero_bytes(self):
        line = disk_layout._describe({"name": "weird", "type": "disk",
                                     "size": None, "fstype": None,
                                     "mountpoint": None})
        # Measured: `_human(0)` renders as `0B` - no space, no decimal - so the
        # first version of this test asserted the absence of "0 B" and "0.0B"
        # and could not see its own mutation, which printed exactly that. The
        # guard has to test what the function actually renders.
        assert "0B" not in line and "0 B" not in line and "0.0B" not in line
        assert disk_layout._human(0) == "0B", (
            "if this changes, this assertion is measuring the wrong string")


# --------------------------------------------------------------------------
# The 32 snap loops, which is the whole reason this is readable
# --------------------------------------------------------------------------

class TestTheSnapLoopsAreHiddenAndCounted:

    def test_thirty_two_loops_do_not_bury_the_one_disk(self, lsblk):
        lsblk([_loop(f"loop{i}") for i in range(32)] + [REAL_DEVICE])
        out = disk_layout._measure()
        assert "nvme0n1" in out
        assert "loop0" not in out, "a squashfs loop device buried the answer"

    def test_the_hidden_count_is_stated_rather_than_dropped(self, lsblk):
        lsblk([_loop(f"loop{i}") for i in range(32)] + [REAL_DEVICE])
        out = disk_layout._measure()
        assert "32 other block device" in out

    def test_they_can_be_asked_for(self, lsblk):
        lsblk([_loop(f"loop{i}") for i in range(3)] + [REAL_DEVICE])
        out = disk_layout._measure(show_hidden=True)
        assert "loop0" in out and "squashfs" in out

    def test_a_machine_whose_only_devices_are_hidden_says_so(self, lsblk):
        lsblk([_loop(f"loop{i}") for i in range(32)])
        out = disk_layout._measure()
        assert "no physical disk" in out
        assert "32" in out, "the count is the useful part"


# --------------------------------------------------------------------------
# Consent: this reports the storage sense's facts, so it shares its switch
# --------------------------------------------------------------------------

class TestTheGate:

    def test_the_gate_is_checked_in_this_skill_not_only_in_a_helper(self):
        # `test_skill_gates_are_enforced.py` reads the skill's own source for
        # the gate. A gate hidden in a helper is a gate that test cannot see.
        source = open(disk_layout.__file__, encoding="utf-8").read()
        assert 'sense_allowed("storage")' in source

    def test_it_refuses_by_the_senses_own_switch(self, monkeypatch):
        class Cfg:
            def sense_allowed(self, name):
                return False  # a bare bool - what config.sense_allowed gives

            def sense_allowed_reason(self, name):
                return "storage-sense-enabled is off"

        monkeypatch.setattr(disk_layout, "ChronoaConfig", Cfg)
        out = disk_layout._run({})
        assert "storage-sense-enabled" in out
        assert "Not shown" in out

    def test_the_gate_reads_a_bare_bool_the_way_config_returns_one(
            self, monkeypatch):
        # Regression, measured: the real `ChronoaConfig.sense_allowed` returns a
        # **bool**, not a `(bool, why)` pair, and a first version unpacked it as
        # a pair. Every in-process test passed because `_measure()` was called
        # directly; the failure was a TypeError raised in the sandboxed child,
        # which is why the test now goes through `_run` with a config whose
        # `sense_allowed` is the real shape.
        granted = []

        class Cfg:
            def sense_allowed(self, name):
                granted.append(name)
                return True

            def sense_allowed_reason(self, name):  # pragma: no cover
                return "unused"

        monkeypatch.setattr(disk_layout, "ChronoaConfig", Cfg)
        out = disk_layout._run({"show_hidden": False})
        assert granted == ["storage"], "the gate must be consulted"
        # It ran rather than refusing: the gate opened, so the real layout is
        # what came back. (A first version asserted the *absence* of the
        # layout, which is the opposite of what an opened gate means.)
        assert "nvme0n1" in out, "an opened gate must produce the real layout"
        assert "Not shown" not in out

    def test_consent_helper_matches_the_house_shape(self):
        # Every sense-sharing skill exposes `_consent(config) -> (bool, str)`,
        # and `test_skill_gates_are_enforced.py` looks for exactly that.
        allowed, why = disk_layout._consent(type("C", (), {
            "sense_allowed": lambda self, n: True})())
        assert allowed is True and why == ""

    def test_the_sense_name_is_derived_not_typed(self):
        # A rename must not silently ungrant the skill, the way a renamed sense
        # key silently ungrants the sense.
        source = open(disk_layout.__file__, encoding="utf-8").read()
        assert 'sense_allowed("storage")' in source
        assert "storage-sense-enabled" not in source


# --------------------------------------------------------------------------
# Reachability: a skill nothing offers is the failure this repo records most
# --------------------------------------------------------------------------

class TestItIsReachable:

    def test_the_handler_is_importable_by_name(self):
        # The dispatch path builds `from module import <name>`, so a lambda
        # would be a SyntaxError in the child and a traceback to the model.
        assert disk_layout._run.__name__ == "_run"
        assert disk_layout._run.__name__.isidentifier()

    def test_the_schema_registers_under_its_own_name(self):
        skill = disk_layout.SKILLS[0]
        assert skill.name == "disk_layout"
        assert skill.schema["function"]["name"] == "disk_layout"

    def test_nothing_is_required_so_a_router_could_use_it(self):
        # `mcp.py` builds a signature in property order, so a required property
        # after an optional one takes the whole MCP server down.
        params = disk_layout.SCHEMA["function"]["parameters"]
        assert "required" not in params
        assert set(params["properties"]) == {"show_hidden"}