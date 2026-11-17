"""Host-selected DuckLake identity and installed-source attestation (stdlib only).

This verifies ordinary source provenance, not hostile interpreters/import hooks
or forged bytecode caches. Runtime files require trusted host deployment.
"""
from __future__ import annotations

from contextlib import contextmanager
import functools
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import threading
from types import MappingProxyType


_KEYS = ('OLAV_WRITER_DOMAIN_MANIFEST', 'OLAV_WRITER_DOMAIN_SHA256', 'OLAV_WRITER_RUNTIME_SHA256')
_DIST = 'olav-skills-ducklake-runtime'
_VERSION = '0.28.0rc4'
_INVENTORY = 'olav_skills/_writer_runtime_inventory.json'
_ROLE = 'importer/ingest_snapshot'
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_binding = None
_entry = None
_mutex = threading.RLock()


class WriterHostError(ValueError):
    """Finite, credential-free host wiring error."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code='RUNTIME_IDENTITY_INVALID'):
    raise WriterHostError(code)


def _safe(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except WriterHostError:
            raise
        except Exception as exc:
            if type(exc).__module__ == 'olav_skills.core.pg_writer':
                raise WriterHostError(exc.code) from None
            if type(exc).__module__ == 'olav_skills.core.writer_domain':
                raise WriterHostError('WRITER_DOMAIN_' + exc.code) from None
            raise WriterHostError('RUNTIME_IDENTITY_INVALID') from None
    return wrapped


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail()
        result[key] = value
    return result


def _read(path, limit=16 * 1024 * 1024):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        _fail()
    # Reject links in every component, even ones resolving to the same target.
    if any(p.is_symlink() for p in (path, *path.parents)):
        _fail()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            _fail()
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read(limit + 1)
        live = path.stat(follow_symlinks=False)
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) > limit or identity != (live.st_dev, live.st_ino, live.st_size, live.st_mtime_ns, live.st_ctime_ns):
            _fail()
        return raw, identity
    finally:
        os.close(fd)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _relative(name):
    if type(name) is not str or '\\' in name:
        _fail()
    p = PurePosixPath(name)
    if p.is_absolute() or p.as_posix() != name or '..' in p.parts or not p.parts or p.parts[0] not in ('olav_skills',):
        _fail()
    return p


def _tree(root):
    files = {}
    for package in ('olav_skills',):
        base = root / package
        if not base.is_dir() or base.is_symlink():
            _fail()
        for path in sorted(base.rglob('*')):
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                _fail()
            if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo') or rel == _INVENTORY:
                continue
            if path.is_dir():
                continue
            if not path.is_file():
                _fail()
            files[rel] = path
            if len(files) > 20000:
                _fail()
    return files


def _normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def _runtime(pin):
    if type(pin) is not str or not _HEX.fullmatch(pin):
        _fail()
    found = []
    for dist in metadata.distributions():
        name = _normalized(dist.metadata.get('Name', ''))
        if name in ('olav', 'olav-netops'):
            _fail()
        if name == _DIST:
            found.append(dist)
    if len(found) != 1:
        _fail()
    dist = found[0]
    if dist.version not in ('0.28.0rc3', _VERSION):
        _fail()
    direct = dist.read_text('direct_url.json')
    if direct and json.loads(direct).get('dir_info', {}).get('editable'):
        _fail()
    root = Path(dist.locate_file('')).absolute()
    if root.resolve() != root:
        _fail()
    recorded = {str(p) for p in (dist.files or ())}
    raw, _ = _read(root / _INVENTORY, 4 * 1024 * 1024)
    if _sha(raw) != pin or _INVENTORY not in recorded:
        _fail()
    inventory = json.loads(raw, object_pairs_hook=_pairs)
    if (type(inventory) is not dict or set(inventory) != {'schema', 'distribution', 'version', 'files', 'entrypoints'}
            or inventory['schema'] != 'olav.writer-runtime/v1' or inventory['distribution'] != _DIST
            or inventory['version'] != dist.version or type(inventory['files']) is not dict
            or type(inventory['entrypoints']) is not dict or set(inventory['entrypoints']) != {_ROLE}
            or not isinstance(inventory['entrypoints'][_ROLE], str) or not _HEX.fullmatch(inventory['entrypoints'][_ROLE])):
        _fail()
    if raw != (json.dumps(inventory, sort_keys=True, separators=(',', ':')) + '\n').encode():
        _fail()
    expected = inventory['files']
    actual = _tree(root)
    if set(actual) != set(expected) or not expected or not set(expected) <= recorded:
        _fail()
    total = 0
    for name, digest in expected.items():
        _relative(name)
        if type(digest) is not str or not _HEX.fullmatch(digest):
            _fail()
        content, _ = _read(actual[name])
        total += len(content)
        if total > 128 * 1024 * 1024 or _sha(content) != digest:
            _fail()
    for name, module in tuple(sys.modules.items()):
        if name.split('.')[0] not in ('olav_skills',) or module is None:
            continue
        file = getattr(module, '__file__', None)
        spec = getattr(module, '__spec__', None)
        if not file or not spec or spec.origin != file:
            _fail()
        path = Path(file).absolute()
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            _fail()
        if rel not in expected or path != root / rel or path.suffix != '.py':
            _fail()
        paths = getattr(module, '__path__', None)
        if paths is not None and tuple(map(Path, paths)) != (path.parent,):
            _fail()
    for package in ('olav_skills',):
        spec = importlib.util.find_spec(package)
        if not spec or spec.origin != str(root / package / '__init__.py'):
            _fail()
    return inventory, root


def has_domain_intent():
    return _binding is not None or any(key in os.environ for key in _KEYS)


@_safe
def configured_ingest_config():
    """Classify configured/native without fallback after host binding intent."""
    global _binding
    from olav_skills.core.ingest_connection import _host_config
    with _mutex:
        config = _host_config()
        if config is not None and config.get("version") == 2:
            if has_domain_intent():
                _fail("HOST_BINDING_INVALID")
            return config
        if config is None and not has_domain_intent():
            return None
        import olav_skills.core.writer_domain as domain
        home = os.environ.get('OLAV_HOME')
        pins = tuple(os.environ.get(k) for k in _KEYS)
        if not config or not home or not all(pins) or Path(home).as_posix() != str(Path(home).absolute()) or Path(home).resolve() != Path(home):
            _fail('HOST_BINDING_INVALID')
        if any(type(p) is not str or not _HEX.fullmatch(p) for p in pins[1:]):
            _fail('HOST_BINDING_INVALID')
        storage = Path(home) / '.olav/config/ingest-storage.json'
        raw, identity = _read(storage, 65536)
        # Pin exact bytes actually parsed rather than a previous config read.
        if json.loads(raw) != config:
            _fail('HOST_BINDING_INVALID')
        _runtime(pins[2])
        selected = (home, pins, raw, identity)
        if _binding is not None:
            if selected != _binding[0]:
                _fail('HOST_BINDING_INVALID')
            domain.preflight(_binding[1])
        else:
            cfg = domain.load_manifest(pins[0], expected_sha256=pins[1], expected_runtime_sha256=pins[2])
            _binding = (selected, cfg, MappingProxyType(dict(config)))
        return _binding[2]


@_safe
def assert_configured_writer_domain():
    """Assert ownership first; never acquire a lease for a factory caller."""
    from olav_skills.core.ingest_connection import _host_config
    config = _host_config()
    if config is not None and config.get("version") == 2:
        from olav_skills.core.pg_writer import assert_held
        return assert_held()
    import olav_skills.core.writer_domain as domain
    lease = domain.assert_writer_domain()
    config = configured_ingest_config()
    if config is None or _binding is None or lease.config != _binding[1]:
        _fail('DOMAIN_IDENTITY_INVALID')
    return lease


@contextmanager
def configured_writer_domain():
    config = configured_ingest_config()
    if config is not None and config.get("version") == 2:
        from olav_skills.core.pg_writer import acquire, active_lease, assert_held
        if active_lease() is not None:
            yield assert_held()
        else:
            with acquire() as lease:
                yield lease
        return
    if _binding is None:
        _fail('HOST_BINDING_INVALID')
    import olav_skills.core.writer_domain as domain
    try:
        with domain.writer_domain(_binding[1]) as lease:
            assert_configured_writer_domain()
            yield lease
    except domain.WriterDomainError as exc:
        raise WriterHostError('WRITER_DOMAIN_' + exc.code) from None


def _main_identity(path):
    main = sys.modules.get('__main__')
    spec = getattr(main, '__spec__', None)
    if (main is None or getattr(main, '__file__', None) != str(path)
            or (spec is not None and spec.origin != str(path))):
        _fail('DOMAIN_IDENTITY_INVALID')


@_safe
def verify_entrypoint(path, *, role):
    """Verify the current real wrapper frame; callers cannot mark prepared."""
    global _entry
    config = configured_ingest_config()
    if config is not None and config.get("version") == 2:
        if role != _ROLE:
            _fail("DOMAIN_IDENTITY_INVALID")
        return
    if role != _ROLE or configured_ingest_config() is None:
        _fail('DOMAIN_IDENTITY_INVALID')
    caller = sys._getframe(1)
    # _safe wrapper is one intervening frame.
    caller = caller.f_back
    actual = Path(path).absolute()
    if caller is None or caller.f_code.co_filename != str(actual) or caller.f_globals.get('__file__') != str(actual):
        _fail('DOMAIN_IDENTITY_INVALID')
    _main_identity(actual)
    inventory, _ = _runtime(_binding[0][1][2])
    raw, identity = _read(actual)
    if _sha(raw) != inventory['entrypoints'][role]:
        _fail('DOMAIN_IDENTITY_INVALID')
    _entry = (os.getpid(), threading.get_ident(), actual, identity, _sha(raw), _binding[0][1][2])


@_safe
def assert_importer_entrypoint():
    assert_configured_writer_domain()
    from olav_skills.core.ingest_connection import _host_config
    if (_host_config() or {}).get("version") == 2:
        return
    if _entry is None or _entry[:2] != (os.getpid(), threading.get_ident()):
        _fail('DOMAIN_IDENTITY_INVALID')
    _main_identity(_entry[2])
    raw, identity = _read(_entry[2])
    if identity != _entry[3] or _sha(raw) != _entry[4] or _binding[0][1][2] != _entry[5]:
        _fail('DOMAIN_IDENTITY_INVALID')


def _after_fork():
    global _entry, _mutex
    _entry = None
    _mutex = threading.RLock()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)
