# Pharsight GraphRAG V1 实现说明

> **历史文档提示（2026-10-17）：** 本文对应首版实现，当前 `graphrag-v1.2` 规范、运行结果
> 和文档分层请从 [GraphRAG V1 文档导航](./README.md) 阅读。

> 日期：2026-10-10
> 运行时 Schema：`pharsight-graph-v1`
> 状态：首版代码与离线相关回归完成，等待 Python 3.11 与真实冻结语料验收

## 实现边界

本版将核心图收敛为三层，不创建 `Source` 节点：

1. `Document / Passage / Evidence`：保存文档身份、页级块和精确引文；
2. `Claim / Observation / Event`：保存来源陈述、带完整条件的数值观察和时间事件；
3. `Entity`：保存跨文档对象身份。

七类证据关系是第二层记录之间的边，不是新的节点层。自动记录默认为
`candidate`，可用于检索导航；报告获得的可引用内容始终是底层 Passage 和
Evidence quote。图中没有原文定位的关系不能进入结果。

## 运行时组件

- `src.graph_rag.models`：Pydantic Schema、稳定枚举和 Evidence Pack；
- `GraphStore`：SQLite 权威存储、事务写入、审核状态与 NetworkX 投影；
- `GraphBuilder.build_document`：对现有页级文档索引增量抽取并构图；
- `GraphRetriever.search`：精确/词法种子、1–2 跳类型扩展及 BM25 Hybrid；
- `GraphEvidenceSearchTool`：把 Evidence Pack 映射为现有 `ToolResult` 契约。

GraphRAG 运行产物默认位于 `outputs/.graph_cache/pharsight_graph.sqlite3`，不进入
Git。稳定 ID 由文档哈希、chunk ID、精确引文和规范化记录内容确定；同一模型、
Schema 和抽取器版本下重复构图直接复用。

## 配置

```yaml
retrieval_backend: hybrid       # chunk | graph | hybrid
graph_build_on_ingest: true
graph_store_path: ./outputs/.graph_cache/pharsight_graph.sqlite3
graph_max_hops: 2
graph_top_k: 8
graph_relation_candidate_limit: 100
graph_relation_confidence_threshold: 0.7
```

构图、实体角色、全局候选关系和查询规划统一使用主模型 `DS_MODEL_NAME`。当前环境
中的 `deepseek-flash` 按项目负责人确认作为 DeepSeek v4.1 服务别名；数据库保存
实际模型标识，不在源码中硬编码模型或密钥。

## 关系与失败处理

全局关系只在共享实体的候选记录之间预计算，支持 `EQUIVALENT_TO`、`SUPPORTS`、
`SUPPLEMENTS`、`INCOMPARABLE_WITH`、`CONTRADICTS`、`SUPERSEDES` 和
`SAME_ORIGIN_AS`。模型必须先判断口径可比性；低于阈值或不在候选集合中的输出被
拒绝。

LLM 输出通过 JSON 修复和 Pydantic 校验；Evidence quote 必须逐字存在于 Passage。
无效记录被拒绝并写入构建报告。Graph/Hybrid 查询没有命中或查询规划失败时，结果
显式携带 warning 并回退现有 BM25 页面检索。

## 尚未完成的验收

- 在项目标准 Python 3.11 环境运行完整测试；
- 使用冻结的多来源公司与行业语料运行真实 DeepSeek 构图；
- 由领域成员审核抽取准确率、条件绑定、实体误合并和七类关系；
- 完成 Graph、BM25 和 Hybrid 的同预算 Recall@k/Precision@k 对照。

在这些验收完成前，默认配置继续使用 `retrieval_backend: chunk`，不得宣称图检索已
优于现有基线。
