from __future__ import annotations

from bot import _build_question_with_context, _inject_question_hints


def test_inject_question_hints_adds_norwood_hint() -> None:
    question = "какой норвуд?"
    hinted = _inject_question_hints(question)
    assert "шкалу Норвуда" in hinted


def test_build_question_with_context_discourages_name_greetings() -> None:
    prompt = _build_question_with_context(
        "что думаешь?",
        visual_context=None,
        reply_text_context=None,
        short_memory="Сообщение из чата: Привет, меня зовут Alex",
    )
    assert "Не обращайся к пользователю по имени" in prompt
    assert "Сообщение из чата: Привет, меня зовут Alex" in prompt
