"""The engine never imports the GTK client, except behind a live-window guard.

`ui_gtk` is the process side that owns GTK startup. The engine side is the
Agent service, the tool loop, the stores, the senses, the triggers and MCP.
This is a source check, not a guess from a passing import: a module that only
imports GTK inside a function still breaks a headless call of that function.

One guarded import is allowed: `skills/browse` uses the in-app WebKit browser
only while `browser_bridge.has_provider()` says a GTK window is registered.
With no window it takes the headless Chromium path, which is the whole point
of the two-browser design.
"""

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PKG = _REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"

UI_PREFIXES = (
    "shani_chronoa.gui",
    "shani_chronoa.settings_window",
    "shani_chronoa.app",
    "shani_chronoa.ui_gtk",
)
GUI_TYPILIBS = {"Gtk", "Adw", "Gdk", "Gtk4LayerShell"}
ALLOWED = {
    # (relative path, imported module) -> why the engine cannot reach it
    (
        "skills/browse.py",
        "shani_chronoa.gui",
    ): "only inside if browser_bridge.has_provider(): a GTK window exists",
}

ENGINE_MODULES = (
    "engine.py",
    "agent_service.py",
    "assistant.py",
    "tools.py",
    "mcp.py",
    "inventory.py",
    "daemon.py",
    "gateway.py",
    "asyncbridge.py",
    "conversation_store.py",
    "model_choice.py",
    "webtext.py",
    "config.py",
)
ENGINE_PACKAGES = ("skills", "senses", "triggers")


def _engine_files():
    for name in ENGINE_MODULES:
        yield _PKG / name
    for package in ENGINE_PACKAGES:
        yield from sorted((_PKG / package).glob("*.py"))


def _violations(path):
    tree = ast.parse(path.read_text())
    found = []

    class Scan(ast.NodeVisitor):
        def __init__(self):
            self.type_checking = 0

        def visit_If(self, node):
            tc = isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
            if tc:
                self.type_checking += 1
            self.generic_visit(node)
            if tc:
                self.type_checking -= 1

        def _note(self, node, module):
            if self.type_checking or not any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in UI_PREFIXES
            ):
                return
            key = (str(path.relative_to(_PKG)), module)
            if key in ALLOWED:
                return
            found.append((node.lineno, module))

        def visit_ImportFrom(self, node):
            module = node.module or ""
            if module == "gi.repository" and GUI_TYPILIBS.intersection(
                a.name for a in node.names
            ):
                found.append((node.lineno, "gi.repository: " + module))
                return
            self._note(node, module)

        def visit_Import(self, node):
            for alias in node.names:
                self._note(node, alias.name)

    Scan().visit(tree)
    return found


def test_no_engine_module_imports_the_gtk_client():
    bad = {}
    for path in _engine_files():
        found = _violations(path)
        if found:
            bad[str(path.relative_to(_PKG))] = found
    assert not bad, f"engine imports GTK client: {bad}"


def test_gtk_launcher_goes_through_the_boundary():
    script = (_REPO / "usr" / "bin" / "shani-chronoa").read_text()
    assert "from shani_chronoa.ui_gtk import main" in script
    assert "from shani_chronoa.app import main" not in script


def test_ui_gtk_does_not_import_gtk_at_module_scope():
    """The boundary itself stays importable headless; GTK loads on `main()`."""
    source = (_PKG / "ui_gtk" / "__init__.py").read_text()
    assert "from gi.repository import" not in source
    assert "gi.require_version" not in source
