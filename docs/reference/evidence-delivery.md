# Offline evidence bundles

Use `olav-skills run reporter evidence_bundle` in 0.2 with one JSON object on stdin.

## Prepare and verify a delivery

Stage only files approved for delivery in the existing
`exports/evidence_sources/` directory of an authorized local project. Use
UTF-8 JSON, CSV or Markdown. Review the source bodies and filenames for
sensitive data, and create the output directory `exports/evidence_bundles/`.

Run `olav-skills run reporter evidence_bundle` with this request. Use the
absolute path to your project and your approved project-relative files.

```json
{
  "operation": "create",
  "project": "/absolute/authorized-project",
  "bundle": "review.zip",
  "sources": [
    "exports/evidence_sources/evidence.json",
    "exports/evidence_sources/report.md"
  ]
}
```

The command creates a ZIP with `manifest.json` and one numbered entry per
source. The manifest lists the project-relative paths, original byte counts
and SHA-256 hashes, and the generating package version. The collection time
is `unknown` and the coverage is `partial` for the selected file scope.
JSON truncation markers, including `has_more`, `truncated` and bounded
excerpts, are surfaced when detected and retained in the original bytes.
The generated metadata omits the absolute project path and filesystem timestamps.

To verify independently, place the ZIP under the receiving project's
`exports/evidence_bundles/`. Send `"operation": "verify"` with its
`project` and `bundle`. Include `expected_manifest_sha256` if you received
the creation result's digest through a trusted separate channel. A verified
result confirms archive structure and content hashes. A digest match binds
the result to the known manifest. An unpinned ZIP and manifest can both be
replaced consistently. The original report's claims, true collection time,
source completeness and author identity are outside integrity verification.

## Recheck against selected files

Send `"operation": "recheck"` with the original request's `sources`,
`project`, and `bundle`. You can also pass `expected_manifest_sha256`.
A recheck verifies the ZIP and compares bytes at the named paths. The
caller-supplied source list must match the manifest to authorize these reads.
Other files in the project are outside that scope. Use a new ZIP name for
a different selection.

| Freshness | Meaning |
| --- | --- |
| `unchanged` | Every selected file matched the archived byte count and hash at read time. |
| `stale` | At least one selected file changed or was missing. |
| `unknown` | At least one selected file was unavailable or unsafe to open, with no observed change or missing file. |
| `not_checked` | Freshness was outside the requested operation or an earlier validation failed. |

A stale result can accompany verified integrity. Read the per-file states
when some files were unavailable. Rechecks are read-only and leave the
original ZIP intact. File matches do not establish that a report is current
with respect to a database, later captures or the live network.

## Boundaries

The script accepts up to 16 files, 1 MiB per file and 8 MiB of source bytes in
total. The JSON request and manifest each have a 16 KiB limit. Sources have a
20,000-line limit and JSON inspection has a 30,000-node limit. The script
uses a 10-second command deadline on local POSIX filesystems. The ZIP reader
accepts this bundle format with bounded, uncompressed regular entries.

Paths use portable ASCII names and specify individual files. Hidden and
common private or credential names, symbolic links, hard links and special
files are refused. The script rejects some recognizable credential values.
Review and redact the approved files before use because content checks are
limited. The script writes only a new ZIP under the existing output directory
and declines to replace an existing destination.
