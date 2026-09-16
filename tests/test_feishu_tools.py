import asyncio


class FakeAdapter:
    def __init__(self):
        self.calls = []

    async def search_records(self, query):
        self.calls.append(("search", query))
        return [{"record_id": "r1", "fields": {"公司名称": "测试公司"}}]

    async def get_record(self, record_id):
        self.calls.append(("get", record_id))
        return {"record_id": record_id, "fields": {"投递状态": "沟通中"}}

    async def create_record(self, fields):
        self.calls.append(("create", fields))
        return {"record_id": "r2", "fields": fields}

    async def update_record(self, record_id, fields):
        self.calls.append(("update", record_id, fields))
        return {"record_id": record_id, "fields": fields}

    async def field_names(self):
        self.calls.append(("field_names",))
        return ["公司名称", "应聘岗位", "投递状态"]


def test_feishu_tools_expose_only_four_business_operations():
    from feishu.tools import FeishuTools

    async def scenario():
        adapter = FakeAdapter()
        tools = FeishuTools(adapter)
        assert set(tools.tool_names) == {
            "search_jobs",
            "get_job",
            "create_job",
            "update_job",
        }
        assert not hasattr(tools, "delete_job")
        assert (await tools.search_jobs("测试"))["records"][0]["record_id"] == "r1"
        assert (await tools.get_job("r1"))["record_id"] == "r1"
        assert (await tools.create_job({"公司名称": "新公司"}))["record_id"] == "r2"
        await tools.update_job("r2", {"投递状态": "已投递"})
        assert adapter.calls == [
            ("search", "测试"),
            ("get", "r1"),
            ("create", {"公司名称": "新公司"}),
            ("update", "r2", {"投递状态": "已投递"}),
        ]

    asyncio.run(scenario())


def test_feishu_tools_expose_table_field_names():
    from feishu.tools import FeishuTools

    async def scenario():
        tools = FeishuTools(FakeAdapter())
        assert await tools.field_names() == ["公司名称", "应聘岗位", "投递状态"]

    asyncio.run(scenario())


def test_feishu_tools_tolerate_adapter_without_field_names():
    from feishu.tools import FeishuTools

    class Bare:
        pass

    async def scenario():
        tools = FeishuTools(Bare())
        assert await tools.field_names() == []

    asyncio.run(scenario())
