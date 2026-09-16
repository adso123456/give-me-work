import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from feishu.api_adapter import FeishuAdapterError, FeishuApiAdapter, parse_table_url

TZ = timezone(timedelta(hours=8))

FIELDS = [
    {"field_id": "fld1", "field_name": "投递记录ID", "type": 1005, "ui_type": "AutoNumber"},
    {"field_id": "fld2", "field_name": "应聘岗位", "type": 1, "ui_type": "Text"},
    {"field_id": "fld3", "field_name": "公司名称", "type": 1, "ui_type": "Text"},
    {"field_id": "fld4", "field_name": "投递日期", "type": 5, "ui_type": "DateTime"},
    {"field_id": "fld5", "field_name": "简历文件", "type": 17, "ui_type": "Attachment"},
    {"field_id": "fld6", "field_name": "投递状态", "type": 3, "ui_type": "SingleSelect",
     "property": {"options": [{"name": "已投递"}, {"name": "沟通中"}]}},
    {"field_id": "fld7", "field_name": "技能标签", "type": 4, "ui_type": "MultiSelect",
     "property": {"options": [{"name": "Python"}, {"name": "RAG"}]}},
    {"field_id": "fld8", "field_name": "已读", "type": 7, "ui_type": "Checkbox"},
    {"field_id": "fld9", "field_name": "创建时间", "type": 1001, "ui_type": "CreatedTime"},
]


class StubAdapter(FeishuApiAdapter):
    """只替换 HTTP 层，其余逻辑走真实实现。"""

    def __init__(self, responses=None, fields=None):
        super().__init__("cli_test", "secret", "app_tok", "tbl_tok")
        self._fields = list(FIELDS if fields is None else fields)
        self._fields_loaded_at = time.monotonic()
        self.responses = list(responses or [])
        self.calls = []

    async def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.responses:
            result = self.responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {"data": {}}


def run(coro):
    return asyncio.run(coro)


def test_parse_table_url_extracts_tokens():
    app_token, table_id = parse_table_url(
        "https://example.feishu.cn/base/ExampleBaseToken123?table=tblExampleTable&view=vewExampleView"
    )
    assert app_token == "ExampleBaseToken123"
    assert table_id == "tblExampleTable"
    assert parse_table_url("") == (None, None)
    assert parse_table_url("not-a-url") == (None, None)


def test_to_plain_flattens_feishu_value_shapes():
    assert FeishuApiAdapter.to_plain([{"text": "XX科技", "type": "text"}]) == "XX科技"
    assert FeishuApiAdapter.to_plain([{"name": "adso"}, {"name": "bob"}]) == "adso, bob"
    assert FeishuApiAdapter.to_plain([{"file_token": "t", "name": "简历.pdf"}]) == "简历.pdf"
    assert FeishuApiAdapter.to_plain(None) == ""
    assert FeishuApiAdapter.to_plain(3) == 3
    assert FeishuApiAdapter.to_plain(["a", "b"]) == "a, b"


def test_parse_datetime_ms_accepts_common_formats():
    expected = int(datetime(2026, 9, 15, tzinfo=TZ).timestamp() * 1000)
    assert FeishuApiAdapter._parse_datetime_ms("2026-09-15") == expected
    assert FeishuApiAdapter._parse_datetime_ms("2026/09/15") == expected
    assert FeishuApiAdapter._parse_datetime_ms("2026年09月15日") == expected
    with pytest.raises(FeishuAdapterError):
        FeishuApiAdapter._parse_datetime_ms("下周一")


def test_create_record_coerces_values_and_skips_readonly_fields():
    adapter = StubAdapter(
        responses=[
            {
                "data": {
                    "record": {
                        "record_id": "rec001",
                        "fields": {"公司名称": "XX科技", "投递状态": "已投递"},
                    }
                }
            }
        ]
    )
    created = run(
        adapter.create_record(
            {
                "公司名称": "XX科技",
                "投递日期": "2026-09-15",
                "技能标签": "Python、RAG",
                "已读": "是",
                "投递记录ID": "13",
                "简历文件": "resume.pdf",
                "创建时间": "2026-09-15",
            }
        )
    )

    method, path, kwargs = adapter.calls[0]
    assert (method, path) == ("POST", "/bitable/v1/apps/app_tok/tables/tbl_tok/records")
    sent = kwargs["json_body"]["fields"]
    assert sent["公司名称"] == "XX科技"
    assert sent["投递日期"] == int(datetime(2026, 9, 15, tzinfo=TZ).timestamp() * 1000)
    assert sent["技能标签"] == ["Python", "RAG"]
    assert sent["已读"] is True
    assert "投递记录ID" not in sent
    assert "创建时间" not in sent
    assert "简历文件" not in sent
    assert created["record_id"] == "rec001"
    assert len(created["warnings"]) == 3


def test_create_record_rejects_unknown_field_and_lists_writable_ones():
    adapter = StubAdapter()
    with pytest.raises(FeishuAdapterError) as excinfo:
        run(adapter.create_record({"公司": "XX科技"}))
    message = str(excinfo.value)
    assert "字段不存在" in message
    assert "公司名称" in message
    assert adapter.calls == []


def test_update_record_targets_record_id_and_returns_plain_fields():
    adapter = StubAdapter(
        responses=[{"data": {"record": {"record_id": "rec9", "fields": {"投递状态": "沟通中"}}}}]
    )
    updated = run(adapter.update_record("rec9", {"投递状态": "沟通中"}))
    method, path, kwargs = adapter.calls[0]
    assert method == "PUT"
    assert path.endswith("/records/rec9")
    assert kwargs["json_body"]["fields"] == {"投递状态": "沟通中"}
    assert updated["fields"]["投递状态"] == "沟通中"


def test_list_records_paginates_and_maps_values():
    adapter = StubAdapter(
        responses=[
            {
                "data": {
                    "items": [
                        {
                            "record_id": "rec1",
                            "fields": {
                                "公司名称": [{"text": "XX科技", "type": "text"}],
                                "创建人": [{"id": "ou_1", "name": "adso"}],
                            },
                        }
                    ],
                    "has_more": True,
                    "page_token": "page2",
                }
            },
            {
                "data": {
                    "items": [{"record_id": "rec2", "fields": {"公司名称": "YY科技"}}],
                    "has_more": False,
                }
            },
        ]
    )
    records = run(adapter.list_records())
    assert [record["record_id"] for record in records] == ["rec1", "rec2"]
    assert records[0]["fields"]["公司名称"] == "XX科技"
    assert records[0]["fields"]["创建人"] == "adso"
    assert adapter.calls[1][2]["params"]["page_token"] == "page2"


def test_list_records_honours_limit():
    adapter = StubAdapter(
        responses=[
            {
                "data": {
                    "items": [{"record_id": "r1", "fields": {}}, {"record_id": "r2", "fields": {}}],
                    "has_more": True,
                    "page_token": "page2",
                }
            }
        ]
    )
    records = run(adapter.list_records(limit=1))
    assert [record["record_id"] for record in records] == ["r1"]
    assert len(adapter.calls) == 1


def test_search_records_uses_server_side_filter():
    adapter = StubAdapter(
        responses=[
            {
                "data": {
                    "items": [{"record_id": "r1", "fields": {"公司名称": "字节跳动"}}],
                    "has_more": False,
                }
            }
        ]
    )
    records = run(adapter.search_records("字节"))
    method, path, kwargs = adapter.calls[0]
    assert (method, path) == ("POST", "/bitable/v1/apps/app_tok/tables/tbl_tok/records/search")
    body = kwargs["json_body"]
    assert body["filter"]["conjunction"] == "or"
    condition_names = [item["field_name"] for item in body["filter"]["conditions"]]
    # 只有文本字段用 contains；单选/多选字段的取值必须命中已有选项，否则整个 filter 会被拒
    assert condition_names == ["应聘岗位", "公司名称"]
    assert body["filter"]["conditions"][0] == {
        "field_name": "应聘岗位",
        "operator": "contains",
        "value": ["字节"],
    }
    assert [record["record_id"] for record in records] == ["r1"]


def test_search_records_uses_is_when_keyword_matches_select_option():
    adapter = StubAdapter(
        responses=[{"data": {"items": [{"record_id": "r1", "fields": {}}], "has_more": False}}]
    )
    run(adapter.search_records("已投递"))
    conditions = adapter.calls[0][2]["json_body"]["filter"]["conditions"]
    by_name = {item["field_name"]: item for item in conditions}
    assert by_name["投递状态"]["operator"] == "is"
    assert by_name["投递状态"]["value"] == ["已投递"]
    assert by_name["公司名称"]["operator"] == "contains"
    # 「已投递」不是 技能标签 的选项，不能加进去，否则整个 filter 会被飞书拒绝
    assert "技能标签" not in by_name


def test_search_records_includes_multiselect_option_when_matched():
    adapter = StubAdapter(
        responses=[{"data": {"items": [{"record_id": "r1", "fields": {}}], "has_more": False}}]
    )
    run(adapter.search_records("Python"))
    conditions = adapter.calls[0][2]["json_body"]["filter"]["conditions"]
    by_name = {item["field_name"]: item for item in conditions}
    assert by_name["技能标签"]["operator"] == "is"
    assert by_name["技能标签"]["value"] == ["Python"]
    assert "投递状态" not in by_name


def test_search_records_degrades_to_text_only_when_filter_rejected():
    adapter = StubAdapter(
        responses=[
            FeishuAdapterError("飞书 API 错误 1254018: InvalidFilter"),
            {"data": {"items": [{"record_id": "r1", "fields": {}}], "has_more": False}},
        ]
    )
    records = run(adapter.search_records("已投递"))
    assert [record["record_id"] for record in records] == ["r1"]
    assert len(adapter.calls) == 2
    second_conditions = adapter.calls[1][2]["json_body"]["filter"]["conditions"]
    assert [item["field_name"] for item in second_conditions] == ["应聘岗位", "公司名称"]


def test_search_records_follows_page_token():
    adapter = StubAdapter(
        responses=[
            {
                "data": {
                    "items": [{"record_id": "r1", "fields": {}}],
                    "has_more": True,
                    "page_token": "p2",
                }
            },
            {"data": {"items": [{"record_id": "r2", "fields": {}}], "has_more": False}},
        ]
    )
    records = run(adapter.search_records("x"))
    assert [record["record_id"] for record in records] == ["r1", "r2"]
    assert adapter.calls[1][2]["params"] == {"page_token": "p2"}


def test_search_records_falls_back_to_local_match_when_api_keeps_failing():
    # 「字节」只命中文本字段，没有可退化的余地：一次搜索失败后直接回退本地匹配
    adapter = StubAdapter(
        responses=[
            FeishuAdapterError("飞书 API 错误 1254018: InvalidFilter"),
            {
                "data": {
                    "items": [
                        {"record_id": "r1", "fields": {"公司名称": "字节跳动"}},
                        {"record_id": "r2", "fields": {"公司名称": "腾讯"}},
                    ],
                    "has_more": False,
                }
            },
        ]
    )
    records = run(adapter.search_records("字节"))
    assert [record["record_id"] for record in records] == ["r1"]
    assert adapter.calls[0][1].endswith("/records/search")
    assert adapter.calls[1][0] == "GET"


def test_search_records_without_searchable_fields_uses_local_match():
    adapter = StubAdapter(
        fields=[{"field_id": "f1", "field_name": "投递记录ID", "type": 1005, "ui_type": "AutoNumber"}],
        responses=[
            {
                "data": {
                    "items": [{"record_id": "r1", "fields": {"投递记录ID": "13"}}],
                    "has_more": False,
                }
            }
        ],
    )
    records = run(adapter.search_records("13"))
    assert [record["record_id"] for record in records] == ["r1"]
    assert adapter.calls[0][0] == "GET"


def test_search_records_without_keyword_lists_all_records():
    adapter = StubAdapter(
        responses=[{"data": {"items": [{"record_id": "r1", "fields": {}}], "has_more": False}}]
    )
    records = run(adapter.search_records("   "))
    assert [record["record_id"] for record in records] == ["r1"]
    assert adapter.calls[0][0] == "GET"


@pytest.mark.parametrize("code", ["1254303", "1254043"])
def test_get_record_returns_none_when_api_reports_missing(code):
    adapter = StubAdapter(responses=[FeishuAdapterError(f"飞书 API 错误 {code}: RecordIdNotFound")])
    assert run(adapter.get_record("rec_gone")) is None


def test_get_record_propagates_permission_errors():
    adapter = StubAdapter(responses=[FeishuAdapterError("飞书 API 错误 91403: Forbidden")])
    with pytest.raises(FeishuAdapterError):
        run(adapter.get_record("rec_x"))


def test_field_names_come_from_fields_api():
    adapter = StubAdapter()
    names = run(adapter.field_names())
    assert names[0] == "投递记录ID"
    assert "简历文件" in names


def test_delete_record_calls_delete_endpoint():
    adapter = StubAdapter(responses=[{"data": {}}])
    assert run(adapter.delete_record("rec5")) is True
    method, path, _kwargs = adapter.calls[0]
    assert method == "DELETE"
    assert path == "/bitable/v1/apps/app_tok/tables/tbl_tok/records/rec5"


@pytest.mark.parametrize("code", ["1254303", "1254043"])
def test_delete_record_returns_false_when_record_missing(code):
    adapter = StubAdapter(responses=[FeishuAdapterError(f"飞书 API 错误 {code}: RecordIdNotFound")])
    assert run(adapter.delete_record("rec_gone")) is False


def test_delete_record_rejects_empty_id():
    adapter = StubAdapter()
    with pytest.raises(FeishuAdapterError):
        run(adapter.delete_record(""))


def test_delete_records_batch_reports_deleted_ids():
    adapter = StubAdapter(
        responses=[
            {
                "data": {
                    "records": [
                        {"deleted": True, "record_id": "r1"},
                        {"deleted": False, "record_id": "r2"},
                    ]
                }
            }
        ]
    )
    deleted = run(adapter.delete_records(["r1", "r2"]))
    method, path, kwargs = adapter.calls[0]
    assert method == "POST"
    assert path == "/bitable/v1/apps/app_tok/tables/tbl_tok/records/batch_delete"
    assert kwargs["json_body"] == {"records": ["r1", "r2"]}
    assert deleted == ["r1"]


def test_delete_records_falls_back_to_input_when_api_returns_no_detail():
    adapter = StubAdapter(responses=[{"data": {}}])
    assert run(adapter.delete_records(["r1", "r2"])) == ["r1", "r2"]


def test_delete_records_requires_ids():
    adapter = StubAdapter()
    with pytest.raises(FeishuAdapterError):
        run(adapter.delete_records([]))


def test_datetime_fields_are_rendered_as_readable_text():
    adapter = StubAdapter()
    ms = int(datetime(2026, 9, 16, tzinfo=TZ).timestamp() * 1000)
    record = adapter._record_to_dict(
        {"record_id": "rec1", "fields": {"投递日期": ms, "公司名称": [{"text": "XX科技"}]}}
    )
    assert record["fields"]["投递日期"] == "2026-09-16"
    assert record["fields"]["公司名称"] == "XX科技"


def test_ms_to_text_keeps_time_when_not_midnight():
    ms = int(datetime(2026, 9, 16, 14, 30, tzinfo=TZ).timestamp() * 1000)
    assert FeishuApiAdapter._ms_to_text(ms) == "2026-09-16 14:30"
    assert FeishuApiAdapter._ms_to_text(None) == ""
