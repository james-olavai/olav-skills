# Change-plan scope and review

Read only for a requested change draft. Use recorded device/platform,
interface/IP, topology and scoped routing-config evidence. Empty text extraction
can mean absent data or a nonmatching pattern; it does not prove absence of a
configuration. Unknown port availability, address conflicts or protocol remain
explicit prerequisites. Graph removal impact is not traffic simulation.

## Deliverable

Summary and purpose; devices/platforms and snapshot/time; implementation;
symmetric rollback; verification commands and meaningful expected results;
risks; unverified prerequisites and pre-change review. Commands are draft text,
never operations to execute through this package. New syntax must match the
actual platform/release and existing hierarchy. Do not fabricate a complete
approved plan where facts are missing.

For vendor-specific examples and rollback pitfalls, read
[CLI authoring](change_plan_cli_authoring.guide.yaml). Examples require review:
IOS, IOS-XE and IOS-XR are not interchangeable. Numeric Cisco BGP prefix counts
can mean established, so a literal 'Established' post-check is not universal.
Pre-change verification requires separately authorized tools/operator review;
no config simulator, lab runner or live deployment exists in this package.

## Limited bulk generator

`olav-skills run analyzer generate_change_plan` accepts `model_pattern`
(SQL LIKE), `output_filename` (basename), optional `upgrade_description`,
`bfs_order` (default true). It reads the matching inventory and writes a file
relative to process CWD under `exports/change_plans/`; run only from an
explicitly authorized output directory with `OLAV_HOME` fixed.

This is a Cisco IOS firmware/software upgrade template, regardless of matched
platform. Confirm ALL matching platforms before using it; do not use it for a
mixed-vendor or general configuration plan. Its `bfs_order` name is historical:
it sorts by role substrings (leaf/access/server/wifi, distribution/dist, wan,
core), not topology traversal, dependency analysis or verified safe ordering.
Missing/unknown roles default to a middle stage. It reads inventory, not proven
historical snapshot membership, and does not pin a snapshot.

Output includes placeholders (TFTP/image values), assumptions and unverified
checks. Treat it as an unreviewed draft, disclose these limits and require
operator completion/review; never present its CLI, durations or stage order as
validated. It cannot automatically simulate or apply changes. Do not silently
edit its inputs to make an unsupported request appear supported.
