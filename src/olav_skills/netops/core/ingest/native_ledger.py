"""History for explicitly opted-in, caller-transaction-owned native imports.

This is not the DuckLake closure or an old-data migration. No original raw body
or complete source metadata document is copied into provenance.
"""
from __future__ import annotations
import hashlib
import json
from olav_skills.core.ingest.schema import CommandFile
from olav_skills.netops.core.query.temporal import time_value, unknown

COLUMNS = ('snapshot_id','device_name','command','source_json','raw_output',
           'parser_json','parsed_at','ingested_at','task_id','commit_token')

class NativeLedgerError(ValueError):
    pass

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()

def _text(value,cap=512):
    if not isinstance(value,str) or not value or value!=value.strip() or not value.isprintable() or len(value.encode())>cap:
        raise NativeLedgerError('NATIVE_LEDGER_METADATA_INVALID')
    return value

def require_transaction(conn,closure_context):
    if conn is None or closure_context is not None:
        raise NativeLedgerError('NATIVE_LEDGER_REQUIRES_NATIVE_TRANSACTION')
    from olav_skills.core.ingest_write import is_ducklake
    if is_ducklake(conn):
        raise NativeLedgerError('NATIVE_LEDGER_REQUIRES_NATIVE_TRANSACTION')
    # In autocommit, each SELECT gets a different ID. An explicit caller-owned
    # transaction keeps both IDs equal; no transaction is begun/committed here.
    first=conn.execute('SELECT system.main.current_transaction_id()').fetchone()
    second=conn.execute('SELECT system.main.current_transaction_id()').fetchone()
    if first!=second or not first:
        raise NativeLedgerError('NATIVE_LEDGER_REQUIRES_NATIVE_TRANSACTION')

def _bytes(reader,rel):
    fs=reader._fs
    if fs._kind=='dir':
        return (fs._root/rel).read_bytes()
    with fs._zf.open(fs._zip_prefix+rel) as fh:
        return fh.read()

def _capture(evidence):
    known = [clock for clock in evidence if clock['status'] == 'known']
    if len({(clock['value'], clock['precision']) for clock in known}) > 1:
        return unknown('capture_evidence', 'conflict')
    if any(clock['status'] == 'invalid' for clock in evidence):
        return unknown('capture_evidence', 'invalid')
    # Device and bundle clocks can corroborate a command header but describe
    # a wider capture. Only an offset-aware header establishes this instant.
    for clock in known:
        if clock['source'] == 'command_header' and clock['precision'] == 'timestamp':
            return clock
    return unknown('capture_evidence')

def prepare_inputs(reader,collection_source):
    """Hash exact file bytes and bind typed metadata, without retaining bodies."""
    _text(collection_source)
    records={};files={}
    manifest_bytes=_bytes(reader,'manifest.yaml')
    files['manifest.yaml']=digest_bytes(manifest_bytes)
    for host in reader._fs.iter_subdirs('devices'):
        _text(host)
        meta=reader._read_device_meta(host)
        meta_path=f'devices/{host}/_meta.yaml'
        if reader._fs.exists(meta_path): files[meta_path]=digest_bytes(_bytes(reader,meta_path))
        for name in reader._fs.iter_files(f'devices/{host}'):
            if name.startswith('.') or not name.endswith('.txt'): continue
            rel=f'devices/{host}/{name}';_text(rel)
            raw=_bytes(reader,rel)
            # Same replacement decode as BundleReader; input hash is the actual
            # original bytes, body hash the decoded header-stripped input body.
            cf=CommandFile.from_text(raw.decode('utf-8',errors='replace'),filename_hint=name)
            _text(cf.command,256)
            key=(host,cf.command)
            if key in records: raise NativeLedgerError('NATIVE_LEDGER_DUPLICATE_COMMAND')
            evidence=[time_value(cf.collected_at_iso,'command_header'),
                time_value(meta.collected_at if meta else None,'device_meta'),
                time_value(reader.manifest.collected_at,'manifest')]
            records[key]={'input_artifact':rel,'input_sha256':digest_bytes(raw),
                'body_sha256':digest_bytes(cf.body.encode()),'platform':meta.platform if meta else 'unknown',
                'capture_evidence':evidence,'collected_at':_capture(evidence)}
            files[rel]=digest_bytes(raw)
    source_fingerprint=digest_bytes(canonical({'records':records_to_list(records),
        'input_hashes':files,'collection_source':collection_source}).encode())
    return {'records':records,'files':files,'source_fingerprint':source_fingerprint,
            'collection_source':collection_source}

def records_to_list(records):
    return [dict(device_name=k[0],command=k[1],**v) for k,v in sorted(records.items())]

def verify_record(source,rec):
    record=source['records'].get((rec.host,rec.command))
    if (record is None or record['body_sha256']!=digest_bytes(rec.body.encode())
        or record['platform']!=rec.platform
        or record['capture_evidence'][0]!=time_value(rec.collected_at_iso,'command_header')):
        raise NativeLedgerError('NATIVE_LEDGER_INPUT_CHANGED')
    return record

def verify_inputs(reader,source):
    if any(digest_bytes(_bytes(reader,rel))!=digest for rel,digest in source['files'].items()):
        raise NativeLedgerError('NATIVE_LEDGER_INPUT_CHANGED')

def prepare_ledger(conn,snapshot_id):
    _text(snapshot_id)
    conn.execute('CREATE SCHEMA IF NOT EXISTS netops')
    conn.execute('CREATE TABLE IF NOT EXISTS netops.ingest_sources ('+','.join(c+' VARCHAR' for c in COLUMNS)+')')
    kind=conn.execute("SELECT table_type FROM information_schema.tables WHERE table_schema='netops' AND table_name='ingest_sources'").fetchall()
    cols=dict(conn.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='netops' AND table_name='ingest_sources'").fetchall())
    if kind!=[('BASE TABLE',)] or cols!={c:'VARCHAR' for c in COLUMNS}:
        raise NativeLedgerError('NATIVE_LEDGER_SCHEMA_CONFLICT')
    # Refuse old SID reuse before any last-writer/parsed table can overwrite it.
    for table in ('raw_output_store','parsed_outputs','bundle_ingests','ingest_sources'):
        found=conn.execute("SELECT table_type FROM information_schema.tables WHERE table_schema='netops' AND table_name=?",[table]).fetchall()
        if not found: continue
        types=dict(conn.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='netops' AND table_name=?",[table]).fetchall())
        if found!=[('BASE TABLE',)] or types.get('snapshot_id')!='VARCHAR':
            raise NativeLedgerError('NATIVE_LEDGER_SCHEMA_CONFLICT')
        if conn.execute('SELECT 1 FROM netops.'+table+' WHERE system.main.encode(snapshot_id)=system.main.encode(?) LIMIT 1',[snapshot_id]).fetchone():
            raise NativeLedgerError('NATIVE_LEDGER_SNAPSHOT_EXISTS')

def insert_records(conn,sid,source,records,ingested_at):
    expected=set(source['records'])
    actual={(r['device_name'],r['command']) for r in records}
    if expected!=actual or len(actual)!=len(records):
        raise NativeLedgerError('NATIVE_LEDGER_RECORD_SHAPE_CONFLICT')
    for rec in records:
        key=(rec['device_name'],rec['command'])
        original=source['records'][key]
        parser=rec['parser']
        if rec['parse_status']=='parsed' and not isinstance(parser,dict):
            raise NativeLedgerError('NATIVE_LEDGER_PARSER_TRACE_MISSING')
        provenance=dict(original,device_name=key[0],command=key[1],source_fingerprint=source['source_fingerprint'],
            collection_source=source['collection_source'],effective_platform=rec['platform'],
            actual_raw_sha256=digest_bytes(rec['raw_output'].encode()),
            parser_fingerprint=digest_bytes(canonical(parser).encode()),
            parser_fingerprint_scope='selected_parser_trace_only',parse_status=rec['parse_status'],
            redaction_assurance='not_certified',
            raw_artifact=dict(table='netops.ingest_sources',snapshot_id=sid,device_name=key[0],command=key[1]))
        _text(provenance['platform'],64);_text(provenance['effective_platform'],64)
        conn.execute('INSERT INTO netops.ingest_sources ('+','.join(COLUMNS)+') VALUES (?,?,?,?,?,?,?,?,?,?)',
            [sid,*key,canonical(provenance),rec['raw_output'],canonical(parser),rec['parsed_at'],ingested_at,None,None])
