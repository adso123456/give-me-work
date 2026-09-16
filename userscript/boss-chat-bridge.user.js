// ==UserScript==
// @name         BOSS 直聘聊天 → 求职助手桥
// @namespace    https://github.com/adso123456/give-me-work
// @version      0.1.0
// @description  旁听 BOSS 沟通页自己发出的请求，把聊天消息转发到 astrbot_plugin_job_agent 的 webhook（只读、不额外发请求）
// @author       adso
// @match        https://www.zhipin.com/web/geek/chat*
// @run-at       document-start
// @grant        GM_xmlhttpRequest
// @connect      *
// ==/UserScript==

/*
 * 用法
 * 1) 装好 Tampermonkey，导入本脚本，打开 BOSS 沟通页 https://www.zhipin.com/web/geek/chat
 * 2) 右下角面板里填 webhook 地址与 token（存在 localStorage，只在你本机）
 * 3) 先点「调试模式」= 开：只采集不发送，看面板里的「采集」计数与接口列表
 * 4) 确认有采集后点「试发一条」，再切到「转发模式」= 开
 *
 * 说明
 * - 只 hook 页面自己的 XHR / fetch / WebSocket，不会额外产生请求，风控特征与真人一致；
 * - 采集内容按接口过滤，最多保留 50 条、单条 16KB，避免内存膨胀；
 * - 消息去重按 (会话 + 正文 + 时间)。
 */

(function () {
    'use strict';

    const CONFIG_KEY = 'job_agent_boss_bridge_config';
    const CHAT_URL_HINTS = ['/wapi/zpchat/', '/wapi/zprelation/friend/', '/web/geek/chat'];
    const MAX_CAPTURES = 50;
    const MAX_BODY = 16 * 1024;

    const config = Object.assign(
        {
            webhook: '',
            token: '',
            debugOnly: true,
            enabled: true,
        },
        JSON.parse(localStorage.getItem(CONFIG_KEY) || '{}')
    );

    const state = {
        captures: [],
        sent: 0,
        seen: new Set(),
        lastError: '',
    };

    function saveConfig() {
        localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
    }

    function looksLikeChatUrl(url) {
        return CHAT_URL_HINTS.some((hint) => String(url).includes(hint));
    }

    // ---------------------------------------------------------------- 消息提取

    /** 在任意 JSON 里找「像聊天消息」的对象。兼容多种字段命名，宁可少发也不乱发。 */
    function extractMessages(payload) {
        const found = [];
        const textKeys = ['text', 'body', 'content', 'message', 'msg', 'lastMsg'];
        const timeKeys = ['time', 'timestamp', 'msgTime', 'sendTime', 'createTime'];
        const whoKeys = ['name', 'bossName', 'geekName', 'fromName', 'senderName', 'friendName'];
        const convKeys = ['conversationId', 'friendId', 'encryptBossId', 'encryptGeekId', 'uid', 'bossId', 'id'];

        const seenObjects = new Set();

        function walk(node, depth) {
            if (!node || depth > 8 || typeof node !== 'object') return;
            if (seenObjects.has(node)) return;
            seenObjects.add(node);

            if (Array.isArray(node)) {
                node.forEach((item) => walk(item, depth + 1));
                return;
            }

            const textKey = textKeys.find((key) => typeof node[key] === 'string' && node[key].trim());
            if (textKey) {
                const item = { content: String(node[textKey]).trim() };
                const timeKey = timeKeys.find((key) => node[key] !== undefined && node[key] !== null);
                if (timeKey) item.rawTime = node[timeKey];
                const whoKey = whoKeys.find((key) => typeof node[key] === 'string' && node[key].trim());
                if (whoKey) item.contact = node[whoKey];
                const convKey = convKeys.find((key) => node[key] !== undefined && node[key] !== null);
                if (convKey) item.conversationId = String(node[convKey]);
                if (typeof node.company === 'string') item.company = node.company;
                if (typeof node.brandName === 'string') item.company = item.company || node.brandName;
                if (typeof node.jobName === 'string') item.position = node.jobName;
                if (typeof node.positionName === 'string') item.position = item.position || node.positionName;
                if (typeof node.fromSelf === 'boolean') item.fromSelf = node.fromSelf;
                if (textKey === 'lastMsg' || textKey === 'message') item.fromList = true;
                found.push(item);
            }

            Object.keys(node).forEach((key) => walk(node[key], depth + 1));
        }

        walk(payload, 0);
        return found;
    }

    function normalizeTime(rawTime) {
        if (rawTime === undefined || rawTime === null || rawTime === '') return new Date().toISOString();
        if (typeof rawTime === 'number') {
            const ms = rawTime > 1e12 ? rawTime : rawTime * 1000;
            return new Date(ms).toISOString();
        }
        const parsed = new Date(rawTime);
        return Number.isNaN(parsed.getTime()) ? new Date().toISOString() : parsed.toISOString();
    }

    function dedupeKey(item) {
        return [item.conversationId || '', item.content, item.rawTime || ''].join('|');
    }

    // ---------------------------------------------------------------- 转发

    function toEvent(item, url) {
        const now = new Date();
        return {
            schema_version: '1.0',
            event_id: 'boss_chat_' + Math.abs(hash(dedupeKey(item) + url)).toString(36) + '_' + now.getTime().toString(36),
            platform: 'boss',
            event_type: 'chat_message',
            conversation_id: item.conversationId || null,
            company: item.company || null,
            position: item.position || null,
            contact: item.contact || null,
            content: item.content,
            occurred_at: normalizeTime(item.rawTime),
            raw: { source_url: url, from_self: item.fromSelf === true },
        };
    }

    function hash(text) {
        let value = 0;
        for (let index = 0; index < text.length; index += 1) {
            value = (value << 5) - value + text.charCodeAt(index);
            value |= 0;
        }
        return value;
    }

    function postEvent(payload, label) {
        if (!config.webhook || !config.token) {
            state.lastError = '未配置 webhook 地址或 token';
            render();
            return;
        }
        const headers = {
            'Content-Type': 'application/json',
            Authorization: 'Bearer ' + config.token,
        };
        if (typeof GM_xmlhttpRequest === 'function') {
            GM_xmlhttpRequest({
                method: 'POST',
                url: config.webhook,
                headers,
                data: JSON.stringify(payload),
                onload: (response) => {
                    if (response.status >= 200 && response.status < 300) {
                        state.sent += 1;
                    } else {
                        state.lastError = 'HTTP ' + response.status + ': ' + String(response.responseText || '').slice(0, 120);
                    }
                    render();
                },
                onerror: () => {
                    state.lastError = '网络错误（webhook 不可达？）';
                    render();
                },
            });
        } else {
            fetch(config.webhook, { method: 'POST', headers, body: JSON.stringify(payload) })
                .then((response) => {
                    if (response.ok) state.sent += 1;
                    else state.lastError = 'HTTP ' + response.status;
                    render();
                })
                .catch((error) => {
                    state.lastError = String(error).slice(0, 120);
                    render();
                });
        }
        if (label) console.log('[job-agent-boss] 已转发', label, payload);
    }

    function handlePayload(url, bodyText) {
        if (!config.enabled || !bodyText) return;
        let json;
        try {
            json = JSON.parse(bodyText);
        } catch (error) {
            return;
        }
        const items = extractMessages(json);
        if (!items.length) return;

        state.captures.push({
            url,
            time: new Date().toISOString(),
            bytes: bodyText.length,
            items,
        });
        if (state.captures.length > MAX_CAPTURES) state.captures.shift();

        items.forEach((item) => {
            const key = dedupeKey(item);
            if (state.seen.has(key)) return;
            state.seen.add(key);
            if (state.seen.size > 2000) state.seen = new Set();
            if (!config.debugOnly) postEvent(toEvent(item, url), item.content);
        });
        render();
    }

    // ---------------------------------------------------------------- Hook

    function truncate(text) {
        return typeof text === 'string' && text.length > MAX_BODY ? text.slice(0, MAX_BODY) : text;
    }

    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
        this.__jobAgentUrl = url;
        return originalOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
        this.addEventListener('load', () => {
            try {
                const url = String(this.__jobAgentUrl || this.responseURL || '');
                if (looksLikeChatUrl(url)) handlePayload(url, truncate(this.responseText));
            } catch (error) {
                console.warn('[job-agent-boss] xhr hook 失败', error);
            }
        });
        return originalSend.apply(this, arguments);
    };

    const originalFetch = window.fetch;
    window.fetch = function () {
        const promise = originalFetch.apply(this, arguments);
        try {
            const url = String((arguments[0] && arguments[0].url) || arguments[0] || '');
            if (looksLikeChatUrl(url)) {
                promise
                    .then((response) => {
                        response
                            .clone()
                            .text()
                            .then((text) => handlePayload(url, truncate(text)))
                            .catch(() => {});
                    })
                    .catch(() => {});
            }
        } catch (error) {
            console.warn('[job-agent-boss] fetch hook 失败', error);
        }
        return promise;
    };

    const OriginalWebSocket = window.WebSocket;
    window.WebSocket = function (url) {
        const socket = new OriginalWebSocket(url);
        if (looksLikeChatUrl(url)) {
            socket.addEventListener('message', (event) => {
                if (typeof event.data === 'string') handlePayload(String(url), truncate(event.data));
            });
        }
        return socket;
    };
    window.WebSocket.prototype = OriginalWebSocket.prototype;

    // ---------------------------------------------------------------- 面板

    let panel;
    let body;

    function render() {
        if (!body) return;
        const endpoints = {};
        state.captures.forEach((capture) => {
            const path = capture.url.split('?')[0].replace(/^https?:\/\/[^/]+/, '');
            endpoints[path] = (endpoints[path] || 0) + 1;
        });
        const lines = Object.keys(endpoints).map((path) => `${endpoints[path]} × ${path}`);
        body.innerHTML = `
            <div>采集：<b>${state.captures.length}</b> 条响应 · 已转发：<b>${state.sent}</b> 条</div>
            <div style="margin:4px 0;color:#888">${lines.join('<br>') || '（还没有采集到聊天接口，去页面上点几个会话）'}</div>
            ${state.lastError ? `<div style="color:#d33">${state.lastError}</div>` : ''}
        `;
    }

    function buildPanel() {
        panel = document.createElement('div');
        panel.style.cssText =
            'position:fixed;right:12px;bottom:12px;z-index:999999;width:300px;background:#fff;' +
            'border:1px solid #d0d5dd;border-radius:10px;box-shadow:0 6px 20px rgba(0,0,0,.15);' +
            'font:12px/1.6 -apple-system,Microsoft YaHei,sans-serif;color:#222;padding:10px';
        panel.innerHTML = `
            <div style="font-weight:700;margin-bottom:6px">💼 求职助手桥（只读旁听）</div>
            <label style="display:block">Webhook
                <input id="ja-webhook" placeholder="http://<tailscale-ip>:6190/job-agent/events"
                       style="width:100%;box-sizing:border-box;margin:2px 0 6px">
            </label>
            <label style="display:block">Token
                <input id="ja-token" placeholder="webhook_token" style="width:100%;box-sizing:border-box;margin:2px 0 6px">
            </label>
            <label style="display:block;margin-bottom:6px">
                <input id="ja-debug" type="checkbox"> 调试模式（只采集，不发送）
            </label>
            <div id="ja-body"></div>
            <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">
                <button id="ja-save">保存</button>
                <button id="ja-test">试发一条</button>
                <button id="ja-copy">复制采集</button>
                <button id="ja-clear">清空</button>
            </div>
        `;
        document.body.appendChild(panel);

        const webhookInput = panel.querySelector('#ja-webhook');
        const tokenInput = panel.querySelector('#ja-token');
        const debugInput = panel.querySelector('#ja-debug');
        webhookInput.value = config.webhook;
        tokenInput.value = config.token;
        debugInput.checked = config.debugOnly;
        body = panel.querySelector('#ja-body');

        panel.querySelector('#ja-save').onclick = () => {
            config.webhook = webhookInput.value.trim();
            config.token = tokenInput.value.trim();
            config.debugOnly = debugInput.checked;
            saveConfig();
            state.lastError = '已保存';
            render();
        };
        panel.querySelector('#ja-test').onclick = () => {
            postEvent(
                toEvent(
                    {
                        content: '这是一条来自浏览器桥的自检消息',
                        contact: '自检',
                        conversationId: 'selftest',
                        company: '自检',
                        position: '自检',
                        rawTime: Date.now(),
                    },
                    'selftest'
                ),
                'selftest'
            );
        };
        panel.querySelector('#ja-copy').onclick = async () => {
            const text = JSON.stringify(state.captures, null, 2);
            try {
                await navigator.clipboard.writeText(text);
                state.lastError = '已复制 ' + state.captures.length + ' 条采集到剪贴板';
            } catch (error) {
                state.lastError = '复制失败，请打开控制台执行 copy(JSON.stringify(window.__jobAgentCaptures))';
            }
            render();
        };
        panel.querySelector('#ja-clear').onclick = () => {
            state.captures = [];
            state.seen = new Set();
            state.lastError = '';
            render();
        };
        render();
    }

    window.__jobAgentCaptures = state.captures;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', buildPanel);
    } else {
        buildPanel();
    }

    console.log('[job-agent-boss] 桥脚本已加载（调试模式：' + config.debugOnly + '）');
})();
