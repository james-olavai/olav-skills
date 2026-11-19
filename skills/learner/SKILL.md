---
name: learner
description: "Prepare and validate TextFSM or pure Python parsers from authorized CLI samples, then freeze them locally for later parsing. No automatic model or batch-learning service."
metadata:
  runtime: "olav-skills>=0.2.0"
---

# Learner

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

## Prepare, draft, validate and freeze

This workflow writes local parser files and may affect later imports. Use it
only within the authorized project and samples. Group samples by actual
`platform` and `command`, retaining device identity and variance.

1. `olav-skills run learner prepare_learn` accepts `platform`, `command` and
   `samples` = `[{"device":"R1","raw_output":"..."}]`. It returns hints and
   a drafting prompt; the current harness drafts the parser.
2. Choose TextFSM for aligned one-record-per-line tables, Python for multiline
   or nested structures. Read [DSL choice](references/DSL_CHOICE_RULES.md) when
   unclear. Generalize AS formats and other observed variance, not constants.
3. Pass `platform`, `command`, the entire `parser_response` and all `samples`
   to `olav-skills run learner finish_learn`. Source begins with
   `# OLAV_DSL: textfsm` or `# OLAV_DSL: python`, followed by source only.
4. Check `status`, diagnostics, `parsed_rows`, `sample_count`, path and
   `quarantined`. Validation failure writes nothing; correct the demonstrated
   issue within the host's retry budget instead of retrying unchanged source.

Python is `def parse(raw: str) -> list[dict]`, a pure function with no file or
network access. Validation enforces an AST allowlist and sandbox; do not work
around it. Relevant allowed modules include json, re, typing, collections,
ipaddress, dataclasses, itertools, logging and netutils. OS/process/network,
open/eval/exec/dynamic-import access is forbidden.

Validate all supplied samples and expected fields, including independently held
out variants when available. Nonempty rows alone do not prove semantic accuracy.
TextFSM learned from one sample goes live immediately; report the overfit risk.
Single-sample Python can be quarantined. Read [frozen layout](references/FROZEN_LAYOUT.md)
for persistence and lookup. No automatic promotion, retries or scheduled batch
learning are provided. A parser update does not reparse historical rows by itself.

## Version management and offline replay

For explicit candidate versions, independent expected-output validation,
activation or rollback, read [parser lifecycle](references/parser_lifecycle.md)
then use `olav-skills run learner manage_parser`. The candidate must pass its
own documented validation gate before activation; caller-supplied expectations
are not independently certified truth. No active managed selection preserves
the legacy lookup above. Activation affects later parsing inside the selected
project and needs authorization for that write.

`olav-skills run learner reparse_snapshot` replays authorized stored raw into a
new derived export under the lifecycle contract. It does not rewrite the source
database or reconstruct missing captures. Preserve exact SID, raw/parser hashes,
unknown collection time and partial last-writer source coverage.
