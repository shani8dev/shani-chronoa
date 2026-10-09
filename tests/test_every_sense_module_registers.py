"""A sense the loader skipped is invisible to every test that reads the registry.

`kernellog` shipped broken for one commit and the suite was green.

It was written as `kernel_log`, and the consent key is
`<sense-name>-sense-enabled` **derived from the name** while
`glib-compile-schemas` rejects `_` in key names and discards the **entire schema
file** on one bad name. So the sense had to be renamed to `kernellog` — and the
rename touched `Sense(name=...)` and the `<key name=...>` but **not** the `"name"`
inside its schema, which `_register` requires to equal the sense name:

    Skipping malformed sense 'kernellog' from 'builtin:kernel_log':
      schema function name 'kernel_log' does not match sense name 'kernellog'

**And nothing failed.** The registry went to 49 and stayed there. Both existing
guards read the *registry*, which by construction cannot contain a module the
loader skipped:

- `test_every_registered_sense_passes_the_loaders_own_validator` — over
  `registry`.
- `test_every_sense_schema_advertises_its_own_name` — over `registry`.

Each is a good test of what it covers, and between them they leave a module that
never loaded completely unexamined. That is an absence presented as a pass, which
this repository has been bitten by in both directions, and the number that would
have shown it — 49, not 50 — is asserted nowhere.

So this file asks the question the registry cannot: **does every sense module in
the tree actually register?**

**The comparison is by declared sense name, never by filename**, and that is not
a detail. Four modules differ from the sense they declare: `wireless_link.py`
declares `wirelesslink`, `dns_resolvers.py` declares `dnsresolvers`,
`sounds.py` declares something else again, and `kernel_log.py` declares
`kernellog`. My first version compared filenames to registry keys and reported
all four as "not registered" — a false alarm that would have had me rename four
working senses, which is the mirror image of the defect this file is about. The
filename is not a sense's identity; the name it declares is.
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

_SENSES_PKG = _REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa" / "senses"


def _declared_sense_names() -> dict:
    """`{module stem: the sense name it declares}`, read from the AST.

    Parsed rather than imported, so a module that cannot even be imported is
    still counted - which is the other way a sense disappears.
    """
    found: dict = {}
    for path in sorted(_SENSES_PKG.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:
            continue
        if not any(isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "SENSES"
                           for t in node.targets)
                   for node in tree.body):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "Sense"):
                continue
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    found[path.stem] = str(kw.value.value)
                    break
            else:
                if node.args and isinstance(node.args[0], ast.Constant):
                    found[path.stem] = str(node.args[0].value)
            break
    return found


def test_the_package_actually_has_sense_modules():
    """The control: this file would pass vacuously if the scan found nothing.

    A scan that matches nothing finds nothing wrong. Asserted first, so "no
    failures" cannot mean "no modules" - and the fixture below pins the
    module/-sense-name mismatch this file is about, so a rename cannot quietly
    retire the capability a count would not notice.
    """
    declared = _declared_sense_names()
    assert len(declared) > 20, (
        f"only {len(declared)} modules declare a named sense, so this file's "
        f"scan is probably not matching what it should")
    assert declared.get("kernel_log") == "kernellog", (
        f"kernel_log.py declares {declared.get('kernel_log')!r}; this file "
        f"expects 'kernellog', so if the sense was renamed again the naming "
        f"below needs re-reading")
    assert (_SENSES_PKG / "kernel_log.py").exists(), (
        "the kernel-ring sense module is gone; if it was renamed, this file's "
        "own fixtures need re-reading")


def test_every_sense_module_registers(caplog):
    """A finished sense that never loads is a capability nobody has.

    `discover_senses()` is the loader's own entry point and what the app calls,
    so a module absent from it is absent from the product. The loader skips with
    a *warning*, not an exception, which is why this has to be asserted rather
    than noticed.
    """
    caplog.set_level(logging.WARNING)
    from shani_chronoa.senses import discover_senses

    registry = discover_senses()
    missing = sorted(name for name in _declared_sense_names().values()
                     if name not in registry)
    assert not missing, (
        "these senses are declared by a module in the tree but are not in the "
        "registry, so nothing about them reaches the app:\n"
        + "\n".join(f"  - {name}" for name in missing)
        + "\nThe loader's reason is in the warnings above. The two it has hit "
          "are a schema function name that differs from the sense name, and a "
          "sensitivity outside {personal, private, public}.")


def test_the_kernel_ring_sense_is_registered():
    """Named specifically, because it is the one that broke.

    The general guard above would catch it too; this says *which* sense is meant
    to exist, so a rename cannot retire the capability silently.
    """
    from shani_chronoa.senses import discover_senses

    registry = discover_senses()
    assert "kernellog" in registry, (
        "the kernel-ring sense is not registered. Its consent key is "
        "'kernellog-sense-enabled' and the sense name must equal the schema's "
        "function name - the two drifted apart when the name changed for the "
        "gschema '_' rule, and the loader skipped it with a warning rather than "
        "failing")
    assert registry["kernellog"].schema["function"]["name"] == "kernellog"


def test_every_registered_sense_consent_key_is_legal_in_a_gschema_key():
    """The rule that forced the rename, asserted where it is learned.

    `glib-compile-schemas` rejects `_` in key names and **discards the whole file**
    when it hits one - not just the bad key. So a snake_case sense name does not
    fail its own permission, it silently undeclares every other key in the
    schema. The registry test catches the *consequence* late; this catches the
    *cause* early, and names the consequence.
    """
    from shani_chronoa.senses import discover_senses

    registry = discover_senses()
    illegal = sorted(name for name in registry
                     if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                            for c in name))
    assert not illegal, (
        f"{illegal} cannot be a gschema key fragment, so '<name>-sense-enabled' "
        f"cannot be declared - and glib-compile-schemas discards the ENTIRE "
        f"schema file on one such key, so every other permission goes with it. "
        f"Rename the sense, as kernel_log -> kernellog was.")
