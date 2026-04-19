from __future__ import annotations

from app.report import calculate_daily_stats, get_most_active_period


def test_calculate_daily_stats_basic_counts() -> None:
    messages = [
        {
            "chat_id": 1,
            "user_id": 10,
            "text_length": 5,
            "word_count": 1,
            "created_at": "2026-04-18 10:00:00",
        },
        {
            "chat_id": 1,
            "user_id": 10,
            "text_length": 12,
            "word_count": 2,
            "created_at": "2026-04-18 10:30:00",
        },
        {
            "chat_id": 1,
            "user_id": 20,
            "text_length": 3,
            "word_count": 1,
            "created_at": "2026-04-18 18:05:00",
        },
    ]

    stats = calculate_daily_stats(messages)

    assert stats.total_messages == 3
    assert stats.messages_by_user == {10: 2, 20: 1}
    assert stats.chars_by_user == {10: 17, 20: 3}
    assert stats.words_by_user == {10: 3, 20: 1}
    assert stats.activity_by_hour[10] == 2
    assert stats.activity_by_hour[18] == 1


def test_get_most_active_period_evening() -> None:
    hourly = {hour: 0 for hour in range(24)}
    hourly[19] = 5
    hourly[11] = 3
    hourly[1] = 2

    assert get_most_active_period(hourly) == "evening"
