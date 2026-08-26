# metric-pulse-platform

面向 Excel 的低人力智能数据采集、审核与导出平台。

本项目是 `metric-pulse-service` 的全新重构项目，不复制旧服务的业务实现。旧项目仅作为需求、数据迁移和行为回归参考。

## 当前状态

第一版端到端系统已经实现，可在本地开发环境或 Docker Compose 中运行。正式版 API、Worker、Web、PostgreSQL 和 Redis 已通过 Docker Compose 部署到 x86_64 NAS；来源发现使用 NAS 中的 SearXNG，采集模型使用局域网 OMLX。

已实现的业务闭环：

- 用户登录、角色权限和管理审计；
- Excel 上传、哈希存储、11 类工作表的通用 OOXML 结构识别和预览；
- 本地 Qwen3.8 多模态语义识别，结构字段由程序交叉校验；
- 任务规划、启动、暂停、恢复、停止、失败重试和软删除；
- 当前 eager `TaskProcessor` 的运行版本隔离，以及 Compose 中已配置但尚未生产切换验收的 Celery/Redis/PostgreSQL worker 路径；
- 工作簿采集直链优先，并优先使用来源官方机器可读接口；通用 JSON 对象数组转为可确定性匹配的 CSV，直链失败/无数据/歧义时才降级到 SearXNG 前 10 条，并保存完整路由审计；
- HTML 主文去噪、PDF/Word/图片提取；对通用图文页最多六张正文候选图结合文章标题、图片 alt/title、图注及邻近描述识别数据表，回填正文后再完整交给后续提取与复核；对 403/429、JS 空壳和过短正文提供 Playwright Chromium 回退；
- OMLX 仅使用 `Qwen3.8-27B-6bit`、全局并发 1；每行固定串行执行多源候选综合和独立证据复核，对日期、地域、口径和单位失配失败关闭；同来源行相邻调度并复用稳定证据前缀，但不共享跨行结论；
- 规范 URL 的正文、原图和图片派生表持久化到共享 `source-cache-data`；挑战/限流建立 URL 与域级负缓存，避免重复抓取触发验证页；
- execution/resolution/review 三套正交状态、原因/风险分类与独立统计；
- 原始数据、行约束、模型建议、校验过程、证据和审核历史的异常优先核对；
- ReviewPolicy 自动通过/抽样/熔断、快照式批量 preview/commit、`CONFIRMED_UNRESOLVED` 与未解决报告；
- 严格审核门禁、导出失效管理和保留格式的 Excel 回写；
- Vue 3 + Element Plus Web 工作台，Vue Router 5 自动文件路由；
- Alembic 初始及 P0 迁移、API/worker/web Docker 镜像和 Compose 编排。

真实测试工作簿的验收结论见 [验收报告](docs/acceptance-report.md)，相同 URL、图文增强与 OMLX
前缀缓存的专项结论见 [3+3 回归报告](docs/same-url-3x3-regression-report.md)。

## 本地运行

```bash
uv sync --dev
cp .env.example .env
uv run alembic upgrade head
uv run metric-pulse-api
```

另一个终端启动前端：

```bash
cd web
pnpm install --frozen-lockfile
pnpm dev
```

默认 API 为 `http://localhost:8000`，Web 为 `http://localhost:5173`。请在 `.env` 设置管理员密码和本机 OMLX key，密钥不要提交到仓库。

## Docker Compose

```bash
cp .env.example .env
# 设置 POSTGRES_PASSWORD、MP_BOOTSTRAP_PASSWORD、MP_OMLX_API_KEY
docker compose up --build -d
```

默认 Web 端口为 `8080`。Compose 默认连接 `http://10.0.0.203:5008/v1` 的 OMLX 服务，可通过环境变量覆盖。当前 NAS 上已部署 SearXNG 供本地应用使用，但 API/worker/web Compose 仍只完成配置校验，未切换生产流量。

## Docker 镜像发布与生产部署

只有推送 `v*` 正式版本标签时，GitHub Actions 才会自动编译并发布两个 GHCR 镜像。普通 `main` 提交和文档更新不会构建镜像，也不提供绕过版本标签的手动构建入口：

- `ghcr.io/xingzichen/metric-pulse-platform-api`：供数据库迁移、API 和 worker 共用；
- `ghcr.io/xingzichen/metric-pulse-platform-web`：Vue 静态文件和 Nginx 运行时。

当前正式版本为 `1.0.4`。生产编排默认固定该版本，不依赖可变的 `latest` 标签：

```bash
cp .env.example .env
# 设置 POSTGRES_PASSWORD、MP_BOOTSTRAP_PASSWORD、MP_OMLX_API_KEY 等生产参数
docker compose -f compose.prod.yaml pull
docker compose -f compose.prod.yaml up -d --remove-orphans
```

如果 GHCR 镜像保持私有，部署机器需先使用具有 `read:packages` 权限的 GitHub Token 登录：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io --username xingzichen --password-stdin
```

升级或回滚时可以显式指定已经发布的不可变版本，例如：

```bash
MP_IMAGE_TAG=1.0.4 docker compose -f compose.prod.yaml up -d --remove-orphans
```

完整的服务器/NAS 准备、环境变量、首次启动、验收、备份、升级、回滚和故障处理步骤见 [生产部署与运维手册](docs/deployment.md)。仓库需要允许 Actions 写入 Packages；生产密钥只保存在部署环境的 `.env` 中，不得提交。

## 质量验证

```bash
uv run ruff check src tests migrations
uv run pytest -m "not acceptance and not omlx"
uv run pytest -m acceptance
cd web && pnpm run build
```

OMLX 视觉集成测试默认跳过；显式提供临时环境变量后运行 `tests/test_omlx_integration.py`。测试代码不会保存或输出 API key。

## 设计目标

- 用户上传 Excel 后，系统自动识别工作表、行约束、业务键和待采集结果组。
- 工作簿识别使用“OOXML 确定性解析 + 本地多模态模型语义识别 + 程序交叉校验”，不再依赖写死的 Sheet 名和表头位置。
- 对已有行执行受 `RowContract` 约束的行级复合采集。
- 对近空清单型工作表执行完整快照构建和业务键对账。
- 任务可以可靠地启动、暂停、恢复、停止、重试和软删除。
- 每个结果都可追溯到原始行、来源证据、处理步骤和审核记录。
- 用户主要处理异常和低置信度结果；低风险、确定性验证闭环的结果由版本化策略自动通过，高质量结果可受控批量确认。
- 只有满足可审计的自动/人工审核门禁的结果才允许生成正式导出；已确认无法安全填值的项随未解决报告导出。
- 基于原始工作簿回填最终审核值，保持工作表结构、顺序和必要格式。

## 推荐技术栈

| 层次 | 技术 |
| --- | --- |
| 后端语言 | Python 3.14 |
| API | FastAPI、Pydantic |
| ORM 与迁移 | SQLAlchemy 2、Alembic |
| 主数据库 | PostgreSQL 18 |
| 队列与缓存 | Redis、Celery |
| 文件与证据存储 | S3 兼容对象存储，支持 NAS/KS3 |
| Excel | openpyxl，必要时使用 LibreOffice headless 做公式重算和渲染校验 |
| HTTP 与模型调用 | HTTPX，OpenAI 兼容协议接入 OMLX 文本/视觉能力 |
| 前端 | Vue 3、TypeScript、Vite、Element Plus、Vue Router 5 内置文件路由、TanStack Query for Vue、Pinia |
| 测试 | pytest、Hypothesis、Testcontainers、Playwright |
| 工程质量 | uv、Ruff、Pyright、pre-commit |
| 部署 | Docker Compose，Synology 反向代理或独立网关 |

具体版本在开始编码时通过锁文件固定，不在设计文档中依赖浮动的次版本号。

## 设计文档

- [系统设计](docs/system-design.md)
- [应用设计](docs/application-design.md)
- [生产部署与运维手册](docs/deployment.md)
- [ADR-0001：总体技术与架构决策](docs/adr/0001-technology-and-architecture.md)
- [ADR-0002：Vue 前端、组件库与自动文件路由](docs/adr/0002-vue-frontend-and-file-routing.md)
- [ADR-0003：本地多模态模型识别架构](docs/adr/0003-local-multimodal-recognition.md)
- [ADR-0004：质量优先的多源网页采集与浏览器回退](docs/adr/0004-quality-first-web-acquisition.md)
- [ADR-0005：P0 状态、吞吐、审核与金标边界](docs/adr/0005-p0-state-throughput-review-and-gold-boundary.md)
- [ADR-0006：采集直链优先、来源上下文复用与单行隔离](docs/adr/0006-direct-source-first-and-row-isolation.md)
- [2026-08-24 需求与技术方案一致性回检](docs/requirements-consistency-review-2026-08-24.md)

## 需求来源

设计综合以下需求基线：

- [工作区 V2.4 需求文档](../需求文档.md)
- [工作区 V2.4 技术方案](../技术方案文档.md)
- [平台化需求文档](../metric-pulse-service/docs/excel-data-collection-platform-requirements.md)

其中：

- V2.4 文档定义完整工作簿构建、`RowContract`、快照枚举、业务键对账，并确定 `P0-3 状态语义 → P0-2 吞吐 → P0-4 审核效率` 的实施顺序。直链存在时先获取并匹配，只有失败、无数据或歧义才逐行搜索；不得跨行复用搜索结论、批处理/并发模型或省略每行的两次 Qwen 调用。
- 平台化需求定义用户、任务控制、人工审核、导出门禁和 Web 工作台。
- 两者冲突时，以“正式导出必须经过可审计的自动/人工审核门禁”、“行级结果组必须原子提交”和“金标只在平台外部验收”为当前设计决策。

内部可以在审核前生成预览工作簿，但预览产物不能作为正式导出下载或对外发布。

## 仓库结构

```text
metric-pulse-platform/
├── src/metric_pulse/         # 模块化 FastAPI 应用、领域服务和 worker
├── web/                      # Vue 3、Element Plus 和自动文件路由页面
├── migrations/               # Alembic 数据库迁移
├── tests/                    # 单元、API、真实模型和全工作簿验收测试
├── docs/                     # 需求、系统设计、应用设计、ADR 和验收报告
├── compose.yaml              # PostgreSQL、Redis、API、worker 和 Web
└── Dockerfile
```

## 架构原则

1. 模块化单体优先，运行时只拆分 API、worker、scheduler 和 web。
2. PostgreSQL 是任务和审核状态的唯一事实来源。
3. Celery 负责投递和重试，不以 Celery task 状态代表业务任务状态。
4. 处理单元为“一个 `RowContract` 的一个目标字段组”，字段组原子提交。
5. 行号仅用于当前工作簿定位，跨版本身份使用版本化业务键。
6. 来源发现、抓取、提取、标准化、校验和审核相互分离，可独立重试。
7. 系统建议值、人工最终值和导出快照分别保存。
8. 大文本和文件进入对象存储，PostgreSQL 保存索引、状态和可查询元数据。
9. 所有写操作幂等，所有状态转换带预期版本。
10. OpenAPI 是前后端契约，前端 Client 自动生成。
11. Web 页面以文件结构生成强类型路由，权限和布局由路由元数据统一声明。
12. 执行、业务解决和审核状态正交建模，不用 `SUCCEEDED` 推导数据已填齐。
13. 历史金标只供平台运行完成后的外部测试/验收比较器使用，不得进入生产运行时。
14. 每行独立完成可审计的 `DIRECT_LINK/SEARCH_FALLBACK` 获取路由；搜索仅在无直链或直链失败、无数据、歧义时发生。OMLX 只使用 `Qwen3.8-27B-6bit`、全局并发 1，每行固定两次串行独立请求，禁止模型批处理和跨行会话。
15. `preserve` 只由当次业务配置/用户授权决定，不能由金标产生或推断。

## 首个里程碑

首个里程碑只建设可靠底座：

- Python 工程骨架和质量工具；
- PostgreSQL、Redis 和对象存储适配；
- 用户登录和权限；
- 文件流式上传、哈希和工作簿分析；
- 工作表渲染、OMLX 视觉能力探测和结构/语义识别融合；
- 任务、运行实例、outbox、状态机和 worker 租约；
- 275 行快速回归文件的解析和任务规划；
- API OpenAPI 契约和最小任务列表页面。

完成底座后，再实现通用采集、审核工作台和正式导出。
