# Metric Pulse Platform 生产部署与运维手册

本文档说明如何使用已发布的 Docker 镜像，在 Linux 服务器或群晖 NAS 上部署、验证、升级、回滚和维护 Metric Pulse Platform。命令默认在项目部署目录执行，示例正式版本为 `1.0.3`。

## 1. 部署架构

生产编排由 `compose.prod.yaml` 定义，包含以下服务：

| 服务 | 用途 | 是否暴露主机端口 |
| --- | --- | --- |
| `web` | Vue 静态页面、Nginx 反向代理 | 是，仅暴露 Web 端口 |
| `api` | 登录、文件、任务、审核和导出 API | 否，由 `web` 代理 |
| `worker` | Celery 串行执行采集任务 | 否 |
| `migrate` | 启动前执行 Alembic 数据库迁移 | 否，成功后退出 |
| `postgres` | 任务、状态、审核和审计数据 | 否 |
| `redis` | Celery 消息队列和结果后端 | 否 |

外部依赖：

- OMLX：OpenAI 兼容接口，只允许使用 `Qwen3.8-27B-6bit`；
- SearXNG：为无采集链接或直链无法匹配的行提供搜索结果；
- 互联网：用于访问来源网页、附件、GitHub 和 Forbes 等公开来源。

生产采集系统不能读取或感知任何“金标表”。金标只能由生产运行之外的测试或验收程序使用。

## 2. 部署前检查

### 2.1 硬件与系统

- 当前正式镜像平台为 `linux/amd64`，适用于 x86_64 群晖和 Linux 主机；
- 建议至少 4 核 CPU、8 GB 内存；数据库和对象文件所需磁盘空间按工作簿及证据量预留；
- Docker Engine 24 或更高版本；
- Docker Compose v2.20 或更高版本；
- NAS、OMLX 和 SearXNG 应网络互通；
- 部署端口未被占用，并已在主机防火墙中允许受信任局域网访问。

检查命令：

```bash
uname -m
docker version
docker compose version
df -h
```

若 `uname -m` 不是 `x86_64`，不要直接部署当前镜像，应先发布对应架构的正式版本。

### 2.2 端口规划

默认 Web 端口为 `8080`。建议在共享 NAS 上使用未占用的高位端口，例如当前部署使用 `56123`：

```bash
ss -lnt | grep ':56123 '
```

无输出通常表示端口未被占用。部署后只需放行此 Web 端口，不应向局域网或公网直接暴露 PostgreSQL、Redis 和 API 容器端口。

### 2.3 外部服务验证

在部署主机上验证 OMLX 和 SearXNG。不要在终端输出或日志中打印 API Key：

```bash
curl --fail --silent --show-error --max-time 15 \
  -H "Authorization: Bearer ${OMLX_API_KEY}" \
  http://OMLX_HOST:5008/v1/models >/dev/null

curl --fail --silent --show-error --max-time 15 \
  'http://SEARXNG_HOST:8888/search?q=test&format=json' >/dev/null
```

## 3. 准备部署目录

群晖应把部署文件放在共享卷中，例如：

```bash
mkdir -p /volume2/docker/metric-pulse-platform
cd /volume2/docker/metric-pulse-platform
```

从对应版本标签下载生产编排文件，避免使用随 `main` 变化的文件：

```bash
curl --fail --location --output compose.prod.yaml \
  https://raw.githubusercontent.com/xingzichen/metric-pulse-platform/v1.0.3/compose.prod.yaml
```

也可以从仓库检出对应版本后复制 `compose.prod.yaml`。生产环境不需要源代码、Node.js 或 Python 开发环境。

## 4. 配置生产环境变量

在部署目录创建 `.env`，不要把真实配置提交到 Git：

```dotenv
# 镜像版本与入口端口
MP_IMAGE_TAG=1.0.3
MP_WEB_PORT=56123

# 数据库密码建议使用 openssl rand -hex 32 生成
POSTGRES_PASSWORD=替换为随机强密码

# 首次启动时创建的管理员。首次创建后，修改此变量不会重置数据库中的现有密码。
MP_BOOTSTRAP_USERNAME=admin
MP_BOOTSTRAP_PASSWORD=替换为管理员强密码

# OMLX 固定模型
MP_OMLX_BASE_URL=http://OMLX_HOST:5008/v1
MP_OMLX_MODEL=Qwen3.8-27B-6bit
MP_OMLX_API_KEY=替换为OMLX密钥
MP_OMLX_TIMEOUT_SECONDS=900
MP_OMLX_MAX_OUTPUT_TOKENS=4096
MP_SHEET_ANALYSIS_MAX_OUTPUT_TOKENS=2048
MP_SYNTHESIZE_MAX_OUTPUT_TOKENS=4096
MP_VERIFY_MAX_OUTPUT_TOKENS=4096
MP_VISION_TABLE_ENRICHMENT_ENABLED=true
MP_VISION_TABLE_MAX_OUTPUT_TOKENS=8192
MP_VISION_TABLE_RETRY_MAX_OUTPUT_TOKENS=16384

# 搜索服务
MP_SEARCH_URL=http://SEARXNG_HOST:8888/search
MP_SEARCH_TIMEOUT_SECONDS=60
MP_SEARCH_MIN_INTERVAL_SECONDS=60
MP_SEARCH_RETRY_DELAY_SECONDS=60

# 来源获取与浏览器回退
MP_SOURCE_FETCH_CONCURRENCY=3
MP_SOURCE_CACHE_TTL_SECONDS=86400
MP_SOURCE_TRANSIENT_COOLDOWN_BASE_SECONDS=60
MP_SOURCE_CHALLENGE_COOLDOWN_SECONDS=3600
MP_SOURCE_COOLDOWN_MAX_SECONDS=3600
MP_SOURCE_HOST_MIN_INTERVAL_SECONDS=2
MP_BROWSER_FALLBACK_ENABLED=true
MP_BROWSER_TIMEOUT_SECONDS=180
MP_BROWSER_SETTLE_SECONDS=5
MP_BROWSER_MIN_CONTENT_CHARS=500
MP_BROWSER_SITE_COOLDOWN_SECONDS=30

# 单实例 OMLX 环境建议关闭上传后的视觉后台识别，避免 API 与采集 Worker 争用模型。
# 工作簿的确定性结构分析仍会正常执行。
MP_VISION_ANALYSIS_ENABLED=false

# 直接使用局域网 HTTP 时为 false；经 HTTPS 反向代理访问时改为 true。
MP_SESSION_COOKIE_SECURE=false
```

设置权限并验证 Compose 插值。验证命令可能展开敏感变量，不要把完整输出保存或粘贴到公共日志：

```bash
chmod 600 .env
chmod 640 compose.prod.yaml
docker compose --env-file .env -f compose.prod.yaml config --quiet
```

注意事项：

- `MP_OMLX_MODEL` 只能是 `Qwen3.8-27B-6bit`；
- OMLX 的 128K 是输入与输出的总上下文上限，不要把所有阶段都直接调到 16K；视觉表格只有明确截断时才自动升档；
- Worker 固定 `concurrency=1`，禁止增加 Celery 并发或启动多个 Worker 副本；
- `source-cache-data` 卷必须同时挂载到 API、migrate 和 Worker，且纳入备份；删除该卷会失去正文/图片成功缓存和挑战页冷却状态，但不会删除数据库业务记录；
- 单实例 OMLX 部署建议保持 `MP_VISION_ANALYSIS_ENABLED=false`；
- 密码和 Key 不应出现在 Compose 文件、截图、工单或 Git 历史中；
- 示例密码仅是占位符，不能直接用于生产。

## 5. 首次启动

拉取固定版本镜像并启动：

```bash
docker compose --env-file .env -f compose.prod.yaml pull
docker compose --env-file .env -f compose.prod.yaml up -d --remove-orphans
```

首次启动顺序如下：

1. PostgreSQL 和 Redis 通过健康检查；
2. `migrate` 执行数据库迁移并以状态码 `0` 退出；
3. API 和 Worker 启动；
4. API 健康后 Web 启动。

查看状态：

```bash
docker compose --env-file .env -f compose.prod.yaml ps -a
docker inspect metric-pulse-migrate-1 --format 'MIGRATE_EXIT={{.State.ExitCode}}'
```

正常状态应满足：

- `postgres`、`redis`、`api` 为 `healthy`；
- `worker`、`web` 为 `Up`；
- `migrate` 为 `Exited (0)`；
- Worker 日志显示 `concurrency: 1` 和 `ready`。

```bash
docker logs --tail 80 metric-pulse-worker-1
docker logs --tail 80 metric-pulse-api-1
```

## 6. 部署验收

### 6.1 HTTP 健康检查

在 NAS 本机验证：

```bash
curl --fail --silent --show-error --max-time 15 \
  -o /dev/null -w 'WEB_HTTP=%{http_code}\n' \
  http://127.0.0.1:56123/

curl --fail --silent --show-error --max-time 15 \
  -o /dev/null -w 'READY_HTTP=%{http_code}\n' \
  http://127.0.0.1:56123/health/ready
```

两个请求都应返回 `200`。随后从另一台局域网设备访问：

```text
http://NAS_IP:56123
```

若 NAS 本机返回 `200`、局域网设备无法连接，应检查群晖控制面板中的防火墙和网络规则。

### 6.2 登录检查

使用 `.env` 中的管理员账号登录。可通过 API 做只返回状态码的检查，避免输出会话 Cookie：

```bash
curl --silent --show-error --max-time 15 \
  -o /dev/null -w 'LOGIN_HTTP=%{http_code}\n' \
  -H 'Content-Type: application/json' \
  --data '{"username":"admin","password":"替换为管理员密码"}' \
  http://127.0.0.1:56123/api/v1/auth/login
```

预期为 `LOGIN_HTTP=200`。

### 6.3 空表任务冒烟测试

通过 Web 页面执行：

1. 上传原始空表；
2. 确认文件状态为“就绪”；
3. 确认六个范围外工作表显示“不由本平台处理”；
4. 使用默认数据集选择创建任务并立即启动；
5. 观察任务由“排队中”进入“运行中”；
6. 至少观察 10 分钟，确认成功数持续增加、运行中始终最多为 1、失败数没有持续增长；
7. 抽查结果、来源路由、证据和业务解决状态。

“执行成功”只表示采集与校验流程正常结束，不表示数据一定可正式使用。来源未披露、缺少原始单位或不满足行约束时，系统应安全地标记为“未解决”或“无效”，不能强行补值。

## 7. 日常运维

### 7.1 查看状态和日志

```bash
docker compose --env-file .env -f compose.prod.yaml ps -a
docker logs --tail 100 metric-pulse-api-1
docker logs --tail 100 metric-pulse-worker-1
docker logs --since 10m metric-pulse-worker-1
```

不要执行会展开所有容器环境变量的诊断命令，也不要把带有请求鉴权头的调试输出粘贴到工单中。

### 7.2 重启服务

完整重启：

```bash
docker compose --env-file .env -f compose.prod.yaml restart
```

只重启 API 后，Nginx 可能仍缓存旧容器地址并返回 `502`。此时同时重建或重启 Web：

```bash
docker compose --env-file .env -f compose.prod.yaml up -d --no-deps --force-recreate api web
```

不要在任务运行期间随意重启 Worker。确需维护时，应先在页面暂停或停止任务，等待运行中单元归零。

### 7.3 停止与启动整套服务

停止容器但保留数据卷：

```bash
docker compose --env-file .env -f compose.prod.yaml stop
```

重新启动：

```bash
docker compose --env-file .env -f compose.prod.yaml start
```

不要使用 `docker compose down -v`，该命令会删除数据库和对象数据卷。

## 8. 备份与恢复

### 8.1 PostgreSQL 逻辑备份

```bash
mkdir -p backups
docker compose --env-file .env -f compose.prod.yaml exec -T postgres \
  pg_dump -U metric_pulse -d metric_pulse -Fc \
  > "backups/metric-pulse-$(date +%Y%m%d-%H%M%S).dump"
```

### 8.2 对象与导出文件

Compose 使用以下命名卷：

- `metric-pulse_postgres-data`；
- `metric-pulse_redis-data`；
- `metric-pulse_object-data`；
- `metric-pulse_export-data`。

除数据库逻辑备份外，还应使用 NAS 快照或备份工具保护对象与导出卷。恢复前必须停止任务并备份当前状态。数据库恢复会覆盖现有业务数据，应先在隔离环境演练，不要直接在生产执行未经验证的恢复命令。

PostgreSQL 18 的持久化挂载点必须是 `/var/lib/postgresql`，不能沿用旧版本常见的 `/var/lib/postgresql/data`。

## 9. 版本发布、升级与回滚

### 9.1 镜像构建触发规则

GitHub Actions 自动构建只由 `v*` 标签触发：

```text
push v1.0.3 tag -> 构建并发布 API/Web 镜像
push main commit -> 不构建镜像
push 文档提交 -> 不构建镜像
```

工作流不提供绕过版本标签的手动构建入口。没有版本变化时，不会触发镜像发布；确需补发时也应先确认发布策略并创建新的正式版本。

正式发布步骤：

```bash
git status --short
git tag -a v1.0.3 -m 'Release v1.0.3'
git push origin v1.0.3
```

等待 GitHub Actions 的 API 和 Web 镜像均发布成功后再升级生产环境。

### 9.2 升级

先备份，再修改 `.env` 中的固定版本：

```dotenv
MP_IMAGE_TAG=1.0.3
```

然后执行：

```bash
docker compose --env-file .env -f compose.prod.yaml pull
docker compose --env-file .env -f compose.prod.yaml up -d --remove-orphans
docker compose --env-file .env -f compose.prod.yaml ps -a
```

确认迁移状态码、健康接口和登录正常，并进行一条受控任务冒烟测试。

### 9.3 回滚

应用回滚时把 `MP_IMAGE_TAG` 改回上一正式版本并重新拉起。数据库迁移通常只向前兼容；若新版本包含不可逆迁移，不能只回滚镜像，必须按该版本的发布说明执行数据库恢复方案。

## 10. HTTPS 与公网访问

建议仅在局域网使用，或通过群晖反向代理/VPN 暴露。启用 HTTPS 时：

1. 反向代理目标设置为 `http://127.0.0.1:56123`；
2. 配置可信证书；
3. 将 `.env` 中 `MP_SESSION_COOKIE_SECURE` 改为 `true`；
4. 重建 API 和 Web；
5. 限制来源 IP，并启用 NAS 登录、防火墙和告警策略。

不要直接把 PostgreSQL、Redis、API、OMLX 或 SearXNG 暴露到公网。

## 11. 常见故障

### Web 返回 502

可能原因：API 未健康，或 API 重建后 Nginx 仍使用旧容器地址。

```bash
docker compose --env-file .env -f compose.prod.yaml ps -a
docker logs --tail 100 metric-pulse-api-1
docker compose --env-file .env -f compose.prod.yaml up -d --no-deps --force-recreate web
```

### 首页返回 500 或循环重定向

确认使用 `1.0.3` 或更高正式版本的 Web 镜像，并检查实际镜像标签：

```bash
docker compose --env-file .env -f compose.prod.yaml images
```

### 任务长期停留在“排队中”

```bash
docker logs --tail 100 metric-pulse-worker-1
docker compose --env-file .env -f compose.prod.yaml ps -a
```

确认 Worker 已连接 Redis、显示 `concurrency: 1`，且 OMLX 健康。

### 单条任务耗时较长

来源网页的浏览器回退允许数分钟，OMLX 超时默认 900 秒。只要运行中始终为 1、日志持续有来源或模型请求、失败数没有持续增长，就不应为提高速度而增加模型并发。

### PostgreSQL 启动失败或升级后数据为空

检查数据卷挂载是否为 `/var/lib/postgresql`，以及是否误用了新的 Compose 项目名导致创建了另一组空卷。不要删除旧卷；先检查：

```bash
docker volume ls | grep metric-pulse
docker inspect metric-pulse-postgres-1
```

### NAS 本机可访问，其他设备无法访问

检查群晖防火墙是否允许 Web 端口、客户端是否与 NAS 路由互通，以及端口是否绑定到 `0.0.0.0`。

## 12. 安全检查清单

- `.env` 权限为 `600`，且不在 Git 中；
- 管理员和数据库使用不同的随机强密码；
- 已轮换曾在日志、终端或聊天中暴露的历史密钥；
- GHCR 私有镜像 Token 只有 `read:packages` 权限；
- 只暴露 Web 端口；
- HTTPS 环境启用安全 Cookie；
- OMLX 固定模型且全局并发为 1；
- 六个范围外工作表不进入生产任务；
- 金标、验收答案和历史对照表不进入生产主机、容器、对象卷或提示词；
- 定期验证数据库备份和 NAS 快照可恢复；
- 定期检查磁盘空间，避免数据库或对象卷写满。

## 13. 当前 NAS 部署基线

当前已验证的部署基线如下，密钥和密码不记录在文档中：

| 项目 | 当前值 |
| --- | --- |
| 平台版本 | `1.0.3` |
| NAS 架构 | `x86_64` |
| 部署目录 | `/volume2/docker/metric-pulse-platform` |
| Web 地址 | `http://10.0.0.7:56123` |
| OMLX | `Qwen3.8-27B-6bit`，固定并发 1 |
| 搜索服务 | NAS SearXNG `8888` 端口 |
| 视觉后台识别 | 关闭，避免争用单实例 OMLX |
| 生产数据 | PostgreSQL 18 命名卷持久化 |

部署完成后，以本节为当前环境记录；版本、端口或拓扑变化时应同步更新本文档。
