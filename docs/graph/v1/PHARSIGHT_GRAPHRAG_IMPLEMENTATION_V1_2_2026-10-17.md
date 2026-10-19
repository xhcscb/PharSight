# Pharsight GraphRAG V1.2 实现规范

> 运行时 Schema：`pharsight-graph-v1.1`
> 抽取器版本：`graphrag-v1.2`
> 实现基线：2026-10-17，分支 `graphRAG`
> 本文描述当前代码行为；理论依据见《Pharsight GraphRAG V1.2 理论架构》

## 1. 组件与边界

| 组件 | 当前职责 |
| --- | --- |
| `src/tools/document/` | PDF 解析、分页、确定性分块、持久化索引和 BM25 检索 |
| `src/graph_rag/models.py` | Pydantic 运行时模型、枚举、Evidence Pack 和构建报告 |
| `src/graph_rag/builder.py` | 原子记录与材料性抽取、实体消歧、稳定 ID 和构建失败控制 |
| `src/graph_rag/store.py` | SQLite 权威记录、事务、审核、高召回记录搜索和 NetworkX 投影 |
| `src/graph_rag/retriever.py` | QueryPlan、Claim 候选、有限跳扩展、查询子图、排序、BM25 合并和回退 |
| `src/tools/graph/evidence_search.py` | 将 Evidence Pack 适配为现有 `ToolResult` |
| `src/agents/data_collector/` | 按配置选择检索后端、维护批准来源范围、触发 ingest 后构图 |

SQLite 是权威数据源，NetworkX `MultiDiGraph` 是按需重建的遍历投影。报告生成器不访问
二者，只消费 DataCollector 写入 Memory 的标准证据结果。

## 2. 运行时数据模型

### 2.1 节点模型

| 模型 | 关键字段 |
| --- | --- |
| `DocumentNode` | `document_hash`、`source_id`、title、publisher、URL、发布/获取/报告时间、`as_of`、metadata |
| `PassageNode` | `document_id`、`chunk_id`、页码、section、原文、`content_hash` |
| `EvidenceSpan` | `passage_id`、连续 quote、起止字符、`field_paths` |
| `GraphRecord` | Claim/Observation/Event、domain、认识论状态、polarity、attributes、materiality、Evidence ID、审核状态、模型与抽取器版本 |
| `GraphEntity` | 16 类实体、规范名、别名、外部 ID、消歧状态 |

Observation 在校验层要求 `metric`、`value`、`value_nature`；Event 要求 `event_type`，并至少
具有 `event_time` 或 `disclosure_time`。模型输出不允许 Schema 外字段。

### 2.2 边模型

所有边统一保存端点、relation、origin、Evidence ID、支持记录 ID、抽取器/模型标识、审核
状态、有效时间和扩展属性。

当前确定性组织边为：

- `Document --CONTAINS--> Passage`；
- `Passage --YIELDS--> Evidence`；
- `Evidence --SUPPORTS_RECORD--> GraphRecord`。

记录到实体的 relation 来自模型输出的语义角色，转换为大写下划线形式；空角色回退为
`ABOUT`。七类证据关系在收到 query 后临时连接本次候选 GraphRecord，并作为 `QueryGraphEdge`
返回；它们不写回权威基础图。

### 2.3 实体与记录枚举

记录领域：clinical、financial、market、regulatory、commercial、mechanism、other。

认识论状态：asserted、reported、estimated、forecast、opinion、hypothesis、unknown。

实体类型：Company、Security、Drug、Program、Brand、Target、Modality、Indication、Trial、
Regulator、Policy、Payer、MarketSegment、TechnologyPlatform、Transaction。

## 3. SQLite 权威存储

| 表 | 用途 |
| --- | --- |
| `graph_meta` | 当前 Schema 版本 |
| `documents` | 文档身份与来源元数据 |
| `passages` | 页级文本块 |
| `evidence_spans` | 可定位原文范围 |
| `records` | Claim/Observation/Event，含 `materiality_json` |
| `entities` | 规范实体及消歧状态 |
| `record_entities` | 记录—实体—角色索引 |
| `edges` | 组织边、角色边和证据关系 |
| `relation_assessments` | 已接受关系的置信度、理由和可比性 |
| `relation_pair_reviews` | 已审候选对，包括“无支持关系”结果 |
| `build_runs` | 模型、Schema、抽取器、状态、起止时间和错误 |

连接启用外键和 WAL。单文档 bundle 使用 `BEGIN IMMEDIATE` 原子替换；替换前清理该文档
拥有的 Passage、Evidence、Record、相关边和关系评审。异常触发 rollback，不留下半成品。

NetworkX 投影从 documents、passages、evidence_spans、records、entities 和 edges 全量重建，
不承担权威持久化。

## 4. 身份、幂等与版本

稳定 ID 使用规范化 JSON 输入的 SHA-256 前 24 位，并带类型前缀：

- Document：文档内容哈希；
- Passage：文档哈希 + chunk ID；
- Evidence：Passage ID + 字符起点 + quote；
- Record：记录类型 + 文本 + attributes + Evidence ID；
- Entity：实体类型 + 外部 ID；无外部 ID 时使用规范化名称身份；
- Edge：端点 + relation；
- EvidenceRelation：左右记录 + 关系类型。

`document_hash + schema_version + extractor_version + model_name` 存在成功构建记录时，默认
返回 `reused`。强制重建会原子替换该文档拥有的数据。关系候选评审也按抽取器和模型版本
去重，已判断为“无关系”的候选不会在相同版本中重复计费。

## 5. 输入与 PDF 解析边界

本地 PDF 必须先由文档解析器提取分页文本，再调用 `build_document_index`：

- 块不跨页；
- chunk ID 包含文档哈希前缀、页号和页内块号；
- 保存 page_start/page_end、section、content hash 和完整文本；
- 索引先以临时文件写入，再原子替换；
- 构图模型只接收索引中的文本，不直接读取 PDF 或图像。

没有分页索引的 manifest/web 证据被规范化为一个 Document 和一个 Passage；只有 locator
明确给出页码时才填写页码，不虚构“第 1 页”。`cutoff_eligible=false` 的材料不进入图。

## 6. 构图算法

### 6.1 文档级流程

1. 检查内容哈希与当前版本是否已经成功构建；
2. 新建 `running` build run；
3. 对非空 Passage 做受控并发抽取；
4. 校验记录 Schema 和 Evidence quote；
5. 解析实体身份并生成 bundle；
6. 在单个 SQLite 事务内替换文档 bundle；
7. 标记 build 为 `built`，或在异常时标记 `failed`。ingest 不生成七类记录关系。

### 6.2 LLM 调用约束

构图、歧义实体消歧、关系分类均使用调用方传入的主模型标识，即当前 `DS_MODEL_NAME`。
源码不硬编码服务端模型名称和密钥。当前服务配置把 `deepseek-flash` 作为 DeepSeek v4.1
别名使用，数据库保存实际传入标识。

请求使用 temperature 0、严格 JSON object 响应、关闭 thinking，并设置输出 token 上限。
底层 JSON 调用最多尝试 3 次；每个 Passage 的抽取层还会在可恢复错误上重试。返回文本先
移除 Markdown fence，再经过 JSON repair 和 Pydantic 校验。

### 6.3 Evidence gate

Evidence quote 必须是 Passage 中的连续片段。定位首先要求逐字命中；仅允许忽略 PDF 与
模型之间的空白差异，再映射回原始字符范围。找不到 quote 时整条记录被拒绝。

非法 Observation/Event 字段、Schema 外字段或枚举值被拒绝并写入 BuildReport errors。
Passage 抽取失败比例超过配置阈值时，整个文档构建失败。没有结构化记录且存在抽取失败
也不能标记为成功。

### 6.4 实体消歧

1. 有外部 ID 的提及以该身份生成稳定实体；
2. 没有外部 ID 时，仅在相同实体类型内按规范名/别名查找候选；
3. 没有候选时创建 temporary 实体；
4. 有候选时由主模型结合 Passage 和角色保守判断；
5. 模型只能选择提供的候选 ID，非法 ID 被清空；
6. 不确定结果标为 needs_review，而不是强制合并。

### 6.5 材料性抽取与打分

模型为每条原子记录给出 `decision_impact`、`evidence_strength`、`novelty`、
`time_sensitivity`、scope 和 reason。四个维度均在 0～1，最终分数由代码确定性计算：
`0.35 × impact + 0.25 × evidence + 0.20 × novelty + 0.20 × time`。该分数只作轻量排序
先验；它不能代替查询相关度，也不会作为候选准入门槛。

## 7. 检索实现

### 7.1 QueryPlan

主模型把问题转换为：entity_names、record_types、domains、keywords、relation_types 和
0～2 跳的 max_hops。提示词要求扩展别名、缩写、中英文名称与表头术语，目标是高召回而非
最小关键词集合。规划调用有独立超时；失败、超时或 Schema 非法时，系统记录 warning，并
用确定性中英文词法 token 生成计划。罗马数字Ⅰ～Ⅳ在词法层归一化为 I～IV。

### 7.2 高召回原子记录检索

SQLite 直接搜索 Record text、attributes、Evidence quote、Passage section 和所有记录—实体
边派生出的实体名称，不依赖可能不完整的 `record_entities` 角色索引。默认候选池为 120；
`record_types`、domains 和 materiality 均为软加权。rejected 记录被排除，source/document
约束在 SQL 层执行。候选被转换为带 Evidence ID、页码、chunk ID 和原文的 `claim` 通道命中。

### 7.3 基础图与 Chunk 检索

1. 以实体名和关键词在 Document、Passage、Evidence、Record、Entity 中寻找最多
   `max(60, top_k × 4)` 个词法种子；
2. 从 SQLite 重建或复用 NetworkX 投影；
3. 沿入边和出边做 0～2 跳 BFS；
4. 遍历只使用组织边和实体角色边；数据库中旧版本遗留的七类持久语义边不参与召回；
5. 将命中的 Record 回聚到 Evidence、Passage 和 Document；
6. 按 Passage 合并多个 quote，避免同页记录占满结果窗口。

图评分当前综合词法重合、approved 记录数量、路径距离、关系理由、显式 source ID、计数意图
和报告期。问题显式指定报告期时，同期材料获得强加权，错期材料被显著降权。

### 7.4 查询时关系构造

Retriever 从本次 query 的高分 Record 中最多取 24 条，按共享实体、相同 object/subject/
metric/event_type/predicate 或相同 Evidence 阻塞候选对，最多提交 100 对给当前主模型。
模型输出仍须通过 Schema、端点白名单、关系白名单、0.8 置信阈值和确定性 hard gate：
`SAME_ORIGIN_AS` 要求同 source 且同 Evidence；等价要求对象、指标、值和条件对齐；替代要求
明确更新语义及稳定主体；支持不得把普通 Event 时间先后当因果；矛盾/不可比要求问题具有
比较意图。通过的边作为 `QueryGraphEdge` 返回，包含理由、可比性、query relevance、模型、
审核状态和 `origin=query_time`，但不写入 SQLite。

### 7.5 Chunk 与 Hybrid

Chunk 通道使用现有确定性 BM25 文档索引。Hybrid 同时使用图命中和 BM25 命中，以
`(source_id, chunk_id)` 去重；双通道命中获得合并加分，并合并记录摘要、图路径和关系理由。
最终多来源查询采用自适应来源上限；只有一个来源时允许返回完整 top-k，避免原有固定 3 条
上限伤害召回。

Graph 没有命中时显式增加 warning，并调用 BM25。Graph、Chunk 和回退都接受相同的
source_id/document_hash/allowed_source_ids 约束；显式 source_id 越界直接报错。

## 8. Evidence Pack 与现有流程

每个 `EvidenceHit` 返回来源字段、页码、chunk ID、quote、完整 Passage、结构化记录摘要、
图路径、关系理由、得分和检索通道；Evidence Pack 另含本次 query 的 `query_edges`。
`GraphEvidenceSearchTool` 按 source 分组，将这些内容
映射到现有 `ToolResult.data`；candidate 记录在输出中明确标为“仅作导航”。

DataCollector 在 graph/hybrid 模式暴露 Graph 工具，在 chunk 模式保留 PDF evidence search，
并从当前 Memory 刷新批准的 source ID。PDF ingest 完成后，仅在 graph/hybrid 且
`graph_build_on_ingest=true` 时自动构图。

## 9. 配置

| 配置 | 默认值 | 约束/作用 |
| --- | --- | --- |
| `retrieval_backend` | `chunk` | chunk / graph / hybrid |
| `graph_build_on_ingest` | `true` | ingest 后自动构图 |
| `graph_store_path` | `./outputs/.graph_cache/pharsight_graph.sqlite3` | SQLite 路径 |
| `graph_max_hops` | `2` | 0～2 |
| `graph_top_k` | `8` | 1～30 |
| `graph_claim_candidate_pool` | `120` | 20～1000；查询前宽召回记录池 |
| `graph_query_relation_record_limit` | `24` | 2～50；进入查询关系判断的记录上限 |
| `graph_query_planning_timeout` | `30` | 1～300 秒 |
| `graph_build_concurrency` | `4` | 1～16 |
| `graph_json_max_tokens` | `16384` | 4096～32768 |
| `graph_max_extraction_failure_ratio` | `0.1` | 0～1 |
| `graph_relation_candidate_limit` | `100` | 1～10000；单模型批仍上限 100 |
| `graph_relation_confidence_threshold` | `0.8` | 0～1 |

运行产物位于被 Git 忽略的 `outputs/.graph_cache/`。数据库、索引、日志和生成报告不能提交。

## 10. 当前限制

- 自动抽取记录和关系默认仍为 candidate，当前没有完整人工审核 UI；
- QueryPlan 的 `record_types` 和 `domains` 已进入 Schema，但当前图遍历尚未把它们作为硬过滤；
- 原子记录检索会剔除 rejected，但 superseded 仍保留用于时间线，生成前必须按状态和原文复核；
- 查询关系判断增加一次模型调用，其延迟与费用随进入关系阶段的记录数增长；
- `set_review_status` 当前只更新 Record，尚没有对 Edge 的同等人工审核接口；
- 记录到实体的角色是开放字符串规范化，不是封闭角色枚举；
- 实体候选检索是保守名称匹配，尚未接入完整受审别名主数据；
- NetworkX 投影在 Retriever 生命周期内缓存，外部增量构图后应创建新 Retriever；
- 当前没有断点到 Passage 级的持久任务队列；失败重跑仍以文档为单位；
- 没有 embedding、PPR、社区摘要或图数据库；
- 现有全量公司模式已实跑，行业模式和 Python 3.11 标准环境仍待完整验收。
