# 显式巡检证据覆盖与时效

两项时效政策须来自用户明确要求或实际授权宿主政策；缺核查时点或最大年龄时先询问，不用采集时间冒充核查时点，不自定默认年龄，也不以示例参数代替用户政策。

在 0.2 中，宿主授权后固定 `OLAV_HOME`，
运行`olav-skills run analyzer check_evidence`，stdin只接受四字段：

```json
{"snapshot_id":"capture-A","requirements":[{"device":"Edge-1","command":"show interfaces"}],"as_of":"2026-10-01T00:01:00Z","max_age_seconds":60}
```

快照必须是精确SID，不接受latest/current/newest/all。要求清单1–128项，
每项仅含device和command，二进制精确匹配，大小写及空格（包括VRF参数）都保留。
重复精确pair拒绝，不去重；不从库存补充或缩减分母。as_of必须带时区，
max_age_seconds为0–315576000整数，无默认24小时政策。

历史原文只读`netops.ingest_sources`，每项核实实际SID/设备/命令与
source_json.raw_artifact四字段完整key一致，actual_raw_sha256与原文实际hash一致。
错key/hash、重复原文或解析源整次失败。不退到last-writer表冒充完整历史。
缺历史表或精确SID无任何账本行为未知；精确SID已有账本覆盖而缺要求pair才为missing。
0.2 原生导入入口在同一事务内为新导入写历史账本；旧数据不回填，
后续新导入建表不改变旧SID无历史来源的unknown，不从last-writer补造历史来源。

解析结果独立读历史`netops.parsed_outputs`。nonempty仅表示非空JSON对象列表，
不证明解析正确、采集完整或超出该精确key的来源关联。空数组为empty；NULL、
非法JSON和不支持结构为unknown。原文存在不代表解析成功。

时效仅消费有来源且秒级带时区的command_header，capture_evidence必须有唯一
同值header，其他已知时间不得冲突，invalid/partial/conflict均不能当fresh。
日期精度、无时区、manifest/设备级时间、入库更新时间或日期形SID不能替代命令采集时间。
未来时间为unknown（可能时钟漂移）；age等于上限仍fresh，超限stale。
这只核对已存clock自洽，不认证真实时钟准确或来源真实性。

输出effective_request（四字段，as_of规范到UTC）、数据库source_version及全部原序清单。
每项raw已验且非空、parsed非空且clock新鲜才ready；已知缺失/空/超期为not_ready；
其他不足为unknown。整体not_ready优先于unknown。summary的ready/not_ready/unknown
分割显式分母；raw_verified/parsed_nonempty/fresh为不同维度，可重叠计数。
collection_completeness、parsing_success、health_assessment始终unknown；不认证来源。
ready不等于审计批准、全网完整、真实采集或实时健康。

仅Linux native；专用exec reader清洁环境，持锁有界副本/只读查询、最终源复核后关闭
reader默认连接，不关调用者连接或锁。预算：请求32KiB、响应256KiB、DB256MiB、
单raw/parsed512KiB、provenance64KiB、累计文本16MiB、父deadline15秒。
预算/锁/WAL/来源失败不返回假完整子集。

Linux专用worker在首次导入DuckDB公共模块前初始化单线程默认内存连接，验源后正常关闭并退出。初始化只允许真实worker exec入口，不替换Python调用者或已加载的连接；必要API和实际设置不符时拒绝执行。验证范围为DuckDB 1.5.6，不代表后续版本或包内所有native reader均已验收。
