# FinSight 基线进阶使用指南（Pharsight 中文版）

> [!IMPORTANT]
> 本文说明 Pharsight 所继承的 FinSight 金融研究代码、已实现的 V1 医药适配和后续扩展入口。V1 已进入代码和无网络测试，但尚未完成 Python 3.11、真实模型/资料和人工领域验收。证据检索和图适配方向见 [V1 文档](v1/README.md)。真实模型调用需自行承担服务费用，并将运行目录限制在本仓库的忽略路径中。

## 阅读导航

- [架构与执行流程](#架构与执行流程)
- [配置和密钥](#配置和密钥)
- [提示词系统](#提示词系统)
- [报告大纲与样式](#报告大纲与样式)
- [图表与视觉模型](#图表与视觉模型)
- [扩展工具和智能体](#扩展工具和智能体)
- [Memory、检查点与恢复](#memory检查点与恢复)
- [典型调用和 V1 适配](#典型调用和-v1-适配)

## 架构与执行流程

上游 FinSight 基于“代码智能体＋变量内存”（CAVM）：不同智能体在共享 Memory 中保存工具结果、分析结果及任务映射；智能体可以借助代码执行器处理数据；运行中保存检查点以便恢复。

```text
.env（密钥）＋ my_config.yaml（任务配置）＋ default_config.yaml（默认值）
                         ↓
                    Config / 模型实例
                         ↓
Data Collector → Memory → Data Analyzer → Report Generator
       │                        │                 │
       └→ Deep Search           └→ 可选图表/VLM   └→ MD/DOCX/PDF
                         ↓
                 检查点与运行产物
```

各智能体通常经历：准备执行环境、构造初始提示词、调用 LLM、解析动作标签、执行相应动作、保存状态。动作标签如 `<execute>`、`<report>`、`<search>` 是程序接口，不要翻译或擅自改名。

| 部件 | 上游职责 | V1 使用边界 |
| --- | --- | --- |
| Data Collector | 按任务调用金融、宏观、行业和网页工具 | 医药模式使用专用提示词、类别白名单和可选 Deep Search |
| Deep Search | 多跳搜索、页面提取 | V1 离线评测默认关闭 |
| Memory | 数据存储、任务关系和检查点 | 用来承载结构化证据结果 |
| Data Analyzer | 分析、可选 Python 图表及 VLM 审查 | V1 可关闭图表，关闭时不要求 VLM |
| Report Generator | 报告大纲、章节、来源处理及渲染 | 医药模式生成中文 Markdown，并执行精确来源 ID 校验 |

实现设计与具体边界见 [架构规范](ARCHITECTURE.md)。

## 配置和密钥

### 配置优先级

`Config` 依次读取默认配置、用户 YAML 和运行时 `config_dict`；后者可覆盖前者。YAML 中的 `${VARIABLE_NAME}` 引用会解析为环境变量，缺失时应报错。关键入口在 `src/config/config.py`。

```python
from src.config import Config

config = Config(
    config_file_path="my_config.yaml",
    config_dict={"target_name": "示例公司", "language": "zh"},
)
```

不要在代码、命令行参数或提交的 YAML 中写入真实密钥。先复制 `.env.example` 为本地 `.env`，并检查其变量名和模型服务要求。安全占位示例：

```dotenv
DS_MODEL_NAME="所选主模型"
DS_API_KEY="本地密钥占位符"
DS_BASE_URL="https://example.invalid/v1"

VLM_MODEL_NAME="所选视觉模型"
VLM_API_KEY="本地密钥占位符"
VLM_BASE_URL="https://example.invalid/v1"

EMBEDDING_MODEL_NAME="所选向量模型"
EMBEDDING_API_KEY="本地密钥占位符"
EMBEDDING_BASE_URL="https://example.invalid/v1"

SERPER_API_KEY="可选搜索服务密钥"
BOCHAAI_API_KEY="可选搜索服务密钥"
```

服务商聚合接口也可填入对应 `*_BASE_URL`，但模型名称、兼容协议、数据出境、价格和许可由使用者自行核实。本文不推荐特定模型或声称现有配置已经通过验证。

首次受控医药 Markdown 运行可以只在 `llm_config_list` 中保留主 LLM：当 `strict_citations: true`、`enable_chart: false`、`live_web_search: false` 时，不需要 Embedding、VLM 或搜索服务。配置会在默认值、用户 YAML 和运行时字典合并完成后才展开环境变量，因此被用户配置覆盖的可选模型不会要求无关变量。

### 用户配置示例

```yaml
target_name: "示例公司"
stock_code: "000001"
target_type: "financial_company"
language: "zh"
output_dir: "./outputs/example"

reference_doc_path: "src/template/report_template.docx"
outline_template_path: "src/template/company_outline_zh.md"

custom_collect_tasks:
  - "收集资产负债表、利润表及现金流量表"
  - "收集股价及成交量"
custom_analysis_tasks:
  - "分析收入变化和增长驱动因素"
  - "比较同业盈利能力"

use_collect_data_cache: true
use_analysis_cache: true
use_report_outline_cache: true
use_full_report_cache: true
use_post_process_cache: true

llm_config_list:
  - model_name: "${DS_MODEL_NAME}"
    api_key: "${DS_API_KEY}"
    base_url: "${DS_BASE_URL}"
    generation_params:
      temperature: 0.7
      max_tokens: 32768
      top_p: 0.95
  - model_name: "${EMBEDDING_MODEL_NAME}"
    api_key: "${EMBEDDING_API_KEY}"
    base_url: "${EMBEDDING_BASE_URL}"
  - model_name: "${VLM_MODEL_NAME}"
    api_key: "${VLM_API_KEY}"
    base_url: "${VLM_BASE_URL}"
```

上游 `target_type` 的典型值包括 `financial_company`、`macro`、`industry` 和 `general`。Pharsight V1 另增加 `pharma_company` 与 `pharma_industry`，会启用不同的任务、提示词、配置校验和中文大纲。

> [!WARNING]
> `Config._set_dirs()` 会把运行快照写入工作目录的 `config.json`。V1 已递归遮蔽名称中含 `api_key`、`token`、`secret`、`password` 或 `credential` 的值，并拒绝把输出目录设到 Pharsight 之外；这仍不是公开运行目录的授权，检查点、证据片段、日志和生成报告都不得提交。

### 环境变量与模型实例

配置完成后，可通过 `config.llm_dict` 根据已配置的模型名称取得实例。变量替换不是凭据保管机制；日志、异常和配置快照也需审查。

```python
model_name = config.config["llm_config_list"][0]["model_name"]
llm = config.llm_dict[model_name]
```

## 提示词系统

### 目录与加载

上游把提示词放在各智能体的 `prompts/*.yaml` 中。实际文件名和可用键以当前仓库为准，不应仅凭本文示例创建同名功能。

```text
src/agents/
├── data_collector/prompts/
├── data_analyzer/prompts/
├── report_generator/prompts/
└── search_agent/prompts/
```

通用加载方式：

```python
from src.utils.prompt_loader import get_prompt_loader

loader = get_prompt_loader("data_analyzer", report_type="financial")
available_keys = loader.list_available_prompts()
```

通常使用 `{current_time}`、`{user_query}`、`{data_info}`、`{api_descriptions}` 和 `{target_language}` 等占位变量。新增提示词时，应对照实际调用方检查每个 `.format(...)` 参数，否则运行时会因缺失变量而失败。

### 自定义原则

1. 确定提示词所服务的智能体、研究模式和最终输出契约。
2. 在对应智能体目录新增 YAML，并让配置中的模式选择与加载逻辑一致；不能只加文件就宣称已接入。
3. 保留系统使用的动作标签和变量名，不把 `<execute>` 等接口标记翻译成中文。
4. 对医药投研结果，明确证据 ID、事实/计算/假设/判断类别、截止时间、冲突、缺失和非医疗建议声明。
5. 使用受控 Memory 输入做回归测试，检查无依据内容和伪造引用。

V1 已为 Memory、Data Collector、Data Analyzer 和 Report Generator 分别增加公司/行业提示词，并对报告输出执行确定性引用校验；提示词本身不能替代校验器。实现范围见 [V1 实现说明](v1/IMPLEMENTATION_SUMMARY_2026-09-21.md)。

## 报告大纲与样式

### 大纲模板

`outline_template_path` 指定 Markdown 报告大纲。V1 提供 `pharma_company_outline_zh.md` 与 `pharma_industry_outline_zh.md`；选择模式时应使用对应模板。模板已实现不代表报告内容通过领域验收。

```yaml
outline_template_path: "src/template/pharma_company_outline_zh.md"
```

公司大纲区分发行人和证券、产品/适应症、试验及监管事件、商业化、财务与估值假设、催化剂、风险和证据限制；行业大纲先声明子行业与市场口径，再覆盖疾病负担、诊疗路径、技术路线、竞争、政策支付和 A/H 标的映射。

### Word 参考模板

`reference_doc_path` 指向 Word 样式模板。可在获得授权的副本中调整标题层级、正文、表格、页眉页脚、字体、页边距和目录，再将路径写入配置。

```yaml
reference_doc_path: "src/template/report_template.docx"
```

上游使用 Pandoc 进行 Markdown 到 DOCX 等格式转换。Pandoc、Word/PDF 转换软件及字体在不同系统上的行为可能不同，须在 M1 环境基线中实际验证。

## 图表与视觉模型

上游图表路径一般为：LLM 生成绘图代码 → 执行并保存图片 → VLM 依据清晰度、标签/刻度和美观性审查 → 必要时改写代码，直到结束或达到迭代上限。默认配色及实现位于 `src/agents/data_analyzer/data_analyzer.py`；可以通过子类或配置改变配色，但要先确认调用方接口。

图表复核只检查表现形式，不能替代来源、数值、单位和口径验证。Pharsight V1 设为 `enable_chart=False`；未来每张图应记录 `chart_id`、`evidence_ids`、计算输入、单位、期间、截止时间及代码/文件哈希，并以精确 ID 插入报告，而不根据标题做语义匹配。

## 扩展工具和智能体

### 添加工具

现有工具继承 `src/tools/base.py` 中的 `Tool`，返回 `ToolResult` 列表。示意接口：

```python
from src.tools.base import Tool, ToolResult

class ExampleTool(Tool):
    def __init__(self):
        super().__init__(
            name="示例工具",
            description="说明可处理的请求与来源",
            parameters=[
                {"name": "record_id", "type": "str", "description": "记录标识", "required": True},
            ],
        )

    async def api_function(self, record_id: str) -> list[ToolResult]:
        # 此处仅示意返回契约；真实实现须加入来源权限、超时和错误处理。
        return [ToolResult(
            name=f"记录 {record_id}",
            description="示例结果",
            data={"record_id": record_id},
            source="示例来源",
        )]
```

上游工具会按目录自动发现和注册；新增类前检查 `src/tools/__init__.py` 的导入与注册规则。不要因为自动发现就默认新工具会在所有研究模式中安全可用。

V1 的 `PharmaEvidenceManifestTool` 接受 YAML/JSON 资料清单，保留来源 URL/稳定 ID、发布及获取时间、文档定位、适应症/地区/期间限定、证据类别和文件哈希，并按报告截止时间过滤。后续 `PharmaEvidenceSearchTool` 和真实来源连接器应输出同一最小元数据，不复制上游全部工具链。

### 添加智能体

智能体继承 `BaseAgent`，通常定义名称、描述、必需输入，准备初始提示词，并在需要时处理自定义动作。它可作为另一智能体的工具使用。

```python
from src.agents.base_agent import BaseAgent

class ExampleAgent(BaseAgent):
    AGENT_NAME = "example_agent"
    AGENT_DESCRIPTION = "示例分析智能体"
    NECESSARY_KEYS = ["task"]

    async def _prepare_init_prompt(self, input_data: dict) -> list[dict]:
        return [{"role": "user", "content": input_data["task"]}]
```

这只是接口示意，不能直接当作完整可运行智能体。需要新动作时还要校验解析、调度、停止条件、检查点及错误路径。V1 首先复用 DataAnalyzer，不新建医药智能体类。

## Memory、检查点与恢复

Memory 保存 `ToolResult`、分析结果、任务关系和日志等。常用访问：

```python
from src.memory import Memory

memory = Memory(config=config)
collected = memory.get_collect_data()
analyses = memory.get_analysis_result()
```

上游 `retrieve_relevant_data(query, top_k, embedding_model)` 基于整个工具结果摘要检索，不提供医药 Claim 级引用、稳定证据 ID、日期过滤或图路径，不能直接当作 V1 的文档 RAG 后端。

智能体和全局 Memory 可在工作目录下保存 `.pkl` 检查点。上游可按运行入口的 `resume` 参数恢复或重新运行；实际函数签名以 `run_report.py` 为准。典型布局：

```text
outputs/<研究对象>/
├── memory/memory.pkl
├── agent_working/
│   ├── agent_data_collector_*/.cache/
│   ├── agent_data_analyzer_*/.cache/
│   └── agent_report_generator_*/.cache/
└── logs/
```

检查点可能包含来源片段、研究输入或凭据衍生信息，不得提交或公开；恢复时记录代码版本、模型、输入截止时间及语料快照，避免把不同运行的数据混为一体。

## 典型调用和 V1 适配

### 完整报告入口

入口为仓库根目录的 `python run_report.py --config <配置> --no-resume`，按配置执行采集、分析和报告。医药模式必须填写带时区的 `as_of`、司法辖区、研究问题和受控证据清单；仅替换公司名或药物名不能形成合格医药研报。

### 一般研究与 Agent API

高级使用者可以通过 `Config` 创建 `Memory`，再依次实例化 `DataCollector`、`DataAnalyzer` 和 `ReportGenerator`，使用各自的 `async_run(...)`。参数要求、输出键和模型选择可能因智能体而不同；以当前源码及测试为准，不直接复用上游文档里未验证的完整示例代码。

### Pharsight V1 的最小改动路径

```text
固定文档与 evidence manifest
  → PharmaEvidenceManifestTool（截止日过滤、ToolResult 兼容）
  → Memory / DataCollector
  → DataAnalyzer（可关闭图表）
  → ReportGenerator（Markdown）
  → 精确 [Source:<id>] 引用校验与程序化参考资料
  → 忽略目录中的结果、证据及检索轨迹
```

DataAnalyzer 无图兼容、Deep Search 可选加载、配置快照脱敏和精确引用已经实现。图数据库仍只作为后端投影，不向生成器暴露数据库对象；后续先完成 Chunk 基线，再在同一来源编号和元数据契约后增加 Graph/Hybrid。完整字段、分阶段任务和验收见 [V1 研究与实施方案](v1/PHARMA_RAG_V1.md)。

## 贡献

新工具、提示词、图表或文档的贡献，请遵循[贡献指南](../CONTRIBUTING.md)、[项目管理](PROJECT_MANAGEMENT.md)和[证据规范](ARCHITECTURE.md)。上游 FinSight 的演示成果不能替代 Pharsight 医药场景的验收证据。
