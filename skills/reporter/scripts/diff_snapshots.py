#!/usr/bin/env python3
"""ARCH-13: aggregate cross-table snapshot diff (demoted from @tool → script, FINDING-19).

Given two ``snapshot_id`` values this script reports which rows were added
and removed per table, so operators can eyeball a "what changed between
these captures" summary without composing SQL by hand.

Scope (ARCH-13 Phase 1):
    * ``netops.parsed_outputs``   — keyed by (device_name, command)
    * ``netops.topology_links``   — keyed by link_id
    * ``netops.raw_output_store`` — keyed by (device_name, command)
    * ``netops.oc_outputs``       — keyed by (device_name, oc_module)

Usage::

    diff_snapshots(snapshot_id_1="snap_20260101_010000_abc",
                   snapshot_id_2="latest",
                   table_name=None,   # None = all four tables
                   device="R1")       # optional per-device filter

Return shape::

    {
      "status": "success",
      "snapshot_id_1": "...resolved...",
      "snapshot_id_2": "...resolved...",
      "tables": {
        "parsed_outputs": {"added": [...], "removed": [...],
                           "added_count": N, "removed_count": M,
                           "added_has_more": bool, ...},
        ...
      },
      "total_added": N, "total_removed": M,
    }

Counting and sampling are performed in DuckDB (see
``olav_skills.netops.core.diff.snapshot_summary``).  This wrapper keeps the legacy
key-projection EXCEPT semantics and only returns a stable, bounded sample.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from olav_skills.netops.core.diff.snapshot_summary import (
    TABLES as _TABLES,
    diff_snapshots_summary,
    serialize_result,
)


# ── Path / DB resolution ─────────────────────────────────────────────────────


def _find_project_root() -> Path:
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path.cwd()


def _main_db_path() -> Path:
    try:
        from olav_skills.core.config import MAIN_DB_PATH
        return Path(MAIN_DB_PATH)
    except Exception:
        return _find_project_root() / ".olav" / "databases" / "main.duckdb"


# ── Public function ──────────────────────────────────────────────────────────


def diff_snapshots(
    snapshot_id_1: str,
    snapshot_id_2: str,
    table_name: str | None = None,
    device: str | None = None,
) -> dict[str, Any]:
    """Compare two snapshots across the netops tables.

    Args:
        snapshot_id_1: Source snapshot (``"latest"`` resolves via MAX()).
        snapshot_id_2: Target snapshot (``"latest"`` resolves via MAX()).
        table_name: One of ``parsed_outputs`` / ``topology_links`` /
            ``raw_output_store`` / ``oc_outputs``. ``None`` iterates all four.
        device: Optional hostname filter applied per table.

    Returns:
        Summary dict — see module docstring for shape.
    """
    from olav_skills.netops.core.query import warehouse as _warehouse
    if _warehouse.configured():
        return _warehouse.diff_snapshots(snapshot_id_1, snapshot_id_2, table_name, device)
    return diff_snapshots_summary(
        _main_db_path(),
        snapshot_id_1,
        snapshot_id_2,
        table_name,
        device,
    )


from olav_skills.netops.core.query import warehouse as _candidate_warehouse

if __name__ == "__main__" and _candidate_warehouse.configured():
    _candidate_warehouse.candidate_main(diff_snapshots)


if __name__ == "__main__":
    try:
        _raw = sys.stdin.read()
        _args = json.loads(_raw or "{}")
        if not isinstance(_args, dict):
            raise ValueError("stdin JSON must be an object")
        _result = diff_snapshots(**_args)
    except (json.JSONDecodeError, TypeError, ValueError) as _exc:
        _result = {
            "status": "error",
            "error_type": "validation_error",
            "error": f"invalid stdin arguments: {_exc}",
        }
        print(serialize_result(_result))
        sys.exit(2)
    except Exception as _exc:  # noqa: BLE001 - explicit non-zero exit
        _result = {
            "status": "error",
            "error_type": "script_error",
            "error": f"diff_snapshots failed: {_exc}"[:4_000],
        }
        print(serialize_result(_result))
        sys.exit(1)

    _wire = serialize_result(_result)
    print(_wire)
    # Decide the exit code from the actual serialized response.  Serialization
    # may downgrade an over-budget success to a typed budget_exceeded error;
    # the pre-serialization dict would then still say success and wrongly
    # exit 0.
    if json.loads(_wire).get("status") == "error":
        sys.exit(1)
