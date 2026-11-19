from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest


SOURCE_DIR = Path("exports/evidence_sources")
BUNDLE_DIR = Path("exports/evidence_bundles")
REPORT = str(SOURCE_DIR / "network-review.md")
EVIDENCE = str(SOURCE_DIR / "2026-11-18-evidence.json")
ROWS = str(SOURCE_DIR / "returned-rows.csv")
REPORT_BYTES = b"# Recorded review\n\nThe author asserted a collection time.\n"
EVIDENCE_BYTES = (
    b'{"schema":"synthetic/1","collected_at":"2026-11-18",'
    b'"meta":{"truncation":{"rows":{"truncated":true}}},"rows":[{"host":"r1"}]}\n'
)
ROWS_BYTES = b"hostname,role\nr1,core\n"


def cli(project, operation, code=0, bundle="review.zip", **fields):
    request = {"operation": operation, "project": str(project), "bundle": bundle, **fields}
    return request_cli(project, json.dumps(request).encode("utf-8"), code)


def request_cli(project, payload, code):
    process = subprocess.run(
        [sys.executable, "-m", "olav_skills", "run", "reporter", "evidence_bundle"],
        input=payload, capture_output=True, env={**os.environ, "OLAV_HOME": str(project)},
        check=False, timeout=15,
    )
    assert process.returncode == code, (process.stdout, process.stderr)
    assert process.stderr == b""
    return json.loads(process.stdout)


@pytest.fixture
def project(tmp_path):
    for directory in (SOURCE_DIR, BUNDLE_DIR):
        (tmp_path / directory).mkdir(parents=True)
    for path, data in ((REPORT, REPORT_BYTES), (EVIDENCE, EVIDENCE_BYTES), (ROWS, ROWS_BYTES)):
        (tmp_path / path).write_bytes(data)
    return tmp_path


def rewrite(path, items, compression=0):
    """Write a fresh outer ZIP with independent CRC values."""
    with ZipFile(path, "w", allowZip64=False, compression=compression) as bundle:
        for name, payload in items:
            info = ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            bundle.writestr(info, payload, compress_type=compression)


def contents(path):
    with ZipFile(path) as bundle:
        return [(name, bundle.read(name)) for name in bundle.namelist()]


def test_manifest_has_exact_selected_bytes_unknown_time_and_independent_verify(project, tmp_path):
    result = cli(project, "create", sources=[REPORT, EVIDENCE, ROWS])
    bundle = project / result["bundle"]
    assert result["integrity"] == "not_checked"
    assert bundle.stat().st_mode & 0o777 == 0o600
    entries = dict(contents(bundle))
    manifest = json.loads(entries.pop("manifest.json"))
    assert manifest == result["manifest"]
    assert result["manifest_sha256"] == hashlib.sha256(contents(bundle)[0][1]).hexdigest()
    assert manifest["schema"] == "olav.evidence-bundle/v1"
    assert manifest["generator"]["distribution"] == "olav-skills"
    assert manifest["generator"]["version"]
    assert manifest["source_scope"] == "selected_project_files"
    assert manifest["source_claims"] == "unverified"
    assert manifest["collection_time"] == {"status": "unknown", "value": None}
    assert manifest["coverage"] == "partial"
    assert str(project).encode() not in bundle.read_bytes()
    expected = {REPORT: REPORT_BYTES, EVIDENCE: EVIDENCE_BYTES, ROWS: ROWS_BYTES}
    records = manifest["files"]
    assert [row["project_relative_path"] for row in records] == sorted(expected)
    for row in records:
        name = row["project_relative_path"]
        assert entries.pop(row["entry"]) == expected[name]
        assert row["bytes"] == len(expected[name])
        assert row["sha256"] == hashlib.sha256(expected[name]).hexdigest()
        assert row["coverage_marker"] == ("partial_marker_present" if name == EVIDENCE else "unknown")
    assert not entries

    # The receiving project has no source staging directory and needs none.
    receiving = tmp_path / "receiver"
    (receiving / BUNDLE_DIR).mkdir(parents=True)
    (receiving / result["bundle"]).write_bytes(bundle.read_bytes())
    verified = cli(receiving, "verify", expected_manifest_sha256=result["manifest_sha256"])
    assert verified["integrity"] == "verified"
    assert verified["manifest_pin"] == "matched"
    assert verified["freshness"] == "not_checked"
    assert verified["manifest"] == manifest
    duplicate = cli(project, "create", bundle="second.zip", sources=[ROWS, EVIDENCE, REPORT])
    assert (project / duplicate["bundle"]).read_bytes() == bundle.read_bytes()


def test_recheck_requires_explicit_scope_and_marks_known_stale_without_rewriting(project):
    created = cli(project, "create", sources=[REPORT, EVIDENCE])
    archive = project / created["bundle"]
    original = archive.read_bytes(), archive.stat().st_mtime_ns
    assert cli(project, "recheck", 1)["error_type"] == "source_count_out_of_bounds"
    mismatch = cli(project, "recheck", 1, sources=[REPORT, EVIDENCE, ROWS])
    assert mismatch["error_type"] == "source_scope_mismatch"
    assert mismatch["integrity"] == "verified"
    assert mismatch["freshness"] == "not_checked"
    assert cli(project, "recheck", sources=[EVIDENCE, REPORT])["freshness"] == "unchanged"
    # A newly staged but unselected file does not affect the original scope.
    added = project / SOURCE_DIR / "added.md"
    added.write_text("new explicit scope would require a new bundle\n")
    assert cli(project, "recheck", sources=[EVIDENCE, REPORT])["freshness"] == "unchanged"
    (project / REPORT).write_bytes(REPORT_BYTES.replace(b"time", b"date"))
    (project / EVIDENCE).unlink()
    checked = cli(project, "recheck", sources=[EVIDENCE, REPORT])
    assert checked["integrity"] == "verified"
    assert checked["freshness"] == "stale"
    assert checked["sources"] == [
        {"project_relative_path": EVIDENCE, "state": "missing"},
        {"project_relative_path": REPORT, "state": "changed"},
    ]
    updated = cli(project, "recheck", sources=[REPORT, EVIDENCE],
                  expected_manifest_sha256=created["manifest_sha256"])
    assert updated["freshness"] == "stale"
    assert (archive.read_bytes(), archive.stat().st_mtime_ns) == original
    assert cli(project, "verify")["integrity"] == "verified"


def test_recheck_unsafe_replacement_is_unknown_and_never_matches_external_bytes(project):
    outside = project / "unselected.md"
    outside.write_bytes(REPORT_BYTES)
    cli(project, "create", sources=[REPORT, EVIDENCE])
    (project / REPORT).unlink()
    (project / REPORT).symlink_to(outside)
    result = cli(project, "recheck", sources=[REPORT, EVIDENCE])
    assert result["freshness"] == "unknown"
    assert result["sources"][-1] == {"project_relative_path": REPORT, "state": "unavailable"}
    (project / EVIDENCE).write_bytes(b"changed size")
    result = cli(project, "recheck", sources=[REPORT, EVIDENCE])
    assert result["freshness"] == "stale"
    assert result["sources"][-1]["state"] == "unavailable"


@pytest.mark.parametrize("mutation", [
    "changed_payload", "missing_payload", "unexpected_payload", "duplicate_entry",
    "traversal_entry", "compressed_entry", "bad_claim", "invalid_archive", "oversize_payload",
])
def test_verify_rejects_corrupt_or_unsafe_archives(project, mutation):
    created = cli(project, "create", sources=[REPORT])
    archive = project / created["bundle"]
    items = contents(archive)
    compression = 0
    if mutation == "changed_payload":
        items[1] = (items[1][0], b"plausible changed body")
    elif mutation == "missing_payload":
        items.pop()
    elif mutation == "unexpected_payload":
        items.append(("extra.md", b"surprise"))
    elif mutation == "duplicate_entry":
        items.append(items[1])
    elif mutation == "traversal_entry":
        items[1] = ("../../escape.md", items[1][1])
    elif mutation == "compressed_entry":
        compression = ZIP_DEFLATED
    elif mutation == "bad_claim":
        manifest = json.loads(items[0][1])
        manifest["coverage"] = "complete"
        items[0] = ("manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    elif mutation == "invalid_archive":
        archive.write_bytes(archive.read_bytes()[:-10])
    elif mutation == "oversize_payload":
        items[1] = (items[1][0], b"x" * (1024 * 1024 + 1))
    if mutation == "duplicate_entry":
        with pytest.warns(UserWarning, match="Duplicate name"):
            rewrite(archive, items)
    elif mutation != "invalid_archive":
        rewrite(archive, items, compression=compression)
    result = cli(project, "verify", 1)
    assert result["integrity"] == "failed"
    assert result["freshness"] == "not_checked"
    assert "manifest" not in result
    assert set(p.name for p in (project / BUNDLE_DIR).iterdir()) == {"review.zip"}


def test_coordinated_rewrite_requires_out_of_band_pin(project):
    created = cli(project, "create", sources=[REPORT])
    archive = project / created["bundle"]
    items = contents(archive)
    manifest = json.loads(items[0][1])
    new_payload = b"# Different report\n"
    manifest["files"][0].update(bytes=len(new_payload), sha256=hashlib.sha256(new_payload).hexdigest())
    updated = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    rewrite(archive, [(items[0][0], updated), (items[1][0], new_payload)])
    assert cli(project, "verify")["manifest_pin"] == "not_supplied"
    pinned = cli(project, "verify", 1, expected_manifest_sha256=created["manifest_sha256"])
    assert pinned["integrity"] == "failed"
    assert pinned["error_type"] == "manifest_digest_mismatch"


@pytest.mark.parametrize("source", [
    "/absolute/source.md", "exports/evidence_sources/../outside.md",
    "exports/other/report.md", "exports/evidence_sources/.env",
    "exports/evidence_sources/private/report.md", "exports/evidence_sources/report.private.md",
    "exports/evidence_sources/passwords.csv", "exports/evidence_sources/config.txt",
    "exports/evidence_sources/main.duckdb", "exports/evidence_sources/**",
])
def test_reject_source_path_before_open(project, source):
    assert cli(project, "create", 1, sources=[source])["error_type"] == "invalid_source_path"
    assert list((project / BUNDLE_DIR).iterdir()) == []


@pytest.mark.parametrize("bad_type", ["leaf_symlink", "folder_symlink", "hardlink", "fifo", "directory"])
def test_refuse_links_and_nonregular_files(project, bad_type):
    original = project / REPORT
    candidate = project / SOURCE_DIR / "alias.md"
    source = str(candidate.relative_to(project))
    if bad_type == "leaf_symlink":
        candidate.symlink_to(original)
    elif bad_type == "folder_symlink":
        candidate.symlink_to(original.parent, target_is_directory=True)
        source += "/network-review.md"
    elif bad_type == "hardlink":
        os.link(original, candidate)
    elif bad_type == "fifo":
        os.mkfifo(candidate)
    else:
        candidate.mkdir()
    result = cli(project, "create", 1, sources=[source])
    assert result["error_type"] in {"unsafe_file_type", "project_or_file_unavailable"}
    assert list((project / BUNDLE_DIR).iterdir()) == []


def test_reject_project_and_destination_symlinks(project):
    target = project / "linkedproject"
    target.symlink_to(project, target_is_directory=True)
    assert cli(target, "create", 1, sources=[REPORT])["error_type"] == "project_or_file_unavailable"
    output = project / BUNDLE_DIR
    output.rmdir()
    output.symlink_to(project / SOURCE_DIR, target_is_directory=True)
    assert cli(project, "create", 1, sources=[REPORT])["error_type"] == "project_or_file_unavailable"
    assert not (project / SOURCE_DIR / "review.zip").exists()


@pytest.mark.parametrize("suffix,body,error", [
    (".md", b"x" * (1024 * 1024 + 1), "file_byte_budget_exceeded"),
    (".csv", b"r1,core\n" * 20_000, "source_line_budget_exceeded"),
    (".md", b"# Invalid\x00text", "source_contains_control_characters"),
    (".md", b"\xff\xfe", "source_is_not_utf8"),
    (".json", b'{"a":1,"a":2}', "duplicate_json_key"),
    (".json", b'{"a":[' + b"0," * 30_000 + b"0]}", "json_node_budget_exceeded"),
    (".json", br'{"metadata":{"pass\u0077ord":"synthetic-fake"}}', "sensitive_content_refused"),
    (".csv", b'hostname,"api_key"\nr1,synthetic-fake\n', "sensitive_content_refused"),
    (".md", b"enable secret synthetic-fake\n", "sensitive_content_refused"),
])
def test_refuse_invalid_or_budget_exceeding_source_bodies(project, suffix, body, error):
    candidate = project / SOURCE_DIR / ("candidate" + suffix)
    candidate.write_bytes(body)
    assert cli(project, "create", 1, sources=[str(candidate.relative_to(project))])["error_type"] == error
    assert list((project / BUNDLE_DIR).iterdir()) == []


def test_request_count_total_budget_and_existing_bundle_are_fail_closed(project):
    assert cli(project, "create", 1, sources=[REPORT, REPORT])["error_type"] == "duplicate_source_path"
    assert cli(project, "create", 1, sources=[REPORT] * 17)["error_type"] == "source_count_out_of_bounds"
    oversized = b" " * (16 * 1024 + 1)
    assert request_cli(project, oversized, 1)["error_type"] == "request_byte_budget_exceeded"
    absolute = cli(project, "create", 1, bundle="../review.zip", sources=[REPORT])
    assert absolute["error_type"] == "invalid_bundle_name"
    paths = []
    for index in range(9):
        candidate = project / SOURCE_DIR / f"record-{index}.md"
        candidate.write_bytes(b"x" * (1024 * 1024))
        paths.append(str(candidate.relative_to(project)))
    assert cli(project, "create", 1, sources=paths)["error_type"] == "file_byte_budget_exceeded"
    assert list((project / BUNDLE_DIR).iterdir()) == []

    cli(project, "create", sources=[REPORT])
    archive = project / BUNDLE_DIR / "review.zip"
    original = archive.read_bytes()
    assert cli(project, "create", 1, sources=[ROWS])["error_type"] == "bundle_already_exists"
    assert archive.read_bytes() == original
    assert list((project / BUNDLE_DIR).iterdir()) == [archive]


def test_concurrent_create_publishes_exactly_one_complete_archive(project):
    def create(path):
        payload = {"operation": "create", "project": str(project), "bundle": "review.zip", "sources": [path]}
        child = subprocess.run([sys.executable, "-m", "olav_skills", "run", "reporter", "evidence_bundle"],
                               input=json.dumps(payload).encode(), capture_output=True, check=False,
                               env={**os.environ, "OLAV_HOME": str(project)}, timeout=15)
        return child.returncode, json.loads(child.stdout)

    with ThreadPoolExecutor(max_workers=2) as jobs:
        results = sorted(jobs.map(create, [REPORT, EVIDENCE]))
    assert [status for status, _ in results] == [0, 1]
    assert results[1][1]["error_type"] == "bundle_already_exists"
    verified = cli(project, "verify", expected_manifest_sha256=results[0][1]["manifest_sha256"])
    assert verified["integrity"] == "verified"
    assert list((project / BUNDLE_DIR).iterdir()) == [project / BUNDLE_DIR / "review.zip"]
