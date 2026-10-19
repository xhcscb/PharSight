# Pharsight GraphRAG V1.1 建图检查命令与质量报告

> 检查日期：2026-10-12（Asia/Shanghai）
> 检查分支：`graphRAG`
> 运行时 Schema：`pharsight-graph-v1`
> 抽取器版本：`graphrag-v1.1`
> 模型标识：`deepseek-flash`（项目当前 DeepSeek v4.1 服务别名）
> 检查性质：只读数据库审计；未修改 SQLite 内容

## 1. 检查对象与结论

本报告检查的最终全量数据库是：

```text
outputs/.graph_cache/graphrag_full_pdf_v2.sqlite3
```

不要使用 `outputs/.graph_cache/graphrag_full_pdf.sqlite3` 作为全量结果。该文件是早期中断
产物，只包含 1 个 Document、11 个 Passage、7 条 Record，并保留一个 running build run。

### 1.1 总体结论

| 检查领域 | 结果 | 结论 |
| --- | --- | --- |
| Document→Passage→Evidence→Record 可追溯性 | PASS | 没有孤儿引用，所有 Evidence quote 和字符范围均可定位 |
| Edge 端点完整性 | PASS | 没有指向不存在节点的边 |
| 七类关系审计记录 | PASS | 174 条语义关系均有 confidence、reason 和 comparability |
| Record Schema 最低字段 | PASS | 已落库 Observation/Event 满足当前硬校验 |
| Record 人工审核状态 | FAIL | 4,920 条记录全部为 candidate |
| 实体消歧质量 | FAIL | 仅 0.5% resolved，89.0% temporary，并存在同名重复实体 |
| 关系候选覆盖 | FAIL | `record_entities` 只覆盖 582/4,920 条记录，4,338 条未进入候选索引 |
| 实体角色词表 | FAIL | 出现 133 种开放角色，包含 `2026` 等非法谓词 |
| Observation 条件结构化 | WARNING | unit、reporting_period、timepoint 覆盖率很低 |
| 构建拒绝与错误审计 | WARNING | 最近成功构建仍记录 168 次 quote 拒绝、60 条非法记录拒绝 |
| 图统计一致性 | WARNING | 当前库为 17,146 条边，旧文档的 17,077 是较早关系批次快照 |

**综合状态：Review，不应视为语义质量验收完成。**

当前图已经可以作为 Evidence-first 检索导航资产使用，因为最终命中仍回到 Passage 原文；
但实体身份、角色词表、候选关系覆盖和人工审核尚不足以把图本身当作经过验证的知识库。

## 2. 运行环境

从 Pharsight 仓库根目录执行：

```powershell
cd "D:\Lucia\David\04 大学\大创\医药行业券商研报\Finsight\pharsight"
```

本机已经存在以下检查能力：

- `sqlite3.exe`：`C:\ProgramData\anaconda3\Library\bin\sqlite3.exe`；
- Python 标准库 `sqlite3`；
- NetworkX；
- pandas、Plotly；
- 当前未安装 pyvis，也没有项目内置 GraphRAG Web Inspector。

Windows 控制台显示中文前可以切换 UTF-8：

```powershell
chcp 65001 > $null
```

## 3. 数据库身份检查

### 3.1 命令

```powershell
Get-Item "outputs/.graph_cache/graphrag_full_pdf_v2.sqlite3" |
  Select-Object FullName, Length, LastWriteTime

Get-FileHash "outputs/.graph_cache/graphrag_full_pdf_v2.sqlite3" -Algorithm SHA256
```

### 3.2 作用

- 固定本次检查的物理数据库；
- 防止把诊断库、旧库或测试库与最终全量库混用；
- 以后重跑时可以通过数据库哈希判断是否为同一快照。

### 3.3 本次结果

- 文件大小：12,996,608 bytes；
- 检查时 SHA-256：`DA4EB43C016CD4003FF3D383A163BBC42CA047CC6F76979A31F6F2D96DB866D7`；
- 检查时最后 build run ID：8；
- 包含 3 个当前 Document；
- 本报告没有把数据库复制或提交到 Git；
- 哈希应在每次正式验收时重新记录，数据库继续写入后哈希会变化。

## 4. 打开 SQLite

### 4.1 命令

```powershell
sqlite3 "outputs/.graph_cache/graphrag_full_pdf_v2.sqlite3"
```

进入交互界面后执行：

```sql
.headers on
.mode box
.tables
```

退出：

```sql
.quit
```

### 4.2 作用

SQLite 是当前图的权威存储。NetworkX 只是可重建投影，因此检查属性、Evidence、关系理由、
构建状态和审核状态时应优先查询 SQLite。

## 5. 总体规模检查

### 5.1 命令

```sql
SELECT
  (SELECT COUNT(*) FROM documents) AS documents,
  (SELECT COUNT(*) FROM passages) AS passages,
  (SELECT COUNT(*) FROM evidence_spans) AS evidence,
  (SELECT COUNT(*) FROM records) AS records,
  (SELECT COUNT(*) FROM entities) AS entities,
  (SELECT COUNT(*) FROM edges) AS edges,
  (SELECT COUNT(*) FROM relation_assessments) AS semantic_relations,
  (SELECT COUNT(*) FROM relation_pair_reviews) AS reviewed_pairs;
```

### 5.2 作用

- 判断是否误用了空库或中断库；
- 确认三层节点是否都存在；
- 对照构建报告检查数量是否一致。

### 5.3 本次结果

| 项目 | 数量 |
| --- | ---: |
| Document | 3 |
| Passage | 449 |
| Evidence | 4,585 |
| Claim/Observation/Event | 4,920 |
| Entity | 1,826 |
| 全部节点 | 11,783 |
| 全部边 | 17,146 |
| 七类语义关系 | 174 |
| 已评审关系候选对 | 623 |

三个 Document 的 source ID 为：`HR-2026H1`、`HR-2026Q1`、`JQ-2026H1`。

## 6. 边数量与统计口径

### 6.1 命令

```sql
SELECT origin, COUNT(*) AS n
FROM edges
GROUP BY origin;

SELECT relation, COUNT(*) AS n
FROM edges
GROUP BY relation
ORDER BY n DESC;
```

### 6.2 作用

- 区分确定性组织边、实体角色边和七类语义关系；
- 发现边数量与文档记录不一致的原因；
- 检查某一种关系是否异常膨胀。

### 6.3 本次结果

```text
确定性组织边：9,954
  CONTAINS          449
  YIELDS          4,585
  SUPPORTS_RECORD 4,920

实体角色边：7,018
七类语义关系：174
总边数：9,954 + 7,018 + 174 = 17,146
```

SQLite 中按 origin 统计为：

```text
structural = 9,954
extracted  = 7,192
```

这里 `extracted` 同时包含 7,018 条角色边和 174 条语义关系。

旧文档中的 17,077 等于 16,972 条非语义边加当时已写入的 105 条语义关系。当前数据库后来
继续预计算了 69 条语义关系，因此最终为 17,146。该解释是根据当前边分解做出的确定性
算术推断；正式验收应始终记录数据库快照时间和哈希。

## 7. 构建运行与拒绝记录

### 7.1 命令

```sql
SELECT
  id,
  document_hash,
  schema_version,
  extractor_version,
  model_name,
  status,
  started_at,
  finished_at,
  error
FROM build_runs
ORDER BY id DESC;
```

只查看每个文档最近一次成功构建：

```sql
SELECT b.*
FROM build_runs b
JOIN (
  SELECT document_hash, MAX(id) AS id
  FROM build_runs
  WHERE status = 'built'
  GROUP BY document_hash
) latest ON latest.id = b.id
ORDER BY b.id;
```

### 7.2 作用

- 检查是否存在 running/failed 假完成；
- 确认模型、Schema 和抽取器版本；
- 查看 quote 错挂、非法字段和模型 JSON 失败；
- 区分“整次构建失败”和“成功构建中拒绝了个别记录”。

### 7.3 本次结果

数据库共有 8 次历史 build run：

- built：6；
- failed：2；
- 三个当前 Document 均存在最近成功的 built run；
- 当前库没有依赖 running run 才能成立的 Document。

最近三个成功 run 的拒绝日志：

| 文档哈希前缀 | quote 无法定位 | 非法 Record | Passage 抽取失败 |
| --- | ---: | ---: | ---: |
| `a19485e9e199` | 1 | 14 | 0 |
| `ff6910663a97` | 96 | 32 | 0 |
| `75450959cd65` | 71 | 14 | 0 |
| 合计 | 168 | 60 | 0 |

这些数量是被 gate 拒绝的候选输出，不是已落库 Record。当前日志没有记录模型尝试生成的
总候选数，因此不能据此计算严格的候选拒绝率；后续应新增 attempted/accepted/rejected
分母指标。

## 8. Record 类型、领域与审核状态

### 8.1 命令

```sql
SELECT record_type, COUNT(*) AS n
FROM records
GROUP BY record_type
ORDER BY n DESC;

SELECT domain, COUNT(*) AS n
FROM records
GROUP BY domain
ORDER BY n DESC;

SELECT review_status, COUNT(*) AS n
FROM records
GROUP BY review_status;
```

### 8.2 作用

- 检查 Claim、Observation、Event 是否都被抽取；
- 判断某一领域是否异常占据全部图；
- 确认自动记录是否被错误标为 approved。

### 8.3 本次结果

记录类型：

| 类型 | 数量 |
| --- | ---: |
| Observation | 3,946 |
| Claim | 789 |
| Event | 185 |

领域：

| 领域 | 数量 |
| --- | ---: |
| financial | 3,252 |
| clinical | 731 |
| regulatory | 512 |
| other | 174 |
| commercial | 143 |
| market | 55 |
| mechanism | 53 |

全部 4,920 条记录均为 `candidate`。这一点符合“自动抽取默认 candidate”的设计，但也
意味着尚无任何 Record 完成人工语义审核。

## 9. 检查 Claim 与原文链路

### 9.1 命令

```sql
SELECT
  r.id,
  r.domain,
  r.epistemic_status,
  r.polarity,
  r.review_status,
  r.text,
  r.attributes_json,
  es.quote,
  es.start_char,
  es.end_char,
  p.page_start,
  p.chunk_id,
  d.source_id,
  d.title
FROM records r
JOIN json_each(r.evidence_ids_json) ids
JOIN evidence_spans es ON es.id = ids.value
JOIN passages p ON p.id = es.passage_id
JOIN documents d ON d.id = p.document_id
WHERE r.record_type = 'Claim'
ORDER BY d.source_id, p.page_start
LIMIT 100;
```

检查指定主题：

```sql
SELECT
  r.id,
  r.text,
  r.attributes_json,
  es.quote,
  p.page_start,
  p.chunk_id,
  d.source_id
FROM records r
JOIN json_each(r.evidence_ids_json) ids
JOIN evidence_spans es ON es.id = ids.value
JOIN passages p ON p.id = es.passage_id
JOIN documents d ON d.id = p.document_id
WHERE r.record_type = 'Claim'
  AND (
    r.text LIKE '%卡瑞利珠单抗%'
    OR r.attributes_json LIKE '%卡瑞利珠单抗%'
  );
```

### 9.2 作用

人工核对以下内容：

- Claim.text 是否被 quote 直接支持；
- predicate、conditions、qualifiers 是否忠实；
- 否定、预测、估算和不确定性是否保留；
- 页码、chunk ID 和 source ID 是否可复核；
- 是否把公司判断、模型判断或原文事实混为一体。

### 9.3 本次自动检查结果

- 没有 Record 缺失有效 Evidence；
- 没有 Evidence quote 无法在 Passage 中找到；
- 没有 Evidence 字符范围与 quote 不一致；
- 789 条 Claim 全部具有 predicate；
- 546/789 条 Claim 具有 conditions，覆盖率 69.2%；
- 433/789 条 Claim 具有 qualifiers，覆盖率 54.9%。

自动完整性通过不代表语义忠实已经通过；Claim 仍需人工抽样逐条对照原文。

## 10. Observation 属性覆盖

### 10.1 命令

查看具体 Observation：

```sql
SELECT
  id,
  text,
  json_extract(attributes_json, '$.object') AS object,
  json_extract(attributes_json, '$.metric') AS metric,
  json_extract(attributes_json, '$.value') AS value,
  json_extract(attributes_json, '$.value_nature') AS value_nature,
  json_extract(attributes_json, '$.unit') AS unit,
  json_extract(attributes_json, '$.currency') AS currency,
  json_extract(attributes_json, '$.reporting_period') AS reporting_period,
  json_extract(attributes_json, '$.timepoint') AS timepoint,
  json_extract(attributes_json, '$.conditions') AS conditions
FROM records
WHERE record_type = 'Observation'
LIMIT 100;
```

统计字段覆盖：

```sql
SELECT
  COUNT(*) AS total,
  SUM(json_type(attributes_json, '$.metric') IS NOT NULL) AS has_metric,
  SUM(json_type(attributes_json, '$.value') IS NOT NULL) AS has_value,
  SUM(json_type(attributes_json, '$.value_nature') IS NOT NULL) AS has_value_nature,
  SUM(json_type(attributes_json, '$.object') IS NOT NULL) AS has_object,
  SUM(json_type(attributes_json, '$.unit') IS NOT NULL) AS has_unit,
  SUM(json_type(attributes_json, '$.reporting_period') IS NOT NULL) AS has_period,
  SUM(json_type(attributes_json, '$.timepoint') IS NOT NULL) AS has_timepoint
FROM records
WHERE record_type = 'Observation';
```

### 10.2 作用

判断 Observation 是否真的保留了可比较所需的数值、单位、时间和口径，而不只是满足
`metric/value/value_nature` 最低 Schema。

### 10.3 本次结果

| 字段 | 覆盖 | 覆盖率 |
| --- | ---: | ---: |
| metric | 3,946/3,946 | 100.0% |
| value | 3,946/3,946 | 100.0% |
| value_nature | 3,946/3,946 | 100.0% |
| object | 3,934/3,946 | 99.7% |
| unit | 41/3,946 | 1.0% |
| reporting_period | 6/3,946 | 0.2% |
| timepoint | 78/3,946 | 2.0% |

Document 层 reporting_period 可以覆盖一部分报告期语义，但不能替代 Record 级时间点、单位
和口径。当前结果满足最低 Schema，却不足以证明 Observation 已具备稳定的跨文档可比性。

## 11. Event 属性覆盖

### 11.1 命令

```sql
SELECT
  COUNT(*) AS total,
  SUM(json_type(attributes_json, '$.event_type') IS NOT NULL) AS has_event_type,
  SUM(json_type(attributes_json, '$.event_time') IS NOT NULL) AS has_event_time,
  SUM(json_type(attributes_json, '$.disclosure_time') IS NOT NULL) AS has_disclosure_time,
  SUM(json_type(attributes_json, '$.prior_status') IS NOT NULL) AS has_prior_status,
  SUM(json_type(attributes_json, '$.new_status') IS NOT NULL) AS has_new_status
FROM records
WHERE record_type = 'Event';
```

### 11.2 作用

检查事件类型、有效时间、披露时间和状态变化是否进入结构化字段，以防把预测、披露和真实
发生时间混为同一时间轴。

### 11.3 本次结果

| 字段 | 覆盖 | 覆盖率 |
| --- | ---: | ---: |
| event_type | 185/185 | 100.0% |
| event_time | 185/185 | 100.0% |
| disclosure_time | 180/185 | 97.3% |
| prior_status | 184/185 | 99.5% |
| new_status | 184/185 | 99.5% |

字段覆盖较高，但仍需抽样确认 event_time 是否被模型用公告日期错误补齐。

## 12. 实体消歧质量

### 12.1 命令

```sql
SELECT entity_type, resolution_status, COUNT(*) AS n
FROM entities
GROUP BY entity_type, resolution_status
ORDER BY entity_type, n DESC;
```

检查同类型同名重复实体：

```sql
SELECT
  entity_type,
  canonical_name,
  COUNT(*) AS n,
  GROUP_CONCAT(id) AS ids,
  GROUP_CONCAT(resolution_status) AS statuses
FROM entities
GROUP BY entity_type, lower(trim(canonical_name))
HAVING COUNT(*) > 1
ORDER BY n DESC;
```

检查没有任何边的 Entity：

```sql
SELECT COUNT(*) AS isolated_entities
FROM entities n
WHERE NOT EXISTS (
  SELECT 1 FROM edges e
  WHERE e.source_id = n.id OR e.target_id = n.id
);
```

### 12.2 作用

- 判断跨文档实体是否真正统一；
- 发现相同公司、药物或证券被分裂为多个节点；
- 发现构建替换后遗留的无边 Entity；
- 防止错误实体路径污染 Graph 检索。

### 12.3 本次结果

| resolution_status | 数量 | 占比 |
| --- | ---: | ---: |
| temporary | 1,626 | 89.0% |
| needs_review | 191 | 10.5% |
| resolved | 9 | 0.5% |

其他结果：

- 孤立 Entity：360；
- 同类型同规范名重复组：4；
- `江苏恒瑞医药股份有限公司`：3 个 Entity ID；
- `Kailera`：3 个 Entity ID；
- `九强转债`：3 个 Entity ID；
- `北京九强生物技术股份有限公司`：2 个 Entity ID。

该项不通过。当前图的实体连接数量不能解释为已完成跨文档身份统一。

## 13. 实体角色与关系候选覆盖

### 13.1 命令

```sql
SELECT role, COUNT(*) AS n
FROM record_entities
GROUP BY role
ORDER BY n DESC;

SELECT
  COUNT(*) AS relation_index_rows,
  COUNT(DISTINCT record_id) AS indexed_records,
  COUNT(DISTINCT entity_id) AS indexed_entities
FROM record_entities;

SELECT COUNT(*) AS records_without_relation_index
FROM records r
WHERE NOT EXISTS (
  SELECT 1 FROM record_entities re
  WHERE re.record_id = r.id
);
```

检查开放角色词表：

```sql
SELECT relation, COUNT(*) AS n
FROM edges
WHERE relation NOT IN (
  'CONTAINS', 'YIELDS', 'SUPPORTS_RECORD',
  'EQUIVALENT_TO', 'SUPPORTS', 'SUPPLEMENTS',
  'INCOMPARABLE_WITH', 'CONTRADICTS', 'SUPERSEDES', 'SAME_ORIGIN_AS'
)
GROUP BY relation
ORDER BY n DESC, relation;
```

### 13.2 作用

- 验证角色边是否被正确写入关系候选索引；
- 检查候选阻塞是否覆盖足够的 Record；
- 发现模型自由生成的非法、重复或同义角色。

### 13.3 本次结果

`record_entities` 当前结果：

```text
总行数：834
唯一 Record：582
唯一 Entity：264
唯一 role：ABOUT
未进入该索引的 Record：4,338
Record 覆盖率：11.8%
```

与此同时，实际角色边共有 7,018 条、133 种 relation。高频角色包括
`REPORTING_ENTITY`、`SUBJECT`、`ABOUT`、`INDICATION`、`INVESTIGATIONAL_DRUG`；同时出现
`2026`、`OTHER`、`SOURCE`、`PERSON` 等不受控谓词。

根因是当前 `GraphStore.replace_document_bundle` 只把 `HAS_*`、`ABOUT`、`IN_TRIAL`、
`FOR_INDICATION`、`AFFECTS` 写入 `record_entities`，但模型实际输出大量
`REPORTING_ENTITY`、`SUBJECT` 等角色。因此角色边存在于 `edges`，却没有进入候选关系
阻塞索引。

该项为 P0 缺陷：七类关系不是基于全部合理实体角色生成，当前 623 个候选对不能代表全图
关系覆盖。

## 14. 七类语义关系检查

### 14.1 命令

```sql
SELECT
  COALESCE(relation, 'NO_RELATION') AS relation,
  COUNT(*) AS n
FROM relation_pair_reviews
GROUP BY relation
ORDER BY n DESC;
```

查看关系两端、置信度、理由和可比性：

```sql
SELECT
  ed.relation,
  left_record.record_type AS left_type,
  left_record.text AS left_text,
  right_record.record_type AS right_type,
  right_record.text AS right_text,
  ra.confidence,
  ra.reason,
  ra.comparability_json
FROM edges ed
JOIN records left_record ON left_record.id = ed.source_id
JOIN records right_record ON right_record.id = ed.target_id
JOIN relation_assessments ra ON ra.edge_id = ed.id
ORDER BY ed.relation, ra.confidence DESC;
```

检查重复关系对和自环：

```sql
SELECT
  CASE
    WHEN source_id < target_id THEN source_id || '|' || target_id
    ELSE target_id || '|' || source_id
  END AS pair,
  COUNT(*) AS n
FROM edges
WHERE relation IN (
  'EQUIVALENT_TO', 'SUPPORTS', 'SUPPLEMENTS',
  'INCOMPARABLE_WITH', 'CONTRADICTS', 'SUPERSEDES', 'SAME_ORIGIN_AS'
)
GROUP BY pair
HAVING COUNT(*) > 1;

SELECT COUNT(*) AS semantic_self_loops
FROM edges
WHERE source_id = target_id
  AND relation IN (
    'EQUIVALENT_TO', 'SUPPORTS', 'SUPPLEMENTS',
    'INCOMPARABLE_WITH', 'CONTRADICTS', 'SUPERSEDES', 'SAME_ORIGIN_AS'
  );
```

### 14.2 作用

- 检查七类关系是否只来自已阻塞候选；
- 检查 `SUPERSEDES` 是否被误判为 `CONTRADICTS`；
- 检查 confidence、reason、comparability 是否可审核；
- 检查重复标签、自环和无关系评审是否正确保存。

### 14.3 本次结果

| 关系 | 数量 |
| --- | ---: |
| NO_RELATION | 449 |
| INCOMPARABLE_WITH | 89 |
| SUPPLEMENTS | 72 |
| EQUIVALENT_TO | 9 |
| SUPPORTS | 3 |
| SUPERSEDES | 1 |
| CONTRADICTS | 0 |
| SAME_ORIGIN_AS | 0 |

完整性结果：

- 174 条已接受关系全部存在 relation assessment；
- 语义关系重复记录对：0；
- 语义关系自环：0；
- 所有关系均来自允许枚举。

但是关系语义质量尚不能判定为通过：候选索引只覆盖 11.8% Record，且
`INCOMPARABLE_WITH + SUPPLEMENTS` 占已接受关系的 92.5%，需要领域人员进行分层抽样。

## 15. 结构完整性 SQL

### 15.1 Evidence quote 与字符范围

```sql
SELECT COUNT(*) AS quote_not_found
FROM evidence_spans es
JOIN passages p ON p.id = es.passage_id
WHERE instr(p.text, es.quote) = 0;

SELECT COUNT(*) AS wrong_char_span
FROM evidence_spans es
JOIN passages p ON p.id = es.passage_id
WHERE substr(
  p.text,
  es.start_char + 1,
  es.end_char - es.start_char
) <> es.quote;
```

### 15.2 Passage、Evidence 与 Edge 孤儿检查

```sql
SELECT COUNT(*) AS orphan_passage_document
FROM passages p
LEFT JOIN documents d ON d.id = p.document_id
WHERE d.id IS NULL;

SELECT COUNT(*) AS orphan_evidence_passage
FROM evidence_spans es
LEFT JOIN passages p ON p.id = es.passage_id
WHERE p.id IS NULL;

WITH nodes AS (
  SELECT id FROM documents
  UNION SELECT id FROM passages
  UNION SELECT id FROM evidence_spans
  UNION SELECT id FROM records
  UNION SELECT id FROM entities
)
SELECT
  SUM(source_node.id IS NULL) AS orphan_edge_source,
  SUM(target_node.id IS NULL) AS orphan_edge_target
FROM edges e
LEFT JOIN nodes source_node ON source_node.id = e.source_id
LEFT JOIN nodes target_node ON target_node.id = e.target_id;
```

### 15.3 Record Evidence 引用

```sql
SELECT COUNT(*) AS records_without_valid_evidence
FROM records r
WHERE NOT EXISTS (
  SELECT 1
  FROM json_each(r.evidence_ids_json) ids
  JOIN evidence_spans es ON es.id = ids.value
);
```

### 15.4 作用

验证任何 Record 是否都能通过 Evidence 和 Passage 回到原文，并确认图边没有悬空端点。

### 15.5 本次结果

以上所有检查结果均为 0：

- quote_not_found：0；
- wrong_char_span：0；
- orphan_passage_document：0；
- orphan_evidence_passage：0；
- orphan_edge_source：0；
- orphan_edge_target：0；
- records_without_valid_evidence：0。

这说明已经落库的图对象满足结构溯源约束。它不覆盖被构建 gate 拒绝、没有落库的候选记录。

## 16. NetworkX 图投影检查

### 16.1 总体统计命令

```powershell
@'
from collections import Counter
import networkx as nx
from src.graph_rag import GraphStore

store = GraphStore(
    "outputs/.graph_cache/graphrag_full_pdf_v2.sqlite3"
)
graph = store.to_networkx()

print("nodes:", graph.number_of_nodes())
print("edges:", graph.number_of_edges())
print("nodes_by_type:", Counter(
    data.get("node_type")
    for _, data in graph.nodes(data=True)
))
print("isolates:", len(list(nx.isolates(graph))))
print("top_degree:", sorted(
    graph.degree,
    key=lambda item: item[1],
    reverse=True,
)[:20])
'@ | python -X utf8 -
```

### 16.2 作用

- 验证 SQLite 能完整重建 NetworkX 投影；
- 查看各类节点规模、孤立节点和超级枢纽；
- 发现发行人等泛实体是否主导图遍历。

### 16.3 本次结果

```text
nodes = 11,783
edges = 17,146
Document = 3
Passage = 449
Evidence = 4,585
Record = 4,920
Entity = 1,826
isolates = 360，全部为 Entity
```

最高度数实体：

- 北京九强生物技术股份有限公司：degree 1,924；
- 江苏恒瑞医药股份有限公司：degree 1,669；
- 另一个江苏恒瑞医药股份有限公司节点：degree 166。

发行人实体成为超级枢纽，同时同一发行人被拆为多个实体 ID。全图直接进行力导向可视化会
被这些枢纽主导，不能用于判断局部语义质量。

## 17. 导出可读的局部子图

### 17.1 命令

下面示例只导出名称包含“恒瑞医药”的 Entity 两跳子图：

```powershell
@'
from pathlib import Path
import networkx as nx
from src.graph_rag import GraphStore

store = GraphStore(
    "outputs/.graph_cache/graphrag_full_pdf_v2.sqlite3"
)
graph = store.to_networkx()
undirected = graph.to_undirected()

seeds = [
    node_id
    for node_id, data in graph.nodes(data=True)
    if "恒瑞医药" in str(data.get("canonical_name", ""))
]

selected = set()
for seed in seeds:
    selected.update(
        nx.single_source_shortest_path_length(
            undirected,
            seed,
            cutoff=2,
        )
    )

subgraph = graph.subgraph(selected).copy()

for _, data in subgraph.nodes(data=True):
    for key, value in list(data.items()):
        if value is None:
            data[key] = ""
        elif not isinstance(value, (str, int, float, bool)):
            data[key] = str(value)

for _, _, _, data in subgraph.edges(keys=True, data=True):
    for key, value in list(data.items()):
        if value is None:
            data[key] = ""
        elif not isinstance(value, (str, int, float, bool)):
            data[key] = str(value)

output = Path("outputs/.graph_cache/hengrui_2hop.gexf")
nx.write_gexf(subgraph, output)
print(output, subgraph.number_of_nodes(), subgraph.number_of_edges())
'@ | python -X utf8 -
```

### 17.2 作用

- 输出可由 Gephi 或 Cytoscape 打开的 GEXF；
- 按实体检查 Document→Passage→Evidence→Record→Entity 路径；
- 查看七类关系是否连接到合理 Record；
- 避免把 11,783 个节点一次性画在一张不可读的图上。

### 17.3 推荐显示规则

| 图对象 | 推荐样式 |
| --- | --- |
| Document | 深蓝色矩形 |
| Passage | 浅蓝色矩形 |
| Evidence | 灰色小节点 |
| Claim | 橙色圆点 |
| Observation | 绿色圆点 |
| Event | 红色圆点 |
| Entity | 紫色菱形 |
| 确定性组织边 | 浅灰细线 |
| 实体角色边 | 蓝灰线 |
| 七类语义关系 | 按类型着色的粗线 |
| candidate | 实线但低透明度 |
| rejected/superseded | 隐藏或虚线 |

建议先按 source ID、Entity、Record 类型、页码和关系类型过滤，再取 1～2 跳。当前全图直接
可视化没有审计价值。

## 18. 建图质量问题清单

### P0-1：关系候选索引与实际角色边不一致

**证据**

- 实际角色边：7,018；
- 实际角色 relation：133 种；
- `record_entities`：834 行、只含 `ABOUT`；
- 只有 582/4,920 条 Record 进入关系候选索引。

**影响**

七类关系预计算只覆盖图的一小部分。当前 174 条关系的结构完整性没有问题，但不能代表全库
应有关系已经发现。

**建议修复**

- 定义封闭的语义角色枚举及同义角色归一表；
- 所有合法 Record→Entity 角色边均写入 `record_entities`；
- 候选阻塞直接基于规范化角色/实体类型，而不是当前少数关系名白名单；
- 重建后检查 Record 候选索引覆盖率。

**验收标准**

- 不再出现数字、PERSON、SOURCE 等非法角色；
- 有合法实体角色边的 Record 全部进入关系索引；
- 候选增加后仍保持阻塞，不进行 N² 全库比较；
- 七类关系重新计算并重新评测。

### P0-2：实体身份没有完成跨文档统一

**证据**

- resolved 仅 9/1,826；
- temporary 1,626；
- needs_review 191；
- 360 个孤立 Entity；
- 恒瑞医药、九强生物、Kailera、九强转债存在同名多 ID。

**影响**

- 相同实体的跨文档记录不能稳定连通；
- 同一发行人出现多个子图；
- 多跳检索可能漏召回；
- 关系候选被实体分裂进一步压低。

**建议修复**

- 为公司和证券建立稳定登记 ID；
- 统一 Company/Security 边界；
- 修复“已有无 external ID resolved 实体”仍生成不同稳定 ID 的逻辑；
- 替换文档 bundle 时清理不再有边的临时 Entity；
- 增加同类型规范名重复 gate。

**验收标准**

- 受控语料中的发行人、证券、核心药物和试验均有唯一 ID；
- 同类型规范名重复必须解释为合法同名，否则阻断构建；
- 孤立 Entity 为 0；
- temporary/needs_review 比例纳入持续监控。

### P1-1：Observation 条件字段不足

**证据**

- unit 1.0%；
- reporting_period 0.2%；
- timepoint 2.0%。

**影响**

数值可能保存在自由文本或 conditions 内，无法稳定进行单位、报告期和时间点对齐，进而影响
`EQUIVALENT_TO`、`INCOMPARABLE_WITH` 和 `CONTRADICTS`。

**建议修复**

- 对 financial/clinical Observation 定义 Profile 级条件字段；
- 对文本中明确出现但结构字段缺失的单位、期间和时间点拒绝或降级；
- 区分 Document reporting period 与具体 Observation timepoint。

### P1-2：全部 Record 未经人工审核

**证据**

4,920 条 Record 全部为 candidate。

**影响**

图可用于导航，但不能把结构化记录或关系直接视为已核实事实。Retriever 当前还不会硬过滤
rejected/superseded，审核状态必须在生成前再次检查。

**建议修复**

- 提供 Record/Evidence 并排审核界面；
- 支持批量 approved/rejected/superseded；
- 正式报告对 candidate 继续强制回到 Passage 原文；
- 关系边增加与 Record 对等的人工审核接口。

### P1-3：拒绝统计没有完整分母

**证据**

成功 run 记录了 168 次 quote 拒绝和 60 条非法记录拒绝，但没有 attempted record 总数。

**影响**

无法比较不同模型、提示词或文档类型的有效抽取率，也无法判断某份 PDF 是否异常。

**建议修复**

BuildReport 增加 attempted、accepted、schema_rejected、quote_rejected、entity_resolution_failed、
relation_rejected 和 retry_count。

### P2-1：图统计文档缺少快照身份

**证据**

文档曾同时记录 17,077 条总边和 174 条语义关系；当前分解证明最终总边应为 17,146。

**建议修复**

每次验收同时记录数据库哈希、最后 build run ID、统计时间、模型、Schema 和抽取器版本。

## 19. 后续重建验收门槛

修复后至少重新执行本报告全部命令，并满足：

- [ ] 所有结构完整性查询为 0；
- [ ] 所有已落库 Record 都有有效 Evidence；
- [ ] 非法角色 relation 为 0；
- [ ] 有角色边的 Record 全部进入 `record_entities`；
- [ ] 受控核心实体不存在无解释的同类型同名多 ID；
- [ ] 孤立 Entity 为 0；
- [ ] Observation 的单位、时间和报告期缺失率有领域合理解释；
- [ ] 七类关系在修复后的完整阻塞候选上重算；
- [ ] `SUPERSEDES`、`CONTRADICTS`、`INCOMPARABLE_WITH` 完成人工抽样；
- [ ] Record 与 Edge 审核状态可操作并被 Retriever 正确执行；
- [ ] Chunk/Graph/Hybrid 在相同 source scope、top-k 和问题集上重新评测；
- [ ] 报告引用仍回到 Passage/Evidence，不直接引用图记录；
- [ ] 验收记录包含数据库哈希和最后 build run ID。

## 20. 建议的只读 Graph Inspector

当前检查依赖 SQL 和脚本。为了让领域成员能够实际审核，建议后续增加只读 Inspector：

1. **Overview**：节点/边、拒绝率、审核状态、实体消歧和属性覆盖；
2. **Record & Evidence**：结构化字段、原文 quote、完整 Passage、页码并排；
3. **Entity Review**：同名候选、别名、外部 ID、相邻 Record 和合并风险；
4. **Relation Review**：左右 Record、两侧原文、comparability、reason 和审核按钮；
5. **Local Graph**：按 source/entity/relation 过滤的 1～2 跳子图；
6. **Build Runs**：模型、版本、错误类型、重试、耗时和调用量；
7. **Export**：CSV、JSON、GEXF 和脱敏质量报告。

Inspector 必须只通过受控接口更新 review status，不允许直接编辑原文、稳定 ID 或来源元数据。

## 21. 最终判定

本次全量 GraphRAG 构建已经证明：

- 完整 PDF 可以先解析为分页文本再构图；
- 三层节点和原文链路能够稳定持久化；
- SQLite 可以重建 NetworkX 图；
- Graph/Hybrid 能消费这些节点并回到可引用 Passage；
- 七类关系具有可审计的理由和可比性记录。

但本次检查同时证明，当前图尚未达到“高质量语义图”验收标准。最优先修复项不是扩大节点
数量，而是统一实体身份、收敛角色词表、修复 `record_entities` 候选索引覆盖，并建立审核
闭环。完成这些修复前，图的正确定位是 **Evidence 导航与召回增强层**，不是可以脱离原文
独立回答问题的事实知识库。
