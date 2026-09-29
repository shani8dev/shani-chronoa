"""Reading the desktop over AT-SPI instead of xdotool.

`list_windows` shells out to `xdotool`, which is X11-only and absent under
Wayland - so on a Wayland session the honest answer today is "I cannot see your
windows". This sense reads the same information over the accessibility bus,
which is D-Bus and reachable under both.

The three things pinned below are the ones that could quietly go wrong:

- **Unavailable is not empty.** A dead bus must never read as a desktop with
  nothing on it. The user can look at their own screen and check.
- **Reading only.** AT-SPI can also press buttons and type. That is not here.
- **Truncation is disclosed.** A capped list that looks complete is a
  plausible-looking wrong answer, which is the failure this codebase treats as
  worse than an honest unknown.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import config as config_mod  # noqa: E402
from shani_chronoa.senses import (  # noqa: E402
    SENSITIVITY_PERSONAL,
    Sense,
    discover_senses,
)
from shani_chronoa.senses import accessibility as A  # noqa: E402


@pytest.fixture(autouse=True)
def _off():
    """Every test starts from the default: this sense is off."""
    A._ORIGIN_SENTINEL = None
    yield


class TestItIsASenseLikeAnyOther:
    def test_it_registers(self):
        assert "accessibility" in discover_senses()

    def test_it_is_off_by_default(self):
        """Application names and window titles say what the user is working on."""
        assert A._SENSE.name == "accessibility"
        assert A._SENSE.run is A._run

    def test_it_is_personal_not_public(self):
        assert A._SENSE.sensitivity == SENSITIVITY_PERSONAL

    def test_it_is_never_polled(self):
        """Perceiving on a timer, without being asked, is not acceptable here."""
        assert A._SENSE.poll_interval is None

    def test_it_declares_a_consent_key(self):
        assert config_mod._SENSE_CONSENT_KEYS.get("accessibility") == \
            "accessibility-sense-enabled"

    def test_the_key_exists_in_the_schema_and_defaults_false(self):
        from shani_chronoa.config import ChronoaConfig
        key = "accessibility-sense-enabled"
        assert key in ChronoaConfig()._valid_keys, (
            "the key is not in the compiled schema, so the sense could never "
            "be switched on"
        )


class TestItFailsClosed:
    def test_it_refuses_when_the_key_is_off(self, monkeypatch):
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(False))
        out = A._run({})
        assert "Not reading" in out

    def test_the_refusal_names_the_setting_to_turn_on(self):
        """Otherwise the user is told no without being told how to change it.

        Uses the real config rather than a fake: `sense_allowed_reason` is the
        thing that names the key, so a hand-written stand-in would have been
        testing my own stub.
        """
        assert "accessibility-sense-enabled" in A._run({})


class TestUnavailableIsNotEmpty:
    """The distinction this whole module is built around."""

    def test_a_dead_bus_does_not_report_an_empty_desktop(self, monkeypatch):
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        def dead():
            raise A._Unavailable("the accessibility bus did not answer")
        monkeypatch.setattr(A, "_desktop", dead)
        out = A._run({})
        assert "could not look" in out

    def test_it_never_says_nothing_is_open_when_it_could_not_look(self, monkeypatch):
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        def dead():
            raise A._Unavailable("the accessibility bus did not answer")
        monkeypatch.setattr(A, "_desktop", dead)
        low = A._run({}).lower()
        # "no applications" would be a checkable lie: the user can look.
        assert "no applications" not in low
        assert "0 application" not in low

    def test_a_missing_typelib_is_unavailable_not_a_crash(self, monkeypatch):
        """No `gi` at all must not take the sense down.

        `_desktop` translates the import failure into `_Unavailable`, so the
        caller reports "could not look" instead of an ImportError escaping
        through the percept pipeline.
        """
        import builtins
        real_import = builtins.__import__

        def blocked(name, *a, **kw):
            if name.startswith("gi"):
                raise ImportError("no gi here")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", blocked)
        with pytest.raises(A._Unavailable) as caught:
            A._desktop()
        assert "not installed" in str(caught.value)


class TestTheReadItself:
    def test_it_reports_what_the_bus_says(self, monkeypatch):
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        monkeypatch.setattr(A, "read_applications",
                            lambda: ([("firefox", "application", 3)], False))
        out = A._run({})
        assert "firefox" in out
        assert "3 window" in out

    def test_one_unreadable_app_does_not_fail_the_whole_read(self, monkeypatch):
        """A wedged application must not take the bus down with it."""
        class Node:
            def __init__(self, n, boom=False):
                self.n, self.boom = n, boom
            def get_name(self):
                if self.boom:
                    raise RuntimeError("app is hung")
                return self.n
            def get_role_name(self):
                return "application"
            def get_child_count(self):
                return 0

        class Desktop:
            def get_child_count(self):
                return 3
            def get_child_at_index(self, i):
                return Node(f"app{i}", boom=(i == 1))

        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        monkeypatch.setattr(A, "_desktop", lambda: Desktop())
        out = A._run({})
        assert "app0" in out and "app2" in out
        assert "app1" not in out


class TestTruncationIsDisclosed:
    def test_a_capped_list_says_so(self, monkeypatch):
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        many = [(f"app{i}", "application", 0) for i in range(A._MAX_APPS + 5)]
        monkeypatch.setattr(A, "read_applications", lambda: (many, True))
        out = A._run({})
        assert "5 more" in out

    def test_a_short_list_says_nothing_about_withholding(self, monkeypatch):
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        monkeypatch.setattr(A, "read_applications",
                            lambda: ([("only", "application", 0)], False))
        assert "more" not in A._run({}).lower()

    def test_the_header_still_counts_everything(self, monkeypatch):
        """So a caller can tell the difference between 45 seen and 40 shown."""
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        many = [(f"app{i}", "application", 0) for i in range(A._MAX_APPS + 5)]
        monkeypatch.setattr(A, "read_applications", lambda: (many, True))
        assert str(A._MAX_APPS + 5) in A._run({}).splitlines()[0]


class TestItOnlyReads:
    """The actuation half of AT-SPI is deliberately not here."""

    def test_the_module_imports_no_input_synthesis(self):
        source = (_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
                  / "senses" / "accessibility.py").read_text()
        for forbidden in ("generate_action", "Action.do", "component.grab_focus"):
            assert forbidden not in source, (
                f"{forbidden} is an input-injection primitive and has no "
                f"verified portable interface here"
            )


def _config_returning(allowed: bool):
    class _C:
        def sense_allowed(self, name):
            return allowed

        def sense_allowed_reason(self, name):
            return "" if allowed else "the accessibility sense is turned off"
    return _C


class TestTheWalkIsBounded:
    """`_TIMEOUT_SECONDS` was declared with a justification and never applied.

    The constant sat next to "A tree walk that has not returned promptly is worse
    than no tree: it holds a D-Bus round trip open against a process that may be
    wedged" - and every D-Bus call was unbounded. A wedged application on the bus
    hung the sense for as long as the process lived, which is the failure the
    comment claimed to be preventing.

    The bound is a plain daemon thread rather than a `ThreadPoolExecutor`, for
    the reason `senses/filesystem.py` already recorded here: `concurrent.futures`
    registers an `atexit` hook that joins every worker it ever started, which was
    measured turning a 5-second refusal into a 60-second process.
    """

    def test_a_wedged_walk_gives_up_instead_of_hanging(self, monkeypatch):
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        monkeypatch.setattr(A, "_TIMEOUT_SECONDS", 0.3)

        def wedged(limit=10):
            time.sleep(30)
            return [], False

        monkeypatch.setattr(A, "read_applications", wedged)
        started = time.monotonic()
        out = A._run({})
        elapsed = time.monotonic() - started
        assert elapsed < 5, f"the call took {elapsed:.1f}s; the budget was 0.3s"
        assert "did not return within" in out

    def test_a_timeout_never_claims_nothing_is_open(self, monkeypatch):
        # The load-bearing honesty property, under the new failure mode. An empty
        # list means "no applications are running", which is a claim about the
        # user's screen; a timeout is an admission of not looking.
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        monkeypatch.setattr(A, "_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(A, "read_applications",
                            lambda limit=10: (time.sleep(30), ([], False))[1])
        out = A._run({})
        assert "not that nothing is open" in out
        assert "reports no applications" not in out

    def test_the_timeout_is_named_so_the_user_can_act_on_it(self, monkeypatch):
        # Distinct from "the desktop is not reporting through AT-SPI": one is a
        # bus that is absent, the other a bus that is present and wedged, and the
        # things a user would try are different.
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        monkeypatch.setattr(A, "_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(A, "read_applications",
                            lambda limit=10: (time.sleep(30), ([], False))[1])
        out = A._run({})
        assert str(A._TIMEOUT_SECONDS) in out
        assert "stopped answering" in out

    def test_the_worker_thread_is_a_daemon(self, monkeypatch):
        # A non-daemon thread blocked in D-Bus is joined at interpreter exit, so
        # the sense's budget would bound the caller while still hanging the whole
        # process on the way out.
        before = {t for t in threading.enumerate()}
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        monkeypatch.setattr(A, "_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(A, "read_applications",
                            lambda limit=10: (time.sleep(30), ([], False))[1])
        A._run({})
        new = [t for t in threading.enumerate() if t not in before]
        assert new, "the walk did not run on a worker thread at all"
        assert all(t.daemon for t in new), "a non-daemon thread was left behind"

    def test_a_fast_walk_is_untouched(self, monkeypatch):
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        monkeypatch.setattr(A, "read_applications", lambda limit=10: ([("App", "x", 1)], False))
        out = A._run({})
        assert "App" in out
        assert "did not return within" not in out

    def test_a_worker_exception_is_re_raised_on_the_callers_thread(self, monkeypatch):
        # The result crosses threads through a Queue, so an exception raised in the
        # walk has to travel with the value rather than vanish into the thread.
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))

        def boom(limit=10):
            raise A._Unavailable("the accessibility bus did not answer (test)")

        monkeypatch.setattr(A, "read_applications", boom)
        out = A._run({})
        assert "did not answer (test)" in out, "the worker's exception was lost"

    def test_a_bus_that_raises_is_not_reported_as_a_timeout(self, monkeypatch):
        monkeypatch.setattr(A, "ChronoaConfig", _config_returning(True))
        monkeypatch.setattr(A, "read_applications",
                            lambda limit=10: (_ for _ in ()).throw(
                                A._Unavailable("not reporting through AT-SPI")))
        out = A._run({})
        assert "not reporting through AT-SPI" in out
        assert "did not return within" not in out
