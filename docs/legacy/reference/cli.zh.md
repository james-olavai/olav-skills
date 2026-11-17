# CLI 命令参考

OLAV 的所有功能都可以通过命令行访问。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L2-01 | `olav version` | ✅ v0.10.0 |
    | C-L2-02 | `olav list` | ✅ v0.10.0 |
    | C-L2-10 | `olav export claude-plugin` | ✅ v0.10.0 |
    | C-L2-13 | `olav init` | ✅ v0.10.0 |
    | C-L2-27 | `olav reset --agent` | ✅ v0.10.0 |
    | C-L2-14 | TUI 交互模式 | ✅ v0.10.0 |

---

## 基本用法

```
olav [选项] [命令] [查询]
```

### 全局选项

| 选项 | 简写 | 默认值 | 说明 |
|------|------|-------|------|
| `--agent NAME` | `-a` | `core` | 指定使用哪个 Agent |
| `--auto-approve` | | 关闭 | 自动批准所有工具调用（跳过确认） |
| `--dangerously-skip-permissions` | | 关闭 | 跳过权限检查（仅限开发环境） |
| `--sandbox MODE` | | `none` | 沙箱模式：`none`（本地）/ `modal` / `daytona` / `runloop`（远程） |
| `--verbose` | `-v` | 关闭 | 显示详细输出（调试用） |
| `--no-splash` | | 关闭 | 跳过启动横幅 |
| `--session ID` | | | 恢复之前的会话 |
| `--version` | `-V` | | 显示版本号 |

---

## 命令列表

### 基础命令

| 命令 | 说明 |
|------|------|
| `olav "你的问题"` | 自然语言查询（使用默认 Agent） |
| `olav --agent <name> "..."` | 指定 Agent 查询（v0.18.1 顶层：`core` / `audit` / `netops` / `services`） |
| `olav` | 启动交互式终端（TUI） |
| `olav version` | 显示平台版本 |
| `olav list` | 列出所有可用 Agent |
| `olav init` | 在当前目录初始化 OLAV 项目 |

### 工作空间管理

| 命令 | 说明 |
|------|------|
| `olav workspace list` | 列出所有工作空间（`*` 标记当前活跃） |
| `olav workspace use <name>` | 切换活跃工作空间 |
| `olav workspace status` | 显示工作空间状态 |

### Agent / 技能管理

| 命令 | 说明 |
|------|------|
| `olav agent install <路径\|URL>` | 从本地目录、wheel 或 Git 仓库安装 agent 扩展包（自带 workspace） |
| `olav agent install <路径> --merge-into <ws>` | 将工具追加到已有工作空间 |
| `olav skills list` | 列出已安装的 skill（与 agent 区分：skill 是 agent 内的能力包）|
| `olav skills info <name>` | 查看 skill 详情（版本、来源、安装时间） |

!!! warning "v0.20.3 命令重命名"
    `olav skill install <path>`（**单数** `skill`）已在 v0.20.3 移除，
    **推荐用 `olav agent install <path>`**。v0.21.0-rc4 保留了向后兼容的
    deprecation shim — 运行旧命令会打印一条黄色 warning 然后转发到新命令，
    功能不变。`olav skills`（**复数**，管理 skill 清单）是独立命令，未受影响。

    **迁移示例**：
    ```bash
    # 旧（v0.19）:
    olav skill install /path/to/olav-netops/

    # 新（v0.20.3+）:
    olav agent install /path/to/olav-netops/

    # 两者效果完全相同：自动 pip install 依赖 + 部署 4 个 workspace
    # + 重建 router 索引（无需 olav refresh）。
    ```

### 服务注册

| 命令 | 说明 |
|------|------|
| `olav registry register <name>` | 注册外部服务（自动发现 OpenAPI schema 并生成工具） |
| `olav registry refresh <name>` | 强制重新获取已注册服务的 schema |
| `olav registry list` | 列出所有已注册的外部服务 |
| `olav registry status <name>` | 检查已注册服务的可达性 |

### 审计与日志

| 命令 | 说明 |
|------|------|
| `olav log list` | 最近 24h 的操作记录 |
| `olav log errors [--hours N]` | 只看错误事件 |
| `olav log show <run-id>` | 查看某次操作的完整详情 |
| `olav log export sft\|trajectory\|atif` | 导出审计数据为训练格式 |

### 后台服务

!!! info "企业版功能 (olav-ent)"
    **Web 服务**（`olav service web`，HTTP API + 浏览器界面，端口 2280）与 **Daemon 加速器**（`olav service daemon`）属于企业版扩展 `olav-ent`（`pip install olav-ent`）。在开源版 `olav` / `olav-netops` 中，`olav service` **仅提供 syslog 接收器**（`olav service logs`）。

| 命令 | 说明 |
|------|------|
| `olav service web start [--port N]` | **企业版 (olav-ent)** —— 启动 Web 服务（默认端口 2280） |
| `olav service daemon start` | **企业版 (olav-ent)** —— 启动 Daemon 加速服务 |
| `olav service logs start [--port N]` | 启动 Syslog 接收服务（默认端口 5514） |
| `olav service start --all` | 启动所有已安装的后台服务 |
| `olav service stop --all` | 停止所有后台服务 |
| `olav service status` | 查看服务运行状态 |

### 用户管理

!!! info "企业版功能 (olav-ent)"
    多用户 **token / server-token / LDAP 认证**——包括 `olav login`、`olav user create` / `olav admin add-user` 及每用户令牌管理——属于企业版扩展 `olav-ent`。开源版 OLAV 默认 `auth.mode=none`：身份来自操作系统用户，该用户拥有完整管理员权限（无 RBAC 摩擦）。RBAC 引擎（角色 + 权限矩阵）本身保留在开源版。

OLAV 用户与 Linux 系统用户对齐 —— `olav user create alice` 默认要求
`pwd.getpwnam('alice')` 能查到。不引入平行身份库。每个用户的
**加密 token**（sha256 hash + salt 存在 `users.duckdb`，明文只在创建
时显示一次）用于 web API 鉴权。

| 命令 | 说明 |
|------|------|
| `olav user create <name> [--role admin\|user\|readonly]` | 为已存在的 Linux 用户签发 token。`--no-verify` 跳过 `getpwnam` 检查（容器场景）。 |
| `olav user list` | 表格化展示 OLAV 已登记用户 |
| `olav user token <name>` | 轮换 + 输出新 token（旧 hash 失效） |
| `olav user revoke <name>` | 禁用用户（不删行） |

旧的啰嗦形式（`olav admin add-user / list-users / rotate-token /
revoke-token`）仍可用，转发到同一实现。

### 知识库管理

| 命令 | 说明 |
|------|------|
| `olav kb import-guides [<dir>]` | 把 workspace 下每个 `*.guide.yaml` prime 进 LanceDB（幂等；源 YAML 被删时 orphan-prune 同步删 LanceDB 行） |
| `olav kb list-guides [--tier vendor\|platform\|team\|user] [--agent <name>]` | 表格化展示活跃 guide + 其 `source_tier` |
| `olav kb show <intent> [--agent <name>]` | 打印 YAML 源（用于晋级流程：复制 → git PR → merge → 重 prime） |
| `olav kb remove <intent> --reason "<text>" [--agent <name>] [--keep-yaml]` | tombstone 源 YAML（`<intent>.guide.yaml.removed`）+ 删 LanceDB 行 + 落一行 `kb_audit/<ts>_remove_<intent>.yaml`。`--reason` 必填（逃生口：`OLAV_KB_REMOVE_ALLOW_NO_REASON=1`） |
| `olav kb status` | 知识库统计 |
| `olav kb export [--dir <path>]` | 导出库到 Obsidian markdown |
| `olav kb search <query>` | 检索整库 |

### Schema 演进

| 命令 | 说明 |
|------|------|
| `olav config evolve --list` | 查看待审批的 schema 演进提案 |
| `olav config evolve --approve <id>` | 审批并应用 schema 演进 |

### 其他

| 命令 | 说明 |
|------|------|
| `olav export claude-plugin` | 导出 Claude 插件工件 |
| `olav reset --agent <name>` | 重置某个 Agent 的对话历史 |

### 诊断 & 发现命令

Round 25（ARCH-12）、Round 45（ARCH-11 可解释性）与 Round 47（ARCH-13）
加入了三个用户层快捷命令，无需启动 agent 循环即可访问数据。

| 命令 | 说明 |
|------|------|
| `olav catalog` | 列数据模型主题（设备清单 / 路由与 BGP / 拓扑 / …）|
| `olav catalog <topic>` | 进入主题：列底层表 + 示例查询 |
| `olav catalog describe <table>` | 某表的完整 schema — 列、recipe 映射、样本 |
| `olav explain "<token>"` | 把审计报告里的 citation token（如 `[src: netops.parsed_outputs#snap_a; device=R1; row=4]`）回查到 DuckDB 原始行。只读 |
| `olav diff <snap1> <snap2>` | 跨快照聚合 diff — 覆盖 parsed_outputs / topology_links / raw_output_store / oc_outputs。Flags：`--table`、`--device`、`--max-rows`、`--json` |

示例：

```bash
# 能查什么关于 BGP 的？
olav catalog "Routing & BGP"
olav catalog describe netops.parsed_outputs

# 验证某条 audit finding 的源行
olav explain "[src: netops.parsed_outputs#snap_20260418_0201; device=R1; row=4]"

# 两个快照之间变化？
olav diff snap_20260418_0100 latest --device R1 --max-rows 10
olav diff snap_a snap_b --table parsed_outputs --json
```

三个命令都走 auth 守门，但纯只读 — 不调 LLM 不改状态。

---

## 交互模式（TUI）

不带查询参数运行 `olav` 启动交互式终端：

```bash
olav                      # 使用默认 Agent（core）
olav --agent audit        # 使用指定 Agent（如 audit / netops / services）
olav --no-splash          # 跳过启动横幅
olav --session <id>       # 恢复之前的会话
```

### 交互模式中的斜杠命令

#### 内置 CLI 动词

| 命令 | 说明 |
|------|------|
| `/help` | 显示可用命令列表 |
| `/clear` | 重置对话并清屏 |
| `/model <name>` | 会话内临时切换 LLM 模型（不修改配置文件） |
| `/trace-review` | 分析最近 7 天失败记录，自动提取经验教训写入记忆 |
| `/quit` `/exit` `/q` | 退出交互模式 |

#### Agent 维度的斜杠命令

这些命令是各 agent 出厂的确定性 Python 包装。可通过
`olav --agent <name> "/<command>"` 或在交互模式（在该 agent 下）
直接 `/<command>` 调用。

| 斜杠 | Agent | 说明 |
|---|---|---|
| `/netops_init` | `netops` | 完整发现流水线 — SSH 全设备、解析、ETL 写 DuckDB、构建 per-command 自动视图 |
| `/export_netbox_csv [--filename X] [--tenant T]` | `netops` | 把 `netops.devices` 导出为 NetBox-import-兼容的 CSV 到 `exports/` 下。完整流程见 [服务连接指南](../guides/connect-a-service.md#netbox-csv-sync) |
| `/import_netbox_csv [--csv X] [--report Y] [--write --endpoint URL --token TOK]` | `services` | 校验（默认）或推送（加 `--write`）一个 NetBox-shape CSV。5 步幂等 lookup-or-create 链。每次写都落一行 `kb_audit/<ts>_netbox_push.yaml` |

### 交互模式特殊语法

| 语法 | 说明 |
|------|------|
| `@file.txt` | 将文件内容注入到当前提问中 |
| `!command` | 直接执行 Shell 命令（不经过 Agent） |

---

## 三种使用接口对比

| 接口 | 启动方式 | 适用场景 |
|------|---------|---------|
| **CLI** | `olav "查询"` | 脚本集成、一次性查询、自动化流水线 |
| **TUI** | `olav`（不带参数） | 多轮对话、探索式分析、调试 |
| **Web UI** *(企业版 · olav-ent)* | `olav service web start` → `http://localhost:2280` | 浏览器访问、团队共享 |
