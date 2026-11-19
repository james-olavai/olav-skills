# Compatibility and verification levels

The current release is the unified **`olav-skills` 0.1.1**, published on PyPI. Its distribution, import name and CLI are separate from the retired platform packages. Python 3.11 or newer is required by package metadata. Deterministic scripts need the installed dependencies; portable knowledge alone is not that runtime.

## Published 0.1.1

| Surface | Verified scope | Limit |
| --- | --- | --- |
| Production-index install | Fresh non-editable 0.1.1 install, version/resources/dependencies | Does not establish every OS/Python combination |
| Synthetic offline workflow | Bundle validation/import, exact R1/R2 set, one CDP link artifact, R1 evidence and Markdown report | Invented input, no live network or model |
| Six installation layouts | Project/user scopes for Codex, Claude Code and Pi; interpreter/ownership behavior | Filesystem layout is not model activation or reasoning |
| Codex 0.169.0 | Native no-model skill discovery | No natural-language workflow acceptance |
| Pi 1.3.0 | Native no-model skill discovery | No natural-language workflow acceptance |
| Claude Code 2.1.323 | Filesystem discovery layout | Native discovery/model behavior not verified in this batch |
| Portable exporter | Seven-skill hashed catalog and bounded knowledge handoff | No runtime, tool permission or workflow engine |

## Unpublished 0.2.0 candidate

| Check | Result and scope |
| --- | --- |
| Layout and explicit rebinding | Passed for Codex, Claude Code and Pi, each at project and user scope. |
| Specified script execution | Passed for the local CLI on the public synthetic workflow. The isolated Pi SDK session also discovered all seven skills and invoked specified tools. |
| Pi natural questions | Failed qualification in the tested Pi SDK 1.2.0 / DeepSeek V4 Pro setup. The final candidate also failed the held-out wording checks. Failures included missed tool calls, incorrect snapshot arguments and answers with claims beyond the evidence. |
| Codex 0.169.0 and Claude Code 2.1.323 | This candidate was checked for install layout and version/help only. Native discovery in these hosts, host tool execution and natural questions were untested with this candidate. |

Use the provided results for the tested versions, data and tools. Actual
behavior depends on the host model, tool permissions, context, active scope
and discovery configuration. [Candidate walkthrough](../release-candidate.md)

## Data and feature compatibility

The `.olav` layout and `OLAV_HOME` name continue, but historical database compatibility requires testing on an authorized copy. Available command views depend on inputs/parsers. Bounded metadata health checks, built-in topology relationships and vendor-specific parsed views have different coverage; the presence of one does not prove the others. Inspect actual context, missing sources and `absent_layers`.

Native offline DuckDB is the reproducible flow in this manual. Retained server/API-client/DuckLake consumers and private WebUI integrations have independent contracts. A package install or local candidate test does not validate or switch those deployments.

The public pack includes no collector connection, simulator, automatic memory/learning loop, generic agent harness, scheduling service or executable workflow runner. Two preserved portable workflows explicitly remain metadata-only with a missing simulator capability. Source references to absent historical tools do not add them to the product.

## Upgrade acceptance

Install a reviewed version in a new venv, run package checks and the synthetic example, reinstall skills from that interpreter and verify your actual host discovery. Then test the intended real workflow against an authorized data copy. Keep original natural-language questions fixed, inspect actual calls and final answers, and retain failures. A tool-contract test that specifies script names proves only that invocation path.

Record model/provider version, settings, dataset, prompt version, cache/order, token/tool counts and semantic correctness if making model or performance comparisons. Unknown metrics remain unknown. No speedup, cost reduction or cross-harness model-quality claim follows from the deterministic checks above. See [installation](../installation.md) and [migration](../migration.md) for reversible upgrade boundaries.
