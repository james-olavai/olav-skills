"""Standalone script regressions, migrated from the public platform tests."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NETOPS_WS = REPO / "skills"
FIXTURE = REPO / "tests" / "fixtures" / "synthetic_2host_bundle"




# A synthetic CDP example for R2 seeing R1. `detail` and
# not the brief form on purpose — brief output puts Platform and Port ID in
# adjacent columns, and ntc-templates mis-splits them into port ids like
# `Uni Eth 0/1`, which `_canonicalise_interface` is right to reject. A fixture
# built on the brief form therefore tests the reject path, not the ETL.
_CDP_R2 = """\
# command: show cdp neighbors detail
# collected_at: 2026-05-15T10:23:30Z
# pre_scrubbed: true

-------------------------
Device ID: R1
Entry address(es):
  IP address: 10.1.12.1
Platform: cisco WS-C3650-24PS,  Capabilities: Switch IGMP
Interface: GigabitEthernet1,  Port ID (outgoing port): GigabitEthernet2
Holdtime : 145 sec

Version :
Cisco IOS Software [Everest], Catalyst L3 Switch Software, Version 16.6.8

advertisement version: 2
Duplex: full

Total cdp entries displayed : 1
"""


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Path:
    """The tracked fixture bundle plus one CDP capture, so L2 is exercised.

    Adding a file changes the bundle's content hash, which ingest checks. The
    manifest is rewritten with the hash computed by the product's own hasher —
    reimplementing it here would let the two drift and turn a real integrity
    failure into a passing test.
    """
    import yaml
    from olav_skills.core.ingest.validators import _hash_bundle_contents

    out = tmp_path_factory.mktemp("bundle")
    dest = out / "collected"
    shutil.copytree(FIXTURE, dest)
    (dest / "devices" / "R2" / "show_cdp_neighbors_detail.txt").write_text(
        _CDP_R2, encoding="utf-8"
    )

    manifest_path = dest / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    observed, _hosts, _commands = _hash_bundle_contents(dest)
    manifest["content_sha256"] = observed
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return dest


def _env(home: Path) -> dict[str, str]:
    """A machine that never ran `olav init` and has no embedding backend.

    `OLAV_HOME` is the project root, so `.olav/` is created *under* it. The
    three embedding vars reproduce the pack default — api mode, nothing
    configured — rather than `mode=none`, which would take a different branch
    and prove nothing about the case operators actually hit.
    """
    env = dict(os.environ)
    for leak in ("OPENAI_API_KEY", "OLAV_EMBEDDING_API_KEY",
                 "OLAV_EMBEDDING_BASE_URL", "OLAV_BASE_PATH"):
        env.pop(leak, None)
    env["OLAV_HOME"] = str(home)
    env["OLAV_EMBEDDING_MODE"] = "api"
    return env


def _run(script: str, payload: dict, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(NETOPS_WS / script)],
        input=json.dumps(payload), capture_output=True, text=True,
        cwd=str(home), env=_env(home), timeout=600,
    )


def _ok(script: str, payload: dict, home: Path):
    """Run a script and parse its last stdout line, failing loudly on a crash."""
    proc = _run(script, payload, home)
    assert proc.returncode == 0, (
        f"{script} exited {proc.returncode} on a standalone install\n"
        f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-4000:]}"
    )
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def landed(tmp_path_factory, bundle) -> tuple[Path, str]:
    """Ingest once; return (home, ingest stderr) for the whole module."""
    home = tmp_path_factory.mktemp("standalone_home")
    proc = _run("importer/scripts/ingest_snapshot.py", {"path": str(bundle)}, home)
    assert proc.returncode == 0, (
        f"ingest failed on a standalone install\nstderr: {proc.stderr[-4000:]}"
    )
    result = json.loads(proc.stdout)
    assert result["hosts"] == 2, result
    assert not (home / ".olav" / "workspace").exists(), (
        "this test is only meaningful without a deployed workspace"
    )
    return home, proc.stderr


def test_ingest_lands_without_a_workspace(landed):
    home, _ = landed
    db = home / ".olav" / "databases" / "main.duckdb"
    assert db.is_file(), "ingest did not create the database under OLAV_HOME"


def test_ingest_does_not_reach_for_an_embedding_backend(landed):
    """Defect 5: a writer with no reader, announced in WARNs.

    An install with no embedding backend has no AutoRecall to read what the
    priming would write, so the correct behaviour is to skip it silently. The
    check is on embedding-specific noise, not on stderr being empty — other
    warnings may legitimately appear.
    """
    _, stderr = landed
    noise = [
        line for line in stderr.splitlines()
        if "WARNING" in line
        and ("embed" in line.lower() or "dimension" in line.lower())
    ]
    assert not noise, "ingest tried to embed on an install with no backend:\n" + "\n".join(noise)


def test_cdp_becomes_topology_links(landed):
    """Defect 3: the config the ETL needs must come from the wheel."""
    import duckdb

    home, _ = landed
    con = duckdb.connect(
        str(home / ".olav" / "databases" / "main.duckdb"), read_only=True
    )
    try:
        links = con.execute("SELECT COUNT(*) FROM netops.topology_links").fetchone()[0]
    finally:
        con.close()
    assert links > 0, (
        "CDP output landed but produced no topology_links — discovery_protocols.yaml "
        "was not found, so the ETL was a no-op and every consumer reads this "
        "database as a network with no links"
    )


def test_query_topology_default_concept_does_not_raise(landed):
    """Defect 1: `concept="all"` is the default and must survive missing views."""
    home, _ = landed
    out = _ok("topology/scripts/query_topology.py", {}, home)
    assert out["snapshot_id"] != "none"
    assert isinstance(out["bgp_sessions"], list)
    assert "absent_layers" in out, (
        "an empty layer must say whether its command was collected"
    )


def test_absent_layer_is_named_not_silently_empty(landed):
    """Empty because nobody asked ≠ empty because there is nothing there."""
    home, _ = landed
    out = _ok("topology/scripts/query_topology.py", {"concept": "ospf"}, home)
    assert out["ospf_adjacencies"] == []
    assert "ospf" in out["absent_layers"], (
        "this bundle has no OSPF command; that has to be distinguishable from "
        "a network with no OSPF adjacencies"
    )


def test_inspect_interfaces_survives_a_single_vendor_collection(landed):
    """Defect 2: the Junos view is absent from a Cisco-only bundle."""
    home, _ = landed
    out = _ok("analyzer/scripts/inspect_interfaces.py", {"devices": []}, home)
    assert out["found"], "no interfaces returned from a bundle that carried them"
    absent = {s["command"] for s in out["absent_sources"]}
    assert "show interfaces terse" in absent, (
        "the source it could not read should be named, not omitted"
    )


def test_list_recipes_reports_an_unseeded_database(landed):
    """Defect 4: no `view_recipes` table is an answer, not an exception."""
    home, _ = landed
    out = _ok("topology/scripts/list_recipes.py", {}, home)
    assert out["recipes"] == []
    assert out["table_present"] is False


def test_rebuild_views_points_at_something_reachable(landed):
    """Its error must not send a pack user to a runtime command they lack."""
    home, _ = landed
    out = _ok("topology/scripts/rebuild_views.py", {}, home)
    assert out["status"] == "error"
    assert "netops_init" not in out["error"], (
        "a standalone install cannot run netops_init; name save_recipe or the "
        "ingest instead"
    )


@pytest.fixture(scope="module")
def own_home(tmp_path_factory, bundle) -> Path:
    """A second install, so writing recipes cannot affect the other tests."""
    home = tmp_path_factory.mktemp("recipe_home")
    proc = _run("importer/scripts/ingest_snapshot.py", {"path": str(bundle)}, home)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return home


def test_protocol_extension_path_needs_no_runtime(own_home):
    """prepare → draft → save → rebuild → query, with no endpoint and no seeding.

    The flow used to open with `discover_recipe`, which calls an OLAV LLM
    endpoint; a pack install has none, so the pack's own differentiator stopped
    at step 1. The drafting is now the reader's job — this test plays that part
    by building the mapping from the sample the script returns, which is exactly
    what the brief asks a model to do.
    """
    home = own_home

    prepared = _ok("topology/scripts/prepare_recipe.py",
                   {"protocol": "bgp", "vendor": "cisco_ios"}, home)
    assert prepared["ok"], prepared
    assert prepared["chosen_command"], "no command carried this protocol"
    assert prepared["instructions"], "no drafting brief for the model to work from"
    sample = prepared["sample"]

    # Map canonical names onto keys that exist in the sample — the one rule the
    # brief insists on. Reading them from the sample instead of hardcoding keeps
    # this test honest when parser field names change.
    wanted = {"neighbor_ip": ("bgp_neighbor", "neighbor_ip"),
              "neighbor_as": ("neighbor_as",),
              "local_as": ("local_as",)}
    mappings = {
        canonical: next(k for k in candidates if k in sample)
        for canonical, candidates in wanted.items()
        if any(k in sample for k in candidates)
    }
    assert len(mappings) >= 2, f"sample lacks usable fields: {sorted(sample)}"

    lines = [f"- command: {prepared['chosen_command']}",
             f"  concept: {prepared['concept']}",
             "  vendor_hint: cisco_ios",
             "  field_mappings:"]
    lines += [f"    {canonical}: {source}" for canonical, source in mappings.items()]
    recipe_yaml = "\n".join(lines) + "\n"

    saved = _ok("topology/scripts/save_recipe.py", {"recipe_yaml": recipe_yaml}, home)
    assert saved["ok"], saved
    assert saved["entries_written"] == 1

    built = _ok("topology/scripts/rebuild_views.py",
                {"concept": prepared["concept"]}, home)
    assert built["status"] == "ok", built
    view = f"netops.v_{prepared['concept']}_auto"
    assert built["views"].get(view, 0) > 0, built

    listed = _ok("topology/scripts/list_recipes.py", {}, home)
    assert listed["table_present"] is True, (
        "save_recipe must create view_recipes where nothing seeded it"
    )
    assert any(r["concept"] == prepared["concept"] for r in listed["recipes"]), listed

    import duckdb
    con = duckdb.connect(
        str(home / ".olav" / "databases" / "main.duckdb"), read_only=True
    )
    try:
        cols = [c[0] for c in con.execute(f"DESCRIBE {view}").fetchall()]
        rows = con.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
    finally:
        con.close()
    assert set(mappings) <= set(cols), (
        f"materialised view lost canonical names: {cols}"
    )
    assert {"device_name", "snapshot_id"} <= set(cols)
    assert rows > 0


def test_blast_radius_graph_knows_the_link(landed):
    """Defect 3, at the level a user would notice it.

    With no ``topology_links`` the graph has nodes and no edges, so every device
    is its own component and removing one "isolates" the rest — reported with an
    empty ``validation_warnings``, which is a confident wrong answer. The
    check is ``pre_components``: 1 means the two devices were connected before
    the removal, 2 means the graph never saw the link CDP described.
    """
    home, _ = landed
    devices = _ok("analyzer/scripts/inspect_devices.py", {"devices": []}, home)
    assert set(devices["found"]) >= {"R1", "R2"}, devices

    radius = _ok("analyzer/scripts/inspect_blast_radius.py",
                 {"remove_devices": ["R1"]}, home)
    assert radius["connectivity_loss"]["pre_components"] == 1, (
        "R1 and R2 are CDP neighbours, so before any removal they must be one "
        f"connected component: {radius['connectivity_loss']}"
    )


def test_sql_is_bounded_and_write_statements_are_not_executed(landed):
    home, _ = landed
    out = _ok('analyzer/scripts/execute_sql.py',
              {'sql': 'SELECT hostname FROM netops.devices ORDER BY hostname',
               'max_rows': 1}, home)
    assert len(out['data']) == 1
    assert out['has_more'] is True
    rejected = _ok('analyzer/scripts/execute_sql.py',
                   {'sql': 'DELETE FROM netops.devices'}, home)
    assert rejected['status'] == 'requires_approval'
    count = _ok('analyzer/scripts/execute_sql.py',
                {'sql': 'SELECT COUNT(*) AS n FROM netops.devices'}, home)
    assert count['data'] == [{'n': 2}]


def test_ingest_writes_report_artifact_in_bound_project(landed):
    home, _ = landed
    reports = list((home / 'exports/import_reports').glob('*.md'))
    assert reports
    content = reports[0].read_text()
    assert '**Source**: `bundle:olav-collector:0.0.0-fixture`' in content
    assert '**Devices landed**: 2' in content
    assert '**Command outputs landed**: 6' in content
    assert '**Not parsed**: 0' in content


def test_native_import_never_activates_embedding_even_with_provider_environment(tmp_path, bundle):
    """Provider libraries/configuration cannot enable unsupported model hooks."""
    code = '''import json, runpy, sys, types
from pathlib import Path
calls = []
def forbidden(*args, **kwargs):
    calls.append('model_or_memory_call')
    return object()
def ready(*args, **kwargs):
    calls.append('embedding_capability_probe')
    return True, 'fake provider available'
for name in ('openai', 'sentence_transformers', 'langchain_text_splitters',
             'olav_skills.core.embedder', 'olav_skills.core.memory',
             'olav_skills.core.memory.guide_kb', 'olav_skills.netops.core.memory_primer'):
    module = types.ModuleType(name)
    for attr in ('OpenAI', 'SentenceTransformer', 'RecursiveCharacterTextSplitter',
                 'get_store', 'SemanticCache', 'embed_text',
                 'prime_guides_from_dir', 'prime_memory_at_ingest'):
        setattr(module, attr, forbidden)
    module.embedding_backend_ready = ready
    sys.modules[name] = module
sys.argv = [sys.argv[1]]
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except SystemExit as exc:
    assert exc.code == 0, exc.code
assert calls == [], calls
'''
    env = {k: os.environ[k] for k in ('PATH', 'PYTHONPATH', 'SYSTEMROOT', 'LANG') if k in os.environ}
    env.update(OLAV_HOME=str(tmp_path), OLAV_EMBEDDING_MODE='api',
               OLAV_EMBEDDING_API_KEY='synthetic-test-key',
               OLAV_EMBEDDING_BASE_URL='http://127.0.0.1:1/synthetic-never-called',
               OPENAI_API_KEY='synthetic-test-key')
    proc = subprocess.run([sys.executable, '-c', code,
                           str(NETOPS_WS / 'importer/scripts/ingest_snapshot.py')],
                          input=json.dumps({'path': str(bundle)}), cwd=tmp_path,
                          env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result['hosts'] == 2 and result['commands'] == 6
