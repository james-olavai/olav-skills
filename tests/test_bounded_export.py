"""Synthetic checks of the real analyzer export entry point."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ENTRY = Path(__file__).resolve().parents[1] / "skills/analyzer/scripts/export_query.py"
ROWS = [
    ("b", "show y", "[{\"local\":\"二\"}]", "s1", "ios"),
    ("a", "show x", None, "s1", "ios"),
    ("a", "show x", None, "s1", "ios"),
    ("a", "show x", "[{\"note\":\"=x\"}]", "s1", "ios"),
    ("z", "show x", "[1]", "S1", "ios"),
    ("w", "show z", "[2]", "s2", "ios"),
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    monkeypatch.setenv("OLAV_HOME", str(root))
    import duckdb

    db = root / ".olav/databases/main.duckdb"
    db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE SCHEMA netops")
        conn.execute("CREATE TABLE netops.parsed_outputs(device_name VARCHAR, command VARCHAR, parsed_data JSON,"
                     " snapshot_id VARCHAR COLLATE nocase, platform VARCHAR)")
        conn.executemany("INSERT INTO netops.parsed_outputs VALUES (?,?,?,?,?)", ROWS)
    return root, db


def invoke(root, export_id="case", **options):
    inputs = {"project": str(root), "export_id": export_id, "source": "parsed_outputs",
              "snapshot_id": "s1", **options}
    proc = subprocess.run([sys.executable, "-B", str(ENTRY)],
                          input=json.dumps(inputs), text=True, capture_output=True, timeout=30)
    assert not proc.stderr, proc.stderr
    result = json.loads(proc.stdout)
    assert proc.returncode == (1 if result["status"] == "failed" else 0), result
    return result


def manifest(root, result):
    path = root / result["manifest"]
    payload = path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == result["receipt"]
    return path, json.loads(payload)


def error(result):
    assert result["status"] == "failed", result
    return result.get("error") or result["reason"]


def test_resume_exact_limit_preserves_nulls_and_duplicates_and_source(project):
    root, db = project
    source_bytes = db.read_bytes()
    first = invoke(root, max_rows=2)
    assert first["status"] == "partial" and first["total_rows"] is None
    first_path, first_manifest = manifest(root, first)
    assert first_manifest["source_version"]["sha256"] == hashlib.sha256(source_bytes).hexdigest()

    last = invoke(root, max_rows=2, receipt=first["receipt"])
    assert last["status"] == "complete" and last["total_rows"] == 4
    path, saved = manifest(root, last)
    assert first_path == path
    records = []
    for part in saved["parts"]:
        payload = (path.parent / part["file"]).read_bytes()
        assert len(payload) == part["bytes"] and hashlib.sha256(payload).hexdigest() == part["sha256"]
        records.extend(json.loads(line) for line in payload.splitlines())
    assert records == [
        {"device_name": "a", "command": "show x", "parsed_data": "[{\"note\":\"=x\"}]"},
        {"device_name": "a", "command": "show x", "parsed_data": None},
        {"device_name": "a", "command": "show x", "parsed_data": None},
        {"device_name": "b", "command": "show y", "parsed_data": "[{\"local\":\"二\"}]"},
    ]
    assert saved["bytes_written"] == sum(p["bytes"] for p in saved["parts"])
    assert db.read_bytes() == source_bytes
    assert list(db.parent.iterdir()) == [db]


def test_filtered_csv_formula_and_embedded_multiline_resume(project):
    root, db = project
    import duckdb

    with duckdb.connect(str(db)) as conn:
        conn.executemany("INSERT INTO netops.parsed_outputs VALUES (?,?,?,?,?)", [
            (" =1", "line 1\nline 2", None, "csv", "ios"),
            ("\tz", "@x", None, "csv", "ios"),
            ("b", "＋x", None, "csv", "ios"),
            ("never", "none", None, "csv", "junos"),
        ])
    options = {"snapshot_id": "csv", "format": "csv", "columns": ["device_name", "command"],
               "filters": {"platform": "ios"}}
    result = invoke(root, max_rows=1, **options)
    assert result["status"] == "partial"
    result = invoke(root, receipt=result["receipt"], **options)
    assert result["status"] == "complete"
    path, saved = manifest(root, result)
    records = set()
    for part in saved["parts"]:
        with (path.parent / part["file"]).open(newline="") as stream:
            reader = csv.reader(stream)
            assert next(reader) == ["device_name", "command"]
            records.update(map(tuple, reader))
    assert records == {("' =1", "line 1\nline 2"), ("'\tz", "'@x"), ("b", "'＋x")}


def test_empty_query_allows_only_real_eof(project):
    root, _ = project
    result = invoke(root, snapshot_id="absent", max_bytes=1)
    assert result["status"] == "complete" and result["total_rows"] == 0 and result["bytes_written"] == 0
    path, saved = manifest(root, result)
    assert (path.parent / saved["parts"][0]["file"]).read_bytes() == b""


@pytest.mark.parametrize("options, reason", [
    ({"max_bytes": 1}, "byte_limit"),
    ({"max_rows": True}, "invalid_max_rows"),
    ({"max_rows": 10001}, "invalid_max_rows"),
    ({"format": "csv", "max_bytes": 1}, "header_byte_limit"),
    ({"source": "read_csv('/tmp/outside')"}, "unsupported_source"),
    ({"sql": "SELECT 1"}, "invalid_request"),
    ({"snapshot_id": "latest"}, "invalid_snapshot_id"),
    ({"columns": ["raw_output"]}, "invalid_columns"),
    ({"filters": {"snapshot_id": "s1"}}, "invalid_filters"),
])
def test_budgets_and_input_validation(project, options, reason):
    root, _ = project
    assert error(invoke(root, **options)) == reason


def test_oversize_cell_cannot_create_zero_progress_partial(project):
    root, db = project
    import duckdb

    with duckdb.connect(str(db)) as conn:
        conn.execute("INSERT INTO netops.parsed_outputs VALUES (repeat('x', 525000), 'show x', NULL, 'big', 'ios')")
    result = invoke(root, snapshot_id="big", columns=["device_name"])
    assert error(result) == "row_byte_limit"
    path, saved = manifest(root, result)
    assert saved["parts"] == [] and not list(path.parent.glob("part-*"))


@pytest.mark.parametrize("change", ["manifest", "part", "request", "db"])
def test_resume_rejects_changes_and_preserves_last_checkpoint(project, change):
    root, db = project
    first = invoke(root, max_rows=2)
    path, saved = manifest(root, first)
    args = {"receipt": first["receipt"]}
    if change == "manifest":
        saved["rows_written"] += 1
        path.write_text(json.dumps(saved))
        expected = "checkpoint_receipt_mismatch"
    elif change == "part":
        (path.parent / saved["parts"][0]["file"]).write_bytes(b"'forged'")
        expected = "checkpoint_part_mismatch"
    elif change == "request":
        args["columns"] = ["device_name"]
        expected = "checkpoint_scope_mismatch"
    else:
        import duckdb
        with duckdb.connect(str(db)) as conn:
            conn.execute("DELETE FROM netops.parsed_outputs WHERE device_name = 'b'")
        expected = "source_changed"
    before = path.read_bytes()
    assert error(invoke(root, **args)) == expected
    assert path.read_bytes() == before and not list(path.parent.glob("part-0002.*"))


@pytest.mark.parametrize("source_path", [True, False])
def test_rejects_symlink_source_and_output(project, tmp_path, source_path):
    root, db = project
    outside = tmp_path / "outside"
    if source_path:
        db.rename(outside)
        db.symlink_to(outside)
    else:
        outside.mkdir()
        (root / ".olav/exports").symlink_to(outside, target_is_directory=True)
    assert error(invoke(root)) == "file_unavailable"
    if not source_path:
        assert list(outside.iterdir()) == []


def test_external_backed_view_and_wal_are_refused(project, tmp_path):
    root, db = project
    import duckdb

    outside = tmp_path / "outside.csv"
    outside.write_text("device_name,command,parsed_data,snapshot_id,platform\na,x,[],s1,ios\n")
    with duckdb.connect(str(db)) as conn:
        conn.execute("DROP TABLE netops.parsed_outputs")
        conn.execute(f"CREATE VIEW netops.parsed_outputs AS SELECT * FROM read_csv('{outside}')")
    assert error(invoke(root)) == "source_schema_unavailable"
    db.with_suffix(".duckdb.wal").write_bytes(b"safety")
    assert error(invoke(root, "wal")) == "source_has_wal"


def test_parent_deadline_prevents_success(project):
    root, _ = project
    assert error(invoke(root, timeout_ms=1)) == "deadline_exceeded"


def test_topology_links_filters_and_text_numbers(project):
    root, db = project
    import duckdb

    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE TABLE netops.topology_links(snapshot_id VARCHAR, source_device VARCHAR, status_changes INTEGER)")
        conn.execute("INSERT INTO netops.topology_links VALUES ('s1','a',123), ('s2','a',99), ('s1','b',11)")
    result = invoke(root, source="topology_links", columns=["source_device", "status_changes"],
                    filters={"source_device": "a"})
    assert result["status"] == "complete" and result["total_rows"] == 1
    path, saved = manifest(root, result)
    assert json.loads((path.parent / saved["parts"][0]["file"]).read_bytes()) == {
        "source_device": "a", "status_changes": "123",
    }


def test_recomputed_manifest_count_is_checked_even_with_new_pin(project):
    root, _ = project
    first = invoke(root, max_rows=2)
    path, saved = manifest(root, first)
    saved["rows_written"] = saved["parts"][0]["rows"] = 1
    payload = json.dumps(saved).encode()
    path.write_bytes(payload)
    assert error(invoke(root, receipt=hashlib.sha256(payload).hexdigest())) == "checkpoint_part_mismatch"


def test_wrong_project_and_operator_paths_rejected(project, tmp_path):
    root, _ = project
    assert error(invoke(root, project=str(tmp_path))) == "invalid_project"
    assert error(invoke(root, export_id="../test")) == "invalid_export_id"


def test_byte_cutoff_and_recover_orphan_with_previous_receipt(project):
    root, _ = project
    result = invoke(root, columns=["device_name"], max_bytes=23)
    assert result["status"] == "partial" and result["reason"] == "byte_limit"
    path, saved = manifest(root, result)
    assert saved["bytes_written"] == len(b'{"device_name":"a"}\n')
    assert saved["rows_written"] == 1
    for name in (".page.tmp", ".manifest.tmp", "part-0002.jsonl"):
        orphan = path.parent / name
        orphan.write_bytes(b"incomplete")
        orphan.chmod(0o600)
    result = invoke(root, columns=["device_name"], receipt=result["receipt"])
    assert result["status"] == "complete" and result["total_rows"] == 4, result
    _, saved = manifest(root, result)
    payload = b"".join((path.parent / p["file"]).read_bytes() for p in saved["parts"])
    assert payload == b'{"device_name":"a"}\n' * 3 + b'{"device_name":"b"}\n'


def test_pinned_exporter_identity_and_hardlink_part_rejection(project, tmp_path):
    root, _ = project
    first = invoke(root, max_rows=2)
    path, saved = manifest(root, first)
    assert saved["encoding"]["column_types"] == ["VARCHAR", "VARCHAR", "JSON"]
    assert len(saved["source_version"]["exporter_sha256"]) == 64
    part = path.parent / saved["parts"][0]["file"]
    os.link(part, tmp_path / "outside-hardlink")
    assert error(invoke(root, receipt=first["receipt"])) == "unsafe_file"


def test_bound_filter_is_literal(project):
    root, _ = project
    result = invoke(root, filters={"device_name": "x' OR 1=1 --"})
    assert result["status"] == "complete" and result["total_rows"] == 0
