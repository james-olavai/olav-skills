"""Discover, export and dispatch standard skills with an explicit interpreter."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

from . import __version__

SKILLS = ("analyzer", "importer", "learner", "netops", "reporter", "topology", "writer")
MARKER = ".olav-skills-install.json"
DEPENDENCIES = ("duckdb", "yaml", "pydantic", "textfsm", "ntc_templates", "networkx", "netutils", "netconan")
HARNESS_PATHS = {
    "claude-code": {"project": ".claude/skills", "user": ".claude/skills"},
    "codex": {"project": ".agents/skills", "user": ".agents/skills"},
    "pi": {"project": ".pi/skills", "user": ".pi/agent/skills"},
}


def resource_root() -> Path:
    installed = Path(__file__).parent / "resources" / "skills"
    if installed.is_dir():
        return installed
    # Source checkout only; wheels always contain resources above.
    source = Path(__file__).resolve().parents[2] / "skills"
    if source.is_dir():
        return source
    raise ValueError("skill resources missing; reinstall olav-skills")


def interpreter() -> str:
    # Keep the venv path: resolving its symlink would select the system Python.
    return os.path.abspath(sys.executable)


def inventory(path: Path) -> dict[str, str]:
    result = {}
    for entry in sorted(path.rglob("*")):
        if entry.is_symlink():
            raise ValueError(f"symlink refused: {entry}")
        if entry.is_file() and entry != path / MARKER:
            result[entry.relative_to(path).as_posix()] = hashlib.sha256(entry.read_bytes()).hexdigest()
    return result


def ensure_safe_path(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f"symlink destination refused: {parent}")


def preflight(path: Path, binding: dict) -> None:
    ensure_safe_path(path)
    if not path.exists():
        return
    marker = path / MARKER
    if not path.is_dir() or not marker.is_file():
        raise ValueError(f"unowned destination refused: {path}")
    metadata = json.loads(marker.read_text())
    if not isinstance(metadata, dict) or metadata.get("owner") != "olav-skills" or metadata.get("binding") != binding:
        raise ValueError(f"destination ownership/binding mismatch: {path}")
    if metadata.get("files") != inventory(path):
        raise ValueError(f"locally changed destination refused: {path}")


def write_managed(path: Path, sources: dict[str, Path], binding: dict) -> None:
    preflight(path, binding)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".olav-skills-", dir=path.parent))
    try:
        for name, source in sources.items():
            target = staging / name if name else staging
            inventory(source)  # Refuse resource symlinks before following them.
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        runtime = {"distribution": "olav-skills", "version": __version__, "python": interpreter(), "binding": binding}
        (staging / "olav-runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
        for skill_md in staging.rglob("SKILL.md"):
            skill = skill_md.parent.name if skill_md.parent != staging else path.name
            (skill_md.parent / "olav-runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
            command = shlex.join([interpreter(), "-m", "olav_skills", "run", skill, "<script>"])
            instructions = ("\n## Installed Python binding\n\n"
                            f"Use this exact interpreter for this installation: `{interpreter()}`.\n"
                            f"Dispatch stdin JSON with `{command}` (replace `<script>` with a listed script).\n"
                            "Use this binding for every Python example below; do not substitute another Python environment. "
                            "Inspect `olav-runtime.json` and the installation manifest to verify the binding.\n\n")
            content = skill_md.read_text()
            frontmatter = re.match(r"\A---\n.*?\n---(?:\n|$)", content, flags=re.DOTALL)
            offset = frontmatter.end() if frontmatter else 0
            skill_md.write_text(content[:offset] + instructions + content[offset:])
        (staging / MARKER).write_text(json.dumps({"owner": "olav-skills", "version": __version__, "binding": binding, "files": inventory(staging)}, indent=2) + "\n")
        # All existing contents have been verified as owned and unchanged.
        if path.exists():
            backup = staging.with_name(staging.name + "-old")
            path.rename(backup)
            try:
                staging.rename(path)
            except BaseException:
                backup.rename(path)
                raise
            shutil.rmtree(backup)
        else:
            staging.rename(path)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def run_script(skill: str, script: str) -> int:
    if skill not in SKILLS or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.py)?", script):
        raise ValueError("unknown skill or invalid script name")
    filename = script if script.endswith(".py") else script + ".py"
    scripts = resource_root() / skill / "scripts"
    target = scripts / filename
    if not target.is_file() or target.is_symlink() or target.resolve().parent != scripts.resolve():
        raise ValueError("script is not in the packaged skill allowlist")
    return subprocess.run([interpreter(), str(target)], check=False).returncode


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="olav-skills")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    commands.add_parser("list")
    commands.add_parser("doctor")
    export = commands.add_parser("export")
    export.add_argument("destination", type=Path)
    install = commands.add_parser("install")
    install.add_argument("--harness", choices=HARNESS_PATHS, required=True)
    install.add_argument("--scope", choices=("project", "user"), required=True)
    install.add_argument("--project", type=Path)
    install.add_argument("--home", type=Path)
    run = commands.add_parser("run")
    run.add_argument("skill")
    run.add_argument("script")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "version":
            print(__version__)
        elif args.command == "list":
            root = resource_root()
            print(json.dumps({"version": __version__, "python": interpreter(), "skills": [{"name": skill, "path": str(root / skill), "scripts": sorted(p.name for p in (root / skill / "scripts").glob("*.py") if not p.name.startswith("_"))} for skill in SKILLS]}, indent=2))
        elif args.command == "doctor":
            missing = [name for name in DEPENDENCIES if importlib.util.find_spec(name) is None]
            root = resource_root()
            missing_skills = [name for name in SKILLS if not (root / name / "SKILL.md").is_file()]
            print(json.dumps({"version": __version__, "python": interpreter(), "resources": str(root), "missing_dependencies": missing, "missing_skills": missing_skills, "ok": not missing and not missing_skills}, indent=2))
            return int(bool(missing or missing_skills))
        elif args.command == "run":
            return run_script(args.skill, args.script)
        elif args.command == "export":
            destination = args.destination.absolute()
            binding = {"kind": "export", "destination": str(destination)}
            write_managed(destination, {name: resource_root() / name for name in SKILLS}, binding)
            print(json.dumps({"destination": str(destination), "python": interpreter()}))
        elif args.command == "install":
            if args.scope == "project":
                if args.project is None or args.home is not None:
                    raise ValueError("project scope requires --project and rejects --home")
                base = args.project.absolute()
            else:
                if args.project is not None:
                    raise ValueError("user scope rejects --project")
                base = (args.home or Path.home()).absolute()
            if not base.is_dir():
                raise ValueError("scope root must be an existing directory")
            destination = base / HARNESS_PATHS[args.harness][args.scope]
            binding = {"kind": "install", "harness": args.harness, "scope": args.scope, "root": str(base)}
            # Refuse conflicts across the whole installation before writing.
            for skill in SKILLS:
                preflight(destination / skill, binding)
            for skill in SKILLS:
                write_managed(destination / skill, {"": resource_root() / skill}, binding)
            print(json.dumps({"destination": str(destination), "python": interpreter(), "skills": list(SKILLS)}))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"olav-skills: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
