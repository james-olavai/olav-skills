#!/usr/bin/env python3
"""Inspect local wheel/sdist boundaries without extracting or executing them.

Usage: python tools/release_check.py dist/*.whl dist/*.tar.gz
This checks an explicit archive allowlist and known sensitive paths; it does
not replace human content review or demonstrate publication permissions.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

SKILLS = {"analyzer", "importer", "learner", "netops", "reporter", "topology", "writer"}
SDIST_ROOTS = {".gitignore", "LICENSE", "PKG-INFO", "README.md", "docs", "examples", "provenance", "pyproject.toml", "requirements-lean.txt", "skills", "src", "tests", "tools"}
BAD_PARTS = {".git", ".env", ".private", ".olav", "__pycache__", "node_modules", "artifacts", "deploy", "runtime", "secrets", "private"}
PACKAGED_CONFIG = {"backup_only_commands.yaml", "cron_schedules.yaml", "discovery_protocols.yaml", "platform_profiles.yaml", "user_commands.yaml"}
PACKAGED_RECIPES = {"bgp_arista_eos.yaml", "bgp_cisco_ios.yaml", "bgp_juniper_junos.yaml", "l2_topology.yaml", "ospf_arista_eos.yaml", "ospf_cisco_ios.yaml", "ospf_juniper_junos.yaml"}
BAD_SUFFIXES = {".pyc", ".pyo", ".duckdb", ".sqlite", ".sqlite3", ".pem", ".key", ".pcap", ".pcapng"}


def inspect(path: Path) -> dict:
    assert path.stat().st_size <= 20_000_000, "archive exceeds 20 MB review bound"
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            assert all(not (i.external_attr >> 16 & 0o170000) == 0o120000 for i in entries), "symlink"
            blobs = {i.filename: archive.read(i) for i in entries if not i.is_dir()}
        wheel = True
    else:
        with tarfile.open(path, "r:gz") as archive:
            entries = archive.getmembers()
            assert all(i.isfile() or i.isdir() for i in entries), "link or special archive entry"
            blobs = {i.name: archive.extractfile(i).read() for i in entries if i.isfile()}
        wheel = False
    assert len(blobs) <= 2000 and sum(map(len, blobs.values())) <= 20_000_000, "expanded review bound"
    normalized = {}
    for name, data in blobs.items():
        p = PurePosixPath(name)
        assert not p.is_absolute() and ".." not in p.parts, f"unsafe path: {name}"
        parts = set(p.parts)
        if ".olav" in parts:
            relative = name if wheel else name.split("/", 1)[1].removeprefix("src/")
            config_prefix = "olav_skills/netops/data/skillpack/.olav/workspace/netops/netops_init/config/"
            recipe_prefix = "olav_skills/netops/data/skillpack/.olav/workspace/netops/topology/recipes/builtin/"
            allowed = {config_prefix + f for f in PACKAGED_CONFIG} | {recipe_prefix + f for f in PACKAGED_RECIPES}
            assert relative in allowed, f"unexpected .olav state: {name}"
            parts.remove(".olav")
        assert not (parts & BAD_PARTS), f"private/generated path: {name}"
        assert not any(part.startswith('.env.') for part in p.parts), f"credential path: {name}"
        assert p.suffix.lower() not in BAD_SUFFIXES, f"unexpected binary/data: {name}"
        if wheel:
            assert p.parts[0] in {"olav_skills", "olav_skills-0.1.1.dist-info"}, f"wheel namespace: {name}"
            normalized[name] = data
        else:
            assert p.parts[0] == "olav_skills-0.1.1", f"sdist root: {name}"
            assert p.parts[1] in SDIST_ROOTS, f"sdist public boundary: {name}"
            normalized['/'.join(p.parts[1:])] = data
    prefix = "olav_skills/resources/skills/" if wheel else "skills/"
    found = {name[len(prefix):].split('/')[0] for name in normalized if name.startswith(prefix)}
    assert found == SKILLS, f"skill set mismatch: {found}"
    assert all(prefix + skill + "/SKILL.md" in normalized for skill in SKILLS)
    example_prefix = "olav_skills/examples/" if wheel else "examples/"
    assert example_prefix + "synthetic_walkthrough.py" in normalized
    assert example_prefix + "synthetic_2host_bundle/manifest.yaml" in normalized
    metadata_name = "olav_skills-0.1.1.dist-info/METADATA" if wheel else "PKG-INFO"
    metadata = BytesParser().parsebytes(normalized[metadata_name])
    assert metadata["Name"] == "olav-skills" and metadata["Version"] == "0.1.1"
    assert metadata["Description-Content-Type"] == "text/markdown"
    assert metadata["License-File"] == "LICENSE"
    dependencies = metadata.get_all("Requires-Dist", [])
    assert len(dependencies) == 8
    assert not any(d.lower().startswith(("olav", "deepagents", "langchain", "langgraph")) for d in dependencies)
    links = metadata.get_all("Project-URL", [])
    assert "Repository, https://github.com/james-olavai/olav-skills" in links
    assert "Issues, https://github.com/james-olavai/olav-skills/issues" in links
    return {"archive": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "files": len(blobs), "expanded_bytes": sum(map(len, blobs.values())),
            "skills": sorted(found), "project_urls": links, "ok": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", type=Path, nargs="+")
    print(json.dumps([inspect(path) for path in parser.parse_args().archives], indent=2))


if __name__ == "__main__":
    main()
