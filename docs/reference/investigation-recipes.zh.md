# 已记录数据的调查流程

在 0.2 中，将绝对路径 `OLAV_HOME` 绑定到授权项目后调用：

```sh
printf '%s' '{"recipe":"interfaces","snapshot_id":"capture-a","devices":["R1"],"limit":10}' | olav-skills run analyzer run_investigation
```

recipe可为inventory/interfaces/bgp，必须精确SID。devices/vendors各最多50个精确
字符串（每值256字符），空数组表示范围内发现；limit整数1–50默认10。拒绝SQL、
DB路径、warehouse后端、latest/current/all和未知参数。stdin16KiB；Python API有
单独有界的大对象预算。两入口都经专用exec reader，不能用进程内软计时代替硬停。

库存结果分当前目录与历史快照观测，额外列observed_without_catalog_count和受限
名称样本。无vendor过滤时包含目录缺失的已采集成员；vendor依当前目录已知归属筛选，
未知vendor明确排除，不推历史平台/角色。

状态遵循[原生合同](native-contracts.md)。IOS看status/line proto，Junos看admin/link，
proto只是协议族。任一已知非up分量（如admin-down）是nonhealthy观测，即使另一分量
未知；只有已知up/up才healthy，其余unknown。BGP数字只有明确前缀计数形态表示
Established，原生Junos state看已知文本。原生peer_ip/state与兼容字段对可用，双完整
形态拒绝歧义。默认Junos BGP/terse已通过合成原生导入，不代表真实采集或其它格式全覆盖。

healthy/nonhealthy/unknown分别按记录计数，同接口多family/地址可能多行。样本包含
非正常和未知记录，单元格256字符、has_more明确，非up不等于故障。缺所有支持视图时
步骤unknown/计数null，错schema失败。canonical视图可能只保近期记录，历史为空不证明
未采集。采集覆盖与实时状态始终unknown；日期形SID只保留label inference，不充采集时间。

Linux LP64专用读进程持OFD读锁，从已核描述符有界复制到父拥有的0700私有目录，
实际DuckDB只读打开副本；禁外部访问/溢写，交付前核源/副本身份、字节哈希与WAL。
单靠/proc/self/fd不能固定DuckDB实际打开对象。依赖可信本地FS、可信稳定临时根及
遵守DuckDB锁的写者，不认证作者或抵抗任意恶意文件改写。底层helper不能在持有同源
传统DuckDB锁的进程中直接调用；公开Python读入口也隔离，保全调用者writer锁。

父拥有scratch生命周期，输入读完后五秒硬预算，超时终止/回收worker并清副本；宿主
负责传输和崩溃恢复。引擎256MiB/单线程、DB256MiB、输出32KiB，schema有界；
不支持持久自定义macro或显式COLLATE DDL，scope/protocol列须标准VARCHAR。
返回实际DB/schema、recipe/执行器/状态合同hash、参数、来源与时间，不代表完整采集、
当前网络真值或签名。没有模型答案学习、答案缓存、新harness或自动WebUI授权。
