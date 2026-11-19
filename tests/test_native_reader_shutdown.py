"""Actual dedicated-reader scheduler cleanup and public caller isolation."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux native-reader contract')
def test_explicit_reader_cleanup_joins_default_scheduler(tmp_path):
    # Exercise the real extension and an explicit read, not a mock close method.
    code = '''
import json,os
from pathlib import Path
import duckdb
from olav_skills.native_snapshot import close_native_reader
with duckdb.connect(':memory:', config={'threads':1}) as conn:
    rows=conn.execute('SELECT 17').fetchall()
threads_before=len(list(Path('/proc/self/task').iterdir()))
close_native_reader()
threads_after=len(list(Path('/proc/self/task').iterdir()))
print(json.dumps({'rows':rows,'before':threads_before,'after':threads_after}))
'''
    proc = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                          env={**os.environ, 'OLAV_HOME':str(tmp_path)},
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    result=json.loads(proc.stdout)
    assert result['rows']==[[17]]
    assert result['after']==1 and result['before']>=result['after'], result


def test_public_reader_api_preserves_caller_default_connection(tmp_path, monkeypatch):
    import duckdb
    from olav_skills.query_recipes import run_investigation
    monkeypatch.setenv('OLAV_HOME',str(tmp_path))
    duckdb.sql('CREATE TEMP TABLE caller_shutdown_guard AS SELECT 23 AS value')
    try:
        result=run_investigation({'recipe':'inventory','snapshot_id':'missing'})
        assert result['status']=='error', result
        assert duckdb.sql('SELECT value FROM caller_shutdown_guard').fetchall()==[(23,)]
    finally:
        duckdb.sql('DROP TABLE caller_shutdown_guard')
