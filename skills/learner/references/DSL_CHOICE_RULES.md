# Parser DSL choice

The harness drafts the parser from prepare_learn output; no automatic model
router is supplied. Prefer TextFSM for aligned tables with one record per line;
prefer pure Python for multiline blocks, nested/key-value structures or related
records that a line state machine cannot express clearly.

Begin source with `# OLAV_DSL: textfsm` or `# OLAV_DSL: python`. Python exposes
`def parse(raw: str) -> list[dict]` and must satisfy the validator AST/sandbox.
Generalize observed variants (including dotted ASNs and missing optional fields)
and validate semantic fields across all supplied samples. Empty or device-error
output is not evidence for a useful parser. Running-config/raw-only commands
should not be learned merely because they have no structured rows.

finish_learn returns diagnostics on invalid source and writes nothing. Correct
the actual defect with a bounded host-approved retry; the package provides no
automatic retry budget, batch learner or provider. Consult FROZEN_LAYOUT.md for
persistence and low-sample behavior.
