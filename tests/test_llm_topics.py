from __future__ import annotations

from app.llm_topics import _extract_message_text


def test_extract_message_text_prefers_content() -> None:
    message = {"content": "  готовый ответ  ", "reasoning_content": "внутренние рассуждения"}
    assert _extract_message_text(message) == "готовый ответ"


def test_extract_message_text_does_not_fallback_to_reasoning_content() -> None:
    message = {"content": "   ", "reasoning_content": "  ответ из reasoning  "}
    assert _extract_message_text(message) is None


def test_extract_message_text_returns_none_for_empty_payload() -> None:
    message = {"content": "", "reasoning_content": "   "}
    assert _extract_message_text(message) is None


def test_extract_message_text_rejects_thinking_process_prefix() -> None:
    message = {"content": "Thinking Process: сначала подумаю, потом отвечу"}
    assert _extract_message_text(message) is None
