# Migrate from the generated pack or legacy platform

The standalone product has one distribution, `olav-skills`, one Python namespace, `olav_skills`, and one management command, `olav-skills`. Version **0.1.1 is published on PyPI**. It includes the seven standard skills and their deterministic runtime. The old generated pack's separately installed `runtime/` and the `olav` / `olav_netops` namespaces are not installation dependencies or import aliases.

## Move a consumer in stages

1. Record the old interpreter, package versions, skill discovery roots and authorized data roots. Preserve its environment and backup/lock files.
2. Install 0.1.1 into a **new dedicated venv**, then run `version`, `doctor` and the [synthetic walkthrough](offline-data.md). Do not replace a running legacy service environment in place.
3. Export the new resources to a separate reviewed directory. Compare API calls and imports; custom Python consumers must use `olav_skills`. There is no alias that silently redirects old imports.
4. Resolve same-name skills and duplicate project/user registrations explicitly. Install into the selected harness scope from the new interpreter. Existing manual directories are not implicitly installer-owned. See [ownership and upgrade behavior](installation.md).
5. Set a new `OLAV_HOME` and test a copied, authorized dataset. Check exact device sets, parser gaps, timestamps, bounded results and output files before moving that consumer.
6. Verify actual harness discovery and model behavior separately. Keep the prior venv/resources available until the consumer has passed its own acceptance checks.

The `.olav` layout and environment names continue, but this is **not a universal historical database compatibility guarantee**. These commands do not convert user data automatically. Restoring the prior venv does not undo writes to data; rollback needs an authorized backup or disposable copy.

## Source and integration boundaries

This repository is the maintained native source. Its `provenance/` records the initial official extraction and namespace/resource transformations. Future native changes belong here; do not regenerate the old public export over it or maintain a second runtime copy.

Open WebUI integration, legacy API-client and DuckLake server consumers have separate contracts. Native package tests do not validate their deployments or authorize a service switch. The package's own [portable knowledge exporter](portable.md) is available, but preserved workflows are metadata-only and no simulator or workflow engine is included. Old platform harness, memory, service scheduling and automatic model loops are outside the standalone product.

## Legacy release status

The old `olav` and `olav-netops` projects received `0.27.0.post1` retirement-notice releases. Their historical distributions and yank states are retained; PyPI's separate Archived status still requires an owner action. Do not interpret a notice release as removal of old files or a source-repository archive. Existing consumers still need their own migration checks.

Follow this current manual for Skills commands and use the [legacy archive](legacy-index.md) only for historical context. Publishing, source synchronization, old-package archival and deployment are distinct operations. Updating local docs does not change a deployed service or republish the immutable 0.1.1 artifact. The existing [license](license.md) and notices remain applicable.
