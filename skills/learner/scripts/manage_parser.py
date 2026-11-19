#!/usr/bin/env python3
"""Manage explicit project parser versions from one stdin JSON object."""
from olav_skills.parser_lifecycle import main

if __name__ == "__main__":
    raise SystemExit(main("manage"))
