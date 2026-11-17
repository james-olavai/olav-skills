# 快速上手

5 分钟内让 OLAV 看见你的网络：安装、配置设备、采集数据、发出第一个查询。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-NE-01 | 安装 olav-netops 后 `/netops_init --dry-run` 通过环境检查 | ✅ v0.13.1 |
    | C-NE-02 | 安装 olav-netops 后 `olav list` 显示 6 个平台 agent（admin、audit、core、devops、netops、services）| ✅ v0.18.0 |
    | C-NE-03 | `/netops_init` 采集设备数据并写入 `netops.parsed_outputs` | ✅ v0.13.1 |
    | C-NE-04 | `/netops_init` 从 LLDP/CDP 发现数据生成拓扑链接 | ✅ v0.13.1 |

    *说明：⚠️ (E2E门控) = 测试已存在但需要特殊环境变量，CI 不运行；⚠️ (手动) = 仅演示验证，无 CI 自动化测试*

---

## 第一步：安装 OLAV + olav-netops

```bash
pip install olav olav-netops     # OLAV 核心 + 网络领域包
olav agent install olav-netops   # 部署 netops workspace 并注册 agent
```

`pip install` 从 PyPI 拉取两个包。`olav agent install olav-netops` 随后解析已安装的包、部署其 workspace（`netops` agent 加上具备网络感知能力的 `audit`），并刷新 agent 路由表。（开发时可改传本地路径或 git URL：`olav agent install /path/to/olav-netops/`。）

!!! note "首次运行会自举 core"
    全新项目请先运行一次 `olav`——它会搭建 workspace、数据库和 core agent，并提示输入 LLM API key。完整核心安装见 [安装](../../getting-started/installation.md)。

验证：

```bash
olav list
```

输出应为 6 个顶层 agent：

```
  • admin     — 平台自管理：健康/日志诊断、cron、技能安装、workspace 搭建
  • audit     — 合规检查、漂移检测、网络健康报告生成
  • core      — 跨域能力：SQL / 语义记忆 / Python 沙箱 / Web 搜索 / 报告 / cron
  • devops    — NetBox DCIM/IPAM、InfluxDB 指标、自动化脚本库、HITL 执行
  • netops    — 网络排障：拓扑分析、路由仿真、实验室验证、探测
  • services  — 服务集成：注册 API、部署/停止容器、认证 HTTP 调用
```

---

## 第二步：配置设备

编辑 Nornir 清单文件定义网络设备：

```bash
nano .olav/workspace/netops/config/nornir/hosts.yaml
```

示例 `hosts.yaml`：

```yaml
R1:
  hostname: 10.0.1.1
  platform: cisco_ios
  groups:
    - core_routers

R2:
  hostname: 10.0.1.2
  platform: cisco_ios
  groups:
    - core_routers
```

组默认值在 `groups.yaml` 中配置：

```yaml
core_routers:
  username: admin
  password: "${NORNIR_PASSWORD}"
  connection_options:
    netmiko:
      extras:
        timeout: 30
```

---

## 第三步：运行初始化

```bash
olav --agent netops "/netops_init"
```

初始化执行 5 个阶段的流水线：

| 阶段 | 执行内容 |
|------|---------|
| 0 | 检查环境：Nornir 配置、LLM 可用性、workspace |
| 1 | SSH 连接所有设备，执行 show 命令，TextFSM 解析 |
| 2 | 分析解析差距（P0 关键 / P1 重要 / P2 可选） |
| 3 | 自动修复解析失败，验证发现命令（CDP/LLDP） |
| 4 | 从 LLDP/CDP 邻居数据构建拓扑图 |
| 5 | 注册每日采集 cron 任务 |

!!! tip "先试运行"
    在不接触设备的情况下测试环境：
    ```bash
    olav --agent netops "/netops_init --dry-run"
    ```

---

## 第四步：发出第一个查询

```bash
olav "我的网络有多少台设备？"
```

```bash
olav "展示网络拓扑"
```

```bash
olav "所有 BGP 邻居都建立了吗？"
```

OLAV 从 DuckDB 快照数据库查询已采集的数据并返回结构化答案。查询不需要访问设备——所有数据来自快照。

---

## 下一步

- [基本概念](../concepts/index.md) — 理解快照、模板和 agent 的工作原理
- [Core Agent](../core-agent/index.md) — 日常查询和 CLI 执行
- [NetOps Agent](../netops-agent/index.md) — 深度分析、模拟和漂移检测
