"""The moved tooling is a loadable, standalone app, not a Chronoa skill set."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

import shani_sysadmin  # noqa: E402

MOVED_SKILLS = {
    "binary_hardening",
    "boot_report",
    "bridge_topology",
    "capture_packets",
    "compression_savings",
    "container",
    "device_i2c",
    "device_probe",
    "device_serial",
    "dissect_traffic",
    "driver_build",
    "driver_info",
    "driver_scaffold",
    "driver_status",
    "inspect_binary",
    "lab_network_create",
    "lab_network_destroy",
    "lab_network_list",
    "lab_network_status",
    "list_containers",
    "list_vms",
    "vm",
}


def test_every_moved_skill_loads_with_its_schema():
    schemas, handlers = shani_sysadmin.discover_skills()
    assert set(handlers) == MOVED_SKILLS
    assert len(schemas) == len(handlers)
    for schema in schemas:
        function = schema["function"]
        assert function["name"] in handlers
        assert function["name"] in MOVED_SKILLS
        assert function["description"]


def test_moved_sense_loads():
    senses = shani_sysadmin.discover_senses()
    assert set(senses) == {"kernellog"}
    assert senses["kernellog"].name == "kernellog"
    assert senses["kernellog"].schema["function"]["name"] == "kernellog"


def test_cli_list_and_check_agree_with_the_loader():
    env = {"PYTHONPATH": str(APP_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    listed = subprocess.run(
        [sys.executable, "-m", "shani_sysadmin", "list"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=APP_ROOT,
    ).stdout.split()
    assert set(listed) == MOVED_SKILLS

    check = subprocess.run(
        [sys.executable, "-m", "shani_sysadmin", "check", "--json"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=APP_ROOT,
    )
    report = json.loads(check.stdout)
    assert report["skill_count"] == 22
    assert report["senses"] == ["kernellog"]


def test_cli_show_prints_one_real_schema():
    env = {"PYTHONPATH": str(APP_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run(
        [sys.executable, "-m", "shani_sysadmin", "show", "capture_packets"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=APP_ROOT,
    ).stdout
    assert json.loads(out)["function"]["name"] == "capture_packets"


def test_cli_refuses_an_unknown_skill_without_running_one():
    env = {"PYTHONPATH": str(APP_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "shani_sysadmin", "run", "not_a_skill", "{}"],
        capture_output=True,
        text=True,
        env=env,
        cwd=APP_ROOT,
    )
    assert result.returncode == 2
    assert "unknown skill" in result.stderr


def test_app_has_its_own_packaging_metadata():
    assert (APP_ROOT / "pyproject.toml").is_file()
    assert (APP_ROOT / "README.md").is_file()
    assert (APP_ROOT / "shani_sysadmin" / "__main__.py").is_file()


@pytest.mark.parametrize("name", sorted(MOVED_SKILLS))
def test_handler_is_defined_inside_the_app(name):
    """A shim in Chronoa would pass the loader test but fail this one."""
    handler = shani_sysadmin.discover_skills()[1][name]
    source = Path(inspect.getfile(inspect.getmodule(handler))).resolve()
    assert source.is_relative_to(APP_ROOT), (name, source)
