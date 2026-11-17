# learner：准备与验证 CLI 解析器

Learner 用于补齐已知平台/命令组合的结构化解析缺口。它是围绕人或宿主模型草拟环节的两步确定性接口，不是模型训练、微调、记忆召回或自动学习服务。

## 准备有代表性的样本

`prepare_learn` 必须提供 `platform`、`command` 及非空 `samples`，每条有 `device` 与 `raw_output`。返回 `status`、结构提示、样本数和草拟 prompt，不通过远端模型生成解析器。只使用授权文本，移除敏感内容时保留验证所需结构。

对齐且每行一条记录的表格优先 TextFSM；复杂多行/缩进记录可使用纯 Python `parse(raw: str) -> list[dict]`。候选首行为 `# OLAV_DSL: textfsm` 或 `# OLAV_DSL: python`，之后直接源码，不带 Markdown 代码围栏。应对全部给定样本成立，覆盖不同值，并明确处理空输出/错误输出情况。

## 校验并冻结

`finish_learn` 传入相同 `platform`、`command`、`samples`，以及完整草案 `parser_response`；可选 `contract_version` 默认为 1。它检查 DSL、验证样本解析，再将成功候选保存到分析根模板树。Python 候选还需通过 AST 允许名单和沙箱验证。文件/网络访问、动态执行均不是允许的解析器行为。

检查 `status`、`diagnostic`、`path`、`quarantined`、`sample_count`、`parsed_rows`。`invalid` 表示验证失败，不冻结候选。**TextFSM 即使只有一个样本，成功也立即启用**；低样本是警告，不是隔离。低样本 Python 候选可进入 quarantine。应查看真实 `quarantined`，不能假定两种 DSL 启用策略一致。

解析器参与之后的解析/导入，不保证自动重解析已存旧记录。重新导入或重建现有流程前，审阅解析器并先用可丢弃分析副本验证。

## 选择正确缺口

先读导入报告。`no_parser_registered` 适合考虑扩展；`raw_only` 是刻意不结构化，不应误当缺解析器。`parser_error` 可能属于缺陷，而不是新格式学习。解析器修改与原导入汇报分别记录。

使用多样草拟样本，并保留独立验证样本及预期行。草拟样本通过，只能证明这部分验证，不能证明所有设备/版本都准确。[解析器工作流](../workflows/parsers.md)提供可执行合成准备例子及审阅要点。
