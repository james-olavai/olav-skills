"""Versioned native investigation recipes. No SQL, model memory or answer cache."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from . import __version__, state_contracts as states
from .native_reader_bootstrap import NativeReaderBootstrapError, bootstrap_native_reader, owns_native_reader
from .native_snapshot import NativeSnapshotError, close_native_reader, open_native_snapshot

READER_MODULE = 'olav_skills.query_recipes'
MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_INPUT_BYTES = 16384
# Python callers can supply two lists of 50 strings of 256 characters each.
# Even JSON escaping every character as \uXXXX fits this internal bound.
MAX_WORKER_INPUT_BYTES = 160 * 1024
MAX_OUTPUT_BYTES = 32768
TIMEOUT_SECONDS = 5
RECIPES = {
    'inventory': {'version': 1, 'steps': ['snapshot_summary', 'devices'],
                  'meaning': 'Observed snapshot records, uncatalogued members and current inventory metadata are separate; not a live census.'},
    'interfaces': {'version': 1, 'steps': ['interface_records'],
                   'meaning': 'Canonical recorded interface states; non-up does not establish an outage.'},
    'bgp': {'version': 1, 'steps': ['bgp_records'],
            'meaning': 'Recorded BGP summary states; numeric prefix counts mean Established only in the state_or_prefixes_received profiles, not Junos state.'},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _fail(kind, message):
    return {'schema': 'olav.investigation/v1', 'status': 'error', 'error_type': kind,
            'error': str(message)[:400], 'findings': None}


def _project():
    home = os.environ.get('OLAV_HOME')
    if not home or not Path(home).is_absolute():
        raise ValueError('bind OLAV_HOME to an absolute authorized native project')
    return Path(home)


def _validate(request):
    if not isinstance(request, dict) or set(request) - {'recipe', 'snapshot_id', 'devices', 'vendors', 'limit'}:
        raise ValueError('expected recipe, snapshot_id, optional devices/vendors/limit only')
    recipe = request.get('recipe')
    if not isinstance(recipe, str) or recipe not in RECIPES:
        raise ValueError('recipe must be inventory, interfaces or bgp')
    sid = request.get('snapshot_id')
    if not isinstance(sid, str) or not sid.strip() or len(sid) > 256 or sid.lower() in ('latest', 'current', 'all'):
        raise ValueError('snapshot_id must be an exact recorded identifier, not an alias')
    from .netops.core.query import batch
    args = batch._validate({'operation': 'devices', **{k:v for k,v in request.items() if k != 'recipe'}})
    return recipe, args


def _require_text_columns(q, source, columns):
    rows = q.run("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='netops' AND table_name=? LIMIT 129", [source],cap=129)
    types = dict(rows)
    if len(rows)>128 or any(types.get(col)!='VARCHAR' for col in columns):
        raise ValueError('unsupported scope/source column type: '+source)


def _scope_schema(q):
    custom = q.run("SELECT system.main.count(*) FROM system.main.duckdb_functions() WHERE NOT internal AND function_type IN ('macro','table_macro')")[0][0]
    if custom:
        raise ValueError('persistent custom macros unsupported by native investigation')
    for source in ('parsed_outputs','raw_output_store','bundle_ingests'):
        rows = q.run("SELECT data_type FROM information_schema.columns WHERE table_schema='netops' AND table_name=? AND column_name='snapshot_id' LIMIT 1", [source])
        if rows and rows[0][0]!='VARCHAR':
            raise ValueError('unsupported snapshot identity column type: '+source)


def _records(q, a, recipe):
    from .netops.core.query import batch

    interface = recipe == 'interfaces'
    sources, parts, params = [], [], []
    for source in states.VIEW_SOURCES[recipe]:
        name = states.view_name(source)
        cols = [r[0] for r in q.run("SELECT column_name FROM information_schema.columns WHERE table_schema='netops' AND table_name=? LIMIT 129", [name], cap=129)]
        present = q.run("SELECT table_type FROM information_schema.tables WHERE table_schema='netops' AND table_name=? LIMIT 1", [name])
        profile = states.view_profile(source, cols)
        ok = bool(present and present[0][0] == 'VIEW' and profile is not None)
        sources.append({'source': 'netops.' + name, 'status': 'available' if ok else ('unsupported_schema' if present else 'absent')})
        if not ok:
            if present:
                raise ValueError('unsupported schema: ' + name)
            continue
        _require_text_columns(q, name, (*states.VIEW_SCOPE, *profile.columns))
        if interface:
            if profile.semantics == 'family':
                admin = 's.admin_state' if profile == states.JUNOS_INTERFACE else 'NULL::VARCHAR'
                one, two = admin, 's.link_state'
                columns = f"s.interface,s.ip_address,NULL::VARCHAR AS status,s.proto AS protocol,{admin} AS admin_state,s.link_state,'family' AS protocol_semantics"
            else:
                one, two = 's.status', 's.proto'
                columns = "s.interface,s.ip_address,s.status,s.proto AS protocol,NULL::VARCHAR AS admin_state,NULL::VARCHAR AS link_state,'line_state' AS protocol_semantics"
            condition = states.interface_health_sql(one, two)
        else:
            state_field = profile.fields[0]
            raw = f's.{state_field}'
            condition = states.bgp_health_sql(raw, profile)
            columns = f"s.{profile.identity} AS bgp_neighbor,{raw} AS state,'{state_field}' AS source_state_field,'{profile.semantics}' AS state_semantics"
        bind = [a['snapshot_id']]
        predicate = 's.snapshot_id=?' + batch._in('s.device_name', a['devices'], bind)
        if a['vendors']:
            _require_text_columns(q, 'devices', ('hostname','vendor'))
            vendors = batch._in('d.vendor', a['vendors'], bind)
            predicate += f' AND EXISTS (SELECT 1 FROM netops.devices d WHERE d.hostname=s.device_name{vendors})'
        parts.append(f"SELECT '{name}' AS source,s.device_name,{columns},{condition} AS classification FROM netops.{name} s WHERE {predicate}")
        params.extend(bind)
    available = [s['source'] for s in sources if s['status'] == 'available']
    if not available:
        return {'operation': recipe + '_records', 'status': 'unknown', 'reason': 'no_supported_source', 'counts': None, 'sources': sources, 'rows': [], 'has_more': False}
    sql = 'WITH records AS (' + ' UNION ALL '.join(parts) + ') '
    counts = q.run(sql + "SELECT count(*),count(*) FILTER(WHERE classification='healthy'),count(*) FILTER(WHERE classification='nonhealthy'),count(*) FILTER(WHERE classification='unknown') FROM records", params)[0]
    fields = ['source','device_name','interface','ip_address','status','protocol','admin_state','link_state','protocol_semantics','classification'] if interface else ['source','device_name','bgp_neighbor','state','source_state_field','state_semantics','classification']
    clipped = ','.join(f'left(CAST("{f}" AS VARCHAR),256) AS "{f}"' for f in fields)
    rows = q.run(sql + f'SELECT {clipped} FROM records WHERE classification<>\'healthy\' ORDER BY '+ ','.join('"'+f+'" NULLS LAST' for f in fields) + ' LIMIT ?', [*params, a['limit']], cap=a['limit'])
    return {'status': 'success', 'operation': recipe + '_records', 'sources': sources,
            'counts': dict(zip(('record_count','healthy_record_count','nonhealthy_record_count','unknown_record_count'), counts)),
            'rows': [dict(zip(fields,r)) for r in rows], 'has_more': counts[2]+counts[3] > len(rows),
            'cell_values_may_be_clipped': True, 'sample_scope': 'nonhealthy and unknown records only', 'collection_coverage': 'unknown'}


def _inventory(q, a):
    from .netops.core.query import batch

    fields = batch.FIELDS['devices']
    _require_text_columns(q, 'devices', fields)
    _require_text_columns(q, 'parsed_outputs', ('snapshot_id','device_name','command'))
    dbinds = []
    dwhere = 'TRUE' + batch._in('d0.hostname', a['devices'], dbinds) + batch._in('d0.vendor', a['vendors'], dbinds)
    binds = [a['snapshot_id']]
    pwhere = 'p0.snapshot_id=?' + batch._in('p0.device_name', a['devices'], binds)
    if a['vendors']:
        pwhere += ' AND EXISTS(SELECT 1 FROM d WHERE d.hostname=p0.device_name)'
    cte = f'WITH d AS (SELECT '+','.join('d0.'+f for f in fields)+f' FROM netops.devices d0 WHERE {dwhere}), p AS (SELECT p0.device_name,p0.command FROM netops.parsed_outputs p0 WHERE {pwhere}) '
    params = dbinds + binds
    counts = q.run(cte + 'SELECT (SELECT count(DISTINCT d.hostname) FROM d),count(DISTINCT p.device_name),count(DISTINCT p.command),count(*),count(DISTINCT p.device_name) FILTER(WHERE NOT EXISTS(SELECT 1 FROM netops.devices d0 WHERE d0.hostname=p.device_name)) FROM p', params)[0]
    missing = q.run(cte + 'SELECT DISTINCT left(p.device_name,256) FROM p WHERE p.device_name IS NOT NULL AND NOT EXISTS(SELECT 1 FROM netops.devices d0 WHERE d0.hostname=p.device_name) ORDER BY 1 LIMIT ?', [*params,a['limit']],cap=a['limit'])
    summary = {'operation':'snapshot_summary','status':'success',
               'counts':dict(zip(('inventory_device_count','observed_device_count','command_count','record_count'),counts[:4])),
               'observed_without_catalog_count':counts[4], 'observed_without_catalog_sample':[r[0] for r in missing],
               'catalog_gap_sample_has_more':counts[4]>len(missing), 'sources':['netops.devices','netops.parsed_outputs'],
               'inventory_is_snapshot_membership':False, 'collection_coverage':'unknown'}
    member = ' FROM d WHERE EXISTS(SELECT 1 FROM p WHERE p.device_name=d.hostname)'
    total = q.run(cte+'SELECT count(*)'+member,params)[0][0]
    projection = ','.join(f'left(d.{f},256)' for f in fields)
    order = ','.join(f'd.{f} ASC NULLS LAST' for f in fields)
    rows = q.run(cte+'SELECT '+projection+member+' ORDER BY '+order+' LIMIT ?', [*params,a['limit']],cap=a['limit'])
    catalog = {'operation':'devices','status':'success','rows':[dict(zip(fields,r)) for r in rows],
               'total_rows':total, 'has_more':total>len(rows), 'cell_values_may_be_clipped':True,
               'sources':['netops.devices','netops.parsed_outputs'],
               'scope':'current inventory metadata for observed members; uncatalogued members are not rows here'}
    return [summary,catalog]


def _qualified_time(value):
    collected = value.get('collected_at', {})
    if str(collected.get('source', '')).startswith('snapshot_id'):
        from .netops.core.query import batch
        return {**value, 'snapshot_label_inference':collected,
                'collected_at':batch.unknown(),
                'coverage':{**value.get('coverage', {}), 'status':'unknown'}}
    return value


def _run_investigation(request, scratch, deadline):
    import duckdb
    from .netops.core.diff.snapshot_summary import SnapshotDiffError
    from .netops.core.query import batch

    try:
        recipe, args = _validate(request)
        from .netops.core.query import warehouse
        if warehouse.configured():
            raise ValueError('this recipe candidate supports native projects only')
        project = _project()
        with open_native_snapshot(project, scratch, max_bytes=MAX_DATABASE_BYTES,
                                  deadline=deadline, memory_limit='256MB') as (con, version):
            q = batch.Query(con)
            q.deadline = deadline
            q.exact_snapshot_scope = True
            _scope_schema(q)
            collated = q.run("SELECT count(*) FROM (SELECT sql FROM system.main.duckdb_tables() WHERE NOT internal UNION ALL SELECT sql FROM system.main.duckdb_views() WHERE NOT internal) x WHERE regexp_matches(sql, '(?i)\\bcollate\\b')")[0][0]
            if collated:
                raise ValueError('explicit schema COLLATE annotations unsupported; exact binary scope required')
            q.snapshot(args['snapshot_id'])
            steps = _inventory(q, args) if recipe == 'inventory' else [_records(q, args, recipe)]
            temporal = _qualified_time(q.temporal(args['snapshot_id']))
            for step in steps:
                step['snapshot_id'] = args['snapshot_id']
                step['temporal'] = temporal
            schema = q.run("SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema='netops' ORDER BY table_name,ordinal_position LIMIT 513", cap=513)
            if len(schema) > 512:
                raise ValueError('schema exceeds bounded fingerprint catalog')
        if _project() != project:
            raise ValueError('project binding changed')
        if any(s.get('status') == 'error' for s in steps):
            raise ValueError('an investigation step failed; results withheld')
        result = {'schema':'olav.investigation/v1','status':'success',
                  'recipe':recipe, 'recipe_version':RECIPES[recipe]['version'], 'recipe_sha256':digest(RECIPES[recipe]),
                  'package_version':__version__, 'executor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'source_version':{'database_sha256':version['sha256'], 'schema_sha256':digest(schema),
                                    'backend':version['backend'], 'database_bytes':version['bytes']},
                  'effective_request':request,'vendor_scope':'current inventory metadata; uncatalogued vendor unknown/excluded' if args['vendors'] else 'no inventory membership filter on source records', 'snapshot_id':args['snapshot_id'],'temporal':temporal,
                  'steps':steps, 'collection_coverage':'unknown', 'live_state':'unknown',
                  'semantics':RECIPES[recipe]['meaning'], 'limits':{'rows_per_step':args['limit'],'cell_chars':256,'response_bytes':MAX_OUTPUT_BYTES,'database_bytes':MAX_DATABASE_BYTES,'timeout_seconds':TIMEOUT_SECONDS,'memory_mb':256}}
        if recipe in states.VIEW_SOURCES:
            result['state_contract_sha256'] = hashlib.sha256(Path(states.__file__).read_bytes()).hexdigest()
        if len(json.dumps(result,ensure_ascii=False,default=str).encode()) > MAX_OUTPUT_BYTES:
            return _fail('budget_exceeded','result exceeds output budget; narrow devices or limit')
        return result
    except NativeSnapshotError as exc:
        return _fail(exc.code, str(exc))
    except SnapshotDiffError as exc:
        return _fail(exc.error_type if hasattr(exc,'error_type') else 'source_error', str(exc))
    except (ValueError, TypeError, OSError, duckdb.Error) as exc:
        return _fail('investigation_failed',str(exc))


def run_investigation(request):
    """Run the fixed read in a disposable process under a hard deadline."""
    try:
        _validate(request)
        raw = json.dumps(request, ensure_ascii=False).encode('utf-8')
        if len(raw) > MAX_WORKER_INPUT_BYTES:
            raise ValueError('Python request exceeds the internal input budget')
    except (ValueError, TypeError, RecursionError) as exc:
        return _fail('investigation_failed', str(exc))
    return _invoke_worker(raw)


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate JSON key refused')
        out[key] = value
    return out


def _request_from_stdin(max_bytes=MAX_INPUT_BYTES):
    raw = sys.stdin.buffer.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f'stdin exceeds {max_bytes} bytes')
    return raw, json.loads(raw, object_pairs_hook=_unique_object)


def _write(out):
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 1 if out['status'] == 'error' else 0


def _invoke_worker(raw):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    from .netops.core.query import warehouse

    try:
        project = _project()
        # The child receives no storage selectors. Reject configured storage
        # using the caller's original settings before sanitizing its environment.
        if warehouse.configured():
            raise ValueError('this recipe candidate supports native projects only')
    except ValueError as exc:
        return _fail('investigation_failed', str(exc))

    try:
        with TemporaryDirectory(prefix='olav-investigation-') as scratch:
            env = {
                'OLAV_HOME': str(project),
                'TMPDIR': scratch,
                'PYTHONPATH': str(Path(__file__).resolve().parent.parent),
                'PYTHONIOENCODING': 'utf-8',
                'OPENBLAS_NUM_THREADS': '1',
            }
            process = subprocess.run(
                [sys.executable, '-B', '-P', '-s', '-m', READER_MODULE,
                 '--worker', scratch, str(deadline)],
                input=raw, capture_output=True, cwd=scratch, env=env,
                timeout=max(0.001, deadline - time.monotonic()),
                check=False,
            )
        if process.returncode in (0, 1) and len(process.stdout) <= MAX_OUTPUT_BYTES + 1:
            result = json.loads(process.stdout)
            expected = 'success' if process.returncode == 0 else 'error'
            if (isinstance(result, dict) and result.get('schema') == 'olav.investigation/v1'
                    and result.get('status') == expected):
                return result
        return _fail('worker_failed', 'Investigation read worker did not return a bounded result.')
    except subprocess.TimeoutExpired:
        return _fail('timeout', 'Investigation read exceeded its whole-operation deadline.')
    except (OSError, ValueError):
        return _fail('worker_failed', 'Could not run the private investigation read worker.')


def main():
    try:
        raw, _ = _request_from_stdin()
        return _write(_invoke_worker(raw))
    except (ValueError, TypeError, RecursionError) as exc:
        return _write(_fail('validation_error', str(exc)))


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        _, request = _request_from_stdin(MAX_WORKER_INPUT_BYTES)
        try:
            bootstrap_native_reader(READER_MODULE)
        except NativeReaderBootstrapError as exc:
            result = _fail(str(exc), 'Investigation reader initialization was rejected.')
        else:
            result = _run_investigation(request, Path(sys.argv[2]), float(sys.argv[3]))
        if owns_native_reader(READER_MODULE):
            close_native_reader()
        raise SystemExit(_write(result))
    raise SystemExit(main())
