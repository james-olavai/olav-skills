"""Transactional closure for the explicitly configured, trusted local ingest CLI.

This is not an API identity service. All cooperating writers sharing one Unix
account/filesystem namespace serialize on one fixed host guard. DuckLake has no
UNIQUE constraint here; cross-host writers must not bypass this protocol.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
from pathlib import Path
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone, date
import uuid

from olav_skills.core.config import get_paths_config
from olav_skills.core.ingest.bundle_reader import BundleReader
from olav_skills.core.ingest.validators import validate_bundle
from olav_skills.core.ingest_commit_guard import commit_guard
from olav_skills.core.ingest_connection import open_ingest_connection


class ClosureError(ValueError):
    """A fixed code safe to return without database connection details."""

    def __init__(self, code, *, task_id=None):
        self.code, self.task_id = code, task_id
        super().__init__(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def _assert_domain():
    from olav_skills.core.pg_writer import PgWriterError
    from olav_skills.core.writer_host import assert_importer_entrypoint, WriterHostError
    try:
        assert_importer_entrypoint()
    except (WriterHostError, PgWriterError) as exc:
        raise ClosureError(exc.code) from None


@contextmanager
def writer_guard():
    from olav_skills.core.pg_writer import PgWriterError
    from olav_skills.core.writer_host import configured_writer_domain, WriterHostError
    try:
        with configured_writer_domain():
            _assert_domain()
            yield
    except (WriterHostError, PgWriterError) as exc:
        raise ClosureError(exc.code) from None


def time_evidence(value, source):
    result = {"value": None, "precision": "unknown", "status": "unknown", "source": None}
    if value is None or value == "":
        return result
    if not isinstance(value, str):
        return result | {"status": "invalid", "source": source}
    try:
        if len(value) == 10:
            date.fromisoformat(value)
            return dict(value=value, precision="date", status="known", source=source)
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        precision = "timestamp" if t.tzinfo else "timestamp_without_timezone"
        normalized = t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if t.tzinfo else t.isoformat()
        return dict(value=normalized, precision=precision, status="known", source=source)
    except ValueError:
        return result | {"status": "invalid", "source": source}


def collected_time(evidence):
    known = [v for v in evidence if v["status"] == "known"]
    invalid = any(v["status"] == "invalid" for v in evidence)
    if not known:
        return time_evidence(None, None) | ({"status": "invalid"} if invalid else {})
    # Dates/naive clocks cannot be silently equated with aware instants.
    if len({(v["value"], v["precision"]) for v in known}) > 1:
        return dict(value=None, precision="unknown", status="conflict", source="capture_evidence")
    return known[0] | ({"status": "partial"} if invalid else {})


def prepare_seeds():
    _assert_domain()
    from olav_skills.netops.core.commands_sync import _deploy_seed_templates
    agent = Path(get_paths_config().agent_dir_path)
    _deploy_seed_templates(agent)
    # Re-running is idempotent; a copy failure must not become a different
    # parser selection on the next ingest.
    seeds = agent / "workspace/netops/netops_init/seed_templates"
    if seeds.exists():
        for path in seeds.glob("*/*.textfsm"):
            if not (agent / "templates" / path.relative_to(seeds)).is_file():
                raise ClosureError("SEED_PREPARATION_FAILED")


def parser_identity():
    # Explicit imports also make the official standalone closure discoverable.
    import olav_skills.netops.core.parser_registry
    import olav_skills.netops.core.parser_contract
    import olav_skills.core.ingest.platform_discovery
    import olav_skills.core.ingest.schema
    import olav_skills.core.redaction
    import ntc_templates
    from olav_skills.netops.tools.textfsm_parse import _templates_base
    files = {}
    for label, base in (("active", _templates_base()),
                        ("ntc", Path(ntc_templates.__file__).parent / "templates")):
        if base.exists():
            for path in sorted(base.rglob("*")):
                rel = path.relative_to(base)
                if "_incoming" in rel.parts or "__pycache__" in rel.parts:
                    continue
                if path.is_file() and (path.suffix in {".textfsm", ".py", ".json", ".yaml"} or path.name == "index"):
                    files[f"{label}/{rel.as_posix()}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    modules = ("olav_skills.netops.tools.textfsm_parse", "olav_skills.netops.tools.field_normalizer",
               "olav_skills.netops.core.platform_canonical", "olav_skills.netops.core.parser_registry",
               "olav_skills.netops.core.parser_contract", "olav_skills.netops.core.commands_sync",
               "olav_skills.core.ingest.platform_discovery", "olav_skills.core.ingest.bundle_reader",
               "olav_skills.core.ingest.schema", "olav_skills.core.redaction",
               "olav_skills.netops.core.ingest.landing")
    for name in modules:
        path = Path(importlib.import_module(name).__file__)
        files[f"module/{name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    versions = {}
    for name in ("ntc-templates", "textfsm", "netutils", "netconan"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "missing"
    value = {"files": files, "versions": versions}
    return value | {"fingerprint": digest(value)}


def input_identity(path, collection_source, host_platforms):
    import yaml
    report = validate_bundle(path)
    if not report.ok:
        raise ClosureError("INVALID_BUNDLE")
    reader = BundleReader.open(path)
    manifest = reader.manifest.model_dump(mode="json")
    source = collection_source or f"bundle:{reader.manifest.collector.name}:{reader.manifest.collector.version}"
    metas = {}
    for host in sorted((path / "devices").iterdir()):
        if host.is_dir():
            meta = host / "_meta.yaml"
            metas[host.name] = yaml.safe_load(meta.read_text()) if meta.exists() else {"status": "missing"}
    from olav_skills.core.ingest.schema import CommandFile
    originals = {}
    for file in sorted((path / "devices").glob("*/*.txt")):
        cf = CommandFile.from_text(file.read_text(encoding="utf-8", errors="replace"), filename_hint=file.name)
        originals[(file.parent.name, cf.command)] = {"input_artifact": file.relative_to(path).as_posix(),
            "input_sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    records = []
    for rec in reader.iter_command_outputs():
        record_metadata = dataclasses.asdict(rec)
        record_metadata.pop("body")
        evidence = [time_evidence(rec.collected_at_iso, "command_header"),
                    time_evidence((metas.get(rec.host) or {}).get("collected_at"), "device_meta"),
                    time_evidence(manifest.get("collected_at"), "manifest")]
        records.append(dict(device_name=rec.host, command=rec.command, platform=rec.platform,
                            body_sha256=hashlib.sha256(rec.body.encode()).hexdigest(),
                            capture_evidence=evidence, collected_at=collected_time(evidence),
                            record_metadata=record_metadata, **originals[(rec.host, rec.command)]))
    records.sort(key=lambda item: (item["device_name"], item["command"], canonical(item)))
    keys = [(r["device_name"], r["command"]) for r in records]
    if len(set(keys)) != len(keys):
        raise ClosureError("DUPLICATE_COMMAND_KEY")
    value = dict(manifest=manifest, devices=metas, records=records,
                 collection_source=source, host_platforms=host_platforms or {})
    def remove_checksums(obj):
        if isinstance(obj, dict):
            return {k: remove_checksums(v) for k, v in obj.items()
                    if not any(part in k.lower() for part in ("sha", "checksum", "digest", "signature"))}
        if isinstance(obj, list):
            return [remove_checksums(v) for v in obj]
        return obj
    capture_manifest = remove_checksums(manifest)
    for key in ("hosts_collected", "hosts_failed", "templates"):
        capture_manifest.pop(key, None)
    for record in records:
        capture = dict(manifest=capture_manifest, device_meta=remove_checksums(metas.get(record["device_name"])),
                       record_metadata=record["record_metadata"], collection_source=source,
                       host_platform=(host_platforms or {}).get(record["device_name"]))
        record["observation_key"] = digest(capture) if any(e["status"] == "known" for e in record["capture_evidence"]) else None
    fingerprint = digest(value)
    # Content checksums and signatures are payload, not capture identity.
    observation = json.loads(canonical(value))
    for key in list(observation["manifest"]):
        if "sha" in key.lower() or "checksum" in key.lower() or key == "signature" or key == "templates":
            del observation["manifest"][key]
    for rec in observation["records"]:
        rec.pop("body_sha256")
        rec.pop("input_sha256")
        rec.pop("input_artifact")
    observation = remove_checksums(observation)
    known_capture = any(r["collected_at"]["status"] in {"known", "partial"} for r in records)
    observation_key = digest(observation) if known_capture else fingerprint
    return value | dict(fingerprint=fingerprint, observation_key=observation_key)


def ensure_ledger(conn):
    _assert_domain()
    # Idempotent schema DDL, executed in the managed business path. It carries no
    # business facts, but it is still a write under implicit autocommit, so it runs
    # under the same host guard rather than being claimed as a no-op setup boundary.
    with commit_guard():
        conn.execute("CREATE SCHEMA IF NOT EXISTS netops")
        conn.execute("""CREATE TABLE IF NOT EXISTS netops.ingest_operations (
            idempotency_key VARCHAR, request_sha256 VARCHAR, task_id VARCHAR,
            state VARCHAR, stage VARCHAR, source_fingerprint VARCHAR, parser_fingerprint VARCHAR,
            observation_key VARCHAR, snapshot_id VARCHAR, bundle_id VARCHAR, commit_token VARCHAR,
            result_json VARCHAR, postprocess_json VARCHAR, accepted_at VARCHAR, committed_at VARCHAR,
            pending_path VARCHAR)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS netops.ingest_sources (
            snapshot_id VARCHAR, device_name VARCHAR, command VARCHAR, source_json VARCHAR,
            raw_output VARCHAR, parser_json VARCHAR, parsed_at VARCHAR, ingested_at VARCHAR,
            task_id VARCHAR, commit_token VARCHAR)""")
        # No UNIQUE constraint: DuckLake raises NotImplementedException for table constraints
        # (clean3 rehearsal 2026-11-12); uniqueness is enforced by _withdraw_operation under the writer lock.
        conn.execute("""CREATE TABLE IF NOT EXISTS netops.withdraw_operations (
            idempotency_key VARCHAR, snapshot_id VARCHAR, reason VARCHAR,
            state VARCHAR, stage VARCHAR, started_at VARCHAR, finished_at VARCHAR,
            withdrawn_counts VARCHAR, error VARCHAR)""")


def operation(conn, key):
    rows = conn.execute("SELECT * FROM netops.ingest_operations WHERE idempotency_key=?", [key]).fetchall()
    if len(rows) > 1:
        raise ClosureError("LEDGER_CORRUPT")
    if not rows:
        return None
    cols = [r[0] for r in conn.description]
    return dict(zip(cols, rows[0]))


def update(conn, task_id, **fields):
    _assert_domain()
    conn.execute("UPDATE netops.ingest_operations SET " + ",".join('"'+k+'"=?' for k in fields) + " WHERE task_id=?",
                 [*fields.values(), task_id])


def transaction(conn, action):
    _assert_domain()
    with commit_guard() as sh:
        conn.execute("BEGIN")
        try:
            value = action()
            _assert_domain()
            sh.check()
            conn.execute("COMMIT")
            return value
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise


def response(op, *, replayed=True):
    if op["state"] in {"accepted", "running", "uncertain"}:
        raise ClosureError("RESULT_UNCERTAIN", task_id=op["task_id"])
    result = json.loads(op["result_json"]) if op["result_json"] else {}
    if op.get("_data_missing"):
        result = result | {"error": {"code": "DATA_MISSING", "stage": "reconcile"}, "operator_required": True}
    if op["state"] != "succeeded":
        return result | {"status": "task_state", "task": {"state": op["state"]}, "task_id": op["task_id"], "idempotency_replayed": replayed}
    return result | {"postprocess": json.loads(op["postprocess_json"]), "idempotency_replayed": replayed}


def _legacy_reconcile_operation(op):
    """New connection, held host guard: atomic token+facts can be reconciled."""
    _assert_domain()
    with open_ingest_connection() as fresh:
        current = operation(fresh, op["idempotency_key"])
        if current is None or current["task_id"] != op["task_id"]:
            raise ClosureError("RESULT_UNCERTAIN", task_id=op["task_id"])
        if current["state"] == "succeeded" and current["commit_token"]:
            return current
        nsource = fresh.execute("SELECT count(*) FROM netops.ingest_sources WHERE task_id=? OR snapshot_id=?", [op["task_id"], op["snapshot_id"]]).fetchone()[0]
        exists = fresh.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='netops' AND table_name='bundle_ingests'").fetchone()
        nbundle = fresh.execute("SELECT count(*) FROM netops.bundle_ingests WHERE snapshot_id=?", [op["snapshot_id"]]).fetchone()[0] if exists else 0
        other_facts = 0
        for table in ("raw_output_store", "parsed_outputs", "devices", "topology_links", "oc_outputs"):
            exists = fresh.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='netops' AND table_name=? AND column_name='snapshot_id'", [table]).fetchone()
            if exists:
                other_facts += fresh.execute('SELECT count(*) FROM netops."' + table + '" WHERE snapshot_id=?', [op["snapshot_id"]]).fetchone()[0]
        # No active cooperative transaction can survive acquisition of our
        # guard. In this protocol success/token/sources/facts commit together.
        if not nsource and not nbundle and not other_facts and not current["commit_token"]:
            result = dict(status="failed", task_id=op["task_id"], counts=zero_counts(),
                          error={"code": "INTERRUPTED_UNCOMMITTED", "stage": "reconcile"})
            transaction(fresh, lambda: update(fresh, op["task_id"], state="failed", stage="reconciled_uncommitted", result_json=canonical(result)))
            return operation(fresh, op["idempotency_key"])
        raise ClosureError("RESULT_UNCERTAIN", task_id=op["task_id"])


def _v3():
    from olav_skills.core.ingest_connection import _host_config
    return (_host_config() or {}).get("version") == 2


def _expected_fact_tables():
    """Trusted immutable bootstrap declaration; never infer absence as disabled."""
    try:
        home = os.environ.get("OLAV_HOME")
        if not home:
            return None
        path = Path(home) / ".olav/config/ingest-facts.json"
        if not path.is_absolute() or path.resolve() != path or not path.is_file() or path.stat().st_size > 16384:
            return None
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError()
                value[key] = item
            return value
        value = json.loads(path.read_text(), object_pairs_hook=pairs)
        if (type(value) is not dict or set(value) != {"version", "snapshot_tables"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["snapshot_tables"]) is not list):
            return None
        tables = value["snapshot_tables"]
        allowed = {"raw_output_store", "parsed_outputs", "devices", "topology_links", "oc_outputs"}
        if any(type(table) is not str or table not in allowed for table in tables) or len(set(tables)) != len(tables):
            return None
        return tuple(tables)
    except Exception:
        return None


def _facts(conn, op):
    """An unavailable identity/table/query is distinct from a successful zero."""
    sid = op.get("snapshot_id")
    if not isinstance(sid, str) or not re.fullmatch(r"snap_[0-9a-f]{32}", sid) or not op.get("task_id"):
        return "unknown"
    try:
        expected = _expected_fact_tables()
        if expected is None:
            return "unknown"
        # The closure stores are mandatory. Domain tables are declared by the
        # immutable bootstrap profile, not discovered from possibly lost data.
        total = conn.execute("SELECT count(*) FROM netops.ingest_sources WHERE task_id=? OR snapshot_id=?",
                             [op["task_id"], sid]).fetchone()[0]
        total += conn.execute("SELECT count(*) FROM netops.bundle_ingests WHERE snapshot_id=?", [sid]).fetchone()[0]
        for table in expected:
            present = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='netops' AND table_name=? AND column_name='snapshot_id'", [table]).fetchone()
            if not present:
                return "unknown"
            total += conn.execute('SELECT count(*) FROM netops."' + table + '" WHERE snapshot_id=?', [sid]).fetchone()[0]
        return "yes" if total else "no"
    except Exception:
        return "unknown"


def reconcile_operation(op):
    if not _v3():
        return _legacy_reconcile_operation(op)
    _assert_domain()
    with open_ingest_connection() as fresh:
        current = operation(fresh, op["idempotency_key"])
        if current is None or any(current.get(k) != op.get(k) for k in ("task_id", "snapshot_id", "commit_token", "state")):
            raise ClosureError("LEDGER_CORRUPT", task_id=op.get("task_id"))
        if current["state"] == "failed":
            return current
        facts = _facts(fresh, current)
        if facts == "unknown":
            raise ClosureError("LEDGER_CORRUPT", task_id=current["task_id"])
        if current["state"] == "succeeded":
            if not current["commit_token"]:
                raise ClosureError("LEDGER_CORRUPT", task_id=current["task_id"])
            if facts == "no":
                current = dict(current, _data_missing=True)
            return current
        if current["state"] not in {"accepted", "running", "uncertain"} or current["commit_token"] or facts != "no":
            raise ClosureError("LEDGER_CORRUPT", task_id=current["task_id"])
        result = dict(status="failed", task_id=current["task_id"], counts=zero_counts(),
                      error={"code": "INTERRUPTED_UNCOMMITTED", "stage": "reconcile"})
        transaction(fresh, lambda: update(fresh, current["task_id"], state="failed", stage="reconciled_uncommitted", result_json=canonical(result)))
        return operation(fresh, current["idempotency_key"])


def zero_counts():
    return dict(raw_inserted=0, raw_updated=0, parsed_inserted=0, snapshots=0)


def publish(conn, op):
    _assert_domain()
    result = json.loads(op["result_json"])
    if result.get("origin_task_id"):
        keys = conn.execute("SELECT idempotency_key FROM netops.ingest_operations WHERE task_id=?", [result["origin_task_id"]]).fetchall()
        if len(keys) != 1:
            raise ClosureError("LEDGER_CORRUPT")
        origin = publish(conn, operation(conn, keys[0][0]))
        result["report_path"] = json.loads(origin["result_json"])["report_path"]
        transaction(conn, lambda: update(conn, op["task_id"], postprocess_json=origin["postprocess_json"], result_json=canonical(result)))
        return operation(conn, op["idempotency_key"])
    post = json.loads(op["postprocess_json"])
    root = Path(get_paths_config().project_root) / "exports/ingest_artifacts" / op["task_id"]
    pending = Path(op["pending_path"])
    failures = []
    with commit_guard():
        for item in post["items"]:
            if item["state"] == "succeeded":
                continue
            try:
                dest = root / item["logical_path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                source = pending / item["logical_path"]
                if dest.exists():
                    if not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest() != item["sha256"]:
                        raise ClosureError("ARTIFACT_CONFLICT")
                else:
                    data = source.read_bytes()
                    if hashlib.sha256(data).hexdigest() != item["sha256"]:
                        raise ClosureError("ARTIFACT_SOURCE_CHANGED")
                    tmp = dest.with_name(dest.name + ".pending-" + op["task_id"])
                    with tmp.open("wb") as fh:
                        fh.write(data)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, dest)
                item["state"], item["path"] = "succeeded", str(dest)
                item.pop("error", None)
            except Exception as exc:
                item["state"], item["error"] = "failed", type(exc).__name__
                item.pop("path", None)
                failures.append(item["logical_path"])
    post["state"] = "failed" if failures else "succeeded"
    result = json.loads(op["result_json"])
    reports = [i for i in post["items"] if i["logical_path"].startswith("import_reports/") and i["state"] == "succeeded"]
    result["report_path"] = reports[0]["path"] if reports else None
    transaction(conn, lambda: update(conn, op["task_id"], postprocess_json=canonical(post), result_json=canonical(result)))
    return operation(conn, op["idempotency_key"])


# One ingest batch is ~10k source rows, so chunked multi-row VALUES stay
# inside DuckDB's in-memory and parameter limits (see ingest_write.py).
_PREFLIGHT_CHUNK = 500


def _precheck_key_conflicts(conn, wanted):
    """Reject this bundle if any existing observation_key already carries a
    different body_sha256, without materializing the full ingest_sources
    history into Python (OW-131).

    ``wanted`` maps this bundle's observation_key -> body_sha256. It is staged
    as a chunked TEMP relation and joined once against netops.ingest_sources on
    json_extract_string(source_json, '$.observation_key'); only a single
    mismatched row is ever fetched (LIMIT 1), so memory and runtime stop
    scaling with the whole history.
    """
    if not wanted:
        return
    tmp = "_ow131_wanted_" + uuid.uuid4().hex[:8]
    conn.execute("CREATE TEMP TABLE " + tmp + " (observation_key VARCHAR, body_sha256 VARCHAR)")
    try:
        pairs = list(wanted.items())
        for start in range(0, len(pairs), _PREFLIGHT_CHUNK):
            chunk = pairs[start:start + _PREFLIGHT_CHUNK]
            rows_sql = ",".join("(?,?)" for _ in chunk)
            params = [value for pair in chunk for value in pair]
            conn.execute("INSERT INTO " + tmp + " VALUES " + rows_sql, params)
        conflict = conn.execute(
            "SELECT 1 FROM netops.ingest_sources AS s "
            "JOIN " + tmp + " AS w "
            "ON json_extract_string(s.source_json, '$.observation_key') = w.observation_key "
            "AND json_extract_string(s.source_json, '$.body_sha256') != w.body_sha256 "
            "LIMIT 1"
        ).fetchone()
        if conflict:
            raise ClosureError("IMPORT_KEY_CONFLICT")
    finally:
        conn.execute("DROP TABLE IF EXISTS " + tmp)


_SCRATCH_COPY_DIRS = ("input", "snapshots")
_BUSINESS_TASK_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _symlink_free(path):
    """Fail-closed: True only when ``path`` is absolute and no ancestor is a symlink."""
    try:
        if not path.is_absolute():
            return False
        return not any(p.is_symlink() for p in (path, *path.parents))
    except OSError:
        return False


def cleanup_scratch_copies(task_id, *, apply=True):
    """Single authority for this attempt's own ``input``/``snapshots`` copies
    under the canonical ``<agent_dir>/ingest_pending`` root.

    Validates the live writer domain, the task UUID, the canonical root
    (absolute, no symlink ancestors), the pending dir and per-copy safety (no
    symlink target or in-tree link). ``apply`` removes only the fixed
    ``input``/``snapshots`` allowlist, re-verifying the domain immediately
    before each removal (after the in-tree traversal); ``apply=False`` only
    inspects. Never touches the parent dir or the postprocess sources
    (import_reports/backup/templates_review).

    Returns ``{'task_id', 'root', 'refused', 'copies'}``. Each copy is
    ``{'name', 'status', 'error'}`` with status in
    {'removed', 'present', 'absent', 'refused', 'error'}; 'present' is only
    produced on dry-run ('removed' means it would be removed). A top-level
    'refused' reason covers invalid task id, unresolvable/unsafe root, missing
    task dir or a domain lost before any work.
    """
    result = {'task_id': task_id, 'root': None, 'refused': None, 'copies': []}
    try:
        _assert_domain()
    except ClosureError:
        result['refused'] = 'DOMAIN_LOST'
        return result
    if not isinstance(task_id, str) or not _BUSINESS_TASK_ID.fullmatch(task_id):
        result['refused'] = 'INVALID_TASK_ID'
        return result
    try:
        root = Path(get_paths_config().agent_dir_path) / "ingest_pending"
    except Exception:
        result['refused'] = 'ROOT_UNRESOLVABLE'
        return result
    result['root'] = str(root)
    if not _symlink_free(root) or not root.is_dir():
        result['refused'] = 'UNSAFE_ROOT'
        return result
    pending = root / task_id
    if pending.is_symlink():
        result['refused'] = 'UNSAFE_PENDING'
        return result
    if not pending.is_dir():
        result['refused'] = 'TASK_DIR_MISSING'
        return result
    for name in _SCRATCH_COPY_DIRS:
        child = pending / name
        entry = {'name': name, 'status': None, 'error': None}
        try:
            if child.is_symlink():
                entry['status'] = 'refused'; entry['error'] = 'UNSAFE_COPY'
            elif not child.exists():
                entry['status'] = 'absent'
            elif not child.is_dir():
                entry['status'] = 'refused'; entry['error'] = 'NOT_A_DIRECTORY'
            elif any(p.is_symlink() for p in child.rglob("*")):
                entry['status'] = 'refused'; entry['error'] = 'TREE_SYMLINK'
            elif not apply:
                entry['status'] = 'present'
            else:
                # Long traversal is done; re-verify the live domain immediately
                # before the actual removal, never with a stale domain state.
                try:
                    _assert_domain()
                except ClosureError:
                    entry['status'] = 'refused'; entry['error'] = 'DOMAIN_LOST'
                    result['refused'] = 'DOMAIN_LOST'
                    result['copies'].append(entry)
                    return result
                try:
                    shutil.rmtree(child)
                    entry['status'] = 'removed'
                except OSError as exc:
                    entry['status'] = 'error'; entry['error'] = type(exc).__name__
        except OSError:
            entry['status'] = 'error'; entry['error'] = 'STAT_FAILED'
        result['copies'].append(entry)
    return result


def ingest(path, *, idempotency_key, collection_source=None, host_platforms=None,
           retry_postprocess=False, reconcile=False, observed_snapshot_id=None):
    if (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128
            or any(ord(c) < 33 or ord(c) > 126 for c in idempotency_key)):
        raise ClosureError("INVALID_IDEMPOTENCY_KEY")
    if type(retry_postprocess) is not bool or type(reconcile) is not bool:
        raise ClosureError("INVALID_CONTROL_ACTION")
    if host_platforms is not None and (not isinstance(host_platforms, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in host_platforms.items())):
        raise ClosureError("INVALID_HOST_PLATFORMS")
    if collection_source is not None and not isinstance(collection_source, str):
        raise ClosureError("INVALID_COLLECTION_SOURCE")
    if observed_snapshot_id is not None and (not reconcile or not isinstance(observed_snapshot_id, str) or not re.fullmatch(r"snap_[0-9a-f]{32}", observed_snapshot_id)):
        raise ClosureError("INVALID_CONTROL_ACTION")
    with writer_guard():
        # Control actions use the original immutable operation and artifacts;
        # the caller's old path may have disappeared or changed entirely.
        if retry_postprocess or reconcile:
            if collection_source is not None or host_platforms is not None:
                raise ClosureError("CONTROL_BUSINESS_OVERRIDE_FORBIDDEN")
            with open_ingest_connection() as conn:
                ensure_ledger(conn)
                old = operation(conn, idempotency_key)
                if old is None:
                    raise ClosureError("LEDGER_CORRUPT" if reconcile and _v3() else "OPERATION_NOT_FOUND")
                if reconcile and (_v3() or old["state"] in {"accepted", "running", "uncertain"}):
                    old = reconcile_operation(old)
                if retry_postprocess and old["state"] == "succeeded":
                    old = publish(conn, old)
                return response(old)
        _assert_domain()
        prepare_seeds()
        parser = parser_identity()
        task_id = str(uuid.uuid4())
        pending = Path(get_paths_config().agent_dir_path) / "ingest_pending" / task_id
        pending.mkdir(parents=True, mode=0o700)
        preserve = False
        try:
            source_path = Path(path)
            if not source_path.is_dir() or any(p.is_symlink() for p in source_path.rglob("*")):
                raise ClosureError("DIRECTORY_BUNDLE_REQUIRED")
            shutil.copytree(source_path, pending / "input")
            source = input_identity(pending / "input", collection_source, host_platforms)
            request_sha = digest({"source": source["fingerprint"], "parser": parser["fingerprint"]})
            with open_ingest_connection() as conn:
                ensure_ledger(conn)
                old = operation(conn, idempotency_key)
                if old:
                    if old["request_sha256"] != request_sha:
                        raise ClosureError("IDEMPOTENCY_CONFLICT", task_id=old["task_id"])
                    if old["state"] in {"accepted", "running", "uncertain"}:
                        if reconcile:
                            old = reconcile_operation(old)
                        else:
                            transaction(conn, lambda: update(conn, old["task_id"], state="uncertain"))
                    if retry_postprocess and old["state"] == "succeeded":
                        old = publish(conn, old)
                    return response(old)
                # Conservatively stop this warehouse while any earlier commit is
                # unresolved; repacking/changing parser cannot bypass uncertainty.
                blocked = conn.execute("SELECT task_id FROM netops.ingest_operations WHERE state IN ('accepted','running','uncertain')").fetchall()
                if blocked:
                    raise ClosureError("RESULT_UNCERTAIN", task_id=blocked[0][0])
                if reconcile or retry_postprocess:
                    raise ClosureError("OPERATION_NOT_FOUND")
                # Known per-record observations survive repacking, omitted commands,
                # and unrelated devices; a whole-bundle key alone is insufficient.
                wanted = {r["observation_key"]: r["body_sha256"] for r in source["records"] if r["observation_key"]}
                _precheck_key_conflicts(conn, wanted)
                succeeded = conn.execute("SELECT idempotency_key FROM netops.ingest_operations WHERE state='succeeded' AND source_fingerprint=? AND parser_fingerprint=? ORDER BY accepted_at LIMIT 1", [source["fingerprint"], parser["fingerprint"]]).fetchone()
                sid = "snap_" + task_id.replace("-", "")
                bundle_id, token = str(uuid.uuid4()), str(uuid.uuid4())
                # First possible persistence of this attempt's accepted row. From
                # here the scratch is possibly owned; release the cleanup guard
                # only on a deterministic terminal outcome.
                preserve = True
                transaction(conn, lambda: conn.execute("INSERT INTO netops.ingest_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [idempotency_key, request_sha, task_id, "accepted", "accepted", source["fingerprint"], parser["fingerprint"], source["observation_key"], sid, bundle_id, None, None, None, now(), None, str(pending)]))
                if succeeded:
                    original = operation(conn, succeeded[0])
                    result = json.loads(original["result_json"]) | dict(task_id=task_id, counts=zero_counts(), deduplicated=True, origin_task_id=original["task_id"], parser_executed=False)
                    result["write_counts"] = {k: 0 for k in result.get("write_counts", {})}
                    transaction(conn, lambda: update(conn, task_id, state="succeeded", stage="deduplicated", snapshot_id=original["snapshot_id"], bundle_id=original["bundle_id"], commit_token=original["commit_token"], result_json=canonical(result), postprocess_json=original["postprocess_json"], pending_path=original["pending_path"], committed_at=now()))
                    preserve = False
                    return response(operation(conn, idempotency_key), replayed=False)
                committed = False
                commit_attempted = False
                try:
                    _assert_domain()
                    conn.execute("BEGIN")
                    update(conn, task_id, state="running", stage="landing")
                    from olav_skills.netops.core.ingest.landing import ingest_snapshot
                    context = {"bundle_id": bundle_id}
                    landed = ingest_snapshot(pending / "input", db_path=None,
                        staging_dir=pending / "snapshots/json", collection_source=source["collection_source"],
                        host_platforms=host_platforms, conn=conn, snapshot_id=sid, closure_context=context)
                    if parser_identity()["fingerprint"] != parser["fingerprint"]:
                        raise ClosureError("PARSER_CHANGED")
                    records = {(r["device_name"], r["command"]): r for r in source["records"]}
                    stamp = now()
                    for record in context["records"]:
                        if record["parse_status"] == "parsed" and record["parser"] is None:
                            raise ClosureError("PARSER_TRACE_MISSING")
                        provenance = records[(record["device_name"], record["command"])] | {
                            "source_fingerprint": source["fingerprint"], "parser_fingerprint": parser["fingerprint"],
                            "actual_raw_sha256": hashlib.sha256(record["raw_output"].encode()).hexdigest(),
                            "raw_artifact": {"table": "netops.ingest_sources", "snapshot_id": sid,
                                             "device_name": record["device_name"], "command": record["command"]},
                            "collection_source": source["collection_source"], "manifest": source["manifest"],
                            "device_meta": source["devices"].get(record["device_name"]),
                            "parse_status": record["parse_status"], "parser_versions": parser["versions"]}
                        conn.execute("INSERT INTO netops.ingest_sources VALUES (?,?,?,?,?,?,?,?,?,?)", [sid, record["device_name"], record["command"], canonical(provenance), record["raw_output"], canonical(record["parser"]), record["parsed_at"], stamp, task_id, token])
                    from olav_skills.core.ingest_manager import _load_backup_commands
                    backup_commands = _load_backup_commands(conn=conn)
                    for record in context["records"]:
                        if record["command"] in backup_commands:
                            filename = digest([record["device_name"], record["command"]]) + ".txt"
                            dest = pending / "backup" / filename
                            dest.parent.mkdir(exist_ok=True)
                            dest.write_text(record["raw_output"], encoding="utf-8")
                    templates = pending / "input/templates"
                    if templates.exists():
                        shutil.copytree(templates, pending / "templates_review")
                    items = []
                    for folder in ("import_reports", "backup", "templates_review"):
                        for file in sorted((pending / folder).rglob("*")):
                            if file.is_file():
                                items.append(dict(logical_path=file.relative_to(pending).as_posix(), sha256=hashlib.sha256(file.read_bytes()).hexdigest(), state="pending"))
                    post = dict(state="pending", items=items, audit={"state": "not_configured"}, hooks={"state": "not_configured"}, memory={"state": "deferred"}, semantic_cache={"state": "deferred"})
                    counts = {k: landed.write_counts[k] for k in ("raw_inserted", "raw_updated", "parsed_inserted")} | {"snapshots": 1}
                    result = dataclasses.asdict(landed) | dict(status="succeeded", task_id=task_id, commit_token=token, counts=counts, deduplicated=False, report_path=None,
                        backend="ducklake", db_path=None, parser_executed=True, source_manifest={"fingerprint": source["fingerprint"], "parser_fingerprint": parser["fingerprint"], "parser_versions": parser["versions"], "records": len(records), "collected_at": time_evidence(source["manifest"].get("collected_at"), "manifest")})
                    with commit_guard() as sh:
                        update(conn, task_id, state="succeeded", stage="committed", commit_token=token, result_json=canonical(result), postprocess_json=canonical(post), committed_at=stamp)
                        _assert_domain()
                        sh.check()
                        commit_attempted = True
                        conn.execute("COMMIT")
                        committed = True
                except BaseException as exc:
                    if committed:
                        # COMMIT returned before this exception; only the guard's
                        # __exit__ (SH release) is in flight. The business facts are
                        # durably committed; never overwrite them with a failed row
                        # or claim the commit is unknown.
                        raise ClosureError("RESULT_UNCERTAIN", task_id=task_id) from None
                    if commit_attempted and _v3():
                        # Never write a failed ledger through an engine reconnect
                        # after an ambiguous COMMIT. The next acquire must fence.
                        raise ClosureError("RESULT_UNCERTAIN", task_id=task_id) from None
                    try:
                        conn.execute("ROLLBACK")
                        result = dict(status="failed", task_id=task_id, counts=zero_counts(), error={"code": exc.code if isinstance(exc, ClosureError) else "INGEST_FAILED", "stage": "landing", "type": type(exc).__name__})
                        transaction(conn, lambda: update(conn, task_id, state="failed", stage="rolled_back", result_json=canonical(result)))
                    except Exception:
                        # COMMIT response loss or connection loss: never declare
                        # uncommitted using an unreadable/aborted connection.
                        raise ClosureError("RESULT_UNCERTAIN", task_id=task_id) from None
                    preserve = False
                    return result | {"idempotency_replayed": False}
                if committed:
                    op = operation(conn, idempotency_key)
                    if op is None or op.get("task_id") != task_id:
                        # Our own commit left no readable row (or an identity
                        # mismatch): never release the scratch on that evidence.
                        raise ClosureError("RESULT_UNCERTAIN", task_id=task_id)
                    try:
                        op = publish(conn, op)
                        projected = response(op, replayed=False)
                    except Exception:
                        # Business commit survives a postprocess state-write
                        # failure. Only release the scratch once this task can be
                        # legitimately projected as an authoritative terminal.
                        try:
                            projected = response(op, replayed=False) | {"postprocess": {"state": "uncertain"}}
                        except Exception:
                            # None / bad result_json: preserve the scratch and
                            # surface the existing uncertain/ledger error.
                            raise ClosureError("RESULT_UNCERTAIN", task_id=task_id)
                    preserve = False
                    return projected
        finally:
            if not preserve:
                cleanup_scratch_copies(task_id)


# ── Withdraw (retract) one committed snapshot ──────────────────────────────


def zero_withdraw_counts():
    return {"ingest_operations": 0, "ingest_sources": 0, "parsed_outputs": 0, "oc_outputs": 0,
            "bundle_ingests": 0, "raw_output_store": 0, "topology_links": 0,
            "devices": 0}


def _table_exists(conn, schema, table):
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=? AND table_name=?",
        [schema, table]).fetchone() is not None


def _withdraw_operation(conn, key):
    rows = conn.execute(
        "SELECT * FROM netops.withdraw_operations WHERE idempotency_key=?", [key]
    ).fetchall()
    if len(rows) > 1:
        raise ClosureError("LEDGER_CORRUPT")
    if not rows:
        return None
    cols = [r[0] for r in conn.description]
    return dict(zip(cols, rows[0]))


def _withdraw_update(conn, key, **fields):
    _assert_domain()
    conn.execute(
        "UPDATE netops.withdraw_operations SET " + ",".join('"' + k + '"=?' for k in fields)
        + " WHERE idempotency_key=?",
        [*fields.values(), key],
    )


def _withdraw_insert(conn, key, snapshot_id, reason):
    _assert_domain()
    conn.execute(
        "INSERT INTO netops.withdraw_operations VALUES (?,?,?,?,?,?,NULL,NULL,NULL)",
        [key, snapshot_id, reason, "accepted", "accepted", now()],
    )


def _withdraw_result(op):
    business_state = op["state"] if op["state"] in ("succeeded", "failed") else "uncertain"
    result = {"business_state": business_state, "snapshot_id": op["snapshot_id"]}
    if op["withdrawn_counts"]:
        result["withdrawn_counts"] = json.loads(op["withdrawn_counts"])
    if op["error"]:
        result["error"] = json.loads(op["error"])
    return result


def _snapshot_exists(conn, snapshot_id):
    for table in ("ingest_sources", "parsed_outputs", "bundle_ingests"):
        if _table_exists(conn, "netops", table):
            if conn.execute(
                f"SELECT 1 FROM netops.{table} WHERE snapshot_id=? LIMIT 1",
                [snapshot_id],
            ).fetchone():
                return True
    return False


def _delete_where(conn, table, column, value):
    n = conn.execute(f"SELECT count(*) FROM {table} WHERE {column}=?", [value]).fetchone()[0]
    if n:
        conn.execute(f"DELETE FROM {table} WHERE {column}=?", [value])
    return n


def _in_clause(values):
    return "(" + ",".join("?" for _ in values) + ")"


def _recompute_raw_output_store(conn, snapshot_ids):
    snapshot_ids = [snapshot_ids] if isinstance(snapshot_ids, str) else list(snapshot_ids)
    affected = conn.execute(
        "SELECT device_name, command FROM netops.raw_output_store WHERE snapshot_id IN " + _in_clause(snapshot_ids),
        list(snapshot_ids),
    ).fetchall()
    rewritten = 0
    deleted = 0
    for device, command in affected:
        # Narrow columns first (snapshot_id/device_name/command/ingested_at): on a lake with
        # DuckLake-inlined ingest_sources rows, reading raw_output/source_json materializes the
        # whole inlined column (OW-133/OW-139); only touch the wide columns when a remaining
        # source actually exists for this (device, command).
        candidate = conn.execute(
            "SELECT snapshot_id FROM netops.ingest_sources "
            "WHERE device_name=? AND command=? AND snapshot_id NOT IN " + _in_clause(snapshot_ids) + " "
            "ORDER BY ingested_at DESC, snapshot_id DESC LIMIT 1",
            [device, command, *snapshot_ids],
        ).fetchone()
        source = None
        if candidate is not None:
            source = conn.execute(
                "SELECT snapshot_id, raw_output, json_extract_string(source_json, '$.platform') "
                "FROM netops.ingest_sources "
                "WHERE device_name=? AND command=? AND snapshot_id=? LIMIT 1",
                [device, command, candidate[0]],
            ).fetchone()
        if source is None:
            conn.execute(
                "DELETE FROM netops.raw_output_store WHERE device_name=? AND command=?",
                [device, command],
            )
            deleted += 1
            continue
        rsid, raw_output, platform = source
        bundle = conn.execute(
            "SELECT bundle_id, bundle_sha256 FROM netops.bundle_ingests WHERE snapshot_id=? LIMIT 1",
            [rsid],
        ).fetchone()
        bundle_id = bundle[0] if bundle else None
        bundle_sha256 = bundle[1] if bundle else None
        conn.execute(
            "UPDATE netops.raw_output_store SET raw_output=?, snapshot_id=?, "
            "platform=COALESCE(?, netops.raw_output_store.platform), "
            "bundle_id=COALESCE(?, netops.raw_output_store.bundle_id), "
            "bundle_sha256=COALESCE(?, netops.raw_output_store.bundle_sha256), "
            "ingested_via='bundle' WHERE device_name=? AND command=?",
            [raw_output, rsid, platform, bundle_id, bundle_sha256, device, command],
        )
        rewritten += 1
    return rewritten + deleted


def _recompute_topology_links(conn, snapshot_ids, remaining):
    snapshot_ids = [snapshot_ids] if isinstance(snapshot_ids, str) else list(snapshot_ids)
    affected = conn.execute(
        "SELECT count(*) FROM netops.topology_links WHERE snapshot_id IN " + _in_clause(snapshot_ids), list(snapshot_ids)
    ).fetchone()[0]
    if not affected:
        return 0
    from olav_skills.netops.core.topology_engine import _controlled_topology
    for rsid in remaining:
        _controlled_topology(conn, snapshot_id=rsid)
    orphaned = conn.execute(
        "SELECT count(*) FROM netops.topology_links WHERE snapshot_id IN " + _in_clause(snapshot_ids), list(snapshot_ids)
    ).fetchone()[0]
    if orphaned:
        conn.execute("DELETE FROM netops.topology_links WHERE snapshot_id IN " + _in_clause(snapshot_ids), list(snapshot_ids))
    return affected


def _delete_orphan_devices(conn):
    orphaned = conn.execute(
        "SELECT d.hostname FROM netops.devices d "
        "WHERE NOT EXISTS (SELECT 1 FROM netops.raw_output_store r WHERE r.device_name=d.hostname) "
        "AND NOT EXISTS (SELECT 1 FROM netops.parsed_outputs p WHERE p.device_name=d.hostname)"
    ).fetchall()
    for (hostname,) in orphaned:
        conn.execute("DELETE FROM netops.devices WHERE hostname=?", [hostname])
    return len(orphaned)


def _withdraw_execute(conn, snapshot_ids):
    snapshot_ids = list(snapshot_ids)
    counts = dict(zero_withdraw_counts())
    remaining = [r[0] for r in conn.execute(
        "SELECT snapshot_id FROM netops.bundle_ingests "
        "WHERE snapshot_id IS NOT NULL AND snapshot_id NOT IN " + _in_clause(snapshot_ids) + " "
        "ORDER BY ingested_at ASC, snapshot_id ASC",
        snapshot_ids,
    ).fetchall()]
    for table in ("netops.ingest_sources", "netops.parsed_outputs"):
        counts[table.split(".")[1]] = sum(_delete_where(conn, table, "snapshot_id", sid) for sid in snapshot_ids)
    if _table_exists(conn, "netops", "oc_outputs"):
        counts["oc_outputs"] = sum(_delete_where(conn, "netops.oc_outputs", "snapshot_id", sid) for sid in snapshot_ids)
    counts["bundle_ingests"] = sum(_delete_where(conn, "netops.bundle_ingests", "snapshot_id", sid) for sid in snapshot_ids)
    counts["raw_output_store"] = _recompute_raw_output_store(conn, snapshot_ids)
    counts["topology_links"] = _recompute_topology_links(conn, snapshot_ids, remaining)
    counts["devices"] = _delete_orphan_devices(conn)
    # The ingest ledger rows of the withdrawn snapshots must stop matching bundle
    # de-duplication (ingest() reuses the first *succeeded* operation with the same
    # source/parser fingerprint), otherwise re-importing the same bundle is a no-op.
    counts["ingest_operations"] = _mark_operations_withdrawn(conn, snapshot_ids)
    return counts


def _mark_operations_withdrawn(conn, snapshot_ids):
    snapshot_ids = [snapshot_ids] if isinstance(snapshot_ids, str) else list(snapshot_ids)
    n = conn.execute(
        "SELECT count(*) FROM netops.ingest_operations WHERE state='succeeded' AND snapshot_id IN " + _in_clause(snapshot_ids),
        snapshot_ids,
    ).fetchone()[0]
    if n:
        conn.execute(
            "UPDATE netops.ingest_operations SET state='withdrawn', stage='withdrawn' WHERE state='succeeded' AND snapshot_id IN " + _in_clause(snapshot_ids),
            snapshot_ids,
        )
    return int(n)


def withdraw_snapshot(*, snapshot_id, reason, idempotency_key):
    """Withdraw one snapshot (project import-service contract). See withdraw_snapshots."""
    return withdraw_snapshots(snapshot_ids=[snapshot_id], reason=reason, idempotency_key=idempotency_key)


def withdraw_snapshots(*, snapshot_ids, reason, idempotency_key):
    """Withdraw (retract) every fact row committed by a set of ingest snapshots in one
    transaction. Withdrawing overlapping fixture snapshots as a set avoids re-reading wide
    ingest_sources columns for devices whose only remaining sources are inside the set.

    Same trust/lease model as ingest(): the caller holds no connection; the
    writer domain and ingest connection are acquired inside writer_guard().
    Returns ``{'business_state': 'succeeded'|'failed'|'uncertain',
    'snapshot_id': ..., 'withdrawn_counts': {...}, 'error': {'code','stage'}|None}``.
    """
    if (not isinstance(snapshot_ids, (list, tuple)) or not 1 <= len(snapshot_ids) <= 16
            or len(set(snapshot_ids)) != len(snapshot_ids)
            or any(not isinstance(x, str) or not re.fullmatch(r"snap_[0-9a-f]{32}", x) for x in snapshot_ids)):
        raise ClosureError("INVALID_SNAPSHOT_ID")
    snapshot_ids = list(snapshot_ids)
    snapshot_id = ",".join(snapshot_ids)
    if (not isinstance(reason, str) or len(reason) > 256
            or any(ord(c) < 32 or ord(c) > 126 for c in reason)):
        raise ClosureError("INVALID_REASON")
    if (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128
            or any(ord(c) < 33 or ord(c) > 126 for c in idempotency_key)):
        raise ClosureError("INVALID_IDEMPOTENCY_KEY")
    with writer_guard():
        with open_ingest_connection() as conn:
            ensure_ledger(conn)
            old = _withdraw_operation(conn, idempotency_key)
            if old is not None:
                if old["state"] in ("succeeded", "failed"):
                    return _withdraw_result(old)
                # root (OW138, live 2026-11-12): an accepted/running row seen while we hold the
                # writer lock cannot belong to a live attempt (the previous worker was killed
                # mid-transaction; DuckDB rolled its facts back). Close it as interrupted and
                # let this attempt proceed instead of returning RESULT_UNCERTAIN forever.
                transaction(conn, lambda: _withdraw_update(
                    conn, idempotency_key, state="failed", stage="interrupted",
                    withdrawn_counts=canonical(zero_withdraw_counts()),
                    error=canonical({"code": "INTERRUPTED_UNCOMMITTED", "stage": "worker"}),
                    finished_at=now()))
                return {"business_state": "failed", "snapshot_id": snapshot_id,
                        "withdrawn_counts": zero_withdraw_counts(),
                        "error": {"code": "INTERRUPTED_UNCOMMITTED", "stage": "worker"}}
            def _has_succeeded_ops(sid):
                return conn.execute("SELECT 1 FROM netops.ingest_operations WHERE state='succeeded' AND snapshot_id=? LIMIT 1", [sid]).fetchone() is not None
            if not all(_snapshot_exists(conn, sid) or _has_succeeded_ops(sid) for sid in snapshot_ids):
                transaction(conn, lambda: _withdraw_insert(conn, idempotency_key, snapshot_id, reason))
                transaction(conn, lambda: _withdraw_update(
                    conn, idempotency_key, state="failed", stage="not_found",
                    withdrawn_counts=canonical(zero_withdraw_counts()),
                    error=canonical({"code": "RESOURCE_NOT_FOUND", "stage": "worker"}),
                    finished_at=now()))
                return {"business_state": "failed", "snapshot_id": snapshot_id,
                        "withdrawn_counts": zero_withdraw_counts(),
                        "error": {"code": "RESOURCE_NOT_FOUND", "stage": "worker"}}
            transaction(conn, lambda: _withdraw_insert(conn, idempotency_key, snapshot_id, reason))
            committed = False
            commit_attempted = False
            try:
                _assert_domain()
                conn.execute("BEGIN")
                _withdraw_update(conn, idempotency_key, state="running", stage="withdrawing")
                counts = _withdraw_execute(conn, snapshot_ids)
                with commit_guard() as sh:
                    _withdraw_update(conn, idempotency_key, state="succeeded", stage="committed",
                                     withdrawn_counts=canonical(counts), finished_at=now())
                    _assert_domain()
                    sh.check()
                    commit_attempted = True
                    conn.execute("COMMIT")
                    committed = True
            except BaseException as exc:
                if committed:
                    # COMMIT returned; only the guard's __exit__ (SH release) is in
                    # flight. The withdraw facts are durably committed; never
                    # overwrite them with a failed row.
                    raise ClosureError("RESULT_UNCERTAIN") from None
                if commit_attempted and _v3():
                    raise ClosureError("RESULT_UNCERTAIN") from None
                # Exception class only (never the message): enough to diagnose from the ledger.
                failure = {"code": "WITHDRAW_FAILED", "stage": "worker",
                           "exception_class": type(exc).__name__[:64]}
                try:
                    conn.execute("ROLLBACK")
                    transaction(conn, lambda: _withdraw_update(
                        conn, idempotency_key, state="failed", stage="rolled_back",
                        withdrawn_counts=canonical(zero_withdraw_counts()),
                        error=canonical(failure), finished_at=now()))
                except Exception:
                    raise ClosureError("RESULT_UNCERTAIN") from None
                return {"business_state": "failed", "snapshot_id": snapshot_id,
                        "withdrawn_counts": zero_withdraw_counts(), "error": failure}
            return {"business_state": "succeeded", "snapshot_id": snapshot_id,
                    "withdrawn_counts": counts}
