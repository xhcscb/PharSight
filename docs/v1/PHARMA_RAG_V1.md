# Pharsight 医药投研 RAG V1 研究与实施方案

> **执行优先级说明（2026-09-21）**：本方案保留为后续证据检索与图改造的设计基础。`DataCollector -> DataAnalyzer -> ReportGenerator` 的医药化代码适配已完成，当前应先完成真实端到端和领域验收；新增 `EvidenceBackend`、构图或替换检索链路不作为该验收的前置任务。

## 0. 文档控制

| 项目 | 内容 |
| --- | --- |
| 文档目的 | 将组会形成的 V1 研究思路转化为可开发、可测试、可复现的最小实施方案 |
| 文档状态 | 后续 RAG/图改造拟议设计；主流程实现状态见实现说明 |
| 适用版本 | Pharsight V1 最小闭环 |
| 首个研究模式 | A/H 上市医药公司研究 |
| 默认输出语言 | 中文 |
| 标准运行时 | Python 3.11 |
| FinSight 基线 | `8fc5bce28ac201c713219a47d30367c6a84342f4` |
| 关联文档 | [项目管理](../PROJECT_MANAGEMENT.md)、[总体架构](../ARCHITECTURE.md) |

本文定义后续检索与图改造方向。受控清单工具、医药提示词和三段式主流程已实现，`EvidenceBackend`、图谱、Claim 级检索与对照评测尚未实现；具体状态以[实现说明](./IMPLEMENTATION_SUMMARY_2026-09-21.md)、项目管理任务和测试证据为准。文中的英文专有名词、类名、字段名、状态值及文件路径是接口标识，中文叙述不改变其拼写。

## 1. 执行摘要

V1 不以“生成完整券商研报”为目标，而是选择一个受控的医药投研问题，在少量固定文档上跑通：

```text
文档 -> 解析 -> 信息抽取 -> Claim–Evidence 构图
     -> Chunk/Graph 检索 -> 生成带精确引用的结果 -> 自动校验
```

对 FinSight 的最小改造原则是：

1. 保留其配置、LLM、Memory 和 DataAnalyzer 等可复用主干。
2. 在 DataAnalyzer 前增加稳定的 `EvidenceBackend` 检索边界。
3. 用单一 `PharmaEvidenceSearchTool` 接入医药证据，第一阶段由薄入口确定性调用，不让通用 DataCollector 自主选择大量工具。
4. 以结构化 `EvidenceResult` 把证据 ID、来源、定位信息和检索路径送入 Memory。
5. 沿用已实现的 `[Source:<source_id>]` 精确引用和程序化校验，医药模式不得回退到 ReportGenerator 的模糊来源匹配。
6. 先实现 Chunk 基线，再在同一接口后接入 Graph/Hybrid；图数据库和检索算法不得渗透到生成提示词。
7. V1 关闭图表、代码执行和完整报告生成，仅输出可审计的问答或比较结果。

这条路径把改动集中在少量新模块和两个小型兼容补丁中，能够控制上游合并成本，也为后续 Claim–Evidence 图、Neo4j 投影和混合检索预留稳定接口。

## 2. 研究背景与阶段判断

### 2.1 组会材料形成的共识

本方案综合了工作区 `docs/manual/meeting1_0826/` 下的组会总结、GraphRAG 汇报、讨论记录和数据层进展说明。材料不随 Pharsight 仓库分发，实施时应将其视为只读研究输入。

| 只读研究材料 | 在本方案中的用途 |
| --- | --- |
| `第一次组会总结｜阶段目标、分工与第一阶段交付(1).pdf` | 阶段目标、首批交付、任务分工和完成边界 |
| `面向医药投研的GraphRAG（组会1）(2).pptx` | FinSight、GraphRAG、HippoRAG、G-reasoner 等路线比较及研究重点 |
| `组会1.docx` | “基于 FinSight 修改、先组合现成工具、优先图构建”的讨论结论 |
| `Meeting1数据层工作.docx` | 外部数据层能力、证据定位现状、候选关系状态和未完成质量门槛 |

主要共识是：

- 先组合和复用成熟部件，避免第一阶段自行实现完整基础设施。
- 图构建和证据组织是当前研究重点，完整 Benchmark 和复杂模型可后置。
- 首批语料控制在约 10–20 份，至少形成 5 份稳定解析样例。
- 首批人工资产包括 Schema V1、5 个 Claim–Evidence 标注样例、实体别名表和基础测试。
- 验证问题应覆盖事实问答、跨文档比较、证据追溯和冲突识别。
- 研究对照不是“有图就更先进”，而是同一语料与问题下，Graph/Hybrid 是否相对 Chunk 基线产生可测量收益。

### 2.2 当前工程基础的两层状态

必须区分 Pharsight 仓库与组会材料中描述的数据层原型：

1. **Pharsight 当前状态**：V1 医药化主流程和受控清单工具已实现并通过无网络测试，但尚无完成真实模型与领域验收的医药运行版本。
2. **外部数据层原型**：材料描述了 PDF/OCR、元素抽取、候选断言和证据定位能力，但候选关系尚未形成充分人工批准的事实集，实体链接和语义检索也仍有明显基线性质。

因此 V1 不复制外部数据层的完整技术栈，而是把它视为可替换的证据生产者：优先通过明确 API 或版本化 JSONL 导出接入。若外部接口暂时不可用，先用小型、本地、可再分发的 JSONL fixture 跑通闭环。

### 2.3 研究问题

主研究问题：

> 在相同冻结语料、相同查询和相同生成器条件下，Claim–Evidence 图增强检索是否比普通 Chunk RAG 更好地支持跨文档比较、证据追溯和冲突识别？

子问题包括：

- 图扩展是否提高人工批准证据的召回率？
- 图扩展是否引入更多无关证据或未经审核的候选关系？
- 严格证据 ID 引用是否能阻断伪造来源和错配引用？
- 图检索收益是否值得其额外延迟和数据治理成本？

### 2.4 研究假设

| ID | 假设 | 可证伪观察 |
| --- | --- | --- |
| H1 | Claim–Evidence 图能提高跨文档比较所需证据的召回 | Graph/Hybrid 在固定 top-k 下不优于 Chunk，或提升无统计/实践意义 |
| H2 | 严格证据 ID 和程序化参考文献渲染能显著降低错引 | 仍出现未知 ID、引用无法定位或引用不支持陈述 |
| H3 | 将图封装在后端接口后，可以不修改生成器完成策略切换 | Graph 接入必须改变提示词数据模型或生成器内部逻辑 |
| H4 | 少量人工批准 Claim 足以验证图路径价值 | 5 个批准样例无法覆盖任何真实跨文档路径或冲突场景 |

## 3. V1 范围

### 3.1 首个用例

建议选择：

- 一家 A 股或 H 股上市医药公司；
- 一个核心药物或候选药物；
- 一个明确适应症；
- 5–10 份以文本为主、允许项目使用的固定文档；
- 10 个冻结问题，其中至少包含 3 个事实题、3 个跨文档比较题、1 个冲突题、1 个缺失证据题。

示例问题类型：

- 截止指定日期，该药物在目标适应症中的临床阶段是什么？
- 公司公告与试验登记对计划入组人数、主要终点或时间表的描述是否一致？
- 不同时间披露的研发进度发生了什么变化？
- 哪些结论有一手来源支持，哪些只能作为模型判断？

具体公司、药物、适应症及信息截止时间必须由项目负责人确认后，语料任务才能进入 `Ready`。

### 3.2 输入边界

V1 接受：

- 依法可访问的公司公告、年报、交易所披露；
- 监管记录和临床试验登记；
- 必要时使用的同行评审文献；
- 每份文档的 manifest，包括来源、发布日期、抓取时间、许可说明、文件哈希和研究截止时间。

V1 默认不接受：

- 包含个人健康信息或未公开患者级数据的材料；
- 未获授权的付费原始数据库导出；
- 来源、日期或使用权限不明的文件；
- 默认路径下需要复杂 OCR、图片语义或跨页表格才能正确解释的扫描件。

若首个用例必须处理扫描 PDF，应由外部数据层完成 OCR，并将文本、页码和坐标定位作为证据导出；V1 不在 Pharsight 内重新编排完整 MinerU/OCR 服务。

### 3.3 输出边界

V1 输出：

- `answer.md`：结构化中文回答，逐项带精确证据引用；
- `answer.json`：便于自动评估的结构化结论；
- `evidence.json`：本次实际使用的证据及来源元数据；
- `retrieval_trace.json`：检索策略、查询、过滤条件、候选和图路径；
- `run_manifest.json`：代码版本、配置摘要、语料快照、截止时间和模型信息。

运行产物仅写入被 `.gitignore` 覆盖的 `outputs/` 或 `evaluations/`，不得提交真实运行结果、原始受限数据、缓存或密钥。

### 3.4 明确不做

- 不生成完整行业或公司研报。
- 不生成图表，不运行 VLM 图表审查。
- 不实现财务预测、估值、市场规模或临床成功率模型。
- 不接入全部监管、医保、集采、文献和交易所数据源。
- 不训练 NER、Embedding、GNN 或其他专用模型。
- 不要求构建完整医药本体。
- 不将 130 条候选关系直接视为可信事实。
- 不以 Neo4j、Milvus 等中间件是否部署作为闭环完成标准。

## 4. FinSight 现状分析与复用策略

### 4.1 可复用部分

| FinSight 部件 | V1 用法 | 理由 |
| --- | --- | --- |
| `Config` | 保留，增加安全落盘修正 | 集中管理模型和运行目录，但当前配置落盘需要防止密钥泄漏 |
| `AsyncLLM` | 保留 | 继续使用既有模型适配层 |
| `Memory` | 保留为运行态结果容器 | `EvidenceResult` 可保持 `ToolResult` 兼容，减少主干改动 |
| `Tool`/`ToolResult` | 扩展而非替换 | 新医药检索工具可沿用注册和结果流转方式 |
| `DataAnalyzer` | 作为 V1 文本生成器 | 已能接收 Memory 数据并支持无图表提示词路径 |
| checkpoint/工作目录机制 | 保留 | 有助于复现和调试，但必须遵循忽略规则和脱敏要求 |

### 4.2 暂不使用部分

| FinSight 部件 | V1 处理 | 原因 |
| --- | --- | --- |
| 通用 `DataCollector` 自主规划 | 入口脚本直接调用唯一医药证据工具 | 默认工具集合过宽，MVP 需要确定性和可测性 |
| `Memory.retrieve_relevant_data` | 不作为文档 RAG 主检索器 | 当前以整个 `ToolResult.brief_str()` 为粒度，缺少稳定证据定位和过滤语义 |
| `ReportGenerator._add_reference` | 绕开 | 其自由文本来源占位符通过相似度映射，不能保证逐条主张与证据准确对应 |
| `IndexBuilder` | 保持不变 | 同时服务已有报告引用和图片映射；直接替换回归面过大 |
| 图表与 VLM 循环 | `enable_chart=False` | 与文本证据闭环无关，增加依赖和不确定性 |
| 完整报告入口 | 不调用 | V1 输出是结构化问答，不是完整研报 |

### 4.3 当前 RAG 局限

FinSight 当前的相关数据召回以完整工具结果为单位，Embedding 缓存键主要由名称和描述组成；DataAnalyzer 的相关召回调用目前被注释，实际路径会格式化全部采集结果。ReportGenerator 则把模型生成的来源描述再次映射到候选数据，即便没有超过阈值的候选，也存在回退到最高排名项的行为。

这些机制适合通用自动研报演示，但不满足医药投研的四项要求：

- Claim 级证据绑定；
- 文档页码或字符区间定位；
- 时间、适应症、地区和状态限定；
- 引用错误时硬失败而非语义猜测。

相关代码证据如下，行号以当前固定基线为参考，后续同步上游时应重新核对：

| 观察 | 当前位置 |
| --- | --- |
| `ToolResult` 只有通用名称、描述、数据和来源字段，普通预览会截断复杂数据 | `src/tools/base.py:48` |
| Memory 对整个工具结果摘要生成 Embedding，并以名称加描述作为缓存键 | `src/memory/variable_memory.py:414` |
| DataAnalyzer 的相关数据召回被注释，当前格式化全部采集结果 | `src/agents/data_analyzer/data_analyzer.py:144` |
| DataAnalyzer 的执行器准备默认使用第一个工具作为 DeepSearch | `src/agents/data_analyzer/data_analyzer.py:77` |
| ReportGenerator 对模型写出的来源占位符做语义匹配，并存在最高候选回退 | `src/agents/report_generator/report_generator.py:438` |
| Config 将展开后的完整配置写入运行目录 | `src/config/config.py:89` |

### 4.4 最小改动边界

V1 预计只需要两处小型核心兼容修改：

1. DataAnalyzer 在 `enable_code=False` 时跳过代码执行器准备，并在对应提示词中禁止执行动作。
2. DataAnalyzer 不再假定 `tool_list[0]` 必然是 DeepSearch；无 DeepSearch 配置应安全运行。

另有一项实施前安全修正：Config 写入运行目录的配置快照时，必须删除或掩码 API key、token、authorization 和同类敏感字段。

其余能力通过隔离的新文件实现，不修改 ReportGenerator、IndexBuilder、VLM 和现有金融工具。

## 5. 目标架构

### 5.1 组件关系

```text
┌──────────────────────────────┐
│ 固定语料与清单               │
│ PDF/文本、哈希与截止时间     │
└──────────────┬───────────────┘
               │
               v
┌──────────────────────────────┐
│ 外部解析器/数据层            │
│ 解析 → 抽取 → 实体链接       │
│ → 主张、证据和图             │
└──────────────┬───────────────┘
               │ API 或版本化 JSONL
               v
┌──────────────────────────────┐
│ EvidenceBackend              │
│ 策略：分块/图/混合检索       │
└──────────────┬───────────────┘
               v
┌──────────────────────────────┐
│ PharmaEvidenceSearchTool     │
│ 超时、错误及溯源             │
└──────────────┬───────────────┘
               v
┌──────────────────────────────┐
│ Memory: EvidenceResult[]     │
│ 精确证据 ID                  │
└──────────────┬───────────────┘
               v
┌──────────────────────────────┐
│ DataAnalyzer 纯文本模式      │
│ 结构化带引用回答             │
└──────────────┬───────────────┘
               v
┌──────────────────────────────┐
│ 引用校验与渲染               │
│ 校验失败则阻断输出           │
└──────────────┬───────────────┘
               v
┌──────────────────────────────┐
│ 回答、证据及检索轨迹         │
└──────────────────────────────┘
```

### 5.2 设计原则

- **证据优先**：生成器消费证据，不消费不可追溯的知识摘要。
- **后端可替换**：JSONL、PostgreSQL、Neo4j 或向量库都在 `EvidenceBackend` 后面。
- **生成器无图感知**：生成器只认识 `EvidenceResult`，不认识节点、Cypher 或图数据库驱动。
- **默认失败可见**：缺失、冲突、超时和解析失败不得被模型补全。
- **快照可复现**：每次检索绑定语料和图快照。
- **时间截止一致**：检索和生成共同遵循 `as_of`。
- **候选不等于事实**：未审核 Claim 可以用于召回候选，但不能直接支撑肯定性结论。

## 6. 核心接口设计

### 6.1 `EvidenceBackend`

建议以 `Protocol` 或抽象基类定义：

```python
class EvidenceBackend(Protocol):
    def ingest(self, manifest: CorpusManifest) -> IngestSummary:
        ...

    def search(self, request: EvidenceSearchRequest) -> list[EvidenceHit]:
        ...
```

V1 的线上/异步实现可以扩展为 `async`，但对测试 fixture 应提供无网络、确定性的本地实现。

`EvidenceSearchRequest` 至少包含：

```json
{
  "query": "截至 2025-12-31，核心药物在目标适应症中的临床阶段是什么？",
  "strategy": "chunk",
  "top_k": 10,
  "as_of": "2025-12-31T23:59:59+08:00",
  "filters": {
    "company_ids": ["company:example"],
    "drug_ids": ["drug:example"],
    "indication_ids": ["indication:example"],
    "document_ids": []
  }
}
```

约束：

- `strategy` 只接受 `chunk`、`graph`、`hybrid`。
- `top_k` 设置合理上限，避免上下文失控。
- `as_of` 必填或由运行 manifest 提供，晚于截止时间的材料不得静默混入。
- 过滤器使用规范化 ID；原始名称只作为展示或候选解析输入。
- 后端必须返回当前 `snapshot_id`。

### 6.2 `EvidenceHit`

建议字段：

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| `evidence_id` | 是 | 一次语料快照内稳定、不可复用的证据 ID |
| `document_id` | 是 | 来源文档稳定 ID |
| `passage_id` | 是 | 文档内段落或元素 ID |
| `claim_id` | 否 | 审核过的 Claim ID；Chunk 基线可为空 |
| `title` | 是 | 文档标题 |
| `publisher` | 是 | 发布机构或发行人 |
| `source_url` | 是 | 原始 URL 或稳定记录地址 |
| `published_at` | 是 | 来源发布时间/更新时间，语义需注明 |
| `retrieved_at` | 是 | 项目获取时间 |
| `page` | 条件必需 | PDF 等分页材料的页码 |
| `char_start/end` | 条件必需 | 文本定位范围；与页码至少有一种可用 |
| `quote` | 是 | 有界、忠实的原文证据片段 |
| `entity_ids` | 是 | 片段涉及的规范化实体 |
| `qualifiers` | 是 | 适应症、地区、期间、单位、状态等限定 |
| `review_status` | 是 | `approved/candidate/rejected` 等审核状态 |
| `score` | 是 | 当前策略下的排序分数，不等同事实置信度 |
| `retrieval_path` | 是 | 向量命中或图扩展路径 |
| `snapshot_id` | 是 | 语料/索引/图快照版本 |

一个简化返回示例：

```json
{
  "evidence_id": "ev:doc-001:p12:003",
  "claim_id": "claim:drug-stage:001",
  "document_id": "doc:issuer-announcement:001",
  "passage_id": "passage:doc-001:p12:003",
  "title": "公司研发进展公告",
  "publisher": "示例公司",
  "source_url": "https://example.invalid/filing/001",
  "published_at": "2025-10-18T18:00:00+08:00",
  "retrieved_at": "2026-09-14T12:00:00+08:00",
  "page": 12,
  "char_start": 233,
  "char_end": 401,
  "quote": "……",
  "entity_ids": ["company:example", "drug:example", "indication:example"],
  "qualifiers": {
    "jurisdiction": "CN",
    "indication_id": "indication:example",
    "as_of": "2025-10-18"
  },
  "review_status": "approved",
  "score": 0.87,
  "retrieval_path": ["query", "passage:doc-001:p12:003"],
  "snapshot_id": "corpus:v1:2025-12-31"
}
```

示例域名和内容只用于说明结构，不是实际证据。

### 6.3 `EvidenceResult`

`EvidenceResult` 应保持与 FinSight `ToolResult` 的兼容性，让 Memory 现有的 `isinstance(..., ToolResult)` 路径继续工作。建议：

- `name` 使用稳定的证据短标题，不作为唯一键。
- `description` 表示证据主题和限定条件。
- `source` 用于人类可读来源，但权威元数据保存在结构化字段中。
- `data` 保存序列化后的 `EvidenceHit` 或其只读表示。
- `brief_str()` 输出完整、有长度上限的 quote、限定条件和精确引用令牌 `[Source:<source_id>]`。

不得依赖模型从自然语言来源名称反推 evidence ID。

### 6.4 `PharmaEvidenceSearchTool`

工具职责仅包括：

- 参数验证和实体过滤；
- 调用后端并处理超时；
- 将 `EvidenceHit` 转换为 `EvidenceResult`；
- 对空结果、后端错误、快照不匹配和截止时间违规给出可执行错误；
- 保留后端返回的全部溯源字段。

工具不负责：

- 生成最终结论；
- 把 `candidate` 自动提升为 `approved`；
- 隐藏冲突；
- 根据模型常识补充缺失字段。

## 7. 文档解析、抽取与构图边界

### 7.1 文档清单（manifest）

每份输入文档至少登记：

```text
document_id
local_runtime_path
source_url / stable_source_id
publisher
document_type
published_at
retrieved_at
sha256
license_or_terms_note
report_cutoff
parser_name / parser_version
```

本地真实文档和解析结果放在忽略目录；测试仓库仅保留经过授权、去敏、体积受控的 fixture。

### 7.2 解析输出

解析器输出应保留：

- 文档、页面、元素/段落三级 ID；
- 文本原文和规范化文本；
- 页码、字符区间，可能时保留坐标框；
- 标题层级、表格单元格关系和解析警告；
- 源文件哈希和解析器版本。

V1 只要求文本段落闭环。表格和图片可以保存定位信息，但不得在尚未可靠解析时生成确定性结论。

### 7.3 信息抽取

抽取分为三层：

1. **EntityMention**：原文出现的公司、药物、靶点、适应症、试验登记号等。
2. **CanonicalEntity**：规范化实体及别名映射。
3. **ClaimCandidate**：包含主语、关系、宾语/值、限定条件和证据片段的候选主张。

实体链接的建议顺序：

```text
规范名精确匹配
-> 别名精确匹配
-> 大小写/空格/标点/全半角规范化
-> 实体类型过滤
-> 向量候选
-> 上下文消歧
-> 无法确定则保持 unresolved
```

禁止为了提高覆盖率而强制链接低置信候选。

### 7.4 Claim 审核状态

至少支持：

- `candidate`：自动抽取，尚未人工或规则批准；
- `approved`：满足证据门控，可用于事实性生成；
- `rejected`：经审核不成立或抽取错误；
- `superseded`：被后续版本替代，但保留历史。

Chunk 基线可直接引用原文 Passage。Graph 路径若使用 Claim 支撑肯定性事实，该 Claim 必须为 `approved`；`candidate` 只可作为扩展线索，并最终回到可引用原文。

## 8. 主张—证据图结构 V1

### 8.1 节点

| 节点 | 核心属性 |
| --- | --- |
| `Document` | `document_id`、标题、发布者、日期、URL、哈希、许可说明 |
| `Passage` | `passage_id`、文本、页码/区间、解析版本 |
| `Claim` | `claim_id`、谓词、值、限定条件、审核状态、版本 |
| `Company` | 规范名、A/H 标识、交易所代码、别名 |
| `Drug` | 通用名、研发代号、商品名、别名 |
| `Target` | 标准靶点标识、名称、别名 |
| `Indication` | 标准疾病/适应症标识、名称、别名 |
| `Trial` | 登记号、阶段、地区、状态、更新时间 |

### 8.2 边

基础证据边：

- `Document -[CONTAINS]-> Passage`
- `Passage -[MENTIONS]-> Entity`
- `Claim -[EVIDENCED_BY]-> Passage`
- `Claim -[SUBJECT]-> Entity`
- `Claim -[OBJECT]-> Entity`

领域边可以包括：

- `Company -[DEVELOPS]-> Drug`
- `Drug -[TARGETS]-> Target`
- `Drug -[STUDIED_FOR]-> Indication`
- `Trial -[EVALUATES]-> Drug`
- `Trial -[ENROLLS_FOR]-> Indication`

关系必须携带必要限定条件，尤其是适应症、地区、临床阶段、事件日期和来源快照。不得把“某药在某适应症进入 III 期”压缩为无适应症和地区限定的全局药物状态。

### 8.3 证据关系

`SUPPORTS`、`REFUTES`、`SUPPLEMENTS` 等语义关系只在以下条件满足时进入可信图：

- 两端 Claim 均可回溯到 Passage；
- 时间、地区、适应症、单位和口径已对齐；
- 关系由人工审核或确定性规则批准；
- 审核人、时间和规则版本可追踪。

无法对齐的记录应作为“潜在冲突”返回，不能自动宣判某一来源错误。

## 9. 检索策略

### 9.1 分块检索（Chunk）基线

第一版必须先完成 Chunk 基线：

1. 对 Passage 建立可复现的向量或 BM25/混合索引。
2. 应用 `as_of` 和实体过滤器。
3. 返回 top-k Passage，对每个 Passage 构造 `EvidenceHit`。
4. 记录模型、索引参数、语料快照和原始分数。

若语义 Embedding 暂不可用，可以使用确定性词法检索建立工程基线，但必须在运行记录中标记，不能把哈希向量称为语义向量。

### 9.2 图检索（Graph）策略

Graph V1 不需要 GNN 或全局 PPR。建议流程：

1. Chunk/实体检索得到种子 Passage 或 Entity。
2. 在同一快照图中扩展 1–2 跳。
3. 通过 Claim 找到同主题、同适应症或有冲突关系的其他证据。
4. 排除 `rejected`；`candidate` 仅用于发现证据，不直接支撑最终事实。
5. 按相关性、来源等级、审核状态、时间一致性和路径长度重排。
6. 返回与 Chunk 完全相同的 `EvidenceHit` 数据结构。

### 9.3 混合检索（Hybrid）策略

Hybrid 可以采用：

```text
Chunk top-n 种子
-> Entity/Claim 图扩展
-> 候选合并与去重
-> 证据门控
-> rerank
-> top-k EvidenceHit
```

### 9.4 图后端适配原则

- PostgreSQL 或版本化 JSONL 可以作为事实主存储。
- Neo4j 是可替换的图投影，不是上层公共接口。
- 向量库是索引投影，不应成为证据唯一来源。
- 后端必须返回 `retrieval_path`，例如：

```json
[
  "query",
  "entity:drug:example",
  "claim:trial-stage:001",
  "passage:doc-002:p8:004"
]
```

- 切换 `chunk`、`graph`、`hybrid` 只能改变配置和后端行为，不得要求修改生成器。

## 10. 生成与引用控制

### 10.1 回答结构

为便于自动检查，V1 不生成长篇自由散文，建议输出：

```markdown
## 结论

- [来源事实] …… [Source:S001]
- [来源事实] …… [Source:S002] [Source:S003]
- [模型判断] 基于上述证据，…… [Source:S001] [Source:S003]

## 证据冲突与缺口

- ……

## 口径与截止时间

- ……

## 声明

- 本结果仅用于投资研究辅助，不构成投资建议或医疗建议。
```

标签至少包括：

- `[来源事实]`
- `[确定性计算]`
- `[明确假设]`
- `[模型判断]`

V1 尚未实现确定性计算时，不应为了形式完整而生成该类别内容。

### 10.2 引用规则

- 模型只能使用当前证据集合中出现的 `[Source:<source_id>]`。
- 每个事实性项目和表格行至少需要一个引用。
- 跨文档比较结论至少需要来自两个不同文档的证据。
- 引用必须支持相邻陈述，而不只是主题相关。
- 引用列表由程序根据 evidence metadata 生成，不能采用模型自由书写的标题或 URL。
- 引用证据晚于 `as_of` 时应阻断输出或明确标记为截止日后信息；默认行为是阻断。

### 10.3 校验器

`citation_validator.py` 至少执行：

1. 解析全部 `[Source:...]`。
2. 拒绝未知、空白、格式错误或重复拼接的 ID。
3. 检查每个事实性条目是否有引用。
4. 检查 Evidence 是否具备文档和页码/区间定位。
5. 检查 Evidence 的快照和截止时间是否与本次运行一致。
6. 对比较类问题检查文档多样性。
7. 输出机器可读的错误列表和覆盖率指标。
8. 校验失败时不发布 `answer.md` 为成功结果，而是保留失败状态供调试。

语义上的“引用是否真正支持陈述”需要人工或独立评测器复核；结构校验不能替代内容审核。

## 11. 入口与运行目录

### 11.1 薄入口 `run_pharma_rag.py`

建议流程：

```text
读取脱敏配置
-> 校验语料 manifest 和 snapshot
-> 初始化 EvidenceBackend
-> 调用 PharmaEvidenceSearchTool
-> 将 EvidenceResult 写入 Memory
-> 调用 DataAnalyzer(enable_chart=False, enable_code=False)
-> 执行 citation validator
-> 程序化渲染参考文献
-> 写入忽略的运行目录
```

入口应支持：

```text
--query-file
--strategy chunk|graph|hybrid
--top-k
--as-of
--corpus-manifest
--output-dir
--offline
```

默认测试使用 `--offline`，禁止网络调用。

### 11.2 运行目录建议

项目运行目录只位于 `pharsight/**` 内，建议实际运行时创建：

```text
outputs/pharma_rag_v1/<run_id>/
├─ run_manifest.json
├─ answer.md
├─ answer.json
├─ evidence.json
├─ retrieval_trace.json
└─ validation.json
```

`outputs/` 已由 `.gitignore` 忽略。不得把真实 `.env`、密钥、受限文档、缓存或生成报告提交到 Git。

## 12. 建议代码布局

目录仅在实现首个真实且有测试的能力时创建，不创建空占位模块：

```text
src/tools/pharma/
├─ evidence_models.py       # 请求、命中、manifest 和序列化模型
├─ evidence_backend.py      # 稳定协议与本地 JSONL 后端
└─ evidence_search.py       # FinSight Tool 适配器

src/agents/data_analyzer/prompts/
└─ pharmaceutical_qa_prompts.yaml

src/utils/
└─ citation_validator.py

tests/tools/pharma/
├─ test_evidence_backend.py
├─ test_evidence_search.py
└─ test_citation_validator.py

tests/fixtures/pharma/
├─ corpus_manifest.json
├─ documents/
├─ passages.jsonl
├─ entities.jsonl
├─ claims.jsonl
└─ queries.jsonl

run_pharma_rag.py
```

## 13. 分阶段实施计划

### 阶段 0：范围冻结与评测题定义

输出：

- 选定公司、药物、适应症和信息截止时间；
- 确认 5–10 份可使用文档；
- 冻结 10 个查询和预期证据；
- 明确数据许可与不可提交内容；
- 为后续任务指定具体责任人。

退出条件：语料和问题边界不再依赖开发者临时判断。

### 阶段 1：证据契约与离线测试样例

输出：

- `EvidenceSearchRequest`、`EvidenceHit`、`EvidenceResult`；
- 文档 manifest 和 JSONL schema；
- 至少 5 份解析样例；
- 5 个人工批准的 Claim–Evidence 样例；
- 至少 50 条有来源的实体别名和 10 个实体链接测试用例。

退出条件：所有 fixture 可合法提交、可追溯并通过 schema 校验。

### 阶段 2：分块检索 RAG 最小闭环

输出：

- 本地 `EvidenceBackend`；
- `PharmaEvidenceSearchTool`；
- DataAnalyzer 两项兼容补丁和配置脱敏修正；
- 医药问答提示词；
- 精确引用校验器；
- 薄入口与离线端到端测试。

退出条件：在冻结语料上可以生成只引用已召回 Evidence 的结构化答案。

### 阶段 3：图检索/混合检索适配

输出：

- Schema V1 的图投影；
- 1–2 跳扩展和证据门控；
- `strategy=graph|hybrid`；
- 检索路径和图快照记录。

退出条件：只改运行配置即可切换检索策略，生成与校验代码不变。

### 阶段 4：对照评测与演示

输出：

- Chunk、Graph、Hybrid 在同一查询集上的结果；
- 召回、引用、跨文档、冲突、延迟和失败案例分析；
- 一条从原始文档到最终引用的完整可视化审计链；
- 已知限制和下一阶段建议。

退出条件：能够回答 Graph/Hybrid 是否带来可测量收益，而不只是完成技术集成。

## 14. 测试策略

### 14.1 单元与合同测试

| 范围 | 必测内容 |
| --- | --- |
| 请求模型 | 缺失 query、非法 strategy、无效 top-k、无时区 cutoff |
| 后端 | 正常、空结果、损坏 JSONL、快照不符、过滤器无命中 |
| 工具 | 超时、后端异常、错误信息、元数据无损转换 |
| EvidenceResult | `ToolResult` 兼容、序列化、`brief_str()` 包含准确 ID 和 quote |
| 引用校验 | 未知 ID、无引用事实、跨文档不足、截止时间违规、定位缺失 |
| 实体链接 | 规范名、别名、格式归一、歧义、unresolved |

### 14.2 Agent 回归测试

使用固定 Memory 输入，验证：

- 不使用证据集合之外的 ID；
- 缺证据时不补写事实；
- 冲突时保留双方证据；
- 输出含事实/判断标签和必要声明；
- `enable_chart=False`、`enable_code=False`、无 DeepSearch 时正常结束。

模型输出具有非确定性，因此结构和安全规则应由确定性校验器兜底，内容质量通过冻结评测集和人工审查评估。

### 14.3 端到端测试

默认端到端测试必须：

- 完全离线；
- 使用小型可再分发 fixture；
- Mock LLM 或使用固定响应；
- 不写出仓库允许范围；
- 不依赖真实凭据；
- 检查运行 manifest、证据列表、引用和失败状态。

实时数据层或外部 API 集成测试必须 opt-in、限速、带超时，并避免对易变化的具体值做脆弱断言。

## 15. 研究评测设计

### 15.1 对照条件

Chunk、Graph、Hybrid 对照必须固定：

- 同一语料快照；
- 同一查询集；
- 同一实体过滤条件和 `as_of`；
- 同一生成模型、提示词和采样参数；
- 同一 top-k 或同时报告上下文预算；
- 同一引用校验器。

只有检索策略可以变化。

### 15.2 指标

| 指标 | 定义 |
| --- | --- |
| 证据召回率（Recall@k） | 人工批准相关证据中，被前 k 条结果召回的比例 |
| 证据精确率（Precision@k） | 前 k 条结果中真正支持问题的证据比例 |
| 引用有效率 | 引用 ID 存在且可定位的比例，目标 100% |
| 引用支持率 | 引用内容实际支持相邻陈述的比例 |
| 引用覆盖率 | 需要证据的陈述中，具有支持性引用的比例 |
| 跨文档覆盖率 | 比较题中覆盖所需不同文档/来源的比例 |
| 冲突识别率 | 冻结冲突样例中正确暴露冲突的比例 |
| 证据不足处理质量 | 证据不足时正确拒答或限定结论的比例 |
| 检索延迟 | 后端检索耗时及其分位数 |
| 端到端延迟 | 从查询到通过校验输出的总耗时 |

V1 可先报告逐题结果和宏平均，不应在样本量过小时宣称统计显著性。

### 15.3 人工评审

至少由领域研究和质量评测两个角色分别检查：

- 事实是否与引用一致；
- 临床阶段、适应症、地区和时间限定是否完整；
- 公司披露与监管/试验登记冲突是否被正确表述；
- 模型判断是否被误写成事实；
- 结论是否包含不恰当医疗建议或确定性投资承诺。

## 16. V1 验收标准

V1 最小闭环全部满足以下条件才可称为完成：

1. 至少 5 份文档通过版本化 manifest 进入解析/导出流程。
2. 每条可引用证据都能定位到文档及页码或字符区间，并保留 URL/稳定来源 ID。
3. 至少 5 个 Claim–Evidence 样例经过人工批准；未批准候选不得直接支撑肯定性事实。
4. 10 个冻结问题可以在 Chunk 模式运行，其中比较题至少引用两份文档。
5. 未知证据 ID、无定位证据或截止时间违规会使校验失败。
6. 参考文献由程序从证据元数据生成，不执行模糊来源匹配。
7. 缺失或冲突信息被显式暴露，不由模型虚构补齐。
8. `strategy` 可在 `chunk` 与 `graph`/`hybrid` 间切换，且生成器代码不变。
9. 同一快照和查询记录可复查的候选集合、检索路径和运行配置。
10. 目标单元测试、Agent 回归测试、离线端到端测试和 `ruff check` 通过。
11. 输出包含投资风险和非医疗建议声明。
12. Git 中不出现真实密钥、运行输出、缓存、受限原始数据或模型检查点。

## 17. 风险与缓解

| 风险 | 影响 | V1 缓解 |
| --- | --- | --- |
| 外部数据层接口与实际实现不一致 | 阻断集成 | 先冻结 JSONL 合同，提供本地后端；API 仅作为适配器 |
| 候选主张未审核 | 图把抽取错误放大 | 事实生成只接受已批准主张或直接引用原文片段 |
| 实体别名和适应症粒度不一致 | 错误连接与跨药物污染 | 保留原始名称、规范 ID、限定条件和未消歧状态 |
| 图扩展召回噪声 | 生成质量下降 | 限制 1–2 跳、证据门控、固定前 k 条、对照精确率 |
| 模型伪造引用 | 结果不可审计 | 精确 ID 白名单和失败即阻断校验 |
| 截止时间混入未来信息 | 回测和研究结论失真 | 后端过滤、快照记录、校验器二次检查 |
| 配置快照泄漏密钥 | 安全事故 | 实施前完成递归脱敏并测试 |
| 文档解析错位 | 引用页码不可靠 | 保存哈希、解析器版本、页面定位，人工抽查测试样例 |
| 首批样本过小 | 无法证明普遍有效 | 只宣称工程闭环和个案结果，后续扩展基准评测 |
| 复杂表格/图片过早进入 | 抽取和引用不稳定 | V1 文本优先，复杂多模态后置 |

## 18. 后续图表改造接口

如果后续“图”还包括报告图表，V1 应保持 `enable_chart=False`。未来可给 `AnalysisResult` 增加可选的图表元数据，而不是复用图片标题的语义匹配：

```text
chart_id
evidence_ids
calculation_inputs
unit / period / cutoff
code_hash
rendered_file_hash
```

图表数据必须先经过确定性校验；图表引用继承底层 `evidence_ids`；报告插入使用精确 `chart_id`。VLM 可用于视觉检查，但不能替代数值、单位和来源校验。

## 19. 实施依赖与开放问题

进入开发前必须明确：

1. 首个公司、药物、适应症和截止时间是什么？
2. 首批文档是否允许作为仓库 fixture 再分发；若否，使用何种去敏摘录？
3. 外部数据层提供 API 还是 JSONL 导出，其版本和负责人是谁？
4. Claim 的人工批准由谁负责，批准标准是什么？
5. 首个语义 Embedding 模型及其许可、离线可用性和维度是什么？
6. 图投影首版使用内存图、NetworkX、PostgreSQL 查询还是 Neo4j？
7. 评测模型是否固定，如何记录模型版本和采样参数？

这些问题不会阻止契约和 fixture 设计，但会阻止相应实施任务进入 `Ready`。

## 20. 完成标准

任何 V1 任务只有在以下条件满足后才能进入 `Done`：

- 对应验收标准和失败路径测试通过；
- 输入、输出、证据来源、日期、单位和转换过程可审计；
- 文档、项目状态和架构决策同步更新；
- 无密钥、受限数据、缓存、日志或真实生成报告进入暂存区；
- `pharsight` diff 可审查，且只读 `FinSight` 仍保持固定 HEAD 和干净工作树；
- 未解决风险和后续工作已经记录。

## 附录 A：端到端伪代码

```python
config = load_redacted_config(config_path)
run = create_run_manifest(config=config, as_of=as_of, corpus=corpus_manifest)

backend = build_evidence_backend(config.backend, corpus_snapshot=run.snapshot_id)
tool = PharmaEvidenceSearchTool(backend=backend, timeout=config.timeout)

hits = tool.search(
    query=query,
    strategy=config.strategy,
    top_k=config.top_k,
    as_of=run.as_of,
    filters=config.filters,
)

memory = VariableMemory(config=config)
for hit in hits:
    memory.add_result(EvidenceResult.from_hit(hit))

draft = DataAnalyzer(
    config=config,
    memory=memory,
    enable_code=False,
).run(
    task=query,
    analysis_task=query,
    enable_chart=False,
)

validation = validate_citations(
    draft=draft,
    allowed_evidence=hits,
    as_of=run.as_of,
    snapshot_id=run.snapshot_id,
)

if not validation.ok:
    write_failed_run(validation)
    raise CitationValidationError(validation.errors)

write_answer(render_answer(draft, hits), run)
```

伪代码只表达组件责任和调用顺序，不是已存在的运行 API。

## 附录 B：V1 成功的含义

V1 成功不等于“Pharsight 已能生成专业完整医药研报”。它只表示：在一个固定窄场景中，项目能从文档得到可定位证据，使用 Chunk 和初步 Claim–Evidence 图进行可比较检索，并生成通过精确引用校验的中文研究结果。完整数据源覆盖、行业模式、财务估值、图表、多模态、长期运行稳定性和大规模 Benchmark 仍属于后续里程碑。
