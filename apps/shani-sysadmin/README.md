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

**ML harnesses with no assistant-facing caller** — `rerank.py`, `distill.py`,
`merge.py`, `adapter.py`, `cultivation.py`, `gguf_write.py`,
`model_registry.py`.

`run_command` deliberately stayed in Chronoa: it is the documented exception
to the fixed-whitelist design and closes the edit-test loop for the assistant
itself.

## Shared dependencies kept in Chronoa

Nothing in Chronoa's *skill registry* moved with these tools, but the code
still imports these Chronoa library modules. They are general-purpose and
forking them would be worse than the dependency:

| Imported from Chronoa | Used by |
|---|---|
| `shani_chronoa.skills.Skill` | every skill here (the plugin contract) |
| `shani_chronoa.files`, `shani_chronoa.config` | path resolution and consent reads |
| `shani_chronoa.sense_reading` | sense-backed reports (`boot_report`, `compression_savings`, `list_containers`, `list_vms`) |
| `shani_chronoa.sysfs` | kernel-interface reads (`bridge_topology`, `driver_*`) |
| `shani_chronoa.egress` | network-facing tools |
| `shani_chronoa.netprovision` | the four `lab_network_*` skills |
| `shani_chronoa.senses.Sense` | the moved `kernellog` sense |
| `shani_chronoa.engines`, `gguf_header`, `ctxfit`, `modelfmt` | the moved ML harnesses |

`shani_sysadmin/bootstrap.py` puts both this app root and
`usr/lib/shani-chronoa` on `sys.path` so the checkout works without an install.

Chronoa keeps import shims for six moved ML modules (`distill`, `merge`,
`adapter`, `cultivation`, `gguf_write`, `model_registry`) because two of them
still have callers inside Chronoa (`app/brain.py`, `tool_select.py`,
`gui/surfaces/models.py`). Those shims alias the app's module object in
`sys.modules`; the implementation is only here.

## Tests

```bash
PYTHONPATH=apps/shani-sysadmin python3 -m pytest apps/shani-sysadmin/tests -q
```
