"""Live, end-to-end check of `websearch` through the real consent gate.

The suite drives `search()` with a stubbed fetcher, which proves the parsers
and the routing. This proves the other half: that a real query from a real
machine, with the `web` sense granted through a real compiled gsettings
schema, returns real results from a real engine.

Run:  python3 tools/live_webcheck.py [query]

Nothing here touches the developer's own gsettings - the schema is compiled
into a temp dir and a keyfile beside it grants the two keys, and `HOME` and
`XDG_CONFIG_HOME` point at that temp dir too.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
LIB = REPO / "usr" / "lib" / "shani-chronoa"
SCHEMA_SRC = (REPO / "usr" / "share" / "glib-2.0" / "schemas"
              / "org.shani.chronoa.gschema.xml")

PROBE = """
import sys
sys.path.insert(0, %(lib)r)
from shani_chronoa import websearch
query = sys.argv[1]
results, reason, notes = websearch.search(query)
if not results:
    print("NO RESULTS:", reason)
    for note in notes:
        print("  note:", note)
    raise SystemExit(1)
print(websearch.format_results(query, results, notes))
"""


def main() -> int:
    if not SCHEMA_SRC.exists():
        print("no schema at %s" % SCHEMA_SRC)
        return 1
    home = pathlib.Path(tempfile.mkdtemp(prefix="live-webcheck-"))
    schema = home / "schemas"
    schema.mkdir()
    (schema / SCHEMA_SRC.name).write_text(SCHEMA_SRC.read_text())
    done = subprocess.run(["glib-compile-schemas", str(schema)],
                          capture_output=True, text=True)
    if done.returncode != 0:
        print("glib-compile-schemas failed:", done.stderr[-400:])
        return 1
    # **The keyfile goes at `$XDG_CONFIG_HOME/glib-2.0/settings/keyfile`, under
    # the group named for the schema's *path* - and getting either wrong is a
    # silent refusal.** Measured three ways before this line was right:
    #
    # - beside the schema with `GSETTINGS_KEYFILE_PATH` set and
    #   `XDG_CONFIG_HOME` elsewhere -> every key read as its default;
    # - `$XDG_CONFIG_HOME/gsettings.ini` -> same;
    # - `$XDG_CONFIG_HOME/glib-2.0/settings/keyfile` with
    #   `[org/shani/chronoa]` -> the gate opens.
    #
    # The group spelling is the same trap `config.py`'s own header records:
    # the dotted schema id and the path form are two groups in one file.
    config = home / ".config"
    keyfile_dir = config / "glib-2.0" / "settings"
    keyfile_dir.mkdir(parents=True, exist_ok=True)
    (keyfile_dir / "keyfile").write_text(
        "[org/shani/chronoa]\n"
        "privacy-mode=false\n"
        "web-sense-enabled=true\n")

    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "GSETTINGS_BACKEND": "keyfile",
        "GSETTINGS_SCHEMA_DIR": str(schema),
        "GSETTINGS_KEYFILE_PATH": str(schema / "gsettings.ini"),
        "XDG_CONFIG_HOME": str(config),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    query = sys.argv[1] if len(sys.argv) > 1 else "arch linux install guide"
    probe = home / "probe.py"
    probe.write_text(PROBE % {"lib": str(LIB)})

    print("# live query: %r" % query)
    print("# lib: %s" % LIB)
    print("# schema: %s" % schema)
    print()
    done = subprocess.run([sys.executable, str(probe), query],
                          capture_output=True, text=True, timeout=300,
                          env=env)
    sys.stdout.write(done.stdout)
    if done.returncode != 0:
        sys.stderr.write(done.stderr[-1500:])
        print("# probe exited %d" % done.returncode)
        return 1
    print()
    print("# the consent gate opened, the query left, and results came back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
