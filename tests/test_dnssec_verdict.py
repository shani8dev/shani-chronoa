"""`dns_lookup`'s DNSSEC half, from `delv`.

`dig` cannot answer this question at all. `dig +dnssec` asks for DNSSEC
records and shows the RRSIGs - the evidence - while **`delv` validates the chain
and states the outcome**, its first line being the verdict. Measured on this
machine against a real signed domain:

    ; fully validated
    example.com.  239 IN A  104.20.23.154

That is worth having because **the two halves of a DNSSEC failure look
identical from `dig`**: a name that was never signed and a name whose
signature does not verify both come back as "some answers, plus some RRSIGs".
One is expected for most domains; the other means the answer is being forged
or a key is broken.

**An absent verdict is reported as unknown, never as a clean one.** A missing
`delv` and a failing one are different from an unsigned domain, and the
distinction is the whole value of the section.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import dns_lookup as DL  # noqa: E402

#: Real `delv example.com A`, captured on this machine.
VALIDATED = """; fully validated
example.com.		239	IN	A	104.20.23.154
example.com.		239	IN	RRSIG	A 13 2 300 20261011065115 example.com. oEwN4JYfOXfxX ltg==
"""

#: The same tool on a name whose chain does not verify.
BOGUS = """; validation failure
example.com.		239	IN	A	104.20.23.154
"""


@pytest.fixture
def offline(monkeypatch):
    """Privacy off and egress silenced: a DNS query leaves the machine, so the
    existing gate must be satisfied before these tests say anything."""
    monkeypatch.setattr(DL.egress, "privacy_mode_enabled", lambda: False)
    monkeypatch.setattr(DL.egress, "record", lambda *a, **k: None)


def _fake_delv(tmp_path, monkeypatch, stdout=VALIDATED, code=0, stderr=""):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    data = tmp_path / "delv.out"
    data.write_text(stdout)
    script = bindir / "delv"
    script.write_text(
        "#!/usr/bin/env python3\n"
        f"import sys\n"
        f"sys.stdout.write(open({str(data)!r}).read())\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({code})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def _fake_dig(tmp_path, monkeypatch, name="example.com", status="NOERROR"):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "dig"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.write(';; ->>HEADER<<- opcode: QUERY, status: "
        f"{status}, id: 1\\n"
        ";; flags: qr rd ra; QUERY: 1, ANSWER: 1\\n\\n"
        ";; ANSWER SECTION:\\n"
        f"{name}.\t300\tIN\tA\t104.20.23.154\\n')\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def test_a_validated_chain_is_reported_as_validated(tmp_path, monkeypatch, offline):
    """The real sentence, from the real tool's first line."""
    _fake_dig(tmp_path, monkeypatch)
    _fake_delv(tmp_path, monkeypatch)
    out = DL._run({"name": "example.com", "type": "A"})
    assert "DNSSEC: fully validated" in out
    assert "cannot have been altered in transit" in out


def test_a_validation_failure_is_not_reported_as_validated(tmp_path, monkeypatch,
                                                          offline):
    """The whole point: `dig` looks identical in both cases, so the verdict
    has to come from somewhere else or the section is decoration.
    """
    _fake_dig(tmp_path, monkeypatch)
    _fake_delv(tmp_path, monkeypatch, stdout=BOGUS)
    out = DL._run({"name": "example.com", "type": "A"})
    assert "validation failure" in out
    assert "fully validated" not in out


def test_the_address_answer_is_still_there_alongside_it(tmp_path, monkeypatch,
                                                       offline):
    """A section that replaces the answer would be a regression: the address
    is the question that was asked.
    """
    _fake_dig(tmp_path, monkeypatch)
    _fake_delv(tmp_path, monkeypatch)
    out = DL._run({"name": "example.com", "type": "A"})
    assert "104.20.23.154" in out
    assert "DNS lookup of example.com" in out


def test_a_missing_delv_is_unknown_not_validated(tmp_path, monkeypatch, offline):
    """**An absent verdict must never read as a clean one.** A machine without
    `delv` would otherwise report every lookup as securely signed.

    The stub dir has to *shadow* a real `delv` rather than merely omit one, so
    it is built with `shutil.which`'s own answer removed from the PATH this
    test uses. My first version renamed only the files it had created and left
    this box's real `/usr/bin/delv` reachable, so the test asserted on an
    answer no machine without the tool would ever give.
    """
    import shutil as real_shutil

    _fake_dig(tmp_path, monkeypatch)
    stub = tmp_path / "bin"
    real_delv = real_shutil.which("delv")
    if real_delv:
        (tmp_path / "delv.hidden").write_text(real_delv)
    bindir = tmp_path / "onlydig"
    bindir.mkdir()
    for item in stub.iterdir():
        item.rename(bindir / item.name)

    original_which = real_shutil.which

    def which(name):
        return None if name == "delv" else original_which(name)

    monkeypatch.setattr(DL.shutil, "which", which)
    assert DL.shutil.which("delv") is None
    out = DL._run({"name": "example.com", "type": "A"})
    assert "DNSSEC: unknown" in out
    assert "Nothing is claimed" in out


def test_a_failing_delv_is_unknown_with_its_reason(tmp_path, monkeypatch, offline):
    _fake_dig(tmp_path, monkeypatch)
    _fake_delv(tmp_path, monkeypatch, stdout="", code=1,
               stderr="verification failed\n")
    out = DL._run({"name": "example.com", "type": "A"})
    assert "DNSSEC: unknown" in out or "validation" in out


def test_privacy_mode_still_refuses_the_whole_lookup(monkeypatch):
    """The DNSSEC section must not become a way to ask about a name while the
    gate is shut - it is appended inside the same gated call, and this is the
    test that would notice if it were not.
    """
    monkeypatch.setattr(DL.egress, "privacy_mode_enabled", lambda: True)
    out = DL._run({"name": "example.com", "type": "A"})
    assert "Privacy mode is on" in out
    assert "DNSSEC" not in out