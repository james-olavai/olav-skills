# Source extraction snapshot

On 2026-11-17 the public native pack was refreshed into a scratch directory with
`/home/yhvh/code/Olav/.venv/bin/python scripts/gen_olav_skills.py --profile native --pack olav-skills --out /tmp/olav-skills-source-20261117 --write`.
This was the one-time promotion into maintained source. Builds and exports now
read this repository only. `tools/export_skills.py` exports the authoritative
root `skills/` directories; the installed CLI also supports resource export.

`extraction-20261117.json` records original source bytes, generated scratch bytes
and promoted bytes separately. Dirty state is limited to participating source
paths. Empty package initializers came from the legacy generator. Source paths
are provenance, not installation dependencies. Subsequent maintenance changes
these sources directly; do not regenerate them from the retired platform.

The namespace transformation keeps `.olav/`, `OLAV_HOME` and stored schema
identifiers unchanged. Library/module imports and dynamic module names use
`olav_skills`; package resources use `olav_skills.netops/data`. Plugin discovery
uses dedicated `olav_skills.*` groups. Exported scripts import the installed
package without walking ancestor checkouts for `src/`.

The seven frontmatters use the [Agent Skills specification](https://agentskills.io/specification):
`name`, `description`, `compatibility` and string-valued `metadata.runtime`.
Tool permissions belong to the host. Standard skill resources do not grant them.

Only the public native pack, its required YAML/recipes and a synthetic two-host
fixture were extracted. No private presales/enterprise tree, runtime database,
capture collection, user configuration or credentials is included. The bounded
query regressions and standalone workflow tests were adapted from public source;
the manifest records their origin. The CDP test string is a synthetic example.

`SOURCE-LICENSE` preserves the platform license verbatim and is byte-identical
to the existing root `LICENSE`, as recorded by SHA-256 in the manifest.

The optional DuckLake server attestation code remains fail closed and does not
have a server runtime inventory in this native distribution. This extraction
does not replace or validate the separate DuckLake, portable or API-client
profiles, live collection, models, agent harness behavior or private deployment.

The independent source-boundary review removed unused embedding/memory code,
semantic-cache and priming hooks, unused model-driven parser learning and an
unused LangChain text processor. Native import cannot activate model services
even when a host has provider configuration or dependencies. Excluded original
source hashes remain recorded separately for the review trail.
