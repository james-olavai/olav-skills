# Olav Skills contributor guidance

This repository is the maintained source of Olav Skills, not a generated copy
of the former OLAV agent platform. Do not regenerate over this repository.
The Python distribution and CLI are `olav-skills`; imports use `olav_skills`.
Preserve the existing LICENSE, notices and public provenance.

## Source ownership

- `src/olav_skills/`: deterministic Python library and management CLI.
- `skills/`: seven standard skill directories and stdin-JSON entry scripts.
- `examples/`: public synthetic bundle and model-free walkthrough.
- `tests/`: synthetic fixtures and focused regression checks.
- `tools/`: resource export, release review and documentation build utilities.
- `docs/`: maintained English/Chinese public manual.
- `provenance/`: source extraction and immutable legacy-documentation records.

Do not add an agent harness, model session manager, enterprise integration or
network collector to this package. Keep `.olav` on-disk formats and `OLAV_HOME`
semantics stable. Do not depend on the former platform distributions or SDKs.
Skill instructions never grant host permissions. Preserve result bounds,
read-only SQL enforcement, unknown values, collection-time limits and evidence.
No implicit dotenv loading or automatic upgrade of an existing service.

## Data and credentials

Set `OLAV_HOME` to a new scratch directory before importing or running data code.
Use public synthetic fixtures and independently expected sets/results. Never
read real captures, live databases, credentials or private deployment state
merely to test a change. Never commit tokens, `.env`, `.pypirc`, private data,
local environments, generated site output or build artifacts.

## Validation

Validate distributions from a source-only wheel/sdist in a fresh non-editable
venv outside the checkout. Import success alone is insufficient: run real public
CLI/script entry points and check semantic results plus affected negative cases.
Layout installation, native harness discovery, model routing and answer quality
are distinct evidence. Record actual versions and limitations; do not claim
all harnesses or complete workflows based on directory-copy tests.

Keep existing dirty changes. Use a single writer for shared files and the Git
index; a different owner reviews completed work. Retain first failures and
repeat only affected checks after a fix. Commit, push, package upload,
deployment and data mutation each follow the user's current authorization.

## Documentation

Build with a separate environment using `requirements-docs.txt` and
`python -B tools/build_docs.py`. No old platform checkout or runtime import is
needed. The builder owns its output directory; do not bypass its refusal to
clean unrelated files. `mkdocs.yml` and `wrangler.docs.toml` describe the manual;
a build does not deploy it.

`docs/legacy/**` is immutable public historical source. Preserve its exact bytes
and `provenance/docs-legacy-20261117.json`. Edit current pages, not the archive.
Display context, search/sitemap exclusion and old-route/anchor compatibility
belong to docs hooks/build tooling and `docs-url-map.json`.
Historical blogs/docs do not establish current package capabilities.

PyPI 0.1.1 is an immutable published release. Do not rebuild and upload that
version merely because docs changed. A frozen downstream consumer may bind a
different prepublication artifact with the same version: compare exact hashes
and regenerate its contract only as a separately verified update.
