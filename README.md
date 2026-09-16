# astrbot_plugin_job_agent

AstrBot 求职管理插件 **V0.5.0**：接收统一招聘事件，调用 LLM Agent 维护飞书多维表格，并向绑定的 QQ 会话发送通知卡片。

V0.2 把存储层从「Playwright 抓飞书网页」换成了**飞书开放平台多维表格 API**：
飞书前端把表格网格渲染在 canvas 上，语义化 DOM 选择器（`[role="row"]`、`[data-record-id]`、`[data-field-name]` …）
在真实页面上**全部为 0**，导致新增/修改记录 100% 失败、且读回来的"记录 ID"其实是自增字段值。
换成 API 后 `record_id` 是飞书真实记录 ID，字段名即表头，也不再需要常驻 Chromium。

V0.3 增加**图片卡片 + 回复数字交互**：个人 QQ + NapCat 无法发送可点击按钮（按钮消息要绑定官方机器人的
`bot_appid`，NapCat 源码里也能看到 `botAppid: e.bot_appid`），因此卡片由 Pillow 本地渲染成图片，
动作以「1 / 2 / 3」编号印在卡片上，直接回复数字即可同步飞书。

V0.4 增加**删除指令**：`/job_delete` 默认只预览并要求二次确认（`/job_delete_confirm <确认码>`，5 分钟有效），
加 `--yes` 才直接删除。删除**只走指令，不暴露给 LLM Agent**——`delete_job` 不在 Agent 的可用工具里，
提示词里也仍然写着"不删除投递记录"，模型无法自主删表。

V0.4.1 按代码评审修了五处：卡片状态清理接进主流程（并给待处理卡片加上过期与总量上限）、
文本卡片的动作格式与图片卡片对齐、`card_mode` 补上 `image` 选项、
搜索改走飞书 `records/search` 服务端过滤（失败自动回退本地匹配）、删掉两份用不到的网页登录态。

V0.5.0 接入 **BOSS 直聘聊天流水**（只推通知、不落表）：BOSS 没有面向求职者的开放接口，
所以用浏览器内旁听的方案——油猴脚本挂在沟通页劫持页面自己的 XHR/fetch/WebSocket，
把消息 POST 到本插件 webhook。详见 [`userscript/boss-chat-bridge.user.js`](../userscript/boss-chat-bridge.user.js)。

## 安装

1. 将插件目录放入 AstrBot 的 `data/plugins/astrbot_plugin_job_agent`。
2. 安装 Python 依赖：

   ```bash
   pip install -r requirements.txt
   ```

   只依赖 `aiohttp`（AstrBot 本身已带）；不再需要 Playwright / Chromium。

## 飞书应用准备

1. 到 [open.feishu.cn/app](https://open.feishu.cn/app) 创建**企业自建应用**，拿到 `App ID` / `App Secret`。
2. 「权限管理」添加 **`bitable:app`**（查看、评论、编辑和管理多维表格）。
3. 「版本管理与发布」创建版本并发布。
4. 打开你的多维表格 → 「添加应用 / 添加协作者」把该应用加进来，给**可编辑**权限。

四步缺任何一步，API 都会返回 `403 / 91403 Forbidden`。

## 配置

| 配置项 | 说明 |
| --- | --- |
| `app_id` / `app_secret` | 自建应用凭证（必填，不会写入日志） |
| `feishu_table_url` | 多维表格 URL，用于自动解析 `app_token` / `table_id` |
| `app_token` / `table_id` | 可选，URL 解析不出来时手填 |
| `provider_id` | 可选，指定 AstrBot 的 LLM Provider，留空用默认 |
| `webhook_host` / `webhook_port` | 事件接收地址，默认 **127.0.0.1:6190**（只监听本机） |
| `webhook_token` | Bearer Token；为空则完全不启动 Webhook |
| `card_mode` | `auto`(默认,优先图片卡片,失败回退纯文本) / `image` / `napcat` / `text` |
| `delete_confirm_required` | 删除是否需要二次确认,默认 `true`;关闭后 `/job_delete` 直接删除 |
| `chat_notify_window_seconds` | 聊天流水的合并推送窗口,默认 60 秒(同一会话内的多条合并成一条);填 0 表示每条立刻推 |

数据目录由 AstrBot 的 `StarTools.get_data_dir()` 提供，即 `data/plugin_data/astrbot_plugin_job_agent/`；
旧版写在 `data/plugins/plugin_data/` 下的 `job_agent_state.json` 会自动迁移一次（那个位置在插件升级时会被 AstrBot 删除）。

## 卡片交互

收到招聘事件后,插件会发两条内容:

1. **图片卡片**:只含「标题 + 对方消息正文」,用于一眼看清谁发来了什么;
2. **紧随其后的纯文本**:公司 / 岗位 / HR / 建议回复 / 当前状态 / 动作编号 —— 建议回复放在文字里,
   方便直接复制粘贴发出去:

```
字节跳动  ·  AI 应用开发工程师
HR：HR小李

🤖 建议回复（复制即用，未替你发送）：
“您好，感谢关注！简历我这边马上发您……”

当前：已创建投递记录（record_id: recvxxxx），投递状态保持「已投递」

回复数字即可同步飞书：
1. 我已同意
2. 我未同意
3. 稍后处理
（也可发送 /job_action <token> <动作>）
```

- 直接回复 `1` / `2` / `3` 即可执行(**只有存在待处理卡片时才会拦截纯数字消息**,其他消息不受影响);
- 也可以发送 `/job_action <token> <动作>`;
- 已处理过的卡片会拒绝重复执行;
- 图片卡片依赖 `Pillow` 与系统中文字体(容器内为 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`);
  缺少时自动回退为纯文本卡片,功能不受影响;
- 卡片图片保存在 `data/plugin_data/astrbot_plugin_job_agent/cards/`,自动只保留最近 50 张。

## 指令

- `/job_bind`：绑定当前 QQ 会话为主动通知目标。
- `/job_status`：查看飞书 API、字段列表、LLM、Webhook、待处理卡片状态。
- `/job_table_test`：读取飞书前 3 条记录，不修改表格。
- `/job_table_write_test`：真实走「新增 → 读取 → 修改 → 读取」四阶段写入自检。
- `/job_test_hr`：真实走 Mock 招聘事件、Agent、飞书和卡片发送链路。
- `/job_test_resume`：真实走简历请求链路。
- `/job_chat_test`：模拟一条 BOSS 聊天流水（只推通知、不写表）。
- `/job_action <token> <action>`：处理指定卡片动作（也可直接回复数字）。
- `/job <自然语言>`：新增、修改或查询求职记录。

### 删除记录

```
/job_delete_find 字节跳动          # 搜索，列出 record_id（最多 5 条）
/job_delete recvvlJJBnsi1P         # 预览这条记录，给出确认码
/job_delete_confirm k7Rf2qZp       # 确认删除（5 分钟内有效）
/job_delete rec1 rec2 rec3         # 一次预览多条，确认码确认后批量删除
/job_delete recvvlJJBnsi1P --yes   # 跳过确认，直接删除（不可撤销）
```

- 删除走飞书 `batch_delete` 接口，插件侧没有任何回滚手段，请谨慎；
- 确认码一次性使用，用过或过期都会提示"已执行/已失效"；
- `delete_confirm_required=false` 时 `/job_delete` 直接删除；
- LLM Agent 没有删除工具：`/job` 的自然语言指令只会新增/修改/查询，不会删记录。

## 事件接入

`POST http://<host>:6190/job-agent/events`，请求头 `Authorization: Bearer <webhook_token>`，请求体：

```json
{
  "schema_version": "1.0",
  "event_id": "evt_20260916_001",
  "platform": "boss",
  "event_type": "hr_message",
  "conversation_id": "boss_1",
  "company": "XX科技",
  "position": "AI应用开发工程师",
  "contact": "HR小李",
  "content": "方便介绍一下你的 Agent 项目吗？",
  "occurred_at": "2026-09-16T10:00:00+08:00"
}
```

`event_type` 取值：`hr_message`、`resume_request`、`interview_invitation`、`system_notice`、`chat_message`。

- `hr_message` / `resume_request` / `interview_invitation` / `system_notice`：走完整链路（LLM → 维护飞书 → 发通知卡片）；
- `chat_message`：**只推 QQ 通知**，不走 LLM、不写飞书表格；同一会话在 `chat_notify_window_seconds`
  窗口内的多条消息会合并成一条，适合 HR 聊天流水这种高频琐碎的内容。示例：

```json
{
  "schema_version": "1.0",
  "event_id": "boss_chat_9f2k1_1m3",
  "platform": "boss",
  "event_type": "chat_message",
  "conversation_id": "boss_c_88",
  "company": "字节跳动",
  "position": "AI应用开发工程师",
  "contact": "HR小李",
  "content": "方便发一份简历吗？",
  "occurred_at": "2026-09-16T16:30:00+08:00"
}
```

处理语义：**只有 Agent 与飞书写入都成功，事件才会被标记为已处理**；失败时返回 202 但不记录，
发送方可以安全重试（重复投递返回 `duplicate`，处理中重复投递返回 `in_progress`）。

## BOSS 直聘接入(可选)

BOSS 没有面向求职者的开放接口(`open.zhipin.com` 不存在),社区做法都是复用你自己浏览器的登录态。
本插件采用**浏览器内旁听**:

```
你的 PC(浏览器已登录 BOSS)
  └─ 油猴脚本,挂在 https://www.zhipin.com/web/geek/chat
       ├─ 劫持页面自己的 XHR / fetch / WebSocket(不改请求、不额外发请求)
       └─ 把聊天消息 POST 到插件 webhook(event_type=chat_message)
                    ▼
服务器 astrbot ─ job_agent 插件 ─ 只推 QQ 通知(合并窗口内合并,不写飞书)
```

为什么这么做:登录态、`__zp_stoken__`、滑块全部留在浏览器里;不产生额外请求,风控特征与真人一致;
服务器不需要常开 Chromium(BOSS 侧风控比飞书严得多,服务端无头登录风险高)。

步骤:

1. 浏览器装 Tampermonkey,导入 [`userscript/boss-chat-bridge.user.js`](../userscript/boss-chat-bridge.user.js);
2. 打开 <https://www.zhipin.com/web/geek/chat>;
3. 右下角面板填 webhook 地址与 `webhook_token`;
4. 先保持**调试模式**开着,在页面上点几个会话,看面板里"采集"是否增长;
5. 确认有采集后取消调试模式,开始转发。

网络打通二选一:

- **Tailscale(推荐)**:服务器与你的 PC 加入同一 tailnet,脚本填 `http://<服务器 tailscale IP>:6190/job-agent/events`;
- **SSH 隧道**:`ssh -N -L 6190:127.0.0.1:6190 tencent-personal`,脚本填 `http://127.0.0.1:6190/job-agent/events`
  (不暴露任何端口,适合临时用)。

风险提示:BOSS 用户协议禁止自动化访问。本方案只在你自己浏览器里旁听你自己账号的数据、只读且低频,
风险相对最低;仍**不建议**用它做自动打招呼/批量投递——社区项目 README 里明确写了有封号、降权风险。

## 限制

- 不支持由 Agent 写入附件（如 `简历文件`）与关联/人员字段：这些字段会被忽略并在返回值里给出 `warnings`。
- `投递记录ID`、`创建人`、`创建时间`、`修改人`、`更新时间` 是系统字段，写入时自动忽略。
- 不登录或操作 BOSS；删除只能通过 `/job_delete*` 指令，LLM Agent 不具备删除能力。
