import asyncio
from pathlib import Path


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


def test_adapter_log_url_redaction_never_returns_full_url():
    from feishu.web_adapter import FeishuWebAdapter

    adapter = FeishuWebAdapter(
        "https://my.feishu.cn/base/secret-base?table=secret-table&view=secret-view"
    )
    redacted = adapter.safe_url_for_log()
    assert "secret-table" not in redacted
    assert "secret-view" not in redacted
    assert redacted.startswith("https://my.feishu.cn/base/")


def test_adapter_recognizes_feishu_login_page():
    from feishu.web_adapter import FeishuWebAdapter

    assert FeishuWebAdapter.is_login_page_text("Log In With QR Code")
    assert FeishuWebAdapter.is_login_page_text("扫码登录飞书")
    assert not FeishuWebAdapter.is_login_page_text("简历投递表 待投递视图")


def test_adapter_reuses_and_saves_persistent_storage_state(monkeypatch, tmp_path):
    from feishu import web_adapter

    captured = {}

    class FakeLocator:
        async def inner_text(self):
            return "简历投递表"

        async def all_text_contents(self):
            return ["公司名称"]

    class FakePage:
        url = "https://my.feishu.cn/base/test"

        def set_default_timeout(self, timeout):
            captured["timeout"] = timeout

        async def goto(self, *args, **kwargs):
            return None

        async def wait_for_load_state(self, *args, **kwargs):
            return None

        def locator(self, selector):
            return FakeLocator()

        async def screenshot(self, **kwargs):
            return None

    class FakeContext:
        async def new_page(self):
            return FakePage()

        async def storage_state(self, path):
            captured["saved_state"] = path

        async def close(self):
            return None

    class FakeBrowser:
        async def new_context(self, **kwargs):
            captured["context_kwargs"] = kwargs
            return FakeContext()

        async def close(self):
            return None

    class FakeChromium:
        async def launch(self, **kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        async def start(self):
            return self

        async def stop(self):
            return None

    monkeypatch.setattr(web_adapter, "async_playwright", lambda: FakePlaywright())
    state_path = tmp_path / "feishu_storage_state.json"
    state_path.write_text("{}", encoding="utf-8")
    adapter = web_adapter.FeishuWebAdapter(
        "https://my.feishu.cn/base/test",
        storage_state_path=state_path,
        debug_dir=tmp_path / "debug",
    )

    asyncio.run(adapter.open())

    assert captured["context_kwargs"] == {"storage_state": str(state_path)}
    assert captured["saved_state"] == str(state_path)


def test_adapter_waits_for_async_table_headers():
    from feishu.web_adapter import FeishuWebAdapter

    class FakeLocator:
        def __init__(self, page):
            self.page = page

        async def all_text_contents(self):
            self.page.calls += 1
            return [] if self.page.calls < 6 else ["公司名称"]

    class FakePage:
        def __init__(self):
            self.calls = 0
            self.waits = []

        def locator(self, selector):
            return FakeLocator(self)

        async def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    adapter = FeishuWebAdapter("https://my.feishu.cn/base/test", timeout_ms=1000)
    adapter.page = FakePage()

    headers = asyncio.run(adapter._read_headers())

    assert headers == ["公司名称"]
    assert adapter.page.waits


def test_adapter_extracts_canvas_headers_from_feishu_ssr_data():
    from feishu.web_adapter import FeishuWebAdapter

    html = (
        '<script>"texts":["应聘岗位","公司名称","创建人",'
        '"投递记录ID","13"],"viewType":1</script>'
    )

    assert FeishuWebAdapter._headers_from_ssr_html(html) == [
        "投递记录ID",
        "应聘岗位",
        "公司名称",
        "创建人",
    ]


def test_adapter_parses_canvas_clipboard_rows():
    from feishu.web_adapter import FeishuWebAdapter

    headers = ["投递记录ID", "应聘岗位", "公司名称"]
    clipboard = "13\tPython 工程师\t测试公司\n"

    assert FeishuWebAdapter._parse_clipboard_rows(clipboard, headers) == [
        {
            "record_id": "13",
            "fields": {
                "投递记录ID": "13",
                "应聘岗位": "Python 工程师",
                "公司名称": "测试公司",
            },
        }
    ]


def test_adapter_qr_login_saves_storage_state_after_scan(monkeypatch, tmp_path):
    from feishu import web_adapter

    class FakePage:
        def __init__(self):
            self.login_checks = 0
            self.url = "https://accounts.feishu.cn/login"
            self.qr_path = None

        def set_default_timeout(self, timeout):
            return None

        async def goto(self, *args, **kwargs):
            return None

        async def wait_for_load_state(self, *args, **kwargs):
            return None

        def locator(self, selector):
            return self

        async def inner_text(self):
            self.login_checks += 1
            if self.login_checks == 1:
                return "Log In With QR Code"
            self.url = "https://my.feishu.cn/base/test"
            return "简历投递表"

        async def screenshot(self, path, **kwargs):
            self.qr_path = path
            Path(path).write_bytes(b"qr")

        async def wait_for_timeout(self, milliseconds):
            return None

    class FakeContext:
        def __init__(self, page):
            self.page = page
            self.saved_path = None

        async def new_page(self):
            return self.page

        async def storage_state(self, path):
            self.saved_path = path
            Path(path).write_text("{}", encoding="utf-8")

        async def close(self):
            return None

    class FakeBrowser:
        def __init__(self, context):
            self.context = context

        async def new_context(self, **kwargs):
            return self.context

        async def close(self):
            return None

    class FakePlaywright:
        def __init__(self, browser):
            self.browser = browser
            self.chromium = self

        async def start(self):
            return self

        async def launch(self, **kwargs):
            return self.browser

        async def stop(self):
            return None

    page = FakePage()
    context = FakeContext(page)
    monkeypatch.setattr(
        web_adapter,
        "async_playwright",
        lambda: FakePlaywright(FakeBrowser(context)),
    )
    state_path = tmp_path / "feishu_storage_state.json"
    qr_path = tmp_path / "feishu_login_qr.png"
    adapter = web_adapter.FeishuWebAdapter(
        "https://my.feishu.cn/base/test",
        storage_state_path=state_path,
    )

    async def scenario():
        await adapter.start_qr_login(qr_path)
        assert qr_path.read_bytes() == b"qr"
        assert await adapter.wait_for_qr_login(timeout_ms=100)
        await adapter.close()

    asyncio.run(scenario())

    assert state_path.exists()
    assert context.saved_path == str(state_path)


def test_adapter_qr_login_timeout_has_clear_error(monkeypatch, tmp_path):
    from feishu import web_adapter

    class FakePage:
        url = "https://accounts.feishu.cn/login"

        def set_default_timeout(self, timeout):
            return None

        async def goto(self, *args, **kwargs):
            return None

        async def wait_for_load_state(self, *args, **kwargs):
            return None

        def locator(self, selector):
            return self

        async def inner_text(self):
            return "Log In With QR Code"

        async def screenshot(self, path, **kwargs):
            Path(path).write_bytes(b"qr")

        async def wait_for_timeout(self, milliseconds):
            return None

    class FakeContext:
        async def new_page(self):
            return FakePage()

        async def storage_state(self, path):
            return None

        async def close(self):
            return None

    class FakeBrowser:
        async def new_context(self, **kwargs):
            return FakeContext()

        async def close(self):
            return None

    class FakePlaywright:
        chromium = None

        async def start(self):
            self.chromium = self
            return self

        async def launch(self, **kwargs):
            return FakeBrowser()

        async def stop(self):
            return None

    monkeypatch.setattr(web_adapter, "async_playwright", lambda: FakePlaywright())
    adapter = web_adapter.FeishuWebAdapter(
        "https://my.feishu.cn/base/test",
        storage_state_path=tmp_path / "state.json",
    )

    async def scenario():
        await adapter.start_qr_login(tmp_path / "qr.png")
        try:
            await adapter.wait_for_qr_login(timeout_ms=5)
        finally:
            await adapter.close()

    try:
        asyncio.run(scenario())
    except web_adapter.FeishuAdapterError as exc:
        assert "扫码登录超时" in str(exc)
    else:
        raise AssertionError("expected QR login timeout")
