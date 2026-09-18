"""RED tests: skill name/schema validation, duplicate override, mute coercion, web-search privacy, timer notifications.

Each test fails for a named defect in the current code (failing-first / RED phase).
"""

import asyncio
import inspect
import json
import textwrap
import types

import pytest


class TestSkillValidation:
    """Skill registration must reject malformed names/schemas instead of crashing later."""

    def test_non_string_skill_name_is_rejected(self):
        # Given: a skill whose name is an int, not a string
        from shani_chronoa.skills import Skill, _register
        tools, handlers = [], {}
        module = types.SimpleNamespace(SKILLS=[
            Skill(name=123, schema={"type": "function", "function": {"name": "x"}}, run=lambda a: "ok"),
        ])
        # When: it is registered
        _register(module, "test", tools, handlers)
        # Then: it must be skipped (a non-string name would crash sorted() in tools.py)
        assert 123 not in handlers

    def test_string_schema_is_rejected(self):
        # Given: a skill whose schema is a string, not a dict
        from shani_chronoa.skills import Skill, _register
        tools, handlers = [], {}
        module = types.SimpleNamespace(SKILLS=[
            Skill(name="bad", schema="not-a-dict", run=lambda a: "ok"),
        ])
        # When: it is registered
        _register(module, "test", tools, handlers)
        # Then: it must be skipped (a string schema would crash tools.py's dict access)
        assert "bad" not in handlers

    def test_schema_missing_function_is_rejected(self):
        # Given: a skill whose schema has no "function" member
        from shani_chronoa.skills import Skill, _register
        tools, handlers = [], {}
        module = types.SimpleNamespace(SKILLS=[
            Skill(name="bad", schema={"type": "function"}, run=lambda a: "ok"),
        ])
        # When: it is registered
        _register(module, "test", tools, handlers)
        # Then: it must be skipped (tools.py reads schema["function"]["name"])
        assert "bad" not in handlers

    def test_duplicate_override_keeps_single_schema(self):
        # Given: two skills with the same name (a user override of a built-in)
        from shani_chronoa.skills import Skill, _register
        tools, handlers = [], {}
        schema = {"type": "function", "function": {"name": "dup"}}
        _register(
            types.SimpleNamespace(SKILLS=[Skill(name="dup", schema=schema, run=lambda a: "a")]),
            "a", tools, handlers,
        )
        _register(
            types.SimpleNamespace(SKILLS=[Skill(name="dup", schema=schema, run=lambda a: "b")]),
            "b", tools, handlers,
        )
        # Then: exactly one schema must remain for the name
        assert tools.count(schema) == 1

    def test_user_skill_override_does_not_duplicate_schema(self, temp_user_skills_dir):
        # Given: a user skill that overrides a built-in skill name
        (temp_user_skills_dir / "override.py").write_text(textwrap.dedent("""\
            from shani_chronoa.skills import Skill
            _SCHEMA = {"type": "function", "function": {"name": "get_volume", "description": "override", "parameters": {"type": "object", "properties": {}}}}
            SKILLS = [Skill(name="get_volume", schema=_SCHEMA, run=lambda a: "overridden")]
        """))
        from shani_chronoa.skills import discover_skills
        # When: skills are discovered (built-ins + the user override)
        tools, handlers = discover_skills()
        # Then: exactly one schema exists for the overridden name
        names = [s.get("function", {}).get("name") for s in tools]
        assert names.count("get_volume") == 1

    def test_empty_string_skill_name_is_rejected(self):
        # Given: a skill whose name is an empty string
        from shani_chronoa.skills import Skill, _register
        tools, handlers = [], {}
        module = types.SimpleNamespace(SKILLS=[
            Skill(name="", schema={"type": "function", "function": {"name": "x"}}, run=lambda a: "ok"),
        ])
        # When: it is registered
        _register(module, "test", tools, handlers)
        # Then: it must be skipped
        assert tools == []
        assert handlers == {}

    def test_schema_type_not_function_is_rejected(self):
        # Given: a skill whose schema has a type other than "function"
        from shani_chronoa.skills import Skill, _register
        tools, handlers = [], {}
        module = types.SimpleNamespace(SKILLS=[
            Skill(name="bad", schema={"type": "object", "function": {"name": "bad"}}, run=lambda a: "ok"),
        ])
        # When: it is registered
        _register(module, "test", tools, handlers)
        # Then: it must be skipped
        assert "bad" not in handlers

    def test_schema_function_not_dict_is_rejected(self):
        # Given: a skill whose schema's "function" member is not a dict
        from shani_chronoa.skills import Skill, _register
        tools, handlers = [], {}
        module = types.SimpleNamespace(SKILLS=[
            Skill(name="bad", schema={"type": "function", "function": "not-a-dict"}, run=lambda a: "ok"),
        ])
        # When: it is registered
        _register(module, "test", tools, handlers)
        # Then: it must be skipped
        assert "bad" not in handlers

    def test_schema_function_name_empty_is_rejected(self):
        # Given: a skill whose schema's function name is empty
        from shani_chronoa.skills import Skill, _register
        tools, handlers = [], {}
        module = types.SimpleNamespace(SKILLS=[
            Skill(name="bad", schema={"type": "function", "function": {"name": ""}}, run=lambda a: "ok"),
        ])
        # When: it is registered
        _register(module, "test", tools, handlers)
        # Then: it must be skipped
        assert "bad" not in handlers

    def test_schema_function_name_mismatch_is_rejected(self):
        # Given: a skill whose schema advertises a different function name
        from shani_chronoa.skills import Skill, _register
        tools, handlers = [], {}
        module = types.SimpleNamespace(SKILLS=[
            Skill(name="foo", schema={"type": "function", "function": {"name": "bar"}}, run=lambda a: "ok"),
        ])
        # When: it is registered
        _register(module, "test", tools, handlers)
        # Then: it must be skipped (a mismatch would make MCP/Ollama call a name no handler has)
        assert "foo" not in handlers

    def test_override_replaces_prior_schema_in_place(self):
        # Given: two skills with the same name but different schemas
        from shani_chronoa.skills import Skill, _register
        tools, handlers = [], {}
        first = {"type": "function", "function": {"name": "dup", "description": "first"}}
        second = {"type": "function", "function": {"name": "dup", "description": "second"}}
        _register(
            types.SimpleNamespace(SKILLS=[Skill(name="dup", schema=first, run=lambda a: "a")]),
            "a", tools, handlers,
        )
        _register(
            types.SimpleNamespace(SKILLS=[Skill(name="dup", schema=second, run=lambda a: "b")]),
            "b", tools, handlers,
        )
        # Then: the prior schema is replaced, not appended alongside
        assert tools == [second]
        assert handlers["dup"]({}) == "b"

    def test_malformed_user_module_skills_not_list_does_not_crash(self, temp_user_skills_dir):
        # Given: a user module whose SKILLS is a string, not a list
        (temp_user_skills_dir / "bad.py").write_text(textwrap.dedent("""\
            SKILLS = "not-a-list"
        """))
        from shani_chronoa.skills import discover_skills
        # When: skills are discovered
        tools, handlers = discover_skills()
        # Then: built-ins still load and the malformed module is skipped
        assert "get_datetime" in handlers

    def test_malformed_user_module_import_error_does_not_crash(self, temp_user_skills_dir):
        # Given: a user module that raises at import time
        (temp_user_skills_dir / "boom.py").write_text(textwrap.dedent("""\
            raise RuntimeError("boom")
        """))
        from shani_chronoa.skills import discover_skills
        # When: skills are discovered
        tools, handlers = discover_skills()
        # Then: built-ins still load and the crashing module is skipped
        assert "get_datetime" in handlers


class TestMcpSchemaGuards:
    """MCP translation must skip malformed schemas deterministically (hermetic, no mcp SDK)."""

    def test_build_tool_function_rejects_malformed_schema(self):
        # Given: malformed tool schemas
        from shani_chronoa.mcp import _build_tool_function
        # When: they are translated to MCP tool functions
        # Then: None is returned instead of a crash
        assert _build_tool_function("not-a-dict") is None
        assert _build_tool_function({"type": "function"}) is None
        assert _build_tool_function({"type": "function", "function": {"name": ""}}) is None

    def test_build_tool_function_preserves_signature_bridge(self):
        # Given: a valid skill schema with a required integer parameter
        from shani_chronoa.mcp import _build_tool_function
        schema = {
            "type": "function",
            "function": {
                "name": "set_volume",
                "parameters": {
                    "type": "object",
                    "properties": {"percent": {"type": "integer"}},
                    "required": ["percent"],
                },
            },
        }
        # When: it is translated to an MCP tool function
        fn = _build_tool_function(schema)
        # Then: the inspect.Signature bridge reconstructs the parameter shape
        assert fn is not None
        sig = inspect.signature(fn)
        assert list(sig.parameters) == ["percent"]
        assert sig.parameters["percent"].default is inspect.Parameter.empty
        assert sig.parameters["percent"].annotation is int

    def test_build_server_skips_malformed_schemas(self, monkeypatch):
        # Given: a fake MCPServer and a TOOLS list containing a malformed entry
        import shani_chronoa.mcp as mcp_mod
        added = []

        class FakeServer:
            def __init__(self, **kwargs):
                pass

            def add_tool(self, fn, **kwargs):
                added.append(kwargs.get("name"))

        monkeypatch.setattr(mcp_mod, "MCPServer", FakeServer)
        monkeypatch.setattr(mcp_mod, "is_available", lambda: True)
        monkeypatch.setattr(mcp_mod, "TOOLS", [
            {"type": "function", "function": {"name": "good", "parameters": {"type": "object", "properties": {}}}},
            "not-a-dict",
            {"type": "function"},
        ])
        # When: the server is built
        mcp_mod.build_server()
        # Then: only the valid schema is registered as a tool
        assert added == ["good"]

    def test_main_uses_stdio_transport_only(self, monkeypatch):
        # Given: a fake MCPServer that records its run transport
        import shani_chronoa.mcp as mcp_mod
        transports = []

        class FakeServer:
            def __init__(self, **kwargs):
                pass

            def add_tool(self, fn, **kwargs):
                pass

            def run(self, transport="stdio"):
                transports.append(transport)

        monkeypatch.setattr(mcp_mod, "MCPServer", FakeServer)
        monkeypatch.setattr(mcp_mod, "is_available", lambda: True)
        # When: the standalone server entry point runs
        mcp_mod.main()
        # Then: it only ever runs over stdio
        assert transports == ["stdio"]

    def test_module_documents_same_user_stdio_trust(self):
        # Given: the mcp module documentation
        import shani_chronoa.mcp as mcp_mod
        doc = mcp_mod.__doc__ or ""
        # Then: the same-user stdio-only trust model is documented
        assert "stdio" in doc.lower()
        assert "same-user" in doc.lower()

    def test_server_instructions_mention_stdio_only(self):
        # Given: the MCP server instructions sent to clients
        import shani_chronoa.mcp as mcp_mod
        instructions = mcp_mod._SERVER_INSTRUCTIONS
        # Then: they state the stdio-only same-user trust boundary
        assert "stdio" in instructions.lower()
        assert "same-user" in instructions.lower()


class TestCloudSchemaGuards:
    """Cloud tool translation must skip malformed schemas deterministically."""

    def test_anthropic_tools_translation_skips_malformed(self):
        # Given: a tools list containing malformed entries
        from shani_chronoa.cloud_llm import AnthropicLLM
        tools = [
            {"type": "function", "function": {"name": "good", "parameters": {"type": "object", "properties": {}}}},
            "garbage",
            {"type": "function"},
            {"type": "function", "function": {"name": ""}},
        ]
        # When: it is translated to Anthropic's tool format
        converted = AnthropicLLM._tools_to_anthropic(tools)
        # Then: only the well-formed schema is converted
        assert [t["name"] for t in converted] == ["good"]

    def test_openai_compatible_filters_malformed_tools(self, monkeypatch):
        # Given: a tools list containing a malformed entry
        import httpx
        import shani_chronoa.cloud_llm as cloud_mod
        real_async_client = httpx.AsyncClient
        recorded: dict = {}

        def _handler(request):
            recorded["request"] = request
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

        transport = httpx.MockTransport(_handler)

        def _factory(**kwargs):
            return real_async_client(**kwargs, transport=transport)

        monkeypatch.setattr(cloud_mod.httpx, "AsyncClient", _factory)
        from shani_chronoa.cloud_llm import OpenAICompatibleLLM, PROVIDERS
        llm = OpenAICompatibleLLM(PROVIDERS["llm7"])
        tools = [
            {"type": "function", "function": {"name": "good", "parameters": {"type": "object", "properties": {}}}},
            "garbage",
        ]
        # When: a chat request is sent through a mocked transport
        asyncio.run(llm.chat_message([{"role": "user", "content": "hi"}], tools=tools))
        # Then: only the well-formed schema is sent in the payload
        body = json.loads(recorded["request"].content)
        assert [t["function"]["name"] for t in body["tools"]] == ["good"]


class TestToolExecutionGuards:
    """execute_tool must handle untrusted non-dict arguments deterministically."""

    def test_execute_tool_non_dict_arguments_are_ignored(self, monkeypatch):
        # Given: execute_tool routes every call through the sandbox executor as
        # a subprocess command string (SandboxExecutor.execute), not an
        # in-process function call - so this must assert on the command it
        # builds rather than on a directly-observable handler invocation.
        import shani_chronoa.tools as tools_mod
        captured = {}

        def fake_execute(cmd, config, agent_id="default"):
            captured["cmd"] = cmd
            return (0, "ok", 1.0)

        monkeypatch.setattr(tools_mod._SANDBOX, "execute", fake_execute)
        handler_name = next(iter(tools_mod._HANDLER_FNS))
        # When: it is called with a non-dict arguments value
        result = tools_mod.execute_tool(handler_name, "not-a-dict")
        # Then: the arguments are treated as empty (`{}`) in the command built
        # for the sandbox, instead of crashing or forwarding the non-dict value
        assert result == "ok"
        assert "({})" in captured["cmd"]


class TestMuteCoercion:
    """The mute argument must be a real boolean, not coerced by truthiness."""

    @pytest.mark.parametrize(
        "bad", ["false", "true", "0", "1", 0, 1, None, ""]
    )
    def test_mute_non_boolean_is_rejected(self, fake_wpctl, bad):
        # Given: the LLM passes a non-boolean value for the mute argument
        from shani_chronoa.skills.volume import _run_set_mute
        # When: the mute skill runs
        result = _run_set_mute({"mute": bad})
        # Then: it must return a clear error and must not touch wpctl
        log = fake_wpctl.read_text() if fake_wpctl.exists() else ""
        assert result.startswith("Invalid mute argument")
        assert "set-mute" not in log

    def test_mute_boolean_false_unmutes(self, fake_wpctl):
        # Baseline: a real boolean False must unmute.
        from shani_chronoa.skills.volume import _run_set_mute
        result = _run_set_mute({"mute": False})
        assert result == "Unmuted."
        assert "set-mute @DEFAULT_AUDIO_SINK@ 0" in fake_wpctl.read_text()

    def test_mute_boolean_true_mutes(self, fake_wpctl):
        # Baseline: a real boolean True must mute.
        from shani_chronoa.skills.volume import _run_set_mute
        result = _run_set_mute({"mute": True})
        assert result == "Muted."
        assert "set-mute @DEFAULT_AUDIO_SINK@ 1" in fake_wpctl.read_text()


class TestVolumeCoercion:
    """The percent argument must be a real integer, not coerced or truncated."""

    @pytest.mark.parametrize("bad", [50.0, "50", True, False, None, "abc"])
    def test_volume_non_integer_percent_is_rejected(self, fake_wpctl, bad):
        # Given: the LLM passes a non-integer value for the percent argument
        from shani_chronoa.skills.volume import _run_set_volume
        # When: the volume skill runs
        result = _run_set_volume({"percent": bad})
        # Then: it must return a clear error and must not touch wpctl
        log = fake_wpctl.read_text() if fake_wpctl.exists() else ""
        assert result.startswith("Invalid volume percentage")
        assert "set-volume" not in log

    def test_volume_valid_integer_percent(self, fake_wpctl):
        # Baseline: a real integer percent must set the volume.
        from shani_chronoa.skills.volume import _run_set_volume
        result = _run_set_volume({"percent": 50})
        assert result == "Volume set to 50%."
        assert "set-volume @DEFAULT_AUDIO_SINK@ 50%" in fake_wpctl.read_text()

    def test_volume_percent_clamps_above_100(self, fake_wpctl):
        # Baseline: an integer above 100 must clamp to 100.
        from shani_chronoa.skills.volume import _run_set_volume
        result = _run_set_volume({"percent": 150})
        assert result == "Volume set to 100%."
        assert "set-volume @DEFAULT_AUDIO_SINK@ 100%" in fake_wpctl.read_text()

    def test_volume_percent_clamps_below_0(self, fake_wpctl):
        # Baseline: an integer below 0 must clamp to 0.
        from shani_chronoa.skills.volume import _run_set_volume
        result = _run_set_volume({"percent": -10})
        assert result == "Volume set to 0%."
        assert "set-volume @DEFAULT_AUDIO_SINK@ 0%" in fake_wpctl.read_text()


class TestWebSearchPrivacy:
    """Web search must be blocked in local-only privacy mode."""

    def test_web_search_blocked_in_local_only_mode(self, mock_launch_uri, privacy_manager):
        # Given: local-only privacy mode is active (schema default privacy-mode=true)
        assert privacy_manager.is_local_only
        from shani_chronoa.skills.web_search import _run
        # When: the web search skill is invoked
        _run({"query": "secret query"})
        # Then: no browser may be launched
        assert mock_launch_uri == []

    def test_web_search_launches_when_privacy_off(self, mock_launch_uri, chronoa_config):
        # Baseline: with privacy mode explicitly off, the browser launch is attempted.
        from shani_chronoa.config import PrivacyManager
        chronoa_config.set("privacy-mode", "false")
        privacy = PrivacyManager(chronoa_config)
        assert not privacy.is_local_only
        from shani_chronoa.skills.web_search import _run
        _run({"query": "hello"})
        assert len(mock_launch_uri) == 1


class TestTimerNotifications:
    """Timer completion must respect the notification-enabled setting and run without GLib."""

    def _capture_timer(self, monkeypatch):
        captured: dict = {}

        def _fake_timer(interval, function):
            captured["interval"] = interval
            captured["function"] = function
            timer = types.SimpleNamespace(
                daemon=False, start=lambda: captured.setdefault("started", True)
            )
            captured["timer"] = timer
            return timer

        monkeypatch.setattr("shani_chronoa.skills.timer.threading.Timer", _fake_timer)
        return captured

    def test_timer_notify_send_respects_notification_enabled(
        self, mock_notify_send, chronoa_config, monkeypatch
    ):
        # Given: desktop notifications are disabled
        chronoa_config.set("notification-enabled", "false")
        captured = self._capture_timer(monkeypatch)
        from shani_chronoa.skills.timer import _run
        # When: a timer is set and fires
        _run({"seconds": 60, "label": "pasta"})
        captured["function"]()
        # Then: notify-send must not be invoked
        log = mock_notify_send.read_text() if mock_notify_send.exists() else ""
        assert log == ""

    def test_timer_notify_send_fires_when_enabled(
        self, mock_notify_send, chronoa_config, monkeypatch
    ):
        # Given: desktop notifications are enabled
        chronoa_config.set("notification-enabled", "true")
        captured = self._capture_timer(monkeypatch)
        from shani_chronoa.skills.timer import _run
        # When: a timer is set and fires
        _run({"seconds": 60, "label": "pasta"})
        captured["function"]()
        # Then: notify-send is invoked with the timer label
        log = mock_notify_send.read_text() if mock_notify_send.exists() else ""
        assert "pasta" in log

    def test_timer_schedules_daemon_thread(self, monkeypatch):
        # Given: a valid integer duration
        captured = self._capture_timer(monkeypatch)
        from shani_chronoa.skills.timer import _run
        # When: a timer is set
        result = _run({"seconds": 60, "label": "pasta"})
        # Then: a daemon threading.Timer is scheduled with the exact seconds
        assert result == "Timer set for 60 seconds: 'pasta'."
        assert captured["interval"] == 60
        assert captured["timer"].daemon is True
        assert captured["started"] is True

    @pytest.mark.parametrize("bad", [60.0, "60", True, False, None])
    def test_timer_non_integer_seconds_is_rejected(self, monkeypatch, bad):
        # Given: a non-integer duration
        captured = self._capture_timer(monkeypatch)
        from shani_chronoa.skills.timer import _run
        # When: a timer is set
        result = _run({"seconds": bad})
        # Then: it must return a clear error and schedule nothing
        assert result.startswith("Invalid timer duration")
        assert "function" not in captured