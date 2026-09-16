# astrbot_plugin_job_agent(求职管理 Agent)诊断报告

- 诊断时间:2026-09-16 09:3x CST
- 目标:服务器 `tencent-personal`(101.43.40.43)→ docker 容器 `astrbot`(AstrBot 4.27.4)
- 插件路径:`/opt/services/astrbot/astrbot-data/plugins/astrbot_plugin_job_agent`(v0.1.0, 2057 行, 12 个单测全过)
- 诊断方式:代码审读 + 容器内 Playwright **只读**探针(diag_feishu.py / diag_feishu2.py)+ 线上日志与状态文件

---

## 一、结论先行

**读写两端的实现选错了技术路线:插件的核心能力(维护飞书表格)在当前 UI 上结构性地做不到。**

飞书多维表格的表格网格是 **canvas 渲染**(实测页面上 `canvas[role="faster"]` = 1,而
`[role="row"] / [role="gridcell"] / [data-record-id] / [data-field-name] / [role="columnheader"]` **全部为 0**),
而插件所有写操作都依赖这些语义化 DOM 选择器。结果:

| 能力 | 现状 | 实测证据 |
|---|---|---|
| 读记录 | ✅ 能读(靠"全选复制到剪贴板"的 hack) | `_read_rows()` 返回 3 条,字段对齐 |
| 新增记录 | ❌ 永久失败 | `/job_table_write_test` → `阶段1 创建测试记录失败:未找到新增记录控件`(16:06 日志);`_click_add_record` 的 4 个选择器实测 count=0 |
| 修改记录 | ❌ 永久失败 | `_select_record("13")` → `未找到可编辑的记录行`;`_fill_fields` 依赖的 `[data-field-name]` count=0 |
| 二维码登录 | ❌ 必然超时 | `wait_for_qr_login()` 的等待上限 = `self.timeout_ms` = **20 秒**;日志 15:55:34 发起 → 15:56:03 失败(29s) |
| 接收招聘事件 | ❌ 未启用 | 配置里 `webhook_token` 为空 → `EventServer` 根本不启动 |
| 发送到 QQ | ⚠️ 未绑定 | `job_agent_state.json` 里 `bound_umo: null`,即使有事件也会返回"尚未绑定通知会话" |

也就是说:**在真机上跑通的只有"读",其它三条链路都是死的。** 这不是配置问题,是代码与页面结构不匹配。

---

## 二、致命问题(会静默写错数据或整条链路死掉)

### P0-1 写路径 100% 不可用(canvas 网格 + 语义选择器)
- `web_adapter.py:340-353` `_click_add_record()`:`[aria-label*="添加记录"]` / `[data-testid*="add-record"]` → 实测 **0**。
  页面上的"新增记录"是**纯图标按钮**,`aria-label`/`title`/`data-testid` 全为 null(实测 36 个 button 里只有"问问 AI"一个带 aria-label)。
- `web_adapter.py:355-361` `_select_record()`:靠 `[data-record-id="13"]`,该属性在页面上不存在。
- `web_adapter.py:363-379` `_fill_fields()`:靠 `[data-field-name="公司名称"] input`,同样是 0。
- 影响:`create_job` / `update_job` / `/job_table_write_test` / `/job_action` 全部不可能成功;Agent 的 4 个工具里 2 个是废的。

### P0-2 `record_id` 语义错误(读回来的 ID 不能用来改记录)
`_read_rows` 走剪贴板兜底后,`record_id` 取的是**用户可见的"投递记录ID"字段值(13/14/15 自增号)**,不是飞书内部 `recxxxxxx`。
即使将来能改单元格,用这个号也定位不到记录。而且 `get_record()` 也只在这个伪 ID 上做匹配。

### P0-3 表头解析是"碰巧对",换列即静默错位
`_headers_from_ssr_html()` (`web_adapter.py:407-423`) 的正则抓到的是整页 `"texts"` 数组,实测内容:

```
["应聘岗位","公司名称","投递日期","简历文件","投递状态","面试日期","备注",
 "创建人","创建时间","修改人","更新时间","投递记录ID","adso","2026/0","...","面试邀请","1","13",...]
```

它返回 `[投递记录ID] + 前 11 个元素` —— 之所以得到正确 12 列,**纯粹因为"投递记录ID"恰好排在该数组倒数第 11 位**。
一旦你在表格里加字段/调列序,这个数组的次序就变,返回的"表头"会混进 `adso`、`2026/0` 这类**行数据**,
而 `_read_rows` 是 `dict(zip(self.headers, values))` —— **静默错位,不报错,写错字段**。这是最危险的一处。

### P0-4 二维码登录必然超时
`feishu/login_flow.py:27` 调 `adapter.wait_for_qr_login()` 不传参 → `web_adapter.py:201` `wait_timeout = self.timeout_ms` = **20s**。
另外 `start_qr_login()` 只 `wait_for_timeout(min(5000, timeout_ms))` 就截图,二维码没渲染出来就会发一张没码的图。

### P0-5 事件来源实际上是空的
- `webhook_token` 为空 → `EventServer` 不启动(`main.py:129-139`),全项目**没有任何真实招聘事件来源**(BOSS 抓取 V0.1 明确不做),目前只有 `/job_test_hr` 这类 mock 命令。
- 另有隐患:`EventServer._handle` **先** `mark_event_processed` **再**异步 dispatch;dispatch 失败(没绑定会话、LLM 报错、飞书写失败)时事件已被标记,永久丢失且不会重试(`events/server.py:68-77`)。

---

## 三、重要问题(资源、状态、体验)

| # | 问题 | 证据 / 位置 |
|---|---|---|
| P1-1 | **数据目录放在 plugins/ 下,升级/重装插件即丢登录态** | `main.py:364-365` `parents[1]/plugin_data/...` = `data/plugins/plugin_data/astrbot_plugin_job_agent`;而旧版遗留的 `data/plugin_data/astrbot_plugin_job_agent/`(506KB 的 `feishu_storage_state.json` + debug 截图)**仍然存在**,两份并存,极易误判"明明登录过" |
| P1-2 | **内存压力**:每次 `open()` 拉一个 Chromium,并且常驻不关;`/job_status`、`/job_table_test` 都会触发 | 服务器 3.7G 内存,`astrbot` 容器已占 **2.51GiB**,swap 已用 1.15G;`check_access()`→`open()`(`main.py:160`);`close()` 只在 `terminate()` 调 |
| P1-3 | 事件 ID 列表无限增长,`pending_cards` 只加不清,已完成 token 可重复执行 | `state_store.py:55-62`(无上限)、`:64-90`;`main.py:233` 取 pending 卡片时**不校验 status** → 同一 token 连续点两次会重复写飞书 |
| P1-4 | 没有 LLM Provider 时"假成功" | `main.py:48-50` `_UnavailableLlm` 返回 `{"type":"final","summary":"未找到可用的 LLM Provider"}` → 用户看到"已完成",实际什么都没做 |
| P1-5 | 失败只进日志,用户无感知 | `main.py:297-299` 卡片发送失败、`_handle_webhook_event` 里 Agent 失败,均只 `logger.warning` |
| P1-6 | `open()` 读表头有 20s 竞态 | 15:57 的 `open.png` 只有 7.6KB(纯加载骨架,无任何文字)→ 直接报"未能读取飞书表头";`_wait_until_ready()` 只等 `domcontentloaded`,没有等网格真正渲染 |
| P1-7 | `简历文件` 是附件字段,`fill()` 填不了 | `web_adapter.py:363-379` 只会填 input/textarea;即使格子能定位,附件也不能这么填 |

---

## 四、次要问题

1. `card_mode=auto` 实际走 `NapCatCardRenderer`,给 QQ 发的是 `Plain("**标题**...")` —— **星号会原样显示**(`cards/napcat_renderer.py:19`);且该类的 try/except 是无意义的自欺。
2. `requirements.txt` 里的 `pydantic` 全项目未使用;依赖不锁版本;Playwright 浏览器安装没有启动时自检(README 只写在文档里)。
3. `search_records()` 用 `json.dumps(record)` 做子串匹配,数据量一大就退化,且会被无关字段命中。
4. `_save_debug()` 每次覆盖同名 `create.png`,没有时间戳,排障时拿到的是最后一次。
5. 单测 12 个全过,但**最脆的 `web_adapter`(剪贴板解析、表头解析)零覆盖** —— 测试全绿给了虚假的安全感。
6. `webhook_host` 默认 `0.0.0.0`,token 比较用 `==`(非 `hmac.compare_digest`);`/job_action` 需要用户手动复制 10 位 token,体验差(V0.1 无按钮回调可理解)。

---

## 五、复现命令(全部只读)

```bash
# 1. 单测
docker exec astrbot sh -c 'cd /AstrBot/data/plugins/astrbot_plugin_job_agent && python3 -m pytest tests -q'

# 2. 线上失败证据
docker logs astrbot 2>&1 | grep -aE "job_table_write_test|job_feishu_login|创建测试记录失败|扫码登录超时"

# 3. DOM 真相(只读探针,不点击、不写入)
docker cp diag_feishu2.py astrbot:/tmp/ && docker exec -w /tmp astrbot python3 diag_feishu2.py
```

探针实测输出要点:

```
[role="row"] 0   [role="gridcell"] 0   [data-record-id] 0   [data-field-name] 0   canvas[role="faster"] 1
HEADERS(12): 投递记录ID, 应聘岗位, 公司名称, 投递日期, 简历文件, 投递状态, 面试日期, 备注, 创建人, 创建时间, 修改人, 更新时间
_read_rows() rows=3            # 仅靠剪贴板兜底, record_id 是 13/14/15(伪 ID)
_select_record("13") → FAIL: 未找到可编辑的记录行
```

---

## 六、修复路线(需要你拍板选一条)

### 路线 A(推荐 · 彻底):飞书开放平台多维表格 API
用自建应用 `app_id/app_secret` + `bitable` 记录接口做增删改查,彻底删掉 Playwright 和 canvas 解析。
- 优点:稳定、快、可并发、不占内存、record_id 是真的、支持附件字段、字段名即表头。
- 前提:**要确认你的账号能不能创建自建应用**。你的表在 `my.feishu.cn`(飞书个人版),开放平台自建应用通常要求企业/团队租户 —— 这一步我无法替你确认,需要你登录 [open.feishu.cn](https://open.feishu.cn) 看能否"创建企业自建应用";能,就走 A。
- 改动范围:`feishu/web_adapter.py` → `feishu/api_adapter.py`(新写 ~250 行),`main.py` 去掉浏览器生命周期与二维码登录,`_conf_schema.json` 加 `app_id/app_secret/app_token/table_id`。

### 路线 B(保底 · 不依赖飞书授权):本地 SQLite 当唯一事实源
求职记录落在插件自己的 SQLite 里(结构化、秒级、零依赖),飞书只做**只读镜像**(保留现在能跑的剪贴板读取)或干脆不要。
- 优点:今天就能全部跑通,不受 UI/授权变化影响,查询/统计/去重都能做。
- 代价:飞书表格要你自己手动维护,或者接受"插件是主、飞书是备份"。

### 路线 C(不推荐 · 硬修浏览器自动化):把适配器改成"坐标操作"
按工具栏按钮位置点"新增记录"、按行高列宽点单元格、键盘 Tab 填值,读取继续用剪贴板。
- 优点:不改架构。
- 缺点:任何一次飞书前端发版都会失效;仍然拿不到真 record_id(改记录只能靠"投递记录ID"肉眼匹配);本机内存已经吃紧。**不建议投入。**

无论选哪条,下面这些**与路线无关的必修项**建议一起做:
1. 数据目录迁到 `data/plugin_data/`(用 Star 的 data 目录接口),删掉 `plugins/plugin_data` 与旧版遗留目录,只留一份 `feishu_storage_state.json`。
2. 事件幂等:dispatch 成功后再标记;`processed_event_ids` 加上限/TTL;`/job_action` 校验卡片状态,拒绝重复执行。
3. `_UnavailableLlm` 改为返回 error,禁止假成功;失败必须回一条 QQ 消息。
4. `/job_status` 不要开浏览器;Chromium 改成"用完即关",这台 4G 机器扛不住常驻。
5. `Plain` 里别放 Markdown 星号;`requirements.txt` 去掉 pydantic 并锁版本;给 `web_adapter` 的剪贴板/表头解析补单测。

---

## 七、附:本次诊断产生的文件

| 文件 | 说明 |
|---|---|
| `_job_agent_review/astrbot_plugin_job_agent/` | 服务器插件源码快照(只读审读用) |
| `_job_agent_review/diag_feishu.py` / `diag_feishu2.py` | 只读 DOM 探针(可重复执行) |
| `_job_agent_review/active_create.png` | 16:06 写入测试失败时的页面截图(表格真实样子) |
| `_job_agent_review/active_open.png` | 15:57 打开失败的截图(7.6KB 空白骨架 → 20s 竞态证据) |
| `_job_agent_review/active_feishu_login_qr.png` | 扫码页截图 |
| `_job_agent_review/legacy_browser/` | 被替换掉的浏览器方案（web_adapter.py / login_flow.py）留档 |
| `_job_agent_review/astrbot_plugin_job_agent/` | v0.2.0 插件源码（已部署到服务器） |

---

## 八、v0.2 修复记录(本次已实施并部署)

服务器已备份旧版本:`/home/ubuntu/backups/job_agent_v0.1_20260916_100751.tgz`。

| 原问题 | 修复 | 验证 |
|---|---|---|
| P0-1/2/3 浏览器抓 canvas 网格,增改记录 100% 失败、表头靠启发式 | 新增 `feishu/api_adapter.py`,改走飞书开放平台多维表格 API(`tenant_access_token`);表头取自 `/fields` 接口;`record_id` 是真实 `recXXX` | 真实链路实测:`新增成功 record_id=recvvltakNnU0v` → 读回 → `投递状态=沟通中` → 搜索命中 → 清理,全部 ✅ |
| P0-4 扫码登录 20s 必超时 | 删除浏览器登录流程;`/job_feishu_login` 改为提示 API 模式无需扫码 | 命令返回说明文案 |
| P0-5 事件先标记后处理,失败即丢失 | `events/server.py`:处理成功后才 `mark_event_processed`,失败保持可重试;新增 in-flight 去重 | `tests/test_events.py` 三个用例(成功/失败可重试/处理中) |
| P1-1 数据目录在 plugins/ 下会被升级删除,且新旧两份并存 | 改用 `StarTools.get_data_dir()` → `data/plugin_data/...`,并自动迁移旧状态文件 | 启动日志:`已迁移旧状态文件 /AstrBot/data/plugins/plugin_data/...`,新数据目录 `data=/AstrBot/data/plugin_data/astrbot_plugin_job_agent` |
| P1-2 常驻 Chromium,4G 机器内存吃紧 | API 模式零浏览器;`/job_status` 不再启动浏览器(改查 `/fields`) | 重启后无 Chromium 进程 |
| P1-3 事件 ID 无限增长 / 卡片状态不校验 | `processed_event_ids` 上限 1000 条环形裁剪;`/job_action` 拒绝已完成的卡片;新增 `prune_cards()` 清理过期卡片 | `tests/test_state_store.py` 新增 4 个用例 |
| P1-4 无 Provider 时假成功 | 删除 `_UnavailableLlm`;无 Provider 时直接返回错误;并改成**懒解析** Provider(插件加载早于 Provider 注册) | 启动日志提示,首次调用时重试解析 |
| P1-5 失败只进日志 | Agent 失败/卡片发送失败都会回发一条 QQ 消息 | `main._safe_send` |
| P2 Webhook 默认 0.0.0.0、token 明文比较 | 默认改 `127.0.0.1`;`hmac.compare_digest` 比较 | `_conf_schema.json` / `events/server.py` |
| P2 卡片带 `**` 星号 | `NapCatCardRenderer` 输出纯文本 | 代码审读 |
| P2 Agent 不知道表格有哪些字段,会编字段名 | 提示词注入真实字段名(`table_fields`)并标注只读字段 | `agent.py` / `prompts.py` / `feishu/tools.py` |
| P2 依赖冗余、web_adapter 无测试 | `requirements.txt` 只留 aiohttp;删除 playwright/pydantic;新增 19 个用例覆盖适配器 | **单测 12 → 31 全过** |
| 附件/只读字段静默出错 | 附件、关联、人员字段写入时忽略并返回 `warnings`;未知字段报错时列出可用字段名 | `tests/test_api_adapter.py` |

## 九、待你在 QQ 里验收

1. `/job_bind` — 绑定通知会话(状态文件里 `bound_umo` 一直是 null)。
2. `/job_status` — 预期:`飞书表格:可访问`、12 个字段、`LLM Provider:已配置`。
3. `/job_table_test` — 读取前 3 条记录。
4. `/job_table_write_test` — 四阶段真实写入自检(会在表里建一条 `JOB_AGENT_V1_TEST` 记录并改状态)。
5. `/job_test_hr` — 走完 LLM Agent + 飞书 + 卡片全链路。

有任何一条报错,把返回文案发我,我按错误码继续修。

---

## 十、v0.3.0:卡片交互(个人 QQ 的可行性结论)

**结论:个人 QQ + NapCat 发不出真正可点击的按钮卡片。** 证据来自本机 NapCat 包体:
`/app/napcat/napcat.mjs` 里存在 `botAppid: e.bot_appid` —— 按钮消息必须绑定官方机器人的 appid;
而当前 NapCat 的 OneBot 配置(`onebot11_<QQ号>.json`)只配了一条到 AstrBot 的反向 WebSocket,没有任何 bot 应用信息。
真按钮只能换 QQ 官方机器人平台(`qq_official` 适配器),需要另外申请机器人账号。

**已实现的替代方案**(用户确认采纳):

| 内容 | 形式 | 理由 |
|---|---|---|
| 标题 + 对方消息正文 | **图片卡片**(Pillow 本地渲染,760px 宽,无浏览器/无外部 t2i 服务) | 一眼看清"谁发来了什么",视觉上像卡片 |
| 公司/岗位/HR、建议回复、当前状态、动作编号 | **纯文本**(紧随图片) | 建议回复要能**复制**(图片里的字复制不了) |
| 交互 | 直接回复 `1` / `2` / `3` 执行;或 `/job_action <token> <动作>` | 只有存在待处理卡片时才拦截纯数字消息,其他消息不受影响 |

- 卡片样张:`card_preview_minimal.png`(实际发送的精简版 760×248)、`card_preview.png`(完整版,含建议回复与编号)。
- 单测:41 → **42 个全过**(新增卡片编号解析、图片渲染、字体/emoji 处理、`latest_pending_card`)。
- 静态检查:`pyflakes` 全绿(此前 `_ProviderLlmClient is not defined` 那类错误不会再漏过,并补了入口冒烟测试)。
