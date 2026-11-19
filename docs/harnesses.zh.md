# 接入已有 AI 宿主

七个标准 Skill 说明各项能力何时适用，以及如何调用独立 Python runtime。已有宿主负责模型选择、发现配置、工具调用和权限管理。也可以直接通过 CLI 运行同一套工具。


## 先 pip 安装，再绑定

在独立 Python 环境安装已发布的包，再按所用宿主选择绑定命令。[安装指南](installation.md)

```sh
olav-skills install --harness codex --scope user
olav-skills install --harness claude-code --scope user
olav-skills install --harness pi --scope user
```

绑定会复制七个技能并记录解释器路径。更换包版本或移动 venv 后，从目标环境显式重新绑定。

## 三种交接方式

| 方式 | 交付内容 | 宿主仍需提供 |
| --- | --- | --- |
| `olav-skills install` | 七个可发现技能、精确解释器绑定及归属哈希 | 技能发现、模型、工具执行、项目与数据权限 |
| `olav-skills run <skill> <script>` | 使用当前环境直接执行 stdin JSON 请求 | 明确输入、授权数据根及结果解释 |
| [Portable 导出](portable.md) | 供有界加载器读取的知识与资源哈希 | runtime、注册工具、授权及执行策略 |

项目或用户范围的完整命令见[安装](installation.md)。Codex 使用 `.agents/skills`，Claude Code 使用 `.claude/skills`，Pi 项目使用 `.pi/skills`，Pi 用户范围使用 `.pi/agent/skills`。安装范围根必须已存在。项目和用户副本可能同时暴露同名技能，需检查宿主实际生效配置。

## 验证宿主接入

每个已安装技能包含 `olav-runtime.json` 与 `.olav-skills-install.json`，指令中写入绝对解释器路径。请保留该路径的 venv；只复制技能并不会移动 runtime。venv 移动或替换后，从目标解释器重新执行同一安装命令。遇到冲突时先审阅，不覆盖已编辑技能目录。

1. 核对已复制的清单，再确认宿主实际列出目标范围中的七个技能。按宿主要求刷新会话。
2. 从绑定环境运行 `version`、`doctor`、`list`，再用已知夹具核对工具结果。[离线示例](offline-data.md)
3. 若宿主已配置工具调用，先给定输入，核对指定调用的实际结果。再用同一份已知数据单独测试普通问法，检查激活的技能、工具实参、证据和最终回答。

已测试的 Pi 配置未通过自然问答验收，最终本地 0.2.0 候选对留作独立验证的同义问法也未通过。Codex 和 Claude Code 的自然问答未测。[兼容性](reference/compatibility.md)

不支持标准发现的宿主，可以读取导出到审阅位置的相关 SKILL.md，再通过文档中的 CLI 执行。不要虚构 `execute_skill_script` API。

## 阅读保留的技能原文

部分随包指令保留了旧平台术语，例如委派、`task(...)`、`olav_recall_memory`、`format_and_export`、SSH 采集及模拟器引用。独立包的执行以调用约定说明为准：读取同级技能，调用实际列出的 Python 脚本。独立包没有实现这些历史宿主工具。引用中提到工具，不代表当前可用或已获授权。

调度、后台运行和审批机制由宿主负责。
