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
    # Second move: deep system, network and forensics diagnostics.
    "routing_table",
    "neighbour_table",
    "interface_counters",
    "port_owner",
    "trace_route",
    "dns_lookup",
    "whois_lookup",
    "tailscale_status",
    "network_watch",
    "scan_network",
    "discover_hosts",
    "network_activity",
    "check_port",
    "ping_host",
    "bandwidth_to_host",
    "tls_certificate",
    "speed_test",
    "open_files",
    "file_locks",
    "system_history",
    "log_files",
    "crash_report",
    "disk_layout",
    "disk_activity",
    "space_by_type",
    "check_units",
    "scheduled_tasks",
    "graphics_stack",
    "permission_audit",
    "process_detail",
    "thread_activity",
    "cpu_frequency",
    "cpu_per_core",
    "oom_score",
    # Third move: remaining deep system and developer skills.
    "content_discovery", "control_service", "list_services", "read_logs",
    "check_updates", "firmware_updates", "vpn_control",
    "machine_capabilities", "autostart_entries", "security_status",
    "snapshot_status", "backup_status", "disk_health", "temperatures",
    "usb_devices", "set_hostname", "vcs_status", "project_outline",
}

#: The plain-answer diagnostics that deliberately stayed in Chronoa.
KEPT_SKILLS = {
    "disk_usage",
    "system_info",
    "list_processes",
    "process_cost",
    "login_history",
    "check_internet",
    "my_ip_address",
    "data_usage",
    "run_command",
}


def test_chronoa_does_not_register_moved_skills():
    _, handlers = skills.discover_skills()
    assert not (set(handlers) & MOVED_SKILLS)
    assert KEPT_SKILLS <= set(handlers)  # the plain answers stay here


def test_the_app_registers_every_moved_skill():
    _, handlers = __import__("shani_sysadmin").discover_skills()
    assert MOVED_SKILLS <= set(handlers)


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
    modules = {
        "capture_packets", "dissect_traffic", "binary_hardening",
        "inspect_binary", "driver_dev", "user_driver", "container", "vm",
        "lab_network", "boot_report", "compression_savings",
        "bridge_topology",
    } | {n for n in MOVED_SKILLS if n not in {
        "lab_network_create", "lab_network_destroy", "lab_network_list",
        "lab_network_status", "device_probe", "device_serial", "device_i2c",
        "driver_status", "driver_scaffold", "driver_build",
        # These two keep a library-only shim: Chronoa's restic adapter and
        # Work chip / Git panel still call the helpers, but no SKILLS are
        # registered from the shim.
        "backup_status", "vcs_status",
    }}
    for module in modules:
        assert not (package / "skills" / f"{module}.py").exists(), module
        assert (APP / "shani_sysadmin" / "skills" / f"{module}.py").is_file(), module
    for module in ("backup_status", "vcs_status"):
        shim = (package / "skills" / f"{module}.py").read_text()
        assert "_CHRONOA_NOT_A_SKILL = True" in shim, module
        assert "shani_sysadmin.skills." in shim, module
        assert (APP / "shani_sysadmin" / "skills" / f"{module}.py").is_file(), module
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
    """Names remain in Chronoa so its two real callers keep working; code does not."""
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
        assert f"shani_sysadmin.ml_harness.{name}" in source, name
        assert (APP / "shani_sysadmin" / "ml_harness" / f"{name}.py").is_file(), name
        assert not (APP / "shani_sysadmin" / f"{name}.py").exists(), name


def test_documented_counts_match_the_registry():
    registry_count = len(skills.discover_skills()[1])
    document = (REPO / "CAPABILITIES.md").read_text()
    readme = (REPO / "README.md").read_text()
    assert f"**{registry_count} skills in" in document
    assert f"{registry_count} skills" in readme
