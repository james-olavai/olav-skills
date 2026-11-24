# Review an Olav Skills 0.2.2 wheel locally

Use a supplied **0.2.2** wheel to run the synthetic workflow in an isolated
environment with Python 3.11 or newer and a POSIX shell. The fixture and tools
run without a model, credentials or device access.
[Standard installation](installation.md)

## Install the runtime and bind the skills

Set `WHEEL` to the supplied wheel and `WHEELHOUSE` to a directory
of compatible dependency wheels. Compare the wheel's SHA-256 with the value
supplied by your reviewer before installing. Use a new work directory so the
exercise starts with an empty home and project.

```sh
WHEEL=/absolute/reviewed/olav_skills-0.2.2-py3-none-any.whl
WHEELHOUSE=/absolute/reviewed/dependency-wheels
python3 -c 'from pathlib import Path; import hashlib, sys; print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())' "$WHEEL"

WORK=$(mktemp -d)
WORK=$(cd "$WORK" && pwd -P)
REVIEW_HOME="$WORK/home"
HARNESS_PROJECT="$WORK/project"
export OLAV_HOME="$WORK/analysis"
mkdir -p "$REVIEW_HOME" "$HARNESS_PROJECT"
cd "$WORK"

python3 -m venv "$WORK/venv"
SKILLS_PYTHON="$WORK/venv/bin/python"
"$SKILLS_PYTHON" -m pip --isolated install --no-index --find-links "$WHEELHOUSE" "$WHEEL"

skills() {
    env -i PATH="$PATH" HOME="$REVIEW_HOME" OLAV_HOME="$OLAV_HOME" \
        "$SKILLS_PYTHON" -I -B -m olav_skills "$@"
}

skills version
skills doctor
skills list
skills install --harness pi --scope project --project "$HARNESS_PROJECT"
```

Expect version `0.2.2`, runtime doctor `ok: true` and seven skills. This example
writes the Pi project layout under `"$HARNESS_PROJECT/.pi/skills"`. Each
`olav-runtime.json` records the version and `$SKILLS_PYTHON` path. Choose
`codex` or `claude-code` for those layouts. Starting a harness is optional for
the remaining CLI steps. [Project and user layouts](installation.md#project-scope-and-discovery-layout)

## Import the synthetic bundle

```sh
"$SKILLS_PYTHON" -I -B -m olav_skills.examples.synthetic_walkthrough --home "$OLAV_HOME"

SNAPSHOT=$("$SKILLS_PYTHON" -I -B -c \
    'import json, sys; from pathlib import Path; print(json.loads(Path(sys.argv[1]).read_text())["snapshot_id"])' \
    "$OLAV_HOME/import.json")
```

The installed walkthrough validates and imports six command outputs, checks the
exact inventory set `R1`, `R2`, and writes a Mermaid diagram with one recorded
CDP edge. It saves the import receipt, a bounded R1 evidence record and an
inventory report under `$OLAV_HOME`. Use the exact snapshot ID from the receipt
throughout the rest of the exercise. [Walkthrough outputs](offline-data.md#run-the-complete-synthetic-example)

## Check readiness, observed devices and topology

```sh
skills doctor --data --project "$OLAV_HOME" --snapshot-id "$SNAPSHOT" > data-doctor.json

"$SKILLS_PYTHON" -I -B - "$SNAPSHOT" <<'PY' | skills run analyzer execute_sql > observed-devices.json
import json
import sys

print(json.dumps({
    "sql": "SELECT DISTINCT device_name AS hostname FROM netops.parsed_outputs "
           "WHERE snapshot_id = ? ORDER BY hostname",
    "params": [sys.argv[1]],
    "max_rows": 10,
}))
PY

"$SKILLS_PYTHON" -I -B -c \
    'import json, sys; print(json.dumps({"concept": "all", "snapshot_id": sys.argv[1]}))' \
    "$SNAPSHOT" | skills run topology query_topology > topology-query.json
```

| Output | Expected evidence |
| --- | --- |
| `data-doctor.json` | `status: success`, `data_readiness: recorded_queries_available`, same snapshot, six parsed rows and two observed devices. Collection and parsing coverage remain unknown. |
| `observed-devices.json` | Exact snapshot device set `R1`, `R2`, `has_more: false`, `truncated: false`. This query uses parsed observations from the selected snapshot. |
| `topology-query.json` | Same snapshot, one CDP record from R2 GigabitEthernet1 to R1 GigabitEthernet2. The OSPF layer is unavailable, with `row_count: null` under `result_contract.layers.ospf`. Its relationships remain unknown. |

## Pre-check a draft change plan

An engineer or the host drafts the plan. The example text below only exercises
the structured request against the synthetic R2 record.

```sh
"$SKILLS_PYTHON" -I -B - "$SNAPSHOT" <<'PY' | skills run analyzer check_change_plan > plan-precheck.json
import json
import sys

print(json.dumps({
    "snapshot_id": sys.argv[1],
    "plan": {
        "schema": "olav.change-plan/v1",
        "targets": [{
            "device": "R2",
            "platform": "cisco_ios",
            "template": "cisco_ios",
            "steps": ["Record the proposed fixture interface description"],
            "rollback": ["Restore the previous fixture interface description"],
            "verification": [{
                "command": "show ip interface brief",
                "expected": "Review the recorded fixture interface state",
            }],
        }],
    },
}))
PY
```

Expect `execution_status: ok` and `checks_outcome: unknown`. For the recorded
R2 source, the target and platform checks pass, and the draft includes rollback
and verification. Syntax, reachability and simulation remain unknown. The
pre-check always returns `decision: not_approved`.
[Pre-check contract](reference/plan-precheck.md)

## Rebind after an upgrade or rollback

Pip changes the installed runtime and its bundled resources. To refresh the
copied skills, rerun the same `install` command from the desired venv using the
same harness, scope and root. For rollback, use the retained prior venv, or
reinstall the reviewed prior wheel in the original venv and then rebind. Verify
the version, interpreter and ownership manifest for each skill. Installation
refuses local edits, extra files, symlinks and foreign ownership. Rolling back
the environment and binding leaves imported data and artifacts in place.
[Install and rollback details](installation.md#upgrade-roll-back-or-remove)

## Harness qualification

Local package checks exercised installation layout and specified scripts
on synthetic data. An earlier test of the 0.2 code in Pi failed natural-question
qualification, including the held-out wording. Codex and Claude Code natural
questions were untested.
[Compatibility and evidence levels](reference/compatibility.md)
