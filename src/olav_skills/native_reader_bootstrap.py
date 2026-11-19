"""Own the default DuckDB connection only in allowlisted fresh exec readers.

The low-level preseed prevents package type initialization from creating a
multi-thread default scheduler. API/settings gates are checked at runtime; only
DuckDB 1.5.6 has been exercised here. This is not a fix for allocator internals.
"""
from __future__ import annotations
import importlib
import os
from pathlib import Path
import re
import sys
from typing import NamedTuple

_WORKER_FLAGS = ('-B', '-P', '-s')


class _ReaderLaunch(NamedTuple):
    flags: tuple[str, ...]
    mode: tuple[str, ...]
    variable_args: int
    address_space_limit: int | None = 768 * 1024 * 1024


_LAUNCH_PROFILES = {
    'olav_skills.ospf_analysis': _ReaderLaunch(_WORKER_FLAGS, ('--worker',), 2),
    'olav_skills.evidence_readiness': _ReaderLaunch(_WORKER_FLAGS, ('--worker',), 2),
    'olav_skills.metric_baselines': _ReaderLaunch(_WORKER_FLAGS, ('--worker',), 2),
    'olav_skills.netops.core.diff.configs': _ReaderLaunch(('-B',), (), 1),
    'olav_skills.parser_lifecycle': _ReaderLaunch(('-B',), ('source',), 0),
    'olav_skills.counter_deltas': _ReaderLaunch(('-B',), (), 2),
    'olav_skills.route_changes': _ReaderLaunch(('-B',), ('--worker',), 2),
    'olav_skills.topology_changes': _ReaderLaunch(_WORKER_FLAGS, ('--worker',), 2),
    'olav_skills.topology_paths': _ReaderLaunch(_WORKER_FLAGS, ('--worker',), 2),
    'olav_skills.bounded_export': _ReaderLaunch(('-B',), (), 0),
    # Investigation already bounds the source connection without a process AS cap.
    'olav_skills.query_recipes': _ReaderLaunch(_WORKER_FLAGS, ('--worker',), 2, address_space_limit=None),
}
_SETTINGS={'threads':1,'memory_limit':'128MiB','enable_external_access':False,
           'autoload_known_extensions':False,'autoinstall_known_extensions':False,
           'allow_unsigned_extensions':False,'temp_directory':'',
           'max_temp_directory_size':'0B','lock_configuration':True}
_state=None

class NativeReaderBootstrapError(RuntimeError):
    """Fixed safe code; never include runtime data or environment values."""


def _reject():
    raise NativeReaderBootstrapError('native_reader_bootstrap_rejected')


def _identity(module):
    """Bind CPython's preserved launch, live main module and kernel argv."""
    if sys.platform != 'linux' or module not in _LAUNCH_PROFILES:
        _reject()
    launch = _LAUNCH_PROFILES[module]
    args = sys.argv[1:]
    main = sys.modules.get('__main__')
    spec = getattr(main, '__spec__', None)
    main_file = getattr(main, '__file__', None)
    expected_file = Path(__file__).parent.joinpath(*module.split('.')[1:]).with_suffix('.py').resolve()
    orig = getattr(sys, 'orig_argv', None)
    if (not sys.argv or len(args) != len(launch.mode) + launch.variable_args
        or tuple(args[:len(launch.mode)]) != launch.mode
        or not isinstance(orig, list) or orig[1:] != [*launch.flags, '-m', module, *args]
        or getattr(spec, 'name', None) != module or not isinstance(main_file, str)
        or Path(main_file).resolve() != expected_file or Path(sys.argv[0]).resolve() != expected_file
        or Path(orig[0]).resolve() != Path(sys.executable).resolve()):
        _reject()
    # orig_argv itself can be assigned by arbitrary in-process Python. The
    # kernel command line still distinguishes real -m exec from runpy/-c code.
    try:
        with open('/proc/self/cmdline','rb') as fh:
            raw=fh.read(16385)
        kernel=[os.fsdecode(value) for value in raw.rstrip(b'\0').split(b'\0')]
    except OSError:
        _reject()
    if len(raw)>16384 or kernel!=orig:
        _reject()
    return os.getpid(),module


def _api(module,names):
    if any(not callable(getattr(module,name,None)) for name in names):
        _reject()


def _settings(conn):
    names=tuple(_SETTINGS)
    values=conn.execute('SELECT '+','.join("system.main.current_setting(?)" for _ in names),list(names)).fetchone()
    if values is None or len(values)!=len(names):
        _reject()
    actual=dict(zip(names,values))
    # DuckDB reports normalized binary memory units rather than the input token.
    if (actual['threads']!=1 or actual['memory_limit']!='128.0 MiB'
        or actual['max_temp_directory_size']!='0 bytes'
        or any(actual[name] is not value for name,value in _SETTINGS.items() if type(value) is bool)
        or actual['temp_directory']!=''):
        _reject()


def _verify(low,public,conn):
    _api(low,('connect','set_default_connection','default_connection'))
    _api(public,('default_connection','close'))
    if low.default_connection() is not conn or public.default_connection() is not conn:
        _reject()
    _settings(conn)


def validate_native_reader_identity(module):
    """Read-only launch check before a worker mutates process resource limits."""
    return _identity(module)


def owns_native_reader(module):
    """Do not close a caller's preexisting default after identity rejection."""
    if _state is None:
        return False
    try:
        return _state['identity']==_identity(module)
    except NativeReaderBootstrapError:
        return False


def bootstrap_native_reader(module):
    """Seed once after any required process cap and before either DuckDB import."""
    global _state
    identity=_identity(module)
    limit = _LAUNCH_PROFILES[module].address_space_limit
    if limit is not None:
        import resource
        soft, _ = resource.getrlimit(resource.RLIMIT_AS)
        if soft == resource.RLIM_INFINITY or not 0 < soft <= limit:
            _reject()
    if _state is not None:
        if _state['identity']!=identity:
            _reject()
        try:
            _verify(_state['low'],_state['public'],_state['connection'])
        except Exception:
            try:
                _state['connection'].close()
            finally:
                _reject()
        return _state['public']
    if 'duckdb' in sys.modules or '_duckdb' in sys.modules:
        _reject()
    # No default connection is replaced on a preload/identity refusal. After
    # initialization begins, every failure closes the newly owned connection.
    conn=None
    try:
        low=importlib.import_module('_duckdb')
        _api(low,('connect','set_default_connection','default_connection'))
        version=getattr(low,'__version__',None)
        if not isinstance(version,str) or re.fullmatch(r'1\.(?:[6-9]|[1-9][0-9]+)\.\d+|1\.5\.(?:[6-9]|[1-9][0-9]+)',version) is None:
            _reject()
        conn=low.connect(':memory:',config=dict(_SETTINGS))
        low.set_default_connection(conn)
        public=importlib.import_module('duckdb')
        if getattr(public,'__version__',None)!=version:
            _reject()
        _verify(low,public,conn)
        _state={'identity':identity,'low':low,'public':public,'connection':conn}
        return public
    except Exception:
        if conn is not None:
            try:
                conn.close()
            finally:
                _reject()
        _reject()
