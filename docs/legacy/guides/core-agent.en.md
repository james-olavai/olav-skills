# Core Agent Toolset

The Core Agent is OLAV's "all-around engineer" — it can execute Python code, SQL queries, shell commands, and web searches. When other Agents fall short, the Core Agent has your back.

```bash
olav --agent core "your request"
```

!!! abstract "Feature Claims"
    | ID | Claim | Status |
    |----|-------|--------|
    | C-L2-31 | Core Agent executes Python code and returns output | ✅ v0.10.0 |
    | C-L2-32 | Core Agent executes SQL queries against the business database | ✅ v0.10.0 |
    | C-L2-33 | Core Agent performs web searches and summarizes results | ✅ v0.10.0 |
    | C-L2-34 | Core Agent executes shell commands | ✅ v0.10.0 |
    | C-L2-35 | Admin Agent detects workspace health issues | ✅ v0.10.0 |

---

## Executing Python Code

Have the Agent run Python directly — great for data analysis, calculations, and quick scripts:

```bash
olav --agent core "run this python: import sys; print(sys.version)"
```

```
🔧 run_python_code(...)
Python 3.12.3 (main, Mar 3 2026, ...)
```

More examples:

```bash
olav --agent core "计算这组数据的 P95 延迟: [12, 45, 23, 89, 34]"
olav --agent core "解析这个 CSV 并统计 A 列的唯一值数量"
olav --agent core "画一个柱状图: red=10, blue=25, green=15 并保存到 /tmp/chart.png"
```

Python runs within the OLAV platform's Python environment; output is captured and displayed in the response.

---

## Executing SQL Queries

Query business data directly in the domain database:

```bash
olav --agent core "what tables are in the database?"
```

```
Main Tables (use app. prefix):
 • app.assets (name, type, status, owner)
 • app.events (asset_name, event_type, event_data, snapshot_id)
 • app.relationships (source, target, relationship_type, ...)

Views (no prefix needed):
 • v_asset_summary, v_recent_events, v_active_alerts, ...
```

You can describe what you need in natural language or write SQL directly:

```bash
olav --agent core "how many asset records are in the database?"
olav --agent core "SELECT name, type FROM app.assets"
olav --agent core "show the 5 most recent events"
```

!!! note "SQL queries target the business database"
    The Core Agent's `execute_sql` connects to `.olav/databases/domain.duckdb` (business data). To query **audit logs**, use the `olav log` command or connect directly to `.olav/databases/audit.duckdb`.

---

## Web Search

When you need to look up external documentation or technical references:

```bash
olav --agent core "search: Python asyncio best practices"
olav --agent core "search: DuckDB 窗口函数教程"
```

The Agent searches the web, extracts relevant content, and returns a summary.

---

## Shell Commands

Execute system commands to check server status or files:

```bash
olav --agent core "run: df -h"
olav --agent core "run: ls -la exports/"
olav --agent core "检查 2280 端口是否开放"
```

!!! warning "Mind the execution path"
    Shell commands run as the current user in the OLAV project root directory. Use absolute paths or explicit relative paths.

---

## Skipping Confirmation

In automation scenarios, skip the confirmation prompt for each tool invocation:

```bash
olav --agent core --auto-approve "run: df -h"
```

---

## Workspace Health Check (Admin Agent)

The Admin Agent can diagnose common workspace issues:

```bash
olav --agent admin "执行健康检查"
olav --agent admin "审计工作空间是否有错误"
```

Issues it can detect:

- `@tool` function name conflicts (multiple skills defining tools with the same name)
- `MANIFEST.yaml` missing required fields
- `AGENT.md` referencing non-existent files

---

## Core Agent vs Admin Agent: Which One to Use?

| Task | Which Agent |
|------|-------------|
| Execute Python / SQL / Shell | `core` |
| Web search | `core` |
| Data analysis and calculations | `core` |
| Register external services | `config` |
| Workspace health check | `config` |
| Install / generate skills | `config` |
