"""Rules file, /commands and @mentions (user_prompts.py), and the rules reaching the model but not the transcript."""

import os
from pathlib import Path

from shani_chronoa import user_prompts as up


def _cfg():
    d = up.config_dir()
    (d / "commands").mkdir(parents=True, exist_ok=True)
    return d


def test_rules_are_sent_every_turn_and_never_recorded(tmp_path):
    from shani_chronoa.assistant import Assistant
    from shani_chronoa import conversation_store
    (_cfg() / "rules.md").write_text("Answer in British English.\nMy laptop is called atlas.")
    path = tmp_path / "s.jsonl"
    a = Assistant(llm=None, session_path=path)
    a._record({"role": "user", "content": "hi"})
    msgs = a.build_messages()
    assert msgs[1]["role"] == "system" and "My laptop is called atlas." in msgs[1]["content"]
    assert all("atlas" not in str(m) for m in conversation_store.load(path))
    (_cfg() / "rules.md").write_text("x" * 9000)
    assert len(a.build_messages()[1]["content"]) < 4300, "capped"
    (_cfg() / "rules.md").unlink()
    assert all("rules" not in m["content"] for m in a.build_messages() if m["role"] == "system"
               and m is not a.build_messages()[0])


def test_commands_expand_arguments_and_files_but_never_shell():
    d = _cfg()
    home = Path(os.environ["HOME"])
    (home / "notes.txt").write_text("shipped the matrix")
    (d / "commands" / "standup.md").write_text("---\ndescription: x\n---\nWrite my standup for $1 using @{~/notes.txt}. "
                                               "Also !{rm -rf ~} and all: $ARGUMENTS")
    out, note = up.prepare("/standup Monday please")
    assert note == "/standup" and "Write my standup for Monday" in out and "shipped the matrix" in out
    assert "rm -rf" not in out and "not supported" in out and out.endswith("all: Monday please")
    (d / "commands" / "plain.md").write_text("Summarise this:")
    assert up.prepare("/plain the text")[0] == "Summarise this:\n\nthe text"
    assert up.prepare("/unknown thing") == ("/unknown thing", "")
    listed = up.prepare("/commands")
    assert listed[0] == "" and "/standup" in listed[1] and "/plain" in listed[1]


def test_mentions_fill_in_files_inside_home_only():
    home = Path(os.environ["HOME"])
    (home / "todo.md").write_text("buy milk")
    out = up.expand_mentions("what's on @~/todo.md?")
    assert "buy milk" in out and "end of todo.md" in out
    assert "not included" in up.expand_mentions("read @/etc/passwd")
    assert up.expand_mentions("mail me at a@b.com") == "mail me at a@b.com"
