# 已记录指标的历史基线

这是未发布源码候选，已发布 `olav-skills==0.1.1` 不含此入口。宿主须先授权原生项目。

```sh
export OLAV_HOME=/absolute/authorized/project
printf '%s' '{"baseline_snapshot_ids":["h0","h1","h2","h3","h4","h5"],"target_snapshot_ids":["t0","t1"],"device":"Edge-1","interface":"GigabitEthernet0/1","profile":"input_errors"}' |
  olav-skills run analyzer analyze_baseline
```

五个参数全部必填，设备和接口精确匹配已存名称。固定支持 IOS `show interfaces` 的
`input_errors`、`crc`、`output_errors`（errors/second）及
`queue_drops`、`queue_output_drops`（packets/second）。指标是已记录累计值的净增量除以
已证时间间隔，不认证真实事件发生率或计数器连续性。错误类别可能重叠，不能直接相加。

历史 SID 是按采集顺序排列的 2–32 个互不重复精确 ID；统计至少需要六个快照、五个相邻区间。
另给两个独立目标 SID，不能出现在历史列表中，目标不会参与历史分布。只认可已存、带时区的
命令级真实时点，不从 SID、导入时间或字典顺序猜测时间。全部历史区间必须为正且时长相等。
缺源、缺指标、缺时钟、时钟冲突、平台不支持、已观测重启/清零或计数下降均使区间未知；
任何一个历史区间未知，就不生成整组统计或阈值建议。采集间隔变化也不生成统计。
不会丢弃未知区间后挑选有效子集。普通原生离线包导入可能保留计数但没有命令级时钟，速率须未知。

使用全部选定区间计算最小值、最大值、算术平均值、中位数、最近秩 P90/P95，秩为从一开始的
`ceil(p*n)`。仅五个区间时两百分位均为最大值，只是小样本统计参考，不是稳定总体尾部分位估计。
P95 建议始终 `not_approved`，不写告警规则、不调度。目标比较还要求其起点严格晚于最后历史时点，
且目标区间时长一致；否则目标比较未知，已成立的历史统计仍可保留。
有效目标仅区分 `above_reference` 和 `at_or_below_reference`，不宣称健康或故障。

保留 `health_assessment: unknown`、`counter_continuity: not_certified` 及
`unobserved_reset_possible: true`。没有发现重置，包括 IOS 显示 “never”，都不能证明未发生
未观测重启/清零/回绕。固定命令、平台、字段、单位和相等间隔之外，解析器/采集变化、设备身份连续性
以及采集完整性仍需调用者复核；不能用自洽来源 hash 认证真实性。

同一固定数据库版本内逐对读取精确设备/接口的完整唯一观测，不从截断的全网样本计算总体。
重复命令或接口报错。上限为请求 16 KiB、结果 64 KiB、32 个历史 SID、数据库 1 GiB、
单命令 512 KiB、每对选定数据 32 MiB、全操作 15 秒。执行成功不代表基线可用，须检查语义状态。

原生 Linux 专用子进程使用锁定私有副本、只读查询、最终源复核和父进程超时监督，输出前关闭自身
DuckDB 连接；不关闭调用者连接、不写源库。依赖可信本机文件系统/临时目录、遵守锁的写者和可信已存
记录/时钟。warehouse、真实采集、模型/各 harness 自然路由、浏览器和生产部署均需另验。
