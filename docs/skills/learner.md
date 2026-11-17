# learner: prepare and validate CLI parsers

Learner helps fill a structured parsing gap for a known platform/command pair. It is a two-step deterministic interface around a human or host-model drafting step. It is not model training, fine-tuning, memory recall or an automatic learning service.

## Prepare representative samples

`prepare_learn` requires `platform`, `command` and nonempty `samples`, each with `device` and `raw_output`. It returns `status`, structure hints, sample count and a drafting prompt. It does not generate the parser through a remote model. Use authorized raw text and remove sensitive content without changing the structure needed for validation.

For aligned one-record-per-line tables prefer TextFSM; for complex multiline/indented records a pure Python `parse(raw: str) -> list[dict]` may be appropriate. The candidate starts with `# OLAV_DSL: textfsm` or `# OLAV_DSL: python`, followed by source, without Markdown fences. A draft must work across every supplied sample, including varied values and empty/error output cases handled deliberately.

## Validate and freeze

`finish_learn` takes the same `platform`, `command`, `samples` and the complete draft as `parser_response`; optional `contract_version` defaults to 1. It checks the DSL, validates sample parsing and freezes successful candidates into the analysis root's template tree. Python candidates additionally pass an AST allowlist and sandbox validation. File/network operations and dynamic execution are not permitted parser behavior.

Inspect `status`, `diagnostic`, `path`, `quarantined`, `sample_count` and `parsed_rows`. `invalid` means validation failed and no candidate was frozen. A successful **TextFSM template becomes live immediately, even with one sample**; low sample count produces a warning, not quarantine. Low-sample Python candidates can go into quarantine. Check the actual `quarantined` field rather than assuming the same activation policy for both DSLs.

The parser participates in later parses/imports; this process is not a promise that old stored records are automatically reparsed. Review parser files and use a disposable analysis copy before reimporting or rebuilding an existing workflow.

## Choose the right gap

Read the import report first. `no_parser_registered` can be a useful extension target; `raw_only` is intentionally unstructured and should not be treated as a missing parser. A `parser_error` can be a defect rather than a new format to learn. Keep parser changes separate from the original import summary.

Use varied training samples plus an independent holdout set with expected rows. Passing the drafting samples proves that narrow validation, not accuracy for every device or release. The [parser workflow](../workflows/parsers.md) gives an executable synthetic preparation example and a review checklist.
