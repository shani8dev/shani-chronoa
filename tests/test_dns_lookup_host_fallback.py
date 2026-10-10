"""`dns_lookup` when `dig` is absent: the `host` fallback.

`dns_lookup` hard-refused without `dig`, though `host` ships in the **same
`bind` package on both images** and answers every type the schema offers. A
machine with only `host` got nothing, which is the "answers nothing where
knowing matters most" class this package documents repeatedly.

All outputs below are measured against the real `host` on this box.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import dns_lookup as DL  # noqa: E402

#: Verbatim `host` output, measured.
HOST_A = ("example.com has address 172.66.147.243\n"
          "example.com has address 104.20.23.154\n")
HOST_MX = "example.com mail is handled by 0 .\n"
HOST_PTR = ("1.1.1.1.in-addr.arpa domain name pointer one.one.one.one.\n")
#: **Measured verbatim, including the leading "Host " and the exit status.**
#: `host` prints this and exits **0**, not 1 - so a reader keyed on the exit
#: status sees no error at all. The first fixture here omitted the leading
#: "Host ", which is why the detection looked untested when it was right.
HOST_NXDOMAIN = "Host nosuchdomain.invalid not found: 3(NXDOMAIN)"
HOST_EMPTY = ""


def _fake_host(tmp_path, monkeypatch, stdout, rc=0):
    """A real fake `host` script, so the *parsing* runs against the binary
    rather than against a stub of the function being parsed.

    `dig` is still found on this box, so `which` is patched to say otherwise.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "host"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.exit({rc})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (bindir, os.environ.get("PATH", "")))
    monkeypatch.setattr(DL.shutil, "which",
                        lambda name: "/usr/bin/host" if name == "host" else None)
    monkeypatch.setattr(DL.egress, "privacy_mode_enabled", lambda: False)


def _no_dig_but_host(monkeypatch, rendered=HOST_A, meta=None):
    """`dig` absent, `host` present - the exact situation the fallback is for.

    **`dig` is on this dev box, so a PATH fixture cannot hide it.** The router is
    stubbed instead, which is the same statement the skill makes about its
    environment; the parsing of real `host` output is covered separately against
    the real binary.
    """
    monkeypatch.setattr(DL.shutil, "which",
                        lambda name: "/usr/bin/" + name if name == "host"
                        else None)
    monkeypatch.setattr(DL.egress, "privacy_mode_enabled", lambda: False)
    monkeypatch.setattr(DL, "_host_records",
                        lambda name, rtype: (rendered, meta or {"tool": "host"}))


# --- the chain -----------------------------------------------------------------

def test_dig_is_preferred_when_both_are_present(tmp_path, monkeypatch):
    """`host` is a fallback, not a replacement - `dig` reports TTLs and a status
    word that `host` cannot, and the answer should not lose them while `dig`
    is installed."""
    monkeypatch.setattr(DL.shutil, "which",
                        lambda name: "/usr/bin/" + name if name == "dig"
                        else None)
    monkeypatch.setattr(DL.egress, "privacy_mode_enabled", lambda: False)
    monkeypatch.setattr(DL.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(
                            argv, 0, "example.com.\t300\tIN\tA\t1.2.3.4\n", ""))
    out = DL._run({"name": "example.com", "type": "A"})
    assert "Answered by `host`" not in out, out


def test_host_is_used_when_dig_is_absent(monkeypatch):
    _no_dig_but_host(monkeypatch)
    out = DL._run({"name": "example.com", "type": "A"})
    assert "172.66.147.243" in out, out


def test_a_missing_both_names_the_package(tmp_path, monkeypatch):
    monkeypatch.setattr(DL.shutil, "which", lambda name: None)
    out = DL._run({"name": "example.com", "type": "A"})
    assert "dig" in out
    assert "not installed" in out


# --- the answer is honest about which tool ran --------------------------------

def test_the_answer_names_the_tool_that_answered(tmp_path, monkeypatch):
    """`host`'s output carries no TTL and no status word. Hiding that would let
    a caller read the fallback's answer as `dig`'s."""
    _no_dig_but_host(monkeypatch)
    out = DL._run({"name": "example.com", "type": "A"})
    assert "Answered by `host`" in out
    assert "dig` is not installed" in out


def test_no_ttl_is_claimed_from_host_output(tmp_path, monkeypatch):
    """**The trap.** `dig` prints `example.com. 300 IN A 1.2.3.4` - the 300 is
    a TTL. `host` prints no such number, so a TTL parsed out of a `host` answer
    would be invented."""
    _no_dig_but_host(monkeypatch, HOST_MX)
    out = DL._run({"name": "example.com", "type": "MX"})
    assert "mail is handled by" in out
    # **No TTL value is presented, and the caveat that says so is present.**
    # The first version asserted `"TTL" not in out`, which fails on the honest
    # disclaimer "A TTL ... is not in that output" - a check that rejects the
    # sentence explaining the limit cannot tell a limit from a claim.
    import re as _re
    assert not _re.search(r"\b\d{1,5}\s+IN\s+", out), \
        f"a TTL was parsed out of host output: {out!r}"
    assert "not in that output" in out


def test_dnssec_is_not_claimed_from_the_fallback(tmp_path, monkeypatch):
    """`delv` validates; `host` does not. Saying nothing about the chain is a
    different claim from a validated one, and the section must say which."""
    _no_dig_but_host(monkeypatch)
    out = DL._run({"name": "example.com", "type": "A"})
    assert "DNSSEC: unknown" in out
    assert "fully validated" not in out


# --- the statuses host reports in its text ------------------------------------

def test_nxdomain_is_a_status_not_an_answer(tmp_path, monkeypatch):
    """**`host` reports NXDOMAIN as a line of text**, not in a parsed status
    field. The first version carried it through as an answer, so "the name does
    not exist" was reported in the same shape as an address - the conflation
    this module exists to avoid."""
    _no_dig_but_host(monkeypatch, "", {"status": "NXDOMAIN"})
    out = DL._run({"name": "nosuchdomain.invalid", "type": "A"})
    assert "does not exist" in out
    assert "not a failure to look" in out
    # And not presented as a record.
    assert "has address" not in out


def test_an_empty_answer_is_not_claimed_as_absence(tmp_path, monkeypatch):
    """An empty result and a name that does not exist are different facts, and
    neither may be dressed as the other."""
    _no_dig_but_host(monkeypatch, "", {})
    out = DL._run({"name": "quiet.example", "type": "A"})
    assert "No answer for" in out
    assert "does not exist" not in out


# --- the parsing itself --------------------------------------------------------

def test_a_reverse_lookup_is_parsed_and_punctuated():
    """`host`'s pointer records are fully qualified and end in a dot, so the
    first version printed "The name is: one.one.one.one.."
    """
    out = DL._host_describe(HOST_PTR.splitlines(), "1.1.1.1", "PTR")
    assert out == "The name is: one.one.one.one.", out


def test_several_a_records_are_all_reported():
    out = DL._host_describe(HOST_A.splitlines(), "example.com", "A")
    assert "172.66.147.243" in out and "104.20.23.154" in out


def test_nxdomain_is_detected_by_the_real_function(tmp_path, monkeypatch):
    """**Through the real function, not a stub of it.** The routing test above
    injects a status, so it proves the *answer* handles one; this proves the
    *parser* produces one. A mutation of the detection line leaves that test
    green and this one red - which is the difference between testing the
    router and testing the reader.
    """
    _fake_host(tmp_path, monkeypatch, HOST_NXDOMAIN, rc=0)
    rendered, meta = DL._host_records("nosuchdomain.invalid", "A")
    assert rendered == "", rendered
    assert meta == {"status": "NXDOMAIN"}, meta


def test_a_normal_answer_is_not_mistaken_for_a_status(tmp_path, monkeypatch):
    _fake_host(tmp_path, monkeypatch, HOST_A, rc=0)
    rendered, meta = DL._host_records("example.com", "A")
    # Joined lines, so the fixture's trailing newline is not part of it.
    assert rendered.splitlines() == HOST_A.splitlines(), rendered
    assert "status" not in meta, meta


# --- the real binary -----------------------------------------------------------

@pytest.mark.skipif(not Path("/usr/bin/host").exists()
                    and not Path("/bin/host").exists(),
                    reason="host is not on this box")
def test_the_real_host_output_parses():
    """Against the real binary, not only a fixture - the shapes above were
    measured from it and a fixture agrees with itself by construction."""
    rendered, meta = DL._host_records("example.com", "A")
    assert rendered and meta.get("tool") == "host", (rendered, meta)
    out = DL._host_describe(rendered.splitlines(), "example.com", "A")
    assert "has address" in out


def test_privacy_mode_still_refuses():
    """The fallback sends the same name to the same resolver, so the same gate
    applies to it - a fallback that bypassed the privacy switch would be a hole
    wearing a workaround."""
    monkey = None
    import shani_chronoa.skills.dns_lookup as module
    old = module.egress.privacy_mode_enabled
    module.egress.privacy_mode_enabled = lambda: True
    try:
        out = module._run({"name": "example.com", "type": "A"})
    finally:
        module.egress.privacy_mode_enabled = old
    assert "Privacy mode" in out
