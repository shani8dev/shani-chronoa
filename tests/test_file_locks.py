"""`file_locks`: the kernel's lock table, read directly.

**Why this exists.** "Why won't this file save?" is a blocked writer's
question, and the answer is almost always a lock somebody else holds.
`open_files` answers "what has this file **open**" - and its own docstring
claimed "has a lock" too, which was false. Measured against a descriptor it
was pointed at: lsof prints the lock as trailing characters on the FD
(`171ur` = open read-write, *read lock on part of the file*), the parser keeps
the raw string, the `(command, name)` dedupe drops the row carrying it, and the
word "lock" does not appear in the answer at all. `/proc/locks` is the table
that answers it.

**Measured on this box, 95 live locks**, against the real `/proc/locks`:

    1: POSIX  ADVISORY  WRITE 440213 00:26:4048156 1073741825 1073741825
    3: POSIX  ADVISORY  READ  112059 fc:01:14596476 128 128

Two facts the parser rests on, both read off real lines rather than assumed:

- **the device half is hex, the inode is decimal.** `fc:01:14596476` is
  major 0xfc = 252, minor 0x01 = 1, inode 14596476. Reading the device as
  decimal gives major 252 * 256 + ... and matches nothing; reading the inode as
  hex gives a different number. Both mutations are below.
- **a lock is matched on `(major, minor, inode)`, never the inode alone.**
  Two files on different filesystems share an inode number, so an inode-only
  match reports a lock on one as a lock on the other.

**The privacy boundary is the fd table, and it is kept.** `/proc/locks` names
a pid and an inode but no path, so each holder's own `/proc/<pid>/fd` is read.
Another user's process has an fd table this user cannot read, so its lock keeps
its pid and program name and the path reads as unreadable - never guessed at.
That sentence is pinned below, because "the holder's fd table is not readable"
and "no lock" must not read alike.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.skills import file_locks as fl  # noqa: E402
from shani_chronoa.skills import open_files  # noqa: E402

# Two real lines, verbatim from this machine's /proc/locks, with the device in
# hex and the inode in decimal. Captured, not typed: a fixture that disagrees
# with the kernel's format tests nothing about the parser.
LINE_WRITE = ("1: POSIX  ADVISORY  WRITE 440213 00:26:4048156 "
              "1073741825 1073741825")
LINE_READ_PART = "3: POSIX  ADVISORY  READ  112059 fc:01:14596476 128 128"


def _key_of(path) -> tuple:
    """`(major, minor, inode)` for a real file, the way `_locks_on` builds it.

    **Built through `os.major()`/`os.minor()`, never `st_dev >> 8`.** A first
    version of these fixtures rebuilt the device out of the raw `st_dev` with
    shifts, which is a *guess* at the kernel's encoding - and it failed once in
    a wider run while passing alone, which is the shape of a fixture that
    depends on the machine rather than on the code. Deriving the key the same
    way the module does makes the fixture true on any filesystem, and the
    `!= key` assertion next to each one makes sure it is actually a different
    file's lock.
    """
    st = os.stat(path)
    return os.major(st.st_dev), os.minor(st.st_dev), st.st_ino


class TestTheParse:
    """`1: POSIX ADVISORY READ 112059 fc:01:14596476 128 128`."""

    def test_the_device_is_hex_and_the_inode_is_decimal(self):
        lock = fl._parse(LINE_READ_PART)
        assert lock.major == 0xFC          # 252, not 252*16+1
        assert lock.minor == 0x01
        assert lock.inode == 14596476      # decimal, not hex

    def test_the_zero_major_of_a_zero_device_line(self):
        """`00:26:4048156` is major 0, minor 0x26 - both parse as 0/38.

        The device `00:26` reads as major 0 in both bases, so this line is the
        one that *looks* like it would pass a decimal read and does not: it is
        in the file precisely because a real machine emits both shapes.
        """
        lock = fl._parse(LINE_WRITE)
        assert (lock.major, lock.minor) == (0, 0x26)
        assert lock.inode == 4048156

    def test_the_first_two_fields(self):
        lock = fl._parse(LINE_READ_PART)
        assert lock.index == 3
        assert lock.klass == "POSIX"
        assert lock.lock_type == "ADVISORY"
        assert lock.access == "READ"
        assert lock.pid == 112059
        assert (lock.start, lock.end) == ("128", "128")

    def test_a_line_that_is_not_a_lock_is_skipped_not_raised_over(self):
        """The table is read on every call, so one bad line must not stop the rest."""
        assert fl._parse("") is None
        assert fl._parse("garbage") is None
        assert fl._parse("1: POSIX ADVISORY READ 112059") is None   # 5 fields
        assert fl._parse("1: POSIX ADVISORY READ 112059 zz:01:1 0 EOF") is None
        assert fl._parse("1: POSIX ADVISORY READ x fc:01:1 0 EOF") is None

    def test_one_bad_line_does_not_hide_the_others(self, monkeypatch):
        raw = "junk\n" + LINE_READ_PART + "\n\n" + LINE_WRITE

        def fake_read_text(self_path):
            if str(self_path) == "/proc/locks":
                return raw
            raise AssertionError("only /proc/locks should be read here")

        monkeypatch.setattr(fl.Path, "read_text", fake_read_text)
        locks = fl._locks()
        assert len(locks) == 2
        assert [lock.index for lock in locks] == [3, 1]

    def test_a_table_that_cannot_be_read_is_no_locks(self, monkeypatch):
        def boom(self):
            raise OSError("nope")
        monkeypatch.setattr(fl.Path, "read_text", boom)
        assert fl._locks() == []


class TestTheSurveyPhrasing:
    """The sentences are the skill's product; they are pinned verbatim."""

    def test_the_empty_machine_says_so_without_calling_it_a_failure(self, monkeypatch):
        monkeypatch.setattr(fl, "_locks", lambda: [])
        out = fl._survey()
        assert "No process holds a lock anywhere on this machine" in out
        # "a quiet machine, not a failure" - an empty answer here is the
        # ordinary state, and saying so is the difference between a read and an
        # alarm.
        assert "not a failure" in out

    def test_the_counts_are_write_first_and_name_the_split(self, monkeypatch):
        locks = [fl.Lock(i, "POSIX", "ADVISORY", "WRITE", 1, 0, 8, 10, "0", "EOF")
                 for i in (1, 2)]
        locks.append(fl.Lock(3, "POSIX", "ADVISORY", "READ", 2, 0, 8, 11,
                             "0", "EOF"))
        monkeypatch.setattr(fl, "_locks", lambda: locks)
        monkeypatch.setattr(fl, "_comm", lambda pid: f"pid{pid}")
        monkeypatch.setattr(fl, "_resolve_all", lambda ls: {})
        out = fl._survey()
        assert "3 lock(s) held right now: 2 write, 1 read" in out
        assert "Locks that block another writer" in out

    def test_a_write_lock_is_listed_with_its_holder_and_range(self, monkeypatch):
        lock = fl.Lock(7, "POSIX", "ADVISORY", "WRITE", 4242, 0, 8, 99,
                       "0", "EOF")
        monkeypatch.setattr(fl, "_locks", lambda: [lock])
        monkeypatch.setattr(fl, "_comm", lambda pid: "someapp")
        monkeypatch.setattr(fl, "_resolve_all",
                            lambda ls: {7: "/tmp/data.db"})
        out = fl._survey()
        assert "someapp" in out and "pid 4242" in out
        assert "/tmp/data.db" in out
        assert "the whole file" in out

    def test_an_unreadable_holder_keeps_pid_and_name_and_says_why(self,
                                                                  monkeypatch):
        """A process of another user's fd table is not readable - say that.

        Not "no lock" and not a blank path. This sentence is the whole privacy
        boundary, and it is the difference between "I could not look" and
        "there is nothing there".
        """
        lock = fl.Lock(7, "POSIX", "ADVISORY", "WRITE", 4242, 0, 8, 99,
                       "0", "EOF")
        monkeypatch.setattr(fl, "_locks", lambda: [lock])
        monkeypatch.setattr(fl, "_comm", lambda pid: "otherusersapp")
        monkeypatch.setattr(fl, "_resolve_all", lambda ls: {7: None})
        out = fl._survey()
        assert "otherusersapp" in out and "pid 4242" in out
        assert "(the holder's fd table is not readable)" in out

    def test_read_locks_are_grouped_by_program_not_listed_one_by_one(self,
                                                                   monkeypatch):
        """Three READ locks on one file are ordinary.

        They are *ranged*, which is the case that is genuinely shared - so
        they belong in the grouped section, which is what a range lock is for.
        """
        locks = [fl.Lock(i, "POSIX", "ADVISORY", "READ", pid, 0, 8, 10 * i,
                         "128", "512") for i, pid in ((1, 5), (2, 5), (3, 5),
                                                      (4, 6))]
        monkeypatch.setattr(fl, "_locks", lambda: locks)
        monkeypatch.setattr(fl, "_comm", lambda pid: "readerA" if pid == 5
                            else "readerB")
        monkeypatch.setattr(fl, "_resolve_all", lambda ls: {})
        out = fl._survey()
        assert "readerA: 3" in out
        assert "readerB: 1" in out
        # Three READ locks on the same file are ordinary; listing them as three
        # rows reads as three problems.
        assert out.count("readerA: 3") == 1
        assert "ranged read locks" in out

    def test_a_range_lock_names_its_bytes(self, tmp_path, monkeypatch):
        """The targeted answer shows the range, whatever the access mode."""
        target = tmp_path / "ranged.db"
        target.write_text("x")
        monkeypatch.setattr(fl, "_comm", lambda pid: "app")
        # The locks carry the file's REAL device and inode: `_locks_on` matches
        # on `(major, minor, inode)`, so a fixture with invented numbers is
        # refused by the matcher and the test measures nothing. Building the
        # key from `os.stat` is also what keeps this honest if the matcher
        # changes.
        st = os.stat(target)
        locks = [fl.Lock(1, "POSIX", "ADVISORY", "READ", 5, os.major(st.st_dev),
                         os.minor(st.st_dev), st.st_ino, "128", "512"),
                 fl.Lock(2, "POSIX", "ADVISORY", "READ", 5, os.major(st.st_dev),
                         os.minor(st.st_dev), st.st_ino, "128", "EOF")]
        monkeypatch.setattr(fl, "_locks", lambda: locks)
        monkeypatch.setattr(fl, "_resolve_all",
                            lambda ls: {1: str(target), 2: str(target)})
        out = fl._locks_on(str(target))
        assert "bytes 128-512" in out
        assert "bytes 128 to the end" in out


class TestAWholeFileReadLockBlocksAWriter:
    """Measured on this box, not assumed: a READ lock is not the harmless
    thing its name suggests.

        parent holds whole-file READ   -> child wants WRITE: BLOCKED errno=11
        CONTROL nothing held           -> child wants WRITE: ACQUIRED

    `F_RDLCK` is shared with other readers and exclusive against writers. Seven
    whole-file read locks existed here while this was written, and the survey
    printed every one of them under "Read locks are shared" - so somebody whose
    save was blocked by a read lock was told reads are harmless.
    """

    def test_a_whole_file_read_lock_counts_as_blocking(self):
        lock = fl.Lock(1, "POSIX", "ADVISORY", "READ", 7, 0, 8, 9, "0", "EOF")
        assert fl._blocks_a_writer(lock) is True

    def test_a_ranged_read_lock_is_not_called_a_save_blocker(self):
        """It blocks a write only where the ranges overlap.

        Claiming it blocks the whole file would be as wrong as ignoring a
        whole-file one - the failure in both directions is the same.
        """
        ranged = fl.Lock(1, "POSIX", "ADVISORY", "READ", 7, 0, 8, 9, "128",
                         "512")
        assert fl._blocks_a_writer(ranged) is False

    def test_any_write_lock_blocks_whatever_its_range(self):
        ranged_write = fl.Lock(1, "POSIX", "ADVISORY", "WRITE", 7, 0, 8, 9,
                               "128", "512")
        assert fl._blocks_a_writer(ranged_write) is True

    def test_the_survey_lists_a_whole_file_read_lock_as_blocking(self,
                                                                monkeypatch):
        lock = fl.Lock(1, "POSIX", "ADVISORY", "READ", 4242, 0, 8, 99, "0",
                       "EOF")
        monkeypatch.setattr(fl, "_locks", lambda: [lock])
        monkeypatch.setattr(fl, "_comm", lambda pid: "readerapp")
        monkeypatch.setattr(fl, "_resolve_all", lambda ls: {1: "/tmp/db"})
        out = fl._survey()
        assert "1 lock(s) held right now: 0 write, 1 read" in out
        # Listed, named READ, with its holder and its file.
        assert "Locks that block another writer" in out
        assert "READ" in out and "readerapp" in out and "/tmp/db" in out
        # And not filed under the harmless section.
        assert "ranged read locks" not in out

    def test_the_counts_still_report_write_and_read_as_they_are(self,
                                                               monkeypatch):
        """Grouping does not change what the counts say: one read lock is
        still one read lock, whether or not it blocks a writer."""
        locks = [fl.Lock(1, "POSIX", "ADVISORY", "READ", 1, 0, 8, 9, "0",
                         "EOF"),
                 fl.Lock(2, "POSIX", "ADVISORY", "READ", 2, 0, 8, 10, "5",
                         "9"),
                 fl.Lock(3, "POSIX", "ADVISORY", "WRITE", 3, 0, 8, 11, "0",
                         "EOF")]
        monkeypatch.setattr(fl, "_locks", lambda: locks)
        monkeypatch.setattr(fl, "_comm", lambda pid: f"app{pid}")
        monkeypatch.setattr(fl, "_resolve_all", lambda ls: {})
        out = fl._survey()
        assert "3 lock(s) held right now: 1 write, 2 read" in out
        # Two of the three block a writer: the whole-file read and the write.
        blocking = out.split("Locks that block another writer")[1]
        blocking = blocking.split("The rest are ranged")[0]
        assert blocking.count("pid ") == 2


class TestTheTargetedAnswer:
    def test_a_locked_file_names_its_holders(self, tmp_path, monkeypatch):
        target = tmp_path / "locked.db"
        target.write_text("x")
        st = os.stat(target)
        lock = fl.Lock(4, "POSIX", "ADVISORY", "WRITE", 321, st.st_dev >> 8,
                       st.st_dev & 0xFF, st.st_ino, "0", "EOF")
        monkeypatch.setattr(fl, "_locks", lambda: [lock])
        monkeypatch.setattr(fl, "_comm", lambda pid: "holder")
        monkeypatch.setattr(fl, "_resolve_all", lambda ls: {4: str(target)})
        out = fl._locks_on(str(target))
        assert "1 lock(s) on" in out
        assert "WRITE" in out and "POSIX" in out
        assert "holder (pid 321)" in out
        assert str(target) in out

    def test_an_unlocked_file_says_where_to_look_instead(self, tmp_path,
                                                          monkeypatch):
        target = tmp_path / "quiet.txt"
        target.write_text("x")
        monkeypatch.setattr(fl, "_locks", lambda: [])
        out = fl._locks_on(str(target))
        assert f"No process holds a lock on {target}" in out
        # A write that still fails is then a *different* question, and the two
        # most likely answers are named. "Not locked" alone sends the person
        # back to the same question.
        assert "permissions" in out and "read-only filesystem" in out

    def test_a_missing_file_is_refused_with_the_reason(self):
        out = fl._locks_on("/definitely/not/here")
        assert out.startswith("Cannot look at /definitely/not/here:")
        assert "No such file or directory" in out

    def test_a_lock_on_a_different_inode_is_not_this_file(self, tmp_path,
                                                          monkeypatch):
        """Two files can share an inode number on different filesystems.

        Matching on the inode alone reports the other file's lock as this
        file's - which is the confident wrong answer this module exists not to
        give, and the reason the key is the whole `(major, minor, inode)`.
        """
        target = tmp_path / "mine.db"
        target.write_text("x")
        key = _key_of(target)
        elsewhere = fl.Lock(1, "POSIX", "ADVISORY", "WRITE", 9, key[0],
                            key[1], key[2] + 500, "0", "EOF")
        # The fixture must actually differ from the file's own key, or the
        # test asserts nothing and passes for a reason that has nothing to do
        # with the matcher. A control that cannot fail is not a control.
        assert (elsewhere.major, elsewhere.minor,
                elsewhere.inode) != key
        monkeypatch.setattr(fl, "_locks", lambda: [elsewhere])
        out = fl._locks_on(str(target))
        assert "No process holds a lock on" in out

    def test_the_same_inode_on_a_different_device_is_not_this_file(
            self, tmp_path, monkeypatch):
        """Same inode, different device - the half that needs the device."""
        target = tmp_path / "mine.db"
        target.write_text("x")
        key = _key_of(target)
        other_dev = fl.Lock(1, "POSIX", "ADVISORY", "WRITE", 9, key[0] + 7,
                            key[1], key[2], "0", "EOF")
        assert (other_dev.major, other_dev.minor,
                other_dev.inode) != key
        monkeypatch.setattr(fl, "_locks", lambda: [other_dev])
        out = fl._locks_on(str(target))
        assert "No process holds a lock on" in out


class TestTheFdTable:
    def test_a_real_descriptor_resolves_to_its_path(self):
        """The real thing, not a stub: this process's own `/proc/self/fd`."""
        with open(__file__, "rb") as handle:
            st = os.fstat(handle.fileno())
            key = (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
            paths = fl._fd_paths(os.getpid())
        assert key in paths
        assert paths[key] == __file__

    def test_another_users_fd_table_is_empty_not_an_error(self):
        """pid 1 is another user's here; the honest answer is no paths.

        It must be `{}` and not an exception, because the caller renders this
        as "the holder's fd table is not readable" rather than failing.
        """
        paths = fl._fd_paths(1)
        assert isinstance(paths, dict)

    def test_a_pid_that_does_not_exist_is_empty_not_a_crash(self):
        assert fl._fd_paths(4294967000) == {}

    def test_one_fd_walk_answers_every_lock_that_holder_carries(self,
                                                                monkeypatch):
        """**Three locks, one pid, one walk.**

        The grouping is a cost claim, not a correctness one: walking
        `/proc/<pid>/fd` once per *lock* returns the same paths as walking it
        once per *holder*, so a test of the answer cannot see the difference -
        which is exactly why this mutation survived its first run. Asserting
        the walk count is the only way to hold the property the docstring
        gives, and on a busy machine (a browser holds hundreds of locks) the
        difference is hundreds of reads of another process's fd table.
        """
        walks = []

        def counting(pid):
            walks.append(pid)
            return {(0, 8, 10): "/a", (0, 8, 11): "/b", (0, 8, 12): "/c"}

        locks = [fl.Lock(1, "POSIX", "ADVISORY", "WRITE", 777, 0, 8, 10,
                         "0", "EOF"),
                 fl.Lock(2, "POSIX", "ADVISORY", "READ", 777, 0, 8, 11,
                         "0", "EOF"),
                 fl.Lock(3, "POSIX", "ADVISORY", "WRITE", 777, 0, 8, 12,
                         "0", "EOF")]
        monkeypatch.setattr(fl, "_fd_paths", counting)
        resolved = fl._resolve_all(locks)
        # The answer is right either way - which is why this test counts.
        assert resolved == {1: "/a", 2: "/b", 3: "/c"}
        assert walks == [777]

    def test_two_holders_are_each_walked_once(self, monkeypatch):
        walks = []

        def counting(pid):
            walks.append(pid)
            return {(0, 8, 10): "/a", (0, 8, 20): "/b"}

        locks = [fl.Lock(1, "POSIX", "ADVISORY", "WRITE", 11, 0, 8, 10,
                         "0", "EOF"),
                 fl.Lock(2, "POSIX", "ADVISORY", "WRITE", 22, 0, 8, 20,
                         "0", "EOF")]
        monkeypatch.setattr(fl, "_fd_paths", counting)
        fl._resolve_all(locks)
        assert sorted(walks) == [11, 22]


class TestTheSkillEndToEnd:
    def test_the_registrations_are_one_handler_reachable_by_name(self):
        assert len(fl.SKILLS) == 1
        assert fl.SKILLS[0].name == "file_locks"
        # The dispatch path interpolates `handler.__name__` into an import in
        # the sandboxed child, so a lambda here is a skill that is advertised
        # to the model and fails on every call.
        assert fl.SKILLS[0].run.__name__ == "_run"
        assert fl.SKILLS[0].run.__name__.isidentifier()

    def test_it_is_discovered_by_the_real_registry(self):
        from shani_chronoa import skills
        tools, handlers = skills.discover_skills()
        names = {t["function"]["name"] for t in tools}
        assert "file_locks" in names
        assert "file_locks" in handlers
        # One schema per name: a duplicate would offer the model two of the
        # same tool, and only the first would be reachable.
        assert len(names) == len(tools)

    def test_no_path_surveys_and_a_path_is_targeted(self, tmp_path, monkeypatch):
        target = tmp_path / "f.txt"
        target.write_text("x")
        monkeypatch.setattr(fl, "_locks", lambda: [])
        assert "No process holds a lock anywhere" in fl._run({})
        assert str(target) in fl._run({"path": str(target)})
        assert str(target) in fl._run({"path": target})

    def test_an_empty_path_string_surveys_rather_than_being_a_path(self,
                                                                   monkeypatch):
        """`path=""` is falsy, so it surveys. A model that sends an empty
        string gets every lock rather than an error about an empty path."""
        monkeypatch.setattr(fl, "_locks", lambda: [])
        assert "No process holds a lock anywhere" in fl._run({"path": ""})

    def test_the_schema_offers_a_path_and_requires_nothing(self):
        props = fl.SCHEMA["function"]["parameters"]
        assert fl.SCHEMA["function"]["name"] == "file_locks"
        assert props["required"] == []
        assert "path" in props["properties"]

    def test_the_description_names_the_phrasings_a_person_would_use(self):
        desc = fl.SCHEMA["function"]["description"].lower()
        for phrase in ("locked", "why can", "write this file"):
            assert phrase in desc, f"missing {phrase!r} from the description"


class TestOpenFilesPointsAtThis:
    """The overclaim that made this skill necessary."""

    def test_open_files_docstring_no_longer_claims_the_lock(self):
        doc = open_files.__doc__
        assert "does not answer it either" in doc
        assert "`file_locks`" in doc

    def test_the_claim_is_true_of_the_code_and_not_only_the_prose(self):
        """Control: the docstring now says open_files never surfaces lock
        state. That has to be a fact about the code, so a `_describe` that
        mentioned locks would make this test wrong - which is the point of
        asserting it rather than trusting the prose."""
        assert "lock" not in open_files._NAMED_FD
        source = open_files._describe.__doc__ or ""
        assert "lock" not in source.lower()