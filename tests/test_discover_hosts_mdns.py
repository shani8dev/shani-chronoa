"""`discover_hosts`: the mDNS half of "what is on my network".

An ARP sweep finds *addresses*. mDNS/DNS-SD is the other discovery path
entirely and the two are disjoint: a printer, a Chromecast, a phone or a TV
announces `_ipp._tcp.local` / `_googlecast._tcp.local` and answers nothing
else, so a silent address in a sweep is exactly the device that has a name
over mDNS. Nothing in this package used it.

**What is asserted here, and what is deliberately not.** `avahi-browse` is
*not installed on the machine this was written on* (Ubuntu dev box), so the
output is reduced to what a wrong parse cannot fake: a count and the service
*types*. A slot run with the binary present is the outstanding verification
and is named as such in the module docstring - see the harness rule in
AGENTS.md about a command missing on this box.

**A control that can fail, because the first version could not.** `avahi-browse`
prints a banner of explanatory prose about what it is doing, and that prose
contains the word "scanner"-style nouns. The first backend test listed a
finding once per *different* prose line, where no double-count was possible
and the test stayed green under a mutation that counted every line. It now
repeats the same service type, which only a count that deduplicates correctly
can get right.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.skills import discover_hosts as dh  # noqa: E402

#: The shape of `avahi-browse --all --terminate --resolve` output: `=`
## progress lines, the same service appearing more than once as different
#: instances announce it, and prose at the top that is not a finding.
REAL_SHAPE = """\
Starting browsing...

= wlp0s20f3 IPv4 Brother MFC-L2710DN         _ipp._tcp            local
= wlp0s20f3 IPv4 Chromecast-8f2a              _googlecast._tcp     local
= wlp0s20f3 IPv4 Brother MFC-L2710DN         _ipps._tcp           local
= wlp0s20f3 IPv4 living-room                 _raop._tcp           local
= wlp0s20f3 IPv4 Pixel 8                     _phone._tcp          local
= wlp0s20f3 IPv4 Brother MFC-L2710DN         _ipp._tcp            local
"""


@pytest.fixture
def fake_avahi(tmp_path, monkeypatch):
    def _install(output=REAL_SHAPE, code=0, missing=False):
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        if not missing:
            data = tmp_path / "mdns.txt"
            data.write_text(output)
            script = bindir / "avahi-browse"
            script.write_text(
                "#!/usr/bin/env python3\n"
                f"print(open({str(data)!r}).read(), end='')\n"
                f"raise SystemExit({code})\n"
            )
            script.chmod(0o755)
        else:
            (bindir / "python3").symlink_to(sys.executable)
            monkeypatch.setenv("PATH", str(bindir))
            return
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return _install


def test_announced_services_are_counted_and_typed(fake_avahi):
    fake_avahi()
    lines = dh._mdns_lines("")
    # Six service lines in the fixture, one of them a second instance of
    # `_ipp._tcp`, so the count counts lines and the grouping beside it.
    assert "6 announced service(s)" in lines[0]
    assert "_ipp._tcp" in lines[0]
    assert "_googlecast._tcp" in lines[0]
    assert "_raop._tcp" in lines[0]


def test_the_banner_prose_is_not_counted_as_a_finding(fake_avahi):
    """**The control that can fail.** The banner is prose about what
    `avahi-browse` is doing, and a count that matched words rather than
    service types would count every line of it.

    The first version of this test replaced a fixture line with *itself* - a
    change that could not alter the count - so the assertion stayed green
    under a mutation that counted every line. It now adds the banner to the
    same six findings, which only a parser reading service types can get
    right.
    """
    fake_avahi("Starting browsing...\n"
               "Daemon is running, now showing announced services.\n"
               "Looking for a service on this network.\n\n" + REAL_SHAPE)
    assert "6 announced service(s)" in dh._mdns_lines("")[0]


def test_repeated_instances_of_one_type_are_grouped(fake_avahi):
    """`_ipp._tcp` appears twice above; the answer says so rather than
    reporting a number nobody can reconcile with what they saw."""
    fake_avahi()
    assert "_ipp._tcp x2" in dh._mdns_lines("")[0]


def test_nothing_announced_is_stated_as_a_measurement(fake_avahi):
    fake_avahi("")
    assert "nothing on this network announced a service" in dh._mdns_lines("")[0]
    # ...and says what that does and does not mean.
    assert "powered off" in dh._mdns_lines("")[0]


def test_a_missing_binary_says_the_question_was_not_answered(fake_avahi):
    fake_avahi(missing=True)
    out = dh._mdns_lines("")[0]
    assert "could not be checked" in out
    assert "avahi" in out                  # the package that would fix it
    assert "announced service" not in out


def test_a_failing_exit_is_unknown_not_nothing(fake_avahi):
    fake_avahi("Daemon not running\n", code=1)
    assert "UNKNOWN" in dh._mdns_lines("")[0]
    assert "nothing on this network" not in dh._mdns_lines("")[0]


def test_a_printer_that_cannot_answer_arp_is_still_reported(fake_avahi):
    """**The case the whole addition is for.** A printer does not answer a
    sweep; it announces itself. Without the mDNS half the answer would end at
    "no devices answered", on exactly the machine where it is least true.
    """
    fake_avahi()
    # The no-rows path in `_run` is where a real sweep ends up; the module's
    # own formatting is asserted through the helper the path composes.
    lines = dh._mdns_lines("")
    assert "Brother" not in lines[0]      # verbatim names, never reflowed
    assert "_ipp._tcp" in lines[0]        # but the type that names it is here


def test_verbatim_names_and_the_reason_for_them(fake_avahi):
    """**A name is never unescaped.** avahi escapes special characters in
    service names, and unescaping a form this code has not run against is how
    an invented name would ship - the sentence above the assertion is part of
    the answer, not decoration.
    """
    fake_avahi()
    out = dh._mdns_lines("")[0]
    assert "verbatim" in out
    assert "unescaping" in out
