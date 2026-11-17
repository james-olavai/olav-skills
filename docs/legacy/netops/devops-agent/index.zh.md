# DevOps Agent

DevOps Agent 为你的实际基础设施编写生产级自动化脚本。不同于通用代码助手，它了解你的设备、服务、拓扑和凭证配置。

!!! abstract "功能声明"
    | ID | 声明 | 状态 |
    |----|------|------|
    | C-NE-41 | DevOps agent 生成脚本前自动查询 `netops.devices` | ✅ v0.13.1 |
    | C-NE-42 | 生成的脚本包含 `--dry-run` + 错误处理 + 摘要 | ✅ v0.13.1 |
    | C-NE-43 | 脚本使用数据库中的真实设备名/IP，非占位符 | ✅ v0.13.1 |
    | C-NE-44 | 脚本导出到 `exports/scripts/` 为可执行文件 | ✅ v0.13.1 |

    *说明：⚠️ (E2E门控) = 测试已存在但需要特殊环境变量，CI 不运行；⚠️ (手动) = 仅演示验证，无 CI 自动化测试*

---

## 工作方式

```bash
olav --agent devops "写一个脚本备份所有路由器的运行配置"
```

Agent 流程：

1. **发现环境** — 查询 `netops.devices` 获取真实设备名、IP、平台
2. **生成完整脚本** — 针对你的设备定制（如 Cisco IOS vs Juniper Junos 命令）
3. **导出文件** — 保存到 `exports/scripts/`
4. **报告** — 告诉你文件路径、如何 dry-run、如何执行

---

## 环境感知

Agent 在写任何代码前先读取你的基础设施：

| 数据源 | 提供内容 |
|--------|---------|
| `netops.devices` | 设备名、IP、平台、角色、站点 |
| `netops.topology_links` | 网络拓扑 |
| `services.yaml` | 已注册服务 URL 和认证配置 |
| `infra/references/*.md` | API endpoint 文档 |

---

## 使用场景

| 你说 | Agent 生成 |
|------|-----------|
| "备份所有路由器配置" | Bash: 按设备 SSH，平台感知命令 |
| "把设备导入 NetBox" | Bash: curl POST，--dry-run，跳过已存在 |
| "写 Ansible playbook 部署 OSPF" | YAML: 从数据库读取 host vars |
| "站点迁移预检脚本" | Bash: 检查 BGP/OSPF 状态，备份配置 |

---

## DevOps vs 其他 Agent

| | DevOps | Infra | Ops |
|---|:---:|:---:|:---:|
| **输出** | 脚本文件 | 查询结果 | 运维操作 |
| **执行写操作** | 否（用户运行脚本） | 是（需 --enable-api-write） | 是（SSH/CLAB） |
| **环境感知** | 完整（DB + 服务 + 拓扑） | 仅服务 | 仅设备 |
