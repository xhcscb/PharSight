# Pharsight GraphRAG V1 文档导航

> 下一版设计与当前 V1.3 运行时增量请见 [GraphRAG V2 文档导航](../v2/README.md)。

> 本目录对应历史运行时：`pharsight-graph-v1.1` / `graphrag-v1.2`
> 当前运行时：`pharsight-graph-v1.2` / `graphrag-v1.3`
> 文档基线：2026-10-19
> 适用研究模式：`pharma_company`、`pharma_industry`

本目录同时保存当前实现文档和早期研究材料。阅读当前 GraphRAG 时，应按下面的
权威顺序使用文档，避免把历史方案中的 `Source` 节点或“四层图”误认为运行时设计。

## 当前文档

1. [主要更新介绍](./PHARSIGHT_GRAPHRAG_V1_2_MAJOR_UPDATES_2026-10-18.md)
   - 集中说明 V1.2 为什么从全局静态关系改为“原子记录先行、查询时动态子图”；
   - 概述材料性评分、高召回候选池、关系 hard gate、预期收益和下一轮实验；
   - 适合项目汇报、版本评审和新成员快速了解本次变化。
2. [理论架构](./PHARSIGHT_GRAPHRAG_ARCHITECTURE_V1_2_2026-10-17.md)
   - 只解释设计目标、三层语义模型、关系语义、检索原理和系统边界；
   - 不包含数据库表、配置值、代码路径和实验跑分；
   - 适合项目介绍、架构评审和后续方案演进。
3. [实现规范](./PHARSIGHT_GRAPHRAG_IMPLEMENTATION_V1_2_2026-10-17.md)
   - 描述当前代码模块、运行时 Schema、SQLite 表、稳定 ID、构图与检索算法、
     配置项和失败处理；
   - 适合开发、测试、代码审查和故障定位。
4. [运行与评测](./PHARSIGHT_GRAPHRAG_OPERATIONS_AND_EVALUATION_V1_2_2026-10-17.md)
   - 描述完整 PDF 构图流程、运行检查、质量指标、当前实跑结果、已知风险和验收要求；
   - 适合复现实验、比较 Chunk/Graph/Hybrid 和判断 RAG 增益。

当文档与代码不一致时，运行时 Pydantic Schema、数据库建表代码和测试优先；材料性架构
决定以 `docs/ARCHITECTURE.md` 的 ADR-015～018 为准。

## 历史与背景材料

以下文件保留设计演进和研究背景，不是当前运行时规范：

- `PHARSIGHT_EVIDENCE_FIRST_HYBRID_RAG_DESIGN_V2_2026-10-07.md`：概念母稿，正文仍保留
  `Source` 和四层设计的历史讨论；
- `GRAPH_RAG_RESEARCH_AND_PHARSIGHT_DESIGN_2026-10-07.md`：早期技术调研；
- `graph_schema_v1.md` / `graph_schema_v1.json`：早期 Claim–Evidence Schema 草案；
- `GRAPH_RAG_V1_IMPLEMENTATION_2026-10-10.md`：`graphrag-v1.0` 阶段的简要实现说明；
- `meeting_02.md`：会议材料。

历史文档可以用于理解取舍，但不能覆盖当前三层图、七类证据关系、SQLite 权威记录和
NetworkX 可重建投影等已生效决定。
