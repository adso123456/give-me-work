# Changelog

## 0.4.0 (2026-09-16)

**新增删除能力(仅指令,不交给 LLM)**

- `feishu/api_adapter.py` 新增 `delete_record()`(单条,DELETE 接口)与
  `delete_records()`(批量,`records/batch_delete`),记录不存在(1254303)时返回 `False` 而不是抛错。
- 新指令:
  - `/job_delete_find <关键词>`:搜索并列出 `record_id`(最多 5 条),直接给出可复制的删除命令;
  - `/job_delete <记录ID> [更多ID…]`:默认**只预览**并生成确认码;
  - `/job_delete_confirm <确认码>`:确认后才真正删除(确认码 5 分钟有效、一次性);
  - `/job_delete <记录ID> --yes`:跳过确认直接删除。
- 新增配置 `delete_confirm_required`(默认 `true`),关闭后 `/job_delete` 直接删除。
- 待确认删除单独存放在 `pending_deletes`,与卡片动作互不干扰,并在每次发起删除时清理过期项。
- **LLM Agent 依然拿不到删除工具**:`FeishuTools` 与 `JobAgentService.ALLOWED_TOOLS` 保持原有的
  search/get/create/update 四项,提示词里的"不删除投递记录"继续有效,模型无法自主删表。
- 新增 `commands.py` 统一解析指令参数(兼容 `/`、`!`、无唤醒前缀三种写法),
  并保证 `/job_delete_confirm` 不会被误判成 `/job_delete`。
- 测试 42 → 58:`test_commands.py`、适配器删除用例、状态存储待删除用例、入口冒烟扩展。

## 0.3.0 (2026-09-16)

**卡片交互(适配个人 QQ 的能力边界)**

- 结论:**个人 QQ + NapCat 无法发送可点击按钮**——按钮消息必须绑定官方机器人的 `bot_appid`
  (NapCat 包内可见 `botAppid: e.bot_appid`),这是平台限制,不是插件写法问题。
- 卡片拆成两条消息:
  1. **图片卡片**:只放「标题 + 对方消息正文」,由 Pillow 本地渲染(不依赖浏览器,也不需要外部 t2i 服务);
  2. **纯文本**:公司 / 岗位 / HR / 建议回复 / 当前状态 / 动作编号 —— 建议回复放在文字里,可以直接复制。
- 交互:直接回复 `1` / `2` / `3` 执行动作(**仅在存在待处理卡片时才拦截纯数字消息**,其他消息不受影响);
  `/job_action <token> <动作>` 仍然可用,已完成的卡片拒绝重复执行。
- `card_mode` 支持 `auto` / `image` / `napcat` / `text`;缺少 Pillow 或中文字体时自动回退纯文本卡片。
- 卡片图片存放在 `data/plugin_data/astrbot_plugin_job_agent/cards/`,自动只保留最近 50 张。

## 0.2.0 (2026-09-16)

**破坏性重写:存储层从「Playwright 抓飞书网页」改为飞书开放平台多维表格 API**

原因(实测):飞书多维表格的网格是 **canvas** 渲染,`[role="row"]` / `[role="gridcell"]` /
`[data-record-id]` / `[data-field-name]` 在真实页面上**全部为 0** —— 新增/修改记录 100% 失败;
读回来的 `record_id` 其实是被用户看到的自增字段值;表头靠正则从页面 `"texts"` 数组里猜,
一旦加字段就会把行数据混进表头,而记录组装是 `dict(zip(headers, values))`,**会静默写错列**。

- 新增 `feishu/api_adapter.py`:`tenant_access_token` + 多维表格 v1 记录接口(list / get / create / update),
  带分页、token 失效重试、字段类型转换(日期 → 毫秒时间戳、数字、多选、复选框),
  以及只读字段(自动编号/创建人/时间)和不支持字段(附件/关联/人员)的 `warnings`。
- 表头改为取自 `/fields` 接口,`record_id` 为飞书真实 `recXXX`。
- 删除 `feishu/web_adapter.py`、`feishu/login_flow.py` 与二维码登录实现;
  `/job_feishu_login` 改为提示 API 模式无需扫码;`requirements.txt` 只保留 `aiohttp`(去掉 playwright / pydantic)。
- 数据目录改用 `StarTools.get_data_dir()`,即 `data/plugin_data/<plugin>`(原来的 `plugins/plugin_data/`
  会在插件升级时被 AstrBot 删除);旧状态文件自动迁移一次。
- 事件幂等:只有 `on_event` 成功返回才标记已处理,失败的事件保持可重试;`processed_event_ids` 上限 1000 条;
  新增 in-flight 去重。
- 无 LLM Provider 时不再"假成功",直接返回错误;Provider 改为**懒解析**(插件加载可能早于 Provider 注册);
  Agent 失败 / 卡片发送失败都会回执一条 QQ 消息。
- Webhook 默认只监听 `127.0.0.1`;Bearer Token 比较改用 `hmac.compare_digest`。
- 提示词注入表格真实字段名,避免模型编造字段;`NapCatCardRenderer` 输出纯文本(不再出现 `**`)。
- 测试 12 → 42:新增适配器、入口冒烟(`_ProviderLlmClient is not defined` 这类错误不会再漏过)、
  卡片编号解析、图片渲染、状态存储等用例;CI 前可先跑 `pyflakes`。
