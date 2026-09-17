"""Tests for scan_archive (zip-slip protection)."""

import tempfile
import zipfile
from pathlib import Path

import pytest

from shani_chronoa.skills.scan_archive import scan_archive, SecurityError


def test_safe_archive(tmp_path: Path) -> None:
    """Test that safe archives are processed correctly."""
    zip_path = tmp_path / "safe.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("safe_file.txt", "content")
        zf.writestr("subdir/safe_file.txt", "content")

    entries = scan_archive(str(zip_path), str(tmp_path))
    assert "safe_file.txt" in entries
    assert "subdir/safe_file.txt" in entries


def test_path_traversal_detected(tmp_path: Path) -> None:
    """Test that path traversal attempts are caught."""
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("../evil.txt", "content")

    with pytest.raises(SecurityError):
        scan_archive(str(zip_path), str(tmp_path))


def test_absolute_path_detected(tmp_path: Path) -> None:
    """Test that absolute paths are caught."""
    zip_path = tmp_path / "abs.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("/etc/passwd", "content")

    with pytest.raises(SecurityError):
        scan_archive(str(zip_path), str(tmp_path))


def test_invalid_zip(tmp_path: Path) -> None:
    """Test that invalid zip files raise SecurityError."""
    zip_path = tmp_path / "bad.zip"
    zip_path.write_text("not a zip file")
    with pytest.raises(SecurityError):
        scan_archive(str(zip_path), str(tmp_path))
