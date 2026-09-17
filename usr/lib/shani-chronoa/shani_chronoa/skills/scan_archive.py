"""Zip-Slip Protection for shani-chronoa archive scanning."""

import logging
import zipfile
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Raised when a security validation fails."""
    pass


def scan_archive(archive_path: str, extract_to: str) -> List[str]:
    """Safely scan a zip archive for path traversal vulnerabilities.

    Args:
        archive_path: Path to the zip file.
        extract_to: Directory to extract to (used for validation context).

    Returns:
        List of validated entry names.

    Raises:
        SecurityError: If path traversal is detected.
    """
    validated_entries: List[str] = []

    try:
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            for entry in zip_ref.namelist():
                if '..' in entry:
                    raise SecurityError(
                        f"Path traversal detected in archive entry: {entry}"
                    )
                if entry.startswith('/'):
                    raise SecurityError(
                        f"Absolute path detected in archive entry: {entry}"
                    )
                validated_entries.append(entry)
                logger.debug("Validated archive entry: %s", entry)

    except zipfile.BadZipFile:
        raise SecurityError(f"Invalid zip file: {archive_path}")

    logger.info("Scanned archive with %d validated entries", len(validated_entries))
    return validated_entries
