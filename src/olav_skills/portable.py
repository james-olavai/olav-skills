"""Export installed skill knowledge, never host permissions or a workflow runner.

Catalog shape follows the legacy public portable profile (DATATECHIE PTY LTD),
reimplemented here against the maintained package resources. See LICENSE.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata, resources
import json
from pathlib import Path
import posixpath
import re
import shutil
import sys
import tempfile
from urllib.parse import unquote, urlsplit

import yaml

from . import __version__

SKILLS = ("analyzer", "importer", "learner", "netops", "reporter", "topology", "writer")
SCHEMA = "olav.portable-skills/v1"
MAX_RESOURCE_BYTES = 65536
MAX_TOTAL_BYTES = 2097152
MAX_RESOURCES = 512
MAX_ENTRIES = 2048
MAX_CATALOG_BYTES = 262144
KINDS = {"references": "reference", "scripts": "script", "_lib": "library", "workflows": "workflow"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: str) -> str:
    if (not isinstance(value, str) or not value or any(c in value for c in "\\:\0")
            or value.startswith("/") or any(p in ("", ".", "..") for p in value.split("/"))):
        raise ValueError("unsafe resource path")
    return value


def no_links(path: Path) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink refused")


def source_root() -> Path:
    # Wheels install ordinary filesystem resources. No checkout fallback, legacy
    # lookup, environment override, model-supplied namespace or network access.
    root = Path(str(resources.files("olav_skills").joinpath("resources", "skills")))
    no_links(root)
    if not root.is_dir():
        raise ValueError("installed skill resources missing; install a built wheel")
    return root


def license_bytes() -> bytes:
    dist = metadata.distribution("olav-skills")
    matches = [f for f in dist.files or [] if f.name == "LICENSE" and ".dist-info" in str(f)]
    if len(matches) != 1:
        raise ValueError("installed distribution license missing or ambiguous")
    path = Path(dist.locate_file(matches[0]))
    no_links(path)
    if not path.is_file() or path.stat().st_size > MAX_RESOURCE_BYTES:
        raise ValueError("invalid installed license")
    return path.read_bytes()


def frontmatter(data: bytes) -> dict:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", data.decode("utf-8"), re.S)
    if not match:
        raise ValueError("missing skill frontmatter")
    value = yaml.safe_load(match[1])
    if not isinstance(value, dict):
        raise ValueError("invalid skill frontmatter")
    return value


def validate_links(files: dict[str, bytes], entries: list[dict]) -> None:
    """Small fail-closed Markdown grammar; code examples are not links."""
    inline = re.compile(r'''\]\(\s*(?:<([^>\n]*)>|([^\s)]+))(?:\s+(?:"[^"\n]*"|'[^'\n]*'|\([^\n)]*\)))?\s*\)''')
    definitions = re.compile(r'''(?m)^ {0,3}\[([^]\n]+)\]:[ \t]*(?:<([^>\n]*)>|([^\s]+))(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'|\([^\n)]*\)))?[ \t]*$''')
    for item in entries:
        if item["kind"] not in ("skill", "reference"):
            continue
        dest = item["path"]
        text = files[dest].decode("utf-8")
        text = re.sub(r'(?ms)^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[ \t]*$', '', text)
        text = re.sub(r'(`+)(.*?)\1', '', text, flags=re.S)

        def validate(value: str) -> None:
            parts = urlsplit(value)
            if parts.scheme in ("http", "https", "mailto") or value.startswith("#"):
                return
            if parts.scheme or parts.netloc or "\\" in value or parts.query:
                raise ValueError("unsafe reference URL")
            target = unquote(parts.path)
            if target.startswith("/") or any(c in target for c in "\\:\0"):
                raise ValueError("unsafe resource reference")
            candidate = posixpath.normpath(posixpath.join(posixpath.dirname(dest), target))
            if candidate.startswith("../") or candidate not in files:
                raise ValueError(f"dangling or escaping resource reference: {dest}")

        matched = {m.start() for m in inline.finditer(text)}
        if any(m.start() not in matched for m in re.finditer(r'\]\(', text)):
            raise ValueError("unsupported Markdown link syntax")
        for match in inline.finditer(text):
            validate(match[1] if match[1] is not None else match[2])
        matched = {m.start() for m in definitions.finditer(text)}
        if any(m.start() not in matched for m in re.finditer(r'(?m)^ {0,3}\[[^]\n]+\]:', text)):
            raise ValueError("unsupported Markdown definition syntax")
        labels = set()
        for match in definitions.finditer(text):
            label = ' '.join(match[1].split()).casefold()
            if label in labels:
                raise ValueError("duplicate Markdown reference label")
            labels.add(label)
            validate(match[2] if match[2] is not None else match[3])
        for match in re.finditer(r'\[([^]\n]+)\]\[([^]\n]*)\]', text):
            if ' '.join((match[2] or match[1]).split()).casefold() not in labels:
                raise ValueError("undefined Markdown reference label")


def materialize() -> tuple[dict, dict[str, bytes]]:
    root = source_root()
    files, entries, skills, workflows = {}, [], [], []
    count = 0
    total = 0
    # Validate all entries, even unindexed symlinks and ignored bytecode caches.
    for path in root.rglob("*"):
        count += 1
        if count > MAX_ENTRIES:
            raise ValueError("entry budget exceeded")
        no_links(path)
        if not path.is_dir() and not path.is_file():
            raise ValueError("non-regular resource")
    if set(p.name for p in root.iterdir()) != set(SKILLS):
        raise ValueError("unexpected installed skill set")
    for skill in SKILLS:
        local = []
        for source in sorted((root / skill).rglob("*")):
            if not source.is_file():
                continue
            rel = source.relative_to(root).as_posix()
            safe_relative(rel)
            parts = rel.split("/")
            if "__pycache__" in parts or source.suffix == ".pyc":
                continue
            if parts[1:] == ["SKILL.md"]:
                kind = "skill"
            elif len(parts) >= 3 and parts[1] in KINDS:
                kind = KINDS[parts[1]]
                if ((kind in ("script", "library") and source.suffix != ".py")
                        or (kind in ("reference", "workflow") and source.suffix not in (".md", ".yaml", ".yml"))):
                    raise ValueError("unsupported resource type")
            else:
                raise ValueError("unexpected installed resource")
            if source.stat().st_size > MAX_RESOURCE_BYTES:
                raise ValueError("resource byte budget exceeded")
            data = source.read_bytes()
            total += len(data)
            if len(data) > MAX_RESOURCE_BYTES or total > MAX_TOTAL_BYTES or len(files) >= MAX_RESOURCES:
                raise ValueError("resource budget exceeded")
            dest = "skills/" + rel
            files[dest] = data
            local.append(dest)
            entries.append({"path": dest, "kind": kind, "bytes": len(data), "sha256": digest(data),
                            "source": {"path": dest, "sha256": digest(data)}})
            if kind == "workflow":
                workflow = yaml.safe_load(data)
                if (not isinstance(workflow, dict) or workflow.get("schema_version") != 1
                        or not re.fullmatch(r"[a-z0-9_]+", str(workflow.get("name", "")))
                        or not isinstance(workflow.get("steps"), list) or not workflow["steps"]):
                    raise ValueError("invalid workflow")
                required = set()
                for step in workflow["steps"]:
                    if (not isinstance(step, dict) or not isinstance(step.get("agent"), str)
                            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", step["agent"])
                            or not isinstance(step.get("prompt"), str)):
                        raise ValueError("invalid workflow step")
                    required.add("agent:" + step["agent"])
                workflows.append({"id": skill + "." + workflow["name"], "path": dest,
                                  "requires_capabilities": sorted(required),
                                  "missing_capabilities": sorted(c for c in required if c[6:] not in SKILLS),
                                  "executable": False, "execution_status": "metadata-only"})
        skill_path = f"skills/{skill}/SKILL.md"
        if skill_path not in files:
            raise ValueError("missing skill body")
        front = frontmatter(files[skill_path])
        description = front.get("description")
        if front.get("name") != skill or not isinstance(description, str) or not description.strip() or len(description.encode()) > 2048:
            raise ValueError("invalid skill identity or description")
        capabilities = sorted("script:" + skill + "/" + p.rsplit("/", 1)[1]
                              for p in local if "/scripts/" in p and not p.rsplit("/", 1)[1].startswith("_"))
        skills.append({"id": skill, "name": skill, "description": description, "intents": [],
                       "skill_path": skill_path, "resources": sorted(local), "requires_capabilities": capabilities})
    if len({w["id"] for w in workflows}) != len(workflows):
        raise ValueError("duplicate workflow identity")
    validate_links(files, entries)
    catalog = {"schema": SCHEMA, "version": 1, "profile": "portable", "pack": "olav-skills",
               "distribution": {"name": "olav-skills", "version": __version__},
               "skills": skills, "resources": sorted(entries, key=lambda e: e["path"]),
               "workflows": sorted(workflows, key=lambda w: w["id"])}
    data = (json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if len(data) > MAX_CATALOG_BYTES:
        raise ValueError("catalog byte budget exceeded")
    files["catalog.json"] = data
    files["LICENSE"] = license_bytes()
    files["README.md"] = ("# Olav portable skill knowledge\n\n"
                          "Resources retain installed source bytes and source hashes. Standard skill text is knowledge, "
                          "not host commands. Requirements grant no tools, shell, filesystem, database or delegation. "
                          "Scripts need a separately provisioned runtime and host authorization. Workflows are "
                          "metadata-only, executable=false; absent agents (including simulator) are explicit. "
                          "No workflow engine or private host tool mappings are included. See LICENSE.\n").encode()
    return catalog, files


def export(out: str) -> dict:
    path = Path(out)
    if not path.is_absolute() or str(path) != out or str(path.resolve()) != out:
        raise ValueError("--out must be a canonical absolute new directory")
    no_links(path)
    if path.exists():
        raise ValueError("--out must be a new directory")
    root = source_root().resolve()
    if path == root or root in path.parents or path in root.parents:
        raise ValueError("output overlaps installed resources")
    catalog, files = materialize()  # Invalid/missing sources cannot create output.
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".olav-portable-", dir=path.parent))
    try:
        for relative, data in sorted(files.items()):
            target = staging / safe_relative(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        # mkdir is an exclusive claim; never replace an existing destination.
        path.mkdir()
        for item in staging.iterdir():
            item.rename(path / item.name)
    except BaseException:
        # Do not remove a destination that may have been created by another actor.
        raise
    finally:
        shutil.rmtree(staging)
    return catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        catalog = export(args.out)
    except (ValueError, OSError, yaml.YAMLError, UnicodeError) as exc:
        print(f"olav-skills portable: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"skills": len(catalog["skills"]), "resources": len(catalog["resources"]),
                      "workflows": len(catalog["workflows"]), "version": __version__}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
