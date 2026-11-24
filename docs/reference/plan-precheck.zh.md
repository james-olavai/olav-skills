# 变更计划记录预检

Olav Skills 0.2 提供 `check_change_plan`。
该检查器不执行命令，也不批准上线。

固定授权项目及显式 snapshot，调用
`olav-skills run analyzer check_change_plan`，stdin 是一个 JSON 对象：

```json
{"snapshot_id":"capture-a","plan":{"schema":"olav.change-plan/v1","targets":[{"device":"edge-a","platform":"cisco_ios","template":"cisco_ios","steps":["description planned"],"rollback":["description prior"],"verification":[{"command":"show interfaces","expected":"描述符合人工复核意图"}],"addresses":[{"address":"192.0.2.9/24","vrf":"blue"}]}]}}
```

上例仅展示合成结构，并非已验证的 CLI 步骤。显式 `OLAV_HOME` 或 `project`
指定已授权项目；参数自身不授予权限。数据库固定为项目内
`.olav/databases/main.duckdb`，不使用 warehouse 或数据库路径覆盖。
拒绝 latest/newest/auto；snapshot 标签不证明采集时间或实时状态。

`plan` 与 `plan_path` 必须恰好提供一个。结构化计划使用上述 schema，目标设备
唯一；steps/rollback 为非空文本数组，每个 verification 需要非空 command
和 expected。addresses 可选，支持 IPv4/IPv6 地址或 CIDR 与显式 VRF。
没有掩码的地址只支持相等检测，不据此推断子网。

`plan_path` 只能是所选项目内 `exports/change_plans/` 开头的相对路径，
拒绝父目录穿越和 symlink；`.json` 按结构化计划读取，其余按 Markdown。
inline Markdown 使用字符串 plan。不创建文件或目录。

Markdown 仅检查支持的占位符及旧生成器的设备标题、Rollback、Post-checks
结构标记。出现标记不证明每设备覆盖、回滚有效、验证有意义或 CLI 正确；
目标/平台/地址/回滚/验证语义始终 unknown。旧生成器仍是 Cisco IOS 占位符草稿，
以角色排序，不扩成多厂商或图 BFS。

## 证据与结果

输出 `olav.plan-precheck/v1`，execution_status 与 checks_outcome 分开。
每项包含 pass/fail/unknown、原因及 scope 证据；decision 恒为 not_approved。
exit 0 只表示检查运行成功；非法输入、路径或输入/计划/输出超限为 error、exit 2。

成员及历史平台只依据 netops.parsed_outputs 中所选 snapshot 的真实记录。
全局 inventory 或其它 snapshot 的记录不证明本快照成员或历史平台。
缺源、空源、schema 漂移、读取失败、截断均保留 unknown。完整目标读取未找到
设备且所选快照有解析观测时，成员项为 fail。从 0.2.2 起，`source.requested_devices`
列明计划中的目标；对有效的拟用地址单独查询并记录 `address_source`，
跳过读取时返回 `status="not_read"`。cisco_ios template 仅检查身份，
不验证语法；其它模板未知或不支持。

地址只消费 show ip interface brief、show ipv6 interface、show interfaces terse
的 parsed_data 对象列表，规范字段为 ip_address、可选 prefix_length/vrf。
TextFSM Tier1 和 NTC 将header转小写；仅显式兼容大写 IP_ADDRESS/PREFIX_LENGTH/VRF。
其它拼写/布局不猜；同一记录大小写别名冲突，或独立前缀与内嵌CIDR矛盾时排除该记录，
address_source_fields 返回 unknown 与字段原因。解析器缺字段不重建：例如原生 IOS
interface brief 裸IP不提供子网或VRF；
不支持的布局不推断地址。检查源记录及计划内先前地址的相等、已知前缀交集。
address_overlap 的 pass 仅表示检测到交集，**不是已证冲突或安全**。
缺 VRF 不假定 default；不同 VRF、anycast、复用意图、采集覆盖/时间都会影响判断。
没发现交集不证明全源无冲突；端口 down 或无地址不等于空闲。

CLI 语法、可达、仿真、采集时间和上线批准始终 unknown。
不提供网络、模型调用、命令执行或业务数据库写入。

## 预算与边界

输入96KiB、计划64KiB、目标32、steps/rollback/verification合计256项、
每目标地址32、findings512、输出256KiB。固定参数化 SELECT 复用只读 SQL
预算：1000行、cell4000字符、查询预览256KiB、5秒、DuckDB内存设置256MB、1线程。
行数上限不等于扫描或宿主RSS上限。地址观测最多1000条，超限拒绝。
源截断/错误可观察，不伪装空成功。拒绝 OLAV_SQL_TEMP_DIR，避免可写spill目录。
调用者不能提高预算。不代表已接所有 harness 路由或新增 Open WebUI 工具。
