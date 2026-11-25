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
