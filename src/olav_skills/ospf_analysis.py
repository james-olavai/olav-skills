"""Bounded recorded OSPF states; no health or forwarding certification."""
from __future__ import annotations
import json
import ipaddress
import re
from math import isfinite
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from . import bounded_export as storage
from .native_snapshot import NativeSnapshotError, open_native_snapshot
from .neighbor_observations import _label, _interface, _no_constants
from .netops.core.platform_canonical import canonicalize_platform

SCHEMA = 'olav.ospf-analysis/v1'
MAX_REQUEST = 16 * 1024
MAX_RESPONSE = 64 * 1024
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_TIMEOUT_MS = 15000
MAX_ROWS = 4096
MAX_INPUT_BYTES = 1024 * 1024
ALIASES = {'latest','current','newest','all','unknown','none','null','-'}
PROFILES = {'cisco_ios':'show ip ospf neighbor','juniper_junos':'show ospf neighbor'}
class OSPFError(Exception):
    pass

def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')

def _failed(reason):
    return {'schema':SCHEMA,'status':'error','error_type':reason,'analysis_status':'unknown','summary':None,'observations':[]}

def _request(raw):
    if len(raw)>MAX_REQUEST: raise OSPFError('request_byte_limit')
    try:
        args=json.loads(raw,object_pairs_hook=storage._unambiguous_object,parse_constant=_no_constants)
        if not isinstance(args,dict): raise ValueError()
        mode=args.setdefault('mode','inspect')
        if mode not in ('inspect','compare'): raise ValueError()
        required={'snapshot_id'} if mode=='inspect' else {'snapshot_id_1','snapshot_id_2'}
        if not required<=args.keys()<=required|{'mode','devices','vendors','limit','timeout_ms'}: raise ValueError()
        if any(not _label(args[k]) or args[k].casefold() in ALIASES for k in required): raise ValueError()
        if mode=='compare' and args['snapshot_id_1']==args['snapshot_id_2']: raise ValueError()
        for key in ('devices','vendors'):
            values=args.setdefault(key,[])
            if not isinstance(values,list) or len(values)>64 or any(not _label(x) or x.casefold() in ALIASES for x in values) or len(set(values))!=len(values): raise ValueError()
            if key=='vendors' and any(x not in PROFILES for x in values): raise ValueError()
            args[key]=sorted(values)
        for key,default,cap in [('limit',20,50),('timeout_ms',MAX_TIMEOUT_MS,MAX_TIMEOUT_MS)]:
            value=args.setdefault(key,default)
            if type(value) is not int or not 1<=value<=cap: raise ValueError()
    except (ValueError,TypeError,UnicodeError,RecursionError,storage.ExportError):
        raise OSPFError('validation_error') from None
    return args

def _project():
    home=os.environ.get('OLAV_HOME')
    if not _label(home,2048): raise OSPFError('invalid_project')
    path=Path(home)
    if path.anchor!='/' or str(path)!=home or path==Path('/') or '..' in path.parts: raise OSPFError('invalid_project')
    return path

def _ip(value):
    if not _label(value): return None
    try:
        address=ipaddress.IPv4Address(value)
        return str(address) if not address.is_unspecified else None
    except ValueError: return None

def _classification(state):
    if not _label(state): return 'unknown',None,None
    tokens=state.upper().replace(' ','').split('/')
    if len(tokens)>2: return 'unknown',None,None
    phase=tokens[0].replace('-','')
    role=tokens[1] if len(tokens)==2 else None
    if role not in (None,'DR','BDR','DROTHER','-'): return 'unknown',None,None
    if phase=='FULL': return 'full_observed','Full',role
    if phase=='2WAY': return 'two_way_unknown','2-Way',role
    if phase in ('DOWN','ATTEMPT','INIT','EXSTART','EXCHANGE','LOADING'):
        return 'transitional_observed',phase.title(),role
    return 'unknown',None,role

def _read(conn,sids,args):
    catalog="database_name=system.main.current_database() AND schema_name='netops' AND table_name='parsed_outputs'"
    if conn.execute(f'SELECT 1 FROM system.main.duckdb_tables() WHERE {catalog} AND NOT internal AND NOT temporary LIMIT 1').fetchone() is None: raise OSPFError('source_table_absent')
    fields={'snapshot_id':'VARCHAR','device_name':'VARCHAR','command':'VARCHAR','platform':'VARCHAR','parsed_data':'JSON'}
    actual=dict(conn.execute(f"SELECT column_name,data_type FROM system.main.duckdb_columns() WHERE {catalog} AND column_name IN (?,?,?,?,?) LIMIT 5",list(fields)).fetchall())
    if actual!=fields: raise OSPFError('unsupported_schema')
    size='system.main.octet_length(system.main.encode(CAST(parsed_data AS VARCHAR)))'
    clauses=['system.main.encode(snapshot_id) IN ('+','.join('system.main.encode(?)' for _ in sids)+')',"(command ILIKE '%ospf%neighbor%')"]
    params=list(sids)
    if args['devices']:
        clauses.append('system.main.encode(device_name) IN ('+','.join('system.main.encode(?)' for _ in args['devices'])+')');params+=args['devices']
    query=f"SELECT snapshot_id,CASE WHEN system.main.octet_length(system.main.encode(device_name))<=256 THEN device_name END,CASE WHEN system.main.octet_length(system.main.encode(command))<=256 THEN command END,CASE WHEN system.main.octet_length(system.main.encode(platform))<=256 THEN platform END,{size},CASE WHEN {size}<=262144 THEN CAST(parsed_data AS VARCHAR) END FROM netops.parsed_outputs WHERE {' AND '.join(clauses)} LIMIT ?"
    cursor=conn.execute(query,params+[MAX_ROWS+1])
    sides={sid:{'rows':{},'reasons':set(),'parents':set(),'records':0} for sid in sids}
    consumed=records=0
    for n in range(MAX_ROWS+1):
        parent=cursor.fetchone()
        if parent is None: break
        sid,device,command,platform,size,payload=parent
        consumed+=size or 0
        if n==MAX_ROWS or (size or 0)>262144 or consumed>MAX_INPUT_BYTES: raise OSPFError('input_budget_exceeded')
        side=sides[sid];vendor=canonicalize_platform(platform)
        if args['vendors'] and vendor not in args['vendors']: continue
        identity=(device,command)
        if identity in side['parents']: side['reasons'].add('duplicate_parent')
        side['parents'].add(identity)
        if not _label(device) or device.casefold() in ALIASES or vendor not in PROFILES or command!=PROFILES[vendor]:
            side['reasons'].add('unsupported_or_unknown_source_profile');continue
        try: rows=json.loads(payload,object_pairs_hook=storage._unambiguous_object,parse_constant=_no_constants)
        except (ValueError,TypeError,RecursionError,storage.ExportError): rows=None
        if not isinstance(rows,list) or not rows:
            side['reasons'].add('empty_or_invalid_parsed_source');continue
        records+=len(rows)
        if records>MAX_ROWS: raise OSPFError('input_budget_exceeded')
        for row in rows:
            side['records']+=1
            if not isinstance(row,dict): side['reasons'].add('invalid_parsed_record');continue
            if any(row.get(k) not in (None,'') for k in ('vrf','vrf_name','routing_instance','routing_context','process','process_id','ospf_process','area','area_id','instance','instance_id','network_type','local_role')):
                side['reasons'].add('unsupported_routing_context');continue
            if not set(row)<= {'state','interface','ip_address','neighbor_id','priority','dead_time'}:
                side['reasons'].add('unsupported_parsed_fields');continue
            interface=_interface(row.get('interface'));neighbor=_ip(row.get('neighbor_id'));address=_ip(row.get('ip_address'))
            if interface is None or neighbor is None or address is None:
                side['reasons'].add('unknown_neighbor_identity');continue
            key=(device,vendor,command,interface,address,neighbor)
            if key in side['rows']: side['reasons'].add('duplicate_or_ambiguous_neighbor');continue
            category,state,role=_classification(row.get('state'))
            if category=='unknown': side['reasons'].add('unknown_state')
            side['rows'][key]={'identity':dict(zip(('device','platform','command','interface','neighbor_ip','neighbor_id'),key)),
                'state':state,'state_raw':row.get('state') if _label(row.get('state')) else None,'neighbor_role':role,
                'classification':category,'routing_context':{'vrf':None,'process':None,'area':None,'qualification':'not_exposed_by_stock_profile'},
                'health':'unknown','network_type':None,'local_role':None}
    for side in sides.values():
        if not side['parents']: side['reasons'].add('snapshot_source_missing')
        if args['devices'] and {p[0] for p in side['parents']}!=set(args['devices']): side['reasons'].add('requested_device_source_missing')
        if not side['rows']: side['reasons'].add('no_usable_observations')
    return sides

def _side(side,limit):
    counts={k:0 for k in ('full_observed','two_way_unknown','transitional_observed','unknown')}
    for row in side['rows'].values(): counts[row['classification']]+=1
    return {'analysis_status':'observed' if not side['reasons'] else 'unknown','summary':dict(record_count=len(side['rows']),**counts) if side['rows'] else None,
        'observations':[side['rows'][k] for k in sorted(side['rows'])][:limit],
        'observations_more':len(side['rows'])>limit,'observed_record_count':len(side['rows']) if side['rows'] else None,
        'reasons':sorted(side['reasons']),'source_parent_count':len(side['parents']),'health':'unknown'}

def _worker(args,scratch,deadline):
    from .native_reader_bootstrap import (validate_native_reader_identity,
        bootstrap_native_reader, NativeReaderBootstrapError)
    try:
        validate_native_reader_identity('olav_skills.ospf_analysis')
    except NativeReaderBootstrapError:
        return _failed('native_reader_bootstrap_rejected')
    import resource
    _,hard=resource.getrlimit(resource.RLIMIT_AS)
    memory=storage.ADDRESS_SPACE_BYTES if hard==resource.RLIM_INFINITY else min(hard,storage.ADDRESS_SPACE_BYTES)
    resource.setrlimit(resource.RLIMIT_AS,(memory,hard))
    try:
        duckdb = bootstrap_native_reader('olav_skills.ospf_analysis')
    except NativeReaderBootstrapError:
        return _failed('native_reader_bootstrap_rejected')
    sids=[args['snapshot_id']] if args['mode']=='inspect' else [args['snapshot_id_1'],args['snapshot_id_2']]
    try:
        with open_native_snapshot(_project(),scratch,max_bytes=MAX_SOURCE_BYTES,deadline=deadline) as (conn,version):
            conn.execute('BEGIN TRANSACTION');sides=_read(conn,sids,args);conn.execute('COMMIT')
            result={'schema':SCHEMA,'status':'success','effective_request':args,'source':'netops.parsed_outputs','source_version':version,
                'scope':{'snapshot_ids':sids,'coverage':'unknown','collection_time':'unknown','live_state':'unknown','profile':'stock_ios_junos_ospf_ipv4_neighbor'},
                'meaning':'recorded_state_only_not_health_or_forwarding',
                'assessment':{'persistence_status':'unknown','confirmed_stuck_count':None,
                    'root_cause_status':'unknown','basis':'profile_has_no_qualified_adjacency_duration'}}
            if args['mode']=='inspect': result.update(_side(sides[sids[0]],args['limit']))
            else:
                left,right=(sides[sid] for sid in sids)
                reasons=sorted(left['reasons']|right['reasons'])
                changes=[]
                counts=None
                if not reasons:
                    counts={k:0 for k in ('observed_record_added','observed_record_removed','observed_record_state_changed','observed_record_unchanged')}
                    for key in sorted(left['rows'].keys()|right['rows'].keys()):
                        before,after=left['rows'].get(key),right['rows'].get(key)
                        kind=('observed_record_added' if before is None else 'observed_record_removed' if after is None else
                              'observed_record_state_changed' if (before['state'],before['neighbor_role'])!=(after['state'],after['neighbor_role']) else 'observed_record_unchanged')
                        counts[kind]+=1
                        if kind!='observed_record_unchanged': changes.append({'kind':kind,'identity':(before or after)['identity'],'before':before,'after':after})
                result.update(analysis_status='unknown' if reasons else 'observed',comparison_status='unknown' if reasons else 'partial',
                    comparison_scope='observed_records_only',routing_identity='unknown',adjacency_continuity='unknown',
                    summary=None,changes=changes[:args['limit']],changes_more=len(changes)>args['limit'],counts=counts,
                    reasons=reasons+['routing_context_not_exposed_cannot_establish_adjacency_continuity'],
                    snapshots={sid:_side(sides[sid],args['limit']) for sid in sids})
    except duckdb.OutOfMemoryException: raise OSPFError('input_budget_exceeded') from None
    except duckdb.Error: raise OSPFError('database_read_failed') from None
    return result

def analyze_ospf(raw):
    """Exec a disposable reader; never open the source in the caller process."""
    diagnostic_stage='request'
    try:
        args = _request(raw)
        if sys.platform != 'linux':
            raise OSPFError('unsupported_platform')
        project = _project()
        from .netops.core.query import warehouse
        if warehouse.configured():
            raise OSPFError('unsupported_backend')
        deadline = time.monotonic() + args['timeout_ms'] / 1000
        with tempfile.TemporaryDirectory(prefix='olav-ospf-') as scratch:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OSPFError('deadline_exceeded')
            env = {'OLAV_HOME': str(project), 'TMPDIR': scratch,
                   'PYTHONPATH': str(Path(__file__).resolve().parent.parent),
                   'PYTHONIOENCODING': 'utf-8', 'OPENBLAS_NUM_THREADS': '1'}
            diagnostic_stage='child_exec'
            child = subprocess.run([sys.executable, '-B', '-P', '-s', '-m', 'olav_skills.ospf_analysis', '--worker', scratch, str(deadline)],
                                   input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=remaining, check=False, env=env, cwd=scratch)
        if child.returncode:
            return dict(_failed('worker_failed'),worker_exit_code=child.returncode,diagnostic_stage='child_nonzero')
        if len(child.stdout)>MAX_RESPONSE:
            raise OSPFError('response_byte_limit')
        diagnostic_stage='response_json'
        result=json.loads(child.stdout,object_pairs_hook=storage._unambiguous_object,parse_constant=_no_constants)
        diagnostic_stage='response_schema'
        if not isinstance(result,dict) or result.get('schema')!=SCHEMA or result.get('status') not in ('success','error'):
            raise OSPFError('worker_failed')
        if result['status']=='error':
            if not _label(result.get('error_type')): raise OSPFError('worker_failed')
            return dict(_failed(result['error_type']),diagnostic_stage='reader_reported') if result['error_type']=='worker_failed' else _failed(result['error_type'])
        version=result.get('source_version')
        expected_scope=[args['snapshot_id']] if args['mode']=='inspect' else [args['snapshot_id_1'],args['snapshot_id_2']]
        diagnostic_stage='response_scope'
        if (result.get('effective_request')!=args or result.get('source')!='netops.parsed_outputs'
            or not isinstance(result.get('scope'),dict) or result['scope'].get('snapshot_ids')!=expected_scope):
            raise OSPFError('worker_failed')
        diagnostic_stage='response_version'
        if (not isinstance(version,dict) or not isinstance(version.get('sha256'),str)
            or re.fullmatch('[0-9a-f]{64}',version['sha256']) is None
            or version.get('path')!='.olav/databases/main.duckdb'
            or version.get('backend')!='native_duckdb_locked_copy'
            or type(version.get('bytes')) is not int or version['bytes']<0):
            raise OSPFError('worker_failed')
        return result
    except (OSPFError) as exc:
        return dict(_failed(str(exc)),diagnostic_stage=diagnostic_stage) if str(exc)=='worker_failed' else _failed(str(exc))
    except subprocess.TimeoutExpired:
        return _failed('deadline_exceeded')
    except (ValueError, OSError, storage.ExportError, RecursionError, TypeError):
        return dict(_failed('worker_failed'),diagnostic_stage=diagnostic_stage)


def _run_worker(raw, scratch, deadline):
    try:
        args = _request(raw)
        deadline = float(deadline)
        if not isfinite(deadline):
            raise OSPFError('validation_error')
        return _worker(args, Path(scratch), min(deadline, time.monotonic() + args['timeout_ms'] / 1000))
    except NativeSnapshotError as exc:
        return _failed(exc.code)
    except (OSPFError) as exc:
        return _failed(str(exc))
    except MemoryError:
        return _failed('input_budget_exceeded')
    except (ValueError, OSError, storage.ExportError, RecursionError, TypeError):
        return _failed('worker_failed')


def main(*, scratch=None, deadline=None):
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    result = _run_worker(raw, scratch, deadline) if scratch is not None else analyze_ospf(raw)
    payload = _json(result)
    if len(payload) > MAX_RESPONSE:
        result = _failed('response_byte_limit')
        payload = _json(result)
    if scratch is not None:
        from .native_snapshot import close_native_reader
        from .native_reader_bootstrap import owns_native_reader
        if owns_native_reader('olav_skills.ospf_analysis'):
            close_native_reader()
    sys.stdout.buffer.write(payload + b'\n')
    return 1 if scratch is None and result['status'] == 'error' else 0


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        raise SystemExit(main(scratch=sys.argv[2], deadline=sys.argv[3]))
    raise SystemExit(main())
