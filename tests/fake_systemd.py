"""A logging fake of `systemd-run` and `systemctl` for the timer and alarm tests.

**No test may create a real timer in the developer's `systemd --user` session.**
These fakes go first on PATH, write every argv they receive as one JSON line,
and exit 0 unless an environment switch asks them to fail the way systemd does:

- FAKE_RUN_FAIL=1     systemd-run exits 1 ("Unit ... already exists")
- FAKE_STOP_FAIL=1    systemctl stop exits 1 ("Access denied")
- FAKE_OFFLINE=1      systemctl is-system-running prints "offline", exits 1
- FAKE_INACTIVE=a,b   systemctl is-active prints "inactive" for units containing a or b
"""

import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

_SCRIPT = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    with open(os.environ["FAKE_SYSTEMD_LOG"], "a") as log:
        log.write(json.dumps(sys.argv) + "\\n")
    name, args = os.path.basename(sys.argv[0]), sys.argv[1:]
    if name == "systemd-run":
        if os.environ.get("FAKE_RUN_FAIL"):
            print("Failed to start transient timer unit: Unit already exists.", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    if "is-system-running" in args:
        if os.environ.get("FAKE_OFFLINE"):
            print("offline")
            sys.exit(1)
        print("running")
        sys.exit(0)
    if "is-active" in args:
        lost = [x for x in os.environ.get("FAKE_INACTIVE", "").split(",") if x]
        for unit in args[args.index("is-active") + 1:]:
            print("inactive" if any(x in unit for x in lost) else "active")
        sys.exit(0)
    if "stop" in args and os.environ.get("FAKE_STOP_FAIL"):
        print("Failed to stop unit: Access denied", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
""")


class FakeSystemd:
    def __init__(self, log: Path):
        self.log = log

    def calls(self, program: str = "") -> list:
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]
        return [r for r in rows if not program or os.path.basename(r[0]) == program]

    def runs(self) -> list:
        return self.calls("systemd-run")

    def stops(self) -> list:
        return [r for r in self.calls("systemctl") if "stop" in r]


def install(tmp_path: Path, monkeypatch) -> FakeSystemd:
    bin_dir = tmp_path / "fake-systemd-bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("systemd-run", "systemctl"):
        script = bin_dir / name
        script.write_text(_SCRIPT.format(python=sys.executable))
        script.chmod(0o755)
    log = tmp_path / "systemd-argv.log"
    monkeypatch.setenv("FAKE_SYSTEMD_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    for switch in ("FAKE_RUN_FAIL", "FAKE_STOP_FAIL", "FAKE_OFFLINE", "FAKE_INACTIVE"):
        monkeypatch.delenv(switch, raising=False)
    # The guard the whole file exists for: if PATH ordering ever let the real
    # binaries through, stop before a single real unit is created.
    for name in ("systemd-run", "systemctl"):
        assert shutil.which(name) == str(bin_dir / name), f"real {name} would be used"
    return FakeSystemd(log)
