# GraphRAG V1.3 当前图迁移报告

> 日期：2026-10-19  
> 目标数据库：`outputs/.graph_cache/graphrag_full_pdf_v2.sqlite3`  
> 备份：`outputs/.graph_cache/graphrag_full_pdf_v2.pre_v13.sqlite3`  
> 说明：两者均为 Git 忽略的本地运行产物

## 1. 执行内容

在不重新调用模型的前提下执行兼容迁移：

- Schema 标识更新为 `pharsight-graph-v1.2`；
- `records` 增加 assertion kind、proposition、qualifiers、canonical proposition、origin study、
  valid time 和 recorded time 字段；
- 从旧 attributes 确定性回填 proposition、qualifiers、assertion kind 和 canonical proposition；
- recorded_at 从原 Document.retrieved_at 回填；
- 创建 proposition、origin study 和 recorded_at 索引；
- 保留全部旧 Record、Evidence、Entity 和边，不做破坏性删除。

## 2. 结果

| 指标 | 结果 |
| --- | ---: |
| Record 总数 | 4,920 |
| canonical proposition 已回填 | 4,920 |
| recorded_at 已回填 | 4,920 |
| proposition 非空 | 4,920 |
| qualifiers 非空 | 3,508 |
| origin study 已回填 | 0 |

assertion kind 分布：

- `financial_reported_observation`：2,713；
- `source_assertion`：1,473；
- `study_reported_observation`：605；
- `regulatory_status`：109；
- `forecast`：20。

## 3. 不能由迁移推断的内容

旧图 attributes 中没有可靠、统一的 trial/study ID，因此没有回填 `origin_study_id`。迁移也
不能恢复旧抽取遗漏的人群、剂量、试验臂、终点定义或表格行列。以上字段必须使用 extractor
v1.3 重新读取已解析 Passage 后获得，不能用常识或文件主题补写。

旧数据库仍有 174 条 V1.1 静态语义边。新版 Retriever 明确忽略这些边；它们只作为历史审计
数据保留。数据库中没有 Schema v1.2 / extractor v1.3 的成功 build run，因此当前迁移结果
属于“兼容可读”，不是“完整 V1.3 重建”。下一次 ingest 会因版本不匹配触发重新抽取。

## 4. 恢复方式

如需恢复迁移前状态，停止使用目标数据库并将备份副本作为只读历史基线。不要在两个文件
之间合并表或复制 build run，因为它们代表不同 Schema 语义。
