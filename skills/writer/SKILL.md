---
name: writer
description: "Render scoped recorded topology as draw.io or Mermaid, convert supplied adjacency tables to Mermaid/draw.io/SVG, or polish existing reports without inventing facts."
metadata:
  runtime: "olav-skills>=0.2.0"
---

# Writer

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

## Draw from recorded data

```sh
printf '%s' '{"center":"R2","hops":1,"diagram_format":"mermaid"}' | olav-skills run writer draw_topology
```

`draw_topology` queries, renders and saves under authorized project
`exports/diagrams/`; result includes `path`, `hosts`, `edges`, `scope`, `format`.
Inputs include `name_like`, `center`, `hops`, `protocol` (`l2_cdp`, `bgp`, `ospf`),
`roles`, `sites`, `snapshot_id`, `filename`, `diagram_format` (`drawio`/`mermaid`).
Use a basename for filename. Prefer default bound DB; do not pass an arbitrary
`db_path` to bypass host scope. Default `max_hosts=120` is a readability guard,
not permission to export all data; do not raise it to evade refusal.

Derive scope from the user's actual request. `roles` uses recorded roles;
`name_like="%core%"` is a hostname pattern and does not prove core role.
Do not substitute a prefix for an explicit device set. If no exact supported
scope expresses the request, explain the limitation. Missing sources and empty
matches can both yield no links, so do not infer no network from that result.
One justified narrower retry after `too_wide` is reasonable if still answering
the question; otherwise report the limit and unfinished scope.

## Render an existing adjacency table

Use `render_topology_mermaid`, `render_topology_drawio` or
`render_topology_svg` with `adjacencies_table_markdown`. These transform supplied
recorded rows and do not fetch new facts. Report omission notes as omissions,
not successful diagrams. SVG is inert standalone markup, with limits of
262144 UTF-8 table bytes, 300 nodes, 1000 edges, 256 characters per cell and
300000 stdin bytes. It accepts only the table plus optional `protocol`; it is
not an extra `draw_topology` format. Other renderers have their own contracts.

## Polish an existing report

Read the actual supplied file using an authorized host tool. Improve prose,
headings and presentation while preserving technical facts, code fences,
device names, IPs, ASNs, snapshot/time and evidence limitations. Embed diagrams
from existing adjacency data, or use a separately authorized scoped draw.
New investigation belongs to analyzer/reporter; do not invent missing findings.
Save with the host's actual file writer only when authorized; otherwise return
text. No package-wide formatting/export service or in-memory file API exists.
Report an actual saved path only after the write succeeds.
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
