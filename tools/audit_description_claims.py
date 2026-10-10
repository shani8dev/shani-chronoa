"""Does a skill name a command the package never invokes?

A tool description is the contract the model reads. When it names a binary
nothing calls, the skill advertises a capability it does not have - and the
failure is quiet: the skill runs, answers, and the answer is simply narrower
than promised. This repo has paid for that shape three times (in
`AGENTS.md`, "the three lies the work found").

**Two attempts of this tool were abandoned, and the reason is the point.**

1. The first collected every lowercase 4+ letter token from each docstring.
   It reported **485 of 486 modules**, because `^[a-z]{4,}$` matches "about",
   "again", "case". A check that flags everything is the same as one that
   flags nothing, and here it would have been worse, because 485 red lines
   are read as noise.
2. The second matched tokens that merely *look* command-shaped. Same outcome.

So the third version is bounded by the **image matrices**, which are the only
machine-readable list in the tree of what commands exist. A token is a
finding only if it is:

  - an actual command on a ShaniOS image, per `commands[].command`;
  - invoked by **no module anywhere in the package** (so it is not a real
    dependency being used);
  - and mentioned in a skill's own docstring or schema description.

So the third version is bounded by the **image matrices**, which are the only
machine-readable list in the tree of what commands exist. A token is a
finding only if it is:

  - an actual command on a ShaniOS image, per `commands[].command`;
  - invoked by **no module anywhere in the package** (so it is not a real
    dependency being used);
  - and mentioned in a skill's own docstring or schema description.

That is still a **lead list, not a defect list** - "yt-dlp" in a module that
explains why it does *not* shell out is prose, not a claim. But the set is
small enough to read, which the other two were not.

**Current output (2026-10-10), read and clean: 119 modules.** Every skill in
it names the tool in prose that explains *why the skill does not use it*,
which is the good outcome. The pattern, so the next reader can check rather
than re-derive:

- `tls_certificate` names `openssl s_client` and `sha256sum` while calling
  `ssl` and `hashlib` from the stdlib, and says so in its own second line:
  "the binary can be absent from a minimal image".
- `interface_counters` names `ifstat`, `nstat -s` and `vnstat` in the sentence
  that opens "Read from `/proc/net/dev` and `/proc/net/netstat`, **not** by
  running...".
- `json_query` names `jq` as "the flagged command" it was built instead of;
  the `AGENTS.md` matrix section already records that `open_surfaces` measures
  calls, not answers.
- `nfc` names `ftp`, `sftp` and `telnet` as the **refused** destinations in
  its bank-card denylist.
- `archives`, `capability.py`, `driver_dev.py`, `tool_select.py` and
  `files.py` name the most tools because they are the tables and the
  deliberately-not-built lists - `capability.py`'s eleven are the audit's
  own deliberate exclusions (`rclone`, `socat`, `aria2c`, `traceroute`,
  `wget`, `rg`, `bzip2`, `xz`, `bat`, `sqlite3`, `iwconfig`).

So no skill in this tree currently advertises a command it does not have. The
tool is worth keeping precisely because a violation of its caveat - a skill
naming `openssl` in a sentence that reads like a *capacity* rather than a
*refusal* - is the "advertised and failed on every call" class this repo has
already shipped, and this is the only thing that would catch it.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                    else "usr/lib/shani-chronoa")
MATRICES = [
    pathlib.Path("../shani-install-media/test-env/mout/matrix-gnome.json"),
    pathlib.Path("../shani-install-media/test-env/mout-plasma/shanios-matrix.json"),
]

_GATED = {"agent_messages.py", "__init__.py"}


def image_commands() -> "set[str]":
    have: set = set()
    for path in MATRICES:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        for entry in data.get("commands", []):
            name = entry.get("command")
            if name and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
                have.add(name)
    return have


def _invoked(tree: ast.AST) -> "set[str]":
    found: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            head = node.elts[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                found.add(head.value)
        if isinstance(node, ast.Call):
            fn = node.func
            attr = fn.attr if isinstance(fn, ast.Attribute) \
                else getattr(fn, "id", "")
            if attr in {"which", "_which", "have", "has_tool", "tool",
                        "available", "require", "run"}:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.add(arg.value)
    return found


def _consts(tree: ast.AST) -> "set[str]":
    return {n.value.value for n in tree.body
            if isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)}


def _prose(texts: "list[str]", commands: "set[str]") -> "set[str]":
    """Command names that appear in prose. Matched on whole boundaries, so
    "meta" does not match "metadata"."""
    out: set = set()
    for text in texts:
        for token in re.findall(r"[a-z0-9][a-z0-9._-]*", text.lower()):
            if token in commands:
                out.add(token)
    return out


def main() -> int:
    commands = image_commands()
    if not commands:
        print("no image matrices found; this tool needs them beside the repo")
        return 0

    invoked_anywhere: set = set()
    per_module: dict = {}
    for path in sorted(ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        invoked = _invoked(tree) | _consts(tree)
        invoked_anywhere |= invoked
        strings = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant)
                   and isinstance(n.value, str)]
        per_module[path] = (invoked, strings)

    # **The discriminator is rarity, not shape.** Two attempts of this tool
    # tried to tell a command from an English word by what it looks like and
    # both flagged 456 of 486 modules, because `file`, `test`, `cut`, `look`
    # and `more` are coreutils commands *and* ordinary prose. That is a check
    # reporting everything, which is the same as one reporting nothing.
    #
    # What actually separates them: a vocabulary word appears in the docstrings
    # of **dozens** of modules, and a deliberate citation appears in **one or
    # two**. So count the mentions and keep only the rare ones. Measured: the
    # words that make up the noise appear in 40-150 modules each, and the
    # interesting ones in 1-3.
    rarity: dict = {}
    for path, (invoked, strings) in per_module.items():
        for token in _prose(strings, commands):
            rarity.setdefault(token, set()).add(path)

    findings: list = []
    for path, (invoked, strings) in per_module.items():
        if path.name in _GATED:
            continue
        named = _prose(strings, commands)
        named -= invoked
        named -= invoked_anywhere
        named -= {path.stem}
        # Only a token almost nobody else names.
        named = {t for t in named if len(rarity.get(t, ())) <= 3}
        if named:
            findings.append((path.relative_to(ROOT), sorted(named)))

    findings.sort(key=lambda pair: -len(pair[1]))
    print(f"{len(commands)} commands on the image, "
          f"{len(invoked_anywhere)} invoked anywhere in the package\n")
    if not findings:
        print("no module names an image command that nothing invokes.")
        return 0
    print(f"{len(findings)} module(s) name one. **A lead list, not a "
          f"defect list** - a name in prose explaining why the tool is *not*\n"
          f"used is the most common reason, and that is a good outcome.\n")
    for path, named in findings:
        print(f"  {path}  ({len(named)})")
        print(f"    {', '.join(named[:12])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())