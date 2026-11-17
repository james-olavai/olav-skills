#!/usr/bin/env python3
"""Run an offline example with an installed olav-skills, without a model.

Usage: python synthetic_walkthrough.py --home /absolute/path/to/new-example-home
The home must be absent or empty. The adjacent synthetic_2host_bundle is the
only input. Every product call uses the public stdin-JSON CLI with this Python.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    args = parser.parse_args()
    home = args.home.absolute()
    if home.is_symlink() or (home.exists() and (not home.is_dir() or any(home.iterdir()))):
        parser.error("--home must be an absent or empty directory, never existing user data")
    home.mkdir(parents=True, exist_ok=True)
    bundle = Path(__file__).resolve().parent / "synthetic_2host_bundle"
    # A clean child environment prevents inherited provider or data overrides.
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "LANG", "LC_ALL") if key in os.environ}
    env.update(OLAV_HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    results = {}

    def run(label: str, skill: str, script: str, payload: dict) -> dict:
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "olav_skills", "run", skill, script],
            input=json.dumps(payload), capture_output=True, text=True,
            cwd=home, env=env, timeout=120,
        )
        (home / f"{label}.stderr.log").write_text(proc.stderr, encoding="utf-8")
        if proc.returncode:
            raise RuntimeError(f"{label} exited {proc.returncode}: {proc.stderr[-2000:]}")
        result = json.loads(proc.stdout)
        (home / f"{label}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        results[label] = result
        return result

    valid = run("validate", "importer", "validate_bundle", {"path": str(bundle)})
    assert valid["ok"] is True, valid
    landed = run("import", "importer", "ingest_snapshot", {"path": str(bundle)})
    assert landed["hosts"] == 2 and landed["commands"] == 6, landed
    snapshot = landed["snapshot_id"]
    devices = run("devices", "analyzer", "execute_sql", {
        "sql": "SELECT hostname FROM netops.devices ORDER BY hostname", "max_rows": 10})
    assert devices["data"] == [{"hostname": "R1"}, {"hostname": "R2"}], devices
    assert devices["has_more"] is False, devices
    topology = run("topology", "writer", "draw_topology", {
        "name_like": "R%", "snapshot_id": snapshot,
        "diagram_format": "mermaid", "filename": "synthetic_topology", "max_hosts": 2})
    assert topology["status"] == "ok" and topology["hosts"] == 2 and topology["edges"] == 1, topology
    assert Path(topology["path"]).is_file(), topology
    evidence = run("evidence", "reporter", "query_evidence", {
        "source": "command_output", "pattern": "Hostname", "device": "R1",
        "commands": ["show version"], "snapshot": snapshot, "limit": 5})
    assert evidence["total"] == 1 and len(evidence["matches"]) == 1, evidence
    reports = home / "exports/reports"
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / "synthetic_inventory.md"
    report.write_text(
        "# Synthetic recorded inventory\n\n"
        f"Snapshot: `{snapshot}`. This is fixture data, not live state.\n\n"
        "Exact inventory set from bounded read-only SQL: `R1`, `R2` (2 devices).\n"
        "The synthetic CDP capture produces one recorded topology link.\n\n"
        f"Topology: `{topology['path']}`.\n\n"
        "Evidence: `evidence.json`, scoped to R1 / show version / the imported snapshot.\n"
        "This Markdown was composed by the example from verified tool results; no report-writing tool or model was invoked.\n",
        encoding="utf-8")
    print(json.dumps({"home": str(home), "snapshot_id": snapshot, "devices": ["R1", "R2"],
                      "topology": topology["path"], "report": str(report),
                      "import_report": landed.get("report_path"), "model_used": False}, indent=2))


if __name__ == "__main__":
    main()
