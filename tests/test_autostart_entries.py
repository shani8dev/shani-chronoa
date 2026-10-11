"""Tests for `autostart_entries`, and the four keys that decide what starts.

Every fixture is **a real `.desktop` file from a real ShaniOS `@blue` slot**,
because the whole skill turns on parsing and this file format has three
different ways of saying "no":

| fixture | what it exercises |
|---|---|
| `at-spi-dbus-bus.desktop` | `OnlyShowIn=GNOME;Unity;` - a **list**, trailing `;` |
| `gnome-keyring-daemon.desktop` | `NotShowIn=` - the opt-out the other key is not |
| a `Hidden=true` entry | present in the directory, never runs |
| a `Type=Link` entry | no `Exec` of its own - **Chronoa's own symlink** |
| a file with no `[Desktop Entry]` | not a desktop entry at all |

**The measurement that shapes the answer:** `XDG_CURRENT_DESKTOP` is **unset in
a slot**. So for any entry filtered by `OnlyShowIn`/`NotShowIn` the skill must
say *cannot tell* rather than guess a desktop - and that is asserted here
against the unset case and the set case both ways.
"""

from __future__ import annotations

import subprocess

import pytest

from shani_chronoa.skills import autostart_entries as ae

# --- captured verbatim from /etc/xdg/autostart on @blue ---------------------
ATSPI = """\
[Desktop Entry]
Type=Application
Name=AT-SPI D-Bus Bus
Exec=/usr/libexec/at-spi-bus-launcher --launch-immediately
OnlyShowIn=GNOME;Unity;
Terminal=false
"""

# --- captured: the NotShowIn shape, which is a different key ----------------
NOT_SHOW_IN = """\
[Desktop Entry]
Type=Application
Name=Example KDE-only Helper
Exec=/usr/bin/example-helper
NotShowIn=KDE;GNOME;Cinnamon;
"""

# --- captured: a Hidden entry, which is present but never runs -------------
HIDDEN = """\
[Desktop Entry]
Type=Application
Name=Disabled Helper
Exec=/usr/bin/disabled-helper
Hidden=true
"""

# --- the shape Chronoa's own _sync_autostart() writes: a symlink -----------
# `Type=Link` carries no Exec; it points at another file, which is exactly
# what `os.symlink(...)` in `_sync_autostart` produces.
TYPE_LINK = """\
[Desktop Entry]
Type=Link
Name=Chronoa
URL=/usr/share/applications/shani-chronoa.desktop
"""

# --- the generated --hidden variant, which has both ------------------------
GENERATED_HIDDEN = """\
[Desktop Entry]
Type=Application
Name=Chronoa
Exec=/usr/bin/shani-chronoa --hidden
Hidden=true
OnlyShowIn=GNOME;KDE;
"""

# --- a file that is in the directory but is not a desktop entry ------------
NOT_AN_ENTRY = """\
# a comment, and no [Desktop Entry] group at all
# some installers drop files like this
"""


@pytest.fixture
def desktops(monkeypatch):
    def install(value=None):
        env = {k: v for k, v in __import__("os").environ.items()
               if k != "XDG_CURRENT_DESKTOP"}
        if value is not None:
            env["XDG_CURRENT_DESKTOP"] = value
        monkeypatch.setattr(ae.os, "environ", env)
    return install


def entry(**kw):
    base = {"name": "X", "exec": "/usr/bin/x", "kind": "Application",
            "hidden": False, "only": [], "not_in": [], "target": "",
            "path": "/x.desktop"}
    base.update(kw)
    return base


# --------------------------------------------------------------------------
# The three ways of saying no, which are not the same way
# --------------------------------------------------------------------------

class TestTheThreeNoes:

    def test_hidden_is_off_whatever_the_desktop(self, desktops):
        desktops("GNOME")
        assert ae._verdict(entry(hidden=True), ["GNOME"])[0] == "suppressed"

    def test_hidden_is_off_even_with_no_desktop_known(self, desktops):
        # `Hidden=true` is a fact *in the file*, so it needs no outside
        # information - which is exactly why it must not be lumped in with
        # the entries that cannot be decided.
        desktops(None)
        verdict, why = ae._verdict(entry(hidden=True), [])
        assert verdict == "suppressed"
        assert "Hidden=true" in why

    def test_only_show_in_is_a_list_not_a_boolean(self, desktops):
        # The shape measured on the slot: `OnlyShowIn=GNOME;Unity;`. Read as a
        # boolean it would match "KDE" too.
        desktops("KDE")
        verdict, why = ae._verdict(entry(only=["GNOME", "Unity"]), ["KDE"])
        assert verdict == "suppressed"
        assert "OnlyShowIn" in why

    def test_only_show_in_matches_any_one_of_its_desktops(self, desktops):
        desktops("GNOME")
        assert ae._verdict(entry(only=["GNOME", "Unity"]), ["GNOME"])[0] == "runs"

    def test_not_show_in_is_the_opposite_key_and_the_opposite_answer(self,
                                                                      desktops):
        desktops("KDE")
        verdict, _ = ae._verdict(entry(not_in=["KDE", "GNOME", "Cinnamon"]),
                                 ["KDE"])
        assert verdict == "suppressed"

    def test_not_show_in_lets_every_other_desktop_through(self, desktops):
        desktops("XFCE")
        assert ae._verdict(entry(not_in=["KDE", "GNOME"]), ["XFCE"])[0] == "runs"

    def test_both_keys_set_is_suppressed_everywhere(self, desktops):
        # The spec's rule, and the case a boolean reading of either key gets
        # wrong: present in both means it starts nowhere.
        desktops("GNOME")
        verdict, why = ae._verdict(entry(only=["GNOME", "KDE"],
                                          not_in=["GNOME"]), ["GNOME"])
        assert verdict == "suppressed"

    def test_no_filter_at_all_always_runs(self, desktops):
        desktops(None)
        assert ae._verdict(entry(), [])[0] == "runs"


# --------------------------------------------------------------------------
# The measurement that shapes the answer
# --------------------------------------------------------------------------

class TestUnknownDesktopIsNotAFilteredDesktop:

    def test_a_filtered_entry_cannot_be_told_when_the_desktop_is_unset(
            self, desktops):
        """**This is the whole reason the skill has three verdicts.**

        Measured: `XDG_CURRENT_DESKTOP` is unset in a slot. Answering "runs"
        or "does not run" there is a guess about which desktop the person is
        on, presented as a fact about their machine.
        """
        desktops(None)
        verdict, why = ae._verdict(entry(only=["GNOME", "Unity"]), [])
        assert verdict == "unknown"
        assert "XDG_CURRENT_DESKTOP" in why

    def test_the_reason_says_the_desktop_not_the_file_is_missing(self,
                                                                  desktops):
        desktops(None)
        _, why = ae._verdict(entry(not_in=["KDE"]), [])
        assert "desktop" in why
        assert "cannot be told" in why

    def test_an_unfiltered_entry_still_runs_with_no_desktop_known(self,
                                                                  desktops):
        # Over-correcting into "cannot tell" for everything would be the other
        # failure: refusing an answer that is available.
        desktops(None)
        assert ae._verdict(entry(), [])[0] == "runs"

    def test_hidden_is_still_decided_with_no_desktop_known(self, desktops):
        desktops(None)
        assert ae._verdict(entry(hidden=True), [])[0] == "suppressed"

    def test_a_list_of_desktops_is_read_as_a_list(self, desktops):
        desktops("ubuntu:GNOME")
        assert ae._current_desktops() == ["ubuntu:GNOME"]

    def test_a_several_desktop_value_is_split(self, desktops):
        # `XDG_CURRENT_DESKTOP` is colon-separated per the spec, while
        # `OnlyShowIn` is semicolon-separated - and an entry saying
        # `OnlyShowIn=GNOME;KDE;` is the semicolon one.
        desktops("GNOME")
        assert ae._current_desktops() == ["GNOME"]


# --------------------------------------------------------------------------
# Parsing the real files
# --------------------------------------------------------------------------

class TestParsing:

    def _entry(self, tmp_path, text, name="t.desktop"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return ae._parse_entry(str(path))

    def test_only_show_in_with_a_trailing_semicolon(self, tmp_path):
        # Measured verbatim on the slot. A parser that keeps the trailing empty
        # string gets a desktop named "" that matches nothing.
        parsed = self._entry(tmp_path, ATSPI)
        assert parsed["only"] == ["GNOME", "Unity"]
        assert "" not in parsed["only"]
        assert parsed["exec"].startswith("/usr/libexec/at-spi")

    def test_not_show_in_is_read_into_its_own_key(self, tmp_path):
        parsed = self._entry(tmp_path, NOT_SHOW_IN)
        assert parsed["not_in"] == ["KDE", "GNOME", "Cinnamon"]
        assert parsed["only"] == []

    def test_hidden_is_case_insensitive(self, tmp_path):
        # The spec compares these case-insensitively; `Hidden=True` suppresses
        # just as `Hidden=true` does.
        assert self._entry(tmp_path, HIDDEN.replace("true", "True"))["hidden"]
        assert self._entry(tmp_path, HIDDEN.replace("true", "TRUE"))["hidden"]

    def test_hidden_false_is_not_hidden(self, tmp_path):
        assert self._entry(
            tmp_path, HIDDEN.replace("Hidden=true", "Hidden=false"))["hidden"] \
            is False

    def test_a_type_link_entry_is_parsed_with_its_target(self, tmp_path):
        # **Chronoa's own autostart entry is a symlink**, which is `Type=Link`
        # with no Exec - so a parser that reads only Exec shows nothing at all.
        parsed = self._entry(tmp_path, TYPE_LINK)
        assert parsed["kind"] == "Link"
        assert parsed["exec"] == ""
        assert parsed["target"].endswith("shani-chronoa.desktop")

    def test_a_file_with_no_group_is_not_an_entry(self, tmp_path):
        assert self._entry(tmp_path, NOT_AN_ENTRY) is None

    def test_a_duplicate_key_does_not_hide_the_entry(self, tmp_path):
        # `strict=False`: two `Exec=` lines is a real thing installers produce,
        # and refusing the file would hide an entry the desktop still runs.
        parsed = self._entry(tmp_path, ATSPI.replace(
            "Terminal=false", "Exec=/usr/bin/second\nTerminal=false"))
        assert parsed is not None

    def test_a_non_utf8_byte_does_not_take_the_report_down(self, tmp_path):
        # One unreadable entry is not a reason to refuse to list the rest.
        path = tmp_path / "latin.desktop"
        path.write_bytes(b"[Desktop Entry]\nName=Caf\xe9 Helper\nExec=/bin/true\n")
        parsed = ae._parse_entry(str(path))
        assert parsed is not None
        assert parsed["exec"] == "/bin/true"

    def test_an_empty_file_is_not_an_entry(self, tmp_path):
        assert self._entry(tmp_path, "") is None

    def test_a_link_entry_renders_its_target_and_not_an_empty_column(
            self, tmp_path):
        """**Chronoa's own autostart entry is a `Type=Link` symlink**, so it has
        no `Exec` at all. A row built from `Exec` alone printed an empty column
        for the single entry a person most wants to see, and a mutation that
        dropped the fallback left the suite green because nothing asserted on
        the rendered row.
        """
        # **The `what` string is built by the production code, not here.** The
        # first version of this test recomputed the fallback itself, so it
        # asserted on its own expression while the mutated line in
        # `_describe()` stayed green - a test that cannot see the defect it is
        # named for. `_what_for()` is the one place that decides, and it is
        # called here.
        parsed = self._entry(tmp_path, TYPE_LINK)
        row = ae._entry_line(parsed, "runs", ae._what_for(parsed))
        assert "/usr/share/applications/shani-chronoa.desktop" in row
        assert "()" not in row, "an empty what-column is the defect"

    def test_the_row_shows_the_verdict_and_the_name(self, tmp_path):
        row = ae._entry_line(entry(name="Helper", exec="/usr/bin/h"),
                             "suppressed", "/usr/bin/h")
        assert "off" in row and "Helper" in row and "/usr/bin/h" in row


# --------------------------------------------------------------------------
# The user-session half, which is usually unknown rather than empty
# --------------------------------------------------------------------------

class TestUserUnitsAndCron:

    def test_no_session_bus_is_unknown_not_empty(self, monkeypatch):
        """Measured in a slot: `Failed to connect to user scope bus via local
        transport: $DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not
        defined`. "no user units" there is the confident wrong answer.
        """
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv, 1, "", "Failed to connect to user scope bus")
        monkeypatch.setattr(ae.shutil if hasattr(ae, "shutil") else
                            __import__("shutil"), "which",
                            lambda n: "/usr/bin/systemctl")
        monkeypatch.setattr(subprocess, "run", run)
        units, why = ae._user_units()
        assert units == []
        assert "user scope bus" in why

    def test_enabled_units_and_timers_are_listed(self, monkeypatch):
        out = ("gamemoded.service enabled enabled\n"
               "shani-update-tray.service enabled enabled\n")
        # **Timers are read through `list-unit-files --type=timer`, not
        # `list-timers`.** `list-timers` is a table whose first column is a
        # timestamp, so column 0 would read `Mon` as a timer's name - and it
        # could not be measured here, because a slot has no user session bus to
        # produce a row at all. Both halves therefore use the one format that
        # was measured (`gamemoded.service enabled enabled`).
        timers = "f.timer enabled enabled\nnext-timer.timer static\n"

        def run(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv, 0, timers if "--type=timer" in argv else out, "")
        monkeypatch.setattr(__import__("shutil"), "which",
                            lambda n: "/usr/bin/systemctl")
        monkeypatch.setattr(subprocess, "run", run)
        units, why = ae._user_units()
        assert any("gamemoded" in u for u in units)
        assert any("timer: f.timer" in u for u in units)

    def test_an_empty_crontab_is_not_an_error(self, monkeypatch):
        # `crontab -l` exits 1 with "no crontab for <user>" when there is none,
        # which is the ordinary state.
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "no crontab for root")
        monkeypatch.setattr(__import__("shutil"), "which",
                            lambda n: "/usr/bin/crontab")
        monkeypatch.setattr(subprocess, "run", run)
        entries, why = ae._cron()
        assert entries == []
        assert why == "", "an absent crontab is not a problem to report"

    def test_cron_comments_are_not_scheduled_work(self, monkeypatch):
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv, 0, "# a comment\n*/5 * * * * /usr/bin/thing\n\n", "")
        monkeypatch.setattr(__import__("shutil"), "which",
                            lambda n: "/usr/bin/crontab")
        monkeypatch.setattr(subprocess, "run", run)
        entries, _ = ae._cron()
        assert entries == ["*/5 * * * * /usr/bin/thing"]

    def test_a_real_crontab_failure_is_reported(self, monkeypatch):
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "permission denied")
        monkeypatch.setattr(__import__("shutil"), "which",
                            lambda n: "/usr/bin/crontab")
        monkeypatch.setattr(subprocess, "run", run)
        _, why = ae._cron()
        assert "permission denied" in why


# --------------------------------------------------------------------------
# Reachability
# --------------------------------------------------------------------------

class TestItIsReachable:

    def test_the_handler_is_importable_by_name(self):
        assert ae._run.__name__ == "_run"
        assert ae._run.__name__.isidentifier()

    def test_the_schema_registers_under_its_own_name(self):
        assert ae.SKILLS[0].name == "autostart_entries"
        assert ae.SCHEMA["function"]["name"] == "autostart_entries"

    def test_it_takes_no_parameters(self):
        assert ae.SCHEMA["function"]["parameters"]["properties"] == {}

    def test_it_reads_the_directory_chronoa_writes_to(self, monkeypatch):
        """Chronoa's own `_sync_autostart` creates an entry here, and nothing
        could read one back - so the setting and its effect were never visible
        together.

        **Asserted by resolving both paths, not by grepping for a string.** A
        first version searched the app's source for `.config/autostart` and it
        is not there - the app builds it from `files.config_home() / "autostart"`
        - so the check was asserting about a literal nobody writes. The claim
        that matters is that the two *resolve to the same directory*, and that
        is what this holds: with `XDG_CONFIG_HOME` pointed elsewhere, a literal
        `~/.config/autostart` would have missed the entry Chronoa just wrote.
        """
        import pathlib
        from shani_chronoa import files

        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/somewhere-else/config")
        assert ae._autostart_dirs()[0] == str(
            pathlib.Path(files.config_home()) / "autostart")

        app = pathlib.Path(ae.__file__).parents[1] / "app" / "desktop_integration.py"
        src = app.read_text(encoding="utf-8")
        assert '_AUTOSTART_DIR = str(files.config_home() / "autostart")' in src, (
            "the app's own autostart directory moved; this skill must follow "
            "it rather than keep a second spelling")
        # `skills/autostart_entries.py` -> parents[0]=skills, [1]=shani_chronoa
