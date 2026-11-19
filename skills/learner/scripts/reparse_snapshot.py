#!/usr/bin/env python3
"""Reparse retained raw output into a new project artifact from stdin JSON."""
from olav_skills.parser_lifecycle import main

if __name__ == "__main__":
    raise SystemExit(main("reparse"))
