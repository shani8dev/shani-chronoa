"""Tests for `polkit_policy`'s `about=` lookup, which answers the question the
summary cannot.

The sense was already good: it read the real policy files, classified all five
rule values, and kept UNKNOWN apart from "nothing is allowed". What it could
not do was answer **"can I mount a USB stick without a password?"** - it gave a
count. Measured on a real ShaniOS GNOME image: **491 actions, 153 of them
needing no password**. A person asking about their own machine needs one row,
not 491, and the ones they ask about by name (`mount`, `power`, `wifi`, `upower`)
are not the ones sorted first.

Every fixture below is a **real action from a real image**, because the point of
the lookup is which of several similar actions matches:

| search | what it found on `@blue` |
|---|---|
| `mount` | **17** actions - including `mount-image` (`yes`) beside `mount-untrusted-image` (`auth_admin`) |
| `power` | 16, starting with one that is **refused outright** (`no`) |
| `upower` | 5 |

The first row is why the answer lists every match rather than taking the first:
the same word means "no password" for one action and "an administrator's
password" for its neighbour.
"""

from __future__ import annotations


from shani_chronoa.senses import polkit_policy as pp


def _action(aid, desc, rule, file="x.policy"):
    return {"id": aid, "description": desc, "allow_active": rule,
            "kind": pp._classify(rule), "file": file}


# --- real actions from a real @blue image ----------------------------------
MOUNT_IMAGE = _action(
    "io.systemd.mount-file-system.mount-image",
    "Allow mounting of file system image", "yes",
    "io.systemd.mount-file-system.mount-image.policy")
MOUNT_UNTRUSTED = _action(
    "io.systemd.mount-file-system.mount-untrusted-image",
    "Allow mounting of untrusted file system image", "auth_admin",
    "io.systemd.mount-file-system.mount-untrusted-image.policy")
NM_SLEEP_WAKE = _action(
    "org.freedesktop.NetworkManager.sleep-wake",
    "Put NetworkManager to sleep or wake it up (should only be used by system "
    "power management)", "no", "org.freedesktop.NetworkManager.policy")
POWER_OFF_DRIVE = _action(
    "org.freedesktop.udisks2.power-off-drive",
    "Power off drive", "yes", "org.freedesktop.udisks2.policy")
BATTERY_LIMIT = _action(
    "org.freedesktop.UPower.BatteryLimit",
    "Enable battery charging limit", "auth_admin_keep",
    "org.freedesktop.upower.policy")

ALL = [MOUNT_IMAGE, MOUNT_UNTRUSTED, NM_SLEEP_WAKE, POWER_OFF_DRIVE,
       BATTERY_LIMIT]


# --------------------------------------------------------------------------
# It answers the question, in the words it was asked
# --------------------------------------------------------------------------

class TestTheLookupAnswers:

    def test_an_exact_id_finds_its_own_action(self):
        out = pp._lookup(ALL, "org.freedesktop.upower.BatteryLimit")
        assert BATTERY_LIMIT["id"] in out
        assert "ADMINISTRATOR" in out

    def test_a_plain_word_finds_actions_by_description(self):
        # People ask by what a thing is called, not by its action id.
        out = pp._lookup(ALL, "battery")
        assert BATTERY_LIMIT["id"] in out

    def test_the_file_name_is_searchable_too(self):
        # "which package is this from" is part of the question and the id does
        # not always say.
        out = pp._lookup(ALL, "NetworkManager.policy")
        assert NM_SLEEP_WAKE["id"] in out

    def test_the_search_is_case_insensitive(self):
        """Both spellings must find the same action. The query itself is echoed
        back verbatim - quoting what the person typed is more useful than
        normalising it - so the assertion is on the action found, not on the
        whole string being equal.
        """
        assert "BatteryLimit" in pp._lookup(ALL, "BATTERY")
        assert "BatteryLimit" in pp._lookup(ALL, "battery")
        assert "battery" in pp._lookup(ALL, "battery")
        assert "BATTERY" in pp._lookup(ALL, "BATTERY")

    def test_every_match_is_listed_not_just_the_first(self):
        """**The whole reason for this parameter.** Measured on `@blue`,
        searching `mount` returns 17 actions, and two of them are the same
        operation with opposite answers: `mount-image` is `yes` (no password)
        while `mount-untrusted-image` is `auth_admin`. Taking the first match
        answers "no password needed" about an action that needs an
        administrator - the confident wrong answer in its plainest form.
        """
        out = pp._lookup(ALL, "mount")
        assert MOUNT_IMAGE["id"] in out
        assert MOUNT_UNTRUSTED["id"] in out
        assert "2 installed action(s) match" in out

    def test_the_headline_counts_the_matches(self):
        out = pp._lookup(ALL, "mount")
        assert "2 installed action(s) match" in out
        single = pp._lookup(ALL, MOUNT_IMAGE["id"])
        assert "This action matches" in single

    def test_a_partial_id_still_matches(self):
        # The id is a dotted string; a person will type the tail of it.
        assert BATTERY_LIMIT["id"] in pp._lookup(ALL, "BatteryLimit")

    def test_too_many_matches_are_counted_not_silently_dropped(self):
        """Measured on a real image, "mount" returns **17** actions. Showing
        eight and silently stopping reads as "that is all of them" - the
        count-shaped wrong answer, so the number left out is stated.
        """
        # Two real actions plus twelve more = 14 matching, 8 shown, 6 omitted.
        many = [MOUNT_IMAGE, MOUNT_UNTRUSTED] + [
            _action(f"x.mount.{i}", f"Mount thing {i}", "yes") for i in range(12)]
        out = pp._lookup(many, "mount")
        assert out.count("-> ") == 8, "eight rows are shown, the rest are not"
        assert "6 more that match" in out, (
            "a truncated list must say how much was left out, or it reads "
            "as complete")


# --------------------------------------------------------------------------
# The five outcomes are five different pieces of news
# --------------------------------------------------------------------------

class TestTheFiveAnswers:

    def test_yes_says_no_password(self):
        out = pp._lookup(ALL, MOUNT_IMAGE["id"])
        assert "needs NO password" in out
        assert "[rule: yes]" in out

    def test_admin_says_you_cannot_do_it_alone(self):
        out = pp._lookup(ALL, MOUNT_UNTRUSTED["id"])
        assert "ADMINISTRATOR" in out
        assert "cannot do it alone" in out

    def test_never_says_refused_to_everyone(self):
        # `no` is a real answer and a very different one from a prompt.
        out = pp._lookup(ALL, NM_SLEEP_WAKE["id"])
        assert "REFUSED to everyone" in out

    def test_self_says_your_own_password(self):
        action = _action("x.self", "Thing", "auth_self")
        out = pp._lookup([action], "x.self")
        assert "OWN password" in out

    def test_an_unclassifiable_rule_is_unknown_not_guessed(self):
        action = _action("x.odd", "Thing", "some_future_rule")
        out = pp._lookup([action], "x.odd")
        assert "could not classify" in out
        assert "unknown" in out

    def test_the_rule_is_shown_so_the_wording_can_be_checked(self):
        # The classification is this module's reading; the rule itself is the
        # file's, and a reader can compare the two.
        assert "[rule: auth_admin_keep]" in pp._lookup(ALL, "battery")


# --------------------------------------------------------------------------
# No match is not permission
# --------------------------------------------------------------------------

class TestNoMatchIsNotPermission:

    def test_a_name_that_matches_nothing_does_not_grant_it(self):
        """**The failure this must not have.** "Nothing installed matches X"
        read as "you can do X" is the confident wrong answer the sense's own
        UNKNOWN branch exists to avoid - an action with no policy is either one
        that needs no authorisation or one that is not installed, and the
        policy files cannot tell those apart.
        """
        out = pp._lookup(ALL, "zzznothing")
        assert "Nothing installed matches" in out
        assert "does not mean you are allowed" in out
        assert "cannot tell those apart" in out

    def test_a_no_match_still_offers_the_way_forward(self):
        out = pp._lookup(ALL, "zzznothing")
        assert "detail=true" in out

    def test_the_count_of_free_actions_is_in_the_no_match_answer(self):
        # Two of the five fixtures are `yes`: mount-image and power-off-drive.
        assert "2 of them need no password" in pp._lookup(ALL, "zzznothing")

    def test_an_empty_machine_has_no_match_rather_than_a_crash(self):
        assert "Nothing installed matches" in pp._lookup([], "anything")


# --------------------------------------------------------------------------
# Wiring: the schema declares it and the handler reads it
# --------------------------------------------------------------------------

class TestItIsWired:

    def test_the_parameter_is_declared(self):
        assert "about" in pp._SCHEMA["function"]["parameters"]["properties"]

    def test_the_description_names_the_question_it_answers(self):
        about = pp._SCHEMA["function"]["parameters"]["properties"]["about"]
        assert "password" in about["description"].lower()

    def test_the_sense_description_points_at_it(self):
        assert "about" in pp._SCHEMA["function"]["description"]

    def test_the_handler_consults_it(self, monkeypatch):
        # A declared argument no code reads is the "an option whose label
        # promises something the program does not do" failure this repository
        # records for the tray icon - and for `detail` before it.
        monkeypatch.setattr(pp, "read_actions", lambda: ALL)
        out = pp._run({"about": "battery"})
        assert BATTERY_LIMIT["id"] in out
        assert "5 actions installed" not in out, (
            "about= must replace the summary, not precede it")

    def test_detail_still_works_alongside_it(self, monkeypatch):
        monkeypatch.setattr(pp, "read_actions", lambda: ALL)
        assert "Every installed action" in pp._run({"detail": True})
        # The summary's own wording: "2 of 5 installed polkit actions need no
        # authentication". Asserted against what the sense actually says rather
        # than a phrase written for this test.
        assert "of 5 installed polkit actions" in pp._run({})
        assert "REFUSED" in pp._run({"detail": True}) or "never" in pp._run({})

    def test_about_wins_over_detail_when_both_are_given(self, monkeypatch):
        monkeypatch.setattr(pp, "read_actions", lambda: ALL)
        out = pp._run({"about": "battery", "detail": True})
        assert BATTERY_LIMIT["id"] in out
        assert "Every installed action" not in out

    def test_a_blank_about_falls_back_to_the_summary(self, monkeypatch):
        monkeypatch.setattr(pp, "read_actions", lambda: ALL)
        assert "of 5 installed polkit actions" in pp._run({"about": "   "})