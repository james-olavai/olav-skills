"""Verify the release native readers through their real execs."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

import duckdb
import pytest

import olav_skills


CONFIG = "olav_skills.netops.core.diff.configs"
METRIC = "olav_skills.metric_baselines"
PARSER = "olav_skills.parser_lifecycle"
COUNTER = "olav_skills.counter_deltas"
ROUTES = "olav_skills.route_changes"
TOPOLOGY = "olav_skills.topology_changes"
PATHS = "olav_skills.topology_paths"
EXPORT = "olav_skills.bounded_export"
QUERY = "olav_skills.query_recipes"
READERS = [CONFIG, METRIC, PARSER, COUNTER, ROUTES, TOPOLOGY, PATHS, EXPORT, QUERY]
DB_PATH = ".olav/databases/main.duckdb"
REJECTED = "native_reader_bootstrap_rejected"


@pytest.fixture
def project(tmp_path):
    home = tmp_path / "project"
    db = home / DB_PATH
    db.parent.mkdir(parents=True)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE SCHEMA netops")
        conn.execute(
            "CREATE TABLE netops.raw_output_store (snapshot_id VARCHAR, device_name VARCHAR,"
            " command VARCHAR, raw_output VARCHAR, platform VARCHAR)"
        )
        conn.executemany("INSERT INTO netops.raw_output_store VALUES (?, 'Edge-1', ?, ?, 'cisco_ios')", [
            ("H0", "show running-config", "hostname before"),
            ("H1", "show running-config", "hostname after"),
            ("T0", "show widget state", "widget sample"),
        ])
        conn.execute(
            "CREATE TABLE netops.parsed_outputs (snapshot_id VARCHAR, device_name VARCHAR,"
            " command VARCHAR, platform VARCHAR, parsed_data JSON, collected_at VARCHAR)"
        )
        for index, sid in enumerate(["H0", "H1", "H2", "H3", "H4", "H5", "T0", "T1"]):
            counters = json.dumps([{"interface": "GigabitEthernet0/1", "input_errors": str(index * 10)}])
            clock = (start + timedelta(seconds=index * 10)).isoformat()
            conn.execute(
                "INSERT INTO netops.parsed_outputs VALUES (?, 'Edge-1', 'show interfaces', 'cisco_ios', ?, ?)",
                [sid, counters, clock],
            )
        conn.execute(
            "CREATE TABLE netops.devices (hostname VARCHAR, ip_address VARCHAR, platform VARCHAR,"
            " vendor VARCHAR, model VARCHAR, role VARCHAR)"
        )
        conn.execute("INSERT INTO netops.devices VALUES ('Edge-1', '192.0.2.10', 'cisco_ios', 'Cisco', NULL, NULL)")
        for index, sid in enumerate(["H0", "H1"]):
            route = [{"network": "203.0.113.0", "prefix_length": "24", "vrf": "BLUE",
                      "nexthop_ip": f"192.0.2.{index + 1}", "nexthop_if": "Gi0/1", "nexthop_vrf": ""}]
            adjacent = [{"neighbor_name": "Edge-2", "local_interface": "Gi0/1", "neighbor_interface": "Gi0/2"}]
            conn.executemany(
                "INSERT INTO netops.parsed_outputs VALUES (?, 'Edge-1', ?, 'cisco_ios', ?, NULL)",
                [(sid, "show ip route", json.dumps(route)), (sid, "show cdp neighbors detail", json.dumps(adjacent))],
            )
    return home


def request(module, project):
    pair = dict(snapshot_id_1="H0", snapshot_id_2="H1")
    if module == CONFIG:
        return dict(device="Edge-1", command="show running-config", **pair)
    if module == METRIC:
        return dict(device="Edge-1", interface="GigabitEthernet0/1", profile="input_errors",
                    baseline_snapshot_ids=["H0", "H1", "H2", "H3", "H4", "H5"],
                    target_snapshot_ids=["T0", "T1"])
    if module == PARSER:
        return dict(project=str(project), snapshot_id="T0", platform="cisco_ios", command="show widget state")
    if module == COUNTER:
        return dict(device="Edge-1", **pair)
    if module == ROUTES:
        return dict(source="ios_ipv4_rib", **pair)
    if module == TOPOLOGY:
        return pair
    if module == PATHS:
        return dict(snapshot_id="H0", source_device="Edge-1", destination_device="Edge-2")
    if module == EXPORT:
        return dict(project=str(project), export_id="release-reader", source="parsed_outputs",
                    snapshot_id="H5", columns=["device_name"])
    return dict(recipe="inventory", snapshot_id="H0")


def worker_arguments(module, scratch):
    deadline = str(time.monotonic() + 12)
    if module == CONFIG:
        return ["-B", "-m", module, str(scratch)]
    if module == PARSER:
        return ["-B", "-m", module, "source"]
    if module == COUNTER:
        return ["-B", "-m", module, str(scratch), deadline]
    if module == EXPORT:
        return ["-B", "-m", module]
    if module == ROUTES:
        return ["-B", "-m", module, "--worker", str(scratch), deadline]
    return ["-B", "-P", "-s", "-m", module, "--worker", str(scratch), deadline]


def environment(project, scratch, hook=None):
    packages = Path(olav_skills.__file__).resolve().parents[1]
    paths = [str(packages)] if hook is None else [str(hook), str(packages)]
    return {"OLAV_HOME": str(project), "TMPDIR": str(scratch), "PYTHONPATH": os.pathsep.join(paths),
            "PYTHONIOENCODING": "utf-8", "OPENBLAS_NUM_THREADS": "1", "TZ": "UTC"}


def run_worker(project, tmp_path, module, code):
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(code)
    process = subprocess.run(
        [sys.executable, *worker_arguments(module, scratch)],
        input=json.dumps(request(module, project)), text=True, capture_output=True,
        cwd=scratch, env=environment(project, scratch, hook), timeout=20,
    )
    assert not list(scratch.iterdir())
    return process


INSTRUMENT = """
import json, resource, sys
from pathlib import Path
from olav_skills import native_reader_bootstrap as bootstrap, native_snapshot as snapshot

original_bootstrap = bootstrap.bootstrap_native_reader
original_verify = snapshot._verify_source
original_close = snapshot.close_native_reader
source_checks = 0

def record(event, **fields):
    print('READER ' + json.dumps(dict(event=event, **fields)), file=sys.stderr)

def start(module):
    duckdb = original_bootstrap(module)
    connection = duckdb.default_connection()
    settings = connection.execute(
        "SELECT current_setting('threads'), current_setting('memory_limit'),"
        " current_setting('enable_external_access'), current_setting('lock_configuration')"
    ).fetchone()
    record('start', settings=settings, threads=len(list(Path('/proc/self/task').iterdir())),
           address_space=resource.getrlimit(resource.RLIMIT_AS)[0])
    return duckdb

def verify(*args):
    global source_checks
    source_checks += 1
    return original_verify(*args)

def close():
    original_close()
    record('close', source_checks=source_checks, threads=len(list(Path('/proc/self/task').iterdir())))

bootstrap.bootstrap_native_reader = start
snapshot._verify_source = verify
snapshot.close_native_reader = close
"""


@pytest.mark.parametrize("module", READERS)
def test_real_reader_bootstraps_reads_and_closes(project, tmp_path, module):
    db = project / DB_PATH
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    process = run_worker(project, tmp_path, module, INSTRUMENT)
    assert process.returncode == 0, (process.returncode, process.stdout, process.stderr)
    result = json.loads(process.stdout)
    if module == CONFIG:
        assert result["changed_lines"] == 2 and result["database_version"]["sha256"] == digest
    elif module == METRIC:
        assert result["statistics"]["p95"] == 1 and result["source_version"]["sha256"] == digest
    elif module == PARSER:
        row, = result["value"]["records"]
        assert row["device_name"] == "Edge-1" and row["raw"] == "widget sample"
    elif module == COUNTER:
        row, = result["samples"]
        assert row["counters"]["input_errors"]["delta"] == 10
    elif module == ROUTES:
        assert result["counts"]["next_hop_changed"] == 1
    elif module == TOPOLOGY:
        assert result["counts"] == {"observed_added": 0, "observed_removed": 0,
                                    "content_changed": 0, "unchanged": 0, "content_unknown": 1}
        assert result["changes"][0]["membership_status"] == "common"
    elif module == PATHS:
        assert result["path_count"] == 1 and result["search_complete"]
    elif module == EXPORT:
        assert result["status"] == "complete" and result["total_rows"] == 1
        raw = (project / result["manifest"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == result["receipt"]
    else:
        assert result["steps"][0]["counts"]["observed_device_count"] == 1
        assert result["limits"]["memory_mb"] == 256
        assert result["source_version"]["database_sha256"] == digest
    assert hashlib.sha256(db.read_bytes()).hexdigest() == digest
    started, closed = [json.loads(line[7:]) for line in process.stderr.splitlines() if line.startswith("READER ")]
    address_space = resource.getrlimit(resource.RLIMIT_AS)[0] if module == QUERY else 768 * 1024 * 1024
    assert started == {"event": "start", "settings": [1, "128.0 MiB", False, True],
                       "threads": 1, "address_space": address_space}
    assert closed == {"event": "close", "source_checks": 3, "threads": 1}


def rejection(result, module):
    if module in (PARSER, EXPORT):
        return result.get("error") or result["reason"]
    return result["error_type"]


@pytest.mark.parametrize("module", READERS)
def test_real_reader_rejects_preloaded_default_without_closing_it(project, tmp_path, module):
    process = run_worker(project, tmp_path, module, """
import atexit, duckdb, sys
duckdb.sql('CREATE TEMP TABLE preload_guard AS SELECT 41 AS marker')

def cleanup():
    assert duckdb.sql('SELECT marker FROM preload_guard').fetchone() == (41,)
    duckdb.close()
    print('PRELOAD_OK', file=sys.stderr)

atexit.register(cleanup)
""")
    assert process.returncode == int(module == QUERY), (process.stdout, process.stderr)
    assert rejection(json.loads(process.stdout), module) == REJECTED
    assert process.stderr.strip() == "PRELOAD_OK"


@pytest.mark.parametrize("module", READERS)
def test_real_reader_rejects_preloaded_low_level_duckdb(project, tmp_path, module):
    process = run_worker(project, tmp_path, module, "import _duckdb")
    assert process.returncode == int(module == QUERY), (process.stdout, process.stderr)
    assert rejection(json.loads(process.stdout), module) == REJECTED
    assert not process.stderr


@pytest.mark.parametrize("module", READERS)
def test_runpy_with_forged_argv_preserves_default_writer_and_limits(project, tmp_path, module):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    program = """
import json, os, resource, runpy, sys
from pathlib import Path
import duckdb

module, arguments = sys.argv[1], json.loads(sys.argv[2])
db = Path(os.environ['OLAV_HOME']) / '.olav/databases/main.duckdb'
duckdb.sql('CREATE TEMP TABLE caller_guard AS SELECT 45 AS marker')
kinds = (resource.RLIMIT_AS, resource.RLIMIT_CPU, resource.RLIMIT_FSIZE, resource.RLIMIT_NOFILE)
before = [resource.getrlimit(kind) for kind in kinds]
with duckdb.connect(str(db)) as writer:
    sys.argv = [module, *arguments[arguments.index(module) + 1:]]
    sys.orig_argv = [sys.executable, *arguments]
    try:
        runpy.run_module(module, run_name='__main__', alter_sys=True)
    except SystemExit as end:
        assert end.code == int(module == 'olav_skills.query_recipes')
    assert [resource.getrlimit(kind) for kind in kinds] == before
    assert duckdb.sql('SELECT marker FROM caller_guard').fetchone() == (45,)
    assert writer.execute('SELECT count(*) FROM netops.raw_output_store').fetchone() == (3,)
duckdb.close()
"""
    process = subprocess.run(
        [sys.executable, "-B", "-c", program, module, json.dumps(worker_arguments(module, scratch))],
        input=json.dumps(request(module, project)), text=True, capture_output=True,
        cwd=scratch, env=environment(project, scratch), timeout=20,
    )
    assert process.returncode == 0, (process.stdout, process.stderr)
    assert rejection(json.loads(process.stdout), module) == REJECTED


@pytest.mark.parametrize("module", READERS)
@pytest.mark.parametrize("failure", ["source", "close"])
def test_real_reader_has_no_success_on_final_source_or_close_failure(project, tmp_path, module, failure):
    inject = """
def broken_verify(*args):
    if source_checks == 2:
        raise snapshot.NativeSnapshotError('source_changed', 'QA final verification failure')
    verify(*args)
snapshot._verify_source = broken_verify
""" if failure == "source" else """
def broken_close():
    raise RuntimeError('QA close failure')
snapshot.close_native_reader = broken_close
"""
    process = run_worker(project, tmp_path, module, INSTRUMENT + inject)
    if failure == "close":
        assert process.returncode != 0 and not process.stdout and "QA close failure" in process.stderr
        if module == EXPORT:
            job = project / ".olav/exports/release-reader"
            manifest = json.loads((job / "manifest.json").read_bytes())
            assert manifest["status"] == "complete" and manifest["total_rows"] == 1
            part, = manifest["parts"]
            raw = (job / part["file"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == part["sha256"]
    else:
        assert process.returncode == int(module == QUERY), (process.stdout, process.stderr)
        assert "source_changed" in rejection(json.loads(process.stdout), module)


def test_parser_parse_worker_keeps_original_branch(tmp_path):
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text("""
from olav_skills import native_reader_bootstrap as bootstrap
def forbid(*_):
    raise AssertionError('parse entered native reader bootstrap')
bootstrap.validate_native_reader_identity = forbid
bootstrap.bootstrap_native_reader = forbid
""")
    payload = dict(dsl="textfsm", source="Value NAME (\\S+)\n\nStart\n  ^widget ${NAME}$$ -> Record\n",
                   raw=["widget sample"])
    process = subprocess.run(
        [sys.executable, "-B", "-m", PARSER, "parse"], input=json.dumps(payload),
        text=True, capture_output=True, cwd=tmp_path, env=environment(tmp_path, tmp_path, hook), timeout=20,
    )
    assert process.returncode == 0, (process.stdout, process.stderr)
    assert json.loads(process.stdout) == {"value": [[{"name": "sample"}]]}
