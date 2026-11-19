# 一次性巡检规则集

在 0.2 中，固定授权项目的绝对 `OLAV_HOME` 后，使用绑定
解释器向 `olav-skills run reporter run_audit` 传一个stdin JSON：

```json
{"profile":"baseline","snapshot_id":"capture-a","devices":["R1"],"limit":5}
```

`{"operation":"list"}` 单独列出规则，不读数据。预置baseline运行库存成员、接口
状态、BGP状态三检查；inventory/interfaces/bgp/ospf分别单项。必须精确SID，不接受
latest/current/newest/all。devices/vendors各最多20个精确字符串（每值256字符），空数组
表示授权范围内发现。limit整数1–5；timeout_ms为输入/profile读完后的整次执行
预算1–18000毫秒，默认18000；总输入8KiB，profile文件16KiB，输出64KiB。

底层调用[固定调查流程](investigation-recipes.md)，保留原生来源、字段类型、时间
及资源限制。所有成功规则的实际DB SHA256必须相同，否则整次不交付混合结果；
profile/执行器hash、有效参数、每规则的完整受限证据和版本均保留。

库存规则只检查已观测设备能否在当前目录定位，不证明目录全量已采集或目录属性为
历史事实。接口/BGP规则按明确max_concerns阈值检查已知非正常记录；缺源、空集、
未知状态不当零或正常。计数单位是记录，不一定是唯一接口/会话，也不等确认故障。

规则state为pass/concern/unknown/not_executed。pass仅表示已观测检查符合这项
规则；有执行错误/超时/未执行项时status=partial、completed=false、overall=unknown，
之前已知的concern仍逐项保留。执行完成才可能总体concerns、unknown或
clear_for_observed_checks；live_state和collection_coverage始终unknown。
exit0只表示检查执行成功，业务有concern/unknown也可exit0；partial/error退出1。

可由已授权宿主创建 `.olav/audit_profiles/team.json`，再用profile_path代替profile：

```json
{"schema":"olav.audit-profile/v1","id":"team","version":"1","rules":[{"id":"ports","check":"interface_states","severity":"warning","max_concerns":0}]}
```

只允许1–3项不同固定check（inventory_membership/interface_states/bgp_states/ospf_states）、
唯一id、info/warning/critical severity和0–1000000整数阈值，禁止额外字段/SQL/
用户代码/动态模板。拒绝祖先/文件符号链接、硬链接、越界路径及超限文件。
severity和容忍阈值是明确配置的审阅策略，不认证实际事件影响。

Linux下子能力在本次新进程组中执行，父进程持有TMPDIR、超时终止该组后清理副本；
宿主负责输入/传输超时。无调度器、模型会话、自动重试/补数据、DB写入或报告自动
追加。若要文件交付，由授权宿主保存并审查JSON/报告，再经[证据包](evidence-delivery.md)
显式打包；脚本存在不增加WebUI工具权限或证明各harness自然路由。

## OSPF与显式证据准入

`ospf` 规则集使用固定的 `ospf_states` 检查，调用
[OSPF观测分析](ospf-analysis.zh.md)。默认baseline仍为原三项，不自动扩展。
Full仅选定观测规则可通过；过渡态只待查；2-Way/未知/缺源不认证健康。
自定义策略仍最多3个不同固定check。

可选 `evidence_check` 仅含requirements/as_of/max_age_seconds，snapshot由parent绑定。
先读[证据准入](evidence-readiness.zh.md)。父进程核真实有效请求、分母及DB hash，
非ready/未知/失败则跳过规则、partial/unknown；后续每条规则须与precheck同源hash。
预期设备必须在非空devices过滤范围内；vendors过滤与该门组合暂拒绝，不猜历史映射。
显式时效与清单仅证明这些需求的已存证据资格，不证明全网覆盖/解析正确/来源真实性/健康。
无此参数时不发明分母/时效，整次18秒预算包括precheck，不重采集或自动重试。
新native CLI导入保存新来源历史，旧无账本不回填。完整字段及例子见安装资源
`reporter/references/audit_profiles.md`。
