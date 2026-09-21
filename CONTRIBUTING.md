# 参与 Pharsight 开发

感谢参与项目。Pharsight 是从 FinSight 派生的高校医药投研项目；当前优先建设[文档到带引用结果的 V1 最小闭环](docs/v1/PHARMA_RAG_V1.md)。请先阅读 [项目管理](docs/PROJECT_MANAGEMENT.md) 和 [架构与证据规范](docs/ARCHITECTURE.md)。

## 工作边界

- 只在 `pharsight/**` 内修改项目代码、文档、配置和测试。同级 `../FinSight` 永远只读。
- 保留上游历史、`LICENSE`、版权通知及 GPL-3.0 义务。
- 未经项目负责人明确同意，不添加团队 `origin`，不推送、发布或创建拉取请求。
- 不提交真实 `.env`、密钥、受限原始数据、个人健康信息、缓存、日志、生成报告和模型检查点。
- 已有他人改动属于其所有者；不要擅自重置、移动或覆盖。

## 开发环境

项目标准为 Python 3.11；其他版本未验证。于本仓库根目录执行：

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python -m pip install pytest pytest-asyncio pytest-cov hypothesis ruff
```

根据本机系统激活 `.venv`。本地凭据只放在被忽略的 `.env`；`.env.example` 仅记录变量名和安全占位值。安装命令是开发说明，不表示 M1 环境基线已经验收。

## 选择和推进任务

1. 在项目管理文档中确认任务 ID、研究模式、输入输出边界、证据来源、许可、依赖、负责人、验收标准和测试方法。
2. 满足就绪标准后，从最新本地 `main` 建立 `feature/<ticket>-<slug>`、`fix/<ticket>-<slug>` 或 `docs/<ticket>-<slug>`。
3. 一个分支和提交聚焦一个逻辑变更；使用 `feat:`、`fix:`、`test:`、`docs:` 或 `chore:` 等命令式标题。
4. 实现最小可验证切片，同时维护测试、文档、任务状态和必要的架构决策记录。
5. 评审时检查证据链、日期/单位、错误处理、可复现性和数据安全。

## 测试与风格

```bash
python -m pytest tests/ -v
python -m pytest tests/test_async_bridge.py -v
python -m pytest tests/ --cov=src --cov-report=term-missing
ruff check src/ tests/
```

以上是可选测试入口；具体变更至少运行相关单元测试、回归测试和 `ruff check`。默认测试必须模拟网络请求；实时集成测试单独标记、显式启用并限速。公共 Python 接口应有类型标注和文档字符串，领域计算应可独立确定性测试。

## 添加工具或证据源

1. 先记录来源发布者、URL/稳定 ID、许可、更新频率、访问条款与限速。
2. 在合适的 `src/tools/` 子目录实现 `Tool`，按现有契约返回 `list[ToolResult]`；医药证据工具还须保留文档定位、时间、口径和审核状态。
3. 参数、超时、空结果、错误及元数据转换均需测试；不要把未经审核的候选 Claim 写成事实。
4. 如修改公共契约或编排方式，记录兼容性、迁移及回归测试，并更新架构决策。

## 评审与完成

评审时应能够回答：结论来自何处、数据在何时有效、使用了什么转换、缺失与冲突如何处理，以及失败是否可见。完成标准以 [项目管理文档](docs/PROJECT_MANAGEMENT.md) 为准。项目输出仅辅助投资研究，必须包含投资风险和非医疗建议声明。

提交到本项目的贡献受仓库 [GPL-3.0 许可证](LICENSE)约束；如涉及第三方代码或数据，还须核对其原有许可及再分发限制。
