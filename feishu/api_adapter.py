# -*- coding: utf-8 -*-
"""基于飞书开放平台多维表格 API 的记录适配器。

与原先的 Playwright 方案相比：
- 不依赖页面 DOM / canvas 结构，飞书前端改版不会失效；
- `record_id` 是飞书真实记录 ID，可直接用于更新；
- 字段名即表头，由 `/fields` 接口权威给出，不再猜表头；
- 不需要常驻 Chromium（服务器只有 4G 内存）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import aiohttp

logger = logging.getLogger(__name__)

OPEN_API_BASE = "https://open.feishu.cn/open-apis"
DEFAULT_TZ = timezone(timedelta(hours=8))

# 只读/系统字段：写入时忽略并返回 warning
READONLY_UI_TYPES = {
    "Formula",
    "Lookup",
    "CreatedTime",
    "ModifiedTime",
    "CreatedUser",
    "ModifiedUser",
    "AutoNumber",
}
# 可以用简单标量写入的字段
SCALAR_UI_TYPES = {
    "Text",
    "Number",
    "SingleSelect",
    "MultiSelect",
    "DateTime",
    "Checkbox",
    "Phone",
    "Url",
    "Currency",
    "Progress",
    "Rating",
    "Barcode",
}
# 目前无法通过 Agent 写入的字段（附件/关联/人员等），写入时忽略并返回 warning
UNWRITABLE_UI_TYPES = {
    "Attachment",
    "SingleLink",
    "DuplexLink",
    "User",
    "GroupChat",
    "Location",
    "Reference",
}
# 可以参与服务端 contains 过滤的文本类字段
SEARCHABLE_TEXT_UI_TYPES = {
    "Text",
    "Phone",
    "Url",
    "Barcode",
    "Email",
}
# 单选/多选字段只能用 is 精确匹配，而且取值必须是已存在的选项，
# 否则飞书会直接返回 1254018 InvalidFilter（实测）。
SEARCHABLE_SELECT_UI_TYPES = {"SingleSelect", "MultiSelect"}

ERROR_HINTS = {
    91403: "权限不足：请在开放平台为应用添加「查看、评论、编辑和管理多维表格」(bitable:app) 权限并发布版本，"
    "同时把该多维表格添加为应用可编辑的文档/协作者",
    99991663: "tenant_access_token 无效或过期（将自动重试一次）",
    99991661: "tenant_access_token 缺失",
    1254005: "app_token/table_id 或字段名不存在",
    1254043: "记录不存在或已被删除",
    1254303: "记录不存在或已被删除",
    1254302: "字段值类型不符合字段类型",
}

# 飞书对"记录不存在"返回过多种错误码，这里统一处理
NOT_FOUND_CODES = ("1254043", "1254303")


def is_not_found_error(exc: BaseException) -> bool:
    text = str(exc)
    return any(code in text for code in NOT_FOUND_CODES)


class FeishuAdapterError(RuntimeError):
    """飞书 API 调用失败，message 直接可读。"""


def parse_table_url(url: str) -> tuple[str | None, str | None]:
    """从多维表格分享链接里解析 (app_token, table_id)。"""
    if not url:
        return None, None
    parsed = urlsplit(url.strip())
    if not parsed.scheme.startswith("http"):
        return None, None
    parts = [segment for segment in parsed.path.split("/") if segment]
    app_token = None
    for index, segment in enumerate(parts):
        if segment in {"base", "bitable"} and index + 1 < len(parts):
            app_token = parts[index + 1]
            break
    query = parse_qs(parsed.query)
    table_id = (query.get("table") or [None])[0]
    return app_token, table_id


class FeishuApiAdapter:
    """飞书多维表格记录适配器（tenant_access_token 模式）。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        app_token: str,
        table_id: str,
        base_url: str = OPEN_API_BASE,
        timeout_seconds: float = 20.0,
        session: aiohttp.ClientSession | None = None,
    ):
        if not app_id or not app_secret:
            raise ValueError("app_id / app_secret is required")
        if not app_token or not table_id:
            raise ValueError("app_token / table_id is required")
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._session = session
        self._own_session = session is None
        self._token: str | None = None
        self._token_expire_at = 0.0
        self._token_lock = asyncio.Lock()
        self._fields: list[dict[str, Any]] = []
        self._fields_loaded_at = 0.0
        self._fields_ttl = 300.0

    # ------------------------------------------------------------------ 基础

    @property
    def table_path(self) -> str:
        return f"/bitable/v1/apps/{self.app_token}/tables/{self.table_id}"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        if self._own_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def tenant_token(self, force: bool = False) -> str:
        async with self._token_lock:
            if not force and self._token and time.monotonic() < self._token_expire_at:
                return self._token
            payload = await self._raw_request(
                "POST",
                "/auth/v3/tenant_access_token/internal",
                json_body={"app_id": self.app_id, "app_secret": self.app_secret},
                with_token=False,
            )
            token = str(payload.get("tenant_access_token") or "")
            if not token:
                raise FeishuAdapterError("获取 tenant_access_token 失败：响应中没有 token")
            expire = int(payload.get("expire") or 7200)
            self._token = token
            self._token_expire_at = time.monotonic() + max(60, expire - 120)
            return token

    async def _raw_request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        with_token: bool = True,
    ) -> dict[str, Any]:
        session = await self._get_session()
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if with_token:
            headers["Authorization"] = f"Bearer {await self.tenant_token()}"
        try:
            async with session.request(
                method,
                url,
                json=json_body,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            ) as response:
                text = await response.text()
        except asyncio.TimeoutError as exc:
            raise FeishuAdapterError(f"飞书 API 请求超时（{self.timeout_seconds:.0f}s）：{path}") from exc
        except aiohttp.ClientError as exc:
            raise FeishuAdapterError(f"飞书 API 网络错误：{type(exc).__name__}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FeishuAdapterError(
                f"飞书 API 返回非 JSON（HTTP {response.status}）：{text[:120]}"
            ) from exc
        code = data.get("code")
        if code not in (0, None):
            raise FeishuAdapterError(self._describe_error(code, data.get("msg") or "", path))
        return data

    @staticmethod
    def _describe_error(code: Any, msg: str, path: str) -> str:
        hint = ERROR_HINTS.get(code)
        base = f"飞书 API 错误 {code}: {msg or 'unknown'}"
        return f"{base}（{hint}）" if hint else f"{base}（path={path}）"

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """带一次 token 失效重试的请求。"""
        try:
            return await self._raw_request(method, path, **kwargs)
        except FeishuAdapterError as exc:
            if "99991663" not in str(exc):
                raise
            await self.tenant_token(force=True)
            return await self._raw_request(method, path, **kwargs)

    # ------------------------------------------------------------------ 字段

    async def list_fields(self, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh and self._fields and time.monotonic() - self._fields_loaded_at < self._fields_ttl:
            return self._fields
        fields: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            payload = await self._request("GET", f"{self.table_path}/fields", params=params)
            data = payload.get("data") or {}
            fields.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        self._fields = fields
        self._fields_loaded_at = time.monotonic()
        return fields

    async def field_names(self) -> list[str]:
        return [str(item.get("field_name") or "") for item in await self.list_fields()]

    def _field_map(self) -> dict[str, dict[str, Any]]:
        return {str(item.get("field_name") or ""): item for item in self._fields}

    @staticmethod
    def _ui_type(field: dict[str, Any]) -> str:
        return str(field.get("ui_type") or field.get("type") or "")

    # ------------------------------------------------------------------ 值转换

    @classmethod
    def to_plain(cls, value: Any) -> Any:
        """把飞书字段值压成 Agent 好读的标量/字符串。"""
        if value is None:
            return ""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [cls.to_plain(item) for item in value]
            parts = [part for part in parts if part not in ("", None)]
            if not parts:
                return ""
            if all(isinstance(part, str) for part in parts):
                return ", ".join(parts)
            return parts
        if isinstance(value, dict):
            for key in ("text", "name", "en_name", "value"):
                if key in value:
                    return cls.to_plain(value[key])
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    @classmethod
    def _to_plain_fields(cls, fields: dict[str, Any]) -> dict[str, Any]:
        return {key: cls.to_plain(value) for key, value in (fields or {}).items()}

    @staticmethod
    def _parse_datetime_ms(value: Any) -> int:
        if isinstance(value, (int, float)):
            number = float(value)
            return int(number if number > 1e11 else number * 1000)
        text = str(value).strip()
        if not text:
            raise FeishuAdapterError("日期字段的值为空")
        if text.isdigit():
            number = float(text)
            return int(number if number > 1e11 else number * 1000)
        normalized = text.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                parsed = datetime.strptime(normalized.strip(), fmt)
            except ValueError:
                continue
            return int(parsed.replace(tzinfo=DEFAULT_TZ).timestamp() * 1000)
        raise FeishuAdapterError(f"无法解析日期字段的值：{value}（请用 YYYY-MM-DD）")

    @classmethod
    def _coerce_value(cls, field: dict[str, Any], value: Any) -> Any:
        ui_type = cls._ui_type(field)
        if ui_type in {"DateTime"}:
            return cls._parse_datetime_ms(value)
        if ui_type in {"Number", "Currency", "Progress", "Rating"}:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
            text = str(value).strip().replace(",", "").replace("%", "")
            try:
                number = float(text)
            except ValueError as exc:
                raise FeishuAdapterError(f"字段「{field.get('field_name')}」需要数字，收到：{value}") from exc
            return int(number) if number.is_integer() else number
        if ui_type == "Checkbox":
            if isinstance(value, bool):
                return value
            return str(value).strip().casefold() in {"1", "true", "yes", "y", "是", "已"}
        if ui_type == "MultiSelect":
            if isinstance(value, list):
                return [str(item) for item in value]
            text = str(value)
            return [part.strip() for part in text.replace("、", ",").replace("/", ",").split(",") if part.strip()]
        return "" if value is None else str(value)

    # ------------------------------------------------------------------ 记录

    @classmethod
    def _ms_to_text(cls, value: Any) -> str:
        """毫秒时间戳 → 可读日期，Agent 不必自己换算。"""
        if value in (None, ""):
            return ""
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return str(cls.to_plain(value))
        try:
            moment = datetime.fromtimestamp(float(value) / 1000, tz=DEFAULT_TZ)
        except (OverflowError, OSError, ValueError):
            return str(value)
        if (moment.hour, moment.minute, moment.second) == (0, 0, 0):
            return moment.strftime("%Y-%m-%d")
        return moment.strftime("%Y-%m-%d %H:%M")

    def _record_to_dict(self, item: dict[str, Any]) -> dict[str, Any]:
        known = self._field_map()
        fields: dict[str, Any] = {}
        for key, value in (item.get("fields") or {}).items():
            field = known.get(key)
            ui_type = self._ui_type(field) if field else ""
            if ui_type in {"DateTime", "CreatedTime", "ModifiedTime"}:
                fields[key] = self._ms_to_text(value)
            else:
                fields[key] = self.to_plain(value)
        return {"record_id": str(item.get("record_id") or ""), "fields": fields}

    async def list_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            payload = await self._request("GET", f"{self.table_path}/records", params=params)
            data = payload.get("data") or {}
            records.extend(self._record_to_dict(item) for item in (data.get("items") or []))
            if limit is not None and len(records) >= limit:
                return records[:limit]
            if not data.get("has_more"):
                return records
            page_token = data.get("page_token")

    async def search_records(self, query: str) -> list[dict[str, Any]]:
        """优先走飞书 records/search 服务端过滤，失败再回退本地匹配。

        本地匹配需要把整张表拉下来，表一大就是全量拉取；服务端搜索只回传命中的记录。
        """
        needle = (query or "").strip()
        if not needle:
            return await self.list_records()
        try:
            return await self._search_via_api(needle)
        except FeishuAdapterError as exc:
            logger.warning("[feishu] 服务端搜索失败(%s)，回退到本地匹配", exc)
            return await self._search_local(needle)

    async def _search_conditions(self, needle: str, text_only: bool = False) -> list[dict[str, Any]]:
        fields = await self.list_fields()
        conditions: list[dict[str, Any]] = []
        for field in fields:
            name = str(field.get("field_name") or "")
            if not name:
                continue
            ui_type = self._ui_type(field)
            if ui_type in SEARCHABLE_TEXT_UI_TYPES:
                conditions.append(
                    {"field_name": name, "operator": "contains", "value": [needle]}
                )
            elif (
                not text_only
                and ui_type in SEARCHABLE_SELECT_UI_TYPES
                and needle in self._select_option_names(field)
            ):
                # 只在该选项确实存在时才用 is，否则飞书会拒绝整个 filter
                conditions.append(
                    {"field_name": name, "operator": "is", "value": [needle]}
                )
        return conditions

    @staticmethod
    def _select_option_names(field: dict[str, Any]) -> set[str]:
        options = (field.get("property") or {}).get("options") or []
        return {
            str(option["name"])
            for option in options
            if isinstance(option, dict) and option.get("name") is not None
        }

    async def _search_via_api(self, needle: str) -> list[dict[str, Any]]:
        conditions = await self._search_conditions(needle)
        if not conditions:
            return await self._search_local(needle)
        try:
            return await self._run_search(conditions)
        except FeishuAdapterError as exc:
            # 例如某个字段类型不接受该操作符：退化成只用文本字段再试一次
            text_conditions = await self._search_conditions(needle, text_only=True)
            if not text_conditions or text_conditions == conditions:
                raise
            logger.warning("[feishu] 过滤条件被拒绝(%s)，退化为仅文本字段重试", exc)
            return await self._run_search(text_conditions)

    async def _run_search(self, conditions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            body: dict[str, Any] = {
                "filter": {"conjunction": "or", "conditions": conditions},
                "page_size": 500,
            }
            params = {"page_token": page_token} if page_token else None
            payload = await self._request(
                "POST",
                f"{self.table_path}/records/search",
                json_body=body,
                params=params,
            )
            data = payload.get("data") or {}
            records.extend(self._record_to_dict(item) for item in (data.get("items") or []))
            page_token = data.get("page_token")
            if not data.get("has_more") or not page_token:
                return records

    async def _search_local(self, needle: str) -> list[dict[str, Any]]:
        lowered = needle.casefold()
        records = await self.list_records()
        return [
            record
            for record in records
            if lowered in json.dumps(record.get("fields") or {}, ensure_ascii=False).casefold()
        ]

    async def get_record(self, record_id: str) -> dict[str, Any] | None:
        if not record_id:
            return None
        try:
            payload = await self._request("GET", f"{self.table_path}/records/{record_id}")
        except FeishuAdapterError as exc:
            if is_not_found_error(exc):
                return None
            raise
        record = (payload.get("data") or {}).get("record")
        return self._record_to_dict(record) if record else None

    async def _prepare_fields(self, fields: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        if not isinstance(fields, dict) or not fields:
            raise FeishuAdapterError("fields 必须是非空对象")
        known = self._field_map()
        prepared: dict[str, Any] = {}
        warnings: list[str] = []
        unknown: list[str] = []
        for name, value in fields.items():
            field = known.get(str(name))
            if field is None:
                unknown.append(str(name))
                continue
            ui_type = self._ui_type(field)
            if ui_type in READONLY_UI_TYPES:
                warnings.append(f"字段「{name}」是系统只读字段，已忽略")
                continue
            if ui_type in UNWRITABLE_UI_TYPES:
                warnings.append(f"字段「{name}」是{ui_type}类型，暂不支持由 Agent 写入，已忽略")
                continue
            prepared[str(name)] = self._coerce_value(field, value)
        if unknown:
            writable = [
                str(item.get("field_name"))
                for item in self._fields
                if self._ui_type(item) not in READONLY_UI_TYPES
                and self._ui_type(item) not in UNWRITABLE_UI_TYPES
            ]
            raise FeishuAdapterError(
                f"字段不存在：{'、'.join(unknown)}；当前可写字段：{'、'.join(writable) or '（无）'}"
            )
        if not prepared:
            raise FeishuAdapterError("没有任何可写入的字段（全部被忽略）")
        return prepared, warnings

    async def create_record(self, fields: dict[str, Any]) -> dict[str, Any]:
        await self.list_fields()
        prepared, warnings = await self._prepare_fields(fields)
        payload = await self._request(
            "POST", f"{self.table_path}/records", json_body={"fields": prepared}
        )
        record = (payload.get("data") or {}).get("record") or {}
        result = self._record_to_dict(record)
        if warnings:
            result["warnings"] = warnings
        logger.info("[feishu] created record %s", result.get("record_id"))
        return result

    async def update_record(self, record_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not record_id:
            raise FeishuAdapterError("record_id 不能为空")
        await self.list_fields()
        prepared, warnings = await self._prepare_fields(fields)
        payload = await self._request(
            "PUT", f"{self.table_path}/records/{record_id}", json_body={"fields": prepared}
        )
        record = (payload.get("data") or {}).get("record") or {}
        result = self._record_to_dict(record)
        if warnings:
            result["warnings"] = warnings
        logger.info("[feishu] updated record %s", record_id)
        return result

    async def check_access(self) -> bool:
        fields = await self.list_fields(refresh=True)
        return bool(fields)

    async def delete_record(self, record_id: str) -> bool:
        """删除单条记录；记录不存在时返回 False。"""
        if not record_id:
            raise FeishuAdapterError("record_id 不能为空")
        try:
            await self._request(
                "DELETE", f"{self.table_path}/records/{record_id}"
            )
        except FeishuAdapterError as exc:
            if is_not_found_error(exc):
                return False
            raise
        logger.info("[feishu] deleted record %s", record_id)
        return True

    async def delete_records(self, record_ids: list[str]) -> list[str]:
        """批量删除记录，返回实际被删除的 record_id 列表。"""
        ids = [str(item).strip() for item in (record_ids or []) if str(item).strip()]
        if not ids:
            raise FeishuAdapterError("record_ids 不能为空")
        payload = await self._request(
            "POST",
            f"{self.table_path}/records/batch_delete",
            json_body={"records": ids},
        )
        records = (payload.get("data") or {}).get("records")
        if not isinstance(records, list) or not records:
            logger.info("[feishu] batch deleted %d record(s)", len(ids))
            return ids
        deleted = [
            str(item.get("record_id"))
            for item in records
            if isinstance(item, dict) and item.get("deleted", True)
        ]
        logger.info("[feishu] batch deleted %d/%d record(s)", len(deleted), len(ids))
        return deleted

    # 兼容旧接口（浏览器方案遗留），API 模式无需扫码
    def has_storage_state(self) -> bool:
        return False

    async def start_qr_login(self, qr_path: Any) -> Any:
        raise FeishuAdapterError("当前使用开放平台 API，无需扫码登录")

    async def wait_for_qr_login(self, timeout_ms: int | None = None) -> bool:
        raise FeishuAdapterError("当前使用开放平台 API，无需扫码登录")
