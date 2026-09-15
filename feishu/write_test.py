"""真实飞书写入链路测试，不经过 Agent。"""

from __future__ import annotations

from typing import Any


TEST_RECORD_FIELDS = {
    "公司名称": "JOB_AGENT_V1_TEST",
    "应聘岗位": "AI应用开发测试岗",
    "投递状态": "已投递",
}


async def run_table_write_test(adapter: Any) -> str:
    try:
        created = await adapter.create_record(dict(TEST_RECORD_FIELDS))
    except Exception as exc:
        return f"❌ 阶段1 创建测试记录失败：{_error_summary(exc)}"

    record_id = str((created or {}).get("record_id") or "").strip()
    if not record_id:
        return "❌ 阶段1 创建测试记录失败：未返回记录 ID"

    try:
        confirmed = await adapter.get_record(record_id)
    except Exception as exc:
        return f"❌ 阶段2 读取创建记录失败：{_error_summary(exc)}"
    if not _fields_match(confirmed, TEST_RECORD_FIELDS):
        return "❌ 阶段2 读取创建记录失败：未找到刚创建的测试记录"

    update_fields = {"投递状态": "沟通中"}
    try:
        await adapter.update_record(record_id, update_fields)
    except Exception as exc:
        return f"❌ 阶段3 更新测试记录失败：{_error_summary(exc)}"

    try:
        updated = await adapter.get_record(record_id)
    except Exception as exc:
        return f"❌ 阶段4 读取更新记录失败：{_error_summary(exc)}"
    if not _fields_match(updated, {"投递状态": "沟通中"}):
        return "❌ 阶段4 读取更新记录失败：未确认投递状态已更新"

    return f"✅ 写入测试成功：已创建并确认更新记录 {record_id}"


def _fields_match(record: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    if not record:
        return False
    fields = record.get("fields")
    return isinstance(fields, dict) and all(
        str(fields.get(key, "")) == str(value) for key, value in expected.items()
    )


def _error_summary(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:180] or type(exc).__name__
