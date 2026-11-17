# Importer

**importer** 把离线快照落地到 OLAV 的主数据库 —— 与实时 SSH 采集相同的
`raw_output_store` + 结构化视图。丢进一个可移植 bundle、rancid 备份或厂商导出，
它就把数据落地，无需 OLAV 主机做任何 SSH 访问。

当 OLAV 主机无法直接到达设备时使用它：

- **气隙站点** —— 操作员从跳板机采集后把输出传回。
- **既有备份** —— rancid / Oxidized / NCM 已在每晚采集；复用这些数据。
- **厂商支持交接** —— 用于诊断的 `show tech-support` bundle。

!!! note "collector 的离线兄弟"
    [collector](collector.md) 做**实时** SSH 采集。importer 做**离线**文件采集。
    两者下游相同（`raw_output_store → parsed_outputs → v_*_auto views`）。

---

## 何时使用

- "把这个 zip 里的快照导入 OLAV"
- "我有一个 rancid 备份目录 —— 能分析吗？"
- "在客户用 tarball 发来的数据上跑审计"

---

## 工具

| 工具 | 何时使用 |
|------|---------|
| `survey_bundle` | 先调它 —— 格式检测 + 主机/平台勘察 |
| `validate_bundle` | sha256 + manifest schema 校验（不写 DB）—— 导入前先跑 |
| `ingest_snapshot` | 把 bundle 落地到 `raw_output_store` + 结构化视图 |
| `discover_platform_for_host` | 对某主机做 TextFSM 级联平台检测 |

---

## 示例

```bash
olav --agent netops "导入 ~/.olav/inbox/snapshot.bundle 的 bundle"
```

编排器路由到 importer，它先勘察 bundle（`survey_bundle`），再校验
（`validate_bundle` —— sha256 / schema 不匹配则拒绝继续），然后用
`ingest_snapshot` 落地，并报告落地的主机与命令。

```python
task("importer", "先校验再导入 /data/backups/site-tor1 的 rancid 备份")
```
