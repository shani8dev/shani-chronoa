"""The tool budget follows how fast this machine reads a prompt.

Fixed per-class caps (CPU 4096, GPU 8192) guessed at speed. llama-server
reports `timings.prompt_per_second` with each reply; with that measured, the
cap is speed x TOOL_SECONDS, never above the window's LIVE_SHARE.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

import pytest  # noqa: E402

from shani_chronoa import cloud_llm, tool_select  # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    tool_select._PROMPT_SPEED["tps"] = 0.0
    yield
    tool_select._PROMPT_SPEED["tps"] = 0.0
    tool_select.set_window(0, local=True)


def test_without_a_measurement_the_class_caps_stand():
    tool_select.set_window(32768, local=True, gpu=False)
    assert tool_select.selection_budget() == tool_select.MAX_BUDGET_TOKENS


def test_a_slow_cpu_gets_fewer_tools_and_a_fast_gpu_more():
    tool_select.set_window(32768, local=True, gpu=False)
    tool_select.note_prompt_speed(60.0)
    assert tool_select.selection_budget() == int(60 * tool_select.TOOL_SECONDS)
    tool_select._PROMPT_SPEED["tps"] = 0.0
    tool_select.set_window(131072, local=True, gpu=True)
    tool_select.note_prompt_speed(2500.0)
    assert tool_select.selection_budget() == int(131072 * tool_select.LIVE_SHARE)
    assert tool_select.selection_budget() > tool_select.MAX_GPU_BUDGET_TOKENS


def test_nonsense_speeds_are_ignored():
    tool_select.note_prompt_speed(1000000.0)
    tool_select.note_prompt_speed(0.0)
    assert tool_select._PROMPT_SPEED["tps"] == 0.0


def test_a_local_reply_reports_its_speed_and_a_cloud_one_does_not():
    class Local:
        local = True

    class Cloud:
        local = False
    body = {"timings": {"prompt_n": 1651, "prompt_per_second": 129.8}}
    cloud_llm._note_prompt_speed(Cloud(), body)
    assert tool_select._PROMPT_SPEED["tps"] == 0.0
    cloud_llm._note_prompt_speed(Local(), {"timings": {"prompt_n": 12, "prompt_per_second": 900}})
    assert tool_select._PROMPT_SPEED["tps"] == 0.0, "a 12-token prompt says nothing about speed"
    cloud_llm._note_prompt_speed(Local(), body)
    assert tool_select._PROMPT_SPEED["tps"] == pytest.approx(129.8)
