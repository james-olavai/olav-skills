from __future__ import annotations

import json

import duckdb
import pytest

from olav_skills.netops.core import view_builder


@pytest.fixture
def db():
    with duckdb.connect(config={"threads": 1}) as con:
        con.execute("""
            CREATE SCHEMA netops;
            CREATE TABLE netops.devices (
                hostname VARCHAR, platform VARCHAR, role VARCHAR, site VARCHAR, ip_address VARCHAR
            );
            CREATE TABLE netops.parsed_outputs (
                device_name VARCHAR, snapshot_id VARCHAR, command VARCHAR,
                parsed_data JSON, ingested_at TIMESTAMP
            );
            CREATE TABLE netops.topology_links (
                source_device VARCHAR, source_interface VARCHAR, destination_device VARCHAR,
                destination_interface VARCHAR, discovery_protocol VARCHAR, link_status VARCHAR,
                snapshot_id VARCHAR
            );
        """)
        yield con


def _land(db, rows, command="show example", *, sid="capture-a", ingested_at="2020-01-01"):
    db.execute(
        "INSERT INTO netops.parsed_outputs VALUES (?, ?, ?, ?, ?)",
        ["fixture-a", sid, command, json.dumps(rows), ingested_at],
    )


def _cached(db):
    fleet, views, values = db.execute("SELECT fleet, views, value_distributions FROM netops.introspection_cache").fetchone()
    return json.loads(fleet) if fleet else [], {
        row["view"]: [(col["name"], col["type"]) for col in row["cols"]]
        for row in (json.loads(views) if views else [])
    }, {
        (row["view"], row["col"], row["value"]): (row["freq"], row["cluster"])
        for row in (json.loads(values) if values else [])
    }


def _cache_exists(db):
    return db.execute(
        """SELECT COUNT(*) FROM duckdb_tables()
           WHERE schema_name = 'netops' AND table_name = 'introspection_cache'"""
    ).fetchone()[0] == 1


def _no_temp_profiles(db):
    return db.execute(
        "SELECT COUNT(*) FROM duckdb_tables() WHERE starts_with(table_name, '_ow143_vp_')"
    ).fetchone()[0] == 0


def test_profile_scope_counts_duplicates_nulls_and_clusters(db):
    db.execute("INSERT INTO netops.devices VALUES ('fixture-a', 'ios', NULL, 'lab', '192.0.2.1')")
    rows = [
        {
            "status": ("Up!!" if i < 2 else "UP" if i < 4 else "down" if i < 30 else None),
            "proto": "ip",
            "category": "core",
            "name": "identity",
            "interface": "eth0",
            "distinct_status": str(i),
            "limit_status": str(i % 30),
            "metric": i,
        }
        for i in range(31)
    ]
    _land(db, rows)
    result = view_builder.finalise_ingest(db)
    assert result["introspection"] == {"introspection_cache_rows": 1}
    name = "v_show_example_auto"
    assert result["value_profile"]["value_profile_rows"] == db.execute(
        "SELECT COUNT(*) FROM netops.value_profile"
    ).fetchone()[0]
    assert db.execute(
        "SELECT COUNT(*) FROM netops.value_profile WHERE view_name = ?", [name]
    ).fetchone()[0] == 35
    profile = db.execute(
        "SELECT column_name, value, freq, fingerprint, cluster_id FROM netops.value_profile "
        "WHERE view_name = ? AND column_name <> 'limit_status'", [name]
    ).fetchall()
    assert set(profile) == {
        ("status", "UP", 2, "up", 2),
        ("status", "Up!!", 2, "up", 2),
        ("status", "down", 26, "down", 1),
        ("proto", "ip", 31, "ip", 1),
        ("category", "core", 31, "core", 1),
    }
    fleet, views, values = _cached(db)
    assert fleet == [{"hostname": "fixture-a", "platform": "ios", "role": None,
                      "site": "lab", "ip": "192.0.2.1"}]
    assert views[name][-2:] == [("limit_status", "VARCHAR"), ("metric", "UBIGINT")]
    assert {key[1] for key in values if key[0] == name} == {"status", "proto", "limit_status"}
    assert values[(name, "status", "down")] == (26, 1)
    assert _no_temp_profiles(db)


def test_mutation_replace_schema_and_delete_command(db):
    _land(db, [{"old_status": "old"}])
    _land(db, [{"status": "unused"}], "show removed")
    assert view_builder.finalise_ingest(db)["introspection"]
    assert ("v_show_example_auto", "old_status", "old") in _cached(db)[2]

    db.execute("DELETE FROM netops.parsed_outputs")
    _land(db, [{"new_status": "new"}, {"new_status": "new"}], sid="capture-b")
    assert view_builder.finalise_ingest(db)["introspection"]
    _, views, values = _cached(db)
    assert views["v_show_example_auto"] == [
        ("device_name", "VARCHAR"), ("snapshot_id", "VARCHAR"), ("new_status", "VARCHAR")]
    assert "v_show_removed_auto" not in views
    assert {key: value for key, value in values.items() if key[0].startswith("v_show_")} == {
        ("v_show_example_auto", "new_status", "new"): (2, 1)}
    assert db.execute(
        "SELECT DISTINCT view_name, column_name FROM netops.value_profile "
        "WHERE starts_with(view_name, 'v_show_')"
    ).fetchall() == [
        ("v_show_example_auto", "new_status")]
    assert _no_temp_profiles(db)


def test_empty_source_then_missing_source(db):
    _land(db, [{"status": "up"}])
    assert view_builder.finalise_ingest(db)["introspection"]
    db.execute("DELETE FROM netops.parsed_outputs")
    result = view_builder.finalise_ingest(db)
    assert result["introspection"] == {"introspection_cache_rows": 1}
    assert result["value_profile"] == {"value_profile_rows": 0}
    fleet, views, values = _cached(db)
    assert fleet == []
    assert set(views) == {"v_l2_links_auto", "v_snapshots_auto"}
    assert not values
    db.execute("DROP TABLE netops.parsed_outputs")
    result = view_builder.finalise_ingest(db)
    assert result["introspection"] == {}
    assert not _cache_exists(db)


def test_failed_cache_sql_discards_old_cache(db):
    _land(db, [{"status": "up"}])
    assert view_builder.finalise_ingest(db)["introspection"]
    db.execute("ALTER TABLE netops.devices DROP COLUMN role")
    assert view_builder.finalise_ingest(db)["introspection"] == {}
    assert not _cache_exists(db)
    assert _no_temp_profiles(db)


def test_real_profile_insert_failure_cannot_publish_partial_cache(db):
    _land(db, [{"status": "up"}])
    assert view_builder.finalise_ingest(db)["introspection"]
    db.execute("ALTER TABLE netops.value_profile DROP COLUMN fingerprint")
    result = view_builder.finalise_ingest(db)
    assert result["value_profile"] == result["introspection"] == {}
    assert not _cache_exists(db)
    assert _no_temp_profiles(db)


def test_caller_keeps_transaction_ownership(db):
    _land(db, [{"status": "up"}])
    assert view_builder.finalise_ingest(db)["introspection"]
    before = _cached(db)
    db.execute("BEGIN")
    db.execute("ALTER TABLE netops.devices DROP COLUMN role")
    assert view_builder.finalise_ingest(db)["introspection"] == {}
    assert not _cache_exists(db)
    db.execute("ROLLBACK")
    assert _cached(db) == before
    assert "role" in {row[0] for row in db.execute("DESCRIBE netops.devices").fetchall()}
    assert _no_temp_profiles(db)


def test_failed_execution_aborts_borrowed_transaction_until_caller_rollback(db):
    _land(db, [{"status": "up"}])
    assert view_builder.finalise_ingest(db)["introspection"]
    before = _cached(db)
    db.execute("BEGIN")
    db.execute("ALTER TABLE netops.devices RENAME TO prior_devices")
    db.execute("""
        CREATE VIEW netops.devices AS
        SELECT 'fixture-a' AS hostname, 'ios' AS platform, 'lab' AS site,
               '192.0.2.1' AS ip_address, error('synthetic source error')::VARCHAR AS role
    """)
    _land(db, [{"status": "down"}], sid="capture-b")
    assert view_builder.finalise_ingest(db)["introspection"] == {}
    with pytest.raises(duckdb.TransactionException, match="aborted"):
        db.execute("SELECT COUNT(*) FROM netops.parsed_outputs")
    db.execute("ROLLBACK")
    assert db.execute("SELECT snapshot_id FROM netops.parsed_outputs").fetchall() == [("capture-a",)]
    assert _cached(db) == before
    assert _no_temp_profiles(db)


def test_current_catalog_scope_and_quoted_parser_keys(db):
    _land(db, [{"port's \"status": "up"}])
    db.execute("ATTACH ':memory:' AS other_catalog")
    db.execute("CREATE SCHEMA other_catalog.netops")
    db.execute("CREATE VIEW other_catalog.netops.v_show_example_auto AS SELECT 'wrong' AS alien_status")
    assert view_builder.finalise_ingest(db)["introspection"]
    _, views, values = _cached(db)
    assert views["v_show_example_auto"] == [
        ("device_name", "VARCHAR"), ("snapshot_id", "VARCHAR"), ('port\'s "status', "VARCHAR")]
    assert values[("v_show_example_auto", 'port\'s "status', "up")] == (1, 1)
    assert _no_temp_profiles(db)
