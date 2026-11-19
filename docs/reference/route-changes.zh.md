# 历史路由比较

`compare_routes` 可用于尚未发布的原生 Skills 候选版本。它在 `OLAV_HOME` 指定的项目中，比较两个精确快照 ID 下的 Cisco IOS 路由解析记录。

```sh
export OLAV_HOME=/absolute/path/to/authorized/project
olav-skills run reporter compare_routes <<'JSON'
{"source":"ios_ipv6_rib","snapshot_id_1":"snap-before","snapshot_id_2":"snap-after","device":"edge-a","vrf":"BLUE"}
JSON
```

| `source` | 历史命令 | 比较内容 |
| --- | --- | --- |
| `ios_ipv4_rib` | `show ip route` 或 `show ip route vrf <名称>` | IPv4 前缀和已记录的下一跳集合 |
| `ios_ipv6_rib` | `show ipv6 route` 或 `show ipv6 route vrf <名称>` | IPv6 前缀和已记录的下一跳集合 |
| `ios_ipv4_bgp` | `show ip bgp` 或 `show ip bgp vpnv4 vrf <名称>` | IPv4 CIDR 前缀、下一跳集合和 ASN 序列 |

可选的 `device`、`vrf` 按原值区分大小写。路由上下文以历史记录内显式 `vrf` 或历史快照所存的单 VRF 命令为依据。查询过滤条件无法补齐缺失的上下文。IOS IPv6 和 BGP 裸命令的常规模板没有 VRF 字段。要比较限定 VRF 的命令，导入时需要已有解析器产出受支持的字段。Junos 路由汇总只提供表名和计数，暂无逐前缀比较。

每条观测按设备名、VRF、地址族和规范化 CIDR 前缀识别。同一身份内的相同路径合并。IPv6 链路本地下一跳需要已记录的接口。路由度量值和协议选路不在本比较范围内。BGP 仅在两侧下一跳集合可比较且相同、相应序列均明确时比较 AS_PATH。支持普通 32 位 ASN 序列，也接受原生标准化器输出的单个整数。缺失、空白以及不受支持的 AS_SET 或联盟格式保持未知。

`olav.route-diff/v1` 返回可比较范围的 `counts`、`changes` 样本和 `gaps` 缺口。`counts: null` 表示没有可比较范围。`comparison_status: partial` 表示部分上下文或字段证据不足。`limit` 可指定 1–50 个样本，默认 20。字节上限还会限制样本数，计数覆盖本次成功比较的已存记录。可用 `timeout_ms` 缩短最长 15 秒的截止时间。输入超限或超时返回未知计数。

比较在 Linux 上用读锁生成有界的原生数据库副本，并记录源 SHA-256。源数据库须已经 checkpoint。快照的比较方向取决于请求顺序。前缀和路径差异描述存储观测，实际采集时间与覆盖范围未知。记录中缺少某个前缀，无法据此确认真实撤路或可达性变化。
