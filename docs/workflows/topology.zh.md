# 从记录关系生成图表与报告

先运行[离线示例](../offline-data.md)，它已经核对两台设备、一条记录 CDP 边，写入 Mermaid 产物和报告。本流程解释如何在无模型条件下重复并解释绘图步骤。

## 先读关系，再绘图

用 `topology query_topology`、`concept: all` 和导入返回的快照 ID 查询，检查 `absent_layers`；夹具没有 OSPF 来源。L2、BGP、OSPF 是不同层，空或缺失层不能画成已证实不存在关系。

然后用同一 ID 生成限定范围图：

```sh
SKILLS_PYTHON=/absolute/path/to/skills-venv/bin/python
export OLAV_HOME=/absolute/path/to/new-synthetic-analysis
"$SKILLS_PYTHON" -I -B -c 'import json,os; from pathlib import Path; sid=json.loads((Path(os.environ["OLAV_HOME"])/"import.json").read_text())["snapshot_id"]; print(json.dumps({"name_like":"R%","snapshot_id":sid,"diagram_format":"mermaid","filename":"synthetic_topology_repeat","max_hosts":2}))' | "$SKILLS_PYTHON" -I -B -m olav_skills run writer draw_topology
```

夹具预期为 `status: ok`、两节点、一条边，返回 `path` 对应真实文件。应检查文件，不能只看退出码。名称模式限定范围，不会分类设备角色。大数据集选择更窄模式或 `center`/`hops`；`too_wide` 要求缩小，不代表可以静默丢节点。

## 将已有表格转成 SVG

纯转换器接受 Markdown 邻接表，不接受数据库路径。下面是单独的**虚构双节点示意**有效输入：

```json
{
  "adjacencies_table_markdown": "| Source | Local Intf | Dest | Remote Intf | Status |\n|---|---|---|---|---|\n| demo1 | Gi0/1 | demo2 | Gi0/2 | up |\n",
  "protocol": "l2_cdp"
}
```

通过 stdin 传给 `olav-skills run writer render_topology_svg`，结果是 JSON 编码 SVG 字符串，保存前应解码。`Local Intf`、`Remote Intf` 是接受的列别名；改成 `local-intf`/`remote-intf` 则不接受。省略注释代表没有产出图，此示意也不会向导入网络添加链路。

SVG 仅为表格转换；`draw_topology` 支持 draw.io 与 Mermaid。draw.io 图标可以使用名称启发式进行呈现，不能作为真实设备类型或角色证据。

## 图旁保留证据

报告应引用快照、采集时间来源、查询范围、节点/边数、缺失协议层及产物路径，关键结论配合聚焦 [reporter 证据](../skills/reporter.md)。随包合成报告是普通 Python 编写的 Markdown，不是报告写入 API。宿主写文件需要其自身权限。

记录邻接不证明包可达性或设计获批。分享及运维判断前核对来源覆盖、时间、截断和产物敏感性。
