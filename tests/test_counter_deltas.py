"""Counter truth from native IOS captures and adversarial synthetic storage."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import duckdb
import pytest
import yaml


DB = ".olav/databases/main.duckdb"
COMMAND = "show interfaces"
DEVICE = "Edge-1"
PORT = "GigabitEthernet0/1"
FIELDS = ("input_errors", "crc", "output_errors", "queue_drops", "queue_output_drops")
REQUEST = {"snapshot_id_1": "Before", "snapshot_id_2": "After"}
T1 = "2026-10-01T00:00:00Z"
T2 = "2026-10-01T00:00:10Z"


def call(home, args):
    env = {k: os.environ[k] for k in ("PATH", "PYTHONPATH", "TMPDIR") if k in os.environ}
    env["OLAV_HOME"] = str(home)
    command = ["-m", "olav_skills", "run", "reporter", "compare_counters"]
    result = subprocess.run([sys.executable, "-B", *command], cwd=home, env=env,
                            input=json.dumps(args), text=True, capture_output=True, timeout=60)
    assert result.stdout, (result.returncode, result.stderr)
    return result.returncode, json.loads(result.stdout)


def native_bundle(path, side, date, values, *, output_missing=False, capture_header=True):
    dev = path / "devices" / DEVICE
    dev.mkdir(parents=True)
    commands = 2 if side == "after" else 1
    (dev / "_meta.yaml").write_text(yaml.safe_dump(
        {"hostname": DEVICE, "mgmt_ip": "192.0.2.8", "platform": "cisco_ios", "vendor": "Cisco",
         "commands_attempted": commands, "commands_succeeded": commands, "commands_failed": 0,
         "collected_at": date}
    ))
    inp, crc, out, drop, output_drop = values
    clock_header = f"# collected_at: {date}\n" if capture_header else ""
    raw = (f"# command: {COMMAND}\n{clock_header}# pre_scrubbed: true\n\n"
           "Gi0/1 is up, line protocol is up\n"
           "  Hardware is Gigabit Ethernet, address is 0000.0000.0001 (bia 0000.0000.0001)\n"
           '  Last clearing of "show interface" counters never\n'
           f"  Input queue: 0/75/{drop}/0 (size/max/drops/flushes); Total output drops: {output_drop}\n"
           f"     {inp} input errors, {crc} CRC, 0 frame, 0 overrun, 0 ignored\n")
    if not output_missing:
        raw += f"     {out} output errors, 0 collisions, 0 interface resets\n"
    capture = dev / "show_interfaces.txt"
    capture.write_text(raw)
    if side == "after":
        (dev / "show_version.txt").write_text(
            f"# command: show version\n{clock_header}# pre_scrubbed: true\n\n"
            "Cisco IOS XE Software (CAT9K_IOSXE), Version 17.09.04, RELEASE SOFTWARE (fc1)\n"
            "Edge-1 uptime is 0 minutes\n"
            "System restarted at 00:00:07 UTC Thu Oct 1 2026\n"
        )
    content = hashlib.sha256()
    for command in sorted(dev.glob("*.txt")):
        content.update(command.read_bytes())
    (path / "manifest.yaml").write_text(yaml.safe_dump(
        {"schema_version": 1, "collector": {"name": "olav-collector", "version": "0.0.0-fixture"},
         "collected_at": date, "workspace_id": side, "hosts_collected": 1, "hosts_failed": 0,
         "redaction": {"pre_scrubbed": True, "salt_fingerprint": "fixture0"},
         "content_sha256": content.hexdigest()}
    ))
    return path


@pytest.mark.parametrize("capture_header", [False, True])
def test_native_import_to_reporter(tmp_path, capture_header):
    home = tmp_path / "project"
    home.mkdir()
    env = {k: os.environ[k] for k in ("PATH", "PYTHONPATH", "TMPDIR") if k in os.environ}
    env["OLAV_HOME"] = str(home)
    sids = []
    for side, date, values, missing in [
        ("before", "2026-10-01T00:00:00Z", (11, 6, 2, 0, 14), False),
        ("after", "2026-10-01T00:00:10Z", (15, 4, 0, 0, 21), True),
    ]:
        fixture = native_bundle(tmp_path / side, side, date, values, output_missing=missing, capture_header=capture_header)
        p = subprocess.run([sys.executable, "-B", "-m", "olav_skills", "run", "importer", "ingest_snapshot"],
                           cwd=home, env=env, input=json.dumps({"path": str(fixture)}), text=True,
                           capture_output=True, timeout=60)
        assert p.returncode == 0, (p.stdout, p.stderr)
        imported = json.loads(p.stdout)
        assert imported["parser_fills"][COMMAND] == 1
        sids.append(imported["snapshot_id"])
    db = home / DB
    with duckdb.connect(str(db), read_only=True) as conn:
        rows = conn.execute("SELECT snapshot_id, platform, parsed_data FROM netops.parsed_outputs "
                            "WHERE command = ?", [COMMAND]).fetchall()
        version = conn.execute("SELECT parsed_data FROM netops.parsed_outputs WHERE command = ?",
                               ["show version"]).fetchone()[0]
    payloads = {sid: json.loads(body)[0] for sid, _, body in rows}
    assert {platform for _, platform, _ in rows} == {"cisco_ios"}
    assert [payloads[sid]["interface"] for sid in sids] == [PORT, PORT]
    assert [payloads[sids[0]][name] for name in FIELDS] == ["11", "6", "2", "0", "14"]
    assert payloads[sids[1]]["output_errors"] == ""
    assert json.loads(version)[0]["restarted"] == "00:00:07 UTC Thu Oct 1 2026"
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    code, out = call(home, dict(snapshot_id_1=sids[0], snapshot_id_2=sids[1], device=DEVICE,
                                interface=PORT))
    assert code == 0, out
    assert out["source_version"]["sha256"] == digest == hashlib.sha256(db.read_bytes()).hexdigest()
    assert out["counts"]["matched_interfaces"] == 1
    row, = out["samples"]
    field = row["counters"]
    if capture_header:
        # The native ledger now retains real command clocks. The recorded reboot
        # is inside this interval, so increasing counters cannot supply deltas.
        assert row["elapsed_seconds"] == 10
        assert row["discontinuities"][0]["kind"] == "device_restart"
        for name in ("input_errors", "crc", "queue_drops", "queue_output_drops"):
            assert field[name]["delta"] is None
            assert field[name]["delta_status"] == "confirmed_discontinuity"
    else:
        # Device/manifest clocks alone still cannot time this command interval.
        assert field["input_errors"]["delta"] == 4 and field["input_errors"]["rate"] is None
        assert field["crc"]["delta"] is None and field["crc"]["delta_status"] == "possible_reset_or_wrap"
        assert field["queue_drops"]["delta"] == 0
        assert field["queue_output_drops"]["delta"] == 7
        assert row["elapsed_seconds"] is None
    assert field["output_errors"]["after"] is None and field["output_errors"]["delta_status"] == "unknown"

    # Add explicit parsed command times only to this synthetic DB. This makes
    # the no-header variant timed too; the header variant retains the same
    # already evidenced interval and reboot, rather than gaining new evidence.
    with duckdb.connect(str(db)) as conn:
        conn.execute("ALTER TABLE netops.parsed_outputs ADD COLUMN collected_at TIMESTAMPTZ")
        for sid, clock in zip(sids, (T1, T2)):
            conn.execute("UPDATE netops.parsed_outputs SET collected_at = ? WHERE snapshot_id = ?", [clock, sid])
    timed_digest = hashlib.sha256(db.read_bytes()).hexdigest()
    code, timed = call(home, dict(snapshot_id_1=sids[0], snapshot_id_2=sids[1], device=DEVICE))
    assert code == 0 and timed["source_version"]["sha256"] == timed_digest, timed
    timed_row, = timed["samples"]
    assert timed_row["elapsed_seconds"] == 10
    assert timed_row["discontinuities"][0]["kind"] == "device_restart"
    assert timed_row["counters"]["input_errors"]["delta_status"] == "confirmed_discontinuity"
    assert timed_row["counters"]["output_errors"]["delta_status"] == "unknown"
    assert hashlib.sha256(db.read_bytes()).hexdigest() == timed_digest


def counter_row(port=PORT, **overrides):
    return {"interface": port, "input_errors": "0", "crc": "0", "output_errors": "0",
            "queue_drops": "0", "queue_output_drops": "0", **overrides}


def put(conn, sid, device, payload, *, clock=None, captured=None, command=COMMAND, platform="cisco_ios"):
    conn.execute("INSERT INTO netops.parsed_outputs VALUES (?, ?, ?, ?, ?, ?, ?)",
                 [sid, device, command, platform, json.dumps(payload), clock, captured])


@pytest.fixture
def project(tmp_path):
    db = tmp_path / DB
    db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE SCHEMA netops")
        conn.execute("""CREATE TABLE netops.parsed_outputs(
                     snapshot_id VARCHAR COLLATE NOCASE, device_name VARCHAR COLLATE NOCASE,
                     command VARCHAR COLLATE NOCASE, platform VARCHAR, parsed_data JSON,
                     collected_at VARCHAR, captured_at VARCHAR)""")
    return tmp_path


def test_counter_math_exact_observed_values_and_clock(project):
    with duckdb.connect(str(project / DB)) as conn:
        put(conn, "Before", DEVICE, [counter_row(input_errors=2**64 - 3, crc=17, output_errors=False,
                                                queue_drops=2, queue_output_drops=-1)], clock=T1)
        put(conn, "After", DEVICE, [counter_row(input_errors=str(2**64 - 1), crc=15, output_errors=2,
                                               queue_drops=2, queue_output_drops=5)], clock=T2)
    code, out = call(project, REQUEST)
    assert code == 0, out
    row, = out["samples"]
    assert row["elapsed_seconds"] == 10
    assert row["discontinuities"] == []
    counters = row["counters"]
    assert counters["input_errors"]["after"] == 2**64 - 1
    assert counters["input_errors"]["delta"] == 2
    assert counters["input_errors"]["rate"] == 0.2
    assert counters["input_errors"]["rate_unit"] == "errors/second"
    assert counters["crc"]["delta_status"] == "possible_reset_or_wrap"
    assert counters["crc"]["delta"] is counters["crc"]["rate"] is None
    assert counters["output_errors"]["delta_status"] == "unknown"
    assert counters["queue_output_drops"]["before"] is None
    assert counters["queue_drops"]["rate"] == 0 and counters["queue_drops"]["rate_unit"] == "packets/second"


@pytest.mark.parametrize(("clock", "captured", "status"), [
    ("2026-10-01", None, "unknown"),
    ("2026-10-01 00:00:10", None, "unknown"),
    (None, None, "unknown"),
    (T2, "2026-10-01T00:00:11Z", "unknown"),
    (T2, "X" * 129, "unknown"),
    (T1, None, "nonpositive_interval"),
    ("2026-09-30T23:59:59Z", None, "nonpositive_interval"),
])
def test_unusable_capture_times_keep_delta_without_rate(project, clock, captured, status):
    with duckdb.connect(str(project / DB)) as conn:
        put(conn, "Before", DEVICE, [counter_row(input_errors=2)], clock=T1)
        put(conn, "After", DEVICE, [counter_row(input_errors=7)], clock=clock, captured=captured)
    _, out = call(project, REQUEST)
    row, = out["samples"]
    assert row["time_status"] == status
    assert row["elapsed_seconds"] is None
    assert row["counters"]["input_errors"]["delta"] == 5
    assert row["counters"]["input_errors"]["rate"] is None


def test_reboot_requires_bracketed_restarted_time_and_command_capture(project):
    with duckdb.connect(str(project / DB)) as conn:
        for device in ("Boot", "BadWeekday", "Future", "NoVersionClock", "DurationsOnly"):
            put(conn, "Before", device, [counter_row(crc=20, output_errors=5)], clock=T1)
            put(conn, "After", device, [counter_row(crc=3, output_errors=13)], clock=T2)
        for device, restarted, observed_at in [
            ("Boot", "00:00:07 UTC Thu Oct 1 2026", T2),
            ("BadWeekday", "00:00:07 UTC Wed Oct 1 2026", T2),
            ("Future", "00:00:11 UTC Thu Oct 1 2026", T2),
            ("NoVersionClock", "00:00:07 UTC Thu Oct 1 2026", None),
            ("DurationsOnly", None, T2),
        ]:
            put(conn, "After", device, [{"restarted": restarted, "uptime": "1 minute"}],
                clock=observed_at, command="show version")
    _, out = call(project, REQUEST)
    by_device = {row["device"]: row for row in out["samples"]}
    boot = by_device.pop("Boot")
    event, = boot["discontinuities"]
    assert event["kind"] == "device_restart"
    assert event["value"] == "2026-10-01T00:00:07+00:00"
    for name in ("crc", "output_errors"):
        value = boot["counters"][name]
        assert value["delta"] is value["rate"] is None
        assert value["delta_status"] == "confirmed_discontinuity"
    for other in by_device.values():
        assert other["discontinuities"] == []
        assert other["counters"]["crc"]["delta_status"] == "possible_reset_or_wrap"
        assert other["counters"]["output_errors"]["delta"] == 8


def ledger_row(conn, sid, device, raw, clock, *, forged=False):
    cited = {"source": "command_header", "status": "known", "precision": "timestamp", "value": clock}
    data = {"raw_artifact": {"table": "netops.ingest_sources", "snapshot_id": sid,
                             "device_name": device, "command": COMMAND},
            "device_name": device, "command": COMMAND, "effective_platform": "cisco_ios", "parse_status": "parsed",
            "actual_raw_sha256": hashlib.sha256(raw.encode()).hexdigest() if not forged else "0" * 64,
            "capture_evidence": [cited], "collected_at": cited}
    conn.execute("INSERT INTO netops.ingest_sources VALUES (?, ?, ?, ?, ?)",
                 [sid, device, COMMAND, json.dumps(data), raw])


def test_clearing_evidence_is_bound_to_exact_interface_and_raw_hash(project):
    second = "GigabitEthernet0/2"
    raw = ("Gi0/1 is up, line protocol is up\n"
           '  Last clearing of "show interface" counters 00:00:02\n'
           "Gi0/2 is up, line protocol is up\n"
           '  Last clearing of "show interface" counters never\n')
    with duckdb.connect(str(project / DB)) as conn:
        conn.execute("""CREATE TABLE netops.ingest_sources(snapshot_id VARCHAR, device_name VARCHAR, command VARCHAR,
                                                             source_json VARCHAR, raw_output VARCHAR)""")
        for device in ("Bound", "WrongHash"):
            put(conn, "Before", device, [counter_row(), counter_row(second)], clock=T1)
            put(conn, "After", device, [counter_row(crc=3), counter_row(second, crc=3)])
            ledger_row(conn, "After", device, raw, T2, forged=device == "WrongHash")
    _, out = call(project, REQUEST)
    rows = {(row["device"], row["interface"]): row for row in out["samples"]}
    cleared = rows["Bound", PORT]
    assert cleared["counters"]["crc"]["delta_status"] == "confirmed_discontinuity"
    assert cleared["elapsed_seconds"] == 10
    assert cleared["discontinuities"][0]["kind"] == "interface_counter_clear"
    assert cleared["discontinuities"][0]["earliest"] == "2026-10-01T00:00:07+00:00"
    unaffected = rows["Bound", second]
    assert unaffected["discontinuities"] == []
    assert unaffected["counters"]["crc"]["rate"] == 0.3
    unbound = rows["WrongHash", PORT]
    assert unbound["discontinuities"] == []
    assert unbound["counters"]["crc"]["delta"] == 3
    assert unbound["counters"]["crc"]["rate"] is None


def test_exact_case_and_scope_with_one_sided_and_unsupported_observations(project):
    with duckdb.connect(str(project / DB)) as conn:
        put(conn, "Before", DEVICE, [counter_row(), counter_row("Te0/1")])
        put(conn, "After", DEVICE, [counter_row(), counter_row("Gi0/01")])
        put(conn, "After", DEVICE.lower(), [counter_row()])
        put(conn, "before", DEVICE, [counter_row("ignored")])
        put(conn, "After", DEVICE, [counter_row("ignored")], command="SHOW INTERFACES")
        put(conn, "Before", "J1", [counter_row(crc=9)], platform="juniper_junos")
    _, out = call(project, {**REQUEST, "device": DEVICE})
    assert out["counts"]["matched_interfaces"] == 1
    assert out["counts"]["before_only_interfaces"] == out["counts"]["after_only_interfaces"] == 1
    assert {row["interface"] for row in out["samples"]} == {PORT, "Te0/1", "Gi0/01"}
    _, lower = call(project, {**REQUEST, "device": DEVICE.lower()})
    assert lower["comparison_status"] == "unknown" and lower["counts"]["after_only_interfaces"] == 1
    assert lower["samples"][0]["counters"]["crc"]["delta"] is None
    _, junos = call(project, {**REQUEST, "device": "J1"})
    assert junos["comparison_status"] == "unknown" and junos["counts"]["before_unsupported_source_rows"] == 1
    _, wrong_interface = call(project, {**REQUEST, "device": DEVICE, "interface": PORT.lower()})
    assert wrong_interface["comparison_status"] == "unknown" and not wrong_interface["samples"]


def test_fleet_counters_are_exact_independently_of_sample_limits(project):
    with duckdb.connect(str(project / DB)) as conn:
        for sid in ("Before", "After"):
            conn.execute("""
                INSERT INTO netops.parsed_outputs
                SELECT ?, 'router-' || lpad(i::VARCHAR, 4, '0'), 'show interfaces', 'ios', ?::JSON, NULL, NULL
                FROM range(3000) t(i)
            """, [sid, json.dumps([counter_row()])])
    _, wide = call(project, {**REQUEST, "limit": 50})
    _, narrow = call(project, {**REQUEST, "limit": 1})
    assert narrow["counts"] == wide["counts"]
    assert wide["counts"]["matched_interfaces"] == 3000
    assert wide["counts"]["counters"]["crc"]["known_deltas"] == 3000
    assert wide["counts"]["counters"]["crc"]["unknown_rates"] == 3000
    assert narrow["has_more"] and narrow["sample_count"] == 1
    assert wide["has_more"] and wide["sample_byte_truncated"] and wide["sample_count"] < 50
    assert wide["samples"][0]["device"] == "router-0000"


def test_duplicate_source_and_interface_are_errors(project):
    db = project / DB
    with duckdb.connect(str(db)) as conn:
        put(conn, "Before", DEVICE, [counter_row()])
        put(conn, "Before", DEVICE, [counter_row()])
    code, out = call(project, REQUEST)
    assert code == 1 and out["error_type"] == "ambiguous_source" and out["counts"] is None
    with duckdb.connect(str(db)) as conn:
        conn.execute("DELETE FROM netops.parsed_outputs")
        put(conn, "Before", DEVICE, [counter_row(), counter_row()])
    code, out = call(project, REQUEST)
    assert code == 1 and out["error_type"] == "ambiguous_interface_identity" and not out["samples"]


def test_view_is_not_historical_source_and_wal_is_rejected(project):
    db = project / DB
    wal = db.with_suffix(".duckdb.wal")
    wal.touch()
    assert call(project, REQUEST)[1]["error_type"] == "source_has_wal"
    wal.unlink()
    with duckdb.connect(str(db)):
        assert call(project, REQUEST)[1]["error_type"] == "source_locked"
    with duckdb.connect(str(db)) as conn:
        conn.execute("ALTER TABLE netops.parsed_outputs RENAME TO stored")
        conn.execute("CREATE VIEW netops.parsed_outputs AS SELECT * FROM netops.stored")
    assert call(project, REQUEST)[1]["error_type"] == "unsupported_source_schema"


def test_large_source_and_invalid_requests_are_bounded(project):
    with duckdb.connect(str(project / DB)) as conn:
        put(conn, "Before", DEVICE, [counter_row(padding="A" * (512 * 1024))])
    code, out = call(project, REQUEST)
    assert code == 1 and out["error_type"] == "source_budget_exceeded" and out["counts"] is None
    assert call(project, {**REQUEST, "snapshot_id_1": "LATEST"})[1]["error_type"] == "invalid_snapshot_id"
    assert call(project, {**REQUEST, "snapshot_id_1": "After"})[1]["error_type"] == "invalid_snapshot_id"
    assert call(project, {**REQUEST, "limit": True})[1]["error_type"] == "invalid_request"


def test_worker_deadline_removes_parent_scratch(project, monkeypatch):
    from olav_skills import counter_deltas

    scratch = project / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(counter_deltas.tempfile, "tempdir", str(scratch))
    monkeypatch.setenv("OLAV_HOME", str(project))
    monkeypatch.setattr(counter_deltas, "TIMEOUT_SECONDS", 0.001)
    assert counter_deltas.compare_counters(REQUEST)["error_type"] == "deadline_exceeded"
    assert list(scratch.iterdir()) == []
