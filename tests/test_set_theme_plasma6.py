"""set_theme on Plasma 6, against a stub shaped like the real plasma-apply-colorscheme.

Measured on the Plasma image (2026-10-01): plasma-lookandfeeltool and kreadconfig5
do not exist there, `plasma-apply-lookandfeel --list` marks no current look, and
`plasma-apply-colorscheme --list-schemes` marks "(current color scheme)". The old
code used the first two and read [KDE] colorScheme, so status and both changes
were broken on Plasma 6 - and no test noticed, because none drove the KDE path.
"""

import stat

import pytest

from shani_chronoa.skills import set_theme


@pytest.fixture
def plasma(tmp_path, monkeypatch):
    state = tmp_path / "scheme"
    state.write_text("BreezeLight")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tool = bindir / "plasma-apply-colorscheme"
    tool.write_text(f"""#!/bin/sh
if [ "$1" = "--list-schemes" ]; then
  echo "You have the following color schemes on your system:"
  for s in BreezeClassic BreezeDark BreezeLight KvArcDark; do
    if [ "$s" = "$(cat {state})" ]; then echo " * $s (current color scheme)"; else echo " * $s"; fi
  done
  exit 0
fi
[ -f {tmp_path}/ignore ] || printf %s "$1" > {state}
echo "Successfully applied the color scheme $1 to your current Plasma session"
""")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(set_theme, "_consent", lambda config: (True, ""))
    return state


def test_status_reads_the_marked_scheme(plasma):
    assert set_theme._kde_state() == ("light", "BreezeLight")
    assert "BreezeLight" in set_theme._run({"action": "status"})


def test_dark_then_light_round_trip(plasma):
    assert "now 'BreezeDark'" in set_theme._run({"action": "dark"})
    assert plasma.read_text() == "BreezeDark"
    assert "now 'BreezeLight'" in set_theme._run({"action": "light"})


def test_a_change_that_does_not_read_back_is_not_verified(plasma, tmp_path):
    (tmp_path / "ignore").write_text("")
    out = set_theme._run({"action": "dark"})
    assert "not verified" in out and "BreezeLight" in out
