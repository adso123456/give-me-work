import asyncio
from pathlib import Path

import pytest


def test_login_flow_sends_qr_and_returns_success_after_scan(tmp_path):
    from feishu.login_flow import run_feishu_login

    class FakeAdapter:
        def __init__(self):
            self.calls = []

        def has_storage_state(self):
            return False

        async def start_qr_login(self, qr_path):
            self.calls.append("start")
            Path(qr_path).write_bytes(b"qr")

        async def wait_for_qr_login(self):
            self.calls.append("wait")
            return True

        async def close(self):
            self.calls.append("close")

    adapter = FakeAdapter()
    sent = []

    async def send_qr(qr_path):
        sent.append(Path(qr_path).read_bytes())

    result = asyncio.run(
        run_feishu_login(adapter, send_qr, tmp_path / "login.png")
    )

    assert result == "登录成功"
    assert sent == [b"qr"]
    assert adapter.calls == ["start", "wait", "close"]


def test_login_flow_skips_qr_when_existing_state_is_valid(tmp_path):
    from feishu.login_flow import run_feishu_login

    class FakeAdapter:
        def __init__(self):
            self.calls = []

        def has_storage_state(self):
            return True

        async def check_access(self):
            self.calls.append("check")
            return True

        async def start_qr_login(self, qr_path):
            self.calls.append("start")

        async def close(self):
            self.calls.append("close")

    adapter = FakeAdapter()

    async def send_qr(qr_path):
        raise AssertionError("valid state must not send a QR code")

    result = asyncio.run(
        run_feishu_login(adapter, send_qr, tmp_path / "login.png")
    )

    assert result == "已有有效登录状态，无需扫码"
    assert adapter.calls == ["check", "close"]


def test_login_flow_preserves_explicit_timeout_error(tmp_path):
    from feishu.login_flow import run_feishu_login
    from feishu.web_adapter import FeishuAdapterError

    class FakeAdapter:
        def has_storage_state(self):
            return False

        async def start_qr_login(self, qr_path):
            return None

        async def wait_for_qr_login(self):
            raise FeishuAdapterError("等待飞书扫码登录超时，请重新执行 /job_feishu_login")

        async def close(self):
            return None

    async def send_qr(qr_path):
        return None

    with pytest.raises(FeishuAdapterError, match="扫码登录超时"):
        asyncio.run(run_feishu_login(FakeAdapter(), send_qr, tmp_path / "login.png"))
