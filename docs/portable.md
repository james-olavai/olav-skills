# Portable knowledge export

Install the `olav-skills` 0.2.2 wheel into a dedicated Python environment,
then run from any working directory:

```sh
/absolute/path/to/skills-venv/bin/python -I -B -m olav_skills.portable --out /absolute/new-directory
```

The output directory must be new, canonical, outside the installed resource
root and reached without symlinks. This command reads the current installed
package only. It does not discover a legacy checkout, load dotenv files, contact
models or databases, install dependencies, execute scripts, or grant host tools.
An uninstalled source checkout cannot stand in for installed wheel resources.

The `olav.portable-skills/v1` catalog (`version: 1`, `profile: portable`)
indexes all seven skills. Original SKILL.md text, references, Python scripts
and skill libraries retain their exact bytes. Each resource includes its
relative source path, SHA-256 and byte count; catalog entries own every resource.
The exporter includes the distribution LICENSE and a knowledge boundary README.
Catalog distribution metadata identifies the producing package version. No
absolute source paths or host tool mappings enter the catalog.

`script:<skill>/<filename.py>` and `agent:<skill>` capabilities describe
requirements, never grants. A host must independently authorize its actual
registered tools, validate catalog hashes and paths, and enforce its read and
execution limits. Source examples are domain knowledge, not host commands.
Scripts in this export are materials, not a standalone installed Python runtime.
This exporter is independent of any private integration.

Two public root network workflows are preserved under
`skills/netops/workflows/`. Their catalog entries always report
`execution_status: metadata-only`, `executable: false` and missing
`agent:simulator`; no workflow orchestrator is supplied. Original steps remain
intact. See `provenance/adapter-portable-20261117.json` for origin, byte hashes and
license continuity; these files are now maintained with the native skill source.

Bounds match a small knowledge loader: 64 KiB per resource, 2 MiB total indexed
resource bytes, 512 resources, 2048 scanned entries and 256 KiB catalog JSON.
Symlinks (including in ignored cache directories), nonregular files, unknown
skill directories, unsupported material types, malformed skill/workflow metadata
and dangling or escaping Markdown links are refused. Bytecode caches are
excluded. A bounded Markdown grammar supports inline links, reference definitions,
anchors and HTTP(S)/mailto links; code examples are excluded from link checking.
Invalid or missing resources fail before creating the output directory. Output
bytes are deterministic for fixed installed resources and version.

This is packaging and knowledge portability. It does not establish natural
language routing, host permissions, model correctness, complete workflow
execution, or production deployment acceptance.
