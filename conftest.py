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