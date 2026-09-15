import pytest


def test_text_card_contains_suggested_reply_and_action_commands():
    try:
        from cards.models import JobNotificationCard
        from cards.text_renderer import TextCardRenderer
    except ModuleNotFoundError:
        pytest.fail("card renderer API is not implemented yet")

    card = JobNotificationCard(
        card_id="card_1",
        type="hr_reply",
        title="您收到一条 HR 回复",
        company="XX科技",
        position="AI应用开发工程师",
        contact="王女士",
        content="方便介绍一下你的 Agent 项目吗？",
        suggested_reply="可以的，我目前主要……",
        status_text="沟通中 · 待回复",
        actions=["replied", "later"],
    )

    text = TextCardRenderer().render(card, "tok_123")
    assert "可以的，我目前主要" in text
    assert "/job_action tok_123 replied" in text
    assert "/job_action tok_123 later" in text


def test_napcat_renderer_has_text_fallback():
    try:
        from cards.models import JobNotificationCard
        from cards.napcat_renderer import NapCatCardRenderer
    except ModuleNotFoundError:
        pytest.fail("NapCatCardRenderer API is not implemented yet")

    card = JobNotificationCard(
        card_id="card_2",
        type="system",
        title="系统通知",
        company=None,
        position=None,
        contact=None,
        content="测试完成",
        suggested_reply=None,
        status_text=None,
        actions=["processed"],
    )

    rendered = NapCatCardRenderer().render(card, "tok_456")
    assert "测试完成" in rendered
    assert "/job_action tok_456 processed" in rendered
