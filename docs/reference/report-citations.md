# Report citation checks

The unreleased candidate adds `olav-skills run reporter lint_report`. It checks
an explicit report/claim map against caller-selected evidence and a trusted
scope/source/exact-snapshot/hash manifest. It neither discovers files nor
reads the data warehouse. Published `olav-skills==0.1.1` does not include it.

Each claim specifies inclusive one-based report lines and evidence IDs.
Its literal `[evidence:E1]` markers must match those IDs. All nonblank lines
must belong to a nonoverlapping range; group a heading or table with its
finding rather than creating a claim per line. Unmapped content, unknown or
missing references, unused evidence and identity/hash mismatches fail lint.
Duplicate identities and invalid ranges are refused.

The trusted host wraps reviewed original UTF-8 result bytes in an
`olav.report-evidence/v1` JSON envelope. `body` is the original string; its
SHA-256 hashes the original UTF-8 bytes, including original newlines, **not**
the wrapper or reformatted JSON. The envelope declares the scope, source and
exact SID. The caller's independently fixed `context` selects allowed
source/SID/body-hash tuples. A `run_audit` or comparison result requires host
review of its actual provenance before wrapping; no schema translation or
automatic source discovery is implied. Evidence-bundle file hashes are a
separate contract and cannot replace body hashes.

Supply inline `report_text` and envelope `evidence[].text`, or explicitly staged
`report_path`/`evidence[].path` under `project/exports/evidence_sources/`.
File mode requires absolute `project` and refuses symlinks/traversal. There are
no writes. Maximums are 512 KiB request, 64 KiB report/500 lines, 128 claims,
16 evidence entries, 32 KiB per envelope/256 KiB total, and 64 returned issues.

A completed lint returns `verdict:pass|fail`; invalid input returns
`verdict:unknown` with `status:error`. Only pass exits zero.
`coverage:all_nonblank_lines_mapped` means text belongs to ranges, not every
assertion has been proved. One range may contain multiple assertions.
`claim_truth` and `source_authenticity` remain `not_checked`, and
`identity_check:caller_context_only` identifies the trust boundary. A claim
whose prose is false can still pass citation checks. If both context and
payload are replaced together, this offline tool cannot detect that forgery.
It does not establish freshness, complete collection, live health or approval.

For the full request schema, executable host-wrapping example, literal marker
rules and error behavior, read the installed skill resource
`reporter/references/report_citations.md`.
