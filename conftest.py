"""Repo-root pytest configuration.

Disables bytecode generation BEFORE pytest collects any test module.
Without this, importing shani_chronoa during collection writes .pyc
files into usr/lib/shani-chronoa/**/__pycache__/, which makes the
packaging tests (test_no_pycache_in_packaged_payload,
test_no_bytecode_files_in_packaged_payload) fail spuriously on every run.

pytest loads root conftest.py before collecting test modules, so this
is the earliest hook available for setting sys.dont_write_bytecode.
"""

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
# **No test reaches the machine's real Bluetooth (or anything else on the
# system bus) unless it asks to.** Building the app starts its device services
# - BLE advertising, AVRCP, MAP, OBEX, incoming calls, and the watch companion,
# which connects to the person's watch - all over the *system* bus. Measured
# 2026-10-15: a window test hung past its cap inside `gatttool` against the
# real watch (Wearables read it on the main loop), with every one of those
# services running against the real adapter. A system-bus address that nothing
# listens on makes each of them fail fast instead: bluetoothctl lists nothing,
# so nothing is held or read. Tests that need a bus bring their own (the fake
# BlueZ in tools/fake_bluez.py is handed its private address). To run against
# real hardware on purpose: CHRONOA_TESTS_REAL_SYSTEM_BUS=1.
if os.environ.get("CHRONOA_TESTS_REAL_SYSTEM_BUS") != "1":
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = "unix:path=/nonexistent/chronoa-tests-no-system-bus"
    # bluetoothctl aborts (0.16s, measured) on the unreachable bus rather than
    # waiting - which is the point - but each abort would leave a core dump.
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
