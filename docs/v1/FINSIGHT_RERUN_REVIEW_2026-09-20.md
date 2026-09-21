# FinSight 重跑复盘（2026-09-20，九安医疗 002432，含授权修复验证）

## 0. 文档控制

| 项目 | 内容 |
| --- | --- |
| 文档目的 | 在完成经授权的 FinSight 代码修复后重跑完整流水线，跟踪日志，验证修复效果并识别残留问题与架构改进方向 |
| 文档性质 | 运行复盘（事后分析）；结论分"运行实证 / 源码事实 / 推断" |
| 运行对象 | `九安医疗 (002432)`，`target_type=financial_company`，`language=zh` |
| 运行方式 | 干净重跑：`asyncio.run(run_report(resume=False, max_concurrent=3))`，绕过旧检查点 |
| 上游基线 | FinSight `8fc5bce28ac201c713219a47d30367c6a84342f4`（HEAD 未变） |
| 关联文档 | [首次运行复盘](./FINSIGHT_RUN_REVIEW_2026-09-18.md)、[架构讨论记录](./DESIGN_DISCUSSION_LOG.md)、[医药投研 RAG V1 方案](./PHARMA_RAG_V1.md) |

证据来源：`FinSight/outputs/smoke/九安医疗/logs/finsight.log`（本次新增 28,523 行）、`console_rerun.out.log`、`console_rerun.err.log`、新生成的 md/docx/pdf、`agent_report_generator_85a03326/citation_audit.json`。

---

## 1. 执行摘要

本次干净重跑约 16 分钟完成（上次约 40 分钟），**代码修复在运行中生效**：点击越界异常彻底消失（`ERROR` 从 122 降到 0），抓取失败的真实原因被显式暴露并据此定位到环境缺陷。但**核心证据链问题大多仍在**：

1. **点击仍 100% 失败（运行实证）**：18 次点击全部失败。直接原因已定位——Playwright 包期望 `chromium_headless_shell-1243`，本机只有 `-1217`，浏览器未安装匹配版本（“Executable doesn't exist”）。
2. **采集层仍为 0 数据（运行实证）**：12/12 个 `DataCollector` 仍保存 0 条，DSML 协议不兼容问题未修复（本次输出中 682 行含 `DSML`）。
3. **报告出现跨主体污染（运行实证）**：一份九安医疗报告里出现整章“**比亚迪**”内容，全文“比亚迪”出现 104 次；污染源为写作提示词中的 BYD 少样本示例。
4. **图表链路断裂（运行实证，已更正）**：分析阶段实际生成 **0 张图**（`chart_name_mapping` 为空、各分析器 `images/` 目录为空）；写作模型自行虚构 26 处 `@import` 占位，最终报告被填入 12 处 “Chart not available” 占位文本，正文图片数为 **0**。
5. **退出卡死复现（运行实证）**：主流程 16:45:01 完成，进程随后 0 CPU、线程全部 Wait，直至被人工终止；`shutdown_async_bridge()` 未能解决。
6. **引用质量明显改善（运行实证）**：引用审计 resolved=129 / unresolved=2 / rejected_entity=3；参考文献不再出现上次的错误主体（603927）与无关来源（工商银行），但仍有重复（同 URL 4 次）与二手来源。

**判断**：代码修复方向正确且可观测性大幅提升；剩余瓶颈主要来自**环境预检缺失、模型协议不兼容、提示词示例泄漏、图表契约断裂与运行时退出治理**。

---

## 2. 运行元数据与阶段

| 阶段 | 起 | 止 | 时长 |
| --- | --- | --- | --- |
| 进程启动 | 16:29:19 | — | — |
| 任务生成 | 16:29:19 | 16:29:42 | ~23 s |
| 优先级 1 采集（12） | 16:29:42 | 16:29:57 | ~15 s |
| 优先级 2 分析（14） | 16:29:57 | 16:32:08 | ~2.2 min |
| 优先级 3 写作（5 节） | 16:32:08 | 16:45:01 | ~13 min |
| 主流程完成 | 16:45:01 | — | — |
| 退出卡死 | 16:45:01 | 人工终止 | >14 min 无进展 |

- 日志级别：INFO=1,142，WARNING=24，**ERROR=0**。
- 最终内存：`log=93, data=539, tasks=27`，`memory.pkl` 897,345 B。
- 产物：新 md（106,182 B，437 行）、docx（62,113 B）、pdf（682,585 B）；旧报告文件仍保留。

---

## 3. 与首次运行（2026-09-18）对比

| 指标 | 首次 | 本次 | 说明 |
| --- | --- | --- | --- |
| 运行时长 | ~40 min + 卡死 | ~16 min + 卡死 | 本次无长孤儿尾声 |
| `ERROR` 数 | 122 | **0** | 点击越界修复生效 |
| 点击成功/尝试 | 2/124 | 0/18 | 底层浏览器缺失，未修复 |
| DataCollector 保存 | 12×0 | 12×0 | 协议问题未修复 |
| 含 DSML 输出行 | 412 | 682 | 协议问题持续 |
| 引用审计 | 无 | resolved 129 / unresolved 2 / rejected 3 | 新增能力生效 |
| 参考文献错误主体 | 有（603927） | 无 | 实体校验生效 |
| 报告图片 | 3 | 0 | 退化 |
| 跨主体污染 | 未发现 | 有（BYD 整章） | 新发现 |

---

## 4. 已验证的修复效果（源码事实 + 运行实证）

1. **点击越界消除**：`src/agents/search_agent/search_agent.py:187-238` 空/错误结果均不再访问 `click_result[0]`；日志由 `ERROR list index out of range` 变为 `WARNING ... result=Error fetching <url>`。
2. **抓取失败可观测**：`src/tools/web/web_crawler.py:79-145` 逐 URL 捕获异常，把真实异常写入 `ClickResult.data`；据此一次性定位到 Playwright 浏览器版本缺失。
3. **引用审计生效**：`src/agents/report_generator/report_generator.py:439-573` 的实体/股票代码校验拒绝了 3 处不匹配引用，参考文献不再串入其他公司，并输出 `citation_audit.json`。
4. **运行时改动未引入回归**：`data_analyzer` 改用 `AsyncBridge` 后，分析阶段正常完成、无 `asyncio.run` 报错；`ExecutionTimeout` 仅 1 次。

---

## 5. 发现的问题（按严重度）

### 5.1 环境预检缺失：Playwright 浏览器版本不匹配（阻断级）
- **现象**：18/18 点击失败。
- **证据**：独立复现 `Click().api_function(['https://www.baidu.com'],'test')` 返回
  `BrowserType.launch: Executable doesn't exist at ...\ms-playwright\chromium_headless_shell-1243\chrome-headless-shell-win64\chrome-headless-shell.exe`；本机缓存只有 `chromium_headless_shell-1217`。
- **影响**：DeepSearch 退化为“仅搜索摘要”，是报告证据薄弱的首要原因。
- **修复（环境层，非代码）**：在项目环境执行 `playwright install chromium`（或 `crawl4ai-setup`），并在启动时做健康检查。

### 5.2 模型动作协议不兼容（阻断级，未纳入上次授权）
- **现象**：采集仍 12/12 为 0；`console_rerun.out.log` 中 682 行含 `DSML`（`<｜DSML｜tool calls>`）。
- **证据**：`BaseAgent._parse_llm_response` 仅识别 XML 标签，无标签回退 `final`（`base_agent.py:598-616`）。
- **影响**：DataCollector 一轮即结束，结构化采集缺位。

### 5.3 提示词示例泄漏导致跨主体污染（高危）
- **现象**：九安医疗报告正文出现整章“比亚迪”（全文“比亚迪”104 次）。
- **证据（源码事实）**：`src/agents/report_generator/prompts/financial_company_prompts.yaml:263` 及多处 BYD 少样本示例（`financial_industry_prompts.yaml:187`、`general_prompts.yaml:421/464`）。推断：模型把示例章节当作可复用内容写入。
- **影响**：报告主体与投资结论被无关公司内容污染，属事实性错误。

### 5.4 图表链路断裂（高危；更正原“220 张 PNG”表述）
- **现象**：分析阶段实际 **0 张图**；写作模型虚构 `@import` 占位，最终报告图片数 0，并出现 12 处 “Chart not available” 占位文本。
- **证据**：14 个 `AnalysisResult` 的 `chart_name_mapping` 均为空且 `images/` 目录为空；日志 1 次 `No image captions found`、26 处 `@import` 虚构占位、0 次 `Building index for N images`；报告含 12 处 “Chart not available”。
- **更正说明**：初稿“分析阶段生成 220 张新 PNG”为递归统计口径错误（当时 `agent_working` 下合计仅 4 张历史 PNG，本次新增 0），特此更正。
- **影响**：研报图表缺失，且写作模型虚构图片引用，与实际能力不符。

### 5.5 运行时退出卡死复现（高危）
- **现象**：16:45:01 `All tasks completed` 后进程 0 CPU、51 线程全部 Wait，>14 分钟无进展。
- **证据**：`Get-Process` CPU 恒定、无 Established 连接、无非 daemon 之外的子进程；`shutdown_async_bridge()` 已在收尾调用但无效。
- **推断**：`AsyncCodeExecutor` 的非 daemon `ThreadPoolExecutor` 工作线程阻塞于桥接调用，解释器退出时 `concurrent.futures.thread._python_exit` 永久 join。

### 5.6 控制台日志编码错误（中危）
- **现象**：`console_rerun.err.log` 有 6 段 `UnicodeEncodeError: 'gbk' codec can't encode character '\u25e6'`。
- **证据（源码事实）**：`logger.py` 的 `StreamHandler(sys.stdout)` 未指定 `encoding='utf-8'`；`echo=True` 会把整段动作结果写入日志。
- **影响**：日志噪声、信息丢失；Windows 下易发。

### 5.7 引用虽改善但仍不完美（中危）
- **现象**：resolved 129、unresolved 2、rejected 3；参考文献 18 条，但同一 moomoo URL 重复 4 次，且含 futunn/moomoo 等二手来源；无“主张→证据”逐条映射。
- **证据**：`citation_audit.json` 与报告 References 列表。

---

## 6. 架构可改进方向

### 6.1 增加启动预检与健康检查（Run Preflight）
在 `run_report` 前校验：Playwright 浏览器可启动、`SERPER_API_KEY` 可用、LLM 动作协议探针（发出一个最小提示，确认返回 `<execute>` 而不是 `DSML`）、Embedding 可用。任一失败即**快速失败并给出修复命令**，而不是静默降级。

### 6.2 动作协议适配与失败关闭
- 首选：结构化 tool call/result 替代 XML 文本协议。
- 过渡：解析器识别 `DSML` 等原生工具调用并转换为内部动作；遇到未知格式**显式报错/重试**，不回退 `final`。

### 6.3 提示词与生成内容的实体护栏
- 移除或改为占位符的跨行业少样本（BYD 等）；示例需参数化并标注“仅格式示例”。
- 生成后做实体一致性 lint：正文出现的公司名/证券代码必须属于目标或显式声明的对比对象；命中模板示例实体即阻断。
- 大纲与章节都要做“目标绑定”校验（target_name/stock_code）。

### 6.4 图表契约显式化
- 定义“分析产出图”到“报告引用图”的稳定标识（ID + 文件 + caption）。
- 报告生成时校验图引用数量；为 0 或缺图时阻断或显式降级并告警，不静默丢失。

### 6.5 运行时与进程治理
- 代码执行与桥接调用应可取消：避免非 daemon 线程池，改用可中断 worker 或子进程隔离；每任务有硬超时。
- 入口增加看门狗：主流程结束后若 N 秒未自然退出，落盘状态后强制退出。
- 桥接关闭前先取消 pending 任务并回收，避免关闭与在途调用竞态。

### 6.6 统一日志与可观测性
- 所有 handler 强制 `encoding='utf-8', errors='replace'`；`echo` 改为截断或落盘为独立 JSON，不写全量动作结果到控制台日志。
- 为 `web_crawler` 等模块 logger 接入统一 handler，保证抓取错误进入主日志。
- 日志中记录关键计数（点击成功/失败、图数、引用 resolved/unresolved、协议异常数），便于自动门禁。

### 6.7 证据与引用
- 证据级：稳定证据 ID、URL 去重、来源分级（一手优先）、期间/单位/币种。
- 引用级：程序化参考文献、主张→证据映射；`citation_audit.json` 纳入验收门禁（如 unresolved 必须为 0，重复为 0）。

### 6.8 评测门禁
以本次与上次复盘为基线，定义运行验收：采集条数>0、点击成功率阈值、图数>0、跨主体实体为 0、unresolved=0、进程在 T 秒内退出。未达标即判失败。

---

## 7. 建议跟进任务（Backlog，待指派）

| 建议 ID | 任务 | 里程碑 | 优先级 | 状态 |
| --- | --- | --- | --- | --- |
| V1-014 | 运行前预检脚本（浏览器/密钥/模型协议/embedding）与快速失败 | M1 | P0 | 待办 |
| V1-015 | 动作协议适配 + 未知格式失败关闭（DSML） | M3 | P0 | 待办 |
| V1-016 | 提示词示例去实体化 + 生成后实体一致性 lint | M3 | P0 | 待办 |
| V1-017 | 图表产出—报告引用契约与缺图门禁 | M3 | P1 | 待办 |
| V1-018 | 运行时进程隔离 / 可取消执行 / 退出看门狗 | M1 | P0 | 待办 |
| V1-019 | 统一 UTF-8 日志与关键指标计数 | M1 | P1 | 待办 |
| V1-020 | 引用去重与来源分级；引用审计纳入验收 | M4 | P0 | 待办 |

---

## 7.5 后续修复（2026-09-20，#3–#7）

经所有者授权，对 #3–#7 实施代码修复（未改动 Git）：

| 问题 | 修复 | 文件 |
| --- | --- | --- |
| #3 跨主体污染 | 提示词示例去实体化（BYD→ExampleCo 等）；生成后实体 lint：命中示例实体即删除该节并写 `entity_lint.json`，全部被删则失败关闭 | `src/agents/report_generator/prompts/*.yaml`、`src/memory/prompts/financial_industry_prompts.yaml`、`src/agents/report_generator/report_generator.py` |
| #4 图表链路 | `_replace_image_path` 以 basename 归一化正确匹配图片；无图时干净移除 `@import` 占位（不再插入占位文本）；检索无结果安全跳过；分析器无 `@import` 时告警、图表失败不再写入 `None` 映射 | `src/agents/report_generator/report_generator.py`、`src/agents/data_analyzer/data_analyzer.py` |
| #5 退出卡死 | 代码执行线程池改为 daemon 且不注册 atexit join；入口在主流程结束后 `logging.shutdown()` + `os._exit` | `src/utils/code_executor_async.py`、`run_report.py` |
| #6 日志编码 | `stdout/stderr` 重配置为 UTF-8（`errors='replace'`）；echo 动作结果截断到 4000 字符 | `src/utils/logger.py`、`src/agents/base_agent.py` |
| #7 引用质量 | 按 URL 去重；来源分级（一手/二手/三手），候选优先一手/二手，存在 ≤1 级来源时剔除三手 | `src/agents/report_generator/report_generator.py` |

DSML（#2）：已按方案 A 实施——在 `BaseAgent` 增加协议归一化，把 DeepSeek DSML（`tool_calls`/`invoke`/`parameter`）重写为规范 `<execute>/<search>/<click>/<report>/<final_result>`，未知格式返回 `<protocol_error>` 并继续（失败关闭，不再静默 `final`）；文件 `src/agents/base_agent.py`。

## 8. 局限与声明

1. 仍为单标的（九安医疗）双运行对比；比例结论不具普遍性。
2. 退出卡死原因为**推断**，未取得线程级堆栈（环境未安装 py-spy，未做侵入式采样）。
3. 未逐条核验报告财务数字；BYD 污染为文本层面确认，其财务数据正确性另需审计。
4. 本次运行在 FinSight 工作树中进行，属所有者授权运行；未执行任何 Git 写命令，HEAD 仍为 `8fc5bce`。运行产物（新报告、console 日志、agent_working）位于 `FinSight/outputs/smoke/九安医疗/`，不应入库。

---

## 附录 A：关键证据索引

| 主题 | 证据 |
| --- | --- |
| 点击根因 | 独立复现输出 `BrowserType.launch: Executable doesn't exist ... chromium_headless_shell-1243` |
| 协议问题 | `console_rerun.out.log` 中 682 行含 `DSML` |
| 采集为空 | `finsight.log` 本次 12 条 `Successfully save 0 items to memory` |
| 跨主体污染 | 新报告第 344 行 `## 比亚迪...`；全文“比亚迪”104 次 |
| 图表断裂 | 14 个 `AnalysisResult` 的 `chart_name_mapping` 为空、`images/` 目录为空；报告 `![` 计数 0；12 处 “Chart not available”；无 `Building index for N images` |
| 引用审计 | `citation_audit.json`：resolved=129, unresolved=2, rejected_entity=3 |
| 日志编码 | `console_rerun.err.log` 6 段 `UnicodeEncodeError 'gbk'` |
| 退出卡死 | 16:45:01 完成后进程 >14 min 存活、CPU 恒定、线程 Wait |

## 附录 B：复现步骤

```powershell
# 干净重跑（不载入旧检查点）
cd FinSight
python -u -c "import asyncio, run_report; asyncio.run(run_report.run_report(resume=False, max_concurrent=3))"

# 抓取根因独立复现
python -B -c "import asyncio; from src.tools.web.web_crawler import Click
async def m():
    r=await Click().api_function(['https://www.baidu.com'],'t'); print(r[0].data)
asyncio.run(m())"

# 环境修复（在项目环境执行）
playwright install chromium
```

## 变更历史

| 日期 | 变更 | 作者 |
| --- | --- | --- |
| 2026-09-20 | 初稿：授权修复后重跑的验证与问题分析 | 项目组 |
| 2026-09-20 | 更正 #4 图表数据（实为 0 张图，非 220 张）；新增 7.5 节记录 #3–#7 修复 | 项目组 |
