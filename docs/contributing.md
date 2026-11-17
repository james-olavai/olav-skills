# Contribute to Olav Skills

The maintained public source is [james-olavai/olav-skills](https://github.com/james-olavai/olav-skills). Read repository instructions and LICENSE before proposing a change. The native product's source is maintained directly; do not regenerate a legacy export over it.

## Find the owning surface

| Path | Responsibility |
| --- | --- |
| `src/olav_skills/` | Deterministic library, management CLI and required package data |
| `skills/` | Authoritative standard instructions, scripts and references |
| `examples/`, `tests/` | Public synthetic examples and meaningful regressions |
| `docs/` | Current public manual and separately preserved legacy material |
| `provenance/` | Extraction/source mapping and transformation records |
| `tools/` | Local build/export/release helpers |

Keep the single distribution and `olav_skills` import boundary. Do not introduce dependencies on the retired platform or agent SDKs to implement native functionality. Skill instructions do not grant host permissions. Collector, private WebUI, model services and server maintenance require their own project contracts.

## Make changes reproducible

Develop in a dedicated venv; source installation can use `python -m pip install .`. For release verification, build a wheel/sdist and install it non-editably into a fresh environment outside the checkout. The build backend is Hatchling; `python -m build` requires the separate `build` frontend in the developer environment. Never equate an editable import with a successful distribution test.

Before executing data code, pin `OLAV_HOME` to a new scratch directory. Use synthetic captures and independently expected sets/rows. Include missing-source, unknown-time, role-unknown, truncation and refusal cases relevant to the change. The repository uses pytest for `tests/`; install your chosen test tooling separately. Run the smallest meaningful affected checks, then the required release checks for a release change. The [installed walkthrough](offline-data.md) verifies more than import success.

Preserve bounded SQL behavior, unknown values, stable data-root selection, parser provenance and artifact evidence. If a response or script argument changes, update both language pages and the actual resource instructions/contracts. Do not hand-maintain a second generated runtime.

## Report a useful defect

Use the [issue tracker](https://github.com/james-olavai/olav-skills/issues) with package/Python version, interpreter, exact public entry point, minimal synthetic stdin, expected semantic result, actual JSON, exit code and separate stderr. Remove credentials, customer captures, live configuration, private deployment paths and internal implementation evidence. Keep the first failure rather than replacing it with only a later passing run.

For natural-language behavior, record the original ordinary question, actual harness/model/settings, active skills, tool arguments/results and final answer. Label a specified-script test as a tool-contract test. Package execution and discovery are separate from model correctness. Preserve license notices and avoid unsupported performance or compatibility claims.
