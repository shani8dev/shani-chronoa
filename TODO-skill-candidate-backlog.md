# Chronoa Comprehensive Skill Candidate & Enhancement Backlog
**Purpose:** A single, agent-ready master backlog of potential new Chronoa skills, enhancements to existing skills, composite workflows, senses, triggers, verifiers, attachments, and supporting ShaniOS integrations.
**Evidence basis**
- `CAPABILITIES.md`: documented Chronoa capability baseline (generated from `capabilities._GROUPS`).
- `matrix-arch.json`: inventory of the ShaniOS machine and candidate skill ideas.
- Candidate proposals in this document: design recommendations inferred from those sources. They are not proof of a missing feature until the coding agent checks implementation code and tests.
**Matrix snapshot:** generated `2026-10-03T10:38:11+00:00`. Recorded totals: **3,990 commands**, **688 packages**, **51 apps**, **695 units**, **129 D-Bus interfaces**, **201 typelibs**, **106 Python import entries**, and **385 matrix idea records**. `CAPABILITIES.md` documents **264 named skills**. The matrix's Chronoa tool registry has **133 entries**; this differs from the documented skill count, so do not treat the matrix registry as the authoritative skill inventory.
## 1. Existing documented capability baseline
### System

`autostart_entries`, `backup_status`, `bandwidth_to_host`, `binary_hardening`, `boot_report`, `bridge_topology`, `capture_packets`, `check_internet`, `check_port`, `check_updates`, `cloud_files`, `compression_savings`, `connect_wifi`, `cpu_frequency`, `cpu_per_core`, `data_usage`, `discover_hosts`, `disk_activity`, `disk_usage`, `dissect_traffic`, `dns_lookup`, `file_locks`, `firmware_updates`, `graphics_stack`, `inspect_binary`, `interface_counters`, `lab_network_create`, `lab_network_destroy`, `lab_network_list`, `lab_network_status`, `list_wifi_networks`, `log_files`, `login_history`, `machine_capabilities`, `my_ip_address`, `neighbour_table`, `network_activity`, `open_files`, `open_settings`, `permission_audit`, `ping_host`, `port_owner`, `print_file`, `process_detail`, `routing_table`, `scan_network`, `scheduled_tasks`, `security_status`, `set_hostname`, `snapshot_status`, `system_history`, `system_info`, `tailscale_status`, `thread_activity`, `tls_certificate`, `trace_route`, `vpn_control`, `whois_lookup`

### Files

`analyze_table`, `apply_patch`, `broken_symlinks`, `cleanup_apply`, `cleanup_report`, `compare_files`, `compute_hash`, `convert_document`, `convert_media`, `create_archive`, `create_directory`, `create_document`, `dashboard`, `delete_file`, `directory_tree`, `disk_layout`, `duplicate_files`, `edit_file`, `empty_directories`, `empty_trash`, `extract_archive`, `files_by_name`, `find_and_replace`, `find_files`, `find_recently_modified`, `get_file_info`, `identify_file`, `json_query`, `list_archive`, `list_directory`, `manage_mount`, `move_or_copy_file`, `notes`, `office_document`, `open_file`, `pdf_pages`, `read_audio`, `read_document`, `read_image`, `read_text_file`, `read_video`, `search_documents`, `search_file_contents`, `search_my_files`, `space_by_type`, `stale_files`, `sync_folder`, `temp_files`, `undo_last_change`, `write_text_file`

### Devices

`airplane_mode`, `android_device`, `bluetooth_call`, `bluetooth_devices`, `bluetooth_gatt`, `device_i2c`, `device_probe`, `disk_health`, `driver_build`, `driver_info`, `driver_scaffold`, `driver_status`, `find_device`, `fm_radio`, `ir_remote`, `lan_phone`, `landline_call`, `nfc`, `phone`, `phone_remote`, `print_queue`, `screen_to_phone`, `temperatures`, `toggle_bluetooth`, `toggle_wifi`, `usb_devices`, `watch`

### Everyday tools

`ask_user`, `calculate`, `convert_color`, `convert_units`, `delegate`, `download_file`, `encode_text`, `explain_command`, `find_emoji`, `generate_password`, `qr_code`, `random_pick`, `routines`, `scan_document`, `solve_math`, `spell_word`

### Time and reminders

`alarm`, `calendar_edit`, `calendar_events`, `calendar_month`, `date_math`, `get_datetime`, `get_world_time`, `notify`, `reminders`, `set_timer`, `set_timezone`, `stopwatch`

### Code and git

`git_branch`, `git_commit`, `git_inspect`, `git_push`, `manage_goals`, `manage_triggers`, `project_outline`, `resolve_conflict`, `run_command`, `todo_list`, `vcs_status`

### Web

`browse`, `convert_currency`, `define_word`, `get_location`, `get_weather`, `lookup_wikipedia`, `maps`, `news`, `speed_test`, `web_search`

### Power and screen

`charger_info`, `do_not_disturb`, `get_battery_status`, `lock_screen`, `power_action`, `set_brightness`, `set_power_profile`, `set_screensaver`, `set_sleep_inhibit`

### Processes and windows

`arrange_window`, `close_window`, `focus_window`, `kill_process`, `list_processes`, `list_windows`, `oom_score`, `press_key`, `process_cost`

### Sound

`audio_output`, `get_volume`, `media_control`, `meeting`, `set_mic_mute`, `set_mute`, `set_volume`, `sing`, `speak`

### Appearance

`accessibility`, `desktop_setting`, `list_fonts`, `set_locale`, `set_scaling`, `set_theme`, `set_wallpaper`, `toggle_night_light`

### Apps

`container`, `default_apps`, `install_app`, `list_apps`, `list_containers`, `list_vms`, `open_application`, `vm`

### Pointer and keyboard

`click_pointer`, `drag`, `list_shortcuts`, `move_pointer`, `scroll`, `set_keyboard_layout`, `type_text`, `ui_elements`

### Pictures

`chart`, `create_video`, `edit_audio`, `edit_image`, `edit_video`, `generate_image`

### Services and logs

`check_units`, `control_service`, `crash_report`, `list_services`, `read_logs`

### What Chronoa knows

`conversations`, `list_capabilities`, `list_percepts`, `review_proposals`

### Photos and video

`photo_metadata`, `photos`, `take_photo`

### Clipboard

`get_clipboard`, `set_clipboard`

### Local models

`install_model`, `recommend_model`

### Privacy controls

`fingerprint_status`, `set_privacy`

### Seeing

`capture_video`, `photo_video`

### Languages

`translate_text`

### Screen

`screenshot`

### Sounds and recordings

`recording`

## 2. Implementation status vocabulary
Use exactly one status per candidate: Already covered | Partial | Missing | Not applicable | Deferred.
#### CORE-01 — Structured tool results and error taxonomy
**Type:** Enhancement · **Priority:** P0 · **Area:** Cross-cutting

**Reuse/check:** All skills / dispatcher / MCP.

**Implementation:** Standardize structured outcomes; include bounded evidence, warnings, changed/verified flags.

**Acceptance:** tests cover success, gate refusal, dependency absence, timeout, partial failure, and postcondition behavior.

#### CORE-02 — Precondition/postcondition verifier
**Type:** Enhancement · **Priority:** P0 · **Area:** Cross-cutting

**Reuse/check:** Action skills.

**Implementation:** Check target/state before/after; do not equate exit 0 with success.

**Acceptance:** tests cover success, gate refusal, dependency absence, timeout, partial failure, and postcondition behavior.

#### CORE-03 — Multi-step task planner and executor
**Type:** Composite workflow · **Priority:** P0 · **Area:** Cross-cutting

**Reuse/check:** Desktop/file/app/document/system skills.

**Implementation:** Bounded DAG/sequence with prerequisites, approval pauses, cancellation, recovery.

**Acceptance:** tests cover success, gate refusal, dependency absence, timeout, partial failure, and postcondition behavior.

#### CORE-04 — Action journal and undo/compensation
**Type:** Enhancement · **Priority:** P0 · **Area:** Cross-cutting

**Reuse/check:** Mutating skills.

**Implementation:** Record action metadata, affected resource, before/after, rollback. No secrets by default.

**Acceptance:** tests cover success, gate refusal, dependency absence, timeout, partial failure, and postcondition behavior.

#### CORE-05 — Skill discovery and capability routing
**Type:** Enhancement · **Priority:** P1 · **Area:** Cross-cutting

**Reuse/check:** `list_capabilities`, dispatcher.

**Implementation:** Route intent by outcome/prerequisites; distinguish unavailable vs disabled.

**Acceptance:** tests cover success, gate refusal, dependency absence, timeout, partial failure, and postcondition behavior.

#### CORE-06 — Dry-run and preview contract
**Type:** Enhancement · **Priority:** P0 · **Area:** Safety

**Reuse/check:** Mutating skills.

**Implementation:** Standardize preview, affected resources, estimated scope, irreversible effects, approval.

**Acceptance:** tests cover success, gate refusal, dependency absence, timeout, partial failure, and postcondition behavior.

#### CORE-07 — Dependency and backend self-test
**Type:** New/composite · **Priority:** P1 · **Area:** Cross-cutting

**Reuse/check:** `machine_capabilities`.

**Implementation:** Check required command/API/version; return actionable missing reports without install attempts.

**Acceptance:** tests cover success, gate refusal, dependency absence, timeout, partial failure, and postcondition behavior.

#### CORE-08 — Timeout, cancellation and resource budgets
**Type:** Enhancement · **Priority:** P0 · **Area:** Reliability

**Reuse/check:** Command-backed skills.

**Implementation:** Per-skill timeout, output/file/item cap, CPU/memory budget, cancellation propagation.

**Acceptance:** tests cover success, gate refusal, dependency absence, timeout, partial failure, and postcondition behavior.

#### CORE-09 — Retry policy with idempotency
**Type:** Enhancement · **Priority:** P1 · **Area:** Reliability

**Reuse/check:** Network/file/automation.

**Implementation:** Retry only transient, safe, idempotent operations; never retry destructive actions blindly.

**Acceptance:** tests cover success, gate refusal, dependency absence, timeout, partial failure, and postcondition behavior.

#### CORE-10 — Evidence/provenance envelope
**Type:** Enhancement · **Priority:** P0 · **Area:** Trust

**Reuse/check:** Reports/data/diagnostics.

**Implementation:** Attach source tool, timestamp, scope, confidence/limitations, source path/page/row.

**Acceptance:** tests cover success, gate refusal, dependency absence, timeout, partial failure, and postcondition behavior.

### B. System health, performance, power and services
- SYS-01 `performance_report`: Read-only diagnostic composite (P1).
- SYS-02 `service_failure_report`: Service state/log evidence; no restart in report path (P1).
- SYS-03 Boot regression and boot-health comparison: Compare boot duration/failure across history (P2).
### C. Network, connectivity, privacy and remote access
- NET-01 `network_diagnosis`: Link → address/route → DNS → endpoint/port/TLS → VPN; bounded probes (P1).
- NET-02 Wi-Fi quality and roaming diagnosis: Signal/link/driver/auth evidence; separate diagnosis from connect actions (P2).
### D. Storage, filesystems, backup, sync and recovery
- STO-01 `btrfs_health_report`: Read-only Btrfs capacity/metadata/device/error; never repair/scrub/destructive auto (P1).
- STO-02 Space growth and reclaim estimate: Compare snapshots/history; estimate reclaimable space with uncertainty (P1).
### E. Desktop, Wayland, accessibility and workspace context
- DESK-01 Wayland/X11 capability probe: Report session backend/protocol/APIs; verify accessibility limitation claim (P0).
- DESK-04 Focus-safe automation: Bind to verified target; re-check focus; stop on ambiguity (P0).
### F. Files, documents, search and knowledge work
- DOC-01 Cross-format document extraction contract: Normalize text/tables/metadata; preserve provenance (P1).
- DOC-02 Multi-document research synthesis: Bounded scope, deduplicate, cite filenames/pages/sections (P1).
### G. Memory, personalization, learning and consent
- MEM-01 Memory provenance and expiry: Source, timestamp, type, confidence, sensitivity, expiry/correction path (P0).
- MEM-02 Inspect/correct/forget memory: List, correct stale claims, remove individual items (P0).
### H. Senses, audio, vision, attachments and multimodal context
- MM-01 Streaming speech session: VAD/streaming partial transcription, barge-in, mic state, cleanup (P1).
- MM-05 Screen/image question answering: OCR/accessibility first; permission-gated; region-grounded answers (P1).
### I. Model management, routing and runtime resource control
- LLM-01 Task-aware local model router: Select installed model by task/modality/context/RAM/offline policy (P1).
- LLM-02 Model runtime health and fallback: Check endpoint/process; fallback only to allowed local model (P1).
### J. Developer, Git, test and project workflows
- DEV-02 Test failure triage: Classify failure, map traceback, propose focused next test (P1).
- DEV-03 Safe code change preparation: Small reviewable diff, check working tree, focused tests (P2).
### K. Security, privacy and identity
- SEC-01 Security posture report: Firewall, Secure Boot/TPM, services, AppArmor, updates where available (P1).
- SEC-05 Privacy status and capture audit: Which senses/triggers/permissions are enabled; quick disable controls (P1).
### L. Triggers, routines, proactive assistance and background work
- AUTO-01 Trigger lifecycle manager: Inspect/pause/resume/disable/expiry; test/simulate mode (P1).
- AUTO-03 Quiet hours and interruption budget: User-defined thresholds, grouping, snooze (P2).
### M. Communication, planning, calendar, tasks and personal workflows
- LIFE-01 Task extraction with confirmation (P2).
### N. Accessibility, language, localization and user experience
- UX-01 Voice interaction state machine (P1); UX-03 Accessible result rendering (P2).
### O. Optional integrations and specialized domains (optional, not core dependencies)
- INT-01 Local backup backend adapters (P3); INT-05 Smartphone/ADB workflow (P3); INT-06 QR/barcode utility (P3).
## 4. Cross-cutting enhancements
- Strict typed schemas; normalize paths; prevent traversal.
- Classify actions by risk; propagate strictest gate through composites.
- Bound stdout/stderr, file/item counts, sample periods; time out and clean up children.
- Report partial success explicitly; never label unavailable data as zero/false.
- Redact tokens, passwords, environment values, private keys and sensitive command lines.
- Test success, invalid input, missing dependency, gate refusal, timeout, non-zero exit, malformed output, partial failure, secret redaction, postcondition verification, regression in registry/help/MCP.
## 5. Matrix command and API leads
Verified on this image; not a mandate to add. Example categories:
- **Accessibility & input:** `lou_allround`, `lou_checkhyphens`, `lou_checktable`, `lou_checkyaml`, `kccachetest`, `kcdirtest`, `kcforesttest`, `kcgrasstest`, `kchashtest`, `kclangctest`, `kcpolytest`, `kcprototest`, `kcstashtest`, `kctreetest`, `kcutiltest`, `enchant-lsmod-2`, `lou_trace`, `mev`, `mouse-test`, `enchant-2`, `espeak-ng`, `espeakup`, `gpm-root`, `speak-ng`, `dconf`, `kcutilmgr`, `trietool`, `trietool-0.2`, `xkbcli`, `lou_tableinfo`, `kcdirmgr`, `kcforestmgr`, `kchashmgr`, `kcpolymgr`, `kctreemgr`, `lou_debug`, `lou_translate`
- **Audio & video:** `pw-dump`, `ffprobe`, `qvidcap`, `cd-paranoia`, `cdparanoia`, `gst-inspect-1.0`, `gst-stats-1.0`, `gst-typefind-1.0`, `plugreport`, `pw-dot`, `pw-mididump`, `sndfile-info`, `soxi`, `soxi_ng`, `spa-inspect`, `zdump`, `plugctl`, `sndfile-metadata-get`, `sndfile-metadata-set`, `metaflac`, `serdi`, `spa-json-dump`, `faad`, `padsp`, `pw-top`, `sndfile-concat`, `y4munsharp`, `yuvmedianfilter`, `yuvycsnoise`, `qv4l2`, `cec-compliance`, `sord_validate`, `v4l2-compliance`, `dvbv5-scan`, `v4l2-tracer`, `transmageddon`, `a52dec`, `edid-decode`, `encodedv`, `jpeg2yuv`, `lav2yuv`, `lavtrans`, `mp2enc`, `mpeg2dec`, `mpeg2enc`
- **Containers & VMs:** `dltest`, `lxc-checkconfig`, `virt-host-validate`, `virt-xml-validate`, `virt-pki-validate`, `virt-qemu-sev-validate`, `logresolve`, `lxc-monitor`, `lxc-top`, `rotatelogs`, `brltty`, `distrobox-list`, `lxc-ls`, `lxc-freeze`, `lxc-unfreeze`, `wirefilter`, `stoken-gui`, `apxs`, `distrobox`, `distrobox-assemble`, `distrobox-enter`, `distrobox-ephemeral`, `distrobox-export`, `distrobox-host-exec`, `distrobox-init`, `distrobox-rm`, `distrobox-upgrade`, `dpipe`, `htcacheclean`, `htdbm`, `lxc-checkpoint`, `lxc-destroy`, `lxc-snapshot`, `lxc-wait`, `qemu-ga`, `qemu-system-i386`, `qemu-system-x86_64`, `snap`, `stoken`, `unixcmd`, `vde_over_ns`, `vde_pcapplug`, `vde_plug2tap`, `vde_router`, `vde_switch`
- **Desktop & apps:** `biosdecode`, `cd-drive`, `cd-info`, `cd-read`, `dbus-monitor`, `freerdp-shadow-cli3`, `getcifsacl`, `gst-discoverer-1.0`, `gtk-query-settings`, `gtk4-query-settings`, `idevice_id`, `idevicecrashreport`, `ideviceinfo`, `iso-info`, `iso-read`, `libwacom-list-devices`, `libwacom-list-local-devices`, `libwacom-show-stylus`, `ownership`, `setcifsacl`, `sxpm`, `xdg-settings`, `xkbcomp`, `xmessage`, `xprop`, `smb2-quota`, `smbinfo`, `json-glib-format`, `json-glib-validate`, `xsltproc`, `xmlcatalog`, `cxpm`, `desktop-file-validate`, `gst-play-1.0`, `idevicediagnostics`, `gst-device-monitor-1.0`, `gnome-disks`, `gnome-firmware`, `gnome-screenshot`, `afcclient`, `cifs.idmap`, `cifs.upcall`, `dbus-cleanup-sockets`, `dbus-update-activation-environment`, `gnome-session-inhibit`
- **Development:** `fy-compose`, `fy-dump`, `fy-filter`, `fy-join`, `fy-testsuite`, `fy-tool`, `fy-ypath`, `as`, `bat`, `c++filt`, `clear`, `elfedit`, `gprofng-collect-app`, `hg-ssh`, `ld`, `libtoolize`, `pccritic`, `pcre2grep`, `pcregrep`, `python3.14`, `reset`, `strip`, `svnlook`, `tset`, `git-shell`, `gitk`, `sqlite3`, `svn`, `svnadmin`, `svnmucc`, `tdbtool`, `eu-elfclassify`, `eu-readelf`, `eu-srcfiles`, `gdbm_dump`, `gprof`, `gprofng-display-src`, `gprofng-display-text`, `infocmp`, `libtool-next-version`, `objdump`, `readelf`, `size`, `strings`, `svnrdump`
- **Hardware & power:** `lstopo`, `dumpiso`, `getkeycodes`, `hwloc-dump-hwdata`, `hwloc-gather-cpuid`, `hwloc-ls`, `hwloc-ps`, `kbdinfo`, `lstopo-no-graphics`, `numastat`, `screendump`, `sensors`, `sensors-detect`, `showconsolefont`, `gpscat`, `gpscsv`, `gpsplot`, `gpsprof`, `gpsrinex`, `gpssubframe`, `i2c-stub-from-dump`, `i2cget`, `lsusb.py`, `usb-devices`, `usbhid-dump`, `dumpkeys`, `fgconsole`, `getunimap`, `hwloc-info`, `kbd_mode`, `memhog`, `migspeed`, `setkeycodes`, `ppscheck`, `pwmconfig`, `gpsfake`, `dvcont`, `setlogcons`, `i2ctransfer`, `gps2udp`, `clrunimap`, `dc1394_reset_bus`, `deallocvt`, `fancontrol`, `hwloc-calc`
- **Images & documents:** `apcaccess`, `composefs-info`, `cups-calibrate`, `escputil`, `foomatic-compiledb`, `giftext`, `grolbp`, `grolj4`, `gsbj`, `gsdj`, `gsdj500`, `gslj`, `gslp`, `gxditview`, `rdjpgcom`, `vipsheader`, `a2ps`, `identify`, `ippeveprinter`, `lp2`, `lpinfo`, `lpq`, `pdfinfo`, `system-config-printer-applet`, `fc-cat`, `fc-list`, `foomatic-addpjloptions`, `foomatic-combo-xml`, `foomatic-getpjloptions`, `foomatic-ppd-options`, `foomatic-preferred-driver`, `foomatic-rip`, `gifbuild`, `info`, `install-info`, `pango-view`, `paper`, `printafm`, `ipptool`, `lpr`, `lprm`, `fc-conflist`, `opj_compress`, `opj_decompress`, `opj_dump`
- **Network:** `iperf3`, `mtr`, `dnet`, `dnssec-verify`, `named-checkzone`, `named-compilezone`, `signver`, `eapol_test`, `isotest`, `rctest`, `testparm`, `ctstat`, `lnstat`, `nstat`, `rtacct`, `rtstat`, `arpsnmp`, `arpwatch`, `btmon`, `eventlogadm`, `nbd-trplay`, `smbstatus`, `ifstat`, `adig`, `ahost`, `genl-ctrl-list`, `iwgetid`, `iwlist`, `iwspy`, `kproplog`, `mii-tool`, `nc`, `netstat`, `nfsstat`, `pluginviewer`, `pppstats`, `rpcctl`, `rpcinfo`, `sasldblistusers2`, `ssltap`, `tcpdump`, `traceroute`, `bridge`, `hostname`, `iw`
- **Other:** `nbdinfo`, `lsinitrd`, `cardos-tool`, `dnie-tool`, `egk-tool`, `gspath`, `iasecc-tool`, `iblinkinfo`, `ibv_asyncwatch`, `ibv_devices`, `npa-tool`, `touchpad-edge-detector`, `defaults`, `dump_fts`, `ibhosts`, `ibnodes`, `ibrouters`, `ibswitches`, `lowdown-diff`, `nfc-barcode`, `nfc-list`, `pldes`, `plutil`, `smpdump`, `cmtime`, `ffcfstress`, `ffmvforce`, `fftest`, `nfc-scan-device`, `rdma_client`, `rdma_server`, `rping`, `ucmatose`, `cracklib-check`, `create-cracklib-dict`, `ibv_rc_pingpong`, `ibv_uc_pingpong`, `ibv_ud_pingpong`, `jstest`, `pn53x-diagnose`, `rstream`, `udaddy`, `udpong`, `xmlparse`, `ib_acme`
- **Security & identity:** `aa-status`, `apparmor_status`, `aa-notify`, `aa-unconfined`, `captree`, `getpcaps`, `getsubids`, `tpm2_eventlog`, `tpm2_getcap`, `tpm2_getpolicydigest`, `tpm2_getsessionauditdigest`, `tpm2_gettime`, `tpm2_nvreadlock`, `tpm2_nvreadpublic`, `tpm2_readclock`, `tpm2_readpublic`, `watchgnupg`, `faillock`, `cvc-print`, `fido2-assert`, `secret-tool`, `tpm2_getcommandauditdigest`, `tpm2_geteccparameters`, `tpm2_getekcertificate`, `tpm2_getrandom`, `tpm2_gettestresult`, `tpm2_nvread`, `tpm2_pcrread`, `tpm2_print`, `xxh128sum`, `xxh32sum`, `xxh3sum`, `xxh64sum`, `xxhsum`, `pkaction`, `grpck`, `ldapvc`, `pam_timestamp_check`, `pwck`, `sbverify`, `slapacl`, `slapauth`, `slapdn`, `slapschema`, `tpm2_checkquote`
- **Storage & filesystems:** `lvdisplay`, `lvs`, `lvscan`, `pvdisplay`, `pvs`, `pvscan`, `vgdisplay`, `vgs`, `cache_dump`, `dmvdostats`, `dump.exfat`, `dump.f2fs`, `dumpe2fs`, `e2freefrag`, `era_dump`, `exfat2img`, `getfattr`, `lvmdiskscan`, `lvmsar`, `mcat`, `mdir`, `mdu`, `minfo`, `mtype`, `ntfscluster`, `ntfsls`, `quota`, `thin_delta`, `thin_dump`, `thin_ls`, `xfs_bmap`, `xfs_info`, `xfs_logprint`, `xfs_spaceman`, `exfatlabel`, `xqmstats`, `filefrag`, `hashstats`, `lsattr`, `lsdosattr`, `mshortname`, `mshowfat`, `ntfscat`, `ntfsinfo`, `lvchange`
- **System & services:** `cifsiostat`, `fastfetch`, `iostat`, `mpstat`, `pidstat`, `tapestat`, `column`, `inxi`, `irqtop`, `lsblk`, `lsclocks`, `lscpu`, `lsfd`, `lsipc`, `lsirq`, `lslocks`, `lslogins`, `lsmem`, `lsns`, `sfdisk`, `bashbug`, `gendict`, `getent`, `ldd`, `locale`, `lsmod`, `lur-command`, `modinfo`, `pldd`, `sadf`, `sar`, `sprof`, `yad`, `yad-tools`, `dmesg`, `resolvectl`, `systemd-creds`, `varlinkctl`, `cal`, `dir`, `echo`, `getino`, `gobject-query`, `groups`, `hexdump`
- **Text & files:** `lsar`, `tree`, `devdump`, `djvudump`, `efibootdump`, `gawkbug`, `grep`, `gssdp-device-sniffer`, `heif-info`, `ircat`, `isodebug`, `isodump`, `isoinfo`, `isovfy`, `lirc-lsplugins`, `lzless`, `lzmainfo`, `lzmore`, `mode2`, `snmp-bridge-mib`, `snmpdf`, `snmpget`, `snmpps`, `snmptable`, `snmptop`, `uchardet`, `unzip`, `which`, `xzless`, `xzmore`, `zipinfo`, `zstdgrep`, `zstdless`, `accessdb`, `efi-readvar`, `irdb-get`, `manpath`, `sign-efi-sig-list`, `snmpbulkwalk`, `snmpstatus`, `snmpwalk`, `whatis`, `osinfo-detect`, `snmpnetstat`, `xmode2`
## 6. Matrix ideas appendix (abbreviated)
Contains the full discovery inventory from the matrix; deduplicate against source before implementation.
## 7. Recommended implementation sequence
Phase 0: coverage ledger, registry reconciliation, regression tests. Phase 1: performance/network/service reports. Phase 2: reliability, backup, memory/provenance. Phase 3: multimodal/speech/workspace/context. Phase 4: optional integrations.
## 8. Definition of done
Implemented in code, registered in authoritative source, schema/gate/dependency/timeout/error modes present, sensitive data handled, tests cover critical cases, `CAPABILITIES.md` regenerated, no duplicate added, exact results reported.
## 9. Final instruction to coding agent
Use this document as backlog; build coverage ledger first; implement missing/demonstrably incomplete items in reviewable batches; preserve consent/sandbox; do not commit/push; finish with tests, regenerated docs, diff, and exact results report.
## 10. Source limitations
Grounded in the supplied `CAPABILITIES.md` and `matrix-arch.json`. No proof of missing feature without source/test inspection. Matrix snapshot reflects the target image; may differ from build.
