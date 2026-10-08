from src.agents.base_agent import BaseAgent


def _parse(response: str) -> tuple[str, str]:
    agent = object.__new__(BaseAgent)
    return agent._parse_llm_response(response)


def test_execute_tag_strips_nested_markdown_fence():
    action, content = _parse("<execute>```python\nprint('ok')\n```</execute>")

    assert action == "code"
    assert content == "print('ok')"


def test_dsml_execute_is_normalized_to_code():
    response = """<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="execute">
```python
print("ok")
```
</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>"""

    action, content = _parse(response)

    assert action == "code"
    assert content == 'print("ok")'


def test_dsml_execute_tolerates_provider_mixed_closing_tags():
    response = """<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="execute">```python
print("ok")
```</execute>
</invoke>
</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>"""

    action, content = _parse(response)

    assert action == "code"
    assert content == 'print("ok")'


def test_dsml_agent_invocations_become_call_tool_statements():
    response = """<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="deepsearch agent">
<｜｜DSML｜｜ parameter name="query" string="true">恒瑞医药年报 PDF</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>"""

    action, content = _parse(response)

    assert action == "code"
    assert "call_tool(tool_name='deepsearch agent'" in content
    assert "query='恒瑞医药年报 PDF'" in content
    assert "print(_dsml_tool_result_0)" in content


def test_orphan_dsml_code_parameter_is_still_executable():
    response = """<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ parameter name="code" string="true">print("ok")</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>"""

    action, content = _parse(response)

    assert action == "code"
    assert content == 'print("ok")'


def test_execute_tag_strips_provider_language_label():
    action, content = _parse("<execute>Python 代码\nprint('ok')</execute>")

    assert action == "code"
    assert content == "print('ok')"


def test_truncated_dsml_execute_remains_an_action():
    response = '<｜｜DSML｜｜ invoke name="execute">\nPython 代码</execute>'

    action, content = _parse(response)

    assert action == "code"
    assert "continue with the task" in content


def test_empty_execute_tag_uses_following_fenced_code():
    response = """<execute>Python 代码</execute>

```python
result = call_tool(tool_name='deepsearch agent', query='年报 PDF')
print(result)
```"""

    action, content = _parse(response)

    assert action == "code"
    assert content.startswith("result = call_tool")
    assert content.endswith("print(result)")


def test_later_report_wins_over_detached_execute_block():
    response = """<execute>Python code</execute>
```python
print(get_data(0))
```
<report>最终章节</report>"""

    assert _parse(response) == ("report", "最终章节")


def test_empty_model_response_requests_retry():
    action, content = _parse("   ")

    assert action == "invalid_response"
    assert "empty response" in content
