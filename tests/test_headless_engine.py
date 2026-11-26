"""Chronoa's engine starts and answers with no GTK typelib in the process.

The UI is a client of the engine, not a part of it. This test blocks
`gi.repository.Gtk`/`Adw`/`Gdk`/`Gtk4LayerShell` before importing anything,
builds the headless Engine with the same stand-in Ollama the repo's tool-loop
tests use, drives one ask to a reply, and checks the Agent service is started.
It runs in a subprocess: `gi.repository` is process-global, so an in-process
block would poison the rest of the suite at collection time.

The negative control is part of the script: `shani_chronoa.app` must still
fail under the block. A test that cannot fail the moment Gtk becomes loadable
again is not a check of the boundary.
"""

import json
from pathlib import Path
import subprocess
import sys
import textwrap

_REPO = Path(__file__).resolve().parents[1]

_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import json
    import sys
    from pathlib import Path

    repo = Path(sys.argv[1])
    sys.path.insert(0, str(repo / "usr" / "lib" / "shani-chronoa"))
    sys.path.insert(0, str(repo / "tests"))

    BLOCKED = {
        "gi.repository.Gtk", "gi.repository.Adw", "gi.repository.Gdk",
        "gi.repository.Gtk4LayerShell",
    }

    class BlockGtk:
        def find_spec(self, fullname, path=None, target=None):
            if fullname in BLOCKED:
                raise ImportError("BLOCKED " + fullname)

    sys.meta_path.insert(0, BlockGtk())

    from shani_chronoa.engine import Engine
    from shani_chronoa.agent_service import AgentService
    from test_tool_loop_continuity import _MockOllama, _final_reply

    exported = []
    AgentService.export = lambda self: exported.append("agent")

    mock = _MockOllama([_final_reply("hello from a headless engine")])
    engine = Engine(llm=mock.build())
    engine.start()
    answer = asyncio.run(engine.assistant.handle("say hello"))

    gui_blocked = False
    try:
        import shani_chronoa.app  # noqa: F401
    except ImportError as exc:
        gui_blocked = "BLOCKED" in str(exc)

    print("RESULT " + json.dumps({
        "answer": answer,
        "started": exported,
        "gui_blocked": gui_blocked,
        "has_agent": bool(engine.agent),
    }))
    """
)


def test_engine_answers_with_gtk_blocked():
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(_REPO)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(_REPO),
        env=None,
    )
    line = next(
        (line for line in proc.stdout.splitlines() if line.startswith("RESULT ")), None
    )
    assert line is not None, f"engine did not start:\n{proc.stdout}\n{proc.stderr}"
    result = json.loads(line[len("RESULT ") :])

    assert result["answer"] == "hello from a headless engine"
    assert result["started"] == ["agent"]
    assert result["has_agent"] is True
    assert result["gui_blocked"] is True, "the Gtk block did not take effect"
