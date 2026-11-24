# Olav Skills

Olav Skills is the Python toolkit behind OLAV’s offline network analysis
workflow. Network operations and NetDevOps teams use it to query device
inventories, inspect recorded topology, compare snapshots, pre-check draft
changes and produce reports with source evidence. The CLI and seven Agent Skills
work with a local DuckDB database.

[Website](https://olavai.com/) · [Documentation](https://docs.olavai.com/) ·
[中文文档](https://docs.olavai.com/zh/) · [PyPI](https://pypi.org/project/olav-skills/) ·
[Browser demo](https://olavai.com/demo/)

Collect device output in the authorized environment, then transfer a reviewed
offline bundle to the analysis environment. The collector is a separate tool.
Device credentials stay on the collection side.
[Architecture and data flow](https://olavai.com/architecture/)

## Current release

[Olav Skills 0.2.2](https://pypi.org/project/olav-skills/0.2.2/) compares exact
recorded topology endpoints, scopes historical path candidates by hop and
interface, and checks recorded plan targets separately from proposed-address
evidence. The pre-check returns `not_approved` for human review.
[Release notes](https://docs.olavai.com/changelog/)

The Python distribution and command are both `olav-skills`. The Python namespace
is `olav_skills`. See the [migration guide](https://docs.olavai.com/migration/)
if you use the earlier `olav` or `olav-netops` distributions.

## Install 0.2.2

Use Python 3.11 or newer and a dedicated environment:

```sh
python3 -m venv ~/.venvs/olav-skills
. ~/.venvs/olav-skills/bin/activate
python -m pip install olav-skills==0.2.2
olav-skills version
olav-skills doctor
olav-skills list
```

### Try a synthetic snapshot

Run the packaged example in a new analysis directory:

```sh
analysis_dir=$(mktemp -d)
python -I -B -m olav_skills.examples.synthetic_walkthrough --home "$analysis_dir"
```

The example validates and imports an offline bundle, checks the exact device
set R1/R2, and writes a topology artifact and a source-linked report. It runs
without model credentials or device access.
[Offline walkthrough](https://docs.olavai.com/offline-data/)

### Connect your harness

Choose the binding command for your harness:

```sh
olav-skills install --harness codex --scope user
olav-skills install --harness claude-code --scope user
olav-skills install --harness pi --scope user
```

OpenCode can discover the skills installed through the Codex-compatible
`.agents/skills/` layout. Install from the intended Python environment, then
repeat the binding after upgrading the package or moving the environment.
[Installation and project scope](https://docs.olavai.com/installation/) ·
[Compatibility](https://docs.olavai.com/reference/compatibility/) ·
[OpenCode skill discovery](https://opencode.ai/docs/skills/#discovery)

For OLAV’s local AI workflows, **Gemma 4 31B IT** or **Qwen3.8-27B** is the
minimum recommended model. The CLI can also run the packaged tools directly.
[Minimum models and test notes](https://olavai.com/local-models/)

### Try the browser demo

The public Demo uses Open WebUI and two synthetic networks. Request access by
email, set a password from the one-time link, and select a tenant in the model
picker to try inventory, topology or report queries.
[Demo welcome page](https://olavai.com/demo/) ·
[Demo walkthrough](https://olavai.com/blog/olav-demo-is-live/) ·
[Open WebUI guide](https://docs.olavai.com/openwebui/)

## Skills

| Skill | Responsibility |
| --- | --- |
| `netops` | Skill navigation, shared network evidence and snapshot/config differences |
| `importer` | Import offline bundles and build a local queryable snapshot |
| `analyzer` | Bounded queries, device/interface inspection and change-plan preparation |
| `topology` | Recorded BGP, OSPF and L2 topology and reusable recipes |
| `reporter` | Investigation evidence, comparisons and report preparation |
| `writer` | Topology artifacts including draw.io, Mermaid and SVG |
| `learner` | Prepare and validate parsers for previously unsupported CLI output |

Scripts accept one JSON object on stdin. Run them using the installed
`olav-skills run <skill> <script>` dispatcher and its bound Python environment;
the walkthrough gives executable examples. Set `OLAV_HOME` explicitly before
importing or querying. The existing `.olav` layout is retained, but historical
database compatibility requires testing on an authorized copy.

Snapshots are recorded evidence, not live inventory. Preserve unknown roles,
missing timestamps, absent topology layers and query truncation in findings.

The collector is a separate product. No device access, model credentials,
enterprise permissions or service access is granted by installing skills.

## Maintained source

- `src/olav_skills/`: library, management CLI and required package data.
- `skills/`: standard skill resources and executable entry scripts.
- `tools/`: exports built from this repository, without the former platform checkout.
- `tests/`: synthetic fixtures and regression tests.
- `provenance/`: source extraction mapping and hashes.
- `docs/`: public technical documentation for this product.

The first extraction used the former platform's official generator in a scratch
directory. Subsequent changes belong here. Do not regenerate the old public pack
over this source repository. The repository history preserves the previous
generated layout, including its separate `runtime/` installer.

The old platform's API-client and DuckLake server profiles, deployed Open WebUI
product and public documentation site have separate migration gates. This package
also supplies its own bounded portable knowledge exporter; see its separate contract.
This native package does not replace them or claim their acceptance.

## Deterministic capabilities in 0.2

These additions and updated contracts apply to Olav Skills 0.2.

- Data readiness: `olav-skills doctor --data --project /absolute/project`
  inspects an explicitly selected native project without changing it. See
  [data readiness](docs/data-readiness.md).
- Recorded state changes: `olav-skills run reporter compare_state` compares
  supported historical parsed-record sources between two explicit snapshots.
  See [state differences](docs/reference/state-diff.md). A record disappearing
  does not establish a real link failure or route withdrawal.
- Plan pre-check: `olav-skills run analyzer check_change_plan` reports individual
  checks and evidence gaps. See [plan pre-check](docs/reference/plan-precheck.md).
  This does not approve execution or provide configuration simulation.

- Parser versions and derived replay: learner `manage_parser` and
  `reparse_snapshot` add explicit validation/activation/rollback and offline
  derived outputs. See [parser lifecycle](docs/reference/parser-lifecycle.md).
- Full file export: analyzer `export_query` pins its source and reports
  completeness/resume status. See [full export](docs/reference/full-export.md).
- Reviewed evidence files: reporter `evidence_bundle` packages selected files,
  checks integrity and rechecks freshness. See
  [evidence delivery](docs/reference/evidence-delivery.md).
- Recorded investigation: analyzer `run_investigation` provides fixed
  inventory/interface/BGP recipes with explicit unknown states. See
  [investigation recipes](docs/reference/investigation-recipes.md).

- Native contracts: IOS/Junos parser, query and historical state semantics use
  the [same state contract](docs/reference/native-contracts.md).
- Historical config: netops `diff_configs` now requires precise recorded
  sources and preserves ordered semantic groups; see
  [config differences](docs/reference/config-diff.md).
- Route changes and counter deltas: reporter `compare_routes` and
  `compare_counters` expose supported [route profiles](docs/reference/route-changes.md)
  and [counter/time evidence](docs/reference/counter-deltas.md).
- One-time audits: reporter `run_audit` applies reviewed
  [fixed profiles](docs/reference/audit-profiles.md) without scheduling.

- Report review: reporter `lint_report` checks explicit claim/citation/evidence
  bindings and reports coverage gaps; see [citation checks](docs/reference/report-citations.md).
  It does not certify natural-language claims or caller-provided source authenticity.
- Topology content: reporter `compare_topology` compares exact recorded snapshots,
  preserving endpoint interfaces and parallel links; see
  [topology changes](docs/reference/topology-changes.md). Missing observations are
  not proof of physical link failure.

- Historical graph paths: topology `inspect_path` retains interface-level
  parallel links and bounded enumeration; see [topology paths](docs/reference/topology-paths.md).
  Recorded candidates do not establish forwarding or ECMP.
- Metric baselines: analyzer `analyze_baseline` separates reviewed historical
  intervals from a target interval with fixed metrics and clock/reset checks;
  see [metric baselines](docs/reference/metric-baselines.md). Percentiles are
  statistical references for observed net deltas, not health certification.

- OSPF observations: analyzer `analyze_ospf` inspects supported captured
  neighbor states and compares qualified historical records; see
  [OSPF analysis](docs/reference/ospf-analysis.md). Routing context gaps remain unknown.
- Audit evidence: analyzer `check_evidence` checks an explicit device/command
  list, capture-time policy and historical raw/parsed bindings; see
  [evidence readiness](docs/reference/evidence-readiness.md). Native CLI imports
  in 0.2 retain new raw capture provenance transactionally; old
  missing history is not reconstructed. `run_audit` can require this gate and
  run an explicit OSPF profile without expanding its default three checks.

These are native deterministic entry points. A host still authorizes its actual
tools; adding a script to the package does not register an Open WebUI tool or
establish natural-language routing in every harness.

## Documentation site

The public manual is maintained in this repository's `docs/`, with English
and Chinese pages and an explicitly marked legacy archive. Build it without
the former platform checkout:

```sh
python3 -m venv /absolute/path/to/docs-venv
/absolute/path/to/docs-venv/bin/python -m pip install -r requirements-docs.txt
/absolute/path/to/docs-venv/bin/python -B tools/build_docs.py
```

This creates `site/`, including checked compatibility pages for old documentation
URLs. The builder refuses to clean an unrelated nonempty output directory.
`wrangler.docs.toml` names the existing documentation app; no publication or
service switch is performed by the build. Historical source and route provenance
are recorded separately from current Skills capability claims.

## License

The existing [LICENSE](https://github.com/james-olavai/olav-skills/blob/main/LICENSE) and its terms are preserved. Public source
availability and Python packaging do not change the applicable license or the
permissions granted by it.
