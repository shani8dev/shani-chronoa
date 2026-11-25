# shani-sysadmin

The sysadmin, driver, container/VM and packet-level tooling that used to be
registered as Chronoa skills. It is now a standalone CLI, so it is no longer
offered to the model on every turn and it is no longer part of Chronoa's
`tools` whitelist or MCP server.

## Run it

From a checkout:

```bash
python3 -m shani_sysadmin list
python3 -m shani_sysadmin show capture_packets
python3 -m shani_sysadmin run capture_packets '{"interface": "eth0", "count": 5}'
python3 -m shani_sysadmin senses
python3 -m shani_sysadmin check
```

Packaged installs get a `shani-sysadmin` console script (see
`pyproject.toml`).

## What moved here

**Packet capture and dissection** — `capture_packets`, `dissect_traffic`
(gated by `packet-capture-enabled`, which still lives in Chronoa's config so
existing installs keep their grant).

**Binary analysis** — `binary_hardening`, `inspect_binary`.

**Kernel and driver development** — `driver_status`, `driver_scaffold`,
`driver_build`, `driver_info`, `device_probe`, `device_i2c`, `device_serial`
(the last three share `shani_chronoa/skills/user_driver.py` in this app).
`driver_build` and `device_i2c` keep their consent keys in Chronoa's schema
so a grant already made still applies.

**Containers and VMs** — `container`, `vm`, `list_containers`, `list_vms`.

**Lab networking** — `lab_network_list`, `lab_network_create`,
`lab_network_destroy`, `lab_network_status` (all four keep
`network-provision-enabled`).

**Boot / filesystem / topology** — `boot_report`, `compression_savings`,
`bridge_topology`, and the `kernellog` sense (moved from
`shani_chronoa/senses/kernel_log.py`; it is no longer polled by Chronoa's
ambient scheduler).

**System, network and forensics diagnostics (second move)** — the deep reads
that answer a specialist's question, not an everyday one:

- Network: `routing_table`, `neighbour_table`, `interface_counters`,
  `port_owner`, `trace_route`, `dns_lookup`, `whois_lookup`,
  `tailscale_status`, `network_watch`, `scan_network`, `discover_hosts`,
  `network_activity`, `check_port`, `ping_host`, `bandwidth_to_host`,
  `tls_certificate`, `speed_test`.
- Files and locks: `open_files`, `file_locks`.
- History and logs: `system_history`, `log_files`, `crash_report`.
- Disk internals: `disk_layout`, `disk_activity`, `space_by_type`.
- Units and posture: `check_units`, `scheduled_tasks`, `graphics_stack`,
  `permission_audit`.
- Process internals: `process_detail`, `thread_activity`, `cpu_frequency`,
  `cpu_per_core`, `oom_score`.

`login_history` stayed in Chronoa on purpose: "who logged in" is a plain
question, while its three forensic siblings above moved. The other plain
answers still offered by the assistant are `disk_usage`, `system_info`,
`list_processes`, `process_cost`, `check_internet`, `my_ip_address` and
`data_usage` — eight in total. `run_command` also stayed: it is the documented
exception to the fixed-whitelist design and closes the edit-test loop for the
assistant itself. Asking Chronoa for any moved tool now gets a clear unknown-tool
refusal; use `python3 -m shani_sysadmin list` to see what the app provides.

**Remaining deep system and developer skills (third move)** — the last of the
specialist reads that do not belong in an everyday whitelist:

- Services and logs: `list_services`, `control_service`, `read_logs`.
- Packages, firmware and updates: `check_updates`, `firmware_updates`.
- Network and web posture: `vpn_control`, `content_discovery` (the bounded
  gobuster path finder, previously gated like the moved `scan_network`).
- Machine reports: `machine_capabilities`, `autostart_entries`,
  `security_status`, `snapshot_status`, `backup_status`, `disk_health`,
  `temperatures`, `usb_devices`, `set_hostname`.
- Developer questions: `vcs_status`, `project_outline`.

`vcs_status` and `backup_status` keep library-only shims in Chronoa: its Work
chip, Git panel and restic adapter still import `git_state()` and `_run()` from
those names, but the shims declare `_CHRONOA_NOT_A_SKILL`, so the assistant no
longer offers either tool. The git write skills (`git_inspect`, `git_commit`,
`git_branch`, `git_push`, `resolve_conflict`), `apply_patch` and `edit_file`
stay in Chronoa with `run_command`: Build mode and the Work chip call them
directly, and they are the assistant's own edit loop rather than questions a
person asks about the machine.

All their consent keys remain declared in Chronoa's GSettings schema and
`capabilities.GATE_NAMES` (`service-control-enabled`, `hostname-control-enabled`,
`wifi-connect-enabled`, `snapshots-sense-enabled`, `security-sense-enabled`,
`storage-sense-enabled`, `hwmon-sense-enabled`, `usb-sense-enabled`, ...), so a
grant already made still applies when the app runs the tool; only the
tool-to-key rows in `capabilities.GATED` were removed.

**File discovery, cleanup and system time (third move)** — the overlapping
and admin-heavy tools from three everyday clusters. Nothing was deleted: each
is the same module at its new path.

- **File discovery** — `files_by_name`, `search_documents`. Their handlers were
  folded into the surviving Chronoa skills `find_files` (name search with
  `details: true` for sizes, dates and the newest copy) and `search_my_files`
  (`action=desktop`, the LocalSearch/Baloo backend). The old modules keep their
  standalone tools here; consent keys are unchanged.
- **Disk cleanup** — `cleanup_report`, `cleanup_apply`, `temp_files`,
  `duplicate_files`, `broken_symlinks`, `stale_files`, `empty_directories`,
  `empty_trash`. These are specialist hygiene scans and mutations. Chronoa
  keeps only the plain `disk_usage` answer.
- **System time and sleep** — `set_timezone`, `set_sleep_inhibit`. The clock
  question itself stays in Chronoa as `get_datetime`, which now also answers
  "what time is it in Tokyo" (the old `world_clock` handler is merged into it).

**ML harnesses with no assistant-facing caller** — `rerank.py`, `distill.py`,
`merge.py`, `adapter.py`, `cultivation.py`, `gguf_write.py`,
`model_registry.py`, now grouped under `shani_sysadmin/ml_harness/`. Nothing was
deleted: each is the same module at its new path, and `ml_harness/__init__.py`
records why the package exists.

## Shared dependencies kept in Chronoa

Nothing in Chronoa's *skill registry* moved with these tools, but the code
still imports these Chronoa library modules. They are general-purpose and
forking them would be worse than the dependency:

| Imported from Chronoa | Used by |
|---|---|
| `shani_chronoa.skills.Skill` | every skill here (the plugin contract) |
| `shani_chronoa.files`, `shani_chronoa.config` | path resolution and consent reads |
| `shani_chronoa.sense_reading` | sense-backed reports (`boot_report`, `compression_savings`, `list_containers`, `list_vms`, `crash_report`, `disk_activity`, `disk_layout`) |
| `shani_chronoa.sysfs` | kernel-interface reads (`bridge_topology`, `driver_*`) |
| `shani_chronoa.egress` | network-facing tools |
| `shani_chronoa.netprovision` | the four `lab_network_*` skills |
| `shani_chronoa.senses.Sense` | the moved `kernellog` sense |
| `shani_chronoa.engines`, `gguf_header`, `ctxfit`, `modelfmt` | the moved ML harnesses |

`shani_sysadmin/bootstrap.py` puts both this app root and
`usr/lib/shani-chronoa` on `sys.path` so the checkout works without an install.

Chronoa keeps import shims for all six moved ML modules (`distill`, `merge`,
`adapter`, `cultivation`, `gguf_write`, `model_registry`) because two of them
still have callers inside Chronoa (`app/brain.py`, `tool_select.py`,
`gui/surfaces/models.py`) and the other four are kept reachable for any
out-of-tree caller. Every shim aliases the app's module object in `sys.modules`;
the implementation is only here, under `shani_sysadmin.ml_harness`.

## Tests

```bash
PYTHONPATH=apps/shani-sysadmin python3 -m pytest apps/shani-sysadmin/tests -q
```
