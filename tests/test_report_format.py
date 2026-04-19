from __future__ import annotations

from datetime import date

from app.report import DailyStats, format_daily_report


def test_format_daily_report_contains_expected_blocks() -> None:
    stats = DailyStats(
        total_messages=3,
        messages_by_user={1: 2, 2: 1},
        chars_by_user={1: 120, 2: 30},
        words_by_user={1: 20, 2: 5},
        activity_by_hour={hour: 0 for hour in range(24)},
        most_active_hour=10,
        most_active_period="evening",
    )
    user_names = {1: "Артём", 2: "Ирина"}
    topics = ["Разработка и технологии", "Организация встреч"]
    character = ["пик активности был вечером"]

    report = format_daily_report(
        report_date=date(2026, 4, 18),
        stats=stats,
        topics=topics,
        character_phrases=character,
        user_names=user_names,
    )

    report_bytes = report.encode("utf-8")
    assert report_bytes.startswith(b"\xf0\x9f\x93\x8a")  # 📊
    assert "Итоги за 18.04" in report
    assert "Всего сообщений: 3" in report
    assert "Активность:" in report
    assert "1. Артём" in report
    assert "2. Ирина" in report
    assert "По объёму:" in report
    assert "— Артём: 120 символов" in report
    assert "Основные темы:" in report
    assert "— Разработка и технологии" in report
    assert "Характер обсуждения:" in report
    assert "— пик активности был вечером" in report
