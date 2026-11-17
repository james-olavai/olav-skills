# Compare captures and prepare a change draft

A comparison needs two known snapshots in the same authorized analysis scope. Record source and target IDs explicitly, with their independent collection-time provenance and parser coverage. Import order, lexicographic ID order and file modification times do not establish collection order.

## Understand the type of difference

| Helper | What it compares | What it does not prove |
| --- | --- | --- |
| `query_batch` `diff_summary` | Selected identity keys using `diff_kind` | Full semantic/body differences |
| `diff_snapshots` | Keys in parsed, raw, topology or OC tables | A value change when the identity key is unchanged |
| `diff_configs` | Saved CLI text for a device and command | Why the change occurred or current device state |
| `inspect_blast_radius` | Removal from an observed graph | Policy, forwarding or packet reachability |

For key comparisons, added means target minus source and removed means source minus target. Check exact totals separately from bounded samples and preserve per-direction `has_more`/truncation. Optional source tables can be absent. Select the relevant available table rather than pretending every source exists.

## Verify the comparison path on the fixture

This self-comparison is a smoke check, not evidence of a real network change. It uses the actual imported synthetic snapshot twice and a known available table:

```sh
SKILLS_PYTHON=/absolute/path/to/skills-venv/bin/python
export OLAV_HOME=/absolute/path/to/new-synthetic-analysis
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"snapshot_id_1":sid,"snapshot_id_2":sid,"table_name":"raw_output_store"}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run reporter diff_snapshots
```

Expected: `status: success`, zero added keys and zero removed keys. For a real comparison, replace the two inputs with verified source/target IDs from your own imports into that scope. A zero key delta can still hide changed bodies or changed field values. Use targeted evidence or the saved-text diff where applicable.

`diff_configs` requires `device`, `command`, `snapshot_id_1` and `snapshot_id_2`; `sections`, `context_lines` and `full` affect presentation. Its inputs are saved snapshot raw files, so do not assume every imported database also contains that filesystem source. Surface compact-output limits and missing files.

## Turn evidence into a draft

An engineering change draft should identify actual endpoints/platforms, interfaces, routing context, constraints, unknowns and impact evidence. Include complete reviewed implementation steps, symmetric rollback, expected post-checks and a pre-change verification section. Existing captured state can be stale. A human or host writes the draft using authorized tools; `generate_change_plan` is a separate bulk model-pattern artifact helper, not a generic intent-to-configuration compiler.

No simulator is included. Historical Batfish or `sim` references are not executable standalone validation. A graph impact calculation or well-formatted Markdown does not certify a change. Before application, independently verify against current authorized network state and the appropriate external testing process. The Skills package does not execute device configuration or schedule a maintenance window.
