---
title: 在 Docker 中运行 OLAV
description: 平台与网络域的容器化部署 —— 镜像、compose profile、持久化与配置。
---

# 在 Docker 中运行 OLAV

两种形态，由 compose profile 选择：

| | 镜像 | 包含 |
|---|---|---|
| 仅平台 | `olav:local` | CLI、运行时、平台 agent —— 5 agent / 10 子 agent / 9 工具 |
| 平台 + 网络域 | `olav-netops:local` | 上述内容，外加 olav-netops 与 Batfish —— 6 / 17 / 19 |

## 用 `run`，不是 `up`

OLAV 是交互式终端应用，不是服务。它没有需要常驻的东西：`docker compose up olav`
启动的容器会立刻退出。每一次 OLAV 调用都是 `run`：

```bash
docker compose run --rm olav doctor
docker compose run --rm olav --agent core "有多少台设备？"
```

唯一真正常驻的服务是 Batfish，也是唯一需要 `up` 的东西。

## 首次运行

```bash
cp .env.docker.example .env          # 填入端点与密钥
docker compose build olav
docker compose run --rm olav init    # 生成 /data/.olav
docker compose run --rm olav doctor  # 11 项检查，零模型调用
```

`doctor` 是排查配置问题最快的方式；它不调用模型，所以一秒内出结果，也不会
因为端点原因而失败。全新安装、未配密钥时它报 **8/11**——`llm`、`embedding`、
`memory` 是红的，因为还没配置，不是因为坏了。

## 加上网络域

```bash
docker compose --profile netops build olav-netops
docker compose --profile netops up -d batfish            # 等它 healthy
docker compose --profile netops run --rm olav-netops agent install olav-netops
docker compose --profile netops run --rm olav-netops doctor
```

`agent install` 会打印 `workspace 'audit' is platform-owned and already exists;
skipping overwrite`。这是**正确行为**，不是需要处理的警告：平台镜像本就带了
audit 工作区，用子仓库的开发镜像去覆盖它，正是两者产生漂移的原因。

Batfish 约需一分钟才接受连接。健康检查探测的是 9996 端口——netops 实际使用的
那个——并设了 60 秒 `start_period`，所以冷启动时 `depends_on: service_healthy`
不会失败。

## 什么会被持久化

一个命名卷 `olav-data`，挂载在 `/data`。OLAV 保存的一切都在它下面：

```
/data/.olav/config/api.json     模型、provider、端点（由 init 写入）
/data/.olav/databases/          DuckDB（网络 + 审计）与 LanceDB（记忆）
/data/.olav/workspace/          agent，由 init / agent install 部署
/data/exports/                  报告、拓扑图、变更计划、查询 CSV
```

`docker compose down` 会保留它，`down -v` 会删除。若想在宿主机上直接处理导出
产物，把卷换成绑定挂载：

```yaml
    volumes:
      - ./olav-data:/data
```

镜像以 uid 1000 运行，所以绑定挂载的目录需要该 uid 可写。

## 配置

`.env` 只承载不适合放进配置文件的东西——密钥、凭据、tier。模型、provider 和
端点写在 `.olav/config/api.json` 里，由 `init` 交互式生成。

有两项值得理解而不是照抄：

**`OLAV_LLM_MODEL_TIER`**（`small` ≈8K / `medium` ≈32K / `large` ≥200K 可用
上下文）决定召回的 `top_k` 和 `execute_sql` 的行数上限。设得过大不会立即失败——
它会在长任务里悄悄超出服务端窗口，表现为 `agent error, code 400`。`doctor` 会把
预算与服务端声明的窗口对比，在你撞上之前指出来。

**`OLAV_EMBEDDING_MODE`** 默认是 `api`。`local` 会在进程内跑
sentence-transformers，需要 `[local-embed]` extra，而本镜像**不装**它——那会拉进
一个多数部署并不想要的 CUDA 运行时。如果你的 embedding 服务对长输入是返回
HTTP 500 而不是截断（512-token 的 ubatch 在约 1000 字符以上就会这样），设置
`OLAV_EMBED_MAX_CHARS`——并且只能与服务端的 batch size **一起**调大，不要单独调。

## 访问宿主机上的端点

两个服务都把 `host.docker.internal` 映射到了宿主网关，所以跑在 Docker 宿主机
上的 LLM 可以这样访问：

```
OLAV_EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1
```

网络上其他位置的端点无需特殊处理。

## 设备访问（netops profile）

`CLAB_USERNAME` / `CLAB_PASSWORD` 是 netops 通过 SSH 访问实验室与生产设备的
凭据——与系统其他部分使用的是同一组名称。若用密钥认证，把 `SSH_KEY_DIR` 指向
存放密钥的目录，它会以只读方式挂载到 `/home/olav/.ssh`。

Containerlab **不在**这个容器里运行。它需要宿主机的网络命名空间和 Docker
socket 的特权访问，而把这个权限授予容器，是比「compose 默认值」更大的决定。
请在宿主机上运行 `containerlab`，让 netops agent 通过网络访问由此产生的设备。

## 已知限制

- **没有 Web 界面**。它已迁至 olav-ent，因此没有需要发布的 HTTP 端口。界面就是终端。
- **`local` embedding 在本镜像中不可用**（原因见上）。
- **Containerlab 不在范围内**（原因见上）。
- **镜像未发布**。两个 target 都从本仓库构建，目前 `docker compose build` 是获取
  它们的唯一方式。
