"""Prove the shared native source is copied from a locked file descriptor."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

import duckdb
import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native source")


@pytest.fixture
def projects(tmp_path, monkeypatch):
    project = tmp_path / "project"
    monkeypatch.setenv("OLAV_HOME", str(project))
    db = project / ".olav/databases/main.duckdb"
    db.parent.mkdir(parents=True)
    with duckdb.connect(str(db)) as conn:
        conn.execute("CREATE TABLE sample AS SELECT 'authorized' AS value")
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    return project, db, scratch


def _open(project, scratch, **kwargs):
    from olav_skills.native_snapshot import open_native_snapshot

    return open_native_snapshot(project, scratch, **{
        "max_bytes": 8 * 1024 * 1024, "deadline": time.monotonic() + 10, **kwargs,
    })


def _code(exc, error):
    assert exc.value.code == error


def test_readonly_copy_version_and_cleanup(projects):
    project, db, scratch = projects
    original = db.read_bytes()
    with _open(project, scratch) as (conn, source):
        assert conn.execute("SELECT value FROM sample").fetchone() == ("authorized",)
        assert source["sha256"] == hashlib.sha256(original).hexdigest()
        assert source["bytes"] == len(original)
        assert source["path"] == ".olav/databases/main.duckdb"
        assert source["backend"] == "native_duckdb_locked_copy"
        paths = list(scratch.iterdir())
        assert len(paths) == 1
        assert paths[0] != db and paths[0].read_bytes() == original
        with pytest.raises(duckdb.Error):
            conn.execute("CREATE TABLE forbidden(i INTEGER)")
        with pytest.raises(duckdb.Error):
            conn.execute("SET enable_external_access=true")
    assert not list(scratch.iterdir())
    assert db.read_bytes() == original


def test_ofd_lock_conflicts_with_independent_duckdb_writer(projects):
    project, db, scratch = projects
    writer = "import duckdb,sys; duckdb.connect(sys.argv[1]).close()"
    with _open(project, scratch):
        fd = os.open(db, os.O_RDONLY)
        os.close(fd)  # Closing this descriptor must leave the OFD lock held.
        denied = subprocess.run([sys.executable, "-B", "-c", writer, str(db)],
                                capture_output=True, text=True, timeout=5)
        assert denied.returncode != 0 and "lock" in denied.stderr.lower()
    allowed = subprocess.run([sys.executable, "-B", "-c", writer, str(db)],
                             capture_output=True, text=True, timeout=5)
    assert allowed.returncode == 0, allowed.stderr


def test_independent_duckdb_writer_blocks_the_source(projects):
    from olav_skills.native_snapshot import NativeSnapshotError

    project, db, scratch = projects
    code = """
import duckdb, sys
with duckdb.connect(sys.argv[1]):
    print('ready', flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen([sys.executable, "-B", "-c", code, str(db)],
                          stdout=subprocess.PIPE, stdin=subprocess.PIPE, text=True) as writer:
        try:
            assert writer.stdout.readline() == "ready\n"
            with pytest.raises(NativeSnapshotError) as exc:
                with _open(project, scratch):
                    pytest.fail("active writer was bypassed")
            _code(exc, "source_locked")
            assert not list(scratch.iterdir())
        finally:
            writer.communicate(timeout=5)


def test_source_swap_cannot_feed_unchecked_path_to_engine(projects, monkeypatch):
    from olav_skills import native_snapshot

    project, db, scratch = projects
    other = project / "other.duckdb"
    with duckdb.connect(str(other)) as conn:
        conn.execute("CREATE TABLE sample AS SELECT 'unrelated' AS value")
    other_original = other.read_bytes()
    original_copy = native_snapshot._copy_source
    placeholder = project / "renamed-authorized.duckdb"

    def swap(*args):
        db.rename(placeholder)
        db.symlink_to(other)
        return original_copy(*args)

    monkeypatch.setattr(native_snapshot, "_copy_source", swap)
    with pytest.raises(native_snapshot.NativeSnapshotError) as exc:
        with _open(project, scratch):
            pytest.fail("open after source path replacement")
    _code(exc, "source_changed")
    assert other.read_bytes() == other_original
    assert not list(scratch.iterdir())


def test_engine_snapshot_change_fails_and_cleans(projects):
    from olav_skills.native_snapshot import NativeSnapshotError

    project, db, scratch = projects
    original = db.read_bytes()
    with pytest.raises(NativeSnapshotError) as exc:
        with _open(project, scratch) as (conn, _):
            (copy,) = scratch.iterdir()
            assert conn.execute("SELECT value FROM sample").fetchone() == ("authorized",)
            copy.chmod(0o600)
            with copy.open("r+b") as stream:
                stream.write(b"changed")
    _code(exc, "snapshot_changed")
    assert not list(scratch.iterdir())
    assert db.read_bytes() == original


@pytest.mark.parametrize("suffix", [".wal", ".wal.checkpoint", ".wal.recovery"])
def test_refuses_source_recovery_sidecars(projects, suffix):
    from olav_skills.native_snapshot import NativeSnapshotError

    project, db, scratch = projects
    path = db.with_name(db.name + suffix)
    path.write_bytes(b"synthetic")
    with pytest.raises(NativeSnapshotError) as exc:
        with _open(project, scratch):
            pytest.fail("source recovery sidecar was bypassed")
    _code(exc, "source_has_wal")
    assert path.read_bytes() == b"synthetic"
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("options", [{"max_bytes": 1}, {"deadline": 0.0}])
def test_source_copy_has_a_byte_budget_and_deadline(projects, options):
    from olav_skills.native_snapshot import NativeSnapshotError

    project, db, scratch = projects
    with pytest.raises(NativeSnapshotError) as exc:
        with _open(project, scratch, **options):
            pytest.fail("source budget was bypassed")
    _code(exc, "source_budget_exceeded" if "max_bytes" in options else "deadline_exceeded")
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("fixture", ["nonempty", "permissions", "symlink", "ancestor_symlink"])
def test_refuses_untrusted_scratch_before_copy(projects, fixture):
    from olav_skills.native_snapshot import NativeSnapshotError

    project, db, scratch = projects
    if fixture == "nonempty":
        (scratch / "other").touch()
    if fixture == "permissions":
        scratch.chmod(0o755)
    if fixture == "symlink":
        scratch.rmdir()
        other = scratch.with_name("other")
        other.mkdir(mode=0o700)
        scratch.symlink_to(other, target_is_directory=True)
    if fixture == "ancestor_symlink":
        other = scratch.with_name("other")
        other.symlink_to(scratch.parent, target_is_directory=True)
        scratch = other / scratch.name
    with pytest.raises(NativeSnapshotError) as exc:
        with _open(project, scratch):
            pytest.fail("unsafe scratch was accepted")
    _code(exc, "unsafe_scratch")


@pytest.mark.parametrize("options", [
    {"deadline": float("nan")}, {"deadline": float("inf")}, {"deadline": "soon"},
    {"max_bytes": -1}, {"max_bytes": True}, {"memory_limit": "-1"},
    {"memory_limit": "0MiB"}, {"threads": True}, {"threads": -1},
])
def test_invalid_limits_fail_closed_before_creating_copy(projects, options):
    from olav_skills.native_snapshot import NativeSnapshotError

    project, _, scratch = projects
    with pytest.raises(NativeSnapshotError) as exc:
        with _open(project, scratch, **options):
            pytest.fail("invalid limit accepted")
    _code(exc, "invalid_limits")
    assert not list(scratch.iterdir())
