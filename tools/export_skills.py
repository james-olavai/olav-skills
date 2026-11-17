#!/usr/bin/env python3
"""Export standard skill directories from this repository's maintained source.

No platform checkout, generator or runtime is consulted. Install olav-skills
in the Python environment used to execute the exported scripts.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import shutil

SOURCE = Path(__file__).resolve().parents[1] / 'skills'


def export(destination: Path) -> list[str]:
    destination = destination.resolve()
    root = SOURCE.parent.resolve()
    if destination == root or root in destination.parents or destination in root.parents:
        raise ValueError('export destination must be outside the source repository')
    files = sorted(SOURCE.rglob('*'))
    if any(p.is_symlink() for p in files):
        raise ValueError('source resources must not contain symlinks')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('export destination must be empty')
    destination.mkdir(parents=True, exist_ok=True)
    names = []
    for skill in sorted(SOURCE.iterdir()):
        if skill.is_dir() and (skill / 'SKILL.md').is_file():
            shutil.copytree(skill, destination / skill.name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            names.append(skill.name)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        print('\n'.join(export(args.out)))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
