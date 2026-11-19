# Output and host file access

The package has no general Markdown/CSV export service or mandatory report
pipeline. Return an answer inline, or use an authorized host file writer when
the user requests a file. Read an existing file before editing. File tools and
virtual filesystems vary by harness; confirm persistence instead of assuming.

Domain scripts that write return their actual path/status: importer reports,
writer diagrams, parser freezes and the limited bulk change-plan generator.
Do not invent saved paths. Bounded query CSV contains only returned preview
rows, not all source records. Cite scope, snapshot, source truncation and gaps
in both the answer and saved artifact. Do not claim unknown values are zero.

Use descriptive basenames and authorized destinations. No device, service or
simulation permissions follow from an output path. Once the requested result
is complete, stop; no package middleware automatically ends a model loop.
