# Portable 知识导出

在独立 Python 环境安装 `olav-skills` 0.2.2 后，可从任意工作目录执行：

```sh
/absolute/path/to/skills-venv/bin/python -I -B -m olav_skills.portable --out /absolute/new-directory
```

目标目录必须全新、规范、位于已安装资源根外，并且路径不经过符号链接。命令只读取当前已安装包，不发现旧源码树、不加载 dotenv、不访问模型/数据库、不安装依赖、不执行脚本，也不授予宿主工具。未安装源码树不能代替 wheel 资源。

## 目录与哈希合同

`olav.portable-skills/v1` catalog（`version: 1`、`profile: portable`）索引七个技能。原始 SKILL.md、references、Python scripts 和技能库保留精确字节；每份资源包含相对来源路径、SHA-256、字节数，并由 catalog 条目归属。导出包含发行 LICENSE 和知识边界 README；发行元数据记录生成包版本。不写入绝对来源路径或宿主工具映射。

`script:<skill>/<filename.py>` 与 `agent:<skill>` 表示能力要求，不是授权。宿主应独立授权实际注册工具、校验 catalog 哈希/路径并实施读取和执行限制。源码示例是领域知识，不是宿主命令；导出的 scripts 只是资料，不是完整已安装 Python runtime。本导出器独立于私有集成。

## 工作流边界

`skills/netops/workflows/` 保留两个公开根网络工作流。catalog 固定标记 `execution_status: metadata-only`、`executable: false`，并列出缺失的 `agent:simulator`。原步骤保留，但本包不提供执行编排器。来源、字节哈希和许可延续记录在包源码的 `provenance/adapter-portable-20261117.json`。

## 有界加载

每资源上限 64 KiB、总索引资源字节 2 MiB、512 资源、2048 扫描条目、catalog JSON 256 KiB。符号链接（包括被忽略缓存目录内）、非普通文件、未知技能目录、不支持材料、错误元数据及悬空/越界 Markdown 链接均被拒绝。字节码缓存排除。链接检查支持有界 Markdown 语法的行内链接、引用定义、锚点及 HTTP(S)/mailto；代码示例不参与链接检查。

无效/缺失资源会在创建目标目录之前失败。固定安装资源和版本产生确定性输出。接入自己的加载器时，保留边界 README 和许可证，对错误采取失败关闭，不把 catalog 能力名直接转成系统执行权限。

这证明打包与知识可移植性，不证明普通语言路由、宿主权限、模型正确性、工作流完整执行或生产部署验收。完整 runtime 的安装方式见[安装](installation.md)。
