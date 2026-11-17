# 生成自动化脚本

!!! info "功能重命名说明"
    本页面原来描述的"Creator Agent"是一个从 OpenAPI schema 生成可编辑 Python 工具代码的功能。该功能已在 v0.13.0 中被更完善的方案取代。

    现在推荐的两种路径是：

    - **快速 API 接入** → `olav registry register <url>` *(不变 — [接入服务](connect-a-service.md))*
    - **自定义自动化脚本** → `olav --agent devops "写一个脚本..."` *(新 — [DevOps Agent](../netops/devops-agent/index.md))*

---

## 变更说明

| 旧方式（≤ v0.12） | 新方式（v0.13+） |
|---|---|
| `olav --agent admin "为 <url> 创建技能"` | `olav --agent devops "写一个脚本..."` |
| 生成可编辑的 Python 工具代码 | 生成可执行的 bash/Python 脚本 |
| 需要手动管理工作空间 | 脚本自动导出到 `exports/scripts/` |
| 需要了解 OpenAPI schema 细节 | Agent 直接读取数据库中的设备和服务数据 |

---

## DevOps Agent：脚本生成

如需生成与已注册服务或网络基础设施交互的脚本：

```bash
olav --agent devops "写一个脚本，查询所有 NetBox 设备并将其 IP 导出为 CSV"
olav --agent devops "为所有核心路由器写一个 Zabbix 维护窗口脚本"
```

DevOps agent 会：

1. **查询你的真实基础设施数据**（`netops.devices`、已注册服务元数据）
2. **生成环境感知型脚本**，使用真实设备名、IP 和凭证配置
3. **内置安全措施** — `--dry-run` 模式、错误处理、执行摘要
4. **导出到 `exports/scripts/`**，以可执行文件形式保存

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-L2-20 | DevOps agent 生成环境感知型自动化脚本 | ✅ v0.13.0 |

---

## 快速 API 接入

如果你只想查询 REST API 而不需要写代码，`registry register` 仍然是最快的方式：

```bash
olav registry register http://zabbix.internal/api_jsonrpc.php
olav --agent devops "显示所有有活跃告警的 Zabbix 主机"
```

完整工作流程请参见[接入服务](connect-a-service.md)。
