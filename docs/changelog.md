# Olav Skills release history

This history belongs to the standalone `olav-skills` product. The former platform's version history is preserved in the [legacy documentation archive](legacy-index.md), not continued as this package's semantic version sequence.

## 0.2.2

- Compare two exact recorded topology endpoints without reading unrelated parents.
- Read historical path candidates within the requested hop and interface scope.
- Check recorded plan targets separately from proposed-address evidence.

## 0.2.1

- Use English throughout the bundled skill instructions, reference keywords,
  and workflow triggers.
- Ask the host to reply in the user's language unless the user requests another.
  This applies to model replies guided by the skills. Integration-owned
  templates have their own language settings.
- After upgrading, rerun the skill installation command for your host to
  refresh the bound files. [Installation](installation.md)

## 0.2.0

Olav Skills 0.2 adds recorded state/configuration/route/topology changes,
interface-counter deltas, historical path candidates and fixed-metric baselines;
parser version management and derived replay; bounded export and evidence
bundles; investigation/audit profiles, report citation checks, plan pre-checks,
recorded OSPF analysis and explicit evidence-readiness policies. Official native
imports retain new historical raw/provenance within the database transaction.

Each feature has its own vendor, source, time and completeness limits. These
are offline recorded observations, never change approval or proof of live
health. See [compatibility](reference/compatibility.md) for the tested CLI and harness
scopes, including the failed Pi natural-question qualification. The separately
configured private integration controls the tools available in Open WebUI.
[Installation](installation.md) · [Local wheel walkthrough](release-candidate.md) · [Open WebUI guide](openwebui.md)

## 0.1.1 — 17 November 2026

Published to production PyPI after fresh, non-editable package validation. The single distribution contains:

- Deterministic Python code under `olav_skills`, with no dependency on the old `olav` or `olav-netops` distributions or an agent SDK.
- Seven standard skill resource directories and `olav-skills` management/dispatch CLI.
- Explicit interpreter/ownership binding for Codex, Claude Code and Pi project/user installation layouts.
- A public synthetic two-host offline bundle and executable validation/import/query/diagram/evidence/report walkthrough.
- A bounded, deterministic portable knowledge exporter with resource hashes, license and metadata-only workflow boundaries.

Package/workflow verification and native no-model discovery are different evidence levels; see [compatibility](reference/compatibility.md). This release does not supply a model harness, collector connection, memory service, simulator or workflow execution engine.

## 0.1.0 — 17 November 2026, local source candidate

Promoted the generated native skill pack into maintained source. Deterministic domain code, standard skill resources, tests and provenance were extracted using the official legacy generator in an isolated scratch tree, then transformed into the new namespace. Source-only packaging and synthetic workflow checks passed. This local version was not the public PyPI release.

## Documentation updates

The current manual expands installation and offline guides into bilingual capability, workflow, result-boundary, migration and reference pages. Documentation changes do not replace the already published 0.1.1 wheel or imply that a live docs service has switched. Source synchronization, package publication and website deployment are separate release actions.

## Legacy distributions

The old `olav` and `olav-netops` projects have `0.27.0.post1` retirement-notice releases. Historical files and yank states remain. PyPI Archived status is a separate owner action and is not established by those releases. Follow [migration](migration.md) before moving an old consumer; source availability does not change the [license](license.md).
