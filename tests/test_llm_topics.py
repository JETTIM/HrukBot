from __future__ import annotations

from app.llm_topics import _clean_plain_text, _extract_message_text


def test_extract_message_text_prefers_content() -> None:
    message = {"content": "  готовый ответ  ", "reasoning_content": "внутренние рассуждения"}
    assert _extract_message_text(message) == "готовый ответ"


def test_extract_message_text_does_not_fallback_to_reasoning_content() -> None:
    message = {"content": "   ", "reasoning_content": "  ответ из reasoning  "}
    assert _extract_message_text(message) is None


def test_extract_message_text_returns_none_for_empty_payload() -> None:
    message = {"content": "", "reasoning_content": "   "}
    assert _extract_message_text(message) is None


def test_extract_message_text_keeps_non_empty_content_even_if_reasoning_like() -> None:
    message = {"content": "Thinking Process: сначала подумаю, потом отвечу"}
    assert _extract_message_text(message) == "Thinking Process: сначала подумаю, потом отвечу"


def test_extract_message_text_supports_structured_content_parts() -> None:
    message = {
        "content": [
            {"type": "text", "text": "первая строка"},
            {"type": "text", "text": "  вторая строка  "},
            {"type": "input_text", "text": "   "},
        ]
    }
    assert _extract_message_text(message) == "первая строка\nвторая строка"


def test_extract_message_text_falls_back_to_message_text_field() -> None:
    message = {"content": [], "text": "  текст из message.text  "}
    assert _extract_message_text(message) == "текст из message.text"


def test_clean_plain_text_extracts_best_draft_option() -> None:
    raw = (
        "Thinking Process:\n\n"
        "1. Analyze request\n"
        "Draft response options:\n"
        "* Сплю? Я нейросеть, сплю не сплю. (слишком формально)\n"
        "* Нет, я не сплю.\n"
        "* \"\n"
    )
    assert _clean_plain_text(raw, max_chars=900) == "Нет, я не сплю."
