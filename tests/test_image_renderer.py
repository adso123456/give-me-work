from pathlib import Path

import pytest

from cards.image_renderer import CardImageRenderer
from cards.models import JobNotificationCard

pytest.importorskip("PIL", reason="需要 Pillow")

CARD = JobNotificationCard(
    card_id="card_test001",
    type="hr_reply",
    title="💬 您收到一条 HR 回复",
    company="XX科技有限公司",
    position="AI应用开发工程师",
    contact="HR小李",
    content="方便介绍一下你的 Agent 项目吗？我们这边主要做 RAG 和智能体编排，" * 3,
    suggested_reply="您好，我的 Agent 项目主要负责招聘事件接入与飞书多维表格同步，" * 4,
    status_text="已创建投递记录",
    actions=["replied", "later"],
)


def test_wrap_respects_max_width():
    renderer = CardImageRenderer()
    if not renderer.available():
        pytest.skip("环境缺少 Pillow 或中文字体")
    font = renderer._font(28)
    lines = renderer.wrap("中" * 100, font, 300)
    assert len(lines) > 1
    for line in lines:
        assert font.getlength(line) <= 300


def test_strip_emoji_removes_pictographs():
    assert CardImageRenderer.strip_emoji("💬 您收到一条 HR 回复 📌") == "您收到一条 HR 回复"


def test_render_creates_png_artifact(tmp_path: Path):
    renderer = CardImageRenderer()
    if not renderer.available():
        pytest.skip("环境缺少 Pillow 或中文字体")
    target = renderer.render(CARD, "tok1234567", tmp_path / "cards" / "card_test001.png")
    assert target.exists()
    assert target.stat().st_size > 1000
    with open(target, "rb") as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def test_minimal_render_only_keeps_title_and_content(tmp_path: Path):
    renderer = CardImageRenderer()
    if not renderer.available():
        pytest.skip("环境缺少 Pillow 或中文字体")
    from PIL import Image

    full = renderer.render(CARD, "tok", tmp_path / "full.png")
    minimal = renderer.render(CARD, "tok", tmp_path / "minimal.png", minimal=True)
    with Image.open(full) as first, Image.open(minimal) as second:
        assert second.width == first.width
        assert second.height < first.height
        assert second.height > 100


def test_render_grows_with_content(tmp_path: Path):
    renderer = CardImageRenderer()
    if not renderer.available():
        pytest.skip("环境缺少 Pillow 或中文字体")
    from PIL import Image

    small = renderer.render(CARD, "tok", tmp_path / "small.png")
    big_card = JobNotificationCard(
        card_id="card_big",
        type="resume_request",
        title="📄 HR 请求查看您的简历",
        company=CARD.company,
        position=CARD.position,
        contact=CARD.contact,
        content=CARD.content + CARD.content + CARD.content,
        suggested_reply=CARD.suggested_reply,
        status_text=CARD.status_text,
        actions=["resume_accepted", "resume_rejected", "later"],
    )
    big = renderer.render(big_card, "tok", tmp_path / "big.png")
    with Image.open(small) as first, Image.open(big) as second:
        assert second.height > first.height
        assert second.width == first.width
