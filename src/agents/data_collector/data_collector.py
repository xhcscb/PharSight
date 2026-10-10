from typing import Any, Dict, List

from src.agents.base_agent import BaseAgent
from src.tools import ToolResult, get_tool_by_name, get_tool_categories


class DataCollector(BaseAgent):
    AGENT_NAME = 'data_collector'
    AGENT_DESCRIPTION = 'a agent that can collect data from the internet and variable apis'
    NECESSARY_KEYS = ['task']
    def __init__(
        self,
        config,
        tools = [],
        use_llm_name: str = "deepseek-chat",
        enable_code = True,
        memory = None,
        agent_id: str = None
    ):
        super().__init__(
            config=config,
            tools=tools,
            use_llm_name=use_llm_name,
            enable_code=enable_code,
            memory=memory,
            agent_id=agent_id
        )
        # Load prompts using the new YAML-based loader
        from src.utils.prompt_loader import get_prompt_loader

        target_type = self.config.config.get('target_type', 'general')
        self.prompt_loader = get_prompt_loader('data_collector', report_type=target_type)
        self.DATA_COLLECT_PROMPT = self.prompt_loader.get_prompt('data_collect')

        self.collected_data_list: List[ToolResult] = []


    def _set_default_tools(self):
        """
        Attach default tools (search agent + API wrappers).
        """
        tool_list = []
        target_type = self.config.config.get('target_type', 'general')
        is_pharma = target_type in {'pharma_company', 'pharma_industry'}

        if self.config.config.get('live_web_search', not is_pharma):
            try:
                from src.agents.search_agent.search_agent import DeepSearchAgent
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Live web search requires optional web dependencies. "
                    "Install them or set live_web_search: false."
                ) from exc
            tool_list.append(
                DeepSearchAgent(
                    config=self.config,
                    use_llm_name=self.use_llm_name,
                    memory=self.memory,
                )
            )

        configured_categories = self.config.config.get('allowed_tool_categories', [])
        if configured_categories:
            allowed_categories = set(configured_categories)
        elif target_type == 'pharma_company':
            allowed_categories = {'pharma', 'financial', 'industry', 'document'}
        elif target_type == 'pharma_industry':
            allowed_categories = {'pharma', 'industry', 'macro', 'financial', 'document'}
        else:
            allowed_categories = {
                category for category in get_tool_categories() if category != 'web'
            }

        retrieval_backend = str(self.config.config.get('retrieval_backend', 'chunk')).lower()
        if retrieval_backend in {'graph', 'hybrid'}:
            allowed_categories.add('graph')

        allowed_tool_names = set(self.config.config.get('allowed_tools', []))
        for tool_type, tool_name_list in get_tool_categories().items():
            if tool_type not in allowed_categories:
                continue
            for tool_name in tool_name_list:
                if allowed_tool_names and tool_name not in allowed_tool_names:
                    continue
                tool_class = get_tool_by_name(tool_name)
                if tool_class is not None:
                    if retrieval_backend in {'graph', 'hybrid'} and tool_name == 'PDF evidence search':
                        continue
                    if retrieval_backend == 'chunk' and tool_name == 'Graph evidence search':
                        continue
                    tool = tool_class()
                    if tool_name == 'Graph evidence search' and hasattr(tool, 'configure'):
                        tool.configure(
                            self.config,
                            self.config.llm_dict[self.use_llm_name],
                            self.use_llm_name,
                        )
                    tool_list.append(tool)
        self.tools = tool_list
        try:
            self.logger.info(f"Initialized default tools: total {len(tool_list)} items")
        except Exception:
            pass


    async def _prepare_executor(self):
        # Expose helper functions to the code executor for LLM-generated code
        approved_evidence = [
            item.data
            for item in self.memory.get_collect_data()
            if item.source_id
        ]
        self.code_executor.set_variable("call_tool", self._agent_tool_function)
        self.code_executor.set_variable("save_result", self._save_result)
        self.code_executor.set_variable("approved_evidence", approved_evidence)

    def _save_result(self, var: Any, result_name: str, result_description: str, data_source: str):
        """Persist execution results into self.collected_data_list."""
        result = ToolResult(
            name=result_name,
            description=result_description,
            data=var,
            source=data_source
        )
        self.collected_data_list.append(result)
        try:
            self.logger.info(f"Saved collect result: {result_name} (source={data_source})")
        except Exception:
            pass

    def _agent_tool_function(self, tool_name: str = None, **kwargs):
        """Run a tool and normalize a remotely parsed PDF into citable evidence."""
        response = super()._agent_tool_function(tool_name=tool_name, **kwargs)
        if tool_name == "deepsearch agent":
            evidence_items = [
                item
                for item in self.memory.get_collect_data()
                if item.source_id and isinstance(item.data, dict)
            ]
            if not evidence_items:
                return response
            evidence_lines = [
                "\n\n本次 Deep Search 已写入以下可引用证据。"
                "保存结果时必须原样使用这些来源编号，不得改写为 S001 等占位符："
            ]
            for item in evidence_items[-8:]:
                record = item.data
                content_preview = str(record.get("content", ""))[:2500]
                evidence_lines.append(
                    f"\n[Source:{item.source_id}] {record.get('title', item.name)}"
                    f"\nURL: {record.get('url', item.source)}"
                    f"\n文档类型: {record.get('document_type') or '未分类'}"
                    f"\n报告期: {record.get('reporting_period') or '未识别'}"
                    f"\n证据摘录:\n{content_preview}"
                )
            return f"{response}{''.join(evidence_lines)}"
        if tool_name in {"PDF evidence search", "Graph evidence search"}:
            return self._merge_pdf_retrieval_response(response)
        if tool_name != "PaddleOCR PDF reader" or not kwargs.get("file_url"):
            return response
        if not isinstance(response, list) or not response:
            return response
        payload = response[0] if isinstance(response[0], dict) else getattr(response[0], "data", None)
        if not isinstance(payload, dict) or not payload.get("markdown"):
            return response

        from src.tools.document.pdf_ingestion import classify_pdf_document
        from src.tools.pharma import build_web_evidence

        identity = classify_pdf_document(
            payload["markdown"],
            payload.get("document_title") or payload.get("file_name") or kwargs["file_url"],
        )
        index_metadata = payload.get("index") if isinstance(payload.get("index"), dict) else None
        if index_metadata:
            evidence_content = (
                f"完整 PDF 已解析并建立任务检索索引：共 {index_metadata.get('page_count')} 页、"
                f"{index_metadata.get('chunk_count')} 个文本块。文档哈希："
                f"{index_metadata.get('document_hash')}。后续事实采集必须调用 PDF evidence search，"
                "不得把整本文档直接注入模型上下文。"
            )
        else:
            evidence_content = payload["markdown"]
        evidence = build_web_evidence(
            url=kwargs["file_url"],
            title=payload.get("document_title") or payload.get("file_name") or kwargs["file_url"],
            content=evidence_content,
            as_of=self.config.config["as_of"],
            retrieved_at=payload.get("retrieved_at"),
            source_type="web_pdf",
            parser=payload.get("parser"),
            document_hash=payload.get("document_hash"),
            page_count=(index_metadata or {}).get("page_count") or payload.get("page_count"),
            final_url=payload.get("source_url") or kwargs["file_url"],
            document_type=identity["document_type"],
            reporting_period=identity["reporting_period"],
        )
        if evidence is None:
            return response
        if index_metadata:
            evidence.data["document_index"] = index_metadata
            evidence.data["locator"] = (
                f"PDF 完整解析索引，共 {index_metadata.get('page_count')} 页、"
                f"{index_metadata.get('chunk_count')} 个文本块"
            )
        existing_ids = {
            item.source_id
            for item in self.memory.get_collect_data()
            if item.source_id
        }
        if evidence.source_id not in existing_ids:
            self.memory.add_data(evidence)
        approved_evidence = [
            item.data
            for item in self.memory.get_collect_data()
            if item.source_id
        ]
        self.code_executor.set_variable("approved_evidence", approved_evidence)
        if (
            index_metadata
            and self.config.config.get("graph_build_on_ingest", True)
            and str(self.config.config.get("retrieval_backend", "chunk")) in {"graph", "hybrid"}
        ):
            self._build_graph_for_index(index_metadata["document_hash"])
        return [evidence.data]

    def _build_graph_for_index(self, document_hash: str):
        """Build an indexed PDF graph synchronously through the shared async bridge."""
        from src.graph_rag import GraphBuilder, GraphStore
        from src.tools.document import load_document_index
        from src.utils.async_bridge import get_async_bridge

        index = load_document_index(document_hash)
        builder = GraphBuilder(
            GraphStore(self.config.config["graph_store_path"]),
            self.config.llm_dict[self.use_llm_name],
            self.use_llm_name,
            relation_candidate_limit=self.config.config.get("graph_relation_candidate_limit", 100),
            relation_confidence_threshold=self.config.config.get(
                "graph_relation_confidence_threshold", 0.7
            ),
        )
        report = get_async_bridge().run_async(
            builder.build_document(index, as_of=self.config.config.get("as_of"))
        )
        self.logger.info("GraphRAG build report: %s", report.model_dump(mode="json"))
        if report.status == "failed":
            raise RuntimeError("GraphRAG build failed: " + "; ".join(report.errors))
        return report

    def _merge_pdf_retrieval_response(self, response):
        """Upsert retrieved PDF chunks into the document-level evidence record."""
        if not isinstance(response, list):
            return response
        returned_records = [item for item in response if isinstance(item, dict)]
        for record in returned_records:
            source_id = str(record.get("source_id") or "").strip().upper()
            if not source_id or not record.get("content"):
                continue
            existing = next(
                (
                    item
                    for item in self.memory.get_collect_data()
                    if item.source_id == source_id and isinstance(item.data, dict)
                ),
                None,
            )
            if existing is None:
                self.memory.add_data(
                    ToolResult(
                        name=f"{source_id} {record.get('title') or 'PDF 检索证据'}",
                        description="PDF task-level retrieval evidence",
                        data=record,
                        source=record.get("url") or "local parsed-document index",
                    )
                )
                continue

            prior_hits = list(existing.data.get("retrieval_hits") or [])
            known_chunk_ids = {
                str(hit.get("chunk_id"))
                for hit in prior_hits
                if isinstance(hit, dict) and hit.get("chunk_id")
            }
            for hit in record.get("retrieval_hits") or []:
                if isinstance(hit, dict) and str(hit.get("chunk_id")) not in known_chunk_ids:
                    prior_hits.append(hit)
                    known_chunk_ids.add(str(hit.get("chunk_id")))
            for key, value in record.items():
                if key in {"content", "retrieval_hits", "locator", "query"}:
                    continue
                if value is not None and existing.data.get(key) is None:
                    existing.data[key] = value
            retrieval_queries = list(existing.data.get("retrieval_queries") or [])
            query = str(record.get("query") or "").strip()
            if query and query not in retrieval_queries:
                retrieval_queries.append(query)
            if retrieval_queries:
                existing.data["retrieval_queries"] = retrieval_queries
            existing.data["retrieval_hits"] = prior_hits
            existing.data["content"] = "\n\n".join(
                f"[第{hit.get('page_start')}页｜{hit.get('chunk_id')}]\n{hit.get('text')}"
                for hit in prior_hits
            )
            existing.data["locator"] = "；".join(
                f"第{hit.get('page_start')}页/{hit.get('chunk_id')}" for hit in prior_hits
            )

        approved_evidence = [
            item.data for item in self.memory.get_collect_data() if item.source_id
        ]
        self.code_executor.set_variable("approved_evidence", approved_evidence)
        return returned_records

    async def _prepare_init_prompt(self, input_data: dict) -> list[dict]:
        task = input_data.get('task')
        if not task:
            raise ValueError("Input data must contain a 'task' key.")

        # Get target language from config
        target_language = self.config.config.get('language', 'zh')
        language_mapping = {
            'zh': 'Chinese (中文)',
            'en': 'English'
        }
        target_language_name = language_mapping.get(target_language, target_language)

        # Extract research target from task
        target_name = self.config.config.get('target_name', '')
        stock_code = self.config.config.get('stock_code', '')
        research_target = f"{target_name} (ticker: {stock_code})" if stock_code else target_name
        approved_sources = [
            f"{item.source_id}：{item.name}"
            for item in self.memory.get_collect_data()
            if item.source_id
        ]
        approved_evidence_summary = (
            "\n".join(approved_sources)
            if approved_sources
            else "尚未预载受控证据；只能调用本次允许的工具。"
        )

        return [{
            "role": "user",
            "content": self.DATA_COLLECT_PROMPT.format(
                api_descriptions=self._get_api_descriptions(),
                current_time=self.current_time,
                task=task,
                target_language=target_language_name,
                research_target=research_target,
                approved_evidence_summary=approved_evidence_summary,
            )
        }]


    async def async_run(
        self,
        input_data: dict,
        max_iterations: int = 10,
        stop_words: list[str] = [],
        echo=False,
        resume: bool = True,
        checkpoint_name: str = 'latest.pkl',
        # stop_words: list[str] = ["</execute>", "</final_result>"]
    ) -> dict:
        # Reset collected-data cache for each run
        self.collected_data_list = []
        self.logger.info(f"DataCollector started: task={input_data.get('task','')} resume={resume}")
        await self._prepare_executor()
        run_result = await super().async_run(
            input_data=input_data,
            max_iterations=max_iterations,
            stop_words=stop_words,
            echo=echo,
            resume=resume,
            checkpoint_name=checkpoint_name,
        )
        run_result['collected_data_list'] = self.collected_data_list
        for item in self.collected_data_list:
            self.memory.add_data(item)
        self.logger.info(f"Successfully save {len(self.collected_data_list)} items to memory")
        self.memory.add_log(
            id=self.id,
            type=self.type,
            input_data=input_data,
            output_data=self.collected_data_list,
            error=False,
            note=f"DataCollector finished: collected={len(self.collected_data_list)} items"
        )
        self.logger.info(f"DataCollector finished: collected={len(self.collected_data_list)} items")
        self.memory.save()
        return run_result

    def _get_persist_extra_state(self) -> Dict[str, Any]:
        return {
            'collected_data_list': self.collected_data_list,
        }

    def _load_persist_extra_state(self, state: Dict[str, Any]):
        self.collected_data_list = state.get('collected_data_list', [])
