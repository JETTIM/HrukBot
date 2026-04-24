from __future__ import annotations

from bot import (
    _asks_for_photo_upload,
    _chat_fallback_answer,
    _finalize_chat_answer,
    _format_reply_visual_status,
    _looks_like_meta_llm_line,
    _looks_like_rewrite_refusal,
    _needs_rewrite_or_retry,
    _sanitize_chat_answer,
)


def test_sanitize_chat_answer_removes_input_message_prefix() -> None:
    raw = '**Input Message:** "Дай денег" (Give money).'
    assert _sanitize_chat_answer(raw) == '"Дай денег" (Give money).'


def test_sanitize_chat_answer_drops_attempt_line() -> None:
    raw = "*Attempt\nНормальный ответ"
    assert _sanitize_chat_answer(raw) == "Нормальный ответ"


def test_looks_like_rewrite_refusal_detects_common_refusal() -> None:
    text = (
        "Извините, но я не могу переписать ответ другой модели, "
        "так как не имею доступа к исходным данным."
    )
    assert _looks_like_rewrite_refusal(text) is True


def test_sanitize_chat_answer_drops_meta_cannot_see_photo_line() -> None:
    raw = "Since I cannot see the photo, I must inform the user that the photo"
    assert _sanitize_chat_answer(raw) == ""


def test_looks_like_meta_llm_line_detects_english_visual_refusal() -> None:
    assert _looks_like_meta_llm_line("I cannot see the photo directly.") is True


def test_fallback_for_photo_with_visual_context_is_human() -> None:
    assert (
        _chat_fallback_answer(looks_like_photo_question=True, has_visual_context=True)
        == "По фото пока не очень понятно, уточни что именно разобрать."
    )


def test_finalize_chat_answer_uses_fallback_for_meta_response() -> None:
    class _Settings:
        llm_endpoint = "http://127.0.0.1:8080/v1/chat/completions"
        llm_model = "primary"
        llm_chat_endpoint = "http://127.0.0.1:8080/v1/chat/completions"
        llm_chat_model = "primary"
        llm_backend = "llama_cpp"
        llm_timeout = 5.0

    result = _finalize_chat_answer(
        "Я должен объявить, что как ИИ не вижу фото.",
        question="что на фото?",
        has_visual_context=True,
        settings=_Settings(),
    )
    assert result == "По фото пока не очень понятно, уточни что именно разобрать."


def test_asks_for_photo_upload_detects_russian_phrase() -> None:
    assert _asks_for_photo_upload("Пришлите картинку, я посмотрю.") is True


def test_asks_for_photo_upload_detects_missing_image_phrase() -> None:
    assert _asks_for_photo_upload("Картинка не прислана.") is True


def test_finalize_chat_answer_rejects_reupload_request_for_photo_context() -> None:
    class _Settings:
        llm_endpoint = "http://127.0.0.1:8080/v1/chat/completions"
        llm_model = "primary"
        llm_chat_endpoint = "http://127.0.0.1:8080/v1/chat/completions"
        llm_chat_model = "primary"
        llm_backend = "llama_cpp"
        llm_timeout = 5.0

    result = _finalize_chat_answer(
        "Пришлите картинку, я посмотрю.",
        question="что на фото?",
        has_visual_context=True,
        settings=_Settings(),
    )
    assert result == "По фото пока не очень понятно, уточни что именно разобрать."


def test_needs_rewrite_or_retry_detects_reasoning_marker() -> None:
    assert _needs_rewrite_or_retry("Select the best option: Keep") is True


def test_format_reply_visual_status_reports_voice_as_unsupported(monkeypatch) -> None:
    class _Voice:
        pass

    class _Reply:
        voice = _Voice()

    class _Message:
        reply_to_message = _Reply()

    monkeypatch.setattr("bot.ENABLE_VISUAL_FEATURES", True)
    monkeypatch.setattr("bot._find_visual_source_message", lambda message, include_self: None)

    status = _format_reply_visual_status(_Message())
    assert "Voice-сообщения пока не поддерживаются" in status
