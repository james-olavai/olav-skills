# Report Writer

The Report Writer is a sub-agent of Core Agent that polishes raw query results into structured, presentation-ready reports. It is invoked automatically when OLAV detects that the output would benefit from formatting — or you can request it explicitly.

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-V15-03 | Report Writer polishes raw tool output into formatted Markdown report | ✅ v0.15.0 |

---

## How It Works

When Core Agent finishes a tool call (e.g. `execute_sql`, `execute_cli`), it can hand the raw result to the writer sub-agent for polishing:

```
Core Agent → execute_sql → raw table data
                      ↓
               Writer Sub-Agent → formatted Markdown report
```

The writer receives:
- The original user question
- The raw tool output
- Optional formatting hints (e.g. "as a table", "as a bulleted list")

And returns a polished Markdown document with headings, tables, and summary sentences.

---

## Triggering the Writer

### Automatic

When a query produces tabular data, OLAV automatically invokes the writer:

```bash
olav "Show all BGP peers and their status"
```

Output (writer-formatted):

```markdown
## BGP Peer Status — R1

| Neighbor IP | AS | State       | Uptime |
|-------------|-----|-------------|--------|
| 10.0.0.2    | 65002 | Established | 2d 3h |
| 10.0.0.3    | 65003 | Idle        | —     |

**Summary:** 1 of 2 peers are down. R1 → 10.0.0.3 is in Idle state.
```

### Explicit

Ask for a report directly:

```bash
olav "Write a report on device inventory"
olav "Format the last query results as a Markdown table"
```

---

## Workspace Location

The writer sub-agent is defined at:

```
.olav/workspace/core/writer/
├── AGENT.md       ← writer agent definition
└── prompts/
    └── system.md  ← writer system prompt
```

It is invoked by Core Agent as a sub-agent call — it does not appear in `olav list` because it is not a standalone workspace.

---

## Configuration

The writer is always available when `core` workspace is active. No additional configuration is needed.

To disable writer-polishing for a specific query, end your question with `--raw`:

```bash
olav "Show BGP peers --raw"
```

This returns the unformatted tool output directly.
