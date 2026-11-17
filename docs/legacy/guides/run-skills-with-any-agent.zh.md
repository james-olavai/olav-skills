# 用任意 Agent 运行 Skill（以 pi-agent 为例）

`olav-skills` 和 `olav-presales-skills` 是从平台 workspace 代理生成出来的独立、轻量发布包——一组 `SKILL.md` 文件加上各自的脚本和一份精简 vendor 进去的 runtime，不依赖任何正在运行的 OLAV 安装。任何遵循 [Agent Skills](https://code.claude.com/docs/en/skills) 发现约定（`SKILL.md` 带 `name`+`description`，配 `references/`、`scripts/`）的客户端都能识别并驱动它们——Claude Code 是其中一种，[pi-agent](https://github.com/earendil-works/pi) 是另一种；这篇用 pi-agent 做示例,是因为它是完全不同厂商的 runtime,有自己独立的模型路由——比拿 OLAV 自己的工具去测,更能证明这些包确实带着自己的依赖走,不依赖 OLAV 本身。

这里的所有操作都不会连到任何 OLAV 服务端。agent runtime（这里是 pi-agent）负责模型循环,包提供 skill,两者之间就是一次普通的子进程调用——跟这些包在 OLAV 内部本来就在用的 `execute_skill_script` stdin-JSON 协议是同一套。

---

## 一个精简包里有什么

| 目录 | 内容 |
|---|---|
| `skills/<agent>/` | 每个随包发布的代理各一份 `SKILL.md` + `references/*.guide.yaml` + `scripts/*.py` |
| `runtime/` | `olav`/`olav_netops`/`olav_presales` 里脚本真正会 import 的那一部分——不含 LLM 客户端,不含向量存储 |
| `requirements-lean.txt` | runtime 需要的第三方包（DuckDB、PyYAML、Pydantic 等） |

`olav-skills` 打包的是网络运维那条读取/报告/变更计划链路（`analyzer`、`importer`、`learner`、`netops`、`reporter`、`topology`、`writer`）；`olav-presales-skills` 打包的是售前工作流那几个代理（`analyst`、`designer`、`explorer`、`presales`、`publisher`、`surveyor`）。两个包都刻意排除了任何需要 embedding 后端的东西——一个整个功能就是对 LanceDB 做向量检索的脚本（比如项目文档的语义搜索）会被直接排除在打包范围外,而不是打包进去却在运行时悄悄失败,包自己的 README 里也写清楚了这一点。

---

## 安装

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-lean.txt
pip install ./runtime
```

安装就这么多——不需要 OLAV 服务端、不需要 API key、不需要 embedding 端点。`pip install ./runtime` 这一步很关键：它把 `olav`/`olav_netops` 注册成真正可 import 的包,这样每个脚本（不只是那些自带路径兜底逻辑的脚本）里 `from olav.core.config import ...` 才能正确解析。

然后把 skill 指给 agent runtime：

```bash
cp -r skills/* ~/.pi/agent/skills/          # pi-agent 的发现目录
# 或者： cp -r skills/* ~/.claude/skills/    # Claude Code
```

---

## 实测示例：pi-agent

装 pi-agent（需要 Node.js ≥ 18），选一个模型 provider——下面用 DeepSeek 举例：

```bash
npm install -g @earendil-works/pi-coding-agent
export DEEPSEEK_API_KEY="<key>"     # 或者交互式跑 `pi`,用 `/login`
```

**1. 确认 skill 能被发现。** 非交互模式（`--print`）跑一轮就退出，适合脚本化检查：

```bash
pi --provider deepseek --print '列出你现在能看到的所有 skill，每行一个'
```

装对了的话，回答应该正好是你拷进去的那个/那几个包里的代理目录名——不多不少。

**2. 驱动单个脚本。** 直接按名字提任务，pi-agent 会自己找到对应 skill、读它的 `SKILL.md`、自己跑脚本：

```bash
pi --provider deepseek --print \
  '用 analyzer skill 的 describe_table 脚本查一下 netops.devices 这张表，把它返回的内容原样报出来'
```

对着一个空数据库测，正确的行为是干净地返回"表不存在"的结构化错误——重点是 runtime 正确 import、脚本正常执行了，不是刚好有没有数据。

**3. 一个完整、多步骤的任务。** 这些包本来就是为这种用法设计的——给目标，不给步骤清单，让它自己规划：

```bash
pi --provider deepseek --print \
  '用 reporter skill 调查一下这个网络当前的健康状况：设备清单、BGP 会话、OSPF 邻接关系、CDP 推出来的拓扑。把发现综合成结构化报告，通过 format_and_export 导出，告诉我确切的文件路径并把报告内容贴出来'
```

只要有一份 DuckDB 快照可读（怎么采出一份见 [Collector →](../netops/netops-agent/collector.md)），pi-agent 就会自己规划要查哪些 SQL 证据、互相核对不止一个视图、写出导出文件——跟 reporter 代理在 OLAV 内部的行为完全一样,只是跑在另一个厂商完全不同的模型循环上。

---

## 还需要完整平台才能做的事

- **真实设备访问。** 精简包故意排除了 `collector` 里连设备的脚本（`take_snapshot`、`execute_cli_parallel`）——一个"轻量、不需要服务端"的包不该带 netmiko/nornir 和真实 SSH 凭据。如果需要 agent 真的去设备上采集，装完整版扩展（`pip install 'olav[agent]' 'olav-netops[device]'`），再把 `collector` skill 跟精简包的其他 skill 一起拷进去——见 [Collector →](../netops/netops-agent/collector.md)。
- **记忆 / 召回。** 精简包里没有任何东西会碰 LanceDB 或 embedding 端点。一个整个功能就是语义检索的脚本（比如项目文档搜索）会被直接排除在打包范围外，而不是打包进去却悄悄失败。
- **跨代理编排。** OLAV 自己的路由每轮只选一个子代理，还会对每个代理的工具数量做上限约束；第三方 agent runtime 没有这层路由，选哪个 skill 完全靠模型自己判断——加上你的 prompt。需要精确指定时,像上面例子那样直接按名字点出要用哪个 skill。
