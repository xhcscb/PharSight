"""Tool adapter exposing GraphRAG evidence packs to DataCollector."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from src.graph_rag import GraphRetriever, GraphStore
from src.tools.base import Tool, ToolResult


class GraphEvidenceSearchTool(Tool):
    """Retrieve citable passages using graph or graph+BM25 expansion."""

    def __init__(self):
        super().__init__(
            name="Graph evidence search",
            description=(
                "Search the three-layer pharmaceutical evidence graph and return exact source IDs, "
                "page/chunk locations, graph paths, structured records and original text."
            ),
            parameters=[
                {"name": "query", "type": "str", "description": "Research evidence need.", "required": True},
                {"name": "source_id", "type": "str", "description": "Optional source filter.", "required": False},
                {"name": "document_hash", "type": "str", "description": "Optional document filter.", "required": False},
                {"name": "top_k", "type": "int", "description": "Evidence count, 1-30.", "required": False},
            ],
        )
        self.type = "tool_graph_evidence_search"
        self._retriever: GraphRetriever | None = None
        self._backend = "hybrid"
        self._max_hops = 2
        self._top_k = 8
        self._allowed_source_ids: set[str] | None = None

    def configure(
        self,
        config: Any,
        llm: Any,
        model_name: str,
        *,
        allowed_source_ids: set[str] | None = None,
    ) -> None:
        values = config.config if hasattr(config, "config") else dict(config)
        path = Path(values.get("graph_store_path") or "outputs/.graph_cache/pharsight_graph.sqlite3")
        if not path.is_absolute() and hasattr(config, "project_root"):
            path = Path(config.project_root) / path
        self._retriever = GraphRetriever(
            GraphStore(path),
            llm=llm,
            model_name=model_name,
            planner_timeout_seconds=values.get("graph_query_planning_timeout", 30),
        )
        self._backend = str(values.get("retrieval_backend", "hybrid"))
        self._max_hops = int(values.get("graph_max_hops", 2))
        self._top_k = int(values.get("graph_top_k", 8))
        self.set_allowed_source_ids(allowed_source_ids)

    def set_allowed_source_ids(self, source_ids: set[str] | None) -> None:
        """Update the approved evidence scope; an empty set deliberately denies all sources."""
        self._allowed_source_ids = (
            None
            if source_ids is None
            else {str(item).upper() for item in source_ids if str(item).strip()}
        )

    async def api_function(
        self,
        query: str,
        source_id: str = None,
        document_hash: str = None,
        top_k: int = None,
    ) -> list[ToolResult]:
        if self._retriever is None:
            self._retriever = GraphRetriever(GraphStore())
        pack = await self._retriever.search(
            query,
            backend=self._backend,
            top_k=top_k or self._top_k,
            max_hops=self._max_hops,
            source_id=(source_id or "").strip() or None,
            document_hash=(document_hash or "").strip() or None,
            allowed_source_ids=self._allowed_source_ids,
        )
        if not pack.hits:
            return [
                ToolResult(
                    name=self.name,
                    description="No graph or chunk evidence matched the query.",
                    data={"query": query, "hits": [], "content": "", "warnings": pack.warnings},
                    source="local GraphRAG index",
                )
            ]
        grouped: defaultdict[str, list] = defaultdict(list)
        for hit in pack.hits:
            grouped[str(hit.source_id or hit.document_id)].append(hit)
        results: list[ToolResult] = []
        for hits in grouped.values():
            first = hits[0]
            content_parts = []
            retrieval_hits = []
            for hit in hits:
                record_context = "\n".join(
                    f"- [{record['record_type']}|{record['review_status']}] {record['text']}"
                    for record in hit.record_summaries
                )
                content_parts.append(
                    f"[第{hit.page_start}页｜{hit.chunk_id}]\n{hit.passage_text}"
                    + (f"\n图记录（仅作导航，事实以原文为准）：\n{record_context}" if record_context else "")
                )
                retrieval_hits.append(hit.model_dump(mode="json"))
            record = {
                "source_id": first.source_id,
                "source_type": "graph_evidence",
                "publisher": first.publisher,
                "title": first.title,
                "published_at": first.published_at,
                "retrieved_at": first.retrieved_at,
                "reporting_period": first.reporting_period,
                "url": first.source_url,
                "final_url": first.source_url,
                "locator": "；".join(f"第{hit.page_start}页/{hit.chunk_id}" for hit in hits),
                "reliability_tier": first.reliability_tier or "unrated",
                "evidence_label": "sourced_fact",
                "cutoff_eligible": True,
                "limitations": [
                    "图中自动抽取记录默认为 candidate，仅用于检索导航；结论必须以返回原文核验。",
                    *first.limitations,
                    *pack.warnings,
                ],
                "query": query,
                "query_plan": pack.query_plan.model_dump(mode="json"),
                "retrieval_hits": retrieval_hits,
                "content": "\n\n".join(content_parts),
            }
            results.append(
                ToolResult(
                    name=f"{first.source_id or first.document_id} {first.title}",
                    description=f"GraphRAG {pack.backend} retrieval | query={query} | {len(hits)} hits",
                    data=record,
                    source=first.source_url or "local GraphRAG index",
                )
            )
        return results
