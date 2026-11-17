#!/usr/bin/env python3
"""Bounded adjacency Markdown -> inert SVG; no DB, paths, templates or network.

Uses the official draw.io parser, layout and protocol label semantics. Invalid,
empty or over-budget input returns a fixed omission comment, never a partial graph.
"""
from __future__ import annotations

import json
import re
import sys
from html import escape

from render_topology_drawio import (
    _edge_label, _find_col, _layout_tree, _parse_table, _PROTOCOL_EDGE_OK,
)

MAX_TABLE_BYTES = 262144
MAX_REQUEST_BYTES = 300000
MAX_NODES = 300
MAX_EDGES = 1000
MAX_CELL_CHARS = 256
_BAD_XML = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def _omit(reason: str) -> str:
    # Only fixed internal reasons reach this function, never input text.
    return f"<!-- render_topology_svg: {reason}; diagram omitted (not fabricated). -->\n"


def render_topology_svg(adjacencies_table_markdown: str, protocol: str = "l2_cdp") -> str:
    """Return safe SVG, or an omission comment for invalid/empty/over-budget input.

    Required column aliases match render_topology_drawio. No metadata, file path
    or output options are accepted. Limits reject the whole graph, not a prefix.
    Duplicate rows remain recorded edges; distinct names always get distinct IDs.
    """
    table = adjacencies_table_markdown
    if not isinstance(table, str) or not isinstance(protocol, str):
        return _omit("invalid input type")
    if protocol not in _PROTOCOL_EDGE_OK:
        return _omit("unsupported protocol")
    if not table.strip():
        return _omit("empty input")
    if _BAD_XML.search(table):
        return _omit("invalid XML character")
    if len(table.encode("utf-8")) > MAX_TABLE_BYTES:
        return _omit("table byte limit exceeded")
    # The shared parser deliberately skips malformed rows. Refuse those here so
    # a static artifact can never silently claim a partially parsed table.
    lines = [ln.rstrip() for ln in table.splitlines() if ln.strip()]
    if any(not ln.startswith("|") or not ln.endswith("|") for ln in lines):
        return _omit("invalid table structure")
    cells = [[c.strip().strip("*") for c in ln.strip("|").split("|")] for ln in lines]
    width = len(cells[0])
    if any(len(row) != width or any(len(c) > MAX_CELL_CHARS for c in row) for row in cells):
        return _omit("invalid row or cell limit exceeded")
    if len(set(c.lower() for c in cells[0])) != width:
        return _omit("duplicate columns")
    rows = _parse_table(table)
    if not rows:
        return _omit("no adjacency rows")
    cols = [_find_col(rows[0], aliases) for aliases in (
        ["source", "src", "source device"],
        ["local intf", "local interface", "source interface", "src intf"],
        ["dest", "destination", "dst", "dest device"],
        ["remote intf", "remote interface", "destination interface", "dst intf"],
    )]
    if any(col is None for col in cols):
        return _omit("missing required columns")
    status_col = _find_col(rows[0], ["status", "link status", "state"])
    if len(rows) > MAX_EDGES:
        return _omit("edge limit exceeded")
    edges = []
    names = set()
    for row in rows:
        source, local, dest, remote = (row[col].strip() for col in cols)
        if not source or not dest:
            return _omit("missing endpoint")
        names.update((source, dest))
        edges.append((source, local, dest, remote, row[status_col].strip() if status_col else ""))
    if len(names) > MAX_NODES:
        return _omit("node limit exceeded")
    nodes = sorted(names)
    ids = {name: f"n{i}" for i, name in enumerate(nodes)}
    positions = _layout_tree(nodes, [(s, d) for s, _, d, _, _ in edges])
    # The official layout uses a 120 px grid. Scale it to reserve text space.
    positions = {n: (x * 3, y * 2) for n, (x, y) in positions.items()}
    width = max(x for x, _ in positions.values()) + 300
    height = max(y for _, y in positions.values()) + 120
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Recorded topology">',
             '<title>Recorded topology</title>']
    for i, (source, local, dest, remote, status) in enumerate(edges):
        x1, y1 = positions[source]
        x2, y2 = positions[dest]
        dashed = ' stroke-dasharray="6 4"' if status.lower() not in _PROTOCOL_EDGE_OK[protocol] else ""
        parts.append(f'<g id="e{i}"><line x1="{x1 + 90}" y1="{y1 + 24}" x2="{x2 + 90}" y2="{y2 + 24}" stroke="#526478"{dashed}/>'
                     f'<text x="{(x1 + x2) // 2 + 90}" y="{(y1 + y2) // 2 + 12}" text-anchor="middle" font-size="12">{escape(_edge_label(local, remote, status, protocol))}</text></g>')
    for name in nodes:
        x, y = positions[name]
        parts.append(f'<g id="{ids[name]}"><rect x="{x}" y="{y}" width="180" height="48" rx="6" fill="#e7f1f8" stroke="#036897"/>'
                     f'<text x="{x + 90}" y="{y + 29}" text-anchor="middle" font-size="14">{escape(name)}</text></g>')
    return "\n".join(parts) + "\n</svg>\n"


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        result = _omit("request byte limit exceeded")
    else:
        try:
            args = json.loads(raw)
            if not isinstance(args, dict) or set(args) - {"adjacencies_table_markdown", "protocol"}:
                result = _omit("invalid argument object")
            else:
                result = render_topology_svg(**args)
        except (ValueError, TypeError, UnicodeError):
            result = _omit("invalid JSON or arguments")
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
