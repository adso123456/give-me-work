import asyncio

import pytest


class FakeFeishuTools:
    def __init__(self):
        self.calls = []

    async def search_jobs(self, query):
        self.calls.append(("search_jobs", query))
        return {"records": [{"record_id": "rec_1", "fields": {"公司名称": "XX科技"}}]}

    async def get_job(self, record_id):
        self.calls.append(("get_job", record_id))
        return {"record_id": record_id, "fields": {"投递状态": "沟通中"}}

    async def create_job(self, fields):
        self.calls.append(("create_job", fields))
        return {"record_id": "rec_new", "fields": fields}

    async def update_job(self, record_id, fields):
        self.calls.append(("update_job", record_id, fields))
        return {"record_id": record_id, "fields": fields}


class SequenceLlm:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    async def complete(self, prompt, system_prompt):
        self.prompts.append((prompt, system_prompt))
        return self.outputs.pop(0)


def test_agent_event_runs_search_then_returns_notification_payload():
    try:
        from agent import JobAgentService
        from models import RecruitEvent
    except ModuleNotFoundError:
        pytest.fail("JobAgentService API is not implemented yet")

    llm = SequenceLlm(
        [
            '{"type":"tool_call","tool":"search_jobs","arguments":{"query":"XX科技 AI应用开发工程师"}}',
            '{"type":"final","summary":"发现对应岗位","suggested_reply":"可以介绍我的 Agent 项目。","card_type":"hr_reply"}',
        ]
    )
    tools = FakeFeishuTools()
    agent = JobAgentService(llm=llm, tools=tools)
    event = RecruitEvent.from_dict(
        {
            "event_id": "evt_1",
            "platform": "boss",
            "event_type": "hr_message",
            "content": "方便介绍一下你的 Agent 项目吗？",
            "occurred_at": "2026-09-15T12:00:00+08:00",
            "company": "XX科技",
            "position": "AI应用开发工程师",
        }
    )

    result = asyncio.run(agent.handle_event(event))
    assert result.summary == "发现对应岗位"
    assert result.suggested_reply == "可以介绍我的 Agent 项目。"
    assert result.record_id == "rec_1"
    assert tools.calls == [("search_jobs", "XX科技 AI应用开发工程师")]


def test_agent_card_action_gets_record_then_updates_status():
    try:
        from agent import JobAgentService
        from models import CardAction
    except ModuleNotFoundError:
        pytest.fail("JobAgentService action API is not implemented yet")

    llm = SequenceLlm(
        [
            '{"type":"tool_call","tool":"get_job","arguments":{"record_id":"rec_1"}}',
            '{"type":"tool_call","tool":"update_job","arguments":{"record_id":"rec_1","fields":{"简历状态":"已同意","待处理动作":"已处理"}}}',
            '{"type":"final","summary":"已同步飞书","suggested_reply":"","card_type":"system"}',
        ]
    )
    tools = FakeFeishuTools()
    agent = JobAgentService(llm=llm, tools=tools)

    result = asyncio.run(
        agent.handle_action(
            CardAction(
                action_id="a1",
                card_id="c1",
                event_id="e1",
                record_id="rec_1",
                action="resume_accepted",
            )
        )
    )
    assert result.summary == "已同步飞书"
    assert tools.calls[0] == ("get_job", "rec_1")
    assert tools.calls[1][0:2] == ("update_job", "rec_1")
