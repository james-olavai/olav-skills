# 安装并绑定标准技能

本指南针对单一发行包 `olav-skills` **0.1.1**，已发布到 PyPI。其版本历史独立于旧平台。使用 Python 3.11 或更新版本及独立虚拟环境：

```sh
python3 -m venv /absolute/path/to/skills-venv
/absolute/path/to/skills-venv/bin/python -m pip install olav-skills==0.1.1
/absolute/path/to/skills-venv/bin/olav-skills version
/absolute/path/to/skills-venv/bin/olav-skills doctor
/absolute/path/to/skills-venv/bin/olav-skills list
```

也可以在核对提供的 SHA-256 后安装审阅过的本地 wheel：

```sh
/absolute/path/to/skills-venv/bin/python -m pip install /absolute/path/to/olav_skills-0.1.1-py3-none-any.whl
```

源码开发可用 `python -m pip install .`，发行验证则应在新环境非 editable 安装构建产物。wheel 同时包含确定性代码及七个标准技能目录，不依赖 `olav`、`olav-netops`、DeepAgents 或 LangGraph。依赖项由 `pyproject.toml` 声明。CLI 不加载 `.env`，不配置模型，也不运行代理会话。

## 宿主发现布局

显式选择项目或用户范围：

```sh
/absolute/path/to/skills-venv/bin/olav-skills install --harness codex --scope project --project /absolute/project
/absolute/path/to/skills-venv/bin/olav-skills install --harness codex --scope user --home /absolute/user-home
/absolute/path/to/skills-venv/bin/olav-skills install --harness claude-code --scope project --project /absolute/project
/absolute/path/to/skills-venv/bin/olav-skills install --harness claude-code --scope user --home /absolute/user-home
/absolute/path/to/skills-venv/bin/olav-skills install --harness pi --scope project --project /absolute/project
/absolute/path/to/skills-venv/bin/olav-skills install --harness pi --scope user --home /absolute/user-home
```

| 宿主 | 项目布局 | 用户布局 |
| --- | --- | --- |
| Claude Code | `<project>/.claude/skills/<skill>` | `~/.claude/skills/<skill>` |
| Codex | `<project>/.agents/skills/<skill>` | `~/.agents/skills/<skill>` |
| Pi | `<project>/.pi/skills/<skill>` | `~/.pi/agent/skills/<skill>` |

范围根必须已存在。项目范围必须给 `--project`，拒绝 `--home`；用户范围可用 `--home` 指定其它主目录，拒绝 `--project`。Pi 用户范围只指向默认 agent 目录；安装器不读取 `PI_CODING_AGENT_DIR`。自定义目录需改用项目范围或显式管理宿主配置。

安装器提供布局适配；不会改变宿主权限。包测试覆盖布局，不证明模型行为。单独的无模型原生发现检查在 Codex 0.169.0、Pi 1.3.0 通过；Claude Code 2.1.323 仅验证文件系统布局。完整层级见[兼容性](reference/compatibility.md)。

每个技能带 `olav-runtime.json`、指令中的绝对 Python 路径，以及记录范围、版本和内容哈希的 `.olav-skills-install.json`。venv 应保留在绑定路径。移动或替换后，从目标解释器重新安装。同范围重复安装只替换安装器拥有且内容未变的文件。已有同名技能、符号链接、本地修改或额外文件会保留并以退出码 2 拒绝；没有强制覆盖开关。安装先整体检查冲突，但操作系统中途失败仍可能留下部分更新，可检查后重新运行。

## 升级与移除

将审阅过的新 wheel 安装到新 venv，运行 `version`/`doctor`，再从新环境重放原安装命令，范围根保持相同。只有未修改的安装器归属目录会刷新版本和 Python 路径。验证完成前保留旧 venv。单独更新 wheel 不会更新已复制技能。

同名目录未归属、已编辑或多了文件时，先审阅备份；只将精确冲突目录移出发现树后再安装，不要删除整个 `.agents`、`.claude` 或 `.pi`。用户和项目范围可同时暴露同名技能，需核对实际生效配置并显式处理重复项。可编辑的归属元数据不是安全边界。

CLI 没有 `uninstall` 或 `remove`。退役时检查七个目录中的两个 manifest，确认归属并保存本地修改后，只移除这些精确技能目录。这是手工文件操作，不应删除无关技能或整个发现根。`python -m pip uninstall olav-skills` 只卸载 Python 包，不删除已复制技能、分析数据或报告。删除 venv 前先处理旧绑定；数据保留/删除需另行决定。

## 导出与执行

```sh
/absolute/path/to/skills-venv/bin/olav-skills export /absolute/new-directory
export OLAV_HOME=/absolute/path/to/your-authorized-analysis
printf '%s' '{"sql":"SELECT hostname FROM netops.devices ORDER BY hostname"}' | /absolute/path/to/skills-venv/bin/olav-skills run analyzer execute_sql
```

查询前先按[离线示例](offline-data.md)建立数据库。标准导出不需要宿主，重复导出同样检查归属和本地修改。也可用绑定解释器直接运行导出脚本，例如 `/absolute/path/to/skills-venv/bin/python /absolute/new-directory/analyzer/scripts/execute_sql.py`。两种方式均读取 stdin JSON，保留脚本 stdout 与退出状态。

`run` 只接受已知技能及该技能 `scripts/` 中的 Python 基名，拒绝路径和穿越；这不是沙箱，可信脚本仍有进程的文件系统权限。数据操作前显式设置 `OLAV_HOME`；安装/导出不会授予数据权限。`doctor` 仅报告解释器、资源与基础依赖，不能证明业务答案正确。

## 常见问题

依赖或资源缺失时核对绝对解释器并在独立环境重装；不要原地修现役旧环境。归属、绑定或本地修改冲突应保存后解决。技能不可见时核对宿主实际发现范围。数据库不对时检查 `OLAV_HOME`、继承覆盖项、导入 `db_path` 和解析报告。凭据不应进入 stdin、技能正文或日志；更多见[故障排查](troubleshooting.md)。
