"""The `project_outline` half, moved with the skill to shani-sysadmin."""

import os
from pathlib import Path

from shani_sysadmin.skills import project_outline as po


def test_outline_ranks_the_used_module_first_and_reads_python_exactly():
    proj = Path(os.environ["HOME"]) / "proj"
    (proj / "node_modules").mkdir(parents=True)
    (proj / "core.py").write_text("class Store:\n    def load(self, path):\n        pass\n\ndef helper(x):\n    return x\n")
    (proj / "app.py").write_text("from core import Store, helper\nStore().load('a'); Store(); helper(1)\ndef main():\n    pass\n")
    (proj / "web.ts").write_text("export function render(x) {}\nexport class View {}\n")
    (proj / "node_modules" / "junk.js").write_text("function ignored() {}\n")
    (proj / "broken.py").write_text("def (:\n")
    out = po._run({"path": str(proj)})
    lines = out.splitlines()
    assert lines[1] == "core.py:", out
    assert "  1: class Store" in out and "  2:   def load(path)" in out and "  5: def helper(x)" in out
    assert "web.ts:" in out and "export function render" in out
    assert "ignored" not in out and "(does not parse)" in out
    focused = po._run({"path": str(proj), "query": "render"})
    assert "web.ts" in focused and "core.py" not in focused
    assert "outside your home" in po._run({"path": "/usr"})
