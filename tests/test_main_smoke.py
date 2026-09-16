"""冒烟测试：插件入口模块必须能被加载，且关键依赖在模块级就位。

这一层专门用来拦「名字写错/少 import」这类只在真实运行时才暴露的错误
（例如 _ProviderLlmClient is not defined）。

注意：这里不假设插件目录叫什么名字（克隆下来可能叫 give-me-work），
因此手工构造一个包名再导入，而不是写死 `astrbot_plugin_job_agent`。
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("astrbot", reason="需要 AstrBot 运行环境")

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "job_agent_under_test"


def _load_plugin_main():
    if PACKAGE_NAME not in sys.modules:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [str(PLUGIN_ROOT)]
        sys.modules[PACKAGE_NAME] = package
    return importlib.import_module(f"{PACKAGE_NAME}.main")


plugin_main = _load_plugin_main()


def test_plugin_module_exposes_expected_symbols():
    assert plugin_main.PLUGIN_VERSION
    for name in (
        "_ProviderLlmClient",
        "_MissingFeishuAdapter",
        "JobAgentPlugin",
        "PLUGIN_NAME",
    ):
        assert hasattr(plugin_main, name), f"缺少模块级符号: {name}"


def test_provider_llm_client_wraps_text_chat():
    import asyncio

    class FakeResponse:
        completion_text = '{"type":"final","summary":"ok"}'

    class FakeProvider:
        def __init__(self):
            self.calls = []

        async def text_chat(self, prompt=None, session_id=None, system_prompt=None, **kwargs):
            self.calls.append((prompt, session_id, system_prompt))
            return FakeResponse()

    provider = FakeProvider()
    client = plugin_main._ProviderLlmClient(provider)
    text = asyncio.run(client.complete("hello", "system"))
    assert text == '{"type":"final","summary":"ok"}'
    assert provider.calls == [("hello", None, "system")]


def test_missing_feishu_adapter_never_pretends_success():
    import asyncio

    FeishuAdapterError = plugin_main.FeishuAdapterError

    async def scenario():
        adapter = plugin_main._MissingFeishuAdapter("测试原因")
        for coro in (
            adapter.search_records("x"),
            adapter.create_record({"公司名称": "x"}),
            adapter.update_record("rec1", {"投递状态": "已投递"}),
            adapter.list_records(limit=1),
            adapter.check_access(),
            adapter.field_names(),
        ):
            with pytest.raises(FeishuAdapterError):
                await coro

    asyncio.run(scenario())
