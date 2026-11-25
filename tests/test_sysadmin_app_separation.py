"""Chronoa no longer exposes the tooling that moved to shani-sysadmin.

Companion to ``apps/shani-sysadmin/tests/test_sysadmin_app.py``: that file
proves the app loads; this one proves the assistant's whitelist is smaller.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "apps" / "shani-sysadmin"
sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))

from shani_chronoa import capabilities, senses, skills  # noqa: E402

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


def test_chronoa_does_not_register_moved_skills():
    _, handlers = skills.discover_skills()
    assert not (set(handlers) & MOVED_SKILLS)
    assert "run_command" in handlers  # the documented exception stays here


def test_moved_names_are_absent_from_every_registration_table():
    tables = (
        capabilities.GATED,
        capabilities._GROUPS,
        capabilities.GATE_NAMES,
        capabilities.READ_ONLY_TOOLS,
        capabilities.MUTATING_TOOLS,
        capabilities.DESTRUCTIVE_CONSENT_KEYS,
    )
    for table in tables:
        assert not (set(table) & MOVED_SKILLS), table


def test_kernellog_sense_is_no_longer_polled_by_chronoa():
    assert "kernellog" not in senses.discover_senses()
    assert "kernellog" not in capabilities.SENSE_TITLES


def test_moved_skill_and_sense_files_left_the_canonical_package():
    package = REPO / "usr/lib/shani-chronoa/shani_chronoa"
    for module in (
        "capture_packets",
        "dissect_traffic",
        "binary_hardening",
        "inspect_binary",
        "driver_dev",
        "user_driver",
        "container",
        "vm",
        "lab_network",
        "boot_report",
        "compression_savings",
        "bridge_topology",
    ):
        assert not (package / "skills" / f"{module}.py").exists(), module
    assert not (package / "senses" / "kernel_log.py").exists()


def test_app_skeleton_exists():
    for relative in (
        "README.md",
        "pyproject.toml",
        "shani_sysadmin/__main__.py",
        "shani_sysadmin/skills/__init__.py",
    ):
        assert (APP / relative).is_file(), relative


def test_ml_modules_are_shims_to_the_app():
    """Chronoa still has two callers, so import names remain; code does not."""
    for name in (
        "distill",
        "merge",
        "adapter",
        "cultivation",
        "gguf_write",
        "model_registry",
    ):
        source = (
            REPO / "usr/lib/shani-chronoa/shani_chronoa" / f"{name}.py"
        ).read_text()
        assert "sys.modules[__name__] = _impl" in source, name
        assert (APP / "shani_sysadmin" / f"{name}.py").is_file(), name


def test_documented_counts_match_the_registry():
    registry_count = len(skills.discover_skills()[1])
    document = (REPO / "CAPABILITIES.md").read_text()
    readme = (REPO / "README.md").read_text()
    assert f"**{registry_count} skills in" in document
    assert f"{registry_count} skills" in readme
