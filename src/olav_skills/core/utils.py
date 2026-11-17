"""Core Utilities for OLAV.

Provides shared logic for text processing and other common tasks.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware ``datetime``.

    Canonical replacement for ``datetime.utcnow()`` (deprecated in Python 3.12)
    and ``datetime.now()`` (timezone-naive — unsafe across locales). Using this
    helper keeps snapshot_id, audit timestamps, and cross-snapshot comparisons
    consistent across dev/prod, regardless of the host's local timezone.
    """
    return datetime.now(timezone.utc)


_BACKUP_COMMANDS_PATH_ENV = "OLAV_BACKUP_COMMANDS_PATH"
_BACKUP_COMMANDS_FILENAME = "backup_only_commands.yaml"


def find_backup_commands_yaml():
    """Resolve the canonical ``backup_only_commands.yaml`` path.

    Priority (ARCH-22 C — shared by ingest_manager and every domain's
    command_registry). ADR-0002 compliance: no domain name is hardcoded;
    the lookup is generic across any extension that ships a
    ``backup_only_commands.yaml`` in its workspace.

    0. Env override: ``OLAV_BACKUP_COMMANDS_PATH`` — when set to an
       existing file, wins unconditionally. Operators can redirect the
       lookup without editing code for deployments that keep config
       outside the default ``.olav/`` tree.
    1. Per-domain workspace: any ``.olav/workspace/*/*_init/config/backup_only_commands.yaml``
       (matches ``ops/netops_init/`` today, ``k8sops/k8sops_init/`` tomorrow).
    2. Domain config: any ``.olav/config/domains/*/backup_only_commands.yaml``.
    3. Legacy flat path: ``.olav/config/backup_only_commands.yaml``.

    Returns the first existing ``Path`` or ``None`` if no file is found.
    Callers treat ``None`` as "no backup_only_commands configured".
    """
    import os
    raw = os.environ.get(_BACKUP_COMMANDS_PATH_ENV, "").strip()
    if raw:
        candidate = Path(raw)
        if candidate.is_file():
            return candidate

    from olav_skills.core.config import _AGENT_DIR_PATH, _CONFIG_DIR

    # 1. Glob every workspace's *_init/config — covers any domain that
    #    follows the platform init convention.
    workspace_root = _AGENT_DIR_PATH / "workspace"
    if workspace_root.is_dir():
        for candidate in sorted(
            workspace_root.glob(f"*/*_init/config/{_BACKUP_COMMANDS_FILENAME}")
        ):
            if candidate.is_file():
                return candidate

    # 2. Glob every domain under config/domains/.
    domains_root = _CONFIG_DIR / "domains"
    if domains_root.is_dir():
        for candidate in sorted(
            domains_root.glob(f"*/{_BACKUP_COMMANDS_FILENAME}")
        ):
            if candidate.is_file():
                return candidate

    # 3. Legacy flat path.
    legacy = _CONFIG_DIR / _BACKUP_COMMANDS_FILENAME
    if legacy.is_file():
        return legacy
    return None


def create_olav_directories(base_path: Any) -> dict:
    """Create the .olav/ directory structure.

    Args:
        base_path: The base path where .olav/ should be created

    Returns:
        Dict with creation status for each directory
    """
    from pathlib import Path

    base_path = Path(base_path)
    results = {}
    olav_path = base_path / ".olav"

    # Create main .olav directory
    try:
        olav_path.mkdir(parents=True, exist_ok=True)
        results["olav_root"] = {"status": "created", "path": str(olav_path)}
        logger.info(f"Created .olav/ directory at {olav_path}")
    except Exception as e:
        results["olav_root"] = {"status": "error", "error": str(e)}
        logger.error(f"Failed to create .olav/: {e}")
        return results

    subdirs = [
        "config",
        "databases",
        "logs",
        "logs/users",
        "cache",
        "workspace",
        "exports",
        "exports/snapshots",
        "exports/snapshots/latest",
        "exports/snapshots/latest/raw",
        "exports/backup",
        "exports/audit_reports",
        "tmp/snapshots",
        "tmp/staging",
        "knowledge",
    ]

    for subdir in subdirs:
        dir_path = olav_path / subdir
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            results[subdir] = {"status": "created", "path": str(dir_path)}
        except Exception as e:
            results[subdir] = {"status": "error", "error": str(e)}
            logger.error(f"Failed to create {subdir}: {e}")

    return results
