from __future__ import annotations

from app.llm_topics import (
    _clean_plain_text,
    _describe_choice_shape,
    _extract_choice_text,
    _extract_message_text,
)


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


def test_extract_message_text_supports_content_dict() -> None:
    message = {"content": {"type": "text", "text": "  ответ из dict  "}}
    assert _extract_message_text(message) == "ответ из dict"


def test_extract_message_text_supports_list_with_output_text() -> None:
    message = {"content": [{"type": "output_text", "output_text": "  ответ из output_text  "}]}
    assert _extract_message_text(message) == "ответ из output_text"


def test_extract_message_text_falls_back_to_message_text_field() -> None:
    message = {"content": [], "text": "  текст из message.text  "}
    assert _extract_message_text(message) == "текст из message.text"


def test_extract_choice_text_supports_completion_text_field() -> None:
    choice = {"text": "  ответ из completion  "}
    assert _extract_choice_text(choice) == "ответ из completion"


def test_extract_choice_text_supports_delta_content() -> None:
    choice = {"delta": {"content": "  ответ из delta  "}}
    assert _extract_choice_text(choice) == "ответ из delta"


def test_extract_choice_text_prefers_message_content_when_reasoning_content_present() -> None:
    choice = {
        "message": {
            "role": "assistant",
            "content": "Я не сплю. Всегда готов помочь!",
            "reasoning_content": "Thinking Process: ...",
        }
    }
    assert _extract_choice_text(choice) == "Я не сплю. Всегда готов помочь!"


def test_extract_message_text_ignores_reasoning_content_when_content_is_empty() -> None:
    message = {
        "content": "   ",
        "reasoning_content": "Thinking Process:\nDraft response options:\n* первый\n* второй",
    }
    assert _extract_message_text(message) is None


def test_describe_choice_shape_includes_useful_keys() -> None:
    choice = {
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": "ok", "reasoning_content": "think"},
    }
    description = _describe_choice_shape(choice)
    assert "choice_keys=[finish_reason,message]" in description
    assert "finish_reason='stop'" in description
    assert "message_keys=[content,reasoning_content,role]" in description


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
