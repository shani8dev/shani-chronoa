"""The slow-tool budget table, which nothing else in the suite reads.

`_SLOW_TOOLS` is a data table, so a change to it cannot fail a unit test - and
two real properties live only in it. Both were measured on this box, and both are
the reason a value is what it is rather than a round number:

- `ui_elements drive start` **nests** a `container` call. The sandbox's ceiling
  can only ever tighten, so a nested profile asking for more than its parent
  already has is *refused* - measured: "the 'default' sandbox profile allows 300
  of CPU time but this process is already limited to 185". So the pair must be
  equal, and anything less than equal refuses.
- A container's app needs its window to appear *and* its accessibility tree to
  register after it (~11 s here, plus an image pull on a first run), which is
  what killed `ui_elements drive` at the 30 s default in the field.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import tools  # noqa: E402


def test_the_driving_pair_can_be_nested_without_a_refusal() -> None:
    """Equal, and the reason is the ceiling only ever tightening."""
    assert tools._SLOW_TOOLS["ui_elements"] == tools._SLOW_TOOLS["container"] >= 300


def test_the_default_is_smaller_than_everything_that_actually_waits() -> None:
    for name in ("container", "ui_elements", "vm", "watch", "bluetooth_gatt"):
        assert tools._SLOW_TOOLS[name] > 30, name


def test_every_entry_names_a_tool_that_exists() -> None:
    registered = {t["function"]["name"] for t in tools.TOOLS}
    unknown = set(tools._SLOW_TOOLS) - registered
    assert not unknown, f"a budget for a tool that is not registered: {sorted(unknown)}"


def test_the_table_has_no_duplicate_key() -> None:
    """A later duplicate wins silently, and the earlier reason goes with it."""
    import ast

    source = open(tools.__file__, encoding="utf-8").read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "_SLOW_TOOLS" for t in node.targets):
            keys = [k.value for k in node.value.keys]
            dupes = {k for k in keys if keys.count(k) > 1}
            assert not dupes, f"duplicate budget entries: {sorted(dupes)}"
