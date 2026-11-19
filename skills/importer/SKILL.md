---
name: importer
description: "Survey, validate and import authorized offline network bundles, archives or vendor dumps into a local recorded snapshot; report structured parsing gaps."
metadata:
  runtime: "olav-skills>=0.2.0"
---

# Importer

## Execution and permissions

Use the installed, bound interpreter and one JSON object on stdin:

```sh
printf '%s' '{"operation":"snapshot_summary","snapshot_id":"latest"}' | olav-skills run analyzer query_batch
```

Set `OLAV_HOME` to the authorized project before running. The host supplies
execution, file access and permissions. If it exposes a restricted native tool,
use that tool's actual name and schema; a shell example does not grant shell
access or permit bypassing the host. No device access, agent router, model
provider, memory service, simulation engine or general export tool is supplied.
Read sibling skills only when needed; loading a skill does not delegate work.
Scripts return JSON unless their documented output is text. Check process exit
and result status; successful execution alone does not prove an answer.

## Import workflow

Import changes the authorized local project. It does not collect from devices.
Use `olav-skills run importer <script>` with stdin JSON:

1. `survey_bundle` with `{"path":"/absolute/path/to/input"}` surveys and may
   extract/normalize the input. Use its returned `path` for later steps,
   which can differ from the input. Read `notes`, `format`, `collector`,
   `needs_platform_detection` and `platform_sample_lines`.
2. On `error` or `ingest_supported=false`, report the reason and stop. Do not
   bypass unsupported-format or integrity checks by manually unpacking/fixing.
3. For hosts in `needs_platform_detection`, call `discover_platform` with
   `host_dir`. Use supplied sample lines if confidence remains unknown;
   do not invent platforms. Pass only justified overrides as `host_platforms`.
4. `validate_bundle` with the surveyed `path` must return `ok=true` before
   ingest. Report warnings; integrity/SHA mismatch requires a corrected source.
5. `ingest_snapshot` with the surveyed `path`, optional `host_platforms` and
   `collection_source` from actual collector metadata lands the snapshot.
   Large imports can take minutes: use a suitable host timeout if available,
   never invent a timeout JSON argument. Do not start a duplicate while running.

Read [bundle schema](references/bundle_schema.guide.yaml) for canonical input,
[vendor heuristics](references/vendor_dump_heuristics.guide.yaml) for ambiguous
dumps, [RANCID](references/rancid_format.guide.yaml) for that format, and
[platform signatures](references/platform_signatures.guide.yaml) only as needed.

## Report what landed and what did not parse

Use actual returned `bundle_id`, `snapshot_id`, `parse_report`, `report_path`.
Include devices and command outputs landed, outputs parsed/unparsed by reason,
devices with no structured data and their returned sample. Missing fields are
unknown, not invented counts. Raw import success does not imply every command
has structured rows or every topology layer is covered. Inventory and parsed
coverage are distinct; later queries must verify exact device membership.

For `learnable_outputs`, identify top returned platform/command gaps and suggest
[learner](../learner/SKILL.md) as a separate opt-in write workflow. No batch
learning command or slash command is supplied. `raw_only` is intentional;
`parser_error` is a defect, not evidence that a new parser should be learned.
Do not automatically launch parser learning during an import.
## Evidence boundaries

Use a supported bounded template first, one batch for a fleet question. Empty
optional filters discover within authorized scope; limits, clipped cells and
`has_more` / `byte_truncated` still apply. Discovery is not an unlimited export.
Do not infer snapshot membership from inventory or device role from a hostname,
platform, ASN or loopback. Keep unknown roles separate. Missing/unsupported
sources, failed queries and unknown values are not zero or healthy results.
Do not silently drop filters or narrow the user's question.

Cite snapshot and returned collection-time provenance, including precision,
source and status. Recorded data is not live state. Ingest/update time and a
date-looking ID do not prove capture time. If `latest` returns
`ambiguous_snapshot_time`, report the ambiguity and use an explicit authorized
snapshot only when the user's scope supports it; do not order IDs to guess.
An explicit ID can support observations while collection time stays unknown.
Stop once evidence answers the question. Do not repeat unchanged failures or
raise budgets; a corrective retry needs a real corrected argument within scope.
Report unfinished scope and source/preview truncation.

## Native state interpretation (unreleased)

For default IOS/Junos parsing and the shared interface/BGP record semantics,
read [native contracts](../netops/references/native_contracts.md). Keep protocol
families distinct from line states, unknown distinct from nonhealthy, and
current inventory metadata distinct from historical source membership.

## Unreleased native capture ledger

The reviewed candidate's official native `ingest_snapshot` entry stores new raw
records and bounded capture provenance in the same database transaction as
parsed records. Historical SID/device/command and the actual stored raw hash
remain bound after later imports. Command collection headers must be real
evidence; import time or an SID is not a substitute. Old imports without a
ledger are not backfilled, and direct legacy library calls retain their stated
limits. See [evidence readiness](../analyzer/references/evidence_readiness.md)
before interpreting a checklist as ready. This does not certify redaction,
source authenticity, parsing correctness or network coverage, and is absent
from published 0.1.1.
