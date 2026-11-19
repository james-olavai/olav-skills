# 报告引用校验

未发布候选新增 `olav-skills run reporter lint_report`，将显式报告及 claim
映射与调用方固定的 scope/source/精确快照/hash 清单匹配。它不发现文件、不读
数据仓库。已发布 `olav-skills==0.1.1` 尚无此入口。

每个 claim 指定从 1 开始、两端包含的报告行范围和证据 ID；范围内字面标记
`[evidence:E1]` 的集合必须与映射相等。所有非空行须归入不重叠范围；标题或
表格可与相邻结论合并，无需逐行建 claim。未映射内容、未知或缺失引用、未使用
证据、身份或 hash 不符均失败。重复身份、非法范围作为输入错误拒绝。

可信 host 先审查实际来源，再将原始 UTF-8 结果包装成
`olav.report-evidence/v1` JSON envelope。`body` 是原始字符串；SHA-256 对
**body 原始 UTF-8 字节**计算，保留原始换行及末尾换行，不对包装器或重新格式化
JSON 计算。envelope 声明 scope/source/精确 SID，独立可信渠道固定的 `context`
限定允许的 source/SID/body-hash 元组。`run_audit` 或比较结果须由 host 根据
真实 provenance 审查后包装，不自动转换结果 schema 或发现来源。
`evidence_bundle` 的文件 hash 属于另一合同，不能替代这里的 body hash。

可用内联 `report_text` 和 envelope `evidence[].text`，也可显式指定
`report_path`/`evidence[].path`，仅限 `project/exports/evidence_sources/`
内暂存文件。文件模式要求绝对 `project`，拒绝符号链接和路径穿越。无写入。
预算为请求 512 KiB、报告 64 KiB/500 行、128 个 claim、16 份证据、每个 envelope
32 KiB/合计 256 KiB，最多返回 64 条问题；总问题数与截断标识保留。

完成检查返回 `verdict:pass|fail`；输入错误返回 `status:error`、
`verdict:unknown`。仅 pass 退出码为 0。
`coverage:all_nonblank_lines_mapped` 仅表示文本落入映射范围，不是事实覆盖率；
一个范围可以包含多个断言。`claim_truth` 与 `source_authenticity` 始终为
`not_checked`，`identity_check:caller_context_only` 明示信任边界。
正文错误但引用一致仍可能通过；若作者能同时替换 context 和证据，本离线工具
不能发现这种自洽伪造。它不证明新鲜度、采集完整、实时健康或执行批准。

完整字段、可执行 host 包装示例、字面引用规则与错误行为见安装后的技能资源
`reporter/references/report_citations.md`。
