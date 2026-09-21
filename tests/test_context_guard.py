"""Tests for conversation-history guards and fail-fast group execution."""

import asyncio
from types import SimpleNamespace

import pytest

from run_report import _run_priority_group
from src.agents.base_agent import BaseAgent


def _agent(**config):
    agent = BaseAgent.__new__(BaseAgent)
    agent.config = SimpleNamespace(config=config)
    return agent


def test_action_result_truncated_at_configured_limit():
    agent = _agent(max_action_result_chars=100)
    text = "x" * 250

    truncated = agent._truncate_action_result(text)

    assert truncated.startswith("x" * 100)
    assert "已省略 150 字符" in truncated
    assert len(truncated) < len(text)


def test_action_result_under_limit_unchanged():
    agent = _agent()
    assert agent._truncate_action_result("short") == "short"
    payload = {"not": "text"}
    assert agent._truncate_action_result(payload) is payload


def test_history_within_budget_unchanged():
    agent = _agent(max_history_chars=10000)
    history = [{"role": "user", "content": "task"}]
    assert agent._trim_conversation_history(history) is history


def test_history_over_budget_keeps_head_and_recent_turns():
    agent = _agent(max_history_chars=100000)
    history = [{"role": "user", "content": "初始任务"}]
    for index in range(30):
        history.append({"role": "assistant", "content": f"assistant-{index}"})
        history.append({"role": "user", "content": ("r" * 10000) + str(index)})
    original_last = history[-1]

    trimmed = agent._trim_conversation_history(history)

    assert trimmed[0] is history[0]
    assert trimmed[1]["content"] == BaseAgent.HISTORY_ELISION_NOTE
    assert trimmed[-1] is original_last
    assert sum(len(str(message["content"])) for message in trimmed) <= 100000
    assert len(trimmed) < len(history)


def test_resumed_oversized_history_is_trimmed():
    agent = _agent(max_history_chars=50000)
    history = [{"role": "user", "content": "task"}]
    for index in range(100):
        history.append({"role": "assistant", "content": "a" * 5000 + str(index)})
        history.append({"role": "user", "content": "u" * 5000 + str(index)})

    trimmed = agent._trim_conversation_history(history)

    assert trimmed[0]["content"] == "task"
    assert len(trimmed) < len(history)
    assert sum(len(str(message["content"])) for message in trimmed) <= 50000


@pytest.mark.asyncio
async def test_fail_fast_group_cancels_pending_tasks():
    async def ok():
        return "ok"

    async def boom():
        raise RuntimeError("boom")

    async def slow():
        await asyncio.sleep(60)
        return "slow"

    tasks = [asyncio.create_task(ok()), asyncio.create_task(boom()), asyncio.create_task(slow())]

    results = await _run_priority_group(tasks, fail_fast=True)

    assert isinstance(results[tasks[1]], RuntimeError)
    assert tasks[2].cancelled()


@pytest.mark.asyncio
async def test_group_without_fail_fast_waits_for_all_tasks():
    async def value(number):
        await asyncio.sleep(0.01)
        return number

    tasks = [asyncio.create_task(value(1)), asyncio.create_task(value(2))]

    results = await _run_priority_group(tasks, fail_fast=False)

    assert results[tasks[0]] == 1
    assert results[tasks[1]] == 2
