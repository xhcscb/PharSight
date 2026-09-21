# Pharsight V1 架构问答与设计讨论记录

## 0. 文档控制与维护规则

| 项目 | 内容 |
| --- | --- |
| 文档目的 | 沉淀对 FinSight 架构的逐条理解、问答结论与待讨论点，作为 Pharsight V1 后续设计的讨论出发点 |
| 文档性质 | 讨论记录（living log）；不是已实现能力的声明，也不是最终架构决策 |
| 首个研究模式 | A/H 医药行业与上市公司研究（V1 先窄场景） |
| 上游基线 | FinSight `8fc5bce28ac201c713219a47d30367c6a84342f4` |
| 标准运行时 | Python 3.11 |
| 关联文档 | [项目管理](../PROJECT_MANAGEMENT.md)、[总体架构](../ARCHITECTURE.md)、[医药投研 RAG V1 方案](./PHARMA_RAG_V1.md) |

### 维护约定（重要）

1. 本文件**只增不删**。已经写入的条目、结论、代码引用不得删除或改写原意。
2. 新讨论**追加**在文末，使用递增编号与日期，格式：`## 讨论 N（YYYY-MM-DD）：标题`。
3. 对旧结论的更正**不覆盖原文**，而是在其后新增"补充/更正"小节，并注明所针对的条目编号。
4. 每条结论必须区分：源码事实（附 `文件:行号`）、论文主张、个人推断。推断需显式标注。
5. 所有源码结论绑定上游固定基线 `8fc5bce`；上游同步后需复核行号并追加说明，而不是修改历史条目。
6. 文中的运行产物、报告样例、密钥一律不得入库。
7. 自讨论 6 起，每条讨论以「本次问题（摘要）」开头，记录触发该讨论的问题；此前各条保持原样。

---

## 讨论 1（2026-09-18）：FinSight 运行机制、数据链路与最小改造

**背景**：为设计医药投研智能体，需要先确认 FinSight 的 Agent 调用协议、DeepSearch 数据链路、预处理能力和"仅先跑通医药行业"的最小改动边界。以下为讨论结论。

### 1.1 BaseAgent 是调用其他 Agent 的分配模型吗？如何调用？

**结论：不是。** `BaseAgent` 是所有 Agent 的基类与统一动作循环，不是路由器，也不是模型分配器。存在两层调度，且都不由模型分配：

- **顶层调度由代码写死**：`run_report.py:108-165` 静态构造任务列表，采集=优先级 1、分析=2、写作=3；`run_report.py:196-257` 按优先级分组，组内 `asyncio.gather` 并发，组间串行。任务归属由 `agent_class` 决定。
- **Agent 之间通过 `call_tool` 互调**：`_agent_tool_function`（`src/agents/base_agent.py:394`）被注入代码执行器的全局命名空间，LLM 生成的代码以 `call_tool(tool_name='deepsearch agent', query=...)` 形式调用。若目标是 `BaseAgent`，则经 `AsyncBridge` 调 `target_tool.async_run(input_data=kwargs)` 并取 `response['final_result']`（`base_agent.py:446-452`）；若目标是 `Tool`，则调 `api_function(**kwargs)` 并返回 `[item.data for item in response]`（`base_agent.py:453-465`）。

**工具对模型如何可见**：`_get_api_descriptions`（`base_agent.py:475-484`）对普通 `Tool` 列出 name/description/**parameters**，对 `Agent` 只列 `AGENT_NAME`/`AGENT_DESCRIPTION`，**没有参数 schema**。`task` 缺省自动填当前任务（`base_agent.py:447-448`），`query` 必须由模型显式提供。

**协议不一致导致数据质量风险吗？会，且有多处。**

| 问题 | 位置 | 后果 |
| --- | --- | --- |
| Tool 分支返回**数据列表**，Agent 分支返回**最终文本**，返回形状不同 | `base_agent.py:446-465` | 调用方只能猜返回类型，易误用 |
| Agent 当工具时无参数 schema | `base_agent.py:481-482` | 模型可能不传 `query`，`DeepSearchAgent._prepare_init_prompt` 直接 `raise ValueError`（`search_agent.py:56-57`） |
| 异常统一吞掉返回 `[]` | `base_agent.py:470-473`、`src/tools/base.py:37-45` | "调用失败/限流/字段错"与"确实没有数据"不可区分 |
| 响应解析靠正则取**最后一个** XML 标签，无 schema 校验 | `base_agent.py:598-616` | 格式异常即降级为 final 全文 |
| 工具结果溯源是 LLM 自由文本 | `data_collector.py:68-85` 与采集提示词 | URL/来源可写错，无法强制校验 |
| `ToolResult.__eq__/__hash__` 只用 name+description | `tools/base.py:88-92` | 不同报告期/口径被当作同一条，去重与缓存串味 |
| 同一 DeepSearchAgent 重复调用命中旧 checkpoint 直接返回缓存 | `base_agent.py:515-521`（`resume` 默认 True） | 第二次查询可能返回第一次结果 |

**对 Pharsight 的启示**：把"自由文本 + XML 动作"替换为结构化 tool call/result；显式区分空结果、超时、权限不足、字段解析失败；用 typed `EvidenceResult` 承载来源与定位；修复跨查询的状态复用语义。

### 1.2 DeepSearch 如何收集数据？权威性、交付物与增量性

**收集方式**（`src/agents/search_agent/search_agent.py`，默认工具 `[SerperSearch(), Click()]`，`enable_code=False`）：

- **search**（`search_agent.py:91-157`）：调用搜索引擎取 top 结果（title/link/summary），写入 `self.valid_links` 与 `memory.add_data(SearchResult)`。
- **click**（`search_agent.py:159-238`）：URL 必须在先前搜索结果白名单内，否则拒绝；`Click` 用 crawl4ai 转 markdown、`.pdf` 用 `pdfplumber` 抽纯文本，**截断到 6000 字符**（`src/tools/web/web_crawler.py:99-115`），写入 `memory.add_data(ClickResult)`。
- **收尾**：把对话历史交给 LLM 生成带 `[N]` 引用的自然语言报告，包成 `DeepSearchResult` 写入 memory（`search_agent.py:291-320`）。

**权威性保证**：基本只有提示词层面的软约束——prompt 要求只引用搜索结果中的 URL、优先引用浏览过的页面、不得编造、尽量浏览至少 5 个页面（`search_agent/prompts/general_prompts.yaml`）。**没有**来源权威分级、域名白名单、一手来源优先、发布/更新日期与期间/单位校验、去重、原文定位。关键风险：接近迭代上限时会把**只出现在搜索摘要、从未点开的链接**与已浏览页面一起列为 "VERIFIED SOURCES AVAILABLE FOR CITATION"（`search_agent.py:240-266`）。对医药结论，"搜索摘要即证据"不可接受。

**交付物（三类，均进 Memory）**：

1. `SearchResult`：搜索摘要（链接 + snippet），`src/tools/web/base_search.py:10-26`。
2. `ClickResult`：网页正文前 6000 字符 / PDF 全文文本 + URL，`web_crawler.py:200-218`。
3. `DeepSearchResult`：最终自然语言总结报告（含 `[N]`），`search_agent.py:323-334`。

另有 `memory.log` 记录每次调用的输入/输出/错误。需注意链路细节：`Memory.get_collect_data` **始终排除 `DeepSearchResult`**（`variable_memory.py:337-347`），而 `DataAnalyzer` 又 `exclude_type=['search','click']`（`data_analyzer.py:113`）。因此分析器默认看不到原始网页与搜索摘要，只能看到结构化工具结果，并通过 `get_data_from_deep_search(query)` 现场补充检索。

**全量还是增量**：

- **按问题增量**：每次 `async_run` 接受一个 `query`，只收集该问题相关内容。
- **同 run 内共享 Memory 累加**：所有任务共享一个 `Memory`，结果追加。
- **任务级复用**：`Memory.get_or_create_agent` 用 `task_key`（采集=task，分析=analysis_task）匹配 `task_mapping`，命中则恢复而非重采（`variable_memory.py:128-141, 168-179`）；生成的补充任务同样缓存。
- **无跨 run 增量**：新一次运行是新的 Memory/working_dir，会重新采集。医药季度更新需要 Pharsight 自行增加时间窗与增量策略。

### 1.3 FinSight 是否做数据预处理？如何处理？

**结论：几乎没有专门的确定性预处理管线，主要是"抓取→转文本→截断→交给 LLM"。**

- **网页**：crawl4ai 转 markdown；PDF 用 `pdfplumber` 抽纯文本；正文**硬截断 6000 字符**（`web_crawler.py:107-114`）。无清洗、去重、日期/单位解析，PDF 不保留页码定位。
- **结构化工具**：返回 DataFrame/dict，`ToolResult.brief_str()` 只展示前 5 行或前 100 字符（`tools/base.py:58-83`）；无字段口径映射、无币种/单位/期间归一。
- **LLM 代做预处理**：采集 prompt 要求保存清洗后的内容、合并同类指标、优先表格/结构化摘要（`data_collector/prompts/prompts.yaml:33-48`）；`_save_result` 把变量与 LLM 写的 name/description/source 一并存入 Memory（`data_collector.py:68-85`），因此清洗过程不可复现、无 schema。
- **分析器不筛选**：把全部非 web 采集结果格式化进 prompt（`data_analyzer.py:144-161`），原有相关性检索被注释。`Memory.retrieve_relevant_data` 存在（对 `name+description` 做 embedding，`variable_memory.py:414-441`），但默认不调用。
- **配置/报告侧**：`Config._set_dirs` 会把插值后的完整配置（含 API key）写入 `config.json`（`config.py:101-102`）；报告生成用 embedding 相似度做引用/图片匹配，属后处理而非证据预处理。

**对 Pharsight 的启示**：数据质量目前主要靠 LLM 自律；必须补 `EvidenceItem` 级规范化（快照、哈希、页码/字符区间定位、口径、单位、币种、期间）。

### 1.4 只先"跑通"医药行业投研的最小改动

**边界**：按 `AGENTS.md`，不在 `FinSight/` 内改动，所有改动位于 `pharsight/`。因流水线本身领域无关，医药化最小改动基本是配置与提示词/大纲内容，代码最多一行。

**最小可行路径（不改代码即可尝试）**：

1. 配好本地 `.env`（gitignore）：`DS_MODEL_NAME`/`DS_API_KEY`/`DS_BASE_URL`、`VLM_*`、`EMBEDDING_*`、`SERPER_API_KEY`。缺任一被 `${VAR}` 引用的变量，`Config` 会直接抛错（`config.py:60-62`）。
2. 将 `pharsight/my_config.yaml` 改为行业模式（`run_report.py:39` 硬编码读取该文件名）。以 `docs/example_configs/my_config_industry.yaml` 为模板：
   - `target_type: 'financial_industry'`（`PromptLoader` 找不到 `financial_industry_prompts.yaml` 时回退到 `financial_prompts.yaml`，`prompt_loader.py:26-35`）
   - `language: 'zh'`
   - `outline_template_path: 'src/template/industry_outline_zh.md'`
   - `reference_doc_path` 指向 docx 模板
   - `custom_collect_tasks` / `custom_analysis_tasks` 换成医药行业内容
3. 运行 `python run_report.py`。采集主要依赖 DeepSearch；金融工具对医药基本无用但仍会被挂载，构成噪声。

**建议同时改的一行代码**：`pharsight/run_report.py:154` 将写作任务的 `'task_type': 'company'` 改为 `'task_type': 'industry'`，否则行业报告的封面/定位可能不正确。

**可选低成本增强（只改内容，不改逻辑）**：

- 复制 `src/template/industry_outline_zh.md` 为医药版大纲（把金融示例换为医药示例），配置指向新文件。
- 固定一个医药细分赛道（如 GLP-1 减重药物、创新药 CXO）与截止日，避免每次范围漂移。

**该模式的产出与局限**：

- 能得到：一份可渲染的中文医药行业长报告（Markdown/DOCX；PDF 取决于本机 Word 与 `docx2pdf`）。
- 得不到：NMPA/CDE、ClinicalTrials、PubMed、医保集采等一手证据，可靠引用，可复现计算。
- 已知风险全部存在：相似度兜底引用、来源不可定位、正文截断、异常吞成空数据、配置落盘含密钥。因此只能作为端到端冒烟，不能作为可信投研产出。

**向"可信"再进一步的最小三件事**（对应 [PHARMA_RAG_V1.md](./PHARMA_RAG_V1.md) 阶段 1–2）：

1. `EvidenceResult` / `PharmaEvidenceSearchTool` 薄接口，先接离线 JSONL 或少量固定文档；
2. `citation_validator.py`：未知 ID、无定位、超截止时间即失败关闭；
3. 带 `[Source:<source_id>]` 的医药提示词与程序化校验；当前实现沿用完整主流程，并以 `enable_chart=False` 关闭图表/VLM。

当前实现已经按 ADR-009 接入 `ReportGenerator` 的医药精确引用分支，但不修改 `IndexBuilder`，并可关闭 VLM；上述三项的证据契约仍是后续图检索的兼容边界。

### 1.5 本讨论产生的待办与开放问题

以下仅为讨论线索，进入 `Ready` 前需在项目管理中指派责任人与验收标准：

1. 是否保留任意 LLM 代码执行？隔离与网络策略是什么？
2. 结构化 tool call/result 的 schema，以及空结果/超时/权限/解析失败的错误分类。
3. `EvidenceItem → Claim → Calculation` 的唯一存储格式与版本策略。
4. 重复调用同一 Agent 时的状态语义（修复"命中旧 checkpoint 返回缓存"）。
5. 医药一手来源（NMPA/CDE、ClinicalTrials、PubMed、医保集采、A/H 披露）的接入顺序与许可。
6. 首个医药赛道、公司、药物、适应症与信息截止时间。
7. 是否以 V1 最小三件事作为"冒烟之后"的第一个正规增量。

---

## 讨论 2（2026-09-18）：Agent 调用拓扑、全文获取与非公开数据接入

### 2.1 Agent 之间是否互相调用？是否每次都是采集-分析-写作？有没有其他流程（如回答疑问）？

**调用拓扑是星形，不是网状。** 顶层编排由 `run_report.py`（以及 `demo/backend/app.py:591-798` 的同构实现）完成：编排器把任务交给 DataCollector / DataAnalyzer / ReportGenerator 三个"阶段 Agent"，三者共享同一个 `Memory`，**三者之间不互相调用**。

唯一的跨 Agent 调用边是 `任意阶段 Agent → DeepSearchAgent`：

- `data_collector.py:46`、`data_analyzer.py:72`、`report_generator.py:89` 都把 `DeepSearchAgent` 作为工具挂载。
- 调用路径：DeepSearch 以 `Tool`-like 对象经 `_agent_tool_function`（`base_agent.py:394,446-452`）触发；ReportGenerator 另有 `_handle_search_action`（`report_generator.py:188`）与 `_get_deepsearch_result`（`report_generator.py:118-131`）；DataAnalyzer 侧为 `get_data_from_deep_search`（`data_analyzer.py:83-90`）。
- 通用机制上任何 Agent 都能被当工具调用（`base_agent.py:446-452` 的 `BaseAgent` 分支），但现有代码只使用了 DeepSearch 一个。

**是否每次都走采集→分析→写作？产品入口是固定流水线，但存在若干受控变体。**

| 变体 | 说明 | 位置 |
| --- | --- | --- |
| CLI 全流程 | 采集(1)→分析(2)→写作(3)，组间串行、组内并发 | `run_report.py:108-257` |
| Demo 全流程 | 与 CLI 同构 | `demo/backend/app.py:591-798` |
| 断点续跑 | 已完成 Agent 跳过；任务按 `task_key` 复用 | `run_report.py:211-222`；`variable_memory.py:143-264` |
| 任务生成 | 开始时用 LLM 生成补充采集/分析任务并与人工任务合并 | `run_report.py:61-101`；`variable_memory.py:443-515` |
| Programmatic 单 Agent | 可直接只跑 DeepSearch、DataCollector 或 DataAnalyzer | `tests/agents/*`；README「Complete Code Examples」 |
| 通用深研（无采集阶段） | 生成分析任务 → 逐任务 DataAnalyzer → ReportGenerator，跳过 DataCollector | README Example 2 |
| Agent 内部多阶段 | Analyzer：分析→绘图；Generator：大纲→分节→后处理 | `data_analyzer.py:460-550`；`report_generator.py:772-871` |

**是否有"回答疑问"的专门流程？没有独立的问答编排。** 问答能力以组件形式存在：DeepSearchAgent 的输入就是一个 `query`，输出是带引用的自然语言报告（`search_agent.py:17,51-76,291-320`）；DataAnalyzer 的 `analysis_task` 本身就是一个问题。系统里**没有意图路由器**——`target_type` 只切换提示词、工具和模板，不改变流水线形状。因此"只想问一个问题"目前只能手工只调 DeepSearch 或只调 DataAnalyzer，而不是产品内置的一条流程。

> 讨论线索：若 Pharsight 需要"问答模式"，应做成独立受控 subset（单一或多个证据工具 → DataAnalyzer 纯文本 → 引用校验），而不是复用完整研报管线。

### 2.2 搜索只截断 6000 字符，如何获取全部数据？

**现状：拿不到。** `Click.api_function` 先取得完整内容——HTML 经 crawl4ai 转 markdown（`content = str(result.markdown)`），PDF 经 `pdfplumber` 逐页拼接为 `full_text`——随后只把 `content[:6000]` 放进 `ClickResult.data`（`web_crawler.py:99-114`）。**其余内容被直接丢弃，未落盘、未缓存、未保留页码**，因此事后无法恢复。

影响：

- 年报、招股书、长网页主要内容丢失，且保留的是页面前段而非信息密度最高处；
- PDF 的页码/表格结构在拼接时已丢失（`web_crawler.py:187-194` 只做 `page.extract_text()` 拼接）；
- 提示词中 `brief_str()` 对部分类型只展示前 100 字符（`tools/base.py:58-83`），进一步压缩；
- 另注：该函数 docstring 写 "up to 10,000 chars"，与实现的 6000 不一致（`web_crawler.py:76-86`）。

要拿全文必须改这一层。可选方案（按推荐度）：

1. **原始快照 + 分块检索**（Pharsight 推荐）：把完整 markdown/PDF 文本按内容哈希写入被忽略的本地目录，`ClickResult` 只携带 `snapshot_id`、URL、哈希与定位；由 `EvidenceBackend` 分块/建索引，模型只看 top-k 片段。这是 `PHARMA_RAG_V1.md` 的既定方向。
2. **直接去掉/放宽截断**：简单，但会把超长文本灌入 prompt，带来上下文与成本问题，且仍无定位。
3. **PDF 保留分页**：逐页保存文本与页码（必要时坐标框），不要先拼接再截断。
4. **本地重取**：分析代码再抓一次原 URL——仍走同一工具，不解决根本问题。
5. **LLM 摘要分支**：代码中有一段被注释的"按任务抽取相关片段"实现（`web_crawler.py:116-134`），属摘要而非全文，可作降级方案。
6. **扩大抓取数量**：多页/多次检索只能覆盖多个"前 6000 字符"，无法拼出单篇全文。

> 讨论线索：全文获取与证据定位应在**抓取/接入层**解决，而不是在 prompt 层补偿。

### 2.3 如何添加自己获取的非公开数据？

按数据进入系统的位置，有四条路径：

**路径 A：自定义 Tool（最贴合上游契约）**

- 继承 `src/tools/base.py` 的 `Tool`，实现 `api_function(...)`，返回 `[ToolResult(name, description, data, source)]`；放到 `src/tools/<category>/` 下任意 `.py`。
- `_auto_register_tools` 递归发现并注册所有 `Tool` 子类（`tools/__init__.py:133-175`），按子目录名归类。
- DataCollector 默认挂载**除 `web` 外的全部分类工具**（`data_collector.py:48-53`），与 `target_type` 无关；新工具会立即通过 `call_tool(tool_name=...)` 可用。
- 注意当前契约缺陷：异常被吞成 `[]`（`base_agent.py:470-473`、`tools/base.py:37-45`），来源为自由文本；Pharsight 应改为显式状态码与结构化来源。

**路径 B：预置 Memory（一次性、最简单）**

- 运行前构造 `Memory` 并 `memory.add_data(ToolResult(...))`，再调 DataAnalyzer / ReportGenerator；分析器读 `memory.get_collect_data()`（默认排除 search/click/deepsearch，`variable_memory.py:337-347`）。
- 适合固定小批文档的冒烟验证，但不具备检索与定位能力。

**路径 C：Ingest + EvidenceBackend（Pharsight V1 方向）**

- 走 `EvidenceBackend.ingest(manifest)`，再经 `PharmaEvidenceSearchTool` 检索；文档 manifest 至少登记：`document_id`、`source_url/stable_source_id`、`publisher`、`document_type`、`published_at`、`retrieved_at`、`sha256`、`license_or_terms_note`、`report_cutoff`、`parser_name/parser_version`（见 `PHARMA_RAG_V1.md` §7.1）。
- 这是唯一能同时满足"非公开数据 + 可定位证据 + 可复现"的路径。

**路径 D（补充）：注入执行器变量**

- 覆写 `_prepare_executor`，用 `code_executor.set_variable(...)` 暴露本地读取函数，让 LLM 代码直接读私有文件（`data_analyzer.py:77-108` 展示了该模式）。灵活，但更贴近"任意代码执行"的风险面，仅建议受控环境使用。

**治理与合规约束（来自 `AGENTS.md`）**：

- 非公开、付费、受限原始数据**不得入库**；放本地被忽略目录，仅在 `.env.example` 记录变量名。
- 接入前记录许可与再分发限制；测试默认 mock 网络，实时/私有来源测试须 opt-in。
- 每条记录保留 publisher、日期、期间、单位/币种、定位（页码/字符区间）与转换说明，并区分"来源事实/确定性计算/明确假设/模型判断"。

> 讨论线索：非公开数据应作为**证据生产者**接入统一证据层，而不是绕开证据链直接塞进 prompt。

### 2.4 本讨论新增的待确认项

1. Pharsight 是否需要产品级"问答模式"，还是仅保留组件能力？
2. 全文快照的存储格式、分块粒度与定位字段（页码 vs 字符区间 vs 坐标）。
3. 非公开数据的 manifest 必填字段与审批人。
4. 自定义 Tool 的错误状态枚举与 `ToolResult` 扩展方式（兼容上游 vs 新 `EvidenceResult`）。

---

## 讨论 3（2026-09-18）：数据来源与文件类型处理（PDF 及其他）

### 3.1 FinSight 只能搜网页吗？

**不是。** 数据来源分两条通道：**结构化数据工具**（调用 API / 数据源，返回 DataFrame 或 dict）与**网页工具**（搜索 + 抓取 + 图片搜索）。

**结构化数据工具**（全部自动注册，见 `tools/__init__.py:133-175`；DataCollector 默认挂载除 `web` 外的全部分类，`data_collector.py:48-53`）：

| 分类 | 代表工具（`name`） | 底层来源 |
| --- | --- | --- |
| financial | Stock profile、Shareholding structure、Equity valuation metrics、Stock candlestick data、Balance sheet、Income statement、Cash-flow statement、CSI 300/Hang Seng/SSE/Nasdaq daily data | akshare、efinance、yfinance（`stock.py:6-16`、`company_statements.py:2-7`、`market.py:1-2`） |
| macro | 中国杠杆率、LPR、失业率、社融、GDP/CPI/PPI、进出口、财政、货币、存准、外储黄金、EPU 等 21 项；美国 CPI/GDP/失业率/利率（FRED）、美股指数 | akshare；FRED（`us_macro.py:22-33,38-176`，需 `FRED_API_KEY`）；yfinance |
| industry | 工业增加值、工业产量同比、官方/财新 PMI、CPI、GDP、PPI、消费者信心、社零、RPI 等 | akshare（`industry.py:1`） |

注：`industry` 类更接近宏观/总量指标，不是真正的行业知识库；且全部是金融/宏观工具，**没有任何医药专用工具**。

**网页工具**：

- 搜索后端：Bing（requests）、Bocha、Serper/Google、DuckDuckGo、Sogou、金融站点站内搜索（requests/Playwright）、Bing（Playwright）（`search_engine_requests.py`、`search_engine_playwright.py`）。
- 抓取：`Click`（name="Web page content fetcher"），见 `web_crawler.py:26`。
- 图片搜索：`BingImageSearch`（`search_engine_requests.py:430`），返回 `ImageSearchResult`（image_url + page_url，`base_search.py:29-41`）。

### 3.2 PDF 如何处理？

**唯一入口是 `Click`，且处理很弱。**

- 仅当 URL 以 `.pdf` 结尾时走 `extract_pdf_text_async`：用 `pdfplumber` 逐页 `extract_text()` 后**无条件拼接**，再把整体截断到 6000 字符（`web_crawler.py:96-114,169-197`）。
- 丢失：页码、坐标框、表格结构、页内顺序、发布日期；对**扫描版 PDF 无 OCR**，无文本层时返回空/极少文本。
- 若 PDF 是经查看器打开的链接、或 URL 不以 `.pdf` 结尾，则当作 HTML 交给 crawl4ai，通常抓不到正文。
- `src/utils/pdf2fig.py` 能把 PDF 每页渲染成缩略图拼成一张联络图（`pdf2image`/Poppler），但它是一个**独立 `__main__` 脚本，未接入任何 Tool/Agent/流程**，不参与证据链。

结论：PDF 只能得到"拼接后的前 6000 字符纯文本"，无法支撑引用定位，也不适用于年报/招股书/说明书等长文档。

### 3.3 其他类型（docx/pptx/xlsx/csv/图片）如何处理？

- **docx**：仅用于报告模板（`reference_doc_path`）和输出渲染（Pandoc 生成 + `docx2pdf` 转 PDF，`report_generator.py:8,601-649`）；**不作为输入解析**。
- **pptx / xlsx / csv**：代码中**没有任何读取器**（`code_executor.py:28` 只是把 `csv` 列入可用内置模块）；也没有登录/上传入口，Demo 只做配置、执行与产物下载。
- **图片**：`BingImageSearch` 只返回图片 URL；DataAnalyzer 用 matplotlib 生成图表、VLM 只对这些图表做批评与配文（`data_analyzer.py:300-392`）；**没有对任意图片/扫描页做 OCR 或视觉取证的通道**。
- 理论上代码执行器可 `import pandas` 读本地文件（`code_executor_async.py:14`），但没有封装成可发现的 `Tool`，因而不在 Collector 的自动工具面内。

### 3.4 对 Pharsight 的启示

1. 医药证据大量来自 PDF（说明书、年报、招股书、指南、文献）与表格，**不能依赖 `Click`**；必须在接入层增加解析器：保留文档/页/元素三级 ID、文本与规范化文本、页码与字符区间（必要时坐标）、表格单元格关系、解析告警、源文件哈希与解析器版本（对应 `PHARMA_RAG_V1.md` §7）。
2. 扫描件需 OCR，并明确 OCR 置信度与"仅供线索"状态。
3. 结构化来源（NMPA/CDE、ClinicalTrials、医保集采、A/H 披露、文献库）应做成 typed 工具并输出结构化证据，而不是塞回网页抓取。
4. 非公开文件走 `EvidenceBackend.ingest`，不经过 `Click`（参见讨论 2.3 路径 C）。

### 3.5 本讨论新增的待确认项

1. 首批解析器选型（本地文本 PDF vs 扫描件 OCR vs 外部数据层导出）与许可。
2. 表格证据的最小结构化表示（页内表格 vs 单元格级）。
3. 图片/图表作为证据时的定位与复核方式。

---

## 讨论 4（2026-09-18）：多层 iteration 的作用与每一轮在做什么

FinSight 的 "iteration" 不是单层，而是**嵌套的多层循环**。自外向内至少有六层：

| 层级 | 循环 | 机制与默认次数 | 每一轮做什么 | 位置 |
| --- | --- | --- | --- | --- |
| L0 流水线 | 优先级分组 | 每组执行一次，非迭代 | 采集组(1)→分析组(2)→写作组(3)，组间串行、组内并发；resume 时跳过已完成 Agent | `run_report.py:196-257` |
| L1 Agent 动作循环 | `BaseAgent.async_run` | 各 Agent 不同（见下） | 推理→动作→观察：LLM 生成 → 解析最后一个标签 → 执行 → 回填历史 → 存检查点 | `base_agent.py:529-565` |
| L2 LLM 重试 | `AsyncLLM.generate` | 每模型 ≤5 次 | 429/5xx 指数退避 + 抖动；400 上下文超限时删掉首条 user/assistant 消息后重试 | `llm.py:107-160` |
| L3 章节循环 | ReportGenerator 分节 | 每个大纲章节一次 | 每节独立跑一遍 L1：写作、按需补检索、润色 | `report_generator.py:856-871` |
| L4 图表循环 | Analyzer 绘图 | 外层 VLM ≤3，内层代码 ≤3 | 生成绘图代码并执行 → VLM 批评 → 未达标则回填批评重画 | `data_analyzer.py:318-453` |
| L5 多跳搜索 | DeepSearch 内部 | ≤30 | 每轮只做一个动作：一次搜索、一次点开、或产出报告 | `search_agent.py:291-320` |

### L1 Agent 动作循环（核心）
`while current_round < max_iterations+1`（`base_agent.py:529`）：
1. `llm.generate(conversation_history, stop=stop_words)`（`:533`）；
2. `_parse_llm_response` 取**最后一个** XML 标签（`:534, 598-616`）；
3. `_execute_action` 分发到 `_handle_<action>_action`（`:539, 619-626`）；
4. 把 assistant 回复与执行结果追加进历史（`:544-545`）；
5. 每轮保存检查点（`:551-562`）；
6. 动作 `continue=False` 或达到上限时结束；到上限则 `_handle_max_round` 让 LLM 汇总（`:568-570, 595-596`）。

各 Agent 只是在这台同一引擎上换了"动作语义"和次数：

- **DataCollector**（`max_iterations=20`，`run_report.py:118`）：每轮用 `<execute>` 写 Python，调 `call_tool(...)` / `save_result(...)`，或 `<final_result>` 收尾；作用是"规划→取数→保存清洗结果"。
- **DataAnalyzer phase1**（`max_iterations=20`）：每轮用 `<execute>` 探索数据、计算、调用 DeepSearch，产出 `<report>`；到上限时 `_handle_max_round` 生成 REPORT_DRAFT JSON（`data_analyzer.py:172-212, 460-506`）。
- **DeepSearchAgent**（`max_iterations=30`，`search_agent.py:294`）：每轮只允许一个 `<search>` / `<click>` / `<report>`；search 与 click 返回 `continue=True`，report 返回 `False`。作用是"多跳检索与阅读，直到证据足够或轮次耗尽"。
- **ReportGenerator**：Phase0 大纲本身是一轮 L1（`generate_outline`，`report_generator.py:744-768`）；Phase1 每个章节各跑一轮 L1，允许写作中动态补检索（`report_generator.py:187-194, 856-871`）；Phase2 后处理按子阶段推进（图片→摘要标题→封面→引用→渲染），非 LLM 动作循环。

### 结束条件与注意点
- 生产入口默认不传 `stop_words`（各 Agent 覆盖为 `[]`），因此是否结束主要由"模型是否输出终态标签或无标签"决定；`_parse_llm_response` 无标签时按 final 处理（`base_agent.py:605-606`）。
- 循环条件是 `current_round < max_iterations+1`，加上 `current_round >= max_iterations` 的收尾判断，实际轮次与直觉的 `max_iterations` 存在一位偏差（`base_agent.py:529, 568`）。
- 嵌套示例：DataCollector 的第 N 轮代码调用 `call_tool('deepsearch agent')` → DeepSearch 自己再跑最多 30 轮 L5 → 返回摘要 → Collector 继续第 N+1 轮。报告统计中约 983 次搜索 / 469 个页面，就是这些嵌套循环叠加的结果。

### 作用总结
- L2 是**容错**；L1 是**研究推理与工具编排的主引擎**；L3/L4 是**质量迭代**（分节写作、图表视觉复核）；L5 是**信息覆盖迭代**（多跳检索）。四者叠加使系统能做"边查边算边写"，代价是长流程、高成本与更强的状态管理需求。

### 本讨论新增的待确认项
1. Pharsight 是否需要保留全部四类迭代，还是先用受控单轮/少轮版本。
2. 每阶段迭代上限、预算与"证据充足即停止"的判定条件。
3. 结束协议的显式化（终态标签 vs 结构化 tool call）。

---

## 讨论 5（2026-09-18）：搜索多轮是否重复采集、DataAnalyzer 多轮的意义

### 5.1 "多轮"分布在不同层，不是同一件事

- **采集层多轮** = 多个采集任务（人工 + LLM 生成，`run_report.py:92-100`）× 每个任务最多 20 轮采集动作（`run_report.py:118`）。
- **搜索层多轮** = 每个 DeepSearchAgent 最多 30 轮，每轮一次 `<search>` 或 `<click>`（`search_agent.py:294`），是多跳检索。
- **分析层多轮** = 单个分析任务最多 20 轮 Python 计算（`data_analyzer.py:460-506`），不是重复采集。
- **写作层多轮** = 每个大纲章节各一轮；每张图另有 ≤3 轮视觉迭代。

### 5.2 搜索会不会每次都搜到相同数据？

**设计意图是不会。** prompt 明确要求 "No redundancy: Never repeat confirmed searches or revisit previously opened links"（`search_agent/prompts/general_prompts.yaml:22`），每轮应换子问题/换角度；`<click>` 还有白名单——URL 必须出现在先前搜索结果里，否则拒绝（`search_agent.py:165-185`）。

**但硬性去重缺失，重复确实会发生：**

- `Memory.add_data` 只是 `list.append`，**没有按 URL 或内容哈希去重**（`variable_memory.py:292-294`）。
- `<click>` 只校验 URL 是否来自搜索结果，**不禁止重复点开同一 URL**（`search_agent.py:165-185`）。
- 搜索查询可以重复；多个采集/分析任务主题重叠时，跨任务重复不可避免（各任务独立、共享 Memory 但只追加）。
- `ToolResult.__eq__/__hash__` 虽有定义（`tools/base.py:88-92`），但**没有被用于去重**。

**更严重的是"根本没重新搜"：** DeepSearch 的 `async_run` 默认 `resume=True`（`search_agent.py:297`），而 `BaseAgent.async_run` 在加载到含 `return_dict` 的检查点时会**直接返回缓存结果、不执行新一轮**（`base_agent.py:515-521`）。采集/分析/写作都把 DeepSearch 作为固定单实例（`self.tools[0]`，`data_collector.py:46`、`data_analyzer.py:72`、`report_generator.py:89`），因此同一实例的第二次及以后调用可能返回**第一次的结果**，与本次 query 无关。这是需要在 Pharsight 修复的状态语义问题。

结论：既可能重复采集，也可能"该搜没搜"。修复方向：URL/内容哈希去重、以 query 为键的缓存、跨任务证据去重，以及让 DeepSearch 每次调用真正执行（或显式支持同 query 续跑）。

### 5.3 DataAnalyzer 跑多轮有什么意义？

**它不是在重复采集，而是对已采集数据做多轮计算与推理。**

- 引擎是 L1 动作循环，代码执行器**跨轮保留变量**（notebook 式状态），第 N 轮可直接使用第 N−1 轮生成的 DataFrame/变量（`data_analyzer.py:92-108` 注入 `collect_data_list`/`get_existed_data`）。
- 每轮可做：加载与清洗、合并与连接、派生指标计算、异常/一致性检查、中间统计与图，或调用 `get_data_from_deep_search` 补证据。
- 价值：单轮一次性写大段代码既易错又无法观察；多轮支持"看输出→纠错→继续"，并在分析中途补齐证据。论文消融显示去掉动态搜索会使事实性 7.0→5.9、分析深度 7.9→5.7。
- 结束：模型输出 `<report>` 或无标签即 final；到轮次上限走 `_handle_max_round` 生成结构化 REPORT_DRAFT JSON（`data_analyzer.py:172-212`）。之后才进入独立的图表阶段（L4）。
- 局限：初始化时把**全部**采集数据塞进 prompt（`_format_collect_data`，`data_analyzer.py:144-161`），相关性检索被注释；轮次越多，上下文与成本越高。

### 5.4 本讨论新增的待确认项

1. Pharsight 的去重与缓存键策略（URL、内容哈希、query、snapshot）。
2. DeepSearch 实例复用与 `resume` 语义的修复方案。
3. Analyzer 是否改为"按 query 检索 top-k 证据注入"，而非全量注入。

---

## 讨论 6（2026-09-18）：FinSight 论文的评测标准、有效性论证与医药迁移分析

### 本次问题（摘要）

1. FinSight 论文如何评判投资研究报告的生成质量？
2. 它如何证明其多智能体设计的有效性？
3. 这套评判标准有何优点与局限？
4. 能否直接迁移到医药投研？

### 6.1 评测体系（论文主张，arXiv v1 `2510.16844`）

- **Benchmark**：20 个研究目标（10 个公司级 + 10 个行业级；既有调研文档记录 ACL 正式版口径为 5 A 股 + 5 港股 + 10 行业，属版本差异）。每个目标配一份专业券商"黄金参考报告"；筛选门槛为报告 **>20 页且含 >20 张图表**。
- **9 项指标（每项 0–10）**，分三维：
  - 事实准确性 Factual Accuracy：核心结论一致性（Core Conclusion Consistency）、文本忠实性（Textual Faithfulness）、图文一致性（Text-Image Coherence）
  - 信息有效性 Information Effectiveness：信息丰富度（Information Richness）、覆盖度（Coverage）、分析洞见（Analytical Insight）
  - 呈现质量 Presentation Quality：结构逻辑（Structural Logic）、语言专业性（Language Professionalism）、图表表现力（Chart Expressiveness）
- **评测流程**：以 **Gemini-2.5-Pro** 为评委；采用 **list-wise**（候选报告与黄金报告一并给出）；按"与黄金报告相关/无关"设计两类提示词；以黄金报告为基准（黄金≈7 分）做 comparative scoring，1–10 分并附一句理由。
- **主结果**：FinSight 总分 **8.09**，高于 Gemini Deep Research 6.82、OpenAI Deep Research 6.11；其中 Faith. 7.50、T-I 7.85、Rich 8.70、Cover 8.30、Ins 8.45、Vis 9.00；Cons 6.85 略低于 Gemini 的 7.10。
- **消融（事实/分析/呈现）**：完整 7.0/7.9/8.0；去视觉迭代 6.9/7.2/7.5；去两阶段写作 6.4/5.9/6.3；去动态搜索 5.9/5.7/6.4。

### 6.2 它如何证明智能体的有效性

- **外部对比**：与 GPT-5 / Claude-4.1 / DeepSeek-R1 + 搜索，以及 Grok / Perplexity / OpenAI / Gemini Deep Research 比较，主结果领先。
- **内部消融**：逐一移除 VLM 图表迭代、两阶段写作、动态搜索，显示三个组件各自对指标的贡献。
- **人工与统计**：以自动 LLM 评委为主；公开信息中未见评委间一致性、人工专家双盲评分或多轮运行方差分析。
- **判读**：有效性证据是"相对某基准、由某 LLM 评委给出的排序优势 + 消融一致性"，属于**相对、模型评判**的证据，不等于绝对真值或生产可靠性证明。

### 6.3 该标准的优点

- 多维覆盖事实/分析/呈现，且含引用忠实性与图文一致性，不只评估"语言流畅度"。
- 以真实券商报告为锚，贴近专业标准；list-wise + 基准锚定（黄金≈7）缓解跨报告评分漂移。
- 用 VLM 评估图表，覆盖多模态质量。
- 同一套指标贯穿主实验与消融，内部一致性较好；自动化、可扩展、相对低成本。

### 6.4 该标准的局限

- **单一 LLM 评委**（Gemini-2.5-Pro），无多评委校准或人工一致性报告；存在位置、篇幅、自我偏好等已知偏差。
- 分数是**相对黄金报告**的，非绝对；"黄金=7"是人为锚点。
- **核心结论一致性**把"与黄金不同"与"错误"混同；论文亦承认 FinSight 因追求更全面的数据而 Cons 略低。
- **Coverage** 以覆盖黄金信息为导向，可能奖励"像黄金"而非"更正确/更有新意"。
- **事实性不是 claim 级验证**：Textual Faithfulness 由评委整体判断，无逐条蕴含、数字/单位/日期一致性、来源权威性核验；引用参照物是黄金报告而非一手来源。
- 无时间截止检验、幻觉率、拒答/证据不足处理；未报告多次运行稳定性、成本与时延。
- 样本小（20），无统计显著性；领域词表偏金融，覆盖不了临床/监管语义。
- 依赖黄金报告的可得性与版权；黄金报告本身可能有立场或错误。

### 6.5 能否直接迁移到医药投研

**可直接迁移**：

- 报告级维度：结构逻辑、语言专业性、图表表现力、信息丰富度、分析洞见，以及"以高质量券商/行业报告为锚"的思路。
- 自动评测框架与 list-wise/基准锚定方法。

**需重构，不能直接照搬**：

- 事实性必须升级为**逐 Claim 的一手来源核验**（NMPA/CDE、FDA 标签、ClinicalTrials.gov、PubMed、交易所披露），带定位与截止时间；不能用"与黄金报告一致"替代。
- 增加医药专属指标：实体正确性（公司/药品/靶点/适应症/辖区）、临床阶段与监管状态准确性、时间有效性（`as_of`）、单位/币种/期间一致性、计算可复现（患者池、渗透率、rNPV、可比估值）、跨试验可比性警告、证据可追溯（引用支持陈述 + locator）、冲突处理、缺失诚实/拒答。
- 明确区分"来源事实/确定性计算/明确假设/模型判断"，并把混淆视为扣分。
- 黄金报告仅作参考基准，事实裁定必须以一手来源为准；同时注意券商报告的版权与再分发限制。
- 评测主体需加入医药领域专家盲评，不能只靠单一 LLM 评委；高风险结论应强制人工复核。
- 评测集应固定标的与截止日，覆盖 A/H 创新药、CXO、器械、疫苗、中药等细分，并把成本、时延、可复现性作为一等指标。
- 风险：审批/试验状态误引属高影响错误，需失败关闭并显式暴露证据缺口。

### 6.6 本讨论新增的待确认项

1. Pharsight 自建评测集的目标数量、细分赛道与截止日。
2. 确定性校验与 LLM/专家评审的配比及阈值。
3. claim 级"引用支持率/数字一致率/时间有效率"的定义与实现。
4. 黄金报告的获取、许可与脱敏方式。

---

## 讨论 7（2026-09-18）：黄金参考报告从哪里来、如何构造

### 本次问题（摘要）

1. FinSight 论文中的"黄金参考报告（golden reference report）"是如何得到的？

### 7.1 来源与挑选方式（论文主张，arXiv v1 Appendix D）

- **研究目标池**：从研报聚合平台 `https://www.djyanbao.com` 选取 **5 家最热门 A 股公司 + 5 家港股公司 + 10 个代表性行业**，作为 benchmark 的研究问题（Appendix D "Questions"）。
- **黄金报告**：收集**知名中国券商**针对这些目标的**最新个股与行业研究报告**，作为"人类专家级"基准（"collect the latest equity and industry research reports from well-known Chinese securities firms"）。
- **质量筛选**：只保留 **超过 20 页且含 20 张以上图表**的报告；最终 20 个样本（10 公司 + 10 行业）。
- **Table 4 标注了每个目标对应的券商**，例如：中芯国际（688981）→ 东吴证券；寒武纪（688256）→ 东海证券；中国移动（600941）→ 中泰证券；Pop Mart / SenseTime → 中泰证券；3SBio（01530）→ 华泰证券；半导体行业 → 开源证券；食品饮料 → 华创证券；基础化工 → 中泰证券；等。

### 7.2 特点与局限

- 黄金报告是**二手来源（券商研报）**，不是发行人或监管的一手披露；含券商立场、预测与可能的错误。它提供的是"人类专业水准的呈现与观点基准"，**不等于 ground truth**。
- 每个目标通常只有**一份**黄金报告 → 评分锚定单一机构观点，机构口径差异无法体现。
- 选取标准是"最热门 + 报告足够长/图足够多"，存在**流行度与可得性偏差**，未按研究难度或关键性分层。
- 论文称"latest"，但**未说明黄金报告时点与系统生成时间/信息截止时间如何对齐**，存在时点漂移风险。
- **无人工标注一致性或仲裁流程**，也不与一手来源交叉核验；事实真伪不由黄金报告背书。
- **版权与可再分发性**：券商研报多受版权限制；公开仓库中没有与论文完整对应的基准数据、黄金报告与端到端评测脚本（与既有调研一致）。
- Table 4 含 **3SBio（生物医药）**，说明 benchmark 至少触及医药标的，但 9 项指标并未覆盖临床/监管的专属事实。

### 7.3 对 Pharsight 的启示

- 黄金报告可用于**呈现质量与分析方法**的参考基准，但**事实裁定必须回到一手来源**（NMPA/CDE、FDA 标签、ClinicalTrials.gov、PubMed、交易所披露）。
- 注意券商报告的**许可与再分发限制**：受限原文不得入库，可用脱敏摘录或自建标注集替代。
- 若自建医药评测，建议：多评审/多锚点、固定信息截止日、区分事实/计算/假设/判断，并用一手来源裁定事实。

### 7.4 本讨论新增的待确认项

1. Pharsight 是否使用券商报告作为参考；许可与留存方式为何。
2. 每目标单一锚 vs 多锚（多机构）。
3. 黄金报告与一手来源的核验流程。

---

## 变更历史

| 日期 | 变更 | 说明 |
| --- | --- | --- |
| 2026-09-18 | 创建文档并追加讨论 1 | 整理 FinSight 运行机制、DeepSearch、预处理与最小改造四问的结论 |
| 2026-09-18 | 追加讨论 2 | Agent 调用拓扑与流程变体、6000 字符截断与全文获取、非公开数据接入路径 |
| 2026-09-18 | 追加讨论 3 | 数据来源（结构化工具 vs 网页工具）与 PDF/其他文件类型处理现状 |
| 2026-09-18 | 追加讨论 4 | 多层 iteration 的层级、作用、每轮内容与结束条件 |
| 2026-09-18 | 追加讨论 5 | 搜索重复与去重缺失、DeepSearch resume 缓存问题、DataAnalyzer 多轮意义 |
| 2026-09-18 | 追加讨论 6 | FinSight 论文评测体系、有效性论证、标准优劣与医药迁移分析 |
| 2026-09-18 | 追加讨论 7 | 黄金参考报告的来源、挑选标准、版本局限与医药启示 |
