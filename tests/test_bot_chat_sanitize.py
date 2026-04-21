from __future__ import annotations

from bot import _sanitize_chat_answer


def test_sanitize_chat_answer_removes_input_message_prefix() -> None:
    raw = '**Input Message:** "Дай денег" (Give money).'
    assert _sanitize_chat_answer(raw) == '"Дай денег" (Give money).'


def test_sanitize_chat_answer_drops_attempt_line() -> None:
    raw = "*Attempt\nНормальный ответ"
    assert _sanitize_chat_answer(raw) == "Нормальный ответ"
