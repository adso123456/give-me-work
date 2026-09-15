"""基于 Playwright 的飞书多维表格适配器。

业务层只调用本文件定义的记录方法，不接触页面点击细节。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright


logger = logging.getLogger(__name__)


class FeishuAdapterError(RuntimeError):
    pass


class FeishuWebAdapter:
    def __init__(
        self,
        table_url: str,
        headless: bool = True,
        debug_dir: str | Path | None = None,
        storage_state_path: str | Path | None = None,
        timeout_ms: int = 20_000,
    ):
        if not table_url.startswith(("https://", "http://")):
            raise ValueError("feishu_table_url must be an HTTP(S) URL")
        self.table_url = table_url
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.debug_dir = Path(debug_dir or "data/debug")
        self.storage_state_path = (
            Path(storage_state_path) if storage_state_path is not None else None
        )
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None
        self.headers: list[str] = []

    def safe_url_for_log(self) -> str:
        parsed = urlsplit(self.table_url)
        return f"{parsed.scheme}://{parsed.netloc}/base/<redacted>"

    async def open(self) -> None:
        if self.page is not None:
            return
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
            context_options: dict[str, Any] = {}
            if self.storage_state_path and self.storage_state_path.exists():
                context_options["storage_state"] = str(self.storage_state_path)
            self._context = await self._browser.new_context(**context_options)
            self.page = await self._context.new_page()
            self.page.set_default_timeout(self.timeout_ms)
            await self.page.goto(self.table_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            await self._wait_until_ready()
            self.headers = await self._read_headers()
            if not self.headers:
                raise FeishuAdapterError("未能读取飞书表头，页面可能未登录或表格结构不可读")
            if self.storage_state_path:
                self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
                await self._context.storage_state(path=str(self.storage_state_path))
            logger.info("[feishu] opened table and read %d fields", len(self.headers))
        except Exception as exc:
            await self._save_debug("open")
            await self.close()
            if isinstance(exc, FeishuAdapterError):
                raise
            raise FeishuAdapterError(f"打开飞书表格失败: {type(exc).__name__}") from exc

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self.page = None
        self._context = None
        self._browser = None
        self._playwright = None

    async def list_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        await self.open()
        assert self.page is not None
        rows = await self._read_rows()
        if limit is not None:
            return rows[: max(0, limit)]
        return rows

    async def search_records(self, query: str) -> list[dict[str, Any]]:
        records = await self.list_records()
        needle = query.strip().casefold()
        if not needle:
            return records
        return [
            record
            for record in records
            if needle in json.dumps(record, ensure_ascii=False).casefold()
        ]

    async def get_record(self, record_id: str) -> dict[str, Any] | None:
        for record in await self.list_records():
            if record.get("record_id") == record_id:
                return record
        return None

    async def create_record(self, fields: dict[str, Any]) -> dict[str, Any]:
        await self.open()
        assert self.page is not None
        try:
            await self._click_add_record()
            await self._fill_fields(fields)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(300)
            records = await self.list_records()
            record = self._find_matching_record(records, fields)
            if record is None:
                raise FeishuAdapterError("新增后未能重新读取对应记录")
            logger.info("[feishu] created record")
            return record
        except Exception as exc:
            await self._save_debug("create")
            if isinstance(exc, FeishuAdapterError):
                raise
            raise FeishuAdapterError(f"新增飞书记录失败: {type(exc).__name__}") from exc

    async def update_record(self, record_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        await self.open()
        assert self.page is not None
        try:
            current = await self.get_record(record_id)
            if current is None:
                raise FeishuAdapterError(f"未找到记录: {record_id}")
            await self._select_record(record_id)
            await self._fill_fields(fields)
            await self.page.keyboard.press("Enter")
            await self.page.wait_for_timeout(300)
            updated = await self.get_record(record_id)
            if updated is None:
                raise FeishuAdapterError("修改后未能重新读取对应记录")
            logger.info("[feishu] updated record")
            return updated
        except Exception as exc:
            await self._save_debug("update")
            if isinstance(exc, FeishuAdapterError):
                raise
            raise FeishuAdapterError(f"修改飞书记录失败: {type(exc).__name__}") from exc

    async def check_access(self) -> bool:
        await self.open()
        return bool(self.headers)

    async def _wait_until_ready(self) -> None:
        assert self.page is not None
        await self.page.wait_for_load_state("domcontentloaded")
        body = (await self.page.locator("body").inner_text()).strip()
        if self.is_login_page_text(body) or "accounts.feishu.cn" in (self.page.url or ""):
            raise FeishuAdapterError("飞书页面需要登录，服务器无可用登录会话")

    @staticmethod
    def is_login_page_text(text: str) -> bool:
        normalized = " ".join((text or "").casefold().split())
        return any(
            marker in normalized
            for marker in (
                "log in with qr code",
                "login with qr code",
                "sign up now",
                "扫码登录",
                "登录飞书",
            )
        )

    async def _read_headers(self) -> list[str]:
        assert self.page is not None
        selectors = (
            '[role="columnheader"]',
            '[data-testid*="column-header"]',
            '[data-testid*="field-name"]',
            '[class*="column-header"]',
            '[class*="field-name"]',
        )
        deadline = asyncio.get_running_loop().time() + (self.timeout_ms / 1000)
        while True:
            values: list[str] = []
            for selector in selectors:
                try:
                    values.extend(await self.page.locator(selector).all_text_contents())
                except Exception:
                    continue
            headers = self._unique_text(values)
            if not headers:
                try:
                    headers = self._headers_from_ssr_html(await self.page.content())
                except Exception:
                    headers = []
            if headers:
                return headers
            if asyncio.get_running_loop().time() >= deadline:
                return []
            await self.page.wait_for_timeout(300)

    async def _read_rows(self) -> list[dict[str, Any]]:
        assert self.page is not None
        row_locators = ('[role="row"]', 'tr', '[data-row-index]', '[class*="table-row"]')
        rows: list[dict[str, Any]] = []
        for row_selector in row_locators:
            locator = self.page.locator(row_selector)
            try:
                count = await locator.count()
            except Exception:
                continue
            if count == 0:
                continue
            for index in range(count):
                row = locator.nth(index)
                cell_texts: list[str] = []
                for cell_selector in ('[role="gridcell"]', '[role="cell"]', 'td', '[data-col-index]'):
                    try:
                        cell_texts = await row.locator(cell_selector).all_text_contents()
                    except Exception:
                        continue
                    if cell_texts:
                        break
                values = [value.strip() for value in cell_texts]
                if not values or not any(values):
                    continue
                fields = dict(zip(self.headers, values, strict=False))
                record_id = fields.get("投递记录ID") or fields.get("记录ID")
                if not record_id:
                    try:
                        record_id = await row.get_attribute("data-record-id")
                    except Exception:
                        record_id = None
                rows.append({"record_id": record_id or f"row_{index}", "fields": fields})
            if rows:
                break
        if rows:
            return rows
        return await self._read_clipboard_rows()

    async def _read_clipboard_rows(self) -> list[dict[str, Any]]:
        assert self.page is not None
        if self._context is None or not self.headers:
            return []
        try:
            parsed = urlsplit(self.page.url)
            await self._context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin=f"{parsed.scheme}://{parsed.netloc}",
            )
            canvas = self.page.locator('canvas[role="faster"]').first
            if not await canvas.count():
                canvas = self.page.locator("canvas").first
            if not await canvas.count():
                return []
            box = await canvas.bounding_box()
            if not box:
                return []
            deadline = asyncio.get_running_loop().time() + (self.timeout_ms / 1000)
            while True:
                await self.page.mouse.click(box["x"] + 120, box["y"] + 80)
                await self.page.keyboard.press("Control+A")
                await self.page.keyboard.press("Control+C")
                await self.page.wait_for_timeout(300)
                clipboard = await self.page.evaluate("navigator.clipboard.readText()")
                rows = self._parse_clipboard_rows(clipboard, self.headers)
                if rows:
                    return rows
                if asyncio.get_running_loop().time() >= deadline:
                    return []
                await self.page.wait_for_timeout(500)
        except Exception as exc:
            logger.debug("[feishu] failed to read canvas clipboard", exc_info=exc)
            return []

    async def _click_add_record(self) -> None:
        assert self.page is not None
        selectors = (
            '[aria-label*="添加记录"]',
            '[aria-label*="add record" i]',
            '[data-testid*="add-record"]',
            '[data-testid*="add_row"]',
        )
        for selector in selectors:
            locator = self.page.locator(selector).first
            if await locator.count():
                await locator.click()
                return
        raise FeishuAdapterError("未找到新增记录控件")

    async def _select_record(self, record_id: str) -> None:
        assert self.page is not None
        locator = self.page.locator(f'[data-record-id="{record_id}"]').first
        if await locator.count():
            await locator.click()
            return
        raise FeishuAdapterError("未找到可编辑的记录行")

    async def _fill_fields(self, fields: dict[str, Any]) -> None:
        assert self.page is not None
        for name, value in fields.items():
            locator = None
            for selector in (
                f'[data-field-name="{name}"] input',
                f'[data-field-name="{name}"] textarea',
                f'[aria-label="{name}"]',
            ):
                candidate = self.page.locator(selector).first
                if await candidate.count():
                    locator = candidate
                    break
            if locator is None:
                raise FeishuAdapterError(f"字段不存在或不可编辑: {name}")
            await locator.click()
            await locator.fill(self._stringify_value(value))

    @staticmethod
    def _find_matching_record(records: list[dict[str, Any]], fields: dict[str, Any]) -> dict[str, Any] | None:
        for record in reversed(records):
            current = record.get("fields") or {}
            if all(str(current.get(key, "")) == str(value) for key, value in fields.items()):
                return record
        return None

    async def _save_debug(self, operation: str) -> None:
        if self.page is None:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(self.debug_dir / f"{operation}.png"), full_page=True)
        except Exception:
            logger.debug("[feishu] failed to save debug screenshot", exc_info=True)

    @staticmethod
    def _unique_text(values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = " ".join(value.split())
            if text and text not in result:
                result.append(text)
        return result

    @staticmethod
    def _headers_from_ssr_html(html: str) -> list[str]:
        match = re.search(r'"texts":(\[.*?\]),"viewType"', html, flags=re.S)
        if not match:
            return []
        try:
            texts = json.loads(match.group(1))
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(texts, list):
            return []
        values = [str(value).strip() for value in texts if str(value).strip()]
        primary_key = "投递记录ID"
        if primary_key not in values:
            return []
        primary_index = values.index(primary_key)
        return [primary_key, *values[:primary_index]]

    @staticmethod
    def _parse_clipboard_rows(
        clipboard: str, headers: list[str]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, line in enumerate((clipboard or "").splitlines()):
            values = [value.strip() for value in line.rstrip("\r").split("\t")]
            if not values or not any(values):
                continue
            fields = dict(zip(headers, values, strict=False))
            record_id = fields.get("投递记录ID") or fields.get("记录ID")
            rows.append(
                {
                    "record_id": record_id or f"row_{index}",
                    "fields": fields,
                }
            )
        return rows

    @staticmethod
    def _stringify_value(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return "" if value is None else str(value)
