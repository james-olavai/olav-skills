#!/usr/bin/env python3
"""Bounded stdin-JSON change-plan checks; never runs or approves commands."""
from olav_skills.plan_precheck import main

if __name__ == '__main__':
    raise SystemExit(main())
