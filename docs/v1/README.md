# Pharsight V1 文档入口

> 当前进展（2026-09-21）：已在保持 FinSight 总体架构不变的前提下，完成 `DataCollector -> DataAnalyzer -> ReportGenerator` 的医药双模式代码适配与无网络单元测试。下一步是在 Python 3.11、受控资料和固定模型上完成端到端及领域验收。图检索与 Claim–Evidence RAG 仍是后续演进，不是当前主流程的前置条件。

V1 的目标不是一次性完成完整医药研报系统，而是用一个范围受控、证据可核验的场景，先跑通 FinSight 现有三段式主流程：

```text
研究任务书 -> DataCollector 采集 -> DataAnalyzer 分析
           -> ReportGenerator 生成带引用报告 -> 发布校验
```

在此基线上，再演进到“文档解析、信息抽取、Claim–Evidence 构图和混合检索”。

## 核心文档

- [医药投研流程与 FinSight 全流程适配方案](./PHARMA_RESEARCH_WORKFLOW_AND_FINSIGHT_ADAPTATION.md)：当前实施主文档；说明医药公司/行业研究流程、FinSight 差距、非架构性改造、文件级任务、实施顺序和验收标准。
- [V1 医药投研智能体实现说明（2026-09-21）](./IMPLEMENTATION_SUMMARY_2026-09-21.md)：已落地代码、配置、测试、运行方法、已知限制和验收缺口。
- [医药投研 RAG V1 研究与实施方案](./PHARMA_RAG_V1.md)：后续演进文档；研究证据契约、Claim–Evidence 构图和混合检索，不作为当前跑通主流程的前置条件。
- [架构问答与设计讨论记录](./DESIGN_DISCUSSION_LOG.md)：FinSight 运行机制、DeepSearch 数据链路、预处理与最小改造问答；只增不删，作为后续讨论出发点。
- [FinSight 报告生成运行复盘（2026-09-18，九安医疗 002432）](./FINSIGHT_RUN_REVIEW_2026-09-18.md)：基于真实端到端运行日志与产物的实证复盘，分析 FinSight 智能体优势与不足及对 Pharsight 的启示。
- [FinSight 重跑复盘（2026-09-20，含授权修复验证）](./FINSIGHT_RERUN_REVIEW_2026-09-20.md)：修复后干净重跑的验证结果、残留问题与架构改进方向。
- [项目管理](../PROJECT_MANAGEMENT.md)：项目路线图、V1 纵向切片任务、状态和风险。
- [总体架构与证据规范](../ARCHITECTURE.md)：长期架构边界、数据源优先级和架构决策。

## 文档状态

| 项目 | 内容 |
| --- | --- |
| 版本 | V1 流程跑通基线 |
| 日期 | 2026-09-21 |
| 状态 | 代码实现待审；尚未声明真实医药研报质量已验收 |
| 首个研究模式 | A/H 上市医药公司研究的窄场景 |
| 标准运行时 | Python 3.11 |
| 上游基线 | FinSight `8fc5bce28ac201c713219a47d30367c6a84342f4` |

当实现改变接口、检索策略、引用规则或图模型时，应同步更新本目录文档、项目管理记录和总体架构决策日志。
