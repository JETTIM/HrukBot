from __future__ import annotations

from bot import _looks_like_rewrite_refusal, _sanitize_chat_answer


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
