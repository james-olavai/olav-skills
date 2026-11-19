"""Synthetic functionality and bounded offline behavior for parser versions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest

from olav_skills.parser_lifecycle import LifecycleError, manage_parser, reparse_snapshot

COMMAND = "show widget state"
SCOPE = {"platform": "cisco_ios", "command": COMMAND}
V1 = r"""Value NAME (\S+)

Start
  ^widget ${NAME}$$ -> Record
"""
V2 = """def parse(raw):
    return [{"name": line.split()[1].upper()} for line in raw.splitlines()
            if line.startswith("widget ")]
"""
HOLDOUT = [
    {"raw_output": "widget alpha", "expected_rows": [{"name": "alpha"}]},
    {"raw_output": "no widgets", "expected_rows": []},
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("OLAV_HOME", str(tmp_path))
    return tmp_path


def manage(project, operation, version=None, **fields):
    args = {"project": str(project), "operation": operation, **SCOPE, **fields}
    if version is not None:
        args["version"] = version
    return manage_parser(**args)


def stage(project, version="1", source=V1, dsl="textfsm"):
    return manage(project, "create", version, source=source, dsl=dsl,
                  training_samples=["widget draft"])


def validate(project, version="1", holdout=HOLDOUT):
    return manage(project, "validate", version, holdout=holdout)


def parse_at(project: Path, raw="widget actual"):
    code = """import json, sys
from olav_skills.netops.tools.textfsm_parse import parse_output
trace = []
rows = parse_output("ios", "show widget state", sys.argv[1], trace=trace.append)
print(json.dumps([rows, trace]))
"""
    return json.loads(subprocess.check_output(
        [sys.executable, "-B", "-c", code, raw], cwd=project, text=True, timeout=15,
    ))


def test_versions_affect_normal_lookup_only_after_activation(project):
    legacy = project / ".olav/templates/cisco_ios/show_widget_state.textfsm"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(V1.replace("NAME", "LEGACY"))
    before = legacy.read_bytes()
    assert parse_at(project)[0] == [{"legacy": "actual"}]

    draft = stage(project)
    assert draft["candidate"]["status"] == "draft"
    assert parse_at(project)[0] == [{"legacy": "actual"}]
    assert validate(project)["candidate"]["status"] == "validated"
    assert parse_at(project)[0] == [{"legacy": "actual"}]
    manage(project, "activate", "1")
    first, trace = parse_at(project)
    assert first == [{"name": "actual"}]
    assert trace[0]["template_sha256"] == hashlib.sha256(V1.encode()).hexdigest()

    stage(project, "v2", V2, "python")
    heldout = [HOLDOUT[0] | {"expected_rows": [{"name": "ALPHA"}]}, HOLDOUT[1]]
    assert validate(project, "v2", heldout)["candidate"]["status"] == "validated"
    manage(project, "activate", "v2")
    assert parse_at(project)[0] == [{"name": "ACTUAL"}]
    assert manage(project, "rollback")["active_version"] == "1"
    assert parse_at(project)[0] == first
    assert manage(project, "rollback", target="legacy")["active_version"] is None
    assert parse_at(project)[0] == [{"legacy": "actual"}]
    assert legacy.read_bytes() == before


def test_independence_mismatch_and_immutability(project):
    assert stage(project)["candidate"] == stage(project)["candidate"]
    with pytest.raises(LifecycleError, match="immutable"):
        stage(project, source=V1 + "\n")
    with pytest.raises(LifecycleError, match="independent"):
        validate(project, holdout=[{"raw_output": " widget draft\r\n",
                                    "expected_rows": [{"name": "draft"}]}, HOLDOUT[1]])
    wrong = [HOLDOUT[0] | {"expected_rows": [{"name": "wrong"}]}, HOLDOUT[1]]
    assert validate(project, holdout=wrong)["candidate"]["status"] == "failed"
    with pytest.raises(LifecycleError, match="validated"):
        manage(project, "activate", "1")
    assert validate(project)["candidate"]["status"] == "validated"
    with pytest.raises(LifecycleError, match="immutable"):
        validate(project, holdout=wrong)


def _database(project):
    db = project / ".olav/databases/main.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def test_exact_historical_reparse_and_source_unchanged(project):
    stage(project)
    validate(project)
    db = _database(project)
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE SCHEMA netops")
        con.execute("""CREATE TABLE netops.ingest_sources (
            snapshot_id VARCHAR, device_name VARCHAR, command VARCHAR,
            source_json VARCHAR, raw_output VARCHAR)""")
        for sid, name, time in [
            ("2026-11-09", "old", None),
            ("newer", "new", {"status": "known", "value": "2026-11-11T00:00:00Z",
                              "precision": "timestamp", "source": "command_header"}),
        ]:
            raw = "widget " + name
            artifact = {"table": "netops.ingest_sources", "snapshot_id": sid,
                        "device_name": "switch-a", "command": COMMAND}
            source = dict(platform="ios", raw_artifact=artifact,
                          actual_raw_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                          collected_at=time)
            con.execute("INSERT INTO netops.ingest_sources VALUES (?,?,?,?,?)",
                        [sid, "switch-a", COMMAND, json.dumps(source), raw])
    before = db.read_bytes()
    for sid, name, time_status in [("2026-11-09", "old", "unknown"), ("newer", "new", "known")]:
        out = reparse_snapshot(project=str(project), version="1", snapshot_id=sid,
                               output=name + ".json", **SCOPE)
        artifact = json.loads(Path(out["artifact"]).read_bytes())
        record, = artifact["records"]
        assert record["snapshot_id"] == sid
        assert record["rows"] == [{"name": name}]
        assert record["collected_at"]["status"] == time_status
        assert record["raw_sha256"] == hashlib.sha256(("widget "+name).encode()).hexdigest()
    with pytest.raises(LifecycleError, match="exists"):
        reparse_snapshot(project=str(project), version="1", snapshot_id="newer",
                         output="new.json", **SCOPE)
    assert db.read_bytes() == before


def run_script(project, name, request):
    return subprocess.run(
        [sys.executable, "-B", "-m", "olav_skills", "run", "learner", name],
        input=json.dumps(request), capture_output=True, text=True, cwd=project, timeout=30,
    )


def identity(project):
    result = subprocess.run([sys.executable, "-B", "-c",
                             "import json; from olav_skills.netops.core.ingest.closure import "
                             "parser_identity; print(json.dumps(parser_identity()))"],
                            cwd=project, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_legacy_learning_still_works_and_managed_empty_does_not_fall_back(project):
    learned = run_script(project, "finish_learn", SCOPE | {
        "parser_response": "# OLAV_DSL: textfsm\n" + V1.replace("NAME", "LEGACY").replace(
            "  ^widget", "  ^other"),
        "samples": [{"device": "fixture-a", "raw_output": "other learned"}],
    })
    assert learned.returncode == 0, learned.stderr
    result = json.loads(learned.stdout)
    assert result["status"] == "ok"
    assert parse_at(project, "other test")[0] == [{"legacy": "test"}]
    before = identity(project)
    stage(project)
    validate(project)
    assert identity(project)["fingerprint"] == before["fingerprint"]
    manage(project, "activate", "1")
    rows, events = parse_at(project, "other test")
    assert rows is None and events[0]["tier"] == "managed"
    assert identity(project)["managed"]["selections"]
    assert identity(project)["fingerprint"] != before["fingerprint"]
    manage(project, "rollback", target="legacy")
    rolled_back = identity(project)
    assert rolled_back["fingerprint"] == before["fingerprint"]
    assert rolled_back["managed_selection_revisions"] != before["managed_selection_revisions"]
    assert parse_at(project, "other test")[0] == [{"legacy": "test"}]


def test_changed_active_bytes_fail_closed_in_parse_and_fingerprint(project):
    stage(project)
    validate(project)
    manage(project, "activate", "1")
    slot, = (project / ".olav/parser-lifecycle").glob("*.json")
    old = json.loads(slot.read_bytes())
    for field in ("source", "validation"):
        changed = json.loads(json.dumps(old))
        if field == "source":
            changed["versions"]["1"]["source"] += "\n# changed"
        else:
            changed["versions"]["1"]["validation"]["sha256"] = "0" * 64
        slot.write_text(json.dumps(changed))
        with pytest.raises(subprocess.CalledProcessError):
            parse_at(project)
        with pytest.raises(AssertionError, match="mismatch"):
            identity(project)


def test_python_safety_and_bounded_result(project):
    for version, script in [
        ("blocked-import", "import os\ndef parse(raw):\n    return []\n"),
        ("blocked-reflection", "def parse(raw):\n    return ().__class__.__mro__\n"),
        ("too-many-rows", "def parse(raw):\n    return [{'name': 'alpha'}] * 2001\n"),
        ("large-allocation", "def parse(raw):\n    return [{'name': 'a' * 2**31}]\n"),
    ]:
        stage(project, version, script, "python")
        outcome = validate(project, version)["candidate"]
        assert outcome["status"] == "failed"
        assert outcome["validation"]["report"]["samples"][0].get("diagnostic")
        with pytest.raises(LifecycleError, match="validated"):
            manage(project, "activate", version)


def test_last_writer_partial_actual_script_and_bundle_time(project):
    request = {"project": str(project), **SCOPE, "version": "1"}
    for operation, fields in [
        ("create", {"source": V1, "dsl": "textfsm", "training_samples": ["widget draft"]}),
        ("validate", {"holdout": HOLDOUT}),
    ]:
        result = run_script(project, "manage_parser", request | fields | {"operation": operation})
        assert result.returncode == 0, (result.stdout, result.stderr)
    db = _database(project)
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE SCHEMA netops")
        con.execute("""CREATE TABLE netops.raw_output_store(snapshot_id VARCHAR, device_name VARCHAR,
            command VARCHAR, raw_output VARCHAR, platform VARCHAR, bundle_id VARCHAR,
            bundle_sha256 VARCHAR, updated_at TIMESTAMP)""")
        con.execute("""CREATE TABLE netops.bundle_ingests(snapshot_id VARCHAR, bundle_id VARCHAR,
            bundle_sha256 VARCHAR, collected_at TIMESTAMP)""")
        con.execute("INSERT INTO netops.bundle_ingests VALUES ('2026-11-09', 'run', 'abc', '2026-11-08 08:00:00')")
        con.execute("INSERT INTO netops.raw_output_store VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            "2026-11-09", "edge-a", COMMAND, "widget alpha", "ios", "run", "abc", "2026-11-18 11:00:00",
        ])
        con.execute("INSERT INTO netops.raw_output_store VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            "later", "edge-b", COMMAND, "widget never-export", "ios", None, None, None,
        ])
    before = db.read_bytes()
    req = request | {"snapshot_id": "2026-11-09", "output": "derived.json"}
    result = run_script(project, "reparse_snapshot", req)
    assert result.returncode == 0, (result.stdout, result.stderr)
    value = json.loads(result.stdout)
    assert value["coverage"] == {
        "source_scope": "last_writer_exact_label_partial", "matched_raw_records": 1,
        "unknown_platform_count": 0, "snapshot_collection_coverage": "unknown",
    }
    file_bytes = Path(value["artifact"]).read_bytes()
    assert hashlib.sha256(file_bytes).hexdigest() == value["sha256"]
    record, = json.loads(file_bytes)["records"]
    assert record["rows"] == [{"name": "alpha"}] and record["device_name"] == "edge-a"
    assert record["collected_at"]["status"] == "unknown"
    assert record["source"]["bundle_collected_at"]["scope"] == "bundle_metadata_only"
    assert record["source"]["bundle_collected_at"]["value"] == "2026-11-08T08:00:00"
    assert db.read_bytes() == before
    unavailable = run_script(project, "reparse_snapshot", req | {"snapshot_id": "older"})
    assert unavailable.returncode == 1
    assert json.loads(unavailable.stdout)["status"] == "unavailable"


def test_reparse_rejects_source_hash_views_wal_and_links(project, tmp_path):
    stage(project)
    validate(project)
    db = _database(project)
    args = {"project": str(project), **SCOPE, "snapshot_id": "old",
            "output": "out.json", "version": "1"}
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE SCHEMA netops")
        con.execute("CREATE VIEW netops.ingest_sources AS SELECT 'old' AS snapshot_id")
    before = db.read_bytes()
    with pytest.raises(LifecycleError, match="base table"):
        reparse_snapshot(**args)
    assert db.read_bytes() == before

    with duckdb.connect(str(db)) as con:
        con.execute("DROP VIEW netops.ingest_sources")
        con.execute("""CREATE TABLE netops.ingest_sources(snapshot_id VARCHAR, device_name VARCHAR,
            command VARCHAR, raw_output VARCHAR, source_json VARCHAR)""")
        stored = dict(raw_artifact=dict(table="netops.ingest_sources", snapshot_id="old",
                                       device_name="edge-a", command=COMMAND),
                      actual_raw_sha256="0" * 64, platform="ios")
        con.execute("INSERT INTO netops.ingest_sources VALUES (?, ?, ?, ?, ?)", [
            "old", "edge-a", COMMAND, "widget alpha", json.dumps(stored),
        ])
    before = db.read_bytes()
    with pytest.raises(LifecycleError, match="hash mismatch"):
        reparse_snapshot(**args)
    assert db.read_bytes() == before
    wal = db.with_suffix(".duckdb.wal")
    wal.write_bytes(b"fixture WAL marker")
    with pytest.raises(LifecycleError, match="WAL"):
        reparse_snapshot(**args)
    assert wal.read_bytes() == b"fixture WAL marker"
    wal.unlink()
    original = tmp_path / "protected.duckdb"
    db.rename(original)
    db.symlink_to(original)
    with pytest.raises(LifecycleError, match="rejected"):
        reparse_snapshot(**args)
    assert original.read_bytes() == before
    assert not (project / ".olav/derived-reparses").exists()


def test_mid_ingest_select_and_rollback_is_rejected(project):
    stage(project, "a")
    validate(project, "a")
    stage(project, "b", V1.replace("NAME", "OTHER"))
    validate(project, "b", [
        HOLDOUT[0] | {"expected_rows": [{"other": "alpha"}]}, HOLDOUT[1],
    ])
    manage(project, "activate", "a")
    assert parse_at(project)[0] == [{"name": "actual"}]
    bundle = Path(__file__).resolve().parents[1] / "examples/synthetic_2host_bundle"
    code = """
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys

import duckdb
from olav_skills.netops.core.ingest import closure, landing
from olav_skills.netops.tools.textfsm_parse import parse_output

home = Path(os.environ["OLAV_HOME"])
fixture_db = home / "synthetic-ingest.duckdb"
request = dict(project=str(home), platform="ios", command="show widget state")
closure.writer_guard = nullcontext
closure._assert_domain = lambda: None
closure.prepare_seeds = lambda: None
closure.open_ingest_connection = lambda: duckdb.connect(str(fixture_db))
before = closure.parser_identity()

def change(**data):
    child = subprocess.run([sys.executable, "-B", "-m", "olav_skills", "run",
                            "learner", "manage_parser"],
                           input=json.dumps(request | data), capture_output=True, text=True,
                           timeout=15, check=True)
    return json.loads(child.stdout)

def during_landing(*args, conn, **kwargs):
    conn.execute("CREATE TABLE synthetic_transaction_marker AS SELECT 1 AS fact")
    assert change(operation="activate", version="b")["active_version"] == "b"
    assert parse_output("ios", "show widget state", "widget actual") == [{"other": "actual"}]
    assert change(operation="rollback")["active_version"] == "a"
    after = closure.parser_identity()
    assert after["fingerprint"] == before["fingerprint"]
    assert after["managed_selection_revisions"] != before["managed_selection_revisions"]

landing.ingest_snapshot = during_landing
result = closure.ingest(sys.argv[1], idempotency_key="synthetic-aba")
assert result["status"] == "failed", result
assert result["error"]["code"] == "PARSER_CHANGED", result
with duckdb.connect(str(fixture_db), read_only=True) as conn:
    assert conn.execute("SELECT state, stage, commit_token FROM netops.ingest_operations").fetchall() == [
        ("failed", "rolled_back", None)
    ]
    assert conn.execute("SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name='synthetic_transaction_marker'").fetchone() == (0,)
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, str(bundle)], cwd=project,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)


def replay_entry(project, snapshot_id, output="reparsed.json"):
    return run_script(project, "reparse_snapshot", {
        "project": str(project), **SCOPE, "version": "1", "snapshot_id": snapshot_id, "output": output,
    })


def test_collated_raw_snapshot_cannot_replace_exact_snapshot(project):
    stage(project)
    validate(project)
    db = _database(project)
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE SCHEMA netops")
        con.execute("""CREATE TABLE netops.raw_output_store(snapshot_id VARCHAR COLLATE NOCASE,
            device_name VARCHAR, command VARCHAR, raw_output VARCHAR, platform VARCHAR)""")
        con.execute("INSERT INTO netops.raw_output_store VALUES (?,?,?,?,?)",
                    ["RUN-5", "edge-a", COMMAND, "widget alpha", "ios"])
    before = db.read_bytes()
    result = replay_entry(project, "run-5")
    assert (result.returncode, json.loads(result.stdout)["status"]) == (1, "unavailable")
    assert not (project / ".olav/derived-reparses/reparsed.json").exists()
    result = replay_entry(project, "RUN-5")
    assert result.returncode == 0, (result.stdout, result.stderr)
    value = json.loads(result.stdout)
    row, = json.loads(Path(value["artifact"]).read_bytes())["records"]
    assert row["snapshot_id"] == row["source"]["snapshot_id"] == "RUN-5"
    assert db.read_bytes() == before


def test_collated_ledger_snapshot_cannot_shadow_exact_last_writer(project):
    stage(project)
    validate(project)
    db = _database(project)
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE SCHEMA netops")
        con.execute("""CREATE TABLE netops.ingest_sources(snapshot_id VARCHAR COLLATE NOCASE,
            device_name VARCHAR, command VARCHAR, source_json VARCHAR, raw_output VARCHAR)""")
        con.execute("""CREATE TABLE netops.raw_output_store(snapshot_id VARCHAR COLLATE NOCASE,
            device_name VARCHAR, command VARCHAR, raw_output VARCHAR, platform VARCHAR)""")
        raw = "widget ledger"
        provenance = dict(
            raw_artifact=dict(table="netops.ingest_sources", snapshot_id="ARCHIVE",
                              device_name="edge-a", command=COMMAND),
            actual_raw_sha256=hashlib.sha256(raw.encode()).hexdigest(), platform="ios",
        )
        con.execute("INSERT INTO netops.ingest_sources VALUES (?,?,?,?,?)",
                    ["ARCHIVE", "edge-a", COMMAND, json.dumps(provenance), raw])
        con.execute("INSERT INTO netops.raw_output_store VALUES (?,?,?,?,?)",
                    ["archive", "edge-a", COMMAND, "widget native", "ios"])
    before = db.read_bytes()
    for label, source_scope, widget in [
        ("archive", "last_writer_exact_label_partial", "native"),
        ("ARCHIVE", "historical_ledger_exact_label", "ledger"),
    ]:
        result = replay_entry(project, label, label + ".json")
        assert result.returncode == 0, (result.stdout, result.stderr)
        value = json.loads(result.stdout)
        assert value["coverage"]["source_scope"] == source_scope
        row, = json.loads(Path(value["artifact"]).read_bytes())["records"]
        assert row["snapshot_id"] == row["source"]["snapshot_id"] == label
        assert row["rows"] == [{"name": widget}]
    assert db.read_bytes() == before


def test_collated_bundle_provenance_needs_exact_sid_id_and_hash(project):
    stage(project)
    validate(project)
    db = _database(project)
    expected_sha = "b" * 64
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE SCHEMA netops")
        con.execute("""CREATE TABLE netops.raw_output_store(snapshot_id VARCHAR COLLATE NOCASE,
            device_name VARCHAR COLLATE NOCASE, command VARCHAR COLLATE NOACCENT, raw_output VARCHAR,
            platform VARCHAR COLLATE NOCASE, bundle_id VARCHAR, bundle_sha256 VARCHAR)""")
        con.execute("""CREATE TABLE netops.bundle_ingests(snapshot_id VARCHAR COLLATE NOCASE,
            bundle_id VARCHAR COLLATE NOCASE, bundle_sha256 VARCHAR COLLATE NOCASE, collected_at TIMESTAMP)""")
        for device, command, platform in [
            ("edge-a", "SHOW   WIDGET STATE", "IOS"), ("EDGE-A", COMMAND, "cisco_ios"),
            ("edge-b", "show widget staté", "ios"), ("edge-c", COMMAND, "junos"),
        ]:
            con.execute("INSERT INTO netops.raw_output_store VALUES (?,?,?,?,?,?,?)",
                        ["dec-run", device, command, "widget alpha", platform, "Run", expected_sha])
        for sid, bundle, digest in [
            ("DEC-RUN", "Run", expected_sha), ("dec-run", "run", expected_sha),
            ("dec-run", "Run", expected_sha.upper()),
        ]:
            con.execute("INSERT INTO netops.bundle_ingests VALUES (?,?,?,?)",
                        [sid, bundle, digest, "2026-11-08 08:00:00"])
    result = replay_entry(project, "dec-run", "without-match.json")
    assert result.returncode == 0, (result.stdout, result.stderr)
    records = json.loads(Path(json.loads(result.stdout)["artifact"]).read_bytes())["records"]
    assert [row["device_name"] for row in records] == ["EDGE-A", "edge-a"]
    assert all("bundle_collected_at" not in row["source"] for row in records)
    with duckdb.connect(str(db)) as con:
        con.execute("INSERT INTO netops.bundle_ingests VALUES (?,?,?,?)",
                    ["dec-run", "Run", expected_sha, "2026-11-09 09:00:00"])
    before = db.read_bytes()
    result = replay_entry(project, "dec-run", "with-match.json")
    assert result.returncode == 0, (result.stdout, result.stderr)
    records = json.loads(Path(json.loads(result.stdout)["artifact"]).read_bytes())["records"]
    assert len(records) == 2
    assert all(row["source"]["bundle_collected_at"]["value"] == "2026-11-09T09:00:00"
               for row in records)
    assert db.read_bytes() == before
