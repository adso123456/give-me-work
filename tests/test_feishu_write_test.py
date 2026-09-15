import asyncio

import pytest


TEST_FIELDS = {
    "公司名称": "JOB_AGENT_V1_TEST",
    "应聘岗位": "AI应用开发测试岗",
    "投递状态": "已投递",
}


class FakeWriteAdapter:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.calls = []
        self.record = {
            "record_id": "rec_test_1",
            "fields": dict(TEST_FIELDS),
        }

    async def create_record(self, fields):
        self.calls.append(("create", fields))
        if self.fail_at == "create":
            raise RuntimeError("create failed")
        return {"record_id": self.record["record_id"], "fields": dict(fields)}

    async def get_record(self, record_id):
        self.calls.append(("get", record_id))
        if self.fail_at == "read_create":
            return None
        if self.fail_at == "read_update":
            return {"record_id": record_id, "fields": dict(TEST_FIELDS)}
        return {"record_id": record_id, "fields": dict(self.record["fields"])}

    async def update_record(self, record_id, fields):
        self.calls.append(("update", record_id, fields))
        if self.fail_at == "update":
            raise RuntimeError("update failed")
        self.record["fields"].update(fields)
        return {"record_id": record_id, "fields": dict(self.record["fields"])}


@pytest.mark.parametrize(
    ("fail_at", "expected"),
    [
        ("create", "❌ 阶段1 创建测试记录失败：create failed"),
        ("read_create", "❌ 阶段2 读取创建记录失败：未找到刚创建的测试记录"),
        ("update", "❌ 阶段3 更新测试记录失败：update failed"),
        ("read_update", "❌ 阶段4 读取更新记录失败：未确认投递状态已更新"),
    ],
)
def test_write_test_reports_the_failing_stage(fail_at, expected):
    from feishu.write_test import run_table_write_test

    result = asyncio.run(run_table_write_test(FakeWriteAdapter(fail_at)))

    assert result == expected


def test_write_test_creates_then_updates_without_deleting():
    from feishu.write_test import run_table_write_test

    adapter = FakeWriteAdapter()

    result = asyncio.run(run_table_write_test(adapter))

    assert result == "✅ 写入测试成功：已创建并确认更新记录 rec_test_1"
    assert adapter.calls == [
        ("create", TEST_FIELDS),
        ("get", "rec_test_1"),
        ("update", "rec_test_1", {"投递状态": "沟通中"}),
        ("get", "rec_test_1"),
    ]
