from __future__ import annotations

from app.topics import SHORT_DISCUSSION_MESSAGE, extract_main_topics


def test_extract_main_topics_dictionary_match() -> None:
    # Enough tokens/messages to avoid "short discussion" fallback.
    messages = [
        "Давайте обсудим бота и api, нужен код и тесты для бота.",
        "По коду есть баг, надо фикс и деплой на сервер, бот должен работать стабильно.",
        "Сделаем интеграцию через api, добавим тест и поправим баги в коде бота.",
    ]

    topics = extract_main_topics(messages, top_k=3)

    assert any("Разработка" in t for t in topics)


def test_extract_main_topics_short_discussion_fallback() -> None:
    topics = extract_main_topics(["ок"], top_k=3)
    assert topics == [SHORT_DISCUSSION_MESSAGE]
