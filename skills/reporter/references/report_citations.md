# Report citation and evidence identity linting

Unreleased local candidate; absent from published `olav-skills==0.1.1`.
Use `olav-skills run reporter lint_report` with one stdin JSON object.
No model, database, collector, file writing or directory discovery is involved.

## Trusted input and what a pass means

A trusted host first selects the authorized scope, exact source/snapshot IDs
and SHA-256 hashes of the original evidence bytes. It fixes `context` through
an independently trusted channel. The report author supplies the report,
claim mapping and selected evidence. This tool compares them with that caller
context; it cannot authenticate the host or that channel. If an author can
replace both the evidence and context, a consistent forgery cannot be detected.

A pass means references and declared identities match the caller's selected
manifest and every nonblank report line belongs to a claim range. It does not
establish truth, completeness, collection time, freshness or provenance
outside that manifest. `claim_truth` and `source_authenticity` always remain
`not_checked`; `identity_check` is `caller_context_only`. An unsupported claim
with a syntactically correct citation can pass. Human review is still needed.
A large claim range can contain several assertions; the linter does not check
whether evidence supports each assertion or whether a conclusion follows.

## Request

Required keys: `context`, `evidence`, `claims`. Supply exactly one of
`report_text` (UTF-8 string) and `report_path`. Files additionally require
absolute `project`; `OLAV_HOME` alone does not authorize file access.

```json
{
  "report_text": "# Recorded review\n\nr1 has a returned down record [evidence:E1]\n",
  "context": {
    "scope_id": "review-1",
    "sources": [{"source": "interfaces", "snapshot_id": "s1", "sha256": "REPLACE_WITH_ORIGINAL_BODY_SHA256"}]
  },
  "evidence": [{
    "id": "E1",
    "text": "{\"schema\":\"olav.report-evidence/v1\",\"scope_id\":\"review-1\",\"source\":\"interfaces\",\"snapshot_id\":\"s1\",\"body\":\"{\\\"rows\\\":[{\\\"hostname\\\":\\\"r1\\\",\\\"state\\\":\\\"down\\\"}]}\\n\"}"
  }],
  "claims": [{"id": "C1", "line_start": 1, "line_end": 3, "evidence": ["E1"]}]
}
```

The placeholder is intentionally not a usable digest. Build the request with
the executable example below. `evidence[].text` contains an envelope JSON string;
use `path` instead for a staged envelope file. Exact envelope fields are
`schema`, `scope_id`, `source`, `snapshot_id`, `body`. `body` must be the original
UTF-8 evidence text, including its original whitespace and trailing newline.
The source digest hashes **body UTF-8 bytes**, never the envelope, parsed JSON,
or a reformatted result. Envelope whitespace does not affect the source hash.
Only UTF-8 evidence is supported; invalid encoding is refused.

A trusted host may wrap an actual `run_audit`, `compare_state` or other returned
result without changing the original output:

```python
import hashlib, json
from pathlib import Path

# This is one explicitly selected, already authorized/reviewed result, not a
# directory scan. Read bytes: Path.read_text() can normalize CRLF newlines.
raw = Path('/authorized/staging/selected-result.json').read_bytes()
body = raw.decode('utf-8')
assert body.encode('utf-8') == raw
# The host obtains source and exact SID from reviewed actual provenance,
# not a hostname, date-looking SID, free-form author assertion or guessed keys.
source, sid, scope = 'interfaces', 's1', 'review-1'
context = {'scope_id': scope, 'sources': [{
    'source': source, 'snapshot_id': sid,
    'sha256': hashlib.sha256(raw).hexdigest()}]}
envelope = {'schema': 'olav.report-evidence/v1', 'scope_id': scope,
            'source': source, 'snapshot_id': sid, 'body': body}
request = {'report_text': '# Recorded review\n\nr1 has a returned down record [evidence:E1]\n',
           'context': context,
           'evidence': [{'id': 'E1', 'text': json.dumps(envelope)}],
           'claims': [{'id': 'C1', 'line_start': 1, 'line_end': 3, 'evidence': ['E1']}]}
# Pass json.dumps(request).encode('utf-8') as stdin to lint_report.
```

This wrapper does not automatically migrate any native result schema. A result
with two snapshots, partial/unknown evidence or several sources needs host
review and an explicit source policy; wrapping does not make it complete or
turn unknown into zero. Existing `evidence_bundle` manifests hash selected
files, not this original-body contract, and cannot be used interchangeably.
A bundle can transport the reviewed report/envelopes, with its own integrity
and trusted-manifest-pin checks; it does not certify their claims.

## Claims and citations

Claim IDs and evidence IDs use `[A-Za-z][A-Za-z0-9_.-]{0,63}`. Scope/source
use the same form. Exact snapshot IDs use alphanumeric followed by up to 127
alphanumeric, underscore, dot, colon or hyphen characters; `latest`, `current`, `newest` and `all` are refused regardless of case.
Source is a logical reviewed label, not a file path or automatically derived recipe name.
Hashes are lowercase 64-digit SHA-256. No unknown object keys are accepted.

Line numbers start at one; ranges are inclusive and cannot overlap. Empty lines
need no range. All nonblank lines, including headings and table separators,
must be inside a range. Group a heading/table with its finding, as above;
a separate claim per line is unnecessary. The coverage label
`all_nonblank_lines_mapped` describes this mapping only, not fact coverage.
Outside-range nonblank lines cause `unmapped_report_line` and `coverage:partial`.

Every range requires at least one evidence ID. Its exact `[evidence:ID]` marker
set must equal the mapped IDs. Markers are literal, including inside code
blocks; no Markdown parser or link following is used. Repeated markers within
one range, malformed reserved markers, unknown references and unused supplied
evidence cause failure. Reuse across different claims is permitted. Duplicate
claim IDs, evidence IDs, identical scope/source/SID/body-hash tuples, overlapping ranges, duplicate
source/snapshot entries and repeated mapped IDs are input errors. Identical body
bytes at distinct sources or snapshots are allowed when each identity is pinned
in context. The body is not parsed or semantically interpreted; only envelope
JSON and original-text identity are checked.

## Files, budgets and results

Files must be explicitly selected under `exports/evidence_sources/` within
`project`, with 3–8 safe path segments and `.json`, `.csv` or `.md` suffix.
Symlinks, hardlinked/nonregular files, traversal and sensitive filenames are
refused. Paths and report/evidence contents are not copied into results.
No arbitrary absolute input file, URL, archive or recursive scan is accepted.

Limits: request 512 KiB, report 64 KiB/500 lines, 128 claim ranges,
16 manifest sources/evidence entries, each envelope 32 KiB, all envelopes
256 KiB. At most 64 issues are returned; `issue_count` and `issues_truncated`
retain the fact of omitted issues. Original body bytes fit inside the envelope
budget. Both stdin and direct Python `run()` enforce request bounds.

A completed lint returns `status:success`, `completed:true`, `verdict:pass|fail`,
`report_sha256`, mapping coverage and issues. A mismatch is a completed lint
with `verdict:fail`; invalid input or unavailable files returns `status:error`,
`completed:false`, `verdict:unknown`, `error_type`. Process exit is zero only
for pass. A pass never changes the truth/authenticity/collection-time fields.
