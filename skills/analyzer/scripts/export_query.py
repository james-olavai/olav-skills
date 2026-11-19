#!/usr/bin/env python3
"""Export all rows in a supported recorded source scope as verified parts."""
import json
import sys

from olav_skills.bounded_export import MAX_REQUEST_BYTES, export


if __name__ == "__main__":
    result = export(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result["status"] == "failed" else 0)
