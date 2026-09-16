import pytest


def _sample_card(actions):
    from cards.models import JobNotificationCard

    return JobNotificationCard(
        card_id="card_1",
        type="hr_reply",
        title="您收到一条 HR 回复",
        company="XX科技",
        position="AI应用开发工程师",
        contact="王女士",
        content="方便介绍一下你的 Agent 项目吗？",
        suggested_reply="可以的，我目前主要……",
        status_text="沟通中 · 待回复",
        actions=list(actions),
    )


def test_text_card_matches_image_card_action_format():
    try:
        from cards.text_renderer import TextCardRenderer
    except ModuleNotFoundError:
        pytest.fail("card renderer API is not implemented yet")

    text = TextCardRenderer().render(_sample_card(["replied", "later"]), "tok_123")

    assert "可以的，我目前主要" in text
    # 与图片卡片一致的编号交互
    assert "回复数字即可同步飞书" in text
    assert "1. 我已回复" in text
    assert "2. 稍后处理" in text
    # 命令式入口仍然保留
    assert "/job_action tok_123" in text


def test_text_card_renders_all_action_labels_in_order():
    from cards.text_renderer import TextCardRenderer

    text = TextCardRenderer().render(
        _sample_card(["resume_accepted", "resume_rejected", "later"]), "tok_9"
    )
    assert text.index("1. 我已同意") < text.index("2. 我未同意") < text.index("3. 稍后处理")


def test_napcat_renderer_has_text_fallback():
    try:
        from cards.napcat_renderer import NapCatCardRenderer
    except ModuleNotFoundError:
        pytest.fail("NapCatCardRenderer API is not implemented yet")

    card = _sample_card(["processed"])
    card.title = "系统通知"
    card.content = "测试完成"
    card.suggested_reply = None
    card.status_text = None

    rendered = NapCatCardRenderer().render(card, "tok_456")
    assert "测试完成" in rendered
    assert "1. 已处理" in rendered
    assert "/job_action tok_456" in rendered
    assert "**" not in rendered
