# 参与 Olav Skills

维护中的公开源码为 [james-olavai/olav-skills](https://github.com/james-olavai/olav-skills)。提出修改前先读仓库规则与 LICENSE。原生产品直接维护源码，不要用旧生成导出覆盖。

## 找到归属位置

| 路径 | 职责 |
| --- | --- |
| `src/olav_skills/` | 确定性库、管理 CLI、必要包数据 |
| `skills/` | 权威标准指令、脚本及参考 |
| `examples/`、`tests/` | 公开合成示例与有意义回归 |
| `docs/` | 当前公开手册与单独保存的历史资料 |
| `provenance/` | 提取来源映射与转换记录 |
| `tools/` | 本地构建/导出/发行工具 |

保留单一发行包和 `olav_skills` 导入边界。原生功能不要重新依赖退役平台或代理 SDK。技能指令不授予宿主权限。Collector、私有 WebUI、模型服务及服务端维护各有独立项目合同。

## 让修改可复现

在独立 venv 开发，源码可用 `python -m pip install .` 安装。发行验证则构建 wheel/sdist，在源码树外的新环境非 editable 安装。构建后端为 Hatchling；`python -m build` 需要开发环境另装 `build` 前端。editable 导入不能代替发行产物测试。

执行数据代码前，将 `OLAV_HOME` 固定到新的临时目录。使用合成采集和独立预期集合/行，覆盖与变更相关的缺来源、未知时间、未知角色、截断和拒绝场景。仓库 `tests/` 使用 pytest，所选测试工具另行安装。先跑最小且有意义的受影响检查，涉及发行再完成所需发行检查。[已安装示例](offline-data.md)验证的不只是导入成功。

保留有界 SQL、未知值、稳定数据根、解析器来源和产物证据。响应或参数改变时，同时更新双语页面及真实资源指令/合同，不手工维护第二份生成 runtime。

## 提交有用缺陷

在[问题跟踪](https://github.com/james-olavai/olav-skills/issues)提供包/Python 版本、解释器、精确公开入口、最小合成 stdin、语义预期、真实 JSON、退出码和独立 stderr。移除凭据、客户采集、现役配置、私有部署路径及内部实施证据。保留首错，不只留下后来成功样本。

自然语言行为应记录原始日常问题、真实宿主/模型/设置、生效技能、工具参数/结果与最终回答。指定脚本测试标为工具合同测试。包执行、技能发现和模型正确性是不同层级。保留许可声明，不作缺证的性能或兼容性承诺。
