# metric-pulse-platform 系统设计

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.6 |
| 状态 | 当前实现同步稿 + 已批准 P0 改造目标 |
| 更新日期 | 2026-08-24 |
| 目标项目 | `metric-pulse-platform` |
| 架构风格 | Python 模块化单体、API/worker 运行时分离、事件驱动协作 |

## 2. 设计结论

新系统采用 Python 全量重构，不复制旧 NestJS 业务实现。旧系统只承担三项作用：

1. 提供需求和历史行为参考；
2. 提供旧数据库迁移来源；
3. 提供回归测试样本。

新系统同时解决两类问题：

- 平台问题：上传、任务控制、状态查询、人工核对、权限、审计和导出门禁。
- 采集问题：`RowContract` 约束驱动采集、清单快照枚举、业务键对账、来源证据、复合结果组和完整工作簿构建。

正式导出必须经过审核门禁。门禁可由版本化自动审核策略或人工决策满足；只有低风险、确定性验证闭环的结果才允许自动通过。系统可以在审核前生成内部预览产物，但预览不是正式导出。

历史金标只是项目外部的测试/验收比较基准。平台生产包、API、worker、模型、运行配置和数据库不得读取、引用或推断金标；平台完成采集后，独立验收工具才可做业务键和字段语义比较。

### 2.1 当前实现与目标架构边界

| 维度 | 2026-08-24 当前本地运行 | 目标生产架构 |
| --- | --- | --- |
| 数据库 | SQLite 单文件 | PostgreSQL 18 |
| 任务执行 | FastAPI `BackgroundTasks` + eager `TaskProcessor` | Redis/Celery worker |
| 搜索 | NAS SearXNG | 同一 SearXNG 接口，可配置高可用 |
| 网页抓取 | HTTPX + Playwright Chromium 回退 | 同一代码运行于 source worker |
| 模型 | 局域网 OMLX，`Qwen3.8-27B-6bit`，全局并发 1 | 仍只使用该模型与全局串行队列，不扩展并发/批处理 |
| 对象存储 | 本地文件系统 | S3 兼容对象存储 |
| 运维状态 | 本地 API/Web 常驻，定时监视 | NAS 容器化、健康检查和备份 |

后文如未特别标注，PostgreSQL、Redis/Celery、outbox、lease 和多 worker 表示目标架构；SQLite eager 路径是当前已验证的本地运行形态。

## 3. 规模与质量假设

### 3.1 当前基线

- 目标工作簿包含 11 个业务工作表。
- 快速回归样本为 275 行。
- 完整输入基线约 11,996 条活跃行。
- 历史外部验收金标约 17,490 条活跃行，不属于平台运行输入。
- 单个工作表既可能有大量已有行，也可能只有表头或一条样例行。
- 同一任务可能同时包含逐行采集、完整快照构建、实体刷新、保留和结构化导入。

### 3.2 容量设计目标

首版单任务目标：

- 不超过 20 个参与处理的工作表；
- 不超过 50,000 条业务记录；
- 不超过 500,000 个普通字段；
- 不超过 100,000 个目标字段；
- 任务运行时间允许从数分钟到数天；
- 来源调用并发可按域名限流；OMLX 不是可调并发，固定全局为 1。

数据库和对象存储按任务历史长期保留设计，不把完整正文塞入高频查询表。

## 4. 架构原则

1. PostgreSQL 是业务状态和审核事实的唯一来源。
2. Redis/Celery 只负责投递、限流和重试，不定义业务完成状态。
3. API 请求不执行长任务。
4. 处理单元是 `RowContract + target_group + task_run`。
5. 同一目标组字段共享证据链并原子提交。
6. 行号是坐标，不是业务身份。
7. 模板、提示词、规则、业务键和审核结果全部版本化。
8. 来源发现、内容抓取、数据提取、标准化、校验和审核分层。
9. 所有写操作幂等，所有状态转换检查预期版本。
10. 大文件、大正文和生成产物进入对象存储。
11. 异常优先审核，高质量结果支持版本化策略自动通过或受控批量确认。
12. 系统建议、人工事实和导出快照分别保存。
13. 执行、业务解决和审核状态正交建模，不以执行成功推导字段已解决。
14. 每个 unit 必须独立完成可审计的来源获取路由：采集直链优先，只有无直链或直链失败、无目标数据、匹配歧义时才逐行搜索降级；不变 SourceSnapshot 可跨 unit 复用，行证据定位、校验与提交仍隔离。
15. 模型请求不批处理、不并发；每行使用同一 Qwen3.8 固定串行执行候选综合和独立证据复核。
16. 已批准的工作表专用 Profile 优先于通用识别。`ai_algorithm_collectio` 每次按固定 GitHub
    查询构建一个十行月度增量快照，程序确定排序/单位/元数据，模型只逐名次核验来源事实。
17. `top_list_ai` 每次按福布斯固定官方页面构建当前年度 50 行增量快照；名单规模、官方发布
    时间、融资单位和批次状态由程序控制，每家公司独立双模型核验，禁止搜索来源拼接。
18. 年度/月度增量 Profile 使用“任务冻结时间 + 固定来源”的不可变快照；同一快照可复用原文
    和结构化索引，但不得跨行复用模型会话、候选或结论。

## 5. 系统上下文

```mermaid
flowchart LR
    User[操作员 / 审核员 / 管理员]
    Web[Web 应用]
    API[FastAPI]
    Worker[采集 Worker]
    DB[(PostgreSQL)]
    Redis[(Redis / Celery)]
    Obj[(S3 兼容对象存储)]
    OMLX[本地 OMLX]
    Sources[搜索服务 / 官方网站 / 文档来源]

    User --> Web
    Web --> API
    API --> DB
    API --> Obj
    API --> Redis
    Redis --> Worker
    Worker --> DB
    Worker --> Obj
    Worker --> OMLX
    Worker --> Sources
```

### 5.1 当前本地运行拓扑

```mermaid
flowchart LR
    Browser[用户浏览器] --> Web[Vite/Vue :5173]
    Web --> API[FastAPI :8000]
    API --> SQLite[(metric-pulse-codex-run.db)]
    API --> Processor[TaskProcessor eager]
    Processor --> Search[NAS SearXNG\n10.0.0.7:8888]
    Processor --> Public[公开网页/附件]
    Processor --> Chromium[Playwright Chromium]
    Processor --> OMLX[本地 OMLX\nQwen3.8-27B-6bit]
```

图中 API 进程与当前采集处理共生命周期，因此修改采集代码时必须先让任务进入 `PAUSED`，再重启 API。切换 Celery 后该限制转为“重启对应 worker”。

## 6. 运行时组件

### 6.1 Web

- Vue 3 + TypeScript 单页应用，使用 Composition API 和 `<script setup>`。
- Element Plus 提供管理端基础组件；长审核队列使用独立虚拟化能力。
- Vue Router 5 内置文件路由根据 `src/pages` 生成强类型路由，路由元数据统一承载布局和权限要求。
- 通过生成的 TypeScript Client 调用 `/api/v1`。
- 通过 SSE 接收任务事件。
- 不保存业务真相，不自行推断状态转换。

### 6.2 API

- 认证、授权、请求校验和 OpenAPI。
- 文件上传、查询、命令受理和下载授权。
- 创建领域命令并在同一事务写入 outbox。
- 只执行短事务，不做 Excel 全量解析、网络抓取或模型推理。

### 6.3 Worker

按队列隔离不同资源类型：

- `workbook`：工作簿分析、预览、重建和导出；
- `planner`：任务规划、RowContract 和 collection unit 生成；
- `source`：来源发现、抓取和内容解析；
- `collection`：候选提取、标准化和验证；
- `maintenance`：租约恢复、缓存清理和统计校准。

不同队列使用不同并发和资源限制，避免 PDF 解析阻塞 OMLX 采集。

### 6.4 Scheduler / Outbox Dispatcher

- 持续发布尚未投递的 outbox 消息。
- 扫描过期 lease 并重新调度。
- 校准任务统计。
- 检测失联 worker 和卡住的运行实例。
- 触发数据保留和对象垃圾回收任务。

### 6.5 PostgreSQL

- 领域状态、用户、权限、模板和审计。
- 任务、运行实例、RowContract、记录、字段和候选索引。
- 来源元数据、审核事实和导出快照。
- outbox、幂等键、lease 和持久事件。

### 6.6 Redis

- Celery broker；
- 短期缓存；
- 分布式限流计数；
- SSE 唤醒通知；
- 用户会话。

Redis 数据丢失不应造成业务结果丢失。系统能够由 PostgreSQL outbox 和 lease 恢复投递。

### 6.7 对象存储

保存：

- 原始工作簿；
- 工作簿解析快照；
- 来源文件和抓取正文；
- 受控模型响应；
- JSONL/Parquet 调试产物；
- 内部预览工作簿；
- 正式导出。

对象键不使用用户原始文件名作为唯一键，使用租户、内容哈希和随机 ID 组合。

## 7. 领域模块

### 7.1 Identity

- 用户、角色和权限；
- 登录会话；
- API token 扩展；
- 操作人上下文。

### 7.2 File Intake

- 流式上传；
- 文件签名和安全检查；
- 内容哈希和重复识别；
- 原文件对象保存；
- 工作簿分析任务。

### 7.3 Workbook

- OOXML 工作表、单元格、合并区域、公式、数据验证和样式的确定性解析；
- 工作表全局缩略图和带坐标分片渲染；
- 本地多模态模型对表头、数据区域、字段角色和业务语义生成识别候选；
- 结构规则、视觉候选和已发布模板三方融合与冲突检测；
- 原始行快照；
- 业务键计算；
- RowContract 候选配置；
- 工作簿构建；
- 公式、样式、数据验证和冻结窗格回归检查。

### 7.4 Template

- 数据集 profile；
- 工作表匹配；
- 描述字段；
- 目标字段组；
- 字段类型和空值语义；
- 业务键版本；
- 来源策略；
- 采集模式；
- 验证和导出规则。

模板版本发布后不可修改，只能创建新版本。

### 7.5 Task Orchestration

- 用户任务；
- task run；
- dataset run；
- collection unit；
- 控制状态；
- 进度统计；
- lease、心跳和恢复；
- 领域事件。

### 7.6 Collection

- RowContract 构建；
- 搜索词和提示词渲染；
- 来源发现；
- 内容抓取和解析；
- 候选值提取；
- 确定性标准化；
- 复合结果校验；
- 评分和建议选择。
- SourceAcquisitionAttempt 逐行路由、按需 RowSearchAttempt、SourceSnapshot 内容去重和 UnitSourceLink 行级定位；
- 固定双阶段 Qwen3.8 调用、全局串行队列和阶段审计。

### 7.7 Dataset Snapshot

- 完整快照或增量来源读取；
- 清单记录枚举；
- 业务键对账；
- `INSERT/UPDATE/KEEP/RETIRE/CONFLICT`；
- 为枚举记录生成 RowContract。

### 7.8 Evidence

- 来源对象和内容哈希；
- 文本片段和定位；
- 来源权威级别；
- 候选与证据关系；
- 抓取时间和有效期。

### 7.9 Review

- 待审核队列；
- 自动通过、批准、修正、确认未解决、驳回和可选项跳过；
- 字段覆盖值；
- 版本冲突；
- ReviewPolicy、自动审核抽样和风险门槛；
- 基于冻结筛选快照的批量审核；
- 审核完成计算。

### 7.10 Export

- readiness；
- 审核快照；
- 内部预览与正式导出隔离；
- 原工作簿回填；
- 文件哈希和历史导出；
- 过期导出标记。

## 8. 统一采集模型

### 8.1 两类上游入口

```mermaid
flowchart TD
    Input[输入工作簿]
    Mode{数据集模式}
    Existing[已有业务行]
    Enumerate[快照枚举 / 结构化导入]
    Contract[构建不可变 RowContract]
    Unit[创建 Collection Unit]
    Collect[来源发现 / 抓取 / 提取 / 校验]
    Review[人工审核]
    Export[正式导出]

    Input --> Mode
    Mode -->|row_contract_collect / refresh| Existing
    Mode -->|snapshot_build / reconcile| Enumerate
    Mode -->|preserve| Review
    Existing --> Contract
    Enumerate --> Contract
    Contract --> Unit
    Unit --> Collect
    Collect --> Review
    Review --> Export
```

`preserve` 分支只能由当次任务使用的已发布业务模板或用户授权显式选择。生产平台不能根据历史金标是否变化选择 `preserve`，运行时也不存在可供判断的金标。

### 8.2 RowContract

RowContract 是不可变值对象，冻结：

- 数据集和工作表；
- 输入文件版本；
- 当前行原始坐标；
- 当前行业务键；
- 描述字段的英文名、中文含义、原始值、规范值、必需性和空值语义；
- 目标字段组；
- 模板版本、提示词版本和规则版本；
- contract hash。

任务运行期间如果输入文件或模板版本改变，旧 contract 不能继续复用。

### 8.3 Target Group

目标字段组是采集和提交的原子边界。例如：

```text
metric_value_with_source =
  observed(be_data + be_unit) + derived(data) + standard(unit) + provenance(source_url)
```

不同工作表可以定义自己的组，例如备案信息、榜单名次、人员信息或产品参数。

组内字段可以由多个证据产生，但必须保存明确的证据和计算依赖，不允许无依据拼接。

`ai_index` 不使用通用空值率决定字段角色。`index_name` 是主体，所有非空的 `level、region、
province、city、district、other_region、statistical_date、scope、industry` 组成联合约束。
`be_data/be_unit` 必须来自同一事实来源；`unit` 是输入的标准目标；`data` 是版本化转换规则的
派生结果。输入中已有 `be_unit` 只作提示，不代表当次已核验来源单位。

该稳定 profile 同时锁定上传后的视觉识别覆盖权限：视觉模型可以记录结构建议和冲突，但不
得修改 ai_index 的描述字段、目标字段、业务键或采集模式。

### 8.4 Collection Unit

`collection_unit.execution_status` 只表示 worker 执行状态：

```text
PENDING → LEASED → RUNNING → SUCCEEDED
                     ├──────→ FAILED_RETRYABLE → PENDING
                     ├──────→ FAILED_FINAL
                     ├──────→ PAUSED
                     └──────→ DISCARDED
```

`DISCARDED` 表示运行版本已过期、任务已停止或输入 contract 已失效，worker 结果不得成为当前建议值。

业务结果另存 `resolution_status`：

```text
NOT_EVALUATED → RESOLVED
              → PARTIAL
              → UNRESOLVED
              → CONFLICT
              → INVALID
```

解决状态由目标组必需字段、证据充分性、契约匹配和确定性校验器计算；模型不直接决定。`SUCCEEDED` 且业务值为空的 unit 只能是 `UNRESOLVED/PARTIAL/CONFLICT/INVALID`，不得计入已解决覆盖率。

### 8.5 处理步骤

一个 unit 内部按步骤执行，每步独立记录 attempt：

1. `PLAN_QUERY`：生成确定性查询和来源策略；
2. `DISCOVER_SOURCE`：发现候选来源；
3. `FETCH_SOURCE`：下载和缓存内容；
4. `EXTRACT_CONTENT`：PDF、HTML、Word、Excel 转换；
5. `RECOGNIZE_MEDIA`：对需要的图片或文档渲染页执行本地多模态识别；
6. `EXTRACT_CANDIDATE`：从单一来源提取结构化候选；
7. `NORMALIZE`：日期、数值、单位、实体和枚举标准化；
8. `VALIDATE`：RowContract、类型、范围和跨字段校验；
9. `RANK`：基于来源、匹配度和多来源一致性评分；
10. `COMMIT_SUGGESTION`：原子提交当前建议结果。

失败时从最小必要步骤重试，不重复下载和解析已有有效内容。

## 9. 状态模型

### 9.1 任务执行状态

```text
DRAFT → QUEUED → RUNNING → SUCCEEDED
            │        ├────→ SUCCEEDED_WITH_ERRORS
            │        ├────→ FAILED
            │        ├────→ PAUSING → PAUSED → QUEUED
            │        └────→ STOPPING → STOPPED
            └─────────────→ STOPPED
```

约束：

- `STOPPED` 是终态，重新运行必须创建新 `task_run`。
- `PAUSED` 恢复时沿用当前 run，不重置已完成结果。
- 任务状态不代表审核状态。
- 任务状态也不代表业务解决状态。
- `SUCCEEDED_WITH_ERRORS` 允许人工补充或针对失败 unit 创建新 run。

### 9.2 业务解决状态

`resolution_status` 使用 8.4 的枚举。任务聚合同时输出 `execution_counts`、`resolution_counts`和 `review_counts`，不用一个“完成数”混合三种口径。

### 9.3 审核状态

```text
NOT_READY → PENDING → IN_PROGRESS → COMPLETED
```

目标字段组审核状态：

```text
UNREVIEWED → AUTO_APPROVED
           → APPROVED
           → CORRECTED
           → CONFIRMED_UNRESOLVED
           → REJECTED → 新的 collection unit
           → SKIPPED
```

`AUTO_APPROVED` 必须保存 ReviewPolicy 版本、规则输入、命中理由和抽样结果。`CONFIRMED_UNRESOLVED` 表示人工确认当前无可靠值，导出可保留空值但必须在未解决报告中列出。`FAILED_FINAL` 是已执行异常，必须进入人工队列；人工可完整补录为 `CORRECTED + RESOLVED`，或确认无法解决，也可驳回重采，但不得直接批准且不得覆盖执行失败事实。`SKIPPED` 只能用于配置明确允许不纳入本次导出范围的可选项，不得规避必需结果。

“已核对”是完成态指标，只统计 `AUTO_APPROVED/APPROVED/CORRECTED/CONFIRMED_UNRESOLVED/SKIPPED`。`REJECTED` 是触发重采的过程决定，不计入已核对；驳回历史保存在不可变审核决定中，重采开始时当前审核状态回到 `UNREVIEWED`，等待新结果再次审核。

### 9.4 导出状态

```text
BLOCKED → READY → GENERATING → AVAILABLE
                       └────→ FAILED
AVAILABLE → STALE
```

## 10. 可靠任务控制

### 10.1 幂等命令

每个写操作接收 `Idempotency-Key`。数据库保存：

- 用户；
- 接口和资源；
- request hash；
- response snapshot；
- 过期时间。

相同 key 和相同 request 返回原结果；相同 key 和不同 request 返回冲突。

### 10.2 原子状态转换

使用带预期状态和版本的更新：

```sql
UPDATE tasks
SET execution_status = :next_status,
    version = version + 1
WHERE id = :task_id
  AND version = :expected_version
  AND execution_status = ANY(:allowed_statuses);
```

更新行数为 0 时返回状态冲突，不做最后写入覆盖。

### 10.3 Outbox

命令事务同时写入业务表和 `outbox_events`。dispatcher 在事务外发布 Celery 消息，并标记投递时间。

消息至少包含：

- event ID；
- aggregate type 和 ID；
- task run ID；
- run version；
- event type；
- payload schema version。

消费者以 event ID 去重。

### 10.4 Lease

worker 领取 unit 时写入：

- `leased_by`；
- `leased_until`；
- `heartbeat_at`；
- `attempt_no`。

worker 周期续租。scheduler 只恢复超过宽限期且 worker 心跳失联的 lease。

### 10.5 暂停

- `RUNNING → PAUSING` 后停止产生和领取新 unit。
- 在途 unit 完成安全提交或在超时后释放。
- 当活动 lease 为 0 时进入 `PAUSED`。
- 恢复后重新投递待处理和可重试 unit。

### 10.6 停止

- `RUNNING/PAUSED/QUEUED → STOPPING`。
- 不再领取新 unit。
- 尝试取消 HTTPX 请求和支持 signal 的模型调用。
- 提交前校验 run version；停止后的迟到结果标记为 `DISCARDED`。
- 活动 lease 收敛后进入 `STOPPED`。

## 11. 数据设计

### 11.1 Schema 分区

建议使用逻辑 schema：

- `identity`：用户、角色和会话；
- `catalog`：文件、模板和来源配置；
- `work`：任务、运行、记录和采集；
- `review`：审核和最终值；
- `delivery`：导出；
- `ops`：outbox、幂等、审计和系统事件。

首版也可使用一个 schema 加统一前缀，重点是模块边界而不是物理 schema 数量。

### 11.2 核心实体

| 实体 | 关键字段 |
| --- | --- |
| `users` | id、username、password_hash、status、created_at |
| `roles`、`user_roles` | role、permission |
| `files` | id、owner_id、original_name、content_hash、object_key、size、status |
| `file_versions` | file_id、version、object_key、hash、created_at |
| `workbook_sheets` | file_version_id、sheet_key、name、position、profile_match、stats |
| `recognition_attempts` | subject type/id、input object/hash、model/prompt/schema version、proposal、validation、status、timing |
| `template_versions` | template_id、version、status、schema_json、content_hash |
| `tasks` | file_version_id、template_version_id、execution/review/export status、version |
| `task_runs` | task_id、run_no、run_version、status、started/ended/heartbeat |
| `dataset_runs` | task_run_id、dataset_id、mode、snapshot completeness、status |
| `records` | dataset_run_id、business_key、source row、raw_data、fingerprint |
| `row_contracts` | record_id、target_group、contract_json、contract_hash、versions |
| `collection_units` | row_contract_id、task_run_id、execution_status、resolution_status、reason_code、lease、retry、current_candidate_set |
| `row_search_attempts` | unit_id、query、source_policy_version、started/finished_at、ordered results、status |
| `source_snapshots` | normalized_url、etag/last_modified、content_hash、raw/text object key、parse status |
| `unit_source_links` | unit_id、search/source snapshot id、locator、contract match score、purpose |
| `collection_attempts` | unit_id、step、status、input/output refs、model、timing、error |
| `evidence_documents` | source URL、object key、content hash、metadata、retention |
| `evidence_fragments` | document_id、locator、text、fragment hash |
| `candidate_sets` | unit_id、algorithm version、selected candidate、score summary |
| `candidates` | candidate_set_id、values JSONB、validation、score、reason |
| `candidate_evidence` | candidate_id、fragment_id、field path、relation |
| `review_policies` | version、risk predicates、allowed decisions、sampling rule、published_at |
| `review_decisions` | unit_id、version、decision、policy_version/actor、field overrides、comment |
| `final_values` | unit_id、review decision、values JSONB、version、effective_at |
| `export_jobs` | task_id、review snapshot version、status、object key、hash |
| `task_events` | task_id、sequence、type、payload、created_at |
| `outbox_events` | aggregate、type、payload、published_at |
| `idempotency_records` | actor、scope、key、request hash、response |
| `audit_logs` | actor、action、resource、before/after、request_id |

### 11.3 关系图

```mermaid
erDiagram
    FILES ||--o{ FILE_VERSIONS : has
    FILE_VERSIONS ||--o{ WORKBOOK_SHEETS : contains
    WORKBOOK_SHEETS ||--o{ RECOGNITION_ATTEMPTS : analyzes
    FILE_VERSIONS ||--o{ TASKS : creates
    TEMPLATE_VERSIONS ||--o{ TASKS : configures
    TASKS ||--o{ TASK_RUNS : executes
    TASK_RUNS ||--o{ DATASET_RUNS : contains
    DATASET_RUNS ||--o{ RECORDS : produces
    RECORDS ||--o{ ROW_CONTRACTS : constrains
    ROW_CONTRACTS ||--o{ COLLECTION_UNITS : schedules
    COLLECTION_UNITS ||--o{ COLLECTION_ATTEMPTS : attempts
    COLLECTION_UNITS ||--o{ CANDIDATE_SETS : proposes
    CANDIDATE_SETS ||--o{ CANDIDATES : contains
    CANDIDATES }o--o{ EVIDENCE_FRAGMENTS : supports
    COLLECTION_UNITS ||--o{ REVIEW_DECISIONS : reviews
    REVIEW_DECISIONS ||--|| FINAL_VALUES : produces
    TASKS ||--o{ EXPORT_JOBS : exports
```

### 11.4 JSONB 使用边界

适合 JSONB：

- 模板配置；
- 原始行；
- RowContract；
- 候选复合值；
- 校验明细；
- 事件 payload。

必须普通列：

- 所有 ID 和外键；
- 状态；
- 版本；
- 业务键 hash；
- 用户和时间；
- lease、重试和统计；
- 需要频繁过滤和排序的字段。

### 11.5 索引

至少包括：

- task 状态 + created_at；
- task owner + status；
- task_run task_id + run_no unique；
- collection_unit task_run + status + leased_until；
- record dataset_run + business_key unique；
- row_contract contract_hash unique within file/template version；
- review unit + version unique；
- task_event task + sequence unique；
- outbox unpublished partial index；
- evidence content_hash；
- file content_hash + owner。

## 12. 搜索、抓取与模型设计

### 12.1 Source Adapter

统一接口：

```python
class SourceAdapter(Protocol):
    async def discover(self, request: DiscoveryRequest) -> list[SourceCandidate]: ...
    async def fetch(self, candidate: SourceCandidate) -> EvidenceDocument: ...
```

适配器分类：

- 官方结构化 API；
- 官方列表/附件；
- 搜索引擎；
- 通用网页；
- 用户上传文档；
- 内部结构化文件。

#### 12.1.1 直链优先与 SearXNG 降级契约

- `build_search_query()` 从当前行的指标/实体、地域、日期、行业和单位确定性构造查询；
- 工作簿内已有采集直链时，先执行 URL 规范化、SSRF 校验、获取、解析和 RowContract 唯一匹配；唯一匹配且包含目标字段时直接形成证据，不调用搜索；
- 无直链，或直链获取失败、目标缺失、匹配歧义时，记录原因并调用 SearXNG JSON 接口，语言 `zh-CN`，safesearch=0，限制前 10 条有效公网 URL；
- 搜索全局串行，两次搜索开始至少间隔 60 秒，最多 3 次并以 60/120 秒退避；
- 搜索摘要只是低置信线索，网页正文、文档内容或渲染图像才是主证据。

每行必须保存 `SourceAcquisitionAttempt`。`RowSearchAttempt` 只在实际搜索降级时存在。GitHub `blob` 等已知展示 URL 应规范化为可下载资源，同时保存输入、规范化和最终 URL。

### 12.2 抓取安全

- 只允许 `http/https`；
- DNS 解析后阻止环回、链路本地、私网和云元数据地址；
- 每次重定向重新验证；
- 限制响应大小、时间和重定向次数；
- 内容类型白名单；
- 禁止将 OMLX 内网地址通过用户输入传入通用抓取器；
- OMLX endpoint 只能来自管理员配置。

#### 12.2.1 HTTP 与浏览器回退决策

```mermaid
flowchart TD
    Candidate[候选 URL] --> Validate[公网 DNS/SSRF 验证]
    Validate --> HTTP[HTTPX GET，25s，最多 20MB]
    HTTP --> Kind{内容类型}
    Kind -->|PDF/DOCX/DOC/Image/Text| Native[确定性提取]
    Kind -->|HTML| Main[主文去噪]
    HTTP -->|403/406/408/409/425/429/5xx\n或传输失败| Fallback[Chromium 回退]
    Main --> Thin{主文 < 500\n或挑战特征?}
    Thin -->|是| Fallback
    Thin -->|否| Evidence[文本 + 图片证据]
    Fallback --> Route[顶层导航逐次 SSRF 校验]
    Route --> Render[DOM load + 最多 15s networkidle + 5s 稳定]
    Render --> Challenge{验证码/挑战/登录受限?}
    Challenge -->|是| Reject[清空正文/图像，保留原 URL 和错误]
    Challenge -->|否| Evidence
```

回退列表排除 PDF、Office、图片、表格文件和压缩包后缀，这些内容继续使用确定性下载/解析。浏览器不解决验证码、不注入隐身脚本、不保留登录会话，只用于正常公开网页的 JavaScript 渲染。

### 12.3 OMLX Gateway

应用内部只依赖 `ModelGateway`：

```python
class ModelGateway(Protocol):
    async def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult: ...

    async def analyze_media(
        self,
        request: MediaAnalysisRequest,
    ) -> MediaAnalysisResult: ...
```

请求包含：

- 固定 model ID `Qwen3.8-27B-6bit`（可在启动时验证实际 ID，但不做运行时选型或降级）；
- system prompt version；
- user prompt；
- JSON Schema；
- timeout；
- trace metadata。

当前 OMLX 报告 `max_model_len=131072`。`max_tokens` 是输出上限而不是输入配额，实际约束是
文本 token、图片 token 与最大输出预算之和不能超过 128K。应用把送入逐行模型的编号来源限制为
30,000 字符，并按阶段配置输出预算：工作表分析 2,048、`SYNTHESIZE` 4,096、`VERIFY`
4,096、图片表格 8,192。只有视觉表格返回 `finish_reason=length` 或出现典型截断 JSON 时，才以
16,384 再试一次；每次尝试均保留预算、结束原因和 token 用量。全局默认 4,096 只是未显式分阶段
调用的兜底值。

`MediaAnalysisRequest` 额外包含受控图片对象、MIME、像素尺寸、分片坐标和图像哈希。启动时对固定模型执行能力探测，验证实际 ID、图像输入、结构化输出、超时和最大输入约束；未通过时停止新采集并显式告警，不切换其他模型。

网关负责鉴权、超时、并发、错误归一化和响应过滤，不把 API key 写入日志。

#### 12.3.1 固定双阶段证据综合

```mermaid
flowchart LR
    Docs[编号来源文本\n最多 30,000 字符] --> First[第一次 OMLX\n综合候选]
    Vision[联系表\n最多 6 图] --> First
    First --> Candidate[候选 values/source_indices/conflicts]
    Docs --> Audit[第二次 OMLX\n独立证据审核]
    Vision --> Audit
    Candidate --> Audit
    Audit --> Gate{日期、地域、口径、\n单位及直接证据匹配?}
    Gate -->|是| Approved[批准/纠正值]
    Gate -->|否| Empty[业务值置 null\n保留理由与冲突]
```

地域校验失败关闭：城市值不能满足省级目标，省/市/公司子集不能满足国家目标，单国不能满足全球目标。一个数字即使很显眼，只要统计期、指标定义、总体或单位不符，就不得通过。

上图同时是当前实现事实和目标质量契约。`First` 和 `Audit` 都调用同一 `Qwen3.8-27B-6bit`，但是两个串行的独立请求；第二次不复用第一次对话会话。每个 unit 必须完成两次调用，包括结构化来源的行。OMLX 全局并发固定为 1，不允许多行批处理。

### 12.4 模型使用边界

模型适合：

- 非结构化内容理解；
- 复杂工作表版式、多层表头和字段语义识别；
- 网页、PDF 和附件图片中的文字、表格和图表语义抽取；
- 实体和口径匹配；
- 从证据提取结构化候选；
- 生成可验证的来源摘要。

模型不负责：

- 定义实际 OOXML 单元格值、公式、坐标或合并区域；
- 绕过结构校验直接发布字段映射；
- 业务状态转换；
- 直接写数据库；
- 最终决定审核通过；
- 隐式货币或单位计算；
- 生成没有证据的事实；
- 生成或执行任意代码。

单位和数值转换由确定性规则复算。

#### 12.4.1 ai_index 单位转换门禁

应用维护版本化 UnitRegistry，把单位规范化为“维度 + 相对基础单位 Decimal 倍率”。现有表的
人民币/美元数量级、中文计数前缀、FLOPS、字节前缀和同单位传递优先形成程序规则。

处理顺序固定为：

1. 模型从被引用证据提取 `be_data/be_unit` 和可选转换候选；
2. 程序先执行 `convert(be_data, be_unit, unit)`；
3. 程序成功时覆盖并忽略模型候选；
4. 仅程序返回 `UNSUPPORTED` 时，才允许采用 VERIFY 已批准且输入输出完整的模型转换；
5. 模型转换标记高风险，禁止自动批准；缺值、非数值、已知维度冲突和汇率计算不得降级猜测。

转换审计保存模式、规则版本、规范化单位、倍率、公式、输入和输出。审核页直接展示这些字段，
不要求审核员阅读原始 JSON。每行模型调用仍严格为两次，转换降级复用这两次响应，不增加调用。

### 12.5 工作簿识别融合

新系统不携带旧服务的“Sheet 名前缀 + 前两行 `logic_id` + 写死 Profile”主路径。工作簿识别流程为：

1. `StructureExtractor` 从 OOXML 生成 `WorkbookStructureGraph`；
2. `SheetRenderer` 生成全局缩略图与带 A1 坐标的高清分片；
3. `TemplateRetriever` 根据结构签名召回已发布模板；
4. `VisionRecognizer` 输出 header ranges、data regions、descriptor fields、target groups、business-key candidates 和可读理由；
5. `RecognitionFusion` 用实际单元格、合并区域、字段唯一性、类型分布和模板差异交叉校验；
6. 确定签名命中已确认模板时直接复用；新结构只在关键约束全部通过时自动接受；
7. 冲突、越界、必需字段缺失或业务键不稳定时进入 `NEEDS_CONFIRMATION`。

置信度由模板相似度、结构规则、模型候选一致性和冲突数联合校准，不直接采信模型自报分数。用户确认的结果保存为新模板版本，使后续同类文件走低成本快速路径。

### 12.6 图片证据识别

新系统不将 Tesseract + 灰度/二值化作为图片理解主路径。原图经安全检查、尺寸限制和必要分片后发送到本地视觉模型，输出必须通过任务 JSON Schema。证据保存原图 hash、分片坐标、固定模型实际 ID/版本、量化、Prompt 版本、原始受控响应和解析结果。

采集顺序保持“官方结构化数据 > HTML 正文/表格 > 图片派生表格”。对于门户展示页，如果来源
同时提供官方机器可读接口，先将同构 JSON 对象数组通用收敛为 CSV；图片只补充结构化内容和正文
未覆盖的信息，不能用首屏截图替代完整历史序列。

对于可抓取的通用图文网页，正文解析会保留候选图片位置，并联合文章标题、图片
`alt/title`、图注和邻近正文识别数据型图片。视觉结果先收敛为有界二维表，再回填到原正文位置；
缺失列名可基于这些上下文补全，但必须以 `[推测]` 标记。后续 `SYNTHESIZE` 和 `VERIFY`
接收包含图片派生表格的完整正文。同一图片按模型、提示词版本和图片哈希缓存，装饰图显式跳过，
长表中偶发的缺失单元格只补 `null` 并留下结构修正审计，不因一行列数偏差丢弃整表。

成功快照的图片对象与正文一起写入共享来源缓存卷，避免 worker 重启后出现“正文仍有图片占位符、
原图却已丢失”的状态。图片识别成功后，增强正文和结构化结果覆盖基础快照；因此同 URL 的后续行
以及新进程都直接复用增强正文，不重复下载或识图。

如视觉能力不可用，图片证据标记为 `MEDIA_RECOGNITION_UNAVAILABLE`，改用 HTML 可访问文本、替代来源或人工核对；不得在无告警的情况下当作“图片中没有数据”。

## 13. 工作簿设计

### 13.1 读取

- 以流式上传文件保存后的对象为输入；
- 使用 openpyxl 读取工作簿结构和单元格；
- 同时保存 `raw_value`、`display_value`、公式和样式引用；
- 生成工作簿结构签名，并将可能的多层表头交给识别融合流程；
- 原工作簿只读。

### 13.2 构建

- 从原文件复制到新的构建对象；
- 按业务键确定 `INSERT/UPDATE/KEEP/RETIRE/CONFLICT`；
- 重建目标数据区，而不是依赖旧行号跨版本写回；
- 将审核后的最终值写入目标字段；
- 扩展公式、样式、数据验证和冻结窗格；
- 防止以 `= + - @` 开头的非公式文本发生公式注入。

### 13.3 验证

- 工作表名称和顺序；
- 表头签名；
- 业务键集合；
- 字段值和类型；
- 公式覆盖范围；
- 数据验证；
- 样式抽样和关键区域检查；
- Excel 错误公式扫描；
- LibreOffice headless 可选重算和 PDF/图片渲染抽查。

## 14. API 与事件架构

### 14.1 API

- `/api/v1` 版本前缀；
- OpenAPI 是契约；
- 命令返回 `202 Accepted` 和资源当前状态；
- 查询返回强类型分页对象；
- 写操作使用 idempotency key 和 expected version；
- 错误返回稳定 code、message、details、requestId。

### 14.2 SSE

任务事件同时持久化到 `task_events` 并通过 Redis pub/sub 唤醒连接。

客户端提交 `Last-Event-ID` 后：

1. API 从 PostgreSQL 补发缺失事件；
2. 再订阅 Redis 实时通知；
3. Redis 通知丢失时，客户端仍可重连补取。

## 15. 安全架构

### 15.1 认证

- 浏览器使用同源 HTTP-only、Secure、SameSite 会话 Cookie；
- Redis 保存活动 session，PostgreSQL 保存用户和安全事件；
- 写请求使用 CSRF token；
- 后续自动化客户端使用可撤销 API token。

### 15.2 授权

角色：

- `ADMIN`；
- `OPERATOR`；
- `REVIEWER`；
- `VIEWER`。

资源同时检查角色和任务所属 workspace。首版可以只有一个 workspace，但数据模型保留边界。

### 15.3 密钥

- OMLX、搜索、对象存储和数据库凭据通过环境或 Docker secret 注入；
- 配置 API 只显示是否配置，不回传密钥；
- 日志统一脱敏；
- Git 中只提供 `.env.example`。

### 15.4 审计

以下操作必须审计：

- 登录失败和权限拒绝；
- 文件上传和下载；
- 创建、启动、暂停、恢复、停止和删除任务；
- 单条和批量审核；
- 模板发布；
- 正式导出；
- 管理配置变更。

## 16. 性能与资源治理

### 16.1 限流层次

- 每用户 API 限流；
- 每 workspace 同时运行任务数；
- 每来源域名并发和 QPS；
- OMLX 全局并发固定为 1；
- 文档解析并发；
- 单任务预算。

### 16.2 缓存

- 搜索结果不跨行缓存或复用；只有进入 `SEARCH_FALLBACK` 的 unit 建立自己的 `RowSearchAttempt` 并实际调用 SearXNG；
- 当前实现按规范 URL（动态榜单额外含任务快照 scope）建立进程内 L1 与共享文件 L2，成功项保存正文、原始图片、图片派生表格和 content hash，默认 TTL 24 小时；ETag/Last-Modified 条件刷新作为后续增强，不作为本次上线前提；
- `SourceSnapshot` 保存原文、解析文本、结构化索引、媒体引用和哈希，`UnitSourceLink` 保存行级引用与定位；同 URL/同内容可避免重复下载和解析，但每行仍须独立建立证据切片和契约匹配；
- 相同规范 URL 使用共享文件锁合并 API/worker 跨进程并发获取；403/挑战页、429 和瞬时错误写 URL 负缓存，挑战/限流同时写域级冷却。默认瞬时退避 60 秒、挑战冷却 3,600 秒、最大 3,600 秒、同域新请求最小间隔 2 秒；冷却期内直接返回可重试状态，不进入搜索降级或浏览器回退；
- 模型结果不用于跳过新行的两次调用；缓存只可作诊断/对照，不作为新 unit 的正式结果；
- OMLX 前缀缓存只作机会性性能优化并记录命中指标，业务正确性不得依赖缓存；关闭缓存必须得到相同业务结果；
- 人工审核结果复用必须由模板策略明确允许。

### 16.3 非模型批量操作

数据库可批量插入 records 和 contracts，同一行的 HTTP 候选可在域级限流下并发抓取。规划阶段把规范 URL 哈希写入 `CollectionUnit.source_affinity_key`；无直链行使用独立业务键。Processor 按亲和键、源行、unit ID 排序，使相同来源相邻，同时保持该来源内部的源行顺序。搜索降级必须逐行发起，模型每次只处理一行并全局串行。

`SYNTHESIZE` 与 `VERIFY` 均采用“稳定系统提示 + `<shared_sources>` 完整编号证据 + 行级后缀”的顺序。
相同来源相邻行的同阶段共享前缀逐字一致，供 OMLX KV cache 机会性复用；RowContract、原始行、候选
和结论仍位于独立后缀且不跨行共享。

可把多个 unit 放入同一 Celery 消息降低队列开销，但 worker 必须逐个领取、记录和提交，单个失败不回滚整个批次。

## 17. 可观测性

### 17.1 日志

结构化 JSON，至少包含：

- request_id；
- user_id；
- task_id；
- task_run_id；
- dataset_run_id；
- collection_unit_id；
- attempt_id；
- event；
- duration_ms；
- error_code。

不得记录完整密钥、Cookie、敏感来源凭据和模型隐藏思维内容。

### 17.2 指标

- API 延迟和错误率；
- 队列深度；
- active/expired lease；
- worker 心跳；
- 每来源成功率和延迟；
- OMLX 延迟、错误和并发；
- 文本/视觉请求队列深度、识别耗时和能力探测状态；
- 工作簿识别自动接受率、冲突率、人工修正率，以及 CI/外部验收系统发布的质量摘要；
- 候选覆盖率；
- 证据覆盖率；
- 执行状态计数与失败/重试率；
- `RESOLVED/PARTIAL/UNRESOLVED/CONFLICT/INVALID` 业务覆盖率；
- 直链成功率、搜索降级率与原因、每行搜索请求数、URL/内容缓存命中率、下载/解析数，以及 OMLX `SYNTHESIZE/VERIFY` 调用数；
- OMLX 队列等待时间、推理耗时和实测在途并发（必须始终 `<= 1`）；
- 审核耗时、自动通过率、抽样错误率、人工修正率和确认未解决率；
- 导出失败率。

### 17.3 健康检查

- `/health/live`：进程存活；
- `/health/ready`：数据库、Redis、对象存储基本可用；
- OMLX 单独展示健康状态，不一定阻止只读 API ready。

## 18. 部署设计

### 18.1 Docker Compose 服务

```text
web
api
worker-workbook
worker-source
worker-vision
worker-collection
scheduler
postgres
redis
object-storage（可选，若使用外部 KS3 则不部署）
```

当前 `compose.yaml` 实际定义 `postgres` / `redis` / `migrate` / `api` / `worker` / `web`，并不按 workbook/source/vision/collection 拆成多个 worker 容器。上述多 worker 名称是随规模扩容的目标拆分，不应作为当前部署事实。Docker 镜像已安装 `antiword` 和 Playwright Chromium，并将浏览器文件放入非 root 用户可读的共享路径。

### 18.2 NAS 网络

- 对外只暴露 Web/API 入口；
- PostgreSQL、Redis 和对象存储使用内部网络；
- OMLX 使用管理员配置的局域网固定地址；
- Synology 反向代理负责域名和 TLS；
- CORS 配置为实际前端域名；
- 容器使用健康检查和 restart policy。

### 18.3 持久卷

- PostgreSQL data；
- Redis AOF；
- 对象存储 data；
- 备份 staging；
- 不依赖 API/worker 容器本地磁盘保存业务文件。

### 18.4 备份

- PostgreSQL 每日逻辑备份，关键版本前物理快照；
- 对象存储按任务和保留策略备份；
- 备份包含数据库版本和对象清单；
- 每季度执行恢复演练；
- 正式导出和审计记录纳入备份。

## 19. 旧系统迁移

### 19.1 原则

- 旧服务保持只读运行，直到新系统完成验收；
- 不在旧库上直接执行新系统 migration；
- 使用独立迁移 CLI 读取旧 MySQL，写入新 PostgreSQL；
- 每个迁移批次生成对账报告；
- 不迁移明文密钥。

### 19.2 映射

- `files` → 新 files/file_versions；
- `tasks` → legacy tasks/task_runs；
- `data_records.raw_data` → records；
- `target_fields` → 初始 target group 候选；
- `search_results` → evidence discovery metadata；
- `processed_data` → legacy candidate，不自动认定 final value；
- `is_reviewed=1` → legacy review decision，要求保留来源标记。

### 19.3 切换

1. 新系统完成离线基线；
2. 迁移历史数据副本；
3. 新旧系统并行跑同一批任务；
4. 比较任务、结果、证据和工作簿；
5. 冻结旧系统写入；
6. 最终增量迁移；
7. 前端切换；
8. 旧系统保留只读观察期后归档。

## 20. 测试策略

### 20.1 单元测试

- 状态机；
- RowContract；
- 业务键；
- 空值语义；
- 单位和日期标准化；
- 候选评分；
- readiness；
- Excel 公式注入防护。
- HTML 主文去噪、PDF 渲染页、DOCX 表格/图片和视觉联系表；
- 403/短正文浏览器回退判定、二进制文件排除和挑战页识别；
- 状态聚合前 flush，以及单元转 RUNNING 时统计立即可见。

### 20.2 属性测试

使用 Hypothesis 检查：

- 状态机不存在非法跃迁；
- 幂等命令重复执行结果一致；
- 单位转换往返和范围；
- 业务键规范化稳定；
- 随机空值和特殊字符不会破坏工作簿。

### 20.3 集成测试

- PostgreSQL 锁、lease 和 outbox；
- Redis/Celery 重试；
- S3 对象上传、下载和清理；
- worker 强制退出恢复；
- OpenAPI contract；
- SSRF 和下载限制；
- OMLX 图像输入、结构化响应和能力探测合约；
- 工作簿渲染、分片坐标和识别融合门禁。
- 公开 JavaScript 测试页能由 Chromium 恢复静态 HTTP 中不存在的正文并生成截图；
- 微信验证码重定向被识别为受限，保留原始 URL，不将挑战文本/图像作为证据。

### 20.4 端到端

- 275 行快速回归；
- 275 行文件的结构/视觉识别及外部变体测试集回归；
- 平台在无金标权限环境运行完成后，由独立进程执行 11,996 → 17,490 历史金标离线比较；
- 暂停、恢复和停止；
- 并发启动；
- 审核冲突；
- 导出门禁；
- 原工作簿回填和格式验证。

## 21. 实施阶段

共同前置门禁：在 P0-3 数据迁移前，将现有 `gold collector`、金标配置与对照逻辑移出平台生产包和运行时，改由独立验收包负责，并用 CI 扫描锁定边界。该门禁不是第四个 P0 阶段。

### P0-3：状态语义（第一顺位）

- 增加 execution/resolution/review 三套正交状态、原因码和分类聚合；
- 保守迁移旧数据，无法确定的旧结果标记 `NOT_EVALUATED`；
- 同步 API、Web、审核队列、导出 readiness 和指标；
- 用真值表测试锁定 `SUCCEEDED + null`、冲突、驳回重采和导出语义。

### P0-2：吞吐与可恢复性（第二顺位）

- 建立行属于的 `SourceAcquisitionAttempt`、按需 `RowSearchAttempt`、可复用 `SourceSnapshot`、`UnitSourceLink` 和 URL/内容去重；直链优先，搜索仅作降级且响应不跨行复用；
- 将 discover/fetch/parse/synthesize/verify/validate 拆为可续跑阶段，记录幂等键、attempt、lease、heartbeat、`next_attempt_at` 和资源上限；
- 固定 `Qwen3.8-27B-6bit`、OMLX 全局并发 1、一次请求一行，并对每行固定执行两次串行请求；
- 优化相同来源抓取、URL/内容缓存、结构化索引与证据切片复用和恢复扫描，禁止跨行会话、模型批处理、并发或减少调用次数；
- 旧 run 保持只读可追溯，新建任务走新管线，不就地篡改历史证据。

### P0-4：异常审核效率（第三顺位）

- 建立版本化 `ReviewPolicy`、`AUTO_APPROVED` 审计记录与风险抽样；
- 审核队列默认只展示需人工决策的异常；
- 批量操作使用冻结筛选快照、预览影响集、expected version 和逐行结果；
- 实现 `CONFIRMED_UNRESOLVED` 与正式导出附带的未解决报告。

上述三步通过验收后，再继续以下整体建设阶段。

### 阶段 A：工程与可靠底座

- 工程骨架、质量工具和 CI；
- PostgreSQL、Redis、S3 和 Docker Compose；
- 认证、权限和审计；
- 文件上传和对象存储；
- task/task_run/outbox/lease；
- worker 恢复和状态机。

### 阶段 B：工作簿与任务规划

- 工作簿解析；
- 工作表渲染、视觉能力探测和识别融合；
- 模板版本；
- RowContract；
- dataset mode 和业务键；
- 275 行规划验收。

### 阶段 C：采集与证据

- 来源适配器；
- 抓取缓存；
- 文档转换；
- OMLX gateway；
- 候选提取、标准化、验证和评分；
- 字段组原子提交。

### 阶段 D：审核与导出

- 审核 API 和冲突处理；
- 批量审核；
- readiness；
- 审核快照；
- 工作簿回填和正式导出。

### 阶段 E：优化与迁移

- 历史结果复用；
- 权威来源适配器；
- 独立外部金标回归；
- 旧数据迁移；
- NAS 正式切换。

## 22. 关键风险

| 风险 | 应对 |
| --- | --- |
| openpyxl 无法完全保留高级 Excel 特性 | 用真实文件回归；建立 WorkbookEngine 端口；必要时引入 LibreOffice 辅助 |
| Celery 消息和数据库状态不一致 | outbox、幂等消费、lease 和恢复扫描 |
| 通用搜索质量低、人工仍多 | 优先建设权威来源适配器和历史审核安全复用 |
| 模型结果格式和质量波动 | JSON Schema、提示词版本、确定性校验、独立外部回归集 |
| 多模态模型幻觉单元格坐标或字段角色 | OOXML 事实层交叉校验，关键冲突强制人工确认 |
| 视觉请求占满本地 OMLX | 单一全局串行队列、固定并发 1、显示排队与 ETA；不通过批处理或并发规避 |
| 任务暂停/停止后迟到写入 | run version、提交前状态检查、DISCARDED 状态 |
| 数据量增大导致审核页面变慢 | 服务端分页、字段摘要、正文按需加载、索引和只读投影 |
| 旧需求两套口径冲突 | 把预览产物和正式导出分开；正式导出始终受可审计的自动/人工审核门禁控制 |

## 23. 下一阶段生产切换条件

当前已进入全量本地采集阶段。在 NAS/生产切换前，应完成：

1. 本文和 ADR 评审通过；
2. 领域状态机测试样例确认；
3. 核心 ER 模型和索引评审；
4. OpenAPI 第一版冻结；
5. 冻结原始来源 Fixture 的合法使用与存储方式确认，并证明其未从金标反向生成；
6. NAS 上 PostgreSQL、Redis、worker、对象存储和 OMLX 连通性刺探；
7. Excel 保真度刺探；
8. OMLX 视觉合约、275 行结构变体和识别融合门禁刺探；
9. P0-3、P0-2、P0-4 按顺序完成，正式导出验证 `AUTO_APPROVED`、`CONFIRMED_UNRESOLVED` 和可选项 `SKIPPED` 语义；
10. 全量在线任务完成，对浏览器回退、挑战页、证据充分率和 `null` 率进行分 Sheet 抽样；
11. PostgreSQL/Redis/Celery 切换后重跑暂停、恢复、迟到结果和统计一致性验收。
12. 平台运行完成后由独立进程执行金标比较，并通过生产包/配置扫描证明平台无金标依赖。

## 24. 工作表处理范围门禁

工作簿可见不等于平台负责采集。`AI_news`、`gpu_chip_performance`、`ai_person` 和
`ai_computing_power` 由人工维护；`ai_model_permission` 和 `aigc_reg_i` 由既有外部自动
程序维护。平台在确定性分析层把六表标为范围外，并在视觉识别、默认选择和任务规划层分别
设置独立门禁，防止单一前端或配置错误重新纳入。

排除表仍保留在文件结构、预览和正式导出中，但不会产生 RowContract、来源获取、搜索、
模型调用、审核决策或写回更新。机器名及处理方属于版本化代码契约，具体决策见 ADR-0010。
