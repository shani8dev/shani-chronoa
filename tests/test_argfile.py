"""Tests for the file-based argument transport (`shani_chronoa/argfile.py`).

The transport only exists because the inline one has a measured ceiling, so
these tests are written against real subprocesses wherever the claim is about
bytes on disk or about a file's permissions. `_SANDBOX.execute` is NOT mocked
in `TestRealSubprocessRoundTrip`: a mocked executor proves nothing about
whether a payload survives a real `execve`, and this repo has shipped bugs
that read correctly and died on execution.

The oversized cases use a >1 MiB payload with embedded NUL bytes, not a
"hello world" string: an encoding round trip survives short ASCII and fails on
either of those.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest

from shani_chronoa import argfile

# Every byte value 0..255, including NUL, repeated to just over 2 MB.
BINARY_PAYLOAD = bytes(range(256)) * 8000
# 1.26 MB of text carrying 4200 NUL bytes: a naive str/bytes encode-decode pair
# or a text-mode temp file is where this breaks.
TEXT_PAYLOAD = ("NUL\x00inside" + "Z" * 300) * 4200

_CHILD_MODULE = textwrap.dedent('''\
    import hashlib
    import json
    import os
    import stat

    from shani_chronoa.argfile import FileRef


    def echo_text(arguments):
        return arguments["label"].read_text()


    def echo_binary(arguments):
        ref = arguments["image"]
        with open(str(ref), "rb") as handle:
            data = handle.read()
        return json.dumps({
            "is_fileref": isinstance(ref, FileRef),
            "is_str": isinstance(ref, str),
            "os_fspath_ok": os.fspath(ref) == str(ref),
            "kind": ref.kind,
            "declared_size": ref.size,
            "file_len": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "has_nul": b"\\x00" in data,
            "payload_mode": oct(stat.S_IMODE(os.stat(str(ref)).st_mode)),
            "dir_mode": oct(stat.S_IMODE(os.stat(os.path.dirname(str(ref))).st_mode)),
        })


    def explode(arguments):
        raise ValueError("deliberate child failure")


    def needs_a_path(arguments):
        return os.stat(arguments["probe"]).st_size
''')


@pytest.fixture
def argfiles_root(tmp_path, monkeypatch):
    """Point the transport's state directory at a temp HOME-owned path."""
    root = tmp_path / "argfiles"
    monkeypatch.setattr(argfile, "_ARGFILE_ROOT", root)
    return root


@pytest.fixture
def child_module(tmp_path):
    """A real importable module for the child process to import."""
    directory = tmp_path / "childmods"
    directory.mkdir()
    (directory / "argfile_probe.py").write_text(_CHILD_MODULE)
    return directory


def run_real_child(payload, child_module, extra_env=None):
    """Run the actual child program in an actual interpreter.

    Deliberately not `_SANDBOX.execute`: this wants the interpreter, not the
    sandbox policy, and going through the shell would only add the very
    `E2BIG`/quoting layer the file transport exists to avoid.
    """
    package_root = os.path.dirname(os.path.dirname(argfile.__file__))
    program = (
        f"import sys; sys.path.insert(0, {package_root!r}); "
        f"sys.path.insert(0, {str(child_module)!r}); "
        f"from shani_chronoa.argfile import run_from_file; "
        f"sys.exit(run_from_file(sys.argv[1]))"
    )
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run(
        [sys.executable, "-c", program, payload.envelope_path],
        capture_output=True, text=True, env=env, timeout=120,
    )


class TestTransportSelection:
    """When each transport is used, and when it must not be."""

    def test_primitive_arguments_keep_the_inline_path(self, argfiles_root):
        # Given: the shape of argument every built-in skill actually receives
        # Then: no temp file is created at all and the caller keeps the inline
        #       path, so no existing skill changes transport
        assert argfile.reference_command("shani_chronoa.skills.volume", "_run_set_volume", {"percent": 50}) is None
        assert not argfiles_root.exists()

    def test_bytes_argument_forces_the_reference_path_without_being_asked(self, argfiles_root):
        # Given: a binary value, which json.dumps cannot represent at all
        payload = argfile.reference_command("m", "f", {"image": b"\x89PNG\x00\xff"})
        # Then: it is passed by reference on its own, with no opt-in flag
        try:
            assert payload is not None
            assert os.path.isdir(payload.directory)
        finally:
            payload.cleanup()

    @pytest.mark.parametrize("value", [b"x", bytearray(b"x"), memoryview(b"x")])
    def test_every_bytes_like_type_is_carried(self, argfiles_root, value):
        payload = argfile.reference_command("m", "f", {"v": value})
        try:
            assert payload is not None
        finally:
            payload.cleanup()

    def test_nested_bytes_is_carried_not_left_to_the_json_encoder(self, argfiles_root):
        payload = argfile.reference_command("m", "f", {"a": {"b": [1, {"c": b"\x00"}]}})
        try:
            envelope = json.loads(open(payload.envelope_path, encoding="utf-8").read())
            marker = envelope["arguments"]["a"]["b"][1]["c"]
            assert marker["$chronoa_file"]["kind"] == "bytes"
        finally:
            payload.cleanup()

    def test_oversized_text_is_not_switched_silently(self, argfiles_root):
        # Given: a merely large string, which the inline path *can* express
        # Then: it stays inline unless the caller opts in, because handing a
        #       skill a path where it expected text is a silent wrong answer
        #       while E2BIG is at least a loud one
        assert argfile.reference_command("m", "f", {"label": "A" * (512 * 1024)}) is None

    def test_by_reference_flag_is_honoured_for_small_arguments_too(self, argfiles_root):
        payload = argfile.reference_command("m", "f", {"label": "short"}, by_reference=True)
        try:
            assert payload is not None
        finally:
            payload.cleanup()

    def test_non_dict_arguments_are_left_to_the_caller(self, argfiles_root):
        assert argfile.reference_command("m", "f", "not-a-dict") is None

    def test_whitelist_is_untouched_by_the_transport(self):
        # Given: the module that owns the closed set of skills
        import shani_chronoa.tools as tools_mod
        # Then: adding an argument transport added no new callable action, and
        #       every level is still the hard-coded per-name mapping
        assert tools_mod._get_sandbox_config("anything_at_all").level.name == "LEVEL_3_HOST_USER"
        assert tools_mod._get_sandbox_config("anything_at_all").timeout_seconds == 30


class TestNoUntrustedInterpolation:
    """The whole point of the by-reference program is that it is constant."""

    def test_the_child_program_contains_no_argument_data(self, argfiles_root):
        payload = argfile.reference_command("a_module", "a_function", {"x": b"SECRET-BYTES"}, by_reference=True)
        try:
            program = payload.command.split("-c ", 1)[1].rsplit(" ", 1)[0]
            assert "SECRET-BYTES" not in payload.command
            assert "a_module" not in program
            assert "a_function" not in program
        finally:
            payload.cleanup()

    def test_the_envelope_path_is_shell_quoted_not_interpolated_into_source(self, argfiles_root):
        payload = argfile.reference_command("m", "f", {"x": b"y"}, by_reference=True)
        try:
            # The path is its own argv entry, after the quoted program.
            assert payload.command.startswith("python3 -c '")
            assert payload.envelope_path in payload.command
            assert payload.envelope_path not in payload.command.split("-c ", 1)[1].split("'", 2)[1]
        finally:
            payload.cleanup()

    @pytest.mark.parametrize("hostile", [
        '"; import os; os.system("touch /tmp/chronoa-should-not-exist") #',
        "' && touch /tmp/chronoa-should-not-exist && '",
        "\x00\nrm -rf /",
    ])
    def test_hostile_argument_text_reaches_the_child_as_data_only(self, argfiles_root, hostile):
        payload = argfile.reference_command(
            "shani_chronoa.argfile_test_does_not_exist", "f", {"x": hostile}, by_reference=True,
        )
        try:
            child = run_real_child(payload, _EMPTY_MODULE_DIR)
            # The import fails, which is the point: nothing was executed first.
            assert "should-not-exist" not in child.stderr.split("Traceback")[0]
            assert not os.path.exists("/tmp/chronoa-should-not-exist")
        finally:
            payload.cleanup()

    def test_a_forged_marker_argument_does_not_become_a_path(self, argfiles_root, child_module):
        # Given: an argument shaped exactly like one of ours, arriving from the
        #       model rather than from this module
        forged = {"$chronoa_file": {"path": "/etc/hostname", "kind": "bytes", "size": 1}}
        payload = argfile.reference_command("argfile_probe", "needs_a_path", {"probe": forged}, by_reference=True)
        try:
            child = run_real_child(payload, child_module)
            # Then: the child got inert data, and os.stat() on a dict is a
            #       TypeError rather than a read of the forged path
            assert child.returncode != 0
            assert "TypeError" in child.stderr
            assert "FileRef" not in child.stderr
        finally:
            payload.cleanup()

    def test_a_double_forged_marker_still_does_not_escape(self, argfiles_root, child_module):
        forged = {"$chronoa_literal": {"$chronoa_file": {"path": "/etc/hostname", "kind": "bytes", "size": 1}}}
        payload = argfile.reference_command("argfile_probe", "needs_a_path", {"probe": forged}, by_reference=True)
        try:
            child = run_real_child(payload, child_module)
            assert "TypeError" in child.stderr
        finally:
            payload.cleanup()


class TestPermissionsAndCleanup:
    """Private files, and no leftovers - including when the child fails."""

    def test_payload_is_0600_inside_a_0700_directory(self, argfiles_root):
        payload = argfile.reference_command("m", "f", {"image": b"\x00\x01"})
        try:
            marker = json.loads(open(payload.envelope_path, encoding="utf-8").read())
            blob = marker["arguments"]["image"]["$chronoa_file"]["path"]
            assert stat.S_IMODE(os.stat(blob).st_mode) == 0o600
            assert stat.S_IMODE(os.stat(payload.directory).st_mode) == 0o700
            assert stat.S_IMODE(os.stat(payload.envelope_path).st_mode) == 0o600
        finally:
            payload.cleanup()

    def test_cleanup_removes_every_file(self, argfiles_root):
        payload = argfile.reference_command("m", "f", {"a": b"1", "b": "B" * 50000})
        directory = payload.directory
        assert len(os.listdir(directory)) == 3  # envelope + two payloads
        payload.cleanup()
        assert not os.path.exists(directory)

    def test_cleanup_is_idempotent(self, argfiles_root):
        payload = argfile.reference_command("m", "f", {"a": b"1"})
        payload.cleanup()
        payload.cleanup()
        assert not os.path.exists(payload.directory)

    def test_cleanup_happens_when_the_child_raises(self, argfiles_root, child_module):
        payload = argfile.reference_command("argfile_probe", "explode", {"image": b"\x00"})
        child = run_real_child(payload, child_module)
        directory = payload.directory
        payload.cleanup()
        assert child.returncode != 0
        assert "deliberate child failure" in child.stderr
        assert not os.path.exists(directory)

    def test_execute_tool_cleans_up_when_the_executor_raises(self, monkeypatch, argfiles_root):
        # Given: a real by-reference dispatch whose executor blows up
        import shani_chronoa.tools as tools_mod
        monkeypatch.setitem(tools_mod._HANDLER_FNS, "probe_explode", _explode_handler)
        monkeypatch.setattr(tools_mod._TRACKER, "record_call", lambda *a, **k: None)
        made = {}
        real_reference = argfile.reference_command

        def spy(module, function, arguments, by_reference=False):
            payload = real_reference(module, function, arguments, by_reference)
            made["directory"] = payload.directory if payload else None
            return payload

        def exploding_execute(cmd, config, agent_id="default"):
            raise OSError("executor is down")

        monkeypatch.setattr(tools_mod.argfile, "reference_command", spy)
        monkeypatch.setattr(tools_mod._SANDBOX, "execute", exploding_execute)
        # Then: the call reports the failure and the payload is still removed
        result = tools_mod.execute_tool("probe_explode", {"image": b"\x00" * 4096})
        assert "executor is down" in result
        assert made["directory"] is not None
        assert not os.path.exists(made["directory"])

    def test_execute_tool_cleans_up_when_the_child_fails(self, monkeypatch, argfiles_root, child_module):
        # Given: a real dispatch whose child exits non-zero. The handler lives
        #       in a test-only module, and `_run_host()` deliberately builds a
        #       minimal env (secrets + DISPLAY/HOME/USER, no PYTHONPATH), so the
        #       child cannot import it - which is itself a genuine child-side
        #       failure and is what this asserts.
        import sys as _sys
        import shani_chronoa.tools as tools_mod
        _sys.path.insert(0, str(child_module))
        try:
            import argfile_probe
        finally:
            _sys.path.remove(str(child_module))
        monkeypatch.setitem(tools_mod._HANDLER_FNS, "probe_explode", argfile_probe.explode)
        recorded = []
        # `record_call` also takes `origin=` (who asked for the call), so the
        # stub has to accept keyword arguments. The assertion below still reads
        # `recorded[0][2]` as the result string, unchanged.
        monkeypatch.setattr(
            tools_mod._TRACKER, "record_call", lambda *a, **k: recorded.append(a)
        )
        made = {}
        real_reference = argfile.reference_command

        def spy(module, function, arguments, by_reference=False):
            payload = real_reference(module, function, arguments, by_reference)
            made["directory"] = payload.directory if payload else None
            return payload

        monkeypatch.setattr(tools_mod.argfile, "reference_command", spy)
        # Then: a non-zero child is reported, tracked, and leaves nothing behind
        result = tools_mod.execute_tool("probe_explode", {"image": b"\x00" * 4096})
        assert "ModuleNotFoundError" in result
        assert made["directory"] is not None
        assert not os.path.exists(made["directory"])
        assert recorded and recorded[0][2].startswith("ERROR(exit=1)")


class TestRealSubprocessRoundTrip:
    """A real interpreter, real files, real bytes. No mocks in this class."""

    def test_binary_payload_survives_a_real_child_byte_for_byte(self, argfiles_root, child_module):
        payload = argfile.reference_command("argfile_probe", "echo_binary", {"image": BINARY_PAYLOAD})
        try:
            child = run_real_child(payload, child_module)
            assert child.returncode == 0, child.stderr
            seen = json.loads(child.stdout)
            assert seen["sha256"] == hashlib.sha256(BINARY_PAYLOAD).hexdigest()
            assert seen["file_len"] == len(BINARY_PAYLOAD) == 2048000
            assert seen["declared_size"] == len(BINARY_PAYLOAD)
            assert seen["has_nul"] is True
            assert seen["kind"] == "bytes"
        finally:
            payload.cleanup()

    def test_the_child_receives_a_path_usable_as_a_plain_string(self, argfiles_root, child_module):
        payload = argfile.reference_command("argfile_probe", "echo_binary", {"image": b"\x89PNG"})
        try:
            seen = json.loads(run_real_child(payload, child_module).stdout)
            assert seen["is_fileref"] and seen["is_str"] and seen["os_fspath_ok"]
        finally:
            payload.cleanup()

    def test_permissions_observed_from_inside_the_running_child(self, argfiles_root, child_module):
        # Given: a payload file that another user must not be able to read
        payload = argfile.reference_command("argfile_probe", "echo_binary", {"image": BINARY_PAYLOAD})
        try:
            seen = json.loads(run_real_child(payload, child_module).stdout)
            # Then: the child itself measures 0600/0700 while it is reading
            assert seen["payload_mode"] == "0o600"
            assert seen["dir_mode"] == "0o700"
        finally:
            payload.cleanup()

    def test_oversized_text_with_nul_bytes_survives_a_real_child(self, argfiles_root, child_module):
        payload = argfile.reference_command("argfile_probe", "echo_text", {"label": TEXT_PAYLOAD}, by_reference=True)
        try:
            child = run_real_child(payload, child_module)
            assert child.returncode == 0, child.stderr
            assert len(TEXT_PAYLOAD) == 1302000
            assert child.stdout == TEXT_PAYLOAD
            assert child.stdout.count("\x00") == 4200
            assert hashlib.sha256(child.stdout.encode()).hexdigest() == hashlib.sha256(TEXT_PAYLOAD.encode()).hexdigest()
        finally:
            payload.cleanup()

    def test_the_inline_path_cannot_carry_the_same_payload_at_all(self, argfiles_root):
        # Given: what the pre-existing inline transport does with a payload it
        #       cannot express, measured by really trying it
        oversized = "A" * (512 * 1024)
        import shlex
        program = f"import sys; sys.stdout.write({json.dumps(oversized)})"
        # Then: execve itself refuses, which is the defect being fixed
        with pytest.raises(OSError) as excinfo:
            subprocess.run(["/bin/sh", "-c", f"python3 -c {shlex.quote(program)}"], capture_output=True, text=True)
        assert "Argument list too long" in str(excinfo.value) or excinfo.value.errno == 7

    def test_json_dumps_alone_cannot_represent_a_bytes_argument(self, argfiles_root):
        # Given: why the transport exists at all, pinned so a future change
        #       cannot quietly make the inline path look adequate
        # Then: json.dumps silently turns image bytes into a repr string
        assert json.dumps({"image": b"\x89PNG"}, default=str) == '{"image": "b\'\\\\x89PNG\'"}'


class TestFileRef:
    def test_is_a_str_carrying_the_path(self, argfiles_root):
        payload = argfile.reference_command("m", "f", {"image": b"abc"})
        try:
            ref = argfile._materialize(
                json.loads(open(payload.envelope_path, encoding="utf-8").read())["arguments"]
            )["image"]
            assert isinstance(ref, str)
            assert str(ref) == ref.path.as_posix() or os.path.exists(str(ref))
            assert ref.kind == "bytes" and ref.size == 3
            assert ref.read_bytes() == b"abc"
        finally:
            payload.cleanup()

    def test_read_text_is_strict_about_binary(self, argfiles_root):
        payload = argfile.reference_command("m", "f", {"v": b"\xff\xfe"})
        try:
            ref = argfile._materialize(
                json.loads(open(payload.envelope_path, encoding="utf-8").read())["arguments"]
            )["v"]
            with pytest.raises(UnicodeDecodeError):
                ref.read_text()
        finally:
            payload.cleanup()

    def test_an_oversized_container_also_becomes_a_json_ref(self, argfiles_root):
        # Given: a structured value far larger than the cap
        big = {"k" + str(i): i for i in range(20000)}
        assert len(json.dumps(big)) > argfile.MAX_INLINE_VALUE_BYTES
        payload = argfile.reference_command("m", "f", {"probe": big}, by_reference=True)
        try:
            envelope = json.loads(open(payload.envelope_path, encoding="utf-8").read())
            # Then: the cap is uniform, so a skill can rely on "a big value is
            #       a file" and never have to reason about which shape it was
            assert envelope["arguments"]["probe"]["$chronoa_file"]["kind"] == "json"
            assert json.loads(argfile._materialize(envelope["arguments"])["probe"].read_text()) == big
        finally:
            payload.cleanup()

    def test_a_small_container_stays_a_container(self, argfiles_root):
        payload = argfile.reference_command("m", "f", {"probe": {"a": [1, 2, 3]}}, by_reference=True)
        try:
            envelope = json.loads(open(payload.envelope_path, encoding="utf-8").read())
            assert argfile._materialize(envelope["arguments"]) == {"probe": {"a": [1, 2, 3]}}
        finally:
            payload.cleanup()

    def test_an_oversized_scalar_becomes_a_text_ref(self, argfiles_root, child_module):
        # Given: a single string past the cap
        long_text = "Q" * (argfile.MAX_INLINE_VALUE_BYTES + 1)
        payload = argfile.reference_command("argfile_probe", "echo_text", {"label": long_text}, by_reference=True)
        try:
            envelope = json.loads(open(payload.envelope_path, encoding="utf-8").read())
            marker = envelope["arguments"]["label"]["$chronoa_file"]
            # Then: the skill gets a text ref it can decode back to itself
            assert marker["kind"] == "text"
            assert marker["size"] == len(long_text)
            assert argfile._materialize(envelope["arguments"])["label"].read_text() == long_text
        finally:
            payload.cleanup()

    def test_a_container_holding_bytes_spills_those_bytes_not_the_repr(self, argfiles_root):
        # Given: an oversized container that also holds binary
        mixed = {"blob": b"\x89PNG\x00", "pad": "p" * 200000}
        payload = argfile.reference_command("m", "f", {"probe": mixed}, by_reference=True)
        try:
            envelope = json.loads(open(payload.envelope_path, encoding="utf-8").read())
            probe = argfile._materialize(envelope["arguments"])["probe"]
            # Then: the bytes became their own real file instead of a repr
            #       string, the oversized member became a text ref, and the
            #       container shrank enough to stay a plain dict
            assert isinstance(probe, dict)
            assert probe["blob"].kind == "bytes"
            assert probe["blob"].read_bytes() == b"\x89PNG\x00"
            assert probe["pad"].kind == "text"
            assert probe["pad"].read_text() == "p" * 200000
        finally:
            payload.cleanup()


class TestToolTrackerTolerance:
    """A binary argument must not turn a successful call into a failed one."""

    def test_recording_a_bytes_argument_does_not_raise(self, tmp_path):
        from shani_chronoa.tool_tracking import ToolTracker
        tracker = ToolTracker(log_dir=tmp_path / "logs")
        tracker.record_call("probe", {"image": b"\x00\xff"}, "ok", 1.0)
        assert "probe" in (tmp_path / "logs" / "tool_calls.log").read_text()

    def test_a_huge_argument_is_omitted_from_the_log_but_kept_in_memory(self, tmp_path):
        from shani_chronoa.tool_tracking import MAX_LOGGED_ARGS_CHARS, ToolTracker
        tracker = ToolTracker(log_dir=tmp_path / "logs")
        record = tracker.record_call("probe", {"blob": "A" * (2 * 1024 * 1024)}, "ok", 1.0)
        logged = (tmp_path / "logs" / "tool_calls.log").read_text()
        assert len(logged) < MAX_LOGGED_ARGS_CHARS
        assert record.args["blob"] == "A" * (2 * 1024 * 1024)

    def test_small_arguments_are_logged_verbatim(self, tmp_path):
        from shani_chronoa.tool_tracking import ToolTracker
        tracker = ToolTracker(log_dir=tmp_path / "logs")
        tracker.record_call("probe", {"percent": 50, "label": "pasta"}, "ok", 1.0)
        logged = json.loads((tmp_path / "logs" / "tool_calls.log").read_text())
        assert logged["args"] == {"percent": 50, "label": "pasta"}


class TestInlinePathUnchanged:
    """The inline path must be byte-for-byte what it is, for every real skill.

    The argument literal is Python source, not JSON. It used to be written with
    `json.dumps`, which emits `true`/`false`/`null` - the program still
    compiled, because those parse as bare names, and then died at runtime with
    `NameError`. So the `set_mute` expectation below is `True`, not `true`: the
    old expectation was pinning the bug. The quotes are single because that is
    what `repr` produces, and `shlex.quote` then escapes the whole program.
    """

    @pytest.mark.parametrize("name,args,expected", [
        ("get_datetime", {},
         "python3 -c 'from shani_chronoa.skills.clock import _run; import sys; "
         "result = _run({}); sys.stdout.write(str(result))'"),
        ("get_battery_status", {},
         "python3 -c 'from shani_chronoa.skills.battery import _run; import sys; "
         "result = _run({}); sys.stdout.write(str(result))'"),
        ("get_volume", {},
         "python3 -c 'from shani_chronoa.skills.volume import _run_get_volume; import sys; "
         "result = _run_get_volume({}); sys.stdout.write(str(result))'"),
        ("set_volume", {'percent': 42},
         'python3 -c \'from shani_chronoa.skills.volume import _run_set_volume; import sys; result = _run_set_volume({\'"\'"\'percent\'"\'"\': 42}); sys.stdout.write(str(result))\''),
        ("set_mute", {'mute': True},
         'python3 -c \'from shani_chronoa.skills.volume import _run_set_mute; import sys; result = _run_set_mute({\'"\'"\'mute\'"\'"\': True}); sys.stdout.write(str(result))\''),
        ("set_timer", {'seconds': 90, 'label': 'pasta'},
         'python3 -c \'from shani_chronoa.skills.timer import _run; import sys; result = _run({\'"\'"\'seconds\'"\'"\': 90, \'"\'"\'label\'"\'"\': \'"\'"\'pasta\'"\'"\'}); sys.stdout.write(str(result))\''),
    ])
    def test_command_string_is_unchanged(self, monkeypatch, name, args, expected):
        import shani_chronoa.tools as tools_mod
        captured = {}
        monkeypatch.setattr(tools_mod._SANDBOX, "execute", lambda cmd, config, agent_id="default": (
            captured.__setitem__("cmd", cmd), (0, "ok", 1.0))[1])
        monkeypatch.setattr(tools_mod._TRACKER, "record_call", lambda *a, **k: None)
        tools_mod.execute_tool(name, args)
        assert captured["cmd"] == expected

    def test_an_injection_shaped_argument_is_still_inert(self, monkeypatch, tmp_path):
        """The payload must be data, and provably so.

        The old version of this asserted the hostile substring was *absent* from
        the command. That was a proxy for inertness which stopped being true for
        the wrong reason: the argument is now rendered with `repr`, so the text
        legitimately appears inside the program as a quoted string. Substring
        absence measured quoting style, not safety.

        This checks the property itself. The command is a single `python3 -c`
        shell word; the program inside it parses as Python; and the payload
        occurs only inside a string literal, never as executable source. A
        payload that could break out would have to appear as something the AST
        treats as code - an attribute access, a call, an import - and that is
        what is ruled out here.
        """
        import ast
        import shlex

        import shani_chronoa.tools as tools_mod

        captured = {}
        monkeypatch.setattr(tools_mod._SANDBOX, "execute", lambda cmd, config, agent_id="default": (
            captured.__setitem__("cmd", cmd), (0, "ok", 1.0))[1])
        monkeypatch.setattr(tools_mod._TRACKER, "record_call", lambda *a, **k: None)
        marker = tmp_path / "pwned"
        hostile = f'"; import os; os.system("touch {marker}") #'
        tools_mod.execute_tool("set_timer", {"seconds": 5, "label": hostile})

        cmd = captured["cmd"]
        # 1. one shell word: the whole program is a single quoted argument.
        parts = shlex.split(cmd)
        assert parts[0] == "python3" and parts[1] == "-c", parts[:3]
        assert len(parts) == 3, f"the command is not a single python3 -c: {cmd[:120]}"
        program = parts[2]

        # 2. it is valid Python.
        tree = ast.parse(program)

        # 3. the payload lives only inside string literals.
        literal_text = "".join(
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str))
        assert "touch" in literal_text, (
            "the payload should still be present, as data - if it vanished the "
            "argument is being dropped rather than quoted")

        # Nothing executable may reference the payload's identifiers. If the
        # quoting ever fails, `os` and `system` become attribute accesses and a
        # call node rather than part of a string.
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("system", "popen"):
                raise AssertionError(
                    f"the payload escaped into executable code: {ast.dump(node)}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported = getattr(node, "module", None) or ""
                names = [a.name for a in node.names]
                assert "os" not in (imported, *names), (
                    f"the payload became an import: {ast.dump(node)}")
        assert not marker.exists(), "the payload executed"

_EMPTY_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))


def _explode_handler(arguments):
    def _run(args):
        raise ValueError("deliberate child failure")
    return _run
