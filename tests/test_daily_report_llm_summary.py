from __future__ import annotations

from types import SimpleNamespace

from daily_report import (
    _sanitize_llm_summary_lines,
    build_topics_and_summary_lines,
)


def test_sanitize_llm_summary_lines_drops_stat_digest_rows() -> None:
    topics = ["Сообщения", "Топ участников", "Пик активности"]
    summary_lines = [
        "Всего сообщений за день: 96",
        "Топ участников: Artyom=70; Alex=18",
        "Сообщения",
        "основную активность задавал Artyom",
    ]

    cleaned = _sanitize_llm_summary_lines(topics, summary_lines)

    assert cleaned == ["основную активность задавал Artyom"]


def test_build_topics_and_summary_lines_falls_back_when_llm_summary_is_digest(monkeypatch) -> None:
    class _Settings:
        use_llm_topics = True
        llm_backend = "llama_cpp"
        llm_model = "fake"
        llm_endpoint = "http://127.0.0.1:8080/v1/chat/completions"
        llm_timeout = 3.0
        llm_chat_model = ""
        llm_chat_endpoint = ""

    monkeypatch.setattr(
        "daily_report.try_extract_topics_and_summary",
        lambda *args, **kwargs: (
            ["Сообщения", "Топ участников", "Пик активности"],
            [
                "Всего сообщений за день: 96",
                "Топ участников: Artyom=70; Alex=18; Klinch=8",
            ],
        ),
    )

    messages = [
        {
            "chat_id": 1,
            "user_id": 10,
            "text": "обсуждаем релиз бота",
            "text_length": 20,
            "word_count": 3,
            "created_at": "2026-04-24 18:00:00",
            "full_name": "Artyom",
        },
        {
            "chat_id": 1,
            "user_id": 20,
            "text": "надо поправить отчёт",
            "text_length": 21,
            "word_count": 3,
            "created_at": "2026-04-24 18:15:00",
            "full_name": "Alex",
        },
        {
            "chat_id": 1,
            "user_id": 10,
            "text": "согласен",
            "text_length": 8,
            "word_count": 1,
            "created_at": "2026-04-24 18:20:00",
            "full_name": "Artyom",
        },
    ]

    stats = SimpleNamespace(
        total_messages=3,
        messages_by_user={10: 2, 20: 1},
        most_active_period="evening",
    )

    topics, summary = build_topics_and_summary_lines(
        messages=messages,
        stats=stats,
        user_names={10: "Artyom", 20: "Alex"},
        settings=_Settings(),
    )

    assert topics == ["Сообщения", "Топ участников", "Пик активности"]
    assert summary
    assert not any("Всего сообщений" in line for line in summary)
    assert not any("Топ участников" in line for line in summary)
