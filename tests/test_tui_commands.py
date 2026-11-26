"""Slash parsing is plain data; the engine still decides everything else."""

from shani_chronoa.tui import commands


def test_plain_text_is_not_a_command():
    assert commands.parse("hello world") == ("", "hello world")
    assert commands.parse("/") == ("", "/")
    assert commands.parse("//") == ("", "//")


def test_commands_are_lowercased_and_split_from_arguments():
    assert commands.parse("/models conversion") == ("models", "conversion")
    assert commands.parse("  /RESUME  turn-42  ") == ("resume", "turn-42")
    assert commands.parse("/help") == ("help", "")


def test_unknown_commands_still_parse_so_the_client_can_say_so():
    assert commands.parse("/nonsense arg") == ("nonsense", "arg")


def test_slash_completion_only_offers_real_commands():
    assert commands.completions("/mod") == ["/models"]
    assert commands.completions("/m") == ["/memory", "/models"]
    assert commands.completions("/zzz") == []
    assert all(name.startswith("/") for name in commands.completions("/"))


def test_subcommand_completion_offers_the_additive_paths():
    assert "/settings get" in commands.completions("/settings g")
    assert "/models use" in commands.completions("/models u")
    assert "/conversations delete" in commands.completions("/conversations d")


def test_additive_commands_resolve_to_agent_methods():
    assert commands.resolve("models", "") == ("ModelsList", [])
    assert commands.resolve("models", "use local") == ("ModelsUse", ["local"])
    assert commands.resolve("settings", "set privacy-mode true") == (
        "SettingsSet",
        ["privacy-mode", "true"],
    )
    assert commands.resolve("conversations", "rename abc A longer title") == (
        "ConversationsRename",
        ["abc", "A longer title"],
    )


def test_path_completion_expands_home_and_marks_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "projects").mkdir()
    assert any(
        path.endswith("notes.txt") for path in commands.path_completions("@~/no")
    )
    assert any(
        path.endswith("projects/") for path in commands.path_completions("@~/pro")
    )
    assert commands.path_completions("@~/missing") == []


def test_help_covers_every_command():
    lines = commands.help_lines()
    assert len(lines) == len(commands.COMMANDS)
    for name in commands.COMMANDS:
        assert any(line.startswith(f"/{name} ") for line in lines)


def test_skills_reads_the_capabilities_section_too():
    assert commands.SECTION_COMMANDS["skills"] == "skills"
    assert "capabilities" in commands.EXTRA_SECTIONS["skills"]
    assert commands.SECTION_COMMANDS["conversations"] == "conversations"


def test_help_has_one_line_per_command():
    lines = commands.help_lines()
    heads = [line.split(" - ", 1)[0] for line in lines]
    assert len(heads) == len(set(heads)), [h for h in heads if heads.count(h) > 1]
    for name in ("settings", "models", "memory", "notices"):
        help_text = commands.COMMANDS[name].help
        assert sum(line.split(" - ", 1)[1] == help_text for line in lines) == 1
