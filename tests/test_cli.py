"""CLI boundary tests use only temporary user/project homes."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from olav_skills import cli


@pytest.mark.parametrize("harness,relative", [(name, paths["project"]) for name, paths in cli.HARNESS_PATHS.items()])
def test_install_binding_repeat_and_refuse_local_edits(tmp_path, harness, relative, capsys):
    args = ["install", "--harness", harness, "--scope", "project", "--project", str(tmp_path)]
    assert cli.main(args) == 0
    capsys.readouterr()
    target = tmp_path / relative / "analyzer"
    runtime = json.loads((target / "olav-runtime.json").read_text())
    assert runtime["python"] == sys.executable
    assert str(tmp_path) == runtime["binding"]["root"]
    assert sys.executable in (target / "SKILL.md").read_text()
    assert cli.main(args) == 0
    local_file = target / "my-notes.txt"
    local_file.write_text("user content")
    assert cli.main(args) == 2
    assert local_file.read_text() == "user content"


def test_unowned_collision_preflight_preserves_all(tmp_path):
    destination = tmp_path / ".agents/skills"
    existing = destination / "writer"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("mine")
    assert cli.main(["install", "--harness", "codex", "--scope", "project", "--project", str(tmp_path)]) == 2
    assert not (destination / "analyzer").exists()
    assert (existing / "SKILL.md").read_text() == "mine"


def test_symlink_destination_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".agents").symlink_to(outside, target_is_directory=True)
    assert cli.main(["install", "--harness", "codex", "--scope", "project", "--project", str(tmp_path)]) == 2
    assert list(outside.iterdir()) == []


def test_export_repeat_and_user_scope(tmp_path):
    output = tmp_path / "export"
    assert cli.main(["export", str(output)]) == 0
    assert cli.main(["export", str(output)]) == 0
    assert (output / "topology/scripts/query_topology.py").is_file()
    assert cli.main(["install", "--harness", "pi", "--scope", "user", "--home", str(tmp_path)]) == 0
    assert (tmp_path / ".pi/agent/skills/netops/SKILL.md").is_file()


@pytest.mark.parametrize("skill,script", [("../analyzer", "execute_sql"), ("analyzer", "../execute_sql"), ("analyzer", "/tmp/arbitrary.py"), ("analyzer", "execute_sql.py/../x"), ("unknown", "execute_sql"), ("analyzer", "does_not_exist"), ("analyzer", "_helper")])
def test_dispatch_rejects_invalid_paths(skill, script):
    assert cli.main(["run", skill, script]) == 2


def test_project_requires_explicit_binding():
    assert cli.main(["install", "--harness", "codex", "--scope", "project"]) == 2


def test_dispatch_keeps_stdin_and_exit_code(tmp_path, monkeypatch, capfd):
    scripts = tmp_path / "analyzer/scripts"
    scripts.mkdir(parents=True)
    (scripts / "echo.py").write_text("import sys,json; print(json.dumps(json.load(sys.stdin))); sys.exit(7)")
    monkeypatch.setattr(cli, "resource_root", lambda: tmp_path)
    completed = subprocess.run([sys.executable, "-c", "import sys; from pathlib import Path; from olav_skills import cli; cli.resource_root=lambda:Path(sys.argv[1]); raise SystemExit(cli.main(['run','analyzer','echo']))", str(tmp_path)], input='{"marker": 42}', text=True, capture_output=True)
    assert completed.returncode == 7
    assert json.loads(completed.stdout) == {"marker": 42}


@pytest.mark.parametrize("marker", ["[]", "null", "{broken"])
def test_malformed_ownership_marker_preserves_destination(tmp_path, marker):
    output = tmp_path / "export"
    output.mkdir()
    (output / cli.MARKER).write_text(marker)
    (output / "notes").write_text("mine")
    assert cli.main(["export", str(output)]) == 2
    assert (output / "notes").read_text() == "mine"


def test_pi_user_default_layout_ignores_agent_dir_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom-agent-dir"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(custom))
    assert cli.main(["install", "--harness", "pi", "--scope", "user", "--home", str(tmp_path)]) == 0
    skill = tmp_path / ".pi/agent/skills/analyzer"
    assert (skill / "SKILL.md").is_file()
    assert json.loads((skill / "olav-runtime.json").read_text())["binding"]["scope"] == "user"
    assert not (tmp_path / ".pi/skills").exists()
    assert not custom.exists()
