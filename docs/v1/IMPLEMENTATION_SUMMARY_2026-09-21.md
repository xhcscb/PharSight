# V1 医药投研智能体实现说明（2026-09-21）

## 1. 交付结论

本次没有更换 FinSight 的共享 Memory 和三段式编排，而是在原主干上完成了医药化纵向切片：

```text
医药研究配置与受控资料清单
  → 资料预载 / DataCollector 医药采集
  → Memory（结构化来源编号和元数据）
  → DataAnalyzer 医药分析
  → ReportGenerator 中文 Markdown 报告
  → 截止日、精确引用和声明校验
```

代码同时支持 `pharma_company`（上市医药公司研究）与 `pharma_industry`（医药行业研究）。当前结论是“实现已完成并通过无网络单元测试”，不是“真实医药研报已经通过领域验收”。

## 2. 实现范围

### 2.1 双研究模式与配置约束

`src/config/config.py` 和 `src/config/default_config.yaml` 增加以下能力：

- 识别 `pharma_company`、`pharma_industry`；
- 强制填写研究对象、带时区的信息截止时间、司法辖区和研究问题；
- 公司模式额外要求证券代码；
- 校验固定采集/分析任务及输出格式；
- 将项目路径规范化，并拒绝把输出目录写到 Pharsight 仓库之外；
- 写入 `config.json` 前递归遮蔽常见密钥字段；
- 支持 `auto_generate_tasks`、`live_web_search`、`strict_cutoff`、`strict_citations`、`enable_chart`、`output_formats` 和工具类别白名单；医药模式强制开启截止日、精确引用及至少一条引用校验。

示例配置位于：

- `docs/example_configs/my_config_company.yaml`
- `docs/example_configs/my_config_industry.yaml`
- `docs/example_configs/pharma_evidence_manifest.yaml`

### 2.2 受控证据清单工具

新增 `src/tools/pharma/evidence_manifest.py`，并通过原有工具注册机制暴露 `pharma_evidence_manifest`。它读取 YAML/JSON 清单，将经过授权的本地资料或内嵌文本转换成兼容的 `ToolResult`。

主要控制包括：

- 稳定来源编号，例如 `S001`；
- 发布者、标题、发布日期/更新时间、获取时间、报告期、URL/官方稳定记录号/本地路径、定位信息、来源等级和内容类别；
- 公司、证券、资产、适应症和司法辖区等实体限定；
- 严格按 `as_of` 过滤截止日后发布或更新的来源；
- 拒绝重复来源编号；
- 本地文件必须位于 Pharsight 仓库内；
- 计算文件 SHA-256，保留限制与转换说明；
- 支持文本、CSV、JSON、YAML 和 PDF 的受控导入。

该工具是人工资料包的可审计入口，不等于 NMPA、CDE、ClinicalTrials.gov、PubMed、医保或交易所连接器已经实现。

### 2.3 Memory 与提示词路由

`src/memory/variable_memory.py` 增加两个医药模式的精确路由，不再回退到通用提示词。四个提示词层均提供公司和行业版本：

- `src/memory/prompts/`：生成研究任务及证据选择要求；
- `src/agents/data_collector/prompts/`：来源优先级、截止日和采集边界；
- `src/agents/data_analyzer/prompts/`：临床、监管、支付、商业、财务、估值、催化剂和证伪条件；
- `src/agents/report_generator/prompts/`：中文证券研究写作、证据语义和逐项引用。

提示词要求区分：

1. 来源事实；
2. 确定性计算；
3. 明确假设；
4. 模型判断；
5. 来源冲突与证据缺口。

公司模式覆盖产品—适应症—地区—试验/审批组合、患者漏斗、竞争定位、rNPV/可比估值输入、催化剂和证伪条件。行业模式覆盖疾病与诊疗路径、标准治疗、技术路线、管线竞争、政策支付、市场规模多源校验、产业链和 A/H 标的映射。

### 2.4 三段式主流程适配

`run_report.py` 已改为配置驱动入口：

- 支持 `--config`、`--resume/--no-resume` 和 `--max-concurrent`；
- 从研究配置构造包含截止日、辖区、资产、适应症和研究问题的查询；
- 在采集前把受控证据清单预载到 Memory；
- 支持固定任务或模型自动生成任务；
- 按真实 `target_type` 选择提示词和大纲，不再硬编码金融公司模式；
- 把证券代码传递给报告封面；
- 关键步骤可配置失败关闭；
- 重型智能体依赖延迟到实际运行时加载，因此 `--help` 不要求网页抓取组件。

智能体层的主要改动：

- DataCollector 按模式加载提示词和工具白名单；仅在 `live_web_search: true` 时加载 Deep Search；
- DataAnalyzer 在 `enable_chart: false` 时不要求 VLM，保留纯文本分析路径；
- ReportGenerator 仅按配置生成格式；`output_formats: [md]` 时不触发 Pandoc/Word/PDF 链；
- 医药模式报告附带投资风险和非医疗建议声明。

### 2.5 中文报告大纲

新增：

- `src/template/pharma_company_outline_zh.md`
- `src/template/pharma_industry_outline_zh.md`

公司大纲覆盖证券与公司、产品及管线、临床和监管、支付与商业化、竞争、财务、估值、催化剂、风险、证伪条件和证据限制。行业大纲覆盖范围口径、疾病与诊疗路径、政策支付、技术与管线、市场规模、价值链、竞争和 A/H 映射。

### 2.6 精确引用与发布校验

新增 `src/utils/report_validation.py`，医药模式统一使用 `[Source:S001]` 格式：

- 只允许引用本次 Memory 中真实存在的来源编号；
- 未知或格式错误的来源编号使生成失败；
- 配置要求引用时，无引用的报告不能发布；
- 参考资料由 `ToolResult` 的元数据程序化渲染；
- 不再为医药报告用语义相似度猜测引用；
- 自动追加非医疗建议和投资风险声明。

通用金融模式仍保留上游模糊匹配路径，以减少本次改动对原功能的影响。

## 3. 最小运行方法

### 3.1 环境

使用 Python 3.11 创建独立虚拟环境并安装项目依赖。首次受控 Markdown 运行只需要兼容的主 LLM；精确引用路径不需要 Embedding，关闭图表后不需要 VLM，关闭联网搜索后不需要搜索/浏览器服务。

### 3.2 准备配置

```powershell
New-Item -ItemType Directory -Force user_configs
Copy-Item docs/example_configs/my_config_company.yaml user_configs/pharma_company.yaml
Copy-Item docs/example_configs/pharma_evidence_manifest.yaml user_configs/pharma_company_evidence.yaml
```

编辑本地配置时至少确认：

- `target_type: pharma_company` 或 `pharma_industry`；
- `target_name`、`stock_code`（公司模式）、`as_of`、`jurisdictions` 和 `research_questions`；
- `source_manifest` 指向实际资料清单；
- 首次运行使用 `live_web_search: false`、`enable_chart: false`、`output_formats: [md]`；
- `output_dir` 位于仓库的 `outputs/` 下。

资料清单模板本身为空，必须填写真实且有权使用的来源记录后再运行。

### 3.3 执行

```powershell
python run_report.py --config user_configs/pharma_company.yaml --no-resume
```

中断后在输入、模型、代码和资料快照均未变化时，才使用 `--resume`。不得把不同截止时间或不同资料包的检查点混用。

## 4. 自动化验证

本次执行的离线验证：

```text
python -m compileall -q src run_report.py tests/pharma
python -m pytest tests/test_tool_base.py tests/pharma -q -p no:cacheprovider
python -m ruff check <本次涉及的 Python 文件>
git diff --check
```

结果：42 项测试通过；Ruff 与 diff 检查通过。测试覆盖：

- 清单加载、截止日过滤、重复 ID、本地路径边界、恢复时快照变更拒绝和工具注册；
- 双模式配置必填项、时区和脱敏；
- 四层提示词文件及必需键；
- 精确引用、未知引用、缺失引用和声明；
- 报告引用编号/参考资料渲染及医药研究范围页；
- 原有 `Tool`/`ToolResult` 基础契约回归。

测试在本机 Python 3.12.7 上执行，并出现可选 `crawl4ai`、`playwright`、`akshare` 未安装的警告；因此它不能替代项目标准 Python 3.11 的依赖安装和端到端验证。

## 5. 已知限制与未完成验收

以下内容不应被描述为已经完成：

1. 尚未在干净 Python 3.11 环境安装全部锁定依赖并生成真实报告；
2. 尚未固定首个合法资料包、模型版本和温度等复现参数；
3. 尚未完成药学、临床、监管和证券研究人员的人工复核；
4. 尚未实现各官方来源的实时连接器、限速、重试和许可记录；
5. 尚未实现文档版面解析、Claim–Evidence 构图、Chunk/Graph/Hybrid 检索；
6. 尚未验证 DOCX/PDF 转换和图表/VLM 路径；
7. 尚未加入确定性 rNPV、患者漏斗、市场规模和敏感性计算模块。

下一验收任务是项目管理中的 V1-005：用一家上市公司、一个核心药物、一个适应症和 5—10 份一手资料，在固定截止时间下生成首份 Markdown 报告，并保留脱敏运行记录。随后执行 V1-006 的逐项引用审计和人工领域复核。

## 6. 后续图检索适配

本次实现刻意把未来图改造边界固定在“证据获取”一侧：

```text
当前：Manifest → ToolResult(source_id + metadata + content)
后续：Chunk/Graph/Hybrid Backend → 同一 ToolResult 契约
下游：Memory → Analyzer → ReportGenerator → [Source:<id>]
```

因此后续可以用文档解析和 Claim–Evidence 图后端替换或补充清单工具，而不需要改变报告引用语法、参考资料渲染和主要智能体接口。图节点或检索命中必须继续保留稳定来源编号、文档定位、截止日、实体限定、证据类别和快照哈希；图数据库内部 ID 不得直接成为面向报告的引用。
