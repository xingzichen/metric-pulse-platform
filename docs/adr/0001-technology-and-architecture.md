# ADR-0001：总体技术与架构决策

## 状态

已提议，待项目启动评审后接受。

## 背景

旧系统基于 NestJS、TypeORM 和进程内后台 Promise，已经证明可以完成基础 Excel 解析和模型调用，但任务可靠性、通用字段采集、证据链、人工审核和导出门禁需要大幅重构。

新项目需要：

- 在群晖 NAS 上以较低运维成本运行；
- 支持约 1.2 万至 1.75 万活跃行的工作簿，并允许未来扩展；
- 支持耗时较长、可暂停和可恢复的采集任务；
- 更方便地处理数据、Excel、文档、网页抓取和模型调用；
- 为后续前端提供稳定的 OpenAPI 契约；
- 由少量人员维护。

## 决策

### 1. 使用 Python 重构后端

选择 Python 3.14，原因是：

- 数据解析、Excel、文档、抓取和 AI 生态完整；
- 领域模型可以使用严格类型和 Pydantic Schema；
- 与 OMLX 的 OpenAI 兼容接口集成简单；
- 测试和离线数据回归工具成熟。

项目代码不得使用“动态字典到处传递”的方式代替领域类型。核心命令、状态、事件和外部接口全部使用显式类型。

### 2. 使用 FastAPI 作为 API 框架

FastAPI 基于 Python 类型、Pydantic、OpenAPI 和 JSON Schema，适合生成前端 Client，并原生支持异步接口、SSE、认证依赖和文件流式响应。

FastAPI 的进程内 BackgroundTasks 只用于短小、非关键的响应后操作。正式采集、解析、导出和恢复任务必须进入持久 worker。

### 3. 使用 SQLAlchemy 2 和 Alembic

- SQLAlchemy 2 负责 ORM、显式事务和异步 API 查询。
- Alembic 负责版本化数据库迁移。
- API 使用 `AsyncSession`，每个请求和每个用例独占 session。
- Celery worker 初期使用同步 SQLAlchemy session，降低事件循环和 fork worker 组合复杂度。
- 领域层不直接依赖 ORM 实体，通过 repository 端口隔离。

### 4. 使用 PostgreSQL 作为主数据库

选择 PostgreSQL，而不是继续使用 MySQL，原因包括：

- JSONB、部分索引、表达式索引和丰富约束适合模板、原始行和证据元数据；
- `FOR UPDATE SKIP LOCKED` 可支持租约恢复和调度扫描；
- 原生枚举以外还可使用 CHECK 约束，便于状态演进；
- advisory lock、窗口函数和物化视图便于运营统计。

状态值优先使用 `varchar + CHECK` 或领域约束，不使用难以演进的数据库 enum。

### 5. 使用 Redis 和 Celery，但数据库保存业务真相

Celery 负责：

- 任务消息投递；
- 并发 worker；
- 延迟重试；
- 队列路由；
- 计划性任务入口。

Celery 不负责：

- 定义业务任务最终状态；
- 判断暂停、停止和审核完成；
- 保存最终结果；
- 作为唯一恢复依据。

每个 Celery 消息只包含资源 ID、运行版本和幂等键。worker 必须先从 PostgreSQL 原子领取处理单元，再执行外部调用。提交结果前必须验证当前 `task_run.run_version` 和控制状态。

不依赖 `revoke(terminate=True)` 实现停止。停止采用合作式控制：停止新领取、取消有超时和 signal 的请求、拒绝过期运行版本写入。

### 6. 采用模块化单体和六边形边界

代码按领域模块拆分，但不在首版拆成独立微服务。

运行时拆成：

- API；
- worker；
- scheduler/outbox dispatcher；
- web；
- PostgreSQL；
- Redis；
- S3 兼容对象存储。

API 和 worker 共享领域与应用代码，但使用不同入口和依赖组合。

### 7. 使用 S3 兼容对象存储

以下内容不直接放入 PostgreSQL 大字段：

- 原始 Excel；
- 工作簿快照；
- 抓取原文；
- PDF、Word 和 Excel 来源文件；
- 模型受控原始响应；
- 预览文件和正式导出；
- 大型 JSONL/Parquet 调试产物。

对象使用内容哈希寻址和引用计数。实现 `ObjectStore` 端口，支持 NAS 上的 S3 兼容服务或现有 KS3。

### 8. 处理单元采用“行契约 + 目标字段组”

不采用“每个空单元格一个互不关联的任务”，也不采用“整个工作表一个不可恢复的大任务”。

一个 `collection_unit` 对应：

```text
RowContract + target_group + task_run
```

目标字段组中的字段必须共享一致证据链，并在同一个事务中提交。审核界面仍允许逐字段查看和修正。

### 9. 采用 OpenAPI 驱动前端

- FastAPI 生成 OpenAPI。
- CI 检查 OpenAPI 变更。
- 前端通过 OpenAPI 生成 TypeScript Client。
- 前端不得自行维护一份重复的 API 类型定义。

### 10. 前端使用 Vue 3 和 TypeScript

选择 Vue 3、Vite、Element Plus 和带内置文件路由的 Vue Router 5：

- 组件统一使用 Composition API、`<script setup>` 和 TypeScript；
- Element Plus 承担表单、普通表格、上传、对话框、抽屉和反馈组件；
- Vue Router 5 的构建插件从 `src/pages` 生成强类型路由，运行时负责导航、守卫和路由元数据；
- 首版只使用稳定的文件路由和类型路由，不使用实验性 Data Loaders；
- 服务端状态由 TanStack Query for Vue 管理；
- Pinia 只保存会话、个人偏好和跨页 UI 状态，不复制任务数据；
- 管理列表使用 Element Plus 标准表格与服务端分页；审核队列使用 TanStack Virtual，不把处于 beta 的 Table V2 作为核心依赖。

详细决策和页面文件约定见 ADR-0002。

## 被否决或推迟的方案

### 继续修改旧 NestJS 服务

否决。现有固定数值结果模型、状态机和处理链路会使重构持续受旧结构限制。

### 一开始拆成微服务

否决。团队规模和 NAS 部署不需要微服务带来的独立发布能力，反而会增加协议、部署和排障成本。

### 使用 FastAPI BackgroundTasks 执行采集

否决。它不提供持久化、可靠恢复、租约和跨进程控制。

### 只使用 Celery task 状态

否决。Celery 状态不能表达领域审核、导出门禁、行契约版本和准确的合作式暂停/停止。

### 使用 Temporal

暂缓。Temporal 很适合耐久工作流和 signal，但首版需要增加 Temporal Server、持久化、部署和运维知识。当前以 PostgreSQL 状态机、outbox 和 Celery 达到所需可靠性；当跨天工作流、补偿链和多服务编排明显增加时再评估。

### 使用 SQLite

否决。并发 worker、行锁、租约、JSON 查询和长期运行任务不适合以 SQLite 作为生产主库。

### 把所有抓取正文存进 PostgreSQL

否决。会放大备份、查询和表膨胀成本。正文放对象存储，数据库保存摘要、定位和哈希。

## 后果

### 正面

- Python 更适合数据和 AI 处理。
- OpenAPI 降低前后端协作成本。
- 数据库状态机可以精确实现任务控制和恢复。
- 模块化单体便于少量人员开发和部署。
- 行级字段组兼顾一致性、并发和审核体验。

### 代价

- 需要维护 PostgreSQL、Redis 和对象存储三个基础组件。
- Celery 消息与数据库状态之间需要 outbox 和恢复扫描。
- Python API 的 async session 与 worker 的 sync session 需要明确隔离。
- 从 MySQL 迁移到 PostgreSQL 需要一次性迁移和对账工具。
- openpyxl 对部分高级 Excel 特性的保真度有限，必须用真实工作簿回归测试约束。

## 验证方式

在接受本 ADR 前，完成以下技术刺探：

1. Python 3.14 环境下 FastAPI、SQLAlchemy、Celery、openpyxl 和主要解析库安装运行。
2. PostgreSQL + Redis + Celery 在目标群晖 Docker 环境稳定运行。
3. 275 行文件完成解析、RowContract 规划和数据库写入。
4. 约 1.75 万行工作簿完成解析和重建性能测试。
5. worker 处理中强制重启，租约扫描能够恢复且不重复提交。
6. 暂停和停止后，过期运行版本无法写入结果。
7. 原工作簿的工作表、样式、公式、数据验证和冻结窗格保真度满足验收。
8. 本地 OMLX 图像输入合约、工作簿识别融合和静默错误门禁通过 ADR-0003 刺探。

## 参考资料

- Python 文档：https://docs.python.org/3/
- FastAPI 特性：https://fastapi.tiangolo.com/features/
- SQLAlchemy asyncio：https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Alembic：https://alembic.sqlalchemy.org/en/latest/
- Celery 任务：https://docs.celeryq.dev/en/stable/userguide/tasks.html
- Celery worker：https://docs.celeryq.dev/en/stable/userguide/workers.html
- PostgreSQL SELECT/锁：https://www.postgresql.org/docs/current/sql-select.html
- Vue：https://vuejs.org/guide/introduction.html
- Element Plus 组件：https://element-plus.org/en-US/component/overview.html
- TanStack Query for Vue：https://tanstack.com/query/latest/docs/framework/vue/overview
- Vue Router 5 文件路由：https://router.vuejs.org/file-based-routing/
