"""`scheduled_tasks`: what is going to run on this machine.

Nothing among the other skills read a crontab, a `/etc/cron.d` drop-in or a
systemd timer, so "what is scheduled?" had no answer on an OS whose safety story
is blue-green slots and btrfs rollback.

**Two real bugs are pinned here, both of which reported something confidently
false and neither of which looked wrong:**

- `systemctl show --property=X` prints **only** the properties asked for. The
  unit name comes from `Id`, which was read but never requested, so every block
  parsed without one, every block was discarded, and the answer was **"system
  timers: none"** on a machine with fifteen. An empty list and a list whose
  rows were all thrown away are the same output.
- `list-timers`' columns are whitespace-aligned free text - a NEXT date is "Fri
  2026-10-09 23:34:18 IST" and a LEFT column is "3 days" or "56min ago" - so the
  prefix before the `.timer` name contains *both*. Printed as one string it
  produced `next Sat 2026-10-10 00:00:00 IST 29min Fri 2026-10-09 00:00:00 IST`,
  which is a wrong answer about **when a job will run** with a past timestamp
  glued to it.

And the honesty properties, which is most of what is left: three sources that
must be reported separately (a machine can have any one and none of the others),
a dead timer distinguished from a live one, and an unreadable source never
counted as an empty one.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import tools  # noqa: E402
from shani_chronoa.skills import scheduled_tasks as st  # noqa: E402

USER_CRONTAB = """\
# m h dom mon dow  command
*/15 * * * *   /usr/bin/thing --now
0 4 * * *     /usr/local/bin/backup.sh
"""

#: `/etc/cron.d` and `/etc/crontab` carry the account between the schedule and
#: the command. Parsing one as the other turns `root /usr/bin/thing` into a job
#: whose command is "root".
SYSTEM_CRONTAB = """\
SHELL=/bin/sh
PATH=/usr/bin:/bin
17 *	* * *	root	cd / && run-parts --report /etc/cron.hourly
"""

#: Real `systemctl show` output for two timers, copied off this machine.
SHOW = """\
Id=fwupd-refresh.timer
Triggers=fwupd-refresh.service
ActiveState=active
SubState=waiting
NextElapseUSecRealtime=Fri 2026-10-09 23:34:18 IST
LastTriggerUSec=Fri 2026-10-09 22:46:54 IST

Id=ua-timer.timer
Triggers=ua-timer.service
ActiveState=inactive
SubState=dead
NextElapseUSecRealtime=
LastTriggerUSec=
"""

#: `systemctl list-timers` output, kept in the fixture **and used as a real
#: answer**: its columns are free text, and a NEXT date is "Fri 2026-10-09
#: 23:34:18 IST" while a LEFT column is "3 days" or "56min ago", so the prefix
#: before the `.timer` name holds both and printing it as one string answers
#: "when will this run" with a past timestamp glued to it.
LIST_TIMERS = """\
Fri 2026-10-09 23:34:18 IST    3min  Fri 2026-10-09 22:46:54 IST   43min ago fwupd-refresh.timer   fwupd-refresh.service
Sat 2026-10-10 00:22:56 IST   34min  -                                 -        snapd.snap-repair.timer  snapd.snap-repair.service
"""

LIST_UNITS = """\
  anacron.timer             loaded active   waiting Trigger anacron every hour
  ua-timer.timer            loaded inactive dead    Ubuntu Pro Timer
  fwupd-refresh.timer       loaded active   waiting Refresh the firmware
"""


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """cron, systemd and the `/etc` tree all replaceable.

    Each argument can be set to None to make that source *unreadable*, which is
    not the same as empty - the distinction three of these tests exist for.
    """
    state = {"cron": USER_CRONTAB, "system_crontab": SYSTEM_CRONTAB,
             "cron_d": {}, "timers": SHOW, "user_timers": None,
             "list_timers": LIST_TIMERS, "which": True}

    state["calls"] = []

    def _run(cmd):
        state["calls"].append(tuple(cmd))
        if not state["which"]:
            return None            # as `shutil.which(cmd[0]) is None` would
        if cmd[0] == "crontab":
            return None if state["cron"] is None else state["cron"]
        if cmd[0] != "systemctl":
            return None
        out = state["user_timers"] if "--user" in cmd else state["timers"]
        if out is None:
            return None
        if "list-timers" in cmd:
            return state["list_timers"]
        if "list-units" in cmd:
            return LIST_UNITS
        # **`systemctl show --property=X` prints only the properties asked
        # for.** A fixture that returned one fixed blob whatever was requested
        # cannot see the `Id` bug - which is *about* which properties are
        # requested - and four mutations survived here for exactly that reason,
        # including the one this file's docstring is built around.
        wanted = [c.split("=", 1)[1] for c in cmd if c.startswith("--property=")]
        if not wanted:
            return out
        blocks, block = [], {}
        for line in out.splitlines() + [""]:
            if not line.strip():
                if block:
                    blocks.append(block)
                block = {}
                continue
            key, _s, value = line.partition("=")
            if key in wanted:
                block[key] = value
        return "\n\n".join("\n".join(f"{k}={v}" for k, v in b.items())
                             for b in blocks) + "\n"

    # Point the cron reader at a temp tree, so `/etc/crontab` and `/etc/cron.d`
    # are this fixture's content rather than whatever the machine has. The
    # `run-parts` folders are read from the real `/etc` still - which makes them
    # a control on the fixture rather than another stub.
    fake_etc = tmp_path / "etc"
    (fake_etc / "cron.d").mkdir(parents=True, exist_ok=True)
    if state["system_crontab"] is not None:
        (fake_etc / "crontab").write_text(state["system_crontab"])
    for name, body in state["cron_d"].items():
        (fake_etc / "cron.d" / name).write_text(body)
    # One run-parts folder with one script in it. The folders hold **scripts**,
    # not crontabs - the `/etc/crontab` entry `run-parts --report /etc/cron.daily`
    # runs them - so an empty-of-entries folder is still scheduled work, and
    # "nothing" there is a confident wrong answer. Built in the fixture rather
    # than read from the real `/etc` so the premise holds on any machine.
    # A `/etc/cron.d` drop-in: like `/etc/crontab` it carries the account, and
    # it is read by a *different call site* than `/etc/crontab` is. Testing
    # only the latter left the loop's own `user_field` untested - the mutation
    # that parsed this one as a user crontab survived.
    (fake_etc / "cron.d" / "housekeeping").write_text(
        "5 2 * * *\troot\t/usr/local/bin/housekeeping --quiet\n")
    (fake_etc / "cron.daily").mkdir(exist_ok=True)
    (fake_etc / "cron.daily" / "backup").write_text("#!/bin/sh\n/usr/bin/backup --nightly\n")
    monkeypatch.setattr(st, "_etc_dir", lambda: fake_etc)

    monkeypatch.setattr(st, "_run", _run)
    # Both halves of "this binary is not installed": `_run`'s own `which` guard
    # (patched above) and the `shutil.which` check the cron reader makes
    # separately. With only the first, the module asked the real `shutil.which`,
    # found the real `crontab`, and reported a machine state the fixture had
    # just declared impossible - which is exactly the confusion this test exists
    # to catch, so it is worth the global patch.
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda name: None if not state["which"] else real_which(name))
    state["_etc"] = fake_etc
    return state


# ── the crontab parser ──────────────────────────────────────────────────────

def test_a_user_crontab_entry_is_read():
    jobs = st._parse_crontab(USER_CRONTAB, user_field=False)
    assert [j["schedule"] for j in jobs] == ["*/15 * * * *", "0 4 * * *"]
    assert jobs[0]["command"] == "/usr/bin/thing --now"
    # A user's own crontab has no user field, so the account running it is the
    # person asking. Asserted as "not empty and not 'root'": the real name is
    # read from the environment, and hard-coding one would pass on this machine
    # and fail on every other.
    assert jobs[0]["user"] and jobs[0]["user"] != "root"


def test_a_system_entry_keeps_its_user_field_out_of_the_command():
    """`root  cd / && run-parts` - the account is not part of the command."""
    jobs = st._parse_crontab(SYSTEM_CRONTAB, user_field=True)
    assert len(jobs) == 1, jobs
    assert jobs[0]["user"] == "root"
    assert jobs[0]["command"] == "cd / && run-parts --report /etc/cron.hourly"
    assert jobs[0]["user"] not in jobs[0]["command"]


def test_a_variable_assignment_is_not_a_scheduled_job():
    """`SHELL=/bin/sh` has five words before the "command"; it is not one.

    Counting fields rather than matching words is what keeps a variable out, and
    it is also what keeps a genuine entry whose command happens to contain an
    `=` in.
    """
    jobs = st._parse_crontab(SYSTEM_CRONTAB, user_field=True)
    assert not any(j["command"].startswith("SHELL") for j in jobs)
    assert not any("PATH=" in j["command"] for j in jobs)


def test_comments_and_blank_lines_are_not_jobs():
    jobs = st._parse_crontab("# a comment\n\n   \n0 4 * * * /bin/true\n",
                             user_field=False)
    assert len(jobs) == 1


def test_the_schedule_is_described_in_words_when_the_fields_are_literal():
    jobs = st._parse_crontab("0 4 * * * /bin/true\n", user_field=False)
    assert jobs[0]["when"] == "minute 0, hour 4"
    # Every field a wildcard is omitted rather than spelled "every minute" six
    # times, which says less than naming the one that is pinned.
    assert "day of month" not in jobs[0]["when"]


# ── systemd timers ──────────────────────────────────────────────────────────

def test_a_live_timer_reports_when_it_next_runs_and_when_it_last_did(fake):
    rows = {r["unit"]: r for r in st._timers(user=False)}
    row = rows["fwupd-refresh.timer"]
    assert row["state"] == "active"
    assert row["next"] == "Fri 2026-10-09 23:34:18 IST"
    assert row["last"] == "Fri 2026-10-09 22:46:54 IST"
    assert row["activates"] == "fwupd-refresh.service"


def test_a_dead_timer_is_distinguished_from_a_live_one(fake):
    """"Installed" and "will happen" are different facts; only one is true.

    `list-timers` shows these in one flat list. A reader - or a model - counting
    timers would count `ua-timer.timer` as something that is going to run.
    """
    rows = {r["unit"]: r for r in st._timers(user=False)}
    assert rows["ua-timer.timer"]["state"] == "inactive"
    assert rows["ua-timer.timer"]["sub"] == "dead"


def test_a_timer_that_has_never_run_is_not_a_missing_value(fake):
    """Empty `NextElapseUSecRealtime` means "has never elapsed"."""
    rows = {r["unit"]: r for r in st._timers(user=False)}
    assert rows["ua-timer.timer"]["next"] == ""
    assert rows["ua-timer.timer"]["last"] == ""


# ── the honesty properties ──────────────────────────────────────────────────

def test_the_three_sources_are_reported_separately(fake):
    """A machine can have any one of these and none of the others.

    This one has a user crontab, a system crontab and timers. Answering with a
    single number would be a claim about all three, and it is only ever right by
    luck.
    """
    text = st._run_skill({"source": "all"})
    assert "your crontab" in text
    assert "/etc/crontab" in text
    assert "system timers" in text


def test_an_unreadable_source_is_not_an_empty_one(fake):
    """`crontab -l` exits 1 when you have no crontab, and that is the answer.

    The other direction matters more: a source that could not be *asked* is
    reported as unreadable rather than as nothing there, because "I could not
    ask" and "there is nothing" are different facts about the machine.
    """
    fake["timers"] = None
    text = st._run_skill({"source": "timers"})
    assert "systemd could not be asked" in text
    assert "Not answered for" in text
    assert "none" not in text.split("Not answered for")[0].replace(
        "systemd could not be asked", "")


def test_a_missing_cron_binary_is_reported_not_guessed(fake):
    """No `crontab` on this machine is not the same as no cron jobs.

    `/etc/crontab` and `/etc/cron.d` are plain files, so they are still read
    when the binary is gone - and the answer has to say that rather than
    reporting a total it cannot stand behind.
    """
    fake["which"] = False
    text = st._run_skill({"source": "cron"})
    # The binary being absent is named as such - not "you have no crontab",
    # which was what this printed before and is a statement about a machine it
    # never asked.
    assert "crontab command is not installed" in text, text
    assert "you have no crontab" not in text
    assert "Not answered for" in text
    # The files are still read: `/etc/crontab` is a file, not a command, so a
    # missing `crontab` binary does not make the system's own schedule unknown.
    assert "/etc/crontab" in text


def test_a_run_parts_folder_is_not_reported_as_nothing(fake):
    """`/etc/cron.daily` holds **scripts**, and the schedule is set elsewhere.

    The `/etc/crontab` entry `run-parts --report /etc/cron.daily` runs every
    executable in that folder daily - so an empty folder of scripts is still
    work that is going to happen, and saying "nothing" is a confident wrong
    answer. This box has seven files in `/etc/cron.daily`.
    """
    text = st._run_skill({"source": "cron"})
    assert "run-parts" in text or "cron.hourly" in text
    for folder in ("/etc/cron.daily", "/etc/cron.weekly", "/etc/cron.monthly",
                   "/etc/cron.hourly", "/etc/cron.d"):
        # By prefix, not substring: the `/etc/crontab` row legitimately
        # *mentions* "/etc/cron.daily" in its `run-parts` command, and
        # `"...daily" in line` matched that row instead of the folder's. It
        # reported the assertion on the wrong line and passed for the wrong
        # reason - the shape this file keeps recording.
        rows = [ln.strip() for ln in text.splitlines()
                if ln.strip().startswith(f"{folder}:")]
        for row in rows:
            assert "nothing" not in row or "runs on a" not in row, row


def test_a_bad_source_is_refused_by_name(fake):
    assert "cron" in st._run_skill({"source": "whenever"})


# ── reachability ────────────────────────────────────────────────────────────

def test_the_skill_is_registered():
    names = {t["function"]["name"] for t in tools.TOOLS}
    assert "scheduled_tasks" in names


def test_it_is_in_help_and_not_in_the_other_group():
    from shani_chronoa import capabilities
    group, label = capabilities._GROUPS["scheduled_tasks"]
    assert group != "Other", group
    assert label


def test_it_is_a_read_and_needs_no_permission():
    """What the machine has scheduled describes the machine, not the person's world."""
    from shani_chronoa import capabilities
    assert "scheduled_tasks" not in capabilities.GATED
    assert "scheduled_tasks" not in capabilities.MUTATING_TOOLS
    assert "scheduled_tasks" in capabilities.READ_ONLY_TOOLS


@pytest.mark.parametrize("request_", [
    "what is scheduled to run",
    "what cron jobs do I have",
    "any systemd timers",
    "what runs automatically",
    "show my scheduled tasks",
    "is there a crontab",
])
def test_the_router_offers_it_for_the_plainest_phrasings(request_):
    """`CONVERSATIONAL` matches "what is " and sends it to `ask_user` alone.

    Its machine-subject lookahead names `timer` and not `cron`, so a first
    version of this list could not reach the skill at all by that phrasing.
    Asserted per phrasing: 3-of-6 is a green-looking score over a mostly-broken
    feature, which is the shape of failure this repository keeps meeting.
    """
    from shani_chronoa import tool_select
    names = [t["function"]["name"] for t in tool_select.select_tools(request_, tools.TOOLS)]
    assert "scheduled_tasks" in names, f"{request_!r} -> {names[:5]}"


def test_it_schedules_nothing_itself():
    """The name is close to `alarm`, which creates timers. This one only reads."""
    from shani_chronoa import capabilities
    assert "scheduled_tasks" not in capabilities.DESTRUCTIVE_CONSENT_KEYS
    assert "scheduled_tasks" != capabilities._GROUPS and True

def test_the_rendered_timers_say_which_will_fire_and_which_will_not(fake):
    """The dead/live distinction has to reach the *answer*, not just the rows.

    Three of these mutations survived while the tests only inspected `_timers()`'
    return value: counting dead timers as live, and rendering them as live, are
    the same defect seen from two directions, and only one of them is a fact
    about the machine.
    """
    text = st._run_skill({"source": "timers"})
    assert "1 will run" in text, text
    assert "1 installed but dead" in text, text
    assert "ua-timer.timer" in text and "DEAD" in text, text
    assert "fwupd-refresh.timer" in text
    dead_line = [ln for ln in text.splitlines() if "ua-timer.timer" in ln][0]
    assert "DEAD" in dead_line and "will not fire" in dead_line, dead_line


def test_a_system_entry_is_never_reported_with_the_account_as_its_command(fake):
    """`root  cd / && run-parts` through the whole skill, not just the parser.

    `_parse_crontab(..., user_field=True)` was tested directly and passed while
    `_cron_sources` passed `False` for the same file - the parser was right and
    the caller was not, which a test of the parser cannot see.
    """
    # Written through the fixture's own state and read by the skill from a temp
    # `/etc`, so this exercises `_cron_sources`' choice of `user_field` rather
    # than the parser in isolation.
    fake["system_crontab"] = "17 *\t* * *\troot\t/usr/local/bin/backup.sh\n"
    (fake["_etc"] / "crontab").write_text(fake["system_crontab"])
    text = st._run_skill({"source": "cron"})
    # Selected by the `/etc/crontab` prefix, not by the command: `backup.sh` is
    # in the fixture's *user* crontab too, and `if "backup.sh" in line` picked
    # that row, which is a different code path and made the assertion about the
    # wrong thing.
    rows = [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith("/etc/crontab:")]
    assert rows, text
    assert "as root: /usr/local/bin/backup.sh" in rows[0], rows[0]
    assert "root /usr/local" not in rows[0], rows[0]


def test_a_folder_of_run_parts_scripts_is_reported_as_scripts(fake):
    """Positively, not by the absence of a bad phrase.

    The earlier version asserted only that a row did not say both "nothing" and
    "runs on a", which a row saying plain "nothing" satisfies - so removing the
    script branch entirely left the file green.
    """
    text = st._run_skill({"source": "cron"})
    daily = [ln.strip() for ln in text.splitlines()
             if ln.strip().startswith("/etc/cron.daily:")]
    assert daily, text
    assert "runs on a schedule set elsewhere" in daily[0], daily[0]
    assert "nothing" not in daily[0], daily[0]


def test_the_command_issued_is_list_units_not_list_timers(fake):
    """Asserted on the argv, because the two produce output this code cannot
    otherwise tell apart under a fixture.

    `list-timers`' columns are free text and its NEXT/LAST are separate
    columns; reading them as one string is what produced "next <LAST>" answers
    on this machine. The property that prevents it is *which command is run*, so
    that is what is asserted - a fixture that answers both calls with equivalent
    content cannot tell the parsers apart, and three mutations survived here for
    exactly that reason.
    """
    st._timers(user=False)
    issued = [" ".join(c) for c in fake["calls"]]
    assert any("list-units" in c for c in issued), issued
    assert not any("list-timers" in c for c in issued), issued
    # And the properties actually asked for, which is the whole `Id` bug.
    props = [c for c in issued if "--property=" in c]
    assert props, issued
    assert any("--property=Id" in c for c in props), props


def test_a_cron_d_dropin_keeps_its_account_out_of_the_command(fake):
    """The `/etc/cron.d` loop, which `/etc/crontab` is read by a different line."""
    text = st._run_skill({"source": "cron"})
    rows = [ln.strip() for ln in text.splitlines() if "housekeeping" in ln]
    assert rows, text
    assert "as root: /usr/local/bin/housekeeping --quiet" in rows[0], rows[0]


def test_each_source_appears_even_when_another_is_empty(fake):
    """The three are reported separately, so dropping one is visible.

    A mutation that kept cron and quietly stopped reading timers produced an
    answer that still looked complete, because "N scheduled jobs" does not
    mention timers at all.
    """
    text = st._run_skill({"source": "all"})
    for expected in ("your crontab", "/etc/crontab", "/etc/cron.d",
                     "system timers"):
        assert expected in text, f"{expected} missing from:\n{text}"
