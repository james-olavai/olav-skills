# 通过 pip 安装 Olav Skills

从 PyPI 安装唯一的 **`olav-skills`** 包，再绑定到所用 harness。包内已包含 Python 工具和全部七个技能，普通安装不需要克隆仓库、手动复制技能目录，也不需要安装已退役的 `olav`、`olav-netops`。

使用 Python 3.11 或更新版本。先创建并激活独立环境，以下示例使用 POSIX shell：

```sh
python3 -m venv ~/.venvs/olav-skills
. ~/.venvs/olav-skills/bin/activate
```

## 第一步：安装包

```sh
python -m pip install olav-skills==0.2.2
```

后续命令在安装 0.2.2 的同一环境执行。若使用已有的 wheel，请参考[本地 wheel 示例](release-candidate.zh.md)。

## 第二步：绑定到所用 harness

选择你使用的 harness，执行对应命令：

```sh
# Codex
olav-skills install --harness codex --scope user

# Claude Code
olav-skills install --harness claude-code --scope user

# Pi
olav-skills install --harness pi --scope user
```

pip 将 runtime 和技能资源安装到 venv。这一步将七个技能目录复制到宿主发现路径，并记录该 venv 的绝对 Python 解释器路径。请保留当前环境的路径，必要时按宿主要求刷新会话。

检查安装后，继续[离线数据示例](offline-data.md)：

```sh
olav-skills version
olav-skills doctor
olav-skills list
```

CLI 不加载 `.env`，不配置模型，也不运行代理会话。

## 项目范围与宿主发现布局

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

安装器准备相应布局。实际的技能发现和工具权限取决于宿主配置。[兼容性与测试版本](reference/compatibility.md)

每个技能带 `olav-runtime.json`、指令中的绝对 Python 路径，以及记录范围、版本和内容哈希的 `.olav-skills-install.json`。venv 应保留在绑定路径。移动或替换后，从目标解释器重新安装。同范围重复安装只替换安装器拥有且内容未变的文件。已有同名技能、符号链接、本地修改或额外文件会保留并以退出码 2 拒绝；没有强制覆盖开关。安装先整体检查冲突，但操作系统中途失败仍可能留下部分更新，可检查后重新运行。

## 升级、回退与移除

在同一个已激活环境中通过 pip 升级，再执行对应的绑定命令。以选择最新已发布版并绑定到 Codex 为例。

```sh
python -m pip install --upgrade olav-skills
olav-skills install --harness codex --scope user
```

Claude Code 或 Pi 重放第二步中的对应命令。单独执行 pip 升级不会刷新已复制到发现目录的技能。保持原安装范围根；安装器只更新自己拥有且未被修改的技能目录，已有本地修改需先保存和审阅。

分阶段升级时，在新 venv 安装并检查目标版本，再从新环境绑定到同一范围根。验证完成前保留旧 venv。回退时从旧 venv 重新绑定，再核对每个已复制技能的 `olav-runtime.json` 中的版本和解释器。若两个版本先后装在同一 venv，先重装经审阅的旧版再绑定。包和技能的回退范围不包含数据库与产物写入。

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

## 可选：离线安装与源码开发

离线或使用审阅过的发行物时，先核对提供的 SHA-256，再用本地 wheel **替代**第一步中的 PyPI 安装命令：

```sh
python -m pip install /absolute/path/to/olav_skills-0.2.2-py3-none-any.whl
```

离线环境也需备齐依赖。随后仍按第二步绑定 harness；wheel 包含相同的 Python 代码及七个技能。源码贡献者可在 checkout 中使用 `python -m pip install .`，普通用户无需这样安装。发行验证使用新环境中的非 editable wheel。依赖由 `pyproject.toml` 声明，不依赖 DeepAgents 或 LangGraph。
