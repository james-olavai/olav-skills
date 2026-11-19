# From recorded relationships to a diagram and report

Run the [offline walkthrough](../offline-data.md) first. It already verifies two hosts and one recorded CDP edge, writes a Mermaid artifact and creates a report. This workflow explains how to repeat and interpret that artifact step without a model.

If you received a diagram in a configured chat, see how to read its coverage in the [Open WebUI guide](../openwebui.md#read-the-scope-of-a-topology-diagram).

## Read before drawing

Query `topology query_topology` with `concept: all` and the import-returned snapshot ID. Review `absent_layers`; the fixture's OSPF source is absent. L2, BGP and OSPF are different layers, and an empty or missing layer should not be drawn as a verified lack of relationships.

Then use the same ID for a scoped diagram:

```sh
SKILLS_PYTHON=/absolute/path/to/skills-venv/bin/python
export OLAV_HOME=/absolute/path/to/new-synthetic-analysis
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"name_like":"R%","snapshot_id":sid,"diagram_format":"mermaid","filename":"synthetic_topology_repeat","max_hosts":2}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run writer draw_topology
```

Expected for the fixture: `status: ok`, two hosts, one edge and a file at the returned `path`. Inspect the file, not just the process exit. A name pattern scopes a query and does not classify devices by role. On a large dataset choose a tighter name pattern or `center`/`hops`; `too_wide` is a request to narrow, not permission to omit nodes silently.

## Convert an existing table to SVG

The pure converter accepts a Markdown adjacency table, not a database path. For a separate **invented two-node illustration**, this payload is valid:

```json
{
  "adjacencies_table_markdown": "| Source | Local Intf | Dest | Remote Intf | Status |\n|---|---|---|---|---|\n| demo1 | Gi0/1 | demo2 | Gi0/2 | up |\n",
  "protocol": "l2_cdp"
}
```

Pass it on stdin to `olav-skills run writer render_topology_svg`. The result is a JSON-encoded SVG string; decode it before saving. `Local Intf` and `Remote Intf` are accepted column aliases; hyphenating them as `local-intf`/`remote-intf` is not accepted. An omission comment means no diagram was produced. This illustration does not add a link to your imported network.

SVG is a table converter only; `draw_topology` supports draw.io and Mermaid output. Draw.io symbol choices can use naming heuristics for presentation, which are not evidence of a device's actual class or role.

## Preserve evidence beside the picture

A useful report references the snapshot, collection-time provenance, query scope, node/edge counts, absent layers and artifact path. Pair important claims with focused [reporter evidence](../skills/reporter.md). The bundled synthetic report is ordinary Python-generated Markdown, not a supplied report-writing API. Host file writes require the host's own permissions.

Recorded adjacency is not proof of packet reachability or an approved design. Check source coverage, timestamps, truncation and artifact sensitivity before sharing or making operational decisions.

## Unreleased historical path query

Use topology `inspect_path` after [reading the path contract](../reference/topology-paths.md)
for bounded candidates in an exact recorded neighbor graph. Retain interface
identities and parallel links. Missing discovery, hop limits and unenumerated
paths must stay visible; a graph candidate does not establish packet forwarding
or operational failover. This entry is absent from published 0.1.1.
