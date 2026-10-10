import argparse
import asyncio
import logging
import os
import traceback
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from src.config import Config
from src.utils import get_logger, setup_logger

PROJECT_ROOT = Path(__file__).resolve().parent

get_logger().set_agent_context('runner', 'main')

IF_RESUME = True
MAX_CONCURRENT = 3


def _resolve_model_name(config, config_key: str, env_key: str, *, required: bool = True):
    configured_model = config.config.get(config_key)
    if not required and not configured_model:
        return None
    model_name = configured_model or os.getenv(env_key)
    if required and not model_name:
        raise ValueError(
            f"Model name is required. Set '{config_key}' or environment variable '{env_key}'."
        )
    if model_name and model_name not in config.llm_dict:
        raise ValueError(f"Model '{model_name}' is not present in llm_config_list")
    return model_name


def _build_research_query(config) -> str:
    values = config.config
    target_type = str(values.get('target_type', 'company'))
    is_pharma = target_type in {'pharma_company', 'pharma_industry'}
    parts = [
        f"Research target: {values['target_name']}",
        f"ticker: {values.get('stock_code') or 'N/A'}",
        f"target type: {target_type}",
    ]
    if values.get('as_of'):
        parts.append(f"information cutoff: {values['as_of']}")
    if values.get('jurisdictions'):
        parts.append(f"jurisdictions: {', '.join(values['jurisdictions'])}")
    if values.get('focus_assets'):
        parts.append(f"focus assets: {', '.join(values['focus_assets'])}")
    elif is_pharma:
        parts.append("asset coverage: 全量覆盖全部已上市产品和在研管线（未限定重点资产）")
    if values.get('focus_indications'):
        parts.append(f"focus indications: {', '.join(values['focus_indications'])}")
    elif is_pharma:
        parts.append("indication coverage: 全部适应症（未限定重点适应症）")
    if values.get('research_questions'):
        parts.append("research questions: " + "；".join(values['research_questions']))
    return "; ".join(parts)


def _preload_evidence(config, memory):
    from src.tools.pharma import load_evidence_manifest

    manifest_path = config.config.get('source_manifest')
    if not manifest_path:
        return []
    evidence = load_evidence_manifest(
        manifest_path,
        as_of=config.config.get('as_of'),
        strict_cutoff=config.config.get('strict_cutoff', True),
    )
    existing_by_id = {
        item.source_id: item for item in memory.get_collect_data() if item.source_id
    }
    added = []
    for item in evidence:
        existing = existing_by_id.get(item.source_id)
        if existing is not None:
            old_hash = existing.data.get("document_hash")
            new_hash = item.data.get("document_hash")
            if old_hash != new_hash:
                raise RuntimeError(
                    f"Evidence {item.source_id} changed since the checkpoint was created. "
                    "Start a clean run with --no-resume."
                )
            continue
        memory.add_data(item)
        added.append(item)
    return added


async def _build_preloaded_graph(config, memory, model_name: str, logger) -> list:
    """Incrementally graph eligible manifest/web evidence before agents start."""
    if str(config.config.get("retrieval_backend", "chunk")) not in {"graph", "hybrid"}:
        return []
    if not config.config.get("graph_build_on_ingest", True):
        return []
    from src.graph_rag import GraphBuilder, GraphStore

    builder = GraphBuilder(
        GraphStore(config.config["graph_store_path"]),
        config.llm_dict[model_name],
        model_name,
        relation_candidate_limit=config.config.get("graph_relation_candidate_limit", 100),
        relation_confidence_threshold=config.config.get(
            "graph_relation_confidence_threshold", 0.7
        ),
    )
    reports = []
    for item in memory.get_collect_data():
        if not item.source_id or not isinstance(item.data, dict):
            continue
        report = await builder.build_evidence_record(
            item.data,
            as_of=config.config.get("as_of"),
        )
        reports.append(report)
        logger.info("Preloaded GraphRAG build report: %s", report.model_dump(mode="json"))
        if report.status == "failed":
            raise RuntimeError("GraphRAG build failed: " + "; ".join(report.errors))
    return reports


async def _run_priority_group(async_tasks: list, fail_fast: bool) -> dict:
    """Run one priority tier and return a task -> result/exception mapping.

    With fail_fast enabled, the first failed task cancels the remaining tasks
    in the tier instead of letting long-running agents continue in parallel.
    """
    if not fail_fast:
        results = await asyncio.gather(*async_tasks, return_exceptions=True)
        return dict(zip(async_tasks, results))

    done, pending = await asyncio.wait(async_tasks, return_when=asyncio.FIRST_EXCEPTION)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    results = {}
    for task in async_tasks:
        if task.cancelled():
            results[task] = None
        else:
            results[task] = task.exception()
    return results


async def run_report(
    resume: bool = True,
    max_concurrent: int = None,
    config_path: str | os.PathLike = 'my_config.yaml',
):
    """
    Run report generation with optional concurrency limit.

    Args:
        resume: Whether to resume from previous state
        max_concurrent: Maximum number of concurrent tasks. If None, uses MAX_CONCURRENT env var or unlimited.
    """
    # Load .env before importing agents/tools: the tool registry instantiates
    # search tools at import time, and they read their API keys from the
    # environment in __init__.
    load_dotenv(PROJECT_ROOT / ".env")

    # Keep command-line discovery usable before heavyweight runtime dependencies are
    # installed; actual agents are imported only when a run starts.
    from src.agents import DataAnalyzer, DataCollector, ReportGenerator
    from src.memory import Memory


    # Get max concurrent from parameter, env var, or default to unlimited
    if max_concurrent is None:
        max_concurrent = int(os.getenv("MAX_CONCURRENT", "0")) or None
    config = Config(
        config_file_path=str(config_path),
        config_dict={}
    )
    use_llm_name = _resolve_model_name(config, 'primary_llm_name', 'DS_MODEL_NAME')
    enable_chart = config.config.get('enable_chart', True)
    use_vlm_name = _resolve_model_name(
        config,
        'vlm_model_name',
        'VLM_MODEL_NAME',
        required=enable_chart,
    )
    use_embedding_name = _resolve_model_name(
        config,
        'embedding_model_name',
        'EMBEDDING_MODEL_NAME',
        required=not config.config.get('strict_citations', False),
    )

    collect_tasks = config.config.get('custom_collect_tasks', [])
    analysis_tasks = config.config.get('custom_analysis_tasks', [])
    # Initialize memory
    memory = Memory(config=config)

    # Initialize logger
    log_dir = os.path.join(config.working_dir, 'logs')
    logger = setup_logger(log_dir=log_dir, log_level=logging.INFO)

    # Log concurrency settings
    if max_concurrent:
        logger.info(f"Concurrency limit: {max_concurrent} tasks")
    else:
        logger.info("No concurrency limit (unlimited)")

    if resume:
        memory.load()
        logger.info("Memory state loaded")

    preloaded_evidence = _preload_evidence(config, memory)
    if preloaded_evidence:
        logger.info(f"Preloaded {len(preloaded_evidence)} approved evidence sources")
        memory.save()
    await _build_preloaded_graph(config, memory, use_llm_name, logger)

    # Generate additional collect and analysis tasks using LLM if not already generated
    research_query = _build_research_query(config)
    auto_generate_tasks = config.config.get('auto_generate_tasks', True)

    # Generate collect tasks if not already generated (or if we want fresh tasks)
    if auto_generate_tasks and not memory.generated_collect_tasks:
        logger.info("Generating collect tasks using LLM...")
        generated_collect_tasks = await memory.generate_collect_tasks(
            query=research_query,
            use_llm_name=use_llm_name,
            max_num=5,
            existing_tasks=collect_tasks  # Pass existing tasks to avoid duplication
        )
        logger.info(f"Generated {len(generated_collect_tasks)} collect tasks")
    elif auto_generate_tasks:
        generated_collect_tasks = memory.generated_collect_tasks
        logger.info(f"Using {len(generated_collect_tasks)} previously generated collect tasks")
    else:
        generated_collect_tasks = []
        logger.info("Automatic collection-task generation is disabled")

    # Generate analysis tasks if not already generated
    if auto_generate_tasks and not memory.generated_analysis_tasks:
        logger.info("Generating analysis tasks using LLM...")
        generated_analysis_tasks = await memory.generate_analyze_tasks(
            query=research_query,
            use_llm_name=use_llm_name,
            max_num=5,
            existing_tasks=analysis_tasks  # Pass existing tasks to avoid duplication
        )
        logger.info(f"Generated {len(generated_analysis_tasks)} analysis tasks")
    elif auto_generate_tasks:
        generated_analysis_tasks = memory.generated_analysis_tasks
        logger.info(f"Using {len(generated_analysis_tasks)} previously generated analysis tasks")
    else:
        generated_analysis_tasks = []
        logger.info("Automatic analysis-task generation is disabled")

    # Merge custom tasks with generated tasks (remove duplicates)
    all_collect_tasks = list(collect_tasks) + [task for task in generated_collect_tasks if task not in collect_tasks]
    all_analysis_tasks = list(analysis_tasks) + [task for task in generated_analysis_tasks if task not in analysis_tasks]

    logger.info(f"Total collect tasks: {len(all_collect_tasks)} (custom: {len(collect_tasks)}, generated: {len(generated_collect_tasks)})")
    logger.info(f"Total analysis tasks: {len(all_analysis_tasks)} (custom: {len(analysis_tasks)}, generated: {len(generated_analysis_tasks)})")

    # Update the tasks to be used
    collect_tasks = all_collect_tasks
    analysis_tasks = all_analysis_tasks
    if not collect_tasks:
        raise ValueError("At least one custom collection task is required")
    if not analysis_tasks:
        raise ValueError("At least one custom analysis task is required")
    # print(memory.task_mapping)
    # mapping = memory.task_mapping
    # for item in mapping:
    #     print(item['agent_id'])
    # assert False

    # Prepare prioritized task list (lower value = higher priority)
    tasks_to_run = []

    # Data-collection tasks
    for task in collect_tasks:
        tasks_to_run.append({
            'agent_class': DataCollector,
            'task_input': {
                'input_data': {'task': f'{research_query}; collection task: {task}'},
                'echo': True,
                'max_iterations': 20,
                'resume': resume,
            },
            'agent_kwargs': {
                'use_llm_name': use_llm_name,
            },
            'priority': 1,
        })

    # Analysis tasks (run after collection)
    for task in analysis_tasks:
        tasks_to_run.append({
            'agent_class': DataAnalyzer,
            'task_input': {
                'input_data': {
                    'task': research_query,
                    'analysis_task': task
                },
                'echo': True,
                'max_iterations': 20,
                'resume': resume,
                'enable_chart': enable_chart,
            },
            'agent_kwargs': {
                'use_llm_name': use_llm_name,
                'use_vlm_name': use_vlm_name,
                'use_embedding_name': use_embedding_name,
            },
            'priority': 2,
        })

    # Report generation task
    tasks_to_run.append({
        'agent_class': ReportGenerator,
        'task_input': {
            'input_data': {
                'task': research_query,
                'task_type': config.config.get('target_type', 'company'),
                'target_type': config.config.get('target_type', 'company'),
                'stock_code': config.config.get('stock_code', ''),
            },
            'echo': True,
            'max_iterations': 20,
            'resume': resume,
            'enable_chart': enable_chart,
            'add_reference_section': True,
        },
        'agent_kwargs': {
            'use_llm_name': use_llm_name,
            'use_embedding_name': use_embedding_name,
        },
        'priority': 3,
    })


    # Use memory to obtain/create the required agents (records tasks internally)
    agents_info = []
    for task_info in tasks_to_run:
        agent = await memory.get_or_create_agent(
            agent_class=task_info['agent_class'],
            task_input=task_info['task_input'],
            resume=resume,
            priority=task_info['priority'],
            **task_info['agent_kwargs']
        )
        # Retrieve the persisted priority (may differ on resume)
        actual_priority = task_info['priority']
        for saved_task in memory.task_mapping:
            if saved_task.get('agent_id') == agent.id:
                actual_priority = saved_task.get('priority', task_info['priority'])
                break

        agents_info.append({
            'agent': agent,
            'task_input': task_info['task_input'],
            'priority': actual_priority,
        })


    memory.save()


    # Execute tasks by priority tier (parallel within a tier)
    agents_info.sort(key=lambda x: x['priority'])

    # Group tasks by priority
    priority_groups = defaultdict(list)
    for agent_info in agents_info:
        priority_groups[agent_info['priority']].append(agent_info)

    # Execute each priority tier sequentially
    sorted_priorities = sorted(priority_groups.keys())
    for priority in sorted_priorities:
        group = priority_groups[priority]
        agent_resume = group[0]['task_input']['resume']
        concurrency_info = f" (max concurrent: {max_concurrent})" if max_concurrent else ""
        logger.info(f"\nExecuting priority {priority} group ({len(group)} task(s){concurrency_info})")

        # Skip tasks that already finished
        tasks_to_run = []
        for agent_info in group:
            agent = agent_info['agent']
            if agent_resume and resume and memory.is_agent_finished(agent.id):
                logger.info(f"Agent {agent.id} already completed; skip")
                continue
            tasks_to_run.append(agent_info)

        if not tasks_to_run:
            logger.info(f"All tasks with priority {priority} are complete")
            continue

        # Run tasks within the tier with concurrency limit
        semaphore = asyncio.Semaphore(max_concurrent) if max_concurrent else None

        async def run_agent_with_limit(agent_info):
            """Run agent with semaphore limit if configured"""
            agent = agent_info['agent']
            if semaphore:
                async with semaphore:
                    logger.info(f"Starting agent {agent.id}")
                    return await agent.async_run(**agent_info['task_input'])
            else:
                logger.info(f"Starting agent {agent.id}")
                return await agent.async_run(**agent_info['task_input'])

        # Create tasks
        async_tasks = []
        for agent_info in tasks_to_run:
            async_tasks.append(asyncio.create_task(
                run_agent_with_limit(agent_info)
            ))

        # Wait for completion
        if async_tasks:
            results_by_task = await _run_priority_group(
                async_tasks, config.config.get('fail_fast', False)
            )
            for agent_info, async_task in zip(tasks_to_run, async_tasks):
                agent = agent_info['agent']
                result = results_by_task[async_task]
                if async_task.cancelled():
                    logger.info(f"  Task cancelled after earlier failure: Agent {agent.id}")
                    continue
                if isinstance(result, Exception):
                    # Format full traceback for better debugging
                    tb_str = ''.join(traceback.format_exception(type(result), result, result.__traceback__))
                    logger.error(f"  Task failed: Agent {agent.id}, error: {result}\n{tb_str}")
                    if config.config.get('fail_fast', False):
                        raise RuntimeError(
                            f"Workflow stopped after {agent.AGENT_NAME} failure"
                        ) from result
                else:
                    logger.info(f"  Task finished: Agent {agent.id}")

        target_type = config.config.get('target_type')
        if priority == 1 and target_type in {'pharma_company', 'pharma_industry'}:
            citable_evidence = [
                item for item in memory.get_collect_data() if item.source_id
            ]
            if not citable_evidence:
                raise RuntimeError(
                    "No citable evidence was collected. Serper snippets alone are not "
                    "evidence; browse at least one eligible page or provide a source manifest."
                )
        if priority == 2 and target_type in {'pharma_company', 'pharma_industry'}:
            if not memory.get_analysis_result():
                raise RuntimeError(
                    "No analysis result was produced. Pharmaceutical workflows fail closed."
                )

        logger.info(f"Priority {priority} group finished\n")

    # Persist final state
    memory.save()
    logger.info("All tasks completed")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='运行 Pharsight 医药投研报告流程')
    parser.add_argument(
        '--config',
        default=str(PROJECT_ROOT / 'my_config.yaml'),
        help='YAML/JSON 配置文件路径',
    )
    parser.add_argument(
        '--resume',
        action=argparse.BooleanOptionalAction,
        default=IF_RESUME,
        help='是否从检查点恢复',
    )
    parser.add_argument(
        '--max-concurrent',
        type=int,
        default=MAX_CONCURRENT,
        help='同一优先级最大并发任务数；0 表示不限制',
    )
    args = parser.parse_args()
    asyncio.run(
        run_report(
            resume=args.resume,
            max_concurrent=args.max_concurrent or None,
            config_path=args.config,
        )
    )

