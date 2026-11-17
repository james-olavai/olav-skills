# Extend parsing with tested samples

Begin with the import report and a clearly identified platform/command pair. A parser gap is different from a command intentionally kept raw or a broken existing parser. Keep the original capture immutable and use a separate synthetic root for this demonstration.

## Run the two-step interface without a model

The following example intentionally uses `demo_os` and invented two-column text. It demonstrates preparation, validation and freezing, not support for a real vendor command. Choose a **new** absolute directory and your installed interpreter:

```sh
SKILLS_PYTHON=/absolute/path/to/skills-venv/bin/python
export OLAV_HOME=/absolute/path/to/new-parser-demo
mkdir -p "$OLAV_HOME"
"$SKILLS_PYTHON" -I -B - <<'PYTHON'
import json, os, subprocess, sys
from pathlib import Path
home = Path(os.environ["OLAV_HOME"])
samples = [
    {"device": "demo1", "raw_output": "alpha up\n"},
    {"device": "demo2", "raw_output": "beta down\n"},
]
payload = {"platform": "demo_os", "command": "show demo status", "samples": samples}
def call(script, value):
    proc = subprocess.run(
        [sys.executable, "-I", "-B", "-m", "olav_skills", "run", "learner", script],
        input=json.dumps(value), text=True, capture_output=True, check=True, cwd=home)
    (home / (script + ".stderr.log")).write_text(proc.stderr)
    result = json.loads(proc.stdout)
    (home / (script + ".json")).write_text(json.dumps(result, indent=2))
    assert result["status"] == "ok", result
    return result
call("prepare_learn", payload)
parser = "# OLAV_DSL: textfsm\nValue NAME (\\S+)\nValue STATE (\\S+)\n\nStart\n  ^${NAME}\\s+${STATE}$$ -> Record\n"
result = call("finish_learn", {**payload, "parser_response": parser})
assert result["parsed_rows"] == [1, 1]
assert result["quarantined"] is False
print(json.dumps(result, indent=2))
PYTHON
```

Inspect the saved drafting prompt and the returned template path. In normal use, a person or the host model writes the candidate between the two calls. Neither script calls a model endpoint. A failing status must be checked even if the process exits zero.

## Review activation and generalization

Successful TextFSM is live immediately, including with one sample. Python candidates need the additional AST/sandbox validation and fewer than two samples are quarantined. Inspect `quarantined`; do not treat a one-sample success as broad acceptance.

Before using a real parser, independently test expected fields and rows on varied holdout samples: different device values, optional columns, multiline variations, no-neighbor output and errors. Ensure the candidate does not encode one device's address or ASN. A pure parser takes text and returns records; it must not read files, execute commands or contact networks.

A frozen parser influences future parses/imports. This example does not reparse old database rows, repair production data or establish compatibility with every firmware. Keep the candidate source, sample provenance, expected results and failed diagnostics for review. Read [learner](../skills/learner.md) for fields and [safety](../safety.md) for write boundaries.
