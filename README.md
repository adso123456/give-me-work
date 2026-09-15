# astrbot_plugin_job_agent

AstrBot 求职管理插件 V0.1.0：接收统一招聘事件，调用 LLM Agent 维护飞书多维表格，并向绑定的 QQ 会话发送通知。

## 安装

1. 将插件目录放入 AstrBot 的 `data/plugins/astrbot_plugin_job_agent`。
2. 安装 Python 依赖：

   ```bash
   pip install -r requirements.txt
   ```

3. 安装 Playwright Chromium：

   ```bash
   python -m playwright install chromium
   ```

Docker 部署时还需要确认 Chromium 的系统依赖和内存。插件不会自动修改 AstrBot Docker 镜像。

## 配置

配置 `feishu_table_url`、`webhook_token`，可选配置 `provider_id`、`card_mode` 和 Webhook 监听端口。

注意：V0.1 使用 Playwright 操作网页。服务器上的无头浏览器必须能访问并登录飞书表格；本地浏览器的登录状态不会自动传到服务器。插件不会打印完整飞书 URL 或 Webhook Token。

## 指令

- `/job_bind`：绑定当前 QQ 会话为主动通知目标。
- `/job_status`：查看插件、飞书、LLM、Webhook 和待处理卡片状态。
- `/job_table_test`：读取飞书表头和前 3 条记录，不修改表格。
- `/job_test_hr`：真实走 Mock 招聘事件、Agent、飞书和卡片发送链路。
- `/job_test_resume`：真实走简历请求链路。
- `/job_action <token> <action>`：处理纯文本卡片动作。
- `/job <自然语言>`：新增、修改或查询求职记录。

V0.1 不会登录或操作 BOSS，也不会删除飞书记录。
