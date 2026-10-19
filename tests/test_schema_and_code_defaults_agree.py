"""A setting's default is stated in two places, and they have to agree.

`ChronoaConfig.get_bool(key, default)` reads the **schema** whenever the key
is declared in it. The `default` argument is only reached on an install whose
schema predates the key - and `ChronoaConfig._new_settings` builds an
explicit keyfile backend, so that is not a hypothetical machine.

So a fallback that disagrees with the schema means two machines read the same
setting differently: a fresh one, which has the schema, and an older one,
which has the fallback. That is the shape of every default disagreement this
repository has shipped, and the shape of a real defect found on 2026-10-19:

- `324e597` changed `privacy-mode` from `true` to `false` and moved the
  Python fallback with it - but missed a **second reader of the same key** in
  `gui/surfaces/senses.py`, which still fell back to `True`. The Senses panel's
  footer therefore said "Privacy mode is on, so a sense that reaches outside
  this machine stays refused" on a machine whose privacy mode was off.
- The same commit swept **three unrelated sense defaults** in the gschema back
  to `true`, undoing a measured decision (`ef3e952`) that the Python policy
  table still carried, and left a test failing.
- Ten keys the schema ships `true` still had `False` as their code fallback,
  because the commit that turned them on updated the schema and not the code.

The check is over the whole registry, not a hand-kept list, because a list of
the keys that disagree is a list that rots the moment a key is added.
"""

import ast
import pathlib
import xml.etree.ElementTree as ET

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
SCHEMA = REPO / "usr" / "share" / "glib-2.0" / "schemas" / "org.shani.chronoa.gschema.xml"


def _schema_defaults() -> dict:
    """Every boolean key the schema declares, and its default."""
    tree = ET.parse(SCHEMA)
    defaults = {}
    for key in tree.getroot().iter("key"):
        if key.get("type") != "b":
            continue
        default = key.find("default")
        defaults[key.get("name")] = (default.text or "false").strip() == "true"
    return defaults


def _code_fallbacks() -> list:
    """Every explicit `get_bool("key", <literal>)` fallback in the package.

    A **list**, not a dict of sets, and that is the whole point: two sites can
    read the same key with two different fallbacks, and a set of fallbacks
    hides that as long as one of them is right. The first version of this
    check collected a set per key, and a mutation that broke one of two sites
    left the suite green - the wrong fallback was in the set, and so was the
    right one.

    Parsed with `ast` rather than grepped: a key named in a comment is not a
    fallback, and a grep would count it.
    """
    found: list = []
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name) else None)
            if name != "get_bool" or not node.args:
                continue
            key = node.args[0]
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            default = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) \
                    and isinstance(node.args[1].value, bool):
                default = node.args[1].value
            for keyword in node.keywords:
                if keyword.arg == "default" and isinstance(keyword.value, ast.Constant) \
                        and isinstance(keyword.value.value, bool):
                    default = keyword.value.value
            if default is None:
                continue
            found.append({
                "key": key.value,
                "default": default,
                "where": f"{path.relative_to(REPO)}:{node.lineno}",
            })
    return found


@pytest.fixture(scope="module")
def schema_defaults():
    return _schema_defaults()


@pytest.fixture(scope="module")
def code_fallbacks():
    return _code_fallbacks()


class TestTheResolverFindsThings:
    """A check that cannot find anything passes everything."""

    def test_the_schema_has_boolean_keys(self, schema_defaults):
        assert len(schema_defaults) > 100, (
            f"only {len(schema_defaults)} boolean keys found - the schema was "
            f"not read"
        )

    def test_the_code_has_explicit_fallbacks(self, code_fallbacks):
        assert len(code_fallbacks) > 10, (
            f"only {len(code_fallbacks)} explicit get_bool fallbacks found - "
            f"the code was not read"
        )


class TestTheTwoPlacesAgree:
    def test_no_fallback_disagrees_with_the_schema(self, schema_defaults, code_fallbacks):
        """Every site, not every key.

        A key read in two places with two different fallbacks is the case that
        a per-key check cannot see, and it is a real one: `privacy-mode` was
        read in `config.py` with a `False` fallback and in
        `gui/surfaces/senses.py` with a `True` one, so the Senses panel's
        footer described a policy the app was not enforcing.
        """
        out = []
        for site in code_fallbacks:
            key = site["key"]
            if key not in schema_defaults:
                continue
            if site["default"] is not schema_defaults[key]:
                out.append(f"{key} at {site['where']}: the schema says "
                           f"{schema_defaults[key]}, the code falls back to "
                           f"{site['default']}")
        assert not out, (
            "a setting's two defaults disagree, so a machine with an older "
            "schema reads it the other way:\n  " + "\n  ".join(out)
        )

    def test_one_key_is_not_read_with_two_different_fallbacks(self, code_fallbacks):
        """The same defect one level up, and the reason the check is per-site.

        Two readers of one key, disagreeing, is a defect even when the schema
        happens to agree with one of them - because the other one is a machine
        reading it differently.
        """
        seen: dict = {}
        out = []
        for site in code_fallbacks:
            seen.setdefault(site["key"], []).append(site)
        for key, sites in sorted(seen.items()):
            defaults = {s["default"] for s in sites}
            if len(defaults) > 1:
                where = ", ".join(f"{s['where']}={s['default']}" for s in sites)
                out.append(f"{key}: {where}")
        assert not out, (
            "one key is read with two different fallbacks:\n  " + "\n  ".join(out)
        )

    def test_a_key_the_schema_does_not_declare_is_not_counted(self, schema_defaults):
        """A key the code reads that the schema does not declare takes its
        Python default **forever** - which is a different defect, and one the
        schema-agreement test in `test_senses_new_batch.py` covers for the
        sense keys. It is not what this file is about."""
        assert "privacy-mode" in schema_defaults
