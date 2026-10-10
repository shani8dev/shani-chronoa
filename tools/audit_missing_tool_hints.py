"""Which tools reach `files.tool_missing()` with no package name to give?

`tool_missing(binary, ...)` is what a skill says when a command is absent,
and its whole job is to name the package that provides the binary. When
`_PACKAGE_HINTS` has no entry it falls back to the literal string

    the 'the package that provides it' package

which helps nobody install anything.

`curl` shipped with exactly that sentence: it is `shani-tools-network`, it
is on both images by design, and it is the single most-used download path
in this package. Found by running the skill with no `curl` and no `wget` on
`PATH` - not by reading the hint table.

This walks the AST for `tool_missing("x", ...)` / `which("x")` calls across
the package and reports the ones with no hint, so the list cannot rot the way
a hand-kept one would.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                    else "usr/lib/shani-chronoa")
sys.path.insert(0, str(ROOT))

from shani_chronoa import files  # noqa: E402

#: Calls whose first argument is the binary being looked for.
_CALLERS = {"tool_missing", "which", "_which", "have", "has_tool",
            "available", "require", "tool_missing_package"}


def wanted(tree: ast.AST) -> "list[tuple[str, int]]":
    found: list = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in _CALLERS or not node.args:
            continue
        arg = node.args[0]
        # Both `tool_missing("curl", ...)` and `tool_missing(cmd, ...)` where
        # `cmd` is a module constant - the latter is resolved, not skipped,
        # because a constant holding "curl" is exactly the case that rots.
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.append((arg.value, node.lineno))
        elif isinstance(arg, ast.Name):
            found.append((f"<var {arg.id}>", node.lineno))
    return found


def main() -> int:
    hints = files._PACKAGE_HINTS
    missing: dict = {}
    checked = 0

    for path in sorted(ROOT.rglob("*.py")):
        if path.name == "files.py":
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:
            print(f"  !! {path}: {exc}")
            continue
        # Module-level string constants, so `CMD = "curl"` resolves.
        consts = {n.targets[0].id: n.value.value
                  for n in tree.body
                  if isinstance(n, ast.Assign) and len(n.targets) == 1
                  and isinstance(n.targets[0], ast.Name)
                  and isinstance(n.value, ast.Constant)
                  and isinstance(n.value.value, str)}
        for name, line in wanted(tree):
            resolved = consts.get(name[5:-1], name) if name.startswith("<var ") \
                else name
            if not resolved or resolved.startswith("<var"):
                continue
            checked += 1
            if resolved not in hints:
                missing.setdefault(resolved, []).append(
                    f"{path.relative_to(ROOT)}:{line}")

    print(f"{checked} tool reference(s) resolved from "
          f"{len(list(ROOT.rglob('*.py')))} files\n")
    if not missing:
        print("every tool that reaches tool_missing() has a package name.")
        return 0
    print(f"{len(missing)} reach tool_missing() with no package name, so the "
          f"sentence is:\n"
          f"    \"On Arch it comes from the 'the package that provides it' "
          f"package.\"")
    for binary in sorted(missing):
        where = ", ".join(missing[binary][:3])
        extra = (f" (+{len(missing[binary]) - 3} more)"
                 if len(missing[binary]) > 3 else "")
        print(f"  {binary:22} {where}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())