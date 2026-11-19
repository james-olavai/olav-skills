"""Recorded raw configuration behavior through the original native entry point."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/netops/scripts/diff_configs.py"
RAW = "netops.raw_output_store"
LEDGER = "netops.ingest_sources"
DEVICE = "edge-1"
COMMAND = "show running-config"
BASELINE = "2024-02-01"
TARGET = "2023-12-30"
DEFAULT = dict(device=DEVICE, command=COMMAND, snapshot_id_1=BASELINE, snapshot_id_2=TARGET)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("OLAV_HOME", str(tmp_path))
    path = tmp_path / ".olav/databases/main.duckdb"
    path.parent.mkdir(parents=True)
    with duckdb.connect(str(path)) as conn:
        conn.execute("CREATE SCHEMA netops")
        conn.execute(f"""CREATE TABLE {RAW} (
            snapshot_id VARCHAR COLLATE NOCASE, device_name VARCHAR COLLATE NOACCENT,
            command VARCHAR COLLATE NOCASE, raw_output VARCHAR, collected_at TIMESTAMPTZ,
            ingested_at TIMESTAMP DEFAULT TIMESTAMP '2000-01-01')""")
    return path


def _raw(conn, sid, text, device=DEVICE, command=COMMAND, collected_at=None):
    conn.execute(f"INSERT INTO {RAW} VALUES (?, ?, ?, ?, ?, DEFAULT)",
                 [sid, device, command, text, collected_at])


def _ledger(conn):
    conn.execute(f"""CREATE TABLE {LEDGER} (
        snapshot_id VARCHAR COLLATE NOCASE, device_name VARCHAR COLLATE NOACCENT,
        command VARCHAR COLLATE NOCASE, raw_output VARCHAR, source_json VARCHAR)""")


def _historical(conn, sid, text, *, collection=None, device=DEVICE, command=COMMAND,
                overrides=None):
    raw_hash = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
    provenance = {"raw_artifact": {"table": LEDGER, "snapshot_id": sid,
                                  "device_name": device, "command": command},
                  "actual_raw_sha256": raw_hash, "collected_at": collection}
    provenance.update(overrides or {})
    conn.execute(f"INSERT INTO {LEDGER} VALUES (?, ?, ?, ?, ?)",
                 [sid, device, command, text, json.dumps(provenance)])


def _call(payload=None, **overrides):
    body = json.dumps(DEFAULT | overrides) if payload is None else payload
    completed = subprocess.run([sys.executable, str(SCRIPT)], input=body, text=True,
                               capture_output=True, timeout=20)
    assert not completed.stderr, completed.stderr
    result = json.loads(completed.stdout)
    assert completed.returncode == (0 if result["status"] == "success" else 1)
    return result


def _error(error, **overrides):
    out = _call(**overrides)
    assert out["status"] == "error"
    assert out["error_type"] == error
    assert out["diff_status"] == "unknown"
    assert "diff" not in out
    return out


def test_historical_ledger_and_last_writer_exact_with_provenance(db):
    before = "Building configuration...\nCurrent configuration : 123 bytes\n!\ninterface Ethernet1\n description old bytes\n"
    after = "Building configuration...\nCurrent configuration : 456 bytes\n!\ninterface Ethernet1\n description new bytes\n"
    with duckdb.connect(str(db)) as conn:
        _ledger(conn)
        _historical(conn, BASELINE, before, collection={
            "status": "known", "precision": "timestamp", "source": "command_header",
            "value": "2026-01-03T12:34:00+00:00",
        })
        _raw(conn, TARGET, after)
    original = hashlib.sha256(db.read_bytes()).hexdigest()
    out = _call()
    assert (out["snapshot_id_1"], out["snapshot_id_2"]) == (BASELINE, TARGET)
    assert out["command"] == COMMAND
    assert out["changed_lines"] == 2
    assert "+ description new bytes" in out["diff"]
    assert "- description old bytes" in out["diff"]
    assert "Current configuration" not in out["diff"]
    assert out["changed_blocks"] == [
        {"kind": "interface", "header": "interface Ethernet1", "added_lines": 1, "removed_lines": 1},
    ]
    baseline, target = out["sources"]["baseline"], out["sources"]["target"]
    assert baseline["table"] == LEDGER
    assert baseline["scope"] == "historical_ledger_exact_label"
    assert baseline["raw_sha256"] == hashlib.sha256(before.encode()).hexdigest()
    assert baseline["collected_at"]["value"] == "2026-01-03T12:34:00+00:00"
    assert target["table"] == RAW
    assert target["scope"] == "last_writer_exact_label_partial"
    assert target["raw_sha256"] == hashlib.sha256(after.encode()).hexdigest()
    assert target["collected_at"]["status"] == "unknown"
    assert target["collected_at"]["value"] is None
    assert out["database_version"]["sha256"] == original
    assert hashlib.sha256(db.read_bytes()).hexdigest() == original


@pytest.mark.parametrize("overrides", [
    {"snapshot_id_1": None}, {"snapshot_id_2": "LaTest"}, {"snapshot_id_2": "current"},
    {"snapshot_id_2": BASELINE}, {"snapshot_id_1": " "},
])
def test_requires_distinct_explicit_snapshot_ids(db, overrides):
    _error("invalid_snapshot_id", **overrides)


def test_command_must_resolve_exactly_and_uniquely(db):
    with duckdb.connect(str(db)) as conn:
        _ledger(conn)
        _historical(conn, BASELINE, "hostname old", command=COMMAND + " all")
        _historical(conn, BASELINE, "hostname old", command=COMMAND + " brief")
        _historical(conn, TARGET, "hostname new", command=COMMAND + " all")
        _historical(conn, TARGET, "hostname new", command=COMMAND + " brief")
    _error("raw_not_found")
    assert _call(command=COMMAND + " all")["changed_lines"] == 2
    with duckdb.connect(str(db)) as conn:
        _historical(conn, TARGET, "hostname new", command=COMMAND + " all")
    assert _error("ambiguous_record", command=COMMAND + " all")["side"] == "target"


def test_ledger_claims_exact_sid_even_when_requested_command_missing(db):
    with duckdb.connect(str(db)) as conn:
        _ledger(conn)
        _historical(conn, BASELINE, "hostname stale", command=COMMAND + " all")
        _raw(conn, BASELINE, "hostname false")
        _raw(conn, TARGET, "hostname new")
    result = _error("raw_not_found")
    assert result["side"] == "baseline"
    assert result["source_table"] == LEDGER


def test_missing_historical_last_writer_label_does_not_reuse_new_body(db):
    with duckdb.connect(str(db)) as conn:
        _raw(conn, TARGET, "hostname now")
    assert _error("raw_not_found")["side"] == "baseline"


@pytest.mark.parametrize(("stored", "requested", "field"), [
    ("OLDER", "older", "snapshot_id_1"), ("Édge", "Edge", "device"),
    ("SHOW RUNNING-CONFIG", COMMAND, "command"),
])
def test_collation_cannot_expand_identity(db, stored, requested, field):
    args = DEFAULT | {field: stored}
    with duckdb.connect(str(db)) as conn:
        _ledger(conn)
        _historical(conn, args["snapshot_id_1"], "hostname old",
                    device=args["device"], command=args["command"])
        _raw(conn, TARGET, "hostname new", device=args["device"], command=args["command"])
    assert _call(**args)["changed_lines"] == 2
    assert _error("raw_not_found", **(args | {field: requested}))["side"] == "baseline"


@pytest.mark.parametrize("raw", [None, "", "   \n"])
def test_null_and_blank_body_fail_as_missing(db, raw):
    with duckdb.connect(str(db)) as conn:
        _ledger(conn)
        _historical(conn, BASELINE, raw)
        _raw(conn, BASELINE, "hostname forbidden-fallback")
        _raw(conn, TARGET, "hostname new")
    assert _error("raw_unavailable")["side"] == "baseline"


def test_ledger_raw_hash_required(db):
    with duckdb.connect(str(db)) as conn:
        _ledger(conn)
        _historical(conn, BASELINE, "hostname old", overrides={"actual_raw_sha256": "0" * 64})
        _raw(conn, TARGET, "hostname new")
    assert _error("source_integrity_error")["side"] == "baseline"


def test_ios_order_and_body_text_with_noise_like_words(db):
    before = """Building configuration...
Current configuration : 123 bytes
! Last configuration change at 00:00:10 UTC Tue Nov 10 2026
interface Ethernet1
 description Current configuration bytes old
!
router bgp 64500
 neighbor 192.0.2.1 remote-as 64500
!
ip access-list extended EDGE
 permit ip any host 192.0.2.1
 deny ip any any log
!
route-map ROUTE permit 10
 match ip address EDGE
!
route-map ROUTE deny 20
 match ip address DEFAULT
!
banner motd ^
interface This is banner text with bytes
Current configuration : 77 bytes
^
end
"""
    after = (before.replace("123 bytes", "321 bytes").replace("00:00:10", "00:00:11")
             .replace("bytes old", "bytes new").replace("remote-as 64500", "remote-as 64501")
             .replace(" permit ip any host 192.0.2.1\n deny ip any any log",
                      " deny ip any any log\n permit ip any host 192.0.2.1")
             .replace("route-map ROUTE permit 10\n match ip address EDGE\n!\nroute-map ROUTE deny 20\n match ip address DEFAULT",
                      "route-map ROUTE deny 20\n match ip address DEFAULT\n!\nroute-map ROUTE permit 10\n match ip address EDGE")
             .replace("77 bytes", "88 bytes"))
    with duckdb.connect(str(db)) as conn:
        _raw(conn, BASELINE, before)
        _raw(conn, TARGET, after)
    out = _call(full=True, context_lines=0)
    assert {x["kind"] for x in out["changed_blocks"]} == {"interface", "router", "acl", "route_map", "global"}
    assert "+Current configuration : 88 bytes" in out["diff"]
    assert "+ description Current configuration bytes new" in out["diff"]
    assert "321 bytes" not in out["diff"]
    assert "00:00:11" not in out["diff"]
    assert _call(sections=["acl", "route-map ROUTE"])["changed_lines"] >= 4
    _error("sections_not_found", sections=["interface Eth"])


def test_volatile_heading_alone_is_unchanged(db):
    with duckdb.connect(str(db)) as conn:
        _raw(conn, BASELINE, "Building configuration...\nCurrent configuration : 100 bytes\nhostname same")
        _raw(conn, TARGET, "Building configuration...\nCurrent configuration : 200 bytes\nhostname same")
    out = _call()
    assert out["diff_status"] == "no_observed_changes"
    assert out["changed_lines"] == 0


def test_raw_byte_budget_blocks_full_mode(db):
    with duckdb.connect(str(db)) as conn:
        _raw(conn, BASELINE, "界" * 100_000)
        _raw(conn, TARGET, "hostname new")
    assert _error("raw_budget_exceeded", full=True)["side"] == "baseline"


def test_full_preview_respects_line_and_byte_limits(db):
    before = "\n".join(f"description before-{i:04d}" for i in range(450))
    after = "\n".join(f"description after-{i:04d}" for i in range(450))
    with duckdb.connect(str(db)) as conn:
        _raw(conn, BASELINE, before)
        _raw(conn, TARGET, after)
    compact = _call()
    full = _call(full=True)
    assert compact["changed_lines"] == full["changed_lines"] == 900
    assert compact["preview"]["shown_lines"] == 40
    assert full["preview"]["shown_lines"] == 400
    assert full["preview"]["truncated"] is True
    with duckdb.connect(str(db)) as conn:
        conn.execute(f"UPDATE {RAW} SET raw_output=? WHERE snapshot_id=?", ["description " + "界" * 10_000, TARGET])
    byte_truncated = _call(full=True)
    assert byte_truncated["changed_lines"] == 451
    assert byte_truncated["preview"]["truncated"] is True
    assert len(json.dumps(byte_truncated["diff"], ensure_ascii=False).encode()) <= 24 * 1024


def test_full_does_not_raise_line_budget(db):
    with duckdb.connect(str(db)) as conn:
        _raw(conn, BASELINE, "hostname x\n" * 4001)
        _raw(conn, TARGET, "hostname y")
    assert _error("raw_budget_exceeded", full=True)["side"] == "baseline"


def test_declared_source_schema_rejects_view_or_implicit_conversion(db):
    with duckdb.connect(str(db)) as conn:
        _raw(conn, BASELINE, "hostname x")
        _raw(conn, TARGET, "hostname y")
        conn.execute(f"CREATE VIEW {LEDGER} AS SELECT * FROM {RAW}")
    _error("unsupported_source_schema")
    with duckdb.connect(str(db)) as conn:
        conn.execute(f"DROP VIEW {LEDGER}")
        conn.execute(f"CREATE TABLE {LEDGER} AS SELECT 1 AS snapshot_id, device_name, command, raw_output, '' AS source_json FROM {RAW}")
    _error("unsupported_source_schema")


def test_section_selection_retains_the_whole_selected_stanza(db):
    before = "route-map EDGE permit 10\n match ip address OLD\n!\nroute-map EDGE deny 20\n match ip address SAME\n"
    after = before.replace("OLD", "NEW")
    with duckdb.connect(str(db)) as conn:
        _raw(conn, BASELINE, before)
        _raw(conn, TARGET, after)
    assert _call(sections=["route-map EDGE permit 10"])["changed_lines"] == 2
    _error("sections_not_found", sections=["route-map", "interface absent"])


@pytest.mark.parametrize("suffix", ["wal", "symlink", "hardlink"])
def test_script_rejects_unsafe_source_without_mutating_it(db, suffix):
    payload = db.read_bytes()
    if suffix == "wal":
        db.with_name(db.name + ".wal").write_bytes(b"synthetic sidecar")
        _error("source_has_wal")
    if suffix == "symlink":
        moved = db.with_name("protected.duckdb")
        db.rename(moved)
        db.symlink_to(moved)
        _error("source_unavailable")
    if suffix == "hardlink":
        db.with_name("other.duckdb").hardlink_to(db)
        _error("unsafe_source")
    assert db.read_bytes() == payload


def test_cli_dispatches_existing_script_in_source_checkout(db):
    with duckdb.connect(str(db)) as conn:
        _raw(conn, BASELINE, "hostname x")
        _raw(conn, TARGET, "hostname y")
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "olav_skills", "run", "netops", "diff_configs"],
        input=json.dumps(DEFAULT), text=True, capture_output=True, timeout=20, check=True,
    )
    assert not completed.stderr
    assert json.loads(completed.stdout)["changed_lines"] == 2
