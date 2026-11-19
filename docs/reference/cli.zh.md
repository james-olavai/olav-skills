# 管理 CLI 参考

本参考针对 `olav-skills` **0.2**。使用所选 venv 中的命令，或 `/absolute/path/to/skills-venv/bin/python -m olav_skills`。管理命令为：

```text
olav-skills [-h] {version,list,doctor,export,install,run} ...
```

| 命令 | 输入 | 输出/影响 |
| --- | --- | --- |
| `version` | 无 | 纯文本版本号 |
| `list` | 无 | JSON：版本、解释器、七技能路径及公开脚本名 |
| `doctor` | 无 | JSON：资源/依赖检查及 `ok` |
| `export DESTINATION` | 审阅过的目标 | 写标准技能及解释器/归属绑定 |
| `install` | 明确宿主与范围 | 将可发现技能复制到该范围 |
| `run SKILL SCRIPT` | stdin 一个 JSON 对象 | 使用当前解释器派发随包脚本 |

`doctor` 基础依赖/资源齐全时返回 0，缺失时返回 1。管理校验/文件错误通常返回 2，并向 stderr 写消息；参数解析使用 argparse 标准错误。`run` 保留脚本 stdout、stderr 与退出状态。不能只看进程成功，还应检查脚本内容/状态。

## 数据体检

执行 `doctor --data --project ABSOLUTE_PATH`，可加
`--snapshot-id EXACT_ID`。命令只读检查显式选择的
native 项目，不加 `--data` 时会拒绝项目/快照选择器。范围、预算、执行成功与
数据能力就绪的区别、未知采集覆盖见[数据体检](../data-readiness.md)。不调用模型，
也不连接设备。

## 安装参数

```text
olav-skills install --harness {claude-code,codex,pi}
                    --scope {project,user} [--project PATH] [--home PATH]
```

项目范围必须给 `--project`，拒绝 `--home`。用户范围拒绝 `--project`，`--home` 可覆盖实际用户主目录。范围根必须存在，不会默认安装到“当前项目”。六种完整命令、目录、本地修改冲突、升级及移除见[安装](../installation.md)。

`export` 与 `install` 记录绝对解释器和内容归属 manifest。重复操作仅接受匹配绑定、安装器拥有且未修改的目标。没有强制覆盖、移除或卸载命令。

## 脚本派发

```sh
export OLAV_HOME=/absolute/path/to/authorized-analysis
printf '%s\n' '{"operation":"context"}' | /absolute/path/to/skills-venv/bin/olav-skills run analyzer query_batch
```

通过 `list` 获取名称。派发器接受已知技能与 Python 脚本基名，可带 `.py`，拒绝路径穿越或技能目录外脚本，不解释自然语言参数。领域参数放 stdin JSON，不放管理命令参数。管理帮助用 `--help`；许多入口脚本自身没有实现 `--help`。

名称检查不是沙箱，脚本仍拥有进程权限；调用者负责 `OLAV_HOME` 及结果解释。

## Portable 是独立模块

```sh
/absolute/path/to/skills-venv/bin/python -I -B -m olav_skills.portable --out /absolute/new-directory
```

知识导出器不是管理命令的 `portable` 子命令。它要求已安装资源和全新规范目标，详见[完整合同](../portable.md)。CLI 不提供聊天、模型配置、采集、服务、调度、模拟、通用数据库管理或自动迁移命令。

## 解析、调查与证据脚本

通过 `run SKILL SCRIPT` 调用 learner 的
`manage_parser` / `reparse_snapshot`、analyzer 的 `export_query` /
`run_investigation`，以及 reporter 的 `evidence_bundle`。
先查[脚本目录](scripts.md)及其链接合同，核对必填JSON、写入影响、完整性与未知语义。

## 历史比较与巡检脚本

同一派发器也可运行 reporter 的 `compare_routes`、`compare_counters`、`run_audit`。
原生查询/状态语义与netops diff_configs更新了来源合同；先读[脚本目录](scripts.md)。
没有新增管理子命令或调度器。
