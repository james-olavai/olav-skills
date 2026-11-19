#!/usr/bin/env python3
"""Compare two explicit recorded raw configurations via stdin JSON."""
import sys

from olav_skills.netops.core.diff.configs import MAX_REQUEST_BYTES, diff_configs, run_config_diff


__all__ = ["diff_configs"]


if __name__ == "__main__":
    import json

    result = run_config_diff(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    raise SystemExit(0 if result["status"] == "success" else 1)
