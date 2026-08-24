# ADR-0008：人工智能算法收藏月度 Top 10 增量采集

- 状态：Accepted / Implemented
- 日期：2026-08-24
- 适用工作表：`ai_algorithm_collectio` / 人工智能算法收藏

## 背景

该工作表不是对既有样例行逐字段补空，而是月度增量榜单。每次任务都要从固定 GitHub
搜索范围中取得当前收藏量最高的十个仓库，保留工作簿已有历史行，并在新的空白行中追加
一个完整快照。生产系统不得读取或推断外部金标。

固定业务来源为：

`https://github.com/search?q=stars%3A%3E9999&type=repositories&s=stars&o=desc`

## 决策

1. 查询范围是所有 `stars > 9999` 的 GitHub 仓库，输出仅取按精确收藏数降序排列的前
   10 名；名次为 `1..10`，精确收藏数相同时保留 GitHub 响应顺序。
2. 应用使用与固定网页查询等价的 GitHub Search API 获取结构化快照，API 地址只进入内部
   获取审计；工作簿十行的 `source_url` 始终写固定可浏览网页地址。
3. `star` 由程序按 `floor(stargazers_count / 1000)` 生成整数，`star_unit` 固定为 `k`。
   模型不能改写确定性排序、名次或单位换算。
4. 任务规划时一次性冻结 Asia/Shanghai 当前时间，同一个快照的 `collect_date`、
   `datasource_date`、`collection_date` 十行完全一致。
5. 固定字段为 `source_department=Github`、`update_frequency=month`、
   `data_type=采集`、`data_status=新增`；`logic_id` 由“项目名称 + 快照时间”确定性哈希生成。
6. 每次任务追加 10 行：优先使用表头后的预格式化空白行，不覆盖任何已有内容；空白行
   不足时在末尾扩展。
7. 程序先完成一次结构化下载、合法性校验、稳定排序和前十选择。随后为每个名次只切出
   当前仓库的一行证据，分别建立独立 RowContract 和全新模型消息，固定串行执行
   `SYNTHESIZE -> VERIFY` 两次 `Qwen3.8-27B-6bit` 请求。
8. 模型只核验当前名次的 `name` 和整数 `star`，并必须确认 `rank` 约束。十个项目不能进入
   同一个模型会话，禁止模型批处理、跨行候选/结论复用和并发。
9. 固定来源获取失败、返回不足十条或结构不合法时失败关闭并按单元重试；不得降级到通用
   SearXNG，也不得用其他网页拼凑榜单。
10. 工作簿视觉识别不得覆盖这个 Profile 的字段角色、数量、来源或增量模式。

## 字段契约

| 字段 | 生成方式 |
| --- | --- |
| `logic_id` | 程序按 `sha256(name + "\\n" + snapshot_at)` 生成 |
| `collect_date` | 本次快照当前时间 |
| `rank` | 程序生成 1 到 10 |
| `name` | GitHub `full_name`，经逐行双模型核验 |
| `star` | 精确收藏数除以 1000 向下取整，经逐行双模型核验 |
| `star_unit` | 固定 `k` |
| `source_department` | 固定 `Github` |
| `source_url` | 固定 GitHub 搜索网页 |
| `update_frequency` | 固定 `month` |
| `datasource_date` | 与本次快照当前时间相同 |
| `collection_date` | 与本次快照当前时间相同 |
| `data_type` | 固定 `采集` |
| `data_status` | 固定 `新增` |

`update_time`、`created_time` 等未列字段不是本次采集目标，已有历史值不修改，新追加行保持
空白。

## 冲突与取代关系

- 取代通用 `snapshot_build` 对该表的推断；该表固定使用 `monthly_top10_append`。
- 取代“只处理已有非空行”的规划方式；即使模板只有历史样例，也必须新建十个采集单元。
- 取代“目标只包含 `rank/star/star_unit/source_url`”的旧识别结果；项目名称、三类时间和固定
  元数据都属于完整输出契约。
- 取代“直链失败后搜索降级”的通用策略；固定榜单来源具有唯一业务含义，失败只能重试。
- 不改变全局 OMLX 并发 1、每行固定双模型、来源可审计和金标隔离要求。

## 验收

1. 同一任务正好生成 10 个该表单元，写入十个新空白行且不修改已有历史行。
2. 十行排名连续、时间完全一致、固定字段完全一致，名称和收藏量与同一次 API 快照对应。
3. 每行模型提示只包含当前名次的一条仓库证据，每行恰好两次模型调用，通用搜索调用为 0。
4. API 不足十条、顺序异常、非法 JSON 或固定来源获取失败均失败关闭。
5. 导出后原有样式、历史行和其他工作表保持不变。
