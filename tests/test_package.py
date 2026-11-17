"""Distribution contract; usable in source checkout or installed wheel."""
from pathlib import Path
import tomllib

from olav_skills import __version__, cli


def test_all_skill_resources_and_scripts_present():
    root = cli.resource_root()
    for name in cli.SKILLS:
        assert (root / name / "SKILL.md").is_file()
        assert list((root / name / "scripts").glob("*.py"))


def test_distribution_has_only_standalone_dependencies():
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    metadata = tomllib.loads(project_file.read_text())["project"]
    assert metadata["name"] == "olav-skills"
    assert metadata["version"] == __version__
    assert len(metadata["dependencies"]) == 8
    assert not any(item.lower().startswith(("olav", "deepagents", "langgraph", "langchain")) for item in metadata["dependencies"])
