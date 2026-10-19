# 面向医药投研的 Claim 驱动动态 GraphRAG：技术设计与实验方案

**版本：** v1.0  |  **日期：** 2026-10-17  |  **定位：** 大创项目技术方案 / 算法研发与实验实施文档

> **设计主张：** 离线阶段以可追溯的结构化 Claim 作为基本知识单元，维护轻量实体与元数据索引；在线阶段围绕 Query 检索 Claim、动态发现 Claim 间关系、迭代补足桥接证据，并采用路径级验证进行多跳推理。增量知识通过“追加记录—冲突识别—条件兼容性判断—证据仲裁—版本化视图”维护。
>
> **重要边界：** 本文提出的是待验证的系统设计与研究假设。不能保证在固定计算预算下同时严格提高 Recall 和 Precision；“保留候选、延迟剪枝、回检补证据”旨在减小不必要的 Recall 损失，而不是提供普适的零损失保证。

## 执行摘要

项目的目标不是把所有医药文档离线转换为巨大的 Claim-Claim 全局图，而是建立 **Claim Store + Hybrid Index + Query-scoped Evidence Graph + Verified Multi-hop Reasoner + Temporal Arbitration** 五层体系。这样做预计在多跳、跨源信息整合和增量更新方面具有优势，但主要风险发生在更早的环节：Claim 抽取丢失限定条件、实体消歧错误、初始检索遗漏“桥接 Claim”，以及将语义连通性误判为可证明的推理关系。

推荐的四条硬约束：（1）Claim 必须可回溯到原文 span；（2）图中“相关”“支持”“蕴含”“矛盾”采用不同语义，不得混用；（3）候选证据召回池和最终推理证据集分开维护；（4）历史 Claim 原则上不覆盖，区分知识发生时间和系统获知时间。

**近期优先事项：** 建立可复现实验集 → Claim 结构化抽取与一致性校验 → 混合检索基线 → 动态连边 → 迭代证据回检 → 路径验证 → 时间仲裁。论文贡献重点建议放在 *Query-conditioned relation construction* 与 *Recall-aware iterative evidence-graph reasoning*，时序仲裁作为领域特色。

---

# 1. 目标、边界与核心研究问题

## 1.1 研究问题

给定医药文献库 $D$、查询 $q$、检索与推理预算 $B$，在不预构建完整 Claim-Claim 全局关系图的约束下，构建查询局部证据图 $G_q$，输出带可核验证据链的答案 $a$。目标是在预算约束下提高所需证据的覆盖、关系准确性、推理有效性和答案可信度。

形式化表示：

$$
\begin{aligned}
C &= ExtractClaims(D),\\
C_0 &= HybridRetrieve(q,C),\\
G_q &= BuildGraph(q,C_0\cup C_{bridge}),\\
a &= VerifyAndAnswer(q,G_q).
\end{aligned}
$$

可进一步考虑多目标优化：

$$
\max\ \bigl(Recall_{evidence},\ Faithfulness,\ AnswerQuality\bigr),
\quad \min\ \bigl(Cost,\ Latency\bigr),\qquad Cost\le B.
$$

**术语约定：** Claim 是“源材料作出的可检验断言”，并不自动意味着真值已被系统认可。医疗领域需区分“某研究报告了 X”和“X 对普遍患者成立”。

## 1.2 与传统 GraphRAG 的差别

| 维度 | 传统离线图 GraphRAG | 本项目 Claim-centric 动态图 |
|---|---|---|
| 主知识单元 | 实体、关系、原始文本与社区报告 | 上下文完整且有来源的 Claim |
| 图构建时机 | 主要离线预建实体关系图 | 在线按 Query 构建 Claim-Claim 证据子图 |
| 离线索引 | 全局图、社区、文本向量 | Claim 向量、关键词、实体倒排、时间/元数据 |
| 多跳证据 | 全局图遍历及原文补充 | Claim 候选→动态连边→桥接检索→路径验证 |
| 更新模式 | 修改图索引/增量合并 | Append-only Claim + 状态视图 + 查询缓存失效 |
| 主要风险 | 图抽取偏差、维护成本、社区信息丢失 | 初始召回损失、在线连边成本、错误推理链 |

**关键取舍：** 不建完整全局边，**不等于离线完全无结构**。必须保留实体标准化、试验编号、来源、时间与主题索引，以支持在线高召回的图扩展。全局性趋势查询需要专门的主题聚合通道，不能只依赖 Query-local 图。

# 2. 总体系统架构与数据流

## 2.1 离线处理链路

`PDF/公告/论文/注册平台/监管文本 → 解析与版面保真 → Claim 抽取 → 源句校验 → 实体链接与条件标准化 → Claim Store → Dense/BM25/实体/时间/原文索引`

必须保证：Document → Section/Table → EvidenceSpan → Claim 四层可追溯。对表格、森林图、Kaplan–Meier 曲线等不宜仅用自由文本摘要，应单独抽取结构化数值，保留表号、行列标签、单位和原始图表引用；暂不能可靠识别的内容保持原文通道。

## 2.2 在线查询链路

`Query → 类型路由与子问题分解 → 多路并行召回 → 候选 Claim 池 → 低成本候选边生成 → 关系语义校验 → 多路径搜索 → 证据充分性/缺口检测 → 桥接回检（可循环） → 原文核验与规则检查 → 证据化答案`

建议路由类别：`simple_fact / multi_hop / comparison / global_trend / temporal_status / quantitative_analysis`。简单事实保留直接 RAG 路径；复杂任务才启动动态构图和多轮检索；趋势问题通过主题索引、数据库统计和分层总结；财务计算交由确定性执行器处理。

## 2.3 逻辑分层与不变式

| 层 | 输入输出 | 必须维持的不变式 |
|---|---|---|
| Source Layer | document, source span | 源文件与版本可查；引用位置不丢失 |
| Claim Layer | 标准化 Claim、实体、条件 | Claim 为“报告内容”，不自动认证其为真 |
| Candidate Pool | 高召回 Claim 集 | 低分候选可延迟淘汰；保留来源与初始评分 |
| Dynamic Graph | 节点、类型化边、证据 | 主题相关边不能冒充蕴含边 |
| Reasoning Layer | 子问题、证据路径、验证记录 | 每个关键结论对应可检验前提和验证状态 |
| Temporal Layer | 新旧断言、冲突簇、当前视图 | 不直接删除历史；事实有效期与获知期分开 |

# 3. Claim 表示、抽取与质量控制

## 3.1 推荐的 Claim 结构

将 Claim 记为 $c_i=(s,p,o,\mathcal Q,t,\pi,e)$，其中 $s,p,o$ 是主谓宾或数值，$\mathcal Q$ 是限制条件，$t$ 是时间，$\pi$ 是来源/证据出处，$e$ 是原文证据。工程实践还应引入 assertion-level type、epistemic status、negation、measurement 和 origin group。

以下字段为**建议数据契约**，字段必填性按 Claim 类型区分：

```json
{
  "claim_id": "C-000137",
  "claim_text": "在试验 T 的 EGFR 突变晚期 NSCLC 队列，药物 A 报告 ORR 为 48%。",
  "subject": {"id": "DRUG_A", "type": "Drug"},
  "predicate": "reports_outcome",
  "object": {"type": "Measurement", "endpoint": "ORR", "value": 0.48, "unit": "proportion"},
  "qualifiers": {
    "trial_id": "TRIAL_T", "phase": "II", "population": "EGFR-mutant advanced NSCLC",
    "line_of_therapy": null, "arm": "Drug A", "design": "single_arm",
    "comparator": null, "sample_size": null, "followup_months": null
  },
  "assertion_kind": "study_reported_observation",
  "polarity": "positive",
  "source": {"doc_id": "DOC_101", "version": 1, "span_id": "P6-S2", "quote": "..."},
  "time": {"event_time": null, "published_at": "2025-08-01", "recorded_at": "2026-10-17"},
  "origin_study_id": "TRIAL_T",
  "extraction": {"model_version": "extractor-v1", "status": "needs_review"}
}
```

**示例中药物、试验和数据均为虚构，不能用于真实药品评价。** `extraction.status` 不是医学真实性证明；“模型自报置信度”不直接当作统计校准的正确概率。

## 3.2 原子化原则及防信息丢失措施

推荐将“一个试验同时报告 ORR、PFS 和不良事件”分拆为不同 Claim，但保留共同 `origin_study_id` 与相关研究条件。对数值结果保留分子/分母、95%CI、p 值、HR、终点定义等可用字段，不得把“探索性亚组结果”升级成总体结论。对否定、推测、计划、已观察到的结果采用不同 assertion_kind。

抽取流程：

1. 对文档作结构化解析，明确标题、段落、表格、脚注、图注与跨页内容；
2. 利用类型化 Schema 抽取候选 Claim，同时返回 source span 的精确定位；
3. 通过第二次 NLI/一致性检查验证 Claim 是否被原文蕴含；
4. 对实体进行药物/靶点/疾病/试验标准化与消歧；
5. 对高风险字段（效果量、剂量、日期、患者群体）进行规则校验及人工抽检；
6. 将原文 chunk 作为独立索引保存，供漏抽时回查。

## 3.3 医药实体与本体设计

最小核心实体：`Company, Drug, Target, Mechanism, Disease/Indication, Trial, TrialArm, Population, Endpoint, Outcome, AdverseEvent, Regulator, Patent, LicenseDeal`。推荐对 `Trial` 采用公开注册编号作为强标识；靶点、疾病、药物分别引入可维护的标准词表及别名表（如 MeSH、HGNC、DrugBank/ChEMBL 等，具体授权与字段适用性要单独核对）。

实体链接策略：严格 ID 精确匹配 → 规范化别名匹配 → 带上下文的 reranker → 不确定时保留多个候选实体、禁止强制合并。实体匹配还应校验实体类型与试验语境，避免同缩写词碰撞。

## 3.4 Claim 的去重与独立性

必须分开管理：

- `claim_id`：一条具体的来源断言；
- `canonical_proposition_id`：标准化后描述相近科学命题的集合；
- `origin_study_id`：底层独立研究或临床试验；
- `document_id / source_id`：论文、公告、摘要或研报等传播来源。

同一个试验的会议摘要、论文、公司公告、研报引用不能按四条独立研究证据投票。去重时保留文本来源差异，证据综合时按底层试验/研究单位识别依赖。

# 4. Query 驱动的动态构图：质量、效率与语义

## 4.1 两阶段动态图边构建

**阶段 A：高召回 Edge Candidate Generation。** 对候选 Claim 集 $C$，用实体倒排共现、共同 Trial/Population、医学关系模板、语义近邻和 query-subquestion 角色生成候选 Claim pair。允许由“同一通路但不同直接实体”的机制关系触发候选，避免仅靠共享字符串导致桥接召回失败。

**阶段 B：高精度 Edge Verification。** 对候选 pair 分类为 `entails / supports / contradicts / qualifies / elaborates / temporal_precedes / supersedes / compares / prerequisite / related / none`，并输出方向、条件兼容性、证据跨度和边判定依据。

推荐把两个概率/评分通道分开：

$$
P(r_{ij}\mid c_i,c_j,context),\qquad
U_{ij}(q)=Score(useful\_for\_query\mid c_i,c_j,r_{ij},q).
$$

第一项描述相对稳定的**关系语义**，第二项表示当前 Query 下的**使用价值**。Query 不应改变真实关系标签，只改变该关系是否值得进入推理路径。`related` 可以用于探索检索，但**不能用于证明结论**。

## 4.2 推荐边数据结构

```json
{
  "edge_id": "E-0021",
  "from_claim_id": "C-000137",
  "to_claim_id": "C-000214",
  "relation": "supports",
  "direction": "forward",
  "context_compatible": true,
  "query_utility_score": 0.82,
  "classifier_score": 0.91,
  "verified": false,
  "edge_evidence": ["DOC_101:P6-S2", "DOC_202:P3-S5"],
  "model_version": "relation-v1",
  "cache_key": "hash(claim_version_pair,model_version,ontology_version)"
}
```

评分仅作为排序参考。只有经标注集校准的分数才能解释为置信概率。`verified=false` 时不得显示为“已证明关系”。

## 4.3 算法与预算

直接枚举 $N$ 个 Claim 的全部 pair 需要 $N(N-1)/2$ 次候选关系判断，规模为 $O(N^2)$。若每个 Claim 通过 ANN/倒排预选 $k$ 个邻居，则候选边数可压到约 $O(Nk)$（**仅指边候选数，不包括近邻搜索和后续 LLM 成本**）。

建议先部署 `rule/entity blocking → inexpensive cross-encoder/NLI → selective LLM judge` 的三层结构。对重复查询缓存**基础 Claim-Claim 关系**，仅重算 Query-specific utility；Claim 内容、标准化实体、本体或模型更新后失效缓存。

**推荐起始超参数（只是实验起点）：** Dense top-80、BM25 top-80、实体索引 top-100，经去重保留至多 200–300 个初始 Claim；每节点最多 10–20 个低成本候选邻居；在线最深 3–4 跳；Beam width 8–16；先用 small-dev 集做 Recall/Cost 曲线，再调优。不要把这些数字当作被文献证实的最优值。

## 4.4 图级一致性审查

逐边分类可能组合出矛盾路径。需在路径阶段检查：

- 类型/方向：`reported_in` 不能反向解释为治疗适应症；
- 条件兼容：人群、剂量、时间、比较对象、终点定义一致才允许直接对比；
- 证据传播：`related`、`compares`、`temporal_precedes` 不具有普遍逻辑传递性；
- 否定与模态：`may`、`associated with`、`in vitro` 不得升级为临床因果或疗效；
- 全局冲突：允许文献真实不一致，不能为制造“整图一致”而删除对立研究。

# 5. 召回风险：分阶段诊断与不损失召回的原则

## 5.1 四层召回

设 $G_q$ 为人工标注的必要证据 Claim 集，给定最终候选集 $C_t$，定义：

$$
ClaimRecall@K=\frac{|G_q\cap C_K|}{|G_q|}.
$$

另设 `Extraction Recall`（原文 gold claim 的抽取覆盖）、`Edge Recall`（gold edge 识别覆盖）、`Gold Path Recall@K`（top-K 路径是否包含完整可接受 gold path）、`Evidence Set Success Rate`（是否完整找齐某一个可用的充分证据集合）。多条替代证据路径存在时，不可简单假设 gold path 唯一。

若将每阶段视为条件成功率 $p_1,p_2,p_3,p_4$，则指定证据链保留成功率由链式法则可写为 $p_1p_2p_3p_4$，**不要求这些阶段彼此独立**。仅作示意：各环节条件成功率均为 0.9，则完整链保留率约为 0.656。

## 5.2 三类结构性 Recall 损失

1. **抽取损失：** 只存 Claims、删除 chunk，导致亚组条件、图表结果或否定句丢失。解决：Claim 和 SourceSpan 双向映射 + 原始 chunk 兜底召回。
2. **初始检索损失：** 查询直接命中首末节点，却漏掉不相似的中间桥接 Claim。解决：多路检索 + Bridge Query + Gap-driven iterative retrieval。
3. **剪枝损失：** 按“直接相关度”过早淘汰某个低分但不可替代的推理前提。解决：Candidate Pool / Active Evidence 双池；设置桥接价值和路径覆盖多样性奖励；仅软删除候选，不永久丢弃。

## 5.3 高召回混合检索

令初始召回集合为：

$$
C_0=C_{dense}\cup C_{BM25}\cup C_{entity}\cup C_{metadata}\cup C_{sourceChunk}.
$$

- Dense：补充跨表达方式的语义近邻；
- BM25：应对缩写、基因位点、试验编号和术语精确匹配；
- Entity：连接药物、靶点、疾病、注册试验的异名和跨文档信息；
- Metadata：按时间、临床阶段、人群、试验/公司筛选；
- SourceChunk：定位 Claim 漏抽、原始表格和上下文限定条件。

整合时优先用 Reciprocal Rank Fusion 或可训练融合模型，而不是简单把不同检索器的原始分数相加。初始 reranker 的阈值应围绕 **gold evidence recall** 调优，而非仅以 sentence relevance 为目标。

## 5.4 缺口驱动的桥接检索

每轮由 Reasoning Controller 输出：

```json
{
  "sufficient": false,
  "gaps": [
    {"kind": "bridge_entity", "target": "Drug A", "slot": "resistance pathway", "question": "哪条机制证据连接药物 A 与耐药通路？"},
    {"kind": "evidence_span", "target": "Trial T", "slot": "comparator", "question": "原文是否存在可比较的对照组？"}
  ],
  "reason": "目前只有机制相关性，缺乏临床验证。"
}
```

把缺口转换为多个搜索 Query，返回新 Claim 后并入候选池：

$$
C_{t+1}=C_t\cup HybridRetrieve(GapQueries(q,C_t)).
$$

**关键：** 新一轮必须可以检索**整个 Claim Store 和原始 chunk 库**，而不只是现有图邻居。控制循环次数、累计 token 和召回增长，避免无穷扩展。

# 6. 提高多跳推理准确性：探索—验证双轨设计

## 6.1 两层证据池

- `CandidatePool`：高召回、包含尚未核实的候选，支持继续找桥接证据；
- `ActiveEvidence`：从候选中挑选出用于当前推理的一小部分，须有 Claim 来源、路径位置及验证状态。

任何一次推理不得把“候选并已找到”误写成“充分证据并已被核实”。失败后可从 CandidatePool 重新选择替代路径，避免重新检索全部信息。

## 6.2 Query → 子问题 DAG

将“Drug A 的商业化前景是否弱于 Drug B”分解为：适应症重叠性、可比的临床疗效/安全性、上市及监管时间、竞争和支付环境、市场规模与假设。子问题之间采用 DAG 依赖，而不是单纯线性清单：只有已确认试验可比，才允许展开疗效定量比较。

每个子问题标注 `required_evidence_types`、`evidence_found`、`verification_status` 和 `open_gaps`。综合证据覆盖度可使用加权覆盖率；权重需在验证集设定，并报告在不同权重下的稳健性。

## 6.3 多路径检索与评分

设候选路径 $P=(c_1,r_1,c_2,\dots,c_m)$，候选排序函数：

$$
\begin{aligned}
S(P,q)={}&\alpha Rel(P,q)+\beta Edge(P)\\
&+\gamma Coverage(P,q)+\delta Provenance(P)\\
&-\lambda Redundancy(P)-\eta Incompatibility(P).
\end{aligned}
$$

可使用 Beam Search 保留多个高分且来源独立的支持路径和反对路径；加入 MMR/路径覆盖奖励避免 top-K 全来自同一试验。**路径评分只是搜索启发式，不等于结论真实概率**。

## 6.4 逐跳验证器

采用结构化三元中间态：`premises + inference_rule + conclusion`。分类器至少输出：

- `ENTAILS`：在明确的语义规则与限定条件下成立；
- `SUPPORTS`：证据支持但不构成逻辑蕴含；
- `CONTRADICTS`：在可比较条件下对结论形成直接冲突；
- `INSUFFICIENT`：证据缺失或不可比。

需区分 Claim 本身被原文支持，和多个 Claim 组合能否支持结论，两个任务的标注和模型不能混为一谈。关键陈述应再次取回 source span，而不是只读取精简 Claim。

## 6.5 医药专用规则

| 典型错误 | 禁止的默认推导 | 必须增加的检查 |
|---|---|---|
| 机制外推 | 靶点关联 → 临床有效 | 体内外证据等级、临床适应症与患者群体 |
| 跨试验比较 | ORR 48% > 35% → A 优于 B | 入组标准、治疗线数、endpoint、试验设计与统计不确定性 |
| 相关当因果 | 相关性 → 干预因果效果 | 混杂、随机化、机制与因果证据 |
| 终点偷换 | PFS 改善 → OS 必然改善 | 终点定义与是否直接有 OS 结果 |
| 多源重复计算 | 多篇报道 → 多项独立证据 | `origin_study_id` 去重/依赖建模 |
| 时点错置 | 某药曾在 II 期 → 当前仍在 II 期 | 有效时间、最近权威事件、版本历史 |

## 6.6 停止、拒答与修复

停止条件不应只是“LLM 自称足够”。建议满足：（1）必需子问题达到足够覆盖；（2）高风险结论通过规则/数值和证据校验；（3）没有未处理的关键矛盾；（4）或达到成本/延迟预算。若预算耗尽而证据不足，输出分层结论：已证实事实、基于事实的推断、关键未知项，不强行给出肯定答案。

三类修复路径分别是：`retrieval failure → gap re-query`；`grounding failure → revisit source span / re-extract`；`inference failure → verify edge and premise, re-plan or abstain`。避免对所有失败一律追加检索。

## 6.7 简化伪代码

```python
def answer(query, budget):
    plan = route_and_decompose(query)
    pool = hybrid_retrieve(plan, budget.initial_k)
    active, graph, traces = set(), None, []
    for step in range(budget.max_rounds):
        candidates = propose_edges(pool, plan, budget.edge_limit)
        graph = verify_edges(candidates, query, pool)
        paths = diverse_beam_search(graph, plan, top_paths=budget.top_paths)
        active = select_supported_evidence(paths, pool, budget.context_tokens)
        checks = verify_multihop_claims(query, active, plan)
        traces.append((step, pool.ids, paths, checks))
        if checks.sufficient and checks.valid:
            return grounded_answer(query, active, checks, traces)
        gaps = checks.gaps
        if not gaps or budget.exhausted():
            break
        pool |= hybrid_retrieve(expand_gap_queries(gaps), budget.round_k)
    return qualified_or_abstaining_answer(query, active, checks, traces)
```

注意实现中应对“本轮新 Claim 数量接近零”“相同 gap 循环”“两条路径引用同一证据”设置退出条件。

# 7. 增量知识、冲突检测与时间仲裁

## 7.1 事件追加，而非直接覆盖

建议采用 `Append-only Claim Event Log`，为每条 Claim 保存 `document_version`、`recorded_at`、`event_time` 和有效区间。删除应主要通过撤回/纠错事件实现，而不是物理抹去历史。最终提供 `current_view` 和 `as_of(timestamp)` 历史快照两类查询。

**时间需分开：** `valid_time` 表示某状态在现实中生效；`transaction/recorded_time` 表示系统何时得知。投研历史回测尤其需要 `as_known_at(t)`，防止未来信息泄露。

## 7.2 冲突识别必须先判断可比性

Claim A：“早期单臂试验 ORR=48%”；Claim B：“后期随机试验某组 ORR=32%”。如果人群、剂量、治疗线数和试验定义不同，不能直接视为矛盾。进行 `Comparable(c_i,c_j)`：实体、干预、患者群体、终点、时间、比较组和估计对象一致到何种程度？只有关键条件匹配后才进行严格冲突分类。

关系输出：`equivalent / consistent / context_dependent / supersedes / contradictory / uncertain`。

## 7.3 状态更新和科学证据冲突分别处理

- **状态类事实**：试验从 Recruiting → Completed、药物权益发生转让，主要依据时间有效性与权威登记来源更新当前视图，保留历史状态。
- **科学证据类命题**：早期试验与后期试验结果不一致，不能“新覆盖旧”或简单多数投票；依据研究设计、偏倚风险、样本量/置信区间、独立性、适用人群和发表状态做结构化证据综合。不能将来源级打分直接当作命题为真的概率。
- **争议结论**：允许保持 `disputed / unresolved`，最终答案披露争议和各方证据。

## 7.4 仲裁记录模型

```json
{
  "hypothesis_id": "H-008",
  "candidate_claims": ["C-000137", "C-000228"],
  "compatibility": "context_dependent",
  "conflict_class": "not_direct_contradiction",
  "decision": "coexist",
  "review_basis": ["different_population", "different_trial_phase"],
  "effective_as_of": "2026-10-17",
  "reviewer": "arbitrator-v1",
  "requires_human_review": false
}
```

原始 Claim 应保持不可变；仲裁决定是新的、可审计的附属记录。对临床疗效“有效/无效”、监管批准或投资结论等关键争议设置人工复核开关。

## 7.5 新知识的增量处理

1. `ingest(new_doc)`：去重、解析和 source-version 登记；
2. `extract_and_ground()`：新增 Claim 与原文一致性检查；
3. `entity_link()`：刷新别名和 entity 倒排；
4. `retrieve_potential_conflicts()`：按实体+条件+时间寻找相关历史 Claim；
5. `classify_relation_and_arbitrate()`：可比性判断、冲突分类、必要时人工复核；
6. `append_event_and_refresh_views()`：更新当前/历史视图、主题聚合；
7. `invalidate_cache()`：失效受影响的 Claim-pair 边缓存和查询答案缓存。

# 8. 全局趋势、数值分析与投研 Agent 接口

## 8.1 Global Query 单独路径

“过去两年有哪些 ADC 技术趋势？”不是几个高相似度 Claim 就能代表的全局问题。建议离线维持轻量主题维度 `Modality / Target / Indication / Company / Time / Stage`，支持分层与分层抽样检索；数量统计应对结构化基础表（如临床试验实体）计数，而不是对 Claim 条数计数。

`global_query → time_scope & topic taxonomy → stratified retrieval / aggregation → source-normalized summaries → answer`。

若要覆盖所有主题，可引入文献/主题摘要或社区报告，但这属于局部在线证据图之外的**互补模块**。

## 8.2 数值推理由工具执行

例如峰值销售预测：

$$
Revenue=EligiblePatients\times Penetration\times NetPrice\times DurationFactor.
$$

各参数由 Claims 提供来源、适用时间与区间；计算由可测试程序执行并保存参数、公式版本、情景与计算日志。不得由自然语言推理链直接伪造统计检验或成本/NPV 结果。对于临床试验效果量比较，应明确研究可比性和推断范围。

## 8.3 统一接口建议

```text
POST /documents/ingest      -> document_id, extraction_job_id
GET  /claims/{claim_id}     -> claim + provenance + versions
POST /query                 -> answer + evidence_graph + citations + gaps
POST /claims/reconcile     -> claim_pair, compatibility, arbitration
GET  /knowledge/as-of       -> claims visible at historical timestamp
GET  /runs/{run_id}/trace   -> retrieval, edges, path and verification trace
```

`/query` 应返回 `evidence_ids`、`claim_path_ids`、`provenance`、`verification_status`、`uncertainties`、`trace_id` 与成本/延迟统计，而不仅是 answer text。

# 9. 瓶颈—解决方案—可量化验收表

| 瓶颈 | 对应措施 | 建议检测指标 |
|---|---|---|
| Claim 丢失关键信息 | 原子 Claim + 限定条件 + 源句复核 + chunk 兜底 | Extraction Recall；Qualifier Accuracy；Grounding Precision |
| 实体消歧错误 | 标准词表、上下文链接、候选保留 | Entity Linking Accuracy；误合并率 |
| 初始检索漏桥接 Claim | Dense/BM25/Entity/metadata 联合 + Gap 回检 | Claim Recall@K；Bridge Recall；整条证据链成功率 |
| Claim pair 过多 | Blocking + 邻居剪枝 + 小模型预筛 + Cache | Edge candidates/query；Latency；Cost |
| 错误关系边 | 多标签 NLI + 类型/条件校验 + 原文对齐 | Edge macro-F1；关键边误判率 |
| 路径爆炸和同源冗余 | Beam + diversity + path validity + study-level 去重 | Gold Path Recall@K；source diversity |
| 已找到证据但推理错误 | 子问题 DAG + 逐跳 verifier + 反例/数值核验 | Conditional Answer Accuracy；unsupported-claim rate |
| 新旧信息冲突 | Append-only + 可比性门控 + 仲裁记录 | Conflict Precision/Recall；Temporal QA Accuracy |
| 上下文过长 | Pool/Active 双层 + 摘要压缩 + 源句按需取回 | Context Tokens；Faithfulness；成本 |
| 趋势题覆盖不全 | Topic index + 分层抽样 + 数据库聚合 | Theme Coverage；strata coverage |
| 医药数值与图表解析 | 结构化提取器 + 单位/时间解析 + 人工抽检 | Numeric Extraction Accuracy；表格对齐准确率 |
| 评价无法定位收益 | 分层指标 + Gold Evidence Available 实验 | 模块增益、质量—成本 Pareto 曲线 |

# 10. 数据集、评价指标与消融实验

## 10.1 数据集建议

建设领域内的小而精的人工标注集，比先准备海量无标签语料更重要。MVP 可从 150–300 个专家审核 Query 作为起始目标，其中覆盖：

- 简单事实查询（药物/靶点/试验注册信息）；
- 2–4 跳机制或研发链推理；
- 两药/两项目对比（设有不可直接比较的陷阱）；
- 时间更新与新旧冲突；
- 全局趋势与简单统计；
- 明确证据不足、应该拒答的查询。

每条 Query 标注：`gold answer / acceptable answer variants / gold sufficient evidence set(s) / source spans / gold entities / critical edges / required conditions / temporal cut-off / expected abstention`。建议双人独立标注、分歧仲裁，并记录分歧类型；跨文档 gold path 非唯一，应容许等价证明集。

## 10.2 关键分层指标

| 指标 | 推荐定义 | 注意事项 |
|---|---|---|
| Extraction Recall | 已抽出的 gold 事实数 / 原文 gold 事实数 | 不只测文本相似，需检查限定条件 |
| Claim Recall@K | 初始 Top-K 命中的 gold Claim / gold Claim | 必须指定查询与 K，分 hop 统计 |
| Evidence Set Success | 是否包含至少一组完整充分证据 | 支持多组替代 gold evidence |
| Edge Precision/Recall/F1 | 对支持、矛盾、蕴含等类型分别评估 | `related` 不能和 `entails` 合并 |
| Gold Path Recall@K | 前 K 条路径是否覆盖有效 gold path | 核查条件兼容与来源独立性 |
| Grounding Accuracy | Claim 是否忠实于 source span | 高价值 Claim 应人工复核 |
| Conditional QA Accuracy | gold evidence 全部提供时的 QA 准确率 | 分离检索失败与推理失败 |
| Faithfulness | 答案陈述有可引用证据支撑的比例 | 必须反向检查引用有效性 |
| Temporal QA / Conflict F1 | 时间状态和冲突识别正确率 | 采用历史可见信息避免泄露 |
| Cost + Latency | token/$、每轮次数、P50/P95 时延 | 与准确率/召回联合报告 |

## 10.3 可复现的对照与消融

| 编号 | 配置 | 所检验的因果性问题 |
|---|---|---|
| A0 | Chunk RAG（BM25 + Dense，可选 rerank） | 标准基线 |
| A1 | Claim-only RAG | Claim 表示是否改善检索与答题 |
| A2 | A1 + Hybrid/Entity Retrieval | 多路召回是否改善 bridge recall |
| A3 | A2 + Query-time Dynamic Edges | 动态连边是否产生额外收益 |
| A4 | A3 + Gap/Bridge Iterative Retrieval | 可否恢复初始检索遗漏的中间证据 |
| A5 | A4 + Diverse Path Search | 显式多路径是否增加完整证据链覆盖 |
| A6 | A5 + Stepwise Verification | 是否真正提升条件推理准确性 |
| A7 | A6 + Temporal Arbitration | 是否降低过时/冲突知识错误 |
| B1 | Microsoft GraphRAG 的合适模式 | 与离线图方案比较（预算匹配） |
| B2 | PropRAG / SAG 代表性实现 | 与命题路径/动态连接方案比较 |

比较时匹配 Query 集、允许访问的数据、LLM、上下文 token 和成本预算；同时画质量—成本曲线，避免 A6 因调用更多模型而获得不公平优势。

### Gold Evidence Available Test

人为保证正确证据已全部进入候选池，评价关系构图、路径选择和推理验证的纯下游能力。与标准 end-to-end 实验结合，可以定量拆分：`Extraction loss / Retrieval loss / Edge loss / Reasoning loss`。另做“去掉实体索引”“禁用 iterative gap”“禁用 verifier”“禁用历史版本”的单变量消融。

## 10.4 预设研究判据（非承诺值）

以下仅是建议的项目验收框架，不是现有模型的真实性能：

- 对比 A2→A3，若 gold evidence recall 没变而正确推理链准确率改善，证明动态边的潜在贡献；
- 对比 A3→A4，关注多跳 gold evidence set success，而不仅是普通 Recall@K；
- 对比 A5→A6，在 gold-evidence-available 子集验证是否降低 unsupported conclusions；
- 质量提升应同时报告 token、时延和数据标注成本，得到 Pareto 前沿；
- 若无显著提升，允许按实际数据结论缩减 Graph 模块（例如简单题直接 RAG）。

# 11. 工程落地：数据库、接口与开发路线

## 11.1 MVP 技术栈

建议先用 `Python + PostgreSQL/pgvector + BM25 检索服务 + Pydantic Schema + NetworkX (query-time) + 可替换 LLM/NLI 接口`。Claim、source span、entity link、origin study 和仲裁记录使用关系库；在线临时图使用内存图；需求明确后再讨论 Neo4j/图数据库。对于大规模稀疏近邻可采用向量库/ANN；如果规模尚小，先实现可靠基线。

建议的核心表：

```sql
CREATE TABLE documents (
  doc_id TEXT PRIMARY KEY, source_type TEXT, source_uri TEXT,
  version INTEGER, published_at TIMESTAMPTZ, ingested_at TIMESTAMPTZ
);
CREATE TABLE source_spans (
  span_id TEXT PRIMARY KEY, doc_id TEXT REFERENCES documents(doc_id),
  locator JSONB NOT NULL, raw_text TEXT NOT NULL
);
CREATE TABLE claims (
  claim_id TEXT PRIMARY KEY, claim_text TEXT NOT NULL,
  proposition JSONB NOT NULL, qualifiers JSONB NOT NULL,
  source_span_id TEXT REFERENCES source_spans(span_id),
  origin_study_id TEXT, assertion_kind TEXT,
  valid_from TIMESTAMPTZ, valid_to TIMESTAMPTZ,
  recorded_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE claim_entities (
  claim_id TEXT REFERENCES claims(claim_id), entity_id TEXT,
  entity_type TEXT, role TEXT, PRIMARY KEY(claim_id, entity_id, role)
);
CREATE TABLE arbitration_events (
  event_id TEXT PRIMARY KEY, claim_group_id TEXT,
  decision TEXT NOT NULL, basis JSONB NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL
);
```

本表是逻辑示例而非直接可投入生产的完整 DDL：实际需要索引、权限、事务、更新事件、软删除/撤回规则以及跨文档完整性设计。

## 11.2 四阶段迭代计划

**阶段 0：质量基线与标注（先做）**。收集多个数据源的小规模语料；完成 SourceSpan 定位和 150–300 个 Query 的分层标注；跑 Chunk RAG。交付：可复现评测脚本、错误分类手册。

**阶段 1：Claim Store 与多路召回。** 完成 schema、抽取 validator、实体 linking、Claim/chunk 双索引；量化 extraction/initial recall。交付：API 和结构化数据集；若 Claim 化整体显著降低 Recall，先修抽取和兜底索引。

**阶段 2：在线动态关系 + 桥接回检。** 实现候选边生成、关系分类、缓存、top-K 子图、证据缺口判断；与 PropRAG/SAG 类方法对照。交付：路径可视化与逐跳 trace；评估质量—成本曲线。

**阶段 3：受约束推理 + 仲裁。** 实现逐跳验证、原文核验、状态类事件更新、科学争议并存视图和历史快照。交付：可以复现的 multi-hop QA 报告、冲突时间实验、完整消融表。

## 11.3 日志、复现实验和风险控制

每次 Query 记录：模型版本、索引版本、时间截断点、初始候选 ID、每轮新增 Claim、候选边与分类结果、beam 路径、source spans、拒答原因、token、时延、费用。采用固定随机种子和固定数据版本，模型 API 更新后单独标记。对尚未核实的医药推论输出“研究证据与不确定性”，不以投资建议或临床治疗建议的口吻作确定结论。

# 12. 论文创新定位与未解决问题

1. **Query-conditioned Edge Discovery：** 基础关系与 Query relevance 解耦，研究预算受限时的候选边生成、缓存和误连边控制。新颖性需对照已有 Proposition Path、SQL join 和 GraphRAG 方法证明。
2. **Recall-aware Iterative Graph Reasoning：** 将高召回 Pool 与严格推理 Context 分离；证据缺口触发全库桥接回检；同时搜索支持与反对的多条路径。关键实验是初始证据不全时的恢复能力和金标准证据充分时的验证能力。
3. **Temporal Claim Arbitration：** 对“状态事实”和“科学证据”分别仲裁，借助双时间与原始研究 ID 避免知识更新中的时间穿越和伪独立证据投票。

未解决研究问题：怎样校准关系可信度、怎样评估真实医学证据等级、是否应使用事件超边而非 Claim pair、如何定义复杂投研问题的充分证据、如何保证 global coverage，以及如何在证据不足时正确拒答。这些问题需要标注数据和实证分析支持，不能靠 Prompt 假设已经解决。

---

# 附录 A. 推荐文献与对应设计环节（已核对来源）

1. **Edge et al. (2024), From Local to Global: A Graph RAG Approach to Query-Focused Summarization.** Microsoft GraphRAG 的 global query、社区摘要和 map-reduce 基础；主要用于设计全局趋势任务的对照组。https://arxiv.org/abs/2404.16130；官方文档：https://microsoft.github.io/graphrag/query/global_search/
2. **Wang & Han (EMNLP 2025), PropRAG: Guiding Retrieval with Beam Search over Proposition Paths.** 上下文丰富的 proposition 表示与不调用生成式 LLM 的在线路径 Beam Search；直接相关于 Claim 表示和多跳路径模块。https://aclanthology.org/2025.emnlp-main.317/
3. **Li et al. (ACL 2026), S2G-RAG: Structured Sufficiency and Gap Judging for Iterative Retrieval-Augmented QA.** 证据充分性判断、结构化缺口、迭代回检与句级证据压缩；直接相关于 Bridge Claim Retrieval。https://aclanthology.org/2026.acl-long.1185/
4. **Wu et al. (2026), SAG: SQL-Retrieval Augmented Generation with Query-Time Dynamic Hyperedges.** 使用事件/实体索引在查询时通过共享实体连接相关证据，不预建完整全局图；用于动态连接策略对比。https://arxiv.org/abs/2608.12129
5. **Guo et al. (2024), LightRAG: Simple and Fast Retrieval-Augmented Generation.** 双层检索、增量更新与轻量结构化检索；用于低成本检索/工程对比。https://arxiv.org/abs/2410.05779
6. **Microsoft GraphRAG Query Overview.** 对 Local、Global、DRIFT、Basic 等模式及其适用性的官方说明；用于按 Query 路由设计。https://microsoft.github.io/graphrag/query/overview/

**文献使用说明：** 以上文献说明相关机制已被提出或在特定实验中得到支持，不等于证明它们在医药投研数据集、Claim Schema 或成本约束下必然有效。项目应重现可比基线并建立专门领域标注集。

# 附录 B. 最小可执行检查清单

- [ ] 数据层：Claim 与源句双向映射、出处精确到段落/表格、保留历史版本
- [ ] 抽取层：限定条件、否定/假设/观察类型、药物/疾病/试验 ID 正确
- [ ] 检索层：Dense/BM25/Entity/Metadata/SourceChunk 多路融合、Bridge 回检
- [ ] 图层：相关 ≠ 支持 ≠ 逻辑蕴含，边有方向、条件和原始依据
- [ ] 推理层：保留多条独立证据路径，记录每一步前提/规则/结论与验证状态
- [ ] 仲裁层：先做可比性门控，区分状态更新与科学分歧，提供历史快照
- [ ] 评价层：Extraction / Retrieval / Edge / Path / Reasoning 分层评估并记录成本
- [ ] 实验层：A0–A7、PropRAG/SAG/GraphRAG 对照、Gold Evidence Available 检测
