"""Snapshot consumers publish only after validation and really query copies."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import duckdb
import pytest
from olav_skills import bounded_export as export


def test_export_does_not_publish_when_final_source_check_fails(tmp_path,monkeypatch):
    source=tmp_path/'source';source.write_bytes(b'sentinel')
    @contextmanager
    def rejected(*args):
        yield object(),{},object()
        raise export.ExportError('source_changed')
    @contextmanager
    def opened(*args):
        with source.open('rb') as f:yield f
    published=[]
    monkeypatch.setattr(export,'_connect',rejected)
    monkeypatch.setattr(export,'_open_regular',opened)
    monkeypatch.setattr(export,'_unlink_recovery_file',lambda *_:None)
    monkeypatch.setattr(export,'_read_page',lambda *_:object())
    monkeypatch.setattr(export,'_write_page',lambda *_:({'rows':1,'bytes':10,'sha256':'a'*64},None))
    monkeypatch.setattr(export,'_verify_source',lambda *_:None)
    monkeypatch.setattr(export,'_persist',lambda *_:published.append(1))
    scope={'format':'jsonl','project':str(tmp_path),'source':'parsed_outputs','columns':['device_name']}
    with pytest.raises(export.ExportError,match='source_changed'):
        export._perform(0,0,duckdb,scope,{'max_rows':1,'max_bytes':100,'timeout_ms':1000},None,0,0)
    assert not published and source.read_bytes()==b'sentinel'


def trace_call(tmp_path,project,skill,script,request):
    trace=tmp_path/(script+'.trace');ipc=tmp_path/(script+'-ipc');ipc.mkdir(mode=0o700)
    env={**os.environ,'OLAV_HOME':str(project),'TMPDIR':str(ipc),'PYTHONDONTWRITEBYTECODE':'1'}
    cmd=[shutil.which('strace'),'-f','-yy','-e','trace=openat,fcntl','-o',str(trace),sys.executable,'-B','-m','olav_skills','run',skill,script]
    child=subprocess.run(cmd,input=json.dumps(request),text=True,capture_output=True,env=env,timeout=20)
    assert child.returncode==0,(child.stdout,child.stderr)
    text=trace.read_text();source=str(project/'.olav/databases/main.duckdb')
    locks=[line for line in text.splitlines() if ', F_SETLK,' in line and 'F_RDLCK' in line and 'main.duckdb' in line]
    assert locks and all(source not in line for line in locks), locks
    assert any(source in line and 'F_OFD_SETLK' in line for line in text.splitlines())
    assert not list(ipc.iterdir())
    return json.loads(child.stdout)


@pytest.mark.skipif(not shutil.which('strace') or sys.platform!='linux',reason='Linux strace integration')
def test_export_engine_lock_is_on_private_copy(tmp_path,monkeypatch):
    project=tmp_path/'p';db=project/'.olav/databases/main.duckdb';db.parent.mkdir(parents=True)
    monkeypatch.setenv('OLAV_HOME',str(project))
    with duckdb.connect(str(db)) as c:
        c.execute('CREATE SCHEMA netops')
        c.execute('CREATE TABLE netops.parsed_outputs(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR,parsed_data JSON)')
        c.execute("INSERT INTO netops.parsed_outputs VALUES ('s1','one','show version','[]')")
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    result=trace_call(tmp_path,project,'analyzer','export_query',{'project':str(project),'export_id':'copy','source':'parsed_outputs','snapshot_id':'s1'})
    assert result['status']=='complete' and result['total_rows']==1
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before


@pytest.mark.skipif(not shutil.which('strace') or sys.platform!='linux',reason='Linux strace integration')
def test_reparse_engine_lock_is_on_private_copy(tmp_path,monkeypatch):
    from test_parser_lifecycle import stage,validate,SCOPE,COMMAND
    project=tmp_path/'p';project.mkdir();monkeypatch.setenv('OLAV_HOME',str(project))
    stage(project);validate(project)
    db=project/'.olav/databases/main.duckdb';db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as c:
        c.execute('CREATE SCHEMA netops')
        c.execute('CREATE TABLE netops.raw_output_store(snapshot_id VARCHAR,device_name VARCHAR,command VARCHAR,raw_output VARCHAR,platform VARCHAR)')
        c.execute("INSERT INTO netops.raw_output_store VALUES ('s1','one',?,'widget alpha','cisco_ios')",[COMMAND])
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    result=trace_call(tmp_path,project,'learner','reparse_snapshot',{'project':str(project),'snapshot_id':'s1',**SCOPE,'version':'1','output':'derived.json'})
    assert result['status']=='ok' and hashlib.sha256(db.read_bytes()).hexdigest()==before
