"""The fwupdmgr stub half of `firmware_updates`, moved with the skill."""

import json
import stat

from shani_sysadmin.skills import firmware_updates as fw


def test_firmware_updates_parse_and_say_when_none(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "fwupdmgr"
    out = tmp_path / "out.json"
    out.write_text(json.dumps({"Devices": [{"Name": "SSD", "Version": "1.0",
                                            "Releases": [{"Version": "1.2", "Urgency": "high", "Summary": "Fixes TRIM"}]}]}))
    stub.write_text(f"#!/bin/sh\ncat {out}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    assert "SSD: 1.0 -> 1.2 (high urgency) - Fixes TRIM" in fw._run({})
    out.write_text(json.dumps({"Devices": []}))
    assert "No firmware updates" in fw._run({})
    out.write_text("No updatable devices")
    assert "could not list updates" in fw._run({})
