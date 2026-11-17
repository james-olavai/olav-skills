# 路径、环境与本地配置

可复现原生离线操作应使用独立 venv、显式新 `OLAV_HOME` 和明确工作目录。部分路径在导入时解析并缓存，因此先设置环境，再导入 Python 模块。

## 选择一个分析根

```sh
export OLAV_HOME=/absolute/path/to/authorized-analysis
cd "$OLAV_HOME"
/absolute/path/to/skills-venv/bin/python -I -B -m olav_skills doctor
```

普通调用前先建根目录；随包 walkthrough 可以创建不存在/空的根。`doctor` 检查包，不检查数据正确性。`-I` 隔离 Python 导入行为，不隔离文件系统权限，也不屏蔽包使用的环境变量。

通用配置根解析顺序：显式 `OLAV_HOME`，否则 CWD 最近包含 `.olav/` 的祖先，最后 CWD。**有界 `query_batch` 必须给显式 `OLAV_HOME`**，不会使用这个通用回退。不要依赖 shell 所在目录隐式选择敏感数据。

| 相对根的默认路径 | 用途 |
| --- | --- |
| `.olav/databases/main.duckdb` | 原生本地分析库 |
| `.olav/config/` | 已存在的本地 JSON 配置 |
| `.olav/templates/` | 自定义 TextFSM/Python 解析器 |
| `exports/snapshots/` | 文件系统工具使用的已保存快照 |
| `exports/` | 报告、图表及其它产物 |

部分脚本产物相对 CWD，另一些使用配置根常量。检查真实返回 `db_path`、`report_path`、`path`，不能从本表推断所有输出位置。

## 识别生效覆盖项

`AGENT_DIR` 与 `OLAV_PATHS_AGENT_DIR` 影响保留的部分路径配置；`OLAV_PATHS_DATABASES_DIR` 修改使用该解析器的数据库目录，而原生 batch 固定 `OLAV_HOME/.olav/databases/main.duckdb`。`OLAV_PATHS_SNAPSHOTS_DIR` 修改已保存快照位置。首次 walkthrough 避免这些覆盖，以便各入口读取同一预期范围。

SQL 读取器使用 `OLAV_SQL_MEMORY_LIMIT`、`OLAV_SQL_THREADS` 和可选 `OLAV_SQL_TEMP_DIR`，值受校验/限制，不授予外部访问，也不形成操作系统内存沙箱。请求预算见[脚本](scripts.md)。`OLAV_LLM_MODEL_TIER` 或保留本地配置可以改变/降低查询呈现上限；这些兼容设置不在独立 CLI 中启动或配置模型服务。

保留的配置加载器在文件存在时可读取 `api.json`（或旧模型 JSON）、`runtime.json`、`tasks.json`。这是兼容代码，不证明 Skills 提供文件名涉及的旧平台功能。CLI 不加载 `.env`、不询问模型密钥，确定性示例也不需要密钥。宿主确实需要凭据时，应只通过受控进程环境传入，不把整个环境输出到日志。

## 服务端 profile 分开管理

保留的 `.olav/config/ingest-storage.json` 可选择宿主管理的服务路径。错误配置不能静默当成空原生库。本手册可复现流程使用没有继承这类配置的新本地根，不介绍服务端供应，也不迁移 API-client/DuckLake 部署。继承的 `OLAV_DB_PATH` 不是所有原生入口通用支持的覆盖项，配置读取器可能拒绝它。

导入前备份目标分析根并审阅文件访问。数据根绑定不是逐用户 ACL。复用现有项目前请读[安全](../safety.md)及[迁移](../migration.md)。
