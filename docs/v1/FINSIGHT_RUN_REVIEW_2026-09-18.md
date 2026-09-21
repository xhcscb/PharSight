# FinSight 报告生成运行复盘（2026-09-18，九安医疗 002432）

## 0. 文档控制

| 项目 | 内容 |
| --- | --- |
| 文档目的 | 基于一次真实端到端运行的日志与产物，实证评估 FinSight 智能体的优势与不足，并给出对 Pharsight 的启示与跟进任务 |
| 文档性质 | 运行复盘（事后分析）；结论分"源码事实 / 运行日志实证 / 推断"三类，不声明任何 Pharsight 已实现能力 |
| 复盘对象 | 上游 FinSight 冒烟运行：目标 `九安医疗 (002432)`，`target_type=financial_company` |
| 运行入口 | `FinSight/run_report.py`（`max_concurrent=3`，`IF_RESUME=True`） |
| 运行主机 | Windows，Python 环境 `Finsight` |
| 上游基线 | FinSight `8fc5bce28ac201c713219a47d30367c6a84342f4` |
| 标准运行时 | Python 3.11 |
| 证据来源 | `FinSight/outputs/smoke/九安医疗/logs/finsight.log`（32,620 行）及同目录生成的 md/docx/pdf、`memory/memory.pkl`、54 个 `agent_working/*` 检查点 |
| 关联文档 | [项目管理](../PROJECT_MANAGEMENT.md)、[总体架构](../ARCHITECTURE.md)、[医药投研 RAG V1 方案](./PHARMA_RAG_V1.md)、[架构讨论记录](./DESIGN_DISCUSSION_LOG.md) |

### 证据等级约定

- **源码事实**：附上游基线中的 `文件:行号`，可复核。
- **运行实证**：来自本次运行日志/产物的可计数事实，附日志模式与计数。
- **推断**：对原因或影响的解释，显式标注，不当作事实。

---

## 1. 执行摘要

本次运行**产出了一份形式完整、结构接近证券研报的中文报告**（评级、目标价、SOTP、风险提示、图表），但**证据链严重不成立**，且**进程在产物生成后卡死于退出清理**。核心结论如下：

1. **采集层整体失效（运行实证）**：12 个 `DataCollector` 全部保存 0 条数据；上层"采集→分析→写作"事实上退化为"搜索摘要 + 金融数据 API → 写作"。
2. **网页阅读近乎全失败（运行实证）**：124 次点击动作仅 2 次成功、122 次因 `click_result[0]` 越界报错；DeepSearch 退化为"仅凭搜索摘要"。这与 [DESIGN_DISCUSSION_LOG 1.2](./DESIGN_DISCUSSION_LOG.md) 对"搜索摘要即证据"的担忧一致，且比预期更严重。
3. **引用不可审计且含错误来源（运行实证）**：参考文献 18 条中包含**错误主体**（第 3 条为 `stockcode=603927`）与**完全无关来源**（第 11 条为工商银行年报）；正文同时混用自由文本 `[Source: ...]` 与编号 `[1,2]` 两种引用格式，缺少"主张→证据"映射。
4. **协议不兼容导致静默放弃（运行实证 + 源码事实）**：模型输出了 DeepSeek 原生工具调用标记 `<｜DSML｜tool calls>`，而不是 FinSight 约定的 `<execute>/<final_result>` XML 标签；`_parse_llm_response` 无标签时按 final 处理（`src/agents/base_agent.py:598-616`），导致 Agent 一轮即终止。
5. **运行时缺陷导致卡死（运行实证 + 源码事实）**：代码执行器超时后线程继续运行嵌套 `asyncio.run` 的 DeepSearch（`src/utils/code_executor_async.py:350-355`），产生孤儿任务；解释器退出时等待非 daemon 工作线程，日志停在 12:53:42，进程长时间无 CPU、线程全部 Wait。
6. **合规缺口（运行实证）**：报告有"风险提示"，但**没有免责声明 / 非医疗建议声明**，不满足 [ARCHITECTURE.md 分析与报告控制](../ARCHITECTURE.md) 的要求。

**总体判断**：FinSight 的**编排与写作骨架可复用**，但**采集、抓取、引用、运行时与合规五个环节均不可信**。这直接印证并强化了 Pharsight 已记录的决策：V1 走确定性纯文本路径（ADR-008）、精确引用与失败关闭（ADR-007），不依赖通用采集规划、图表、VLM 与 DeepSearch。

---

## 2. 运行对象与产物

### 2.1 运行配置（源码事实）

- 目标：`target_name: 九安医疗`、`stock_code: 002432`、`target_type: financial_company`、`language: zh`（`FinSight/my_config.yaml`）。
- 自定义任务：采集 7 条 + 分析 9 条；由 LLM 追加生成采集 5 条 + 分析 5 条（日志第 9、13-15 行）。
  - 最终：**采集 12 个任务（7 自定义 + 5 生成）**，**分析 14 个任务（9 自定义 + 5 生成）**，写作 1 个任务，共 27 条 `task_mapping`。
- 并发：`max_concurrent=3`（`run_report.py:20`，并由 `Semaphore` 限流，`run_report.py:225-236`）。
- 三阶段优先级：采集=1、分析=2、写作=3（`run_report.py:108-165`）。

### 2.2 产物（运行实证）

| 产物 | 大小 | 生成时间 |
| --- | --- | --- |
| Markdown | 113,513 B（约 4.37 万字符，489 行） | 12:48:07 |
| DOCX | 1,012,679 B | 12:48:10 |
| PDF | 1,036,055 B | 12:48:17 |
| Memory 检查点 | 2,191,818 B | 12:53:42 |

报告结构：标题 + 摘要 + 核心投资逻辑 + 5 个主题章节（正文共 6 个 `##` 级章节）+ `## Reference Data Sources`。《Reference Data Sources》共 18 条链接。

---

## 3. 运行时间线与阶段分解

| 阶段 | 起点 | 终点 | 时长（约） | 说明 |
| --- | --- | --- | --- | --- |
| （前一次尝试，被中断） | 12:11:52 | 12:13:52 | — | 同一日志文件残留：先生成采集任务后中断；本次分析以第二次为准 |
| 任务生成 | 12:13:52 | 12:14:10 | 18 s | 生成 5+5 个补充任务 |
| 优先级 1：采集（12 任务） | 12:14:11 | 12:16:10 | 2 min | 组内并发 3；多数采集任务数秒结束 |
| 优先级 2：分析（14 任务） | 12:16:10 | 12:34:32 | 18.4 min | 主耗时；含代码执行与大量 DeepSearch |
| 优先级 3：写作（1 任务） | 12:34:32 | 12:48:18 | 13.8 min | 见下方细分 |
| └ 大纲 | 12:34:32 | 12:37:31 | 3.0 min | `[Outline]` |
| └ 章节正文（6 节） | 12:37:31 | 12:44:52 | 7.3 min | 分节串行：2:25 / 0:52 / 1:36 / 1:14 / 0:27 / 0:46 |
| └ 后处理 | 12:44:52 | 12:48:18 | 3.4 min | 图片替换→摘要标题→封面→引用→渲染 |
| "All tasks completed" | 12:48:19 | — | — | 主任务实际完成 |
| 孤儿 DeepSearch 尾声 | 12:48:31 | 12:53:42 | 5.4 min | 3 个 DeepSearch 陆续结束 |
| 退出清理卡死 | 12:53:42 | 复盘时仍存活 | > 45 min | 无日志、CPU 0、线程 Wait；复盘结束前进程已退出 |

**运行实证**：主任务在 12:48:19 已完成，报告文件已落盘；随后 5.4 分钟的 DeepSearch 属于父任务超时后仍在运行的孤儿。12:53:42 后进程不再写日志。

---

## 4. 量化运行指标

| 指标 | 数值 | 来源/说明 |
| --- | --- | --- |
| 日志总行数 | 32,620 | 全文统计 |
| INFO / WARNING / ERROR | 2,147 / 22 / 122 | 级别统计 |
| 有日志的 Agent 数 | 36 | 12 采集 + 14 分析 + 1 写作 + 9 DeepSearch |
| `agent_working/*` 目录数 | 54 | 运行产物目录 |
| 动作循环迭代行 `Iteration N` | 457 | 含各层循环与同一 DeepSearch 实例的多次重启 |
| 搜索动作 | 启动 87 / 成功 87 / 失败 1 | `Search action *` |
| 点击动作 | 启动 124 / 成功 2 / 失败 123 / 被拒 2 | `Click action *` |
| 点击失败原因 | `list index out of range` 122 次 | `search_agent.py:201` |
| Memory 持久化 | 94 次 | `Memory save start/saved` |
| 代码执行超时 | 14 次 | `ExecutionTimeout: code execution exceeded 120s limit` |
| LLM 重试 / 上下文截断 | 0 / 0 | `Retrying in` / `Context length exceeded` 均为 0 |
| 图片耗尽告警 | 21 次 | `Available images are exhausted` |
| 其他告警 | 1 次 | 点击被拒（URL 不在搜索结果内） |
| DataCollector 保存数据量 | **12/12 均为 0 条** | `Successfully save 0 items to memory` |
| 报告图片数 | 3 张 | 正文 `![...]` 计数 |
| 报告引用标记 | 自由文本 `[Source:...]` 10 处；多编号 `[n,n]` 82 处；单编号 `[n]` 7 处 | 格式不统一 |
| 最终 Memory | log=273, data=958, tasks=27, 2,191,818 B | 最后一条 `Memory saved` |

---

## 5. FinSight 智能体的优势

> 以下优点均有本次运行或源码支撑；括号内为证据。

1. **端到端自主闭环，产物形式专业（运行实证）**：从任务生成到渲染 DOCX/PDF 全自动完成，约 40 分钟产出一份带"中性评级、目标价 60 元、SOTP 分部估值、2026E-2028E 盈利预测、风险提示、3 张图表"的中文研报，版式与证券研报惯例接近。
2. **分层、可解释、可续跑的编排（源码事实）**：顶层按优先级分组、组内 `asyncio.gather` 并发、组间串行（`run_report.py:196-257`）；每个动作循环每轮保存检查点（`src/agents/base_agent.py:551-562`），支持 `resume`；本次共 94 次 Memory 持久化，具备中断续跑能力。
3. **共享 Memory 作为"黑板"（源码事实）**：采集/分析/写作共用同一 `Memory`，并通过 `dependency` 记录工具与 Agent 关系（`src/memory/variable_memory.py:296-301`），实现跨阶段数据传递。
4. **分析层保留确定性计算与图表能力（源码事实 + 运行实证）**：`DataAnalyzer` 在代码执行器内用 Python/DataFrame 计算，并支持图表与 VLM 复核（`src/agents/data_analyzer/data_analyzer.py`）；报告中的营收/利润/ROE 图表即由此产出。
5. **LLM 层具备重试与上下文降级设计（源码事实）**：`AsyncLLM.generate` 对 429/5xx 指数退避 + 抖动，对上下文超限删除首条消息后重试（`src/utils/llm.py:107-160`）；本次运行 0 次重试、0 次截断，说明在本次网络/额度条件下调用层稳定。
6. **大纲 + 模板驱动写作（源码事实 + 运行实证）**：`outline_template_path` 与 `reference_doc_path` 控制大纲与 DOCX 样式，产出 6 节结构报告。

---

## 6. FinSight 智能体的不足

> 按严重程度排序。每条给出"现象—证据—归因（若为推断则标注）"。

### 6.1 采集层整体失效（最严重）

- **现象**：12 个采集任务全部保存 0 条数据，报告的事实基础并非来自 `DataCollector`。
- **证据**：日志出现 12 次 `Successfully save 0 items to memory` 与 12 次 `DataCollector finished: collected=0 items`；`Saved collect result` 出现 0 次。
- **归因（源码事实 + 推断）**：模型在采集阶段输出了 DeepSeek 原生工具调用标记 `<｜DSML｜tool calls>`（日志原文 Unicode 为 `<U+FF5C><U+FF5C>DSML<U+FF5C><U+FF5C> calls>`），而 `BaseAgent` 只识别 `<execute>/<final_result>/<search>/...` XML 标签；`_parse_llm_response` 在无匹配标签时回退为 `final`（`src/agents/base_agent.py:598-616`），动作 `continue=False`，循环一轮即结束。推断：这是**协议不兼容导致的静默丢弃**，而非模型无能力。
- **对照**：少数采集任务能正常输出 `<execute>`（如 `agent_data_collector_ac4e0fd7` 迭代 21 轮），说明同一批任务出现了**不稳定的协议遵循**，结果随机。

### 6.2 网页抓取近乎全失败，DeepSearch 退化为"搜索摘要"

- **现象**：124 次点击仅 2 次成功；122 次报 `list index out of range`；仅 1 个真实 PDF 成功获取。
- **证据**：`Click action started=124, failed=123, done=2`；失败原因 122 次 `list index out of range`（`src/agents/search_agent/search_agent.py:201`）；成功点击为 `http://pdf.dfcfw.com/pdf/H2_AN201608150017100995_1.pdf`。
- **归因（源码事实）**：`_handle_click_action` 已检查 `len(click_result)==0`（`search_agent.py:190-191`），但紧接着无条件访问 `click_result[0]`（`search_agent.py:201-204`）。当 crawl4ai 抓取失败返回空列表时（`src/tools/web/web_crawler.py:137-139` 捕获异常返回 `[]`），越界异常被外层 `except` 吞掉并记录失败。
- **后果（推断）**：绝大多数"证据"只来自搜索结果摘要（标题+片段），从未打开原文；报告对年报、投资者关系记录的引用多为**未打开的链接**。这与 [DESIGN_DISCUSSION_LOG 1.2](./DESIGN_DISCUSSION_LOG.md) 指出的"接近迭代上限时把未点开的链接列为可引用来源"（`search_agent.py:240-266`）相互印证。

### 6.3 引用不可审计，且含错误/无关来源

- **现象**：参考文献列表质量失控，正文引用格式不统一。
- **证据**：
  - 第 3 条参考文献为 `https://sns.sseinfo.com/en/company.do?stockcode=603927`，主体是**另一家公司**（应为 002432）。
  - 第 11 条为《中国工商银行股份有限公司 2025 年度报告》，与九安医疗**完全无关**。
  - 其余含知乎、moomoo、investing.com、搜狐、前瞻等二手/聚合来源；部分条目是站点首页（巨潮资讯、AKShare 文档）而非具体披露文件。
  - 正文同时存在 10 处自由文本 `[Source: 新浪财经行情快照]` 与 82 处 `[n,n]`、7 处 `[n]`，且出现 `[2,1]` 这类顺序颠倒。
- **归因（源码事实 + 推断）**：引用由 LLM 自由书写，`Memory` 未建立"主张→证据 ID"的稳定映射；[DESIGN_DISCUSSION_LOG 1.1/1.2](./DESIGN_DISCUSSION_LOG.md) 已指出溯源仅为自由文本。Pharsight 的 ADR-007 正是针对此问题。

### 6.4 缺乏来源分级、实体绑定、期间/单位/币种校验

- **现象**：无法判断同一指标（如"归母净利润 22.67 亿"）对应的报告期、口径、单位与来源优先级。
- **证据（源码事实）**：`ToolResult` 仅以 `name+description` 做相等/哈希（`src/tools/base.py`），`Memory.add_data` 只做 `list.append`，无 URL/内容哈希去重（`src/memory/variable_memory.py:292-294`）；`DeepSearch` 的 `ClickResult` 强制截断 6000 字符（`src/tools/web/web_crawler.py:107-114`），PDF 不保留页码定位。
- **对照**：[ARCHITECTURE.md 最小证据链](../ARCHITECTURE.md) 要求发布者、URL、发布/获取时间、截止时间、期间、单位/币种、转换公式、内容类别。本次运行无一具备。

### 6.5 运行时缺陷：孤儿任务与退出卡死

- **现象**：主任务 12:48:19 完成，之后仍有 DeepSearch 运行到 12:53:42，随后进程长时间无日志、CPU 0、线程全部 Wait。
- **证据（源码事实）**：
  - `DataAnalyzer` 在代码执行线程内调用嵌套 `asyncio.run`（`src/agents/data_analyzer/data_analyzer.py:85`）；`ReportGenerator` 改用 `AsyncBridge`（`src/agents/report_generator/report_generator.py:124-131`）。
  - 代码执行用 `loop.run_in_executor(None, sync_exec)` 并 `asyncio.wait_for(timeout=120)`（`src/utils/code_executor_async.py:350-355`）；超时只取消 asyncio Future，**线程内的 `asyncio.run` 继续运行**。本次 14 次 `ExecutionTimeout` 即对应 14 个此类孤儿。
  - `asyncio.run` 退出时会 `shutdown_default_executor()`，解释器退出时 `concurrent.futures` 亦 join 非 daemon 线程（`AsyncBridge` 线程本身为 daemon，见 `src/utils/async_bridge.py:23-28`）。
- **推断**：卡死最可能由某个仍在执行的代码执行线程（其嵌套 `asyncio.run` 清理或 crawl4ai/Playwright 残留）未结束引起。复盘结束前进程已退出，故不排除是外部终止。

### 6.6 报告结构冗余，图表供给不足

- **现象**：6 节中多节主题高度重叠（ROE、总资产周转率、金融化迁移反复出现）；正文仅 3 张图。
- **证据**：日志 `Building index for 4 images` 与 **21 次** `Available images are exhausted`；Markdown 正文 `![...]` 仅 3 处。章节正文里出现大段同义复述（如第 3-6 节反复论证"资产效率失真/金融化"）。
- **归因（推断）**：大纲生成与图/文匹配是两套松散机制，缺图时仅在日志告警并不阻断；章节之间缺少一致性/去重校验。

### 6.7 合规缺口：缺少免责与非医疗建议声明

- **现象**：报告有"风险提示"小节，但无"免责声明 / 不构成投资建议 / 非医疗建议"声明。
- **证据**：正文检索 `免责声明` 出现 0 次；`不构成` 的 2 处为行文，不构成声明；无"非医疗建议"表述。
- **对照**：[ARCHITECTURE.md 分析与报告控制](../ARCHITECTURE.md) 明确要求报告附投资风险和非医疗建议声明。

### 6.8 成本、效率与可复现性

- **现象**：约 40 分钟/次；单个 DeepSearch 实例累计迭代可达 61 轮（`agent_deepsearch agent_2144c8c0`）；多任务对同一实例重复调用。
- **证据**：`Iteration` 行 457；`agent_data_analyzer_e6adf8be` 31 轮、`agent_data_collector_ac4e0fd7` 21 轮、DeepSearch 实例最高 61 轮（跨多次调用累加，因为 `Iteration 1` 反复出现）；14 次代码执行各空耗 120 s。
- **其他风险（源码事实，来自既有讨论）**：`Config._set_dirs` 会把插值后的完整配置（含 API key）写入 `config.json`（[DESIGN_DISCUSSION_LOG 1.3](./DESIGN_DISCUSSION_LOG.md)）；`DeepSearch.async_run` 默认 `resume=True`，命中含 `return_dict` 的旧检查点会直接返回缓存（`src/agents/base_agent.py:515-521`），可能"该搜没搜"。

---

## 7. 优势与不足的机制性归因（小结）

| 维度 | 结论 | 关键源码位置 |
| --- | --- | --- |
| 编排 | 优点：分层清晰、可续跑；缺点：组内失败被 `return_exceptions` 吞并，失败任务静默 | `run_report.py:247-255` |
| 动作协议 | 缺点：仅识别 XML 标签，无 schema；未知格式回退 final，导致静默放弃 | `base_agent.py:598-616` |
| 工具互调 | 缺点：Tool 返回数据列表、Agent 返回文本，形状不一致；异常统一吞为 `[]` | `base_agent.py:446-473` |
| 抓取 | 缺点：空结果未在一处统一处理，`click_result[0]` 越界 | `search_agent.py:190-204`；`web_crawler.py:137-139` |
| 引用 | 缺点：自由文本来源 + 无主张映射；无来源分级 | `search_agent.py:240-266`；`variable_memory.py` |
| 运行时 | 缺点：嵌套 `asyncio.run` + 不可取消线程池；daemon/join 语义导致退出等待 | `code_executor_async.py:350-355`；`async_bridge.py:23-28` |
| 合规 | 缺点：无强制声明注入 | 报告产物；对照 `ARCHITECTURE.md` |
| 分析/图表 | 优点：确定性计算 + 图表；缺点：图/文匹配脆弱 | `data_analyzer.py`；日志图片耗尽告警 |

---

## 8. 对 Pharsight 的启示

### 8.1 战略层：本次运行验证了既有决策

- **继续 ADR-008**：V1 直接调用单一医药证据工具 + `DataAnalyzer(enable_code=False, enable_chart=False)` 纯文本路径，**不依赖**通用采集规划、完整研报、图表、VLM 与 DeepSearch。本次运行的采集层失效与抓取失败，正是该决策的最强证据。
- **坚持 ADR-007**：只能引用检索结果中的证据 ID，参考文献由元数据程序化渲染，未知/不可追溯引用**阻断输出**。本次运行出现的错误主体与无关来源证明"相似度/自由文本引用"不可接受。

### 8.2 协议层

- 采用结构化 tool call/result（含参数 schema 与返回类型），替代"XML 标签 + 自由文本"。
- **失败关闭**：无终态标签、未知动作格式、DSML 等"外来协议"必须显式报错并终止该 Agent，而不是回退 `final`。
- 显式区分：空结果 / 超时 / 权限不足 / 解析失败，禁止统一吞成 `[]` 或空数据。

### 8.3 证据层

- **实体绑定来源注册表**：每条证据绑定公司/证券代码、药物、适应症、地区、报告期；引用错误主体（如 603927）应在校验阶段被拦截。
- **来源优先级**：按 [ARCHITECTURE.md](../ARCHITECTURE.md) 顺序，一手监管/交易所/披露优先；知乎、聚合站不得作为医药关键结论的主证据。
- **稳定证据 ID + 主张→证据映射**：`EvidenceHit` 生成稳定 ID，报告引用可逐条回溯。
- **快照与定位**：保存 URL、抓取时间、内容哈希、页码/字符区间；全文不截断或截断可配置。
- **期间/单位/币种/口径**：进入报告前做确定性归一，缺一即标记缺失。

### 8.4 采集/抓取层

- 统一超时与重试；空结果显式失败，禁止裸索引 `result[0]`。
- PDF 保留页码定位；HTML 转 markdown 保留来源元数据。
- 去重键：URL + 内容哈希 + query；修复"命中旧检查点直接返回缓存"的状态语义。

### 8.5 运行时层

- 禁止在代码执行线程内嵌套 `asyncio.run`；统一由受控的 bridge/executor 调度，并支持取消与 `shutdown()`。
- 为整个 run 设外部看门狗/总超时；运行线程可 daemon 化或强制回收，避免退出卡死。
- 运行产物、缓存、日志与配置分离；配置落盘脱敏（不写密钥）。

### 8.6 报告与合规层

- `citation_validator`：未知 ID、无定位、超截止时间即失败关闭（ADR-007）。
- 统一引用格式；程序化生成参考文献。
- 强制注入"投资风险 + 非医疗建议"声明；正文区分"来源事实 / 确定性计算 / 明确假设 / 模型判断"。
- 图/文一致性校验：图片数量不足时阻断或降级，而不是仅告警。

### 8.7 评测层

- 将本次 8 类问题固化为"运行复盘评分表"，纳入 M5 评测。
- 对固定案例做回归：采集成功率、点击成功率、引用可审计率、未知来源率、免责声明存在性、进程正常退出。

---

## 9. 建议跟进任务（Backlog，待指派）

> 仅登记，未进入 `Ready`；负责人、验收标准与依赖由项目负责人后续确认。

| 建议 ID | 任务 | 里程碑 | 优先级 | 状态 |
| --- | --- | --- | --- | --- |
| V1-007 | 动作协议失败关闭：检测并拒绝非约定动作格式（含 DSML），禁止静默回退 `final` | M3 | P0 | 待办 |
| V1-008 | 引用审计规则与错误实体拦截：`citation_validator` 增加股票代码/实体一致性校验 | M4 | P0 | 待办 |
| V1-009 | 来源分级与实体绑定来源注册表设计（含一期可再分发样例） | M2 | P0 | 待办 |
| V1-010 | 抓取健壮性：统一空结果/超时处理、PDF 页码定位、快照哈希、去掉裸索引 | M2 | P0 | 待办 |
| V1-011 | 运行时治理：去除嵌套 `asyncio.run`、可取消 executor、run 级看门狗 | M1 | P1 | 待办 |
| V1-012 | 报告合规与图/文一致性：强制免责声明注入、缺图阻断策略 | M3 | P1 | 待办 |
| V1-013 | 运行复盘评分表与固定案例回归 | M5 | P0 | 待办 |

---

## 10. 局限与本复盘声明

1. **单样本**：仅一次运行、一个标的（九安医疗 002432）；结论对"采集协议不兼容""抓取失败"具强证据，但不代表所有模型/环境下的普遍比例。
2. **以日志为准**：本复盘基于日志与产物，未做进程级堆栈采样；6.5 的卡死根因属**推断**，已显式标注。
3. **未审阅内部数据**：未逐条核对报告财务数字与原始披露，仅指出证据链与引用层面的问题；数字正确性需后续专项审计。
4. **运行环境备注（运行实证）**：复盘期间 FinSight 进程一度长时间存活（PID 72992，CPU 增量 0、39 线程 Wait），复盘结束前已退出；FinSight 工作树存在运行前既有的本地改动（如 `my_config.yaml`、`.env.example`），HEAD 仍为固定基线 `8fc5bce`，本次复盘未修改 FinSight。
5. 本复盘不包含任何密钥或受限数据；报告样例仅引用公开链接。

---

## 附录 A：关键证据索引

| 主题 | 日志模式 | 本次计数 |
| --- | --- | --- |
| 采集为空 | `Successfully save 0 items to memory` | 12 |
| 协议不兼容 | `LLM response this step: <｜DSML｜tool calls>` | 8（含 DSML 的响应行） |
| 点击失败 | `Click action failed ... list index out of range` | 122 |
| 点击成功 | `Click action done` | 2 |
| 代码超时 | `ExecutionTimeout: code execution exceeded 120s limit` | 14 |
| 图片耗尽 | `Available images are exhausted` | 21 |
| 卡片引用 | `Reference Data Sources` / 正文 `[n,n]` | 18 条 / 82 处 |

主要源码位置：

- 动作解析与回退：`src/agents/base_agent.py:598-616`
- 动作循环与检查点：`src/agents/base_agent.py:529-565`
- 点击空值越界：`src/agents/search_agent/search_agent.py:201`
- 可用来源清单（含未点开链接）：`src/agents/search_agent/search_agent.py:240-266`
- 抓取截断与空返回：`src/tools/web/web_crawler.py:107-114, 137-139`
- 代码执行超时与线程：`src/utils/code_executor_async.py:350-355`
- 分析器嵌套 `asyncio.run`：`src/agents/data_analyzer/data_analyzer.py:85`
- 写作器走 bridge：`src/agents/report_generator/report_generator.py:124-131`
- 桥接线程与超时：`src/utils/async_bridge.py:20-51`
- Memory 追加/持久化：`src/memory/variable_memory.py:53-87, 292-294`
- LLM 重试：`src/utils/llm.py:107-160`

## 附录 B：复核方法

1. 以共享读方式打开日志（运行时文件被 `RotatingFileHandler` 占用）：
   `[System.IO.File]::Open(path, 'Open', 'Read', 'ReadWrite')`。
2. 用正则 `^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})` 提取时间戳，统计级别、缺口与阶段时长。
3. 用 `\[.*?:(agent_.+?)\]` 提取 Agent id，统计每个 Agent 的迭代/搜索/点击。
4. 用模式计数核对本文所有数字（附录 A）。
5. 阅读产物 Markdown 的标题、引用与图片，核对引用质量。

## 附录 C：FinSight 授权代码修复（2026-09-20）

项目负责人于 2026-09-20 明确授权在 `FinSight/` 内实施代码级修复，且要求不触碰 Git（不 `add`/`commit`/`reset`/`checkout`/`stash`）。据此更新了 `AGENTS.md` 的工作区边界约定：`FinSight/**` 仅在获得本次这类显式授权时可修改，且仍禁止任何 Git 写操作，历史与索引保持不变。

本次实施范围（对应第 6 节问题）：

| 修复项 | 文件 | 改动 | 预期效果 |
| --- | --- | --- | --- |
| 抓取健壮性 | `src/agents/search_agent/search_agent.py` | 空结果分支不再访问 `click_result[0]`；区分空返回与错误返回；错误结果不写入证据，只记 `memory.log` | 消除第 6.2 节 122 次 `list index out of range`，失败显式化 |
| 抓取健壮性 | `src/tools/web/web_crawler.py` | 对每个 URL 分别捕获异常/空内容并返回带 `Error` 名的 `ClickResult`，记录真实原因 | crawl4ai 失败不再静默返回 `[]`，错误可见 |
| 运行时治理 | `src/agents/data_analyzer/data_analyzer.py` | `_get_deepsearch_result` 由嵌套 `asyncio.run` 改为 `AsyncBridge` | 消除第 6.5 节嵌套事件循环清理导致的卡死诱因 |
| 运行时治理 | `src/utils/async_bridge.py` | 超时取消 pending future 并告警；新增 `shutdown_async_bridge()` | 桥接任务超时可观测，运行结束可回收 |
| 运行时治理 | `src/utils/code_executor_async.py` | 使用独立带名 `ThreadPoolExecutor`；超时写告警；新增 `shutdown()` | 代码执行线程与主事件循环解耦，超时不再无声 |
| 运行时治理 | `run_report.py` | 收尾调用 `shutdown_async_bridge()` | 主流程结束后回收后台桥接线程 |
| 引用可审计 | `src/agents/report_generator/report_generator.py` | `_add_reference` 去掉"无匹配即强制 top-1"；增加实体/股票代码一致性校验；输出 `citation_audit.json` 与审计日志 | 未解析/主体不符的引用不再被强行编号，引用可审计 |

本次未纳入（超出授权选择，建议后续在 Pharsight 处理）：动作协议失败关闭（检测到 `DSML` 等未知格式时不回退 `final`，而是显式报错）。

验证与状态：

- 对全部改动文件执行 `python -B -c compile(...)` 语法编译，全部通过；未安装或运行新依赖，未写 `__pycache__`/`.ruff_cache`。
- 未运行任何单元测试或网络请求（保持 FinSight 只读运行环境未被进一步写入运行产物）。
- `FinSight` HEAD 仍为 `8fc5bce28ac201c713219a47d30367c6a84342f4`，Git 索引无变更；工作树新增的授权改动文件：`run_report.py`、`src/agents/data_analyzer/data_analyzer.py`、`src/agents/report_generator/report_generator.py`、`src/agents/search_agent/search_agent.py`、`src/tools/web/web_crawler.py`、`src/utils/async_bridge.py`、`src/utils/code_executor_async.py`。
- 残留风险：代码执行线程仍不可强制取消（超时后只能等待其自然结束，最多受 `AsyncBridge` 超时约束）；引用校验为启发式（按 6 位代码判断实体），可能对跨公司对比来源产生误拒，需结合 `citation_audit.json` 复核。

## 变更历史

| 日期 | 变更 | 作者 |
| --- | --- | --- |
| 2026-09-18 | 初稿：基于九安医疗冒烟运行日志与产物的实证复盘 | 项目组 |
| 2026-09-20 | 附录 C：记录经所有者授权的 FinSight 代码修复与验证状态 | 项目组 |
