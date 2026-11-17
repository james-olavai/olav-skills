# Olav Skills

Network analysis skills and deterministic Python tools for existing AI harnesses.
Import offline network captures, query the resulting model, inspect topology,
compare snapshots and prepare evidence-based artifacts. The harness supplies the
model, conversation and permissions; this package does not run its own agent.

This repository is the maintained source of the standalone product. The new distribution is `olav-skills`, its Python namespace
is `olav_skills`, and its command is `olav-skills`. The former `olav` and
`olav-netops` Python distributions are not dependencies.

## Install 0.1.1

Public source: [james-olavai/olav-skills](https://github.com/james-olavai/olav-skills).

Use Python 3.11 or newer and install `olav-skills` 0.1.1 in a dedicated
virtual environment:

```sh
python3 -m venv /absolute/path/to/skills-venv
/absolute/path/to/skills-venv/bin/python -m pip install olav-skills==0.1.1
/absolute/path/to/skills-venv/bin/olav-skills version
/absolute/path/to/skills-venv/bin/olav-skills doctor
/absolute/path/to/skills-venv/bin/olav-skills list
```

Alternatively, install a reviewed local wheel with
`/absolute/path/to/skills-venv/bin/python -m pip install /absolute/path/to/olav_skills-0.1.1-py3-none-any.whl`
after verifying its supplied SHA-256.

The distribution includes the seven skills and a public synthetic example. Follow
[installation](https://github.com/james-olavai/olav-skills/blob/main/docs/installation.md) for Codex, Claude Code and Pi; then run the
[offline data walkthrough](https://github.com/james-olavai/olav-skills/blob/main/docs/offline-data.md) for validation, import, exact
device membership, topology and an evidence report. See [migration](https://github.com/james-olavai/olav-skills/blob/main/docs/migration.md)
before replacing a legacy installation, and [portable export](https://github.com/james-olavai/olav-skills/blob/main/docs/portable.md)
for knowledge-only handoff.

Package tests establish deterministic execution and installation layout.
Actual harness discovery, natural-language routing and model answers need
separate validation.

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
