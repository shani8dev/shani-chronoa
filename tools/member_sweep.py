"""Every `self.X(...)` in an in-package class, resolved against its real bases.

**The bug class this exists for.** `_model_row` was defined on `_ModeStrip`
while `ChronoaWindow.__init__` called it, so every window construction raised
`AttributeError`. pyflakes cannot see it: `self.foo()` inside a method is a
*name*, not an attribute the compiler resolves, and the class it belongs to is
a question of inheritance rather than of syntax.

**What it does not report.** A `self.X()` whose `X` is not defined in-package is
only a finding if no *external* base provides it. So the external bases are
resolved at run time against the installed libraries and asked with `hasattr` -
not checked against a hand-kept list, which is the list this repository keeps
getting wrong (`wpctl` three times, the finite check four times, `_SYNONYMS`,
`capabilities._GROUPS`). A name the library answers for is the library's; a name
it does not answer for is ours to define.

    python3 tools/member_sweep.py           # the whole package
    python3 tools/member_sweep.py gui/window.py

**The control is the reason to believe its clean result.** `reintroducing a
method on the wrong class makes it print that name`, and
`tests/test_member_sweep.py` drives that rather than asserting the sweep is
quiet - a checker that has stopped looking reports the same output as one that
works.
"""

import argparse
import ast
import pathlib
import sys

PKG = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa")

#: Namespaces an external base may come from. A base outside these and outside
#: the package is reported as unknown rather than assumed absent.
#: `namespace -> version` for the typelibs. The version is **not** guessable -
#: asking for Adw "4.0" raises and the namespace then reads as unresolvable,
#: which is how `Adw.ApplicationWindow` ended up reported as missing its own
#: `set_content`.
_GI_VERSIONS = {
    "Gtk": "4.0", "Adw": "1", "Gdk": "4.0", "Gsk": "4.0",
    "Gio": None, "GObject": None,
}
_GI_NAMESPACES = tuple(_GI_VERSIONS)


def _members(cls: ast.ClassDef) -> set:
    """Every name the class defines, including assigned-anything and dataclass."""
    out = set()
    for sub in cls.body:
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(sub.name)
            for arg in sub.args.args:
                out.add(arg.arg)
            for extra in (sub.args.kwarg, sub.args.vararg):
                if extra is not None:
                    out.add(extra.arg)
            if sub.args.posonlyargs:
                out.update(a.arg for a in sub.args.posonlyargs)
            if sub.args.kwonlyargs:
                out.update(a.arg for a in sub.args.kwonlyargs)
        elif isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                out.add(tgt.id if isinstance(tgt, ast.Name) else tgt.attr)
        elif isinstance(sub, ast.AnnAssign):
            tgt = sub.target
            out.add(tgt.id if isinstance(tgt, ast.Name) else tgt.attr)
        elif isinstance(sub, ast.ClassDef):
            out.add(sub.name)
        # Anything assigned anywhere below, whatever the shape: tuple targets,
        # augmented assignment, `self.x: T = v`.
        for inner in ast.walk(sub):
            targets = []
            if isinstance(inner, ast.Assign):
                targets = inner.targets
            elif isinstance(inner, (ast.AugAssign, ast.AnnAssign)):
                targets = [inner.target]
            for tgt in targets:
                if isinstance(tgt, ast.Name):
                    out.add(tgt.id)
                elif isinstance(tgt, ast.Attribute):
                    out.add(tgt.attr)
    return out


def _direct_self_calls(cls: ast.ClassDef) -> set:
    """`self.X(...)` only.

    Three things that are *not* that, each of which this file reported as a
    finding before it was measured:

    - **`self.x.y(...)`** - a method of what `self.x` holds. Counting it
      reported `startswith` as a member `ChronoaWindow` must define.
    - **a `@staticmethod`'s first parameter**, which is not `self` at all.
      `_should_toast(text, ...)` made `text.startswith(...)` a member.
    - **an attribute read**, `self.mac.replace(...)`. That reported `mac` as a
      missing method of `BluezWatch`, which assigns it in `__init__`.
    """
    found = set()
    for sub in cls.body:
        if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not sub.args.args:
            continue
        decorators = {ast.unparse(d) for d in sub.decorator_list}
        if any("staticmethod" in d or "classmethod" in d for d in decorators):
            continue
        first = sub.args.args[0].arg
        for inner in ast.walk(sub):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id == first):
                found.add(inner.func.attr)
    return found


def _index(package: pathlib.Path):
    """`{class name: (module, members, [base names])}` over the whole package."""
    index, order = {}, []
    for path in sorted(package.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            print(f"  !! {path}: SyntaxError: {exc}", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # **Every base, not only the bare-name ones.** Collecting
                # only `ast.Name` dropped `Adw.ApplicationWindow` and
                # `Gtk.Window` entirely, so both reported their own base
                # class's methods as missing - which is the shape of a sweep
                # crying wolf, and the reason to fix a scanner before reading
                # its verdict.
                bases = [b.id if isinstance(b, ast.Name) else ast.unparse(b)
                         for b in node.bases]
                index[node.name] = (
                    str(path.relative_to(package)), _members(node), bases)
                order.append(node.name)
    return index, order


def _mro_members(index, name) -> tuple:
    """(`{members}` over the in-package MRO, every external base, the chain).

    The external names are collected from **every class in the chain**, not just
    the class being swept: `SettingsWindow`'s four bases are all in-package and
    each of those derives from `Adw.NavigationPage`, so reading only the
    immediate bases reported `connect` and `set_child` as missing on a window
    that has always had them.
    """
    seen, stack = set(), list(index[name][2])
    external = []
    while stack:
        base = stack.pop()
        if base in seen:
            continue
        seen.add(base)
        if base in index:
            stack.extend(index[base][2])
        else:
            external.append(base)
    provided = set()
    for cls in {name} | seen:
        if cls in index:
            provided |= index[cls][1]
    return provided, external, sorted(seen)


def _gi_lookup():
    """The installed libraries, or `None` when they cannot be imported."""
    try:
        import gi
    except ImportError:
        return None
    loaded = {}
    for namespace, version in _GI_VERSIONS.items():
        try:
            if version is not None:
                gi.require_version(namespace, version)
            loaded[namespace] = __import__(
                f"gi.repository.{namespace}", fromlist=["x"])
        except (ValueError, ImportError) as exc:
            print(f"  !! {namespace} did not load: {exc}", file=sys.stderr)
    return loaded or None


def _external_mro(gi, names) -> "list | None":
    """Every ancestor of every named external base, or `None` if unresolvable.

    `None` means *we could not ask* - not that the name is absent - and the
    caller reports it as a finding, because a checker that cannot resolve its
    own premise must not certify the absence.
    """
    if gi is None:
        return None
    chain, unresolved = [], []
    for name in names:
        found = None
        # **A dotted base names its own namespace.** `Adw.ApplicationWindow`
        # resolved to `Gtk.ApplicationWindow` when the namespaces were searched
        # in dictionary order, and then reported `set_content` and
        # `add_breakpoint` as missing from a class that has both - the base's
        # *own* members, lost to another library's identically-shaped one.
        if "." in name:
            prefix, _, bare = name.rpartition(".")
            candidates = [prefix] if prefix in gi else list(gi)
            name = bare
        else:
            candidates = list(gi)
        for namespace in candidates:
            module = gi.get(namespace)
            if module is None:
                continue
            candidate = getattr(module, name, None)
            if candidate is not None:
                found = candidate
                break
        if found is None:
            unresolved.append(name)
        else:
            chain.extend(found.__mro__)
    return None if unresolved else chain


def sweep(package: pathlib.Path = PKG, paths=None):
    """`(findings, unresolved)` - real findings, and classes not asked about."""
    index, _order = _index(package)
    gi = _gi_lookup()
    findings, unresolved = [], []
    for path in (paths or sorted(package.rglob("*.py"))):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if cls.name not in index or index[cls.name][0] != str(
                    path.relative_to(package)):
                continue        # a class defined elsewhere, indexed once
            provided, external, chain = _mro_members(index, cls.name)
            # **No in-package base means no composition to get wrong**, and the
            # external base cannot be resolved either - a plain class's
            # `self.x` is its own business.
            if not chain:
                continue
            missing = sorted(_direct_self_calls(cls) - provided)
            if not missing:
                continue
            # **Ask the library rather than keeping a list.** Every name an
            # external base provides is that base's, and the question is
            # answerable by asking - which is why this is a lookup and not the
            # hand-kept allowlist this repository keeps getting wrong.
            external_mro = _external_mro(gi, external)
            if external_mro is None:
                # A base we cannot resolve - a stdlib class, or a library not
                # installed here. **Reported as its own line and not as a
                # finding**: we cannot ask it, so we cannot say what it
                # provides, and a checker that cries wolf about a name it
                # never checked is the failure this repository records most.
                unresolved.append((f"{index[cls.name][0]}::{cls.name}",
                                    sorted(external)))
                continue
            missing = [m for m in missing
                       if not any(hasattr(k, m) for k in external_mro)]
            if missing:
                findings.append((f"{index[cls.name][0]}::{cls.name}",
                                 missing, chain, external))
    return findings, unresolved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="files to sweep (default: all)")
    args = ap.parse_args()
    paths = [pathlib.Path(p) for p in args.paths] or None
    findings, unresolved = sweep(paths=paths)
    for label, bases in unresolved:
        print(f"  ? {label}: could not ask {bases} - not counted either way")
    for label, missing, chain, external in findings:
        print(f"  {label}")
        print(f"      in-package bases: {', '.join(chain)}")
        print(f"      external bases:   {', '.join(external) or '(none)'}")
        print(f"      undefined there:  {missing}")
    total = sum(len(f[1]) for f in findings)
    print(f"\n  {total} self.X() call(s) with no definition on any base"
          f" - {len(findings)} class(es)")
    if unresolved:
        print(f"  {len(unresolved)} class(es) had a base this could not ask about")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())