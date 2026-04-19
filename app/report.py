from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence

MessageRow = Mapping[str, Any]

PERIODS_ORDER = ("night", "morning", "day", "evening")
PERIOD_LABELS = {
    "night": "ночь",
    "morning": "утро",
    "day": "день",
    "evening": "вечер",
}
PERIOD_INSTRUMENTAL = {
    "night": "ночью",
    "morning": "утром",
    "day": "днем",
    "evening": "вечером",
}


@dataclass(frozen=True)
class DailyStats:
    total_messages: int
    messages_by_user: dict[int, int]
    chars_by_user: dict[int, int]
    words_by_user: dict[int, int]
    activity_by_hour: dict[int, int]
    most_active_hour: int | None
    most_active_period: str | None


def count_total_messages(messages: list[MessageRow]) -> int:
    return len(messages)


def count_messages_by_user(messages: list[MessageRow]) -> dict[int, int]:
    result: dict[int, int] = {}
    for row in messages:
        user_id = int(row["user_id"])
        result[user_id] = result.get(user_id, 0) + 1
    return result


def count_chars_by_user(messages: list[MessageRow]) -> dict[int, int]:
    result: dict[int, int] = {}
    for row in messages:
        user_id = int(row["user_id"])
        text_length = int(row.get("text_length", 0) or 0)
        result[user_id] = result.get(user_id, 0) + text_length
    return result


def count_words_by_user(messages: list[MessageRow]) -> dict[int, int]:
    result: dict[int, int] = {}
    for row in messages:
        user_id = int(row["user_id"])
        word_count = int(row.get("word_count", 0) or 0)
        result[user_id] = result.get(user_id, 0) + word_count
    return result


def activity_by_hour(messages: list[MessageRow]) -> dict[int, int]:
    result = {hour: 0 for hour in range(24)}
    for row in messages:
        created_at = _parse_created_at(row["created_at"])
        result[created_at.hour] += 1
    return result


def get_most_active_hour(hourly_activity: dict[int, int]) -> int | None:
    if not hourly_activity:
        return None
    max_count = max(hourly_activity.values(), default=0)
    if max_count == 0:
        return None
    for hour in range(24):
        if hourly_activity.get(hour, 0) == max_count:
            return hour
    return None


def get_period_by_hour(hour: int) -> str:
    if hour == 23 or 0 <= hour <= 5:
        return "night"
    if 6 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 17:
        return "day"
    return "evening"


def activity_by_period(hourly_activity: dict[int, int]) -> dict[str, int]:
    result = {period: 0 for period in PERIODS_ORDER}
    for hour in range(24):
        period = get_period_by_hour(hour)
        result[period] += hourly_activity.get(hour, 0)
    return result


def get_most_active_period(hourly_activity: dict[int, int]) -> str | None:
    period_activity = activity_by_period(hourly_activity)
    max_count = max(period_activity.values(), default=0)
    if max_count == 0:
        return None

    for period in PERIODS_ORDER:
        if period_activity[period] == max_count:
            return period
    return None


def calculate_daily_stats(messages: list[MessageRow]) -> DailyStats:
    hourly = activity_by_hour(messages)
    return DailyStats(
        total_messages=count_total_messages(messages),
        messages_by_user=count_messages_by_user(messages),
        chars_by_user=count_chars_by_user(messages),
        words_by_user=count_words_by_user(messages),
        activity_by_hour=hourly,
        most_active_hour=get_most_active_hour(hourly),
        most_active_period=get_most_active_period(hourly),
    )


def build_discussion_character(
    stats: DailyStats,
    topics: Sequence[str],
    user_names: Mapping[int, str] | None = None,
    max_phrases: int = 2,
) -> list[str]:
    """
    Build 1-2 short phrases for "Характер обсуждения" block.
    Rules are simple and deterministic (no NLP).
    """
    phrases_limit = 1 if max_phrases <= 1 else 2

    if stats.total_messages < 3 or _topics_indicate_short_discussion(topics):
        return ["обсуждение было коротким и без выраженного доминирования тем"]

    phrases: list[str] = []

    main_topic = _pick_main_topic(topics)
    if main_topic:
        phrases.append(f"больше всего обсуждали {main_topic}")

    dominant_user = _pick_dominant_user(stats.messages_by_user)
    if dominant_user is not None and len(phrases) < phrases_limit:
        user_name = _resolve_user_name(dominant_user, user_names)
        phrases.append(f"основную активность задавал {user_name}")

    if len(phrases) < phrases_limit and stats.most_active_period in PERIOD_INSTRUMENTAL:
        period_text = PERIOD_INSTRUMENTAL[stats.most_active_period]
        phrases.append(f"пик активности был {period_text}")

    if not phrases:
        return ["обсуждение было без выраженного доминирования тем"]

    return phrases[:phrases_limit]


def format_daily_report(
    report_date: date | datetime | str,
    stats: DailyStats,
    topics: Sequence[str],
    character_phrases: Sequence[str] | None = None,
    user_names: Mapping[int, str] | None = None,
) -> str:
    """Format daily report in Telegram-friendly text."""
    lines: list[str] = [f"📊 Итоги за {_format_report_date(report_date)}", ""]
    lines.append(f"Всего сообщений: {stats.total_messages}")
    lines.append("")

    lines.append("👤 Активность:")
    lines.extend(_format_activity_block(stats.messages_by_user, user_names))
    lines.append("")

    lines.append("📝 По объёму:")
    lines.extend(_format_volume_block(stats.chars_by_user, user_names))
    lines.append("")

    lines.append("🔥 Основные темы:")
    lines.extend(_format_topics_block(topics))
    lines.append("")

    lines.append("💬 Характер обсуждения:")
    if character_phrases is None:
        character_phrases = build_discussion_character(stats=stats, topics=topics, user_names=user_names)
    lines.extend(_format_character_block(character_phrases))

    return "\n".join(lines)


def _format_activity_block(
    messages_by_user: Mapping[int, int],
    user_names: Mapping[int, str] | None,
) -> list[str]:
    if not messages_by_user:
        return ["1. Нет данных — 0"]

    ordered = sorted(messages_by_user.items(), key=lambda item: item[1], reverse=True)
    result: list[str] = []
    for idx, (user_id, count) in enumerate(ordered[:3], start=1):
        name = _resolve_user_name(user_id, user_names)
        result.append(f"{idx}. {name} — {count}")
    return result


def _format_volume_block(
    chars_by_user: Mapping[int, int],
    user_names: Mapping[int, str] | None,
) -> list[str]:
    if not chars_by_user:
        return ["— Нет данных: 0 символов"]

    ordered = sorted(chars_by_user.items(), key=lambda item: item[1], reverse=True)
    result: list[str] = []
    for user_id, chars in ordered[:3]:
        name = _resolve_user_name(user_id, user_names)
        result.append(f"— {name}: {chars} символов")
    return result


def _format_topics_block(topics: Sequence[str]) -> list[str]:
    cleaned_topics = [topic.strip() for topic in topics if topic and topic.strip()]
    if not cleaned_topics:
        return ["— Нет выраженных тем"]

    result: list[str] = []
    for topic in cleaned_topics[:4]:
        result.append(f"— {topic}")
    return result


def _format_character_block(character_phrases: Sequence[str]) -> list[str]:
    cleaned = [phrase.strip() for phrase in character_phrases if phrase and phrase.strip()]
    if not cleaned:
        return ["— обсуждение было без выраженного доминирования тем"]
    return [f"— {phrase}" for phrase in cleaned[:2]]


def _topics_indicate_short_discussion(topics: Sequence[str]) -> bool:
    if not topics:
        return True
    joined = " ".join(topic.lower() for topic in topics)
    return "коротк" in joined and "тем" in joined


def _pick_main_topic(topics: Sequence[str]) -> str | None:
    for topic in topics:
        cleaned = topic.strip().strip(".")
        if not cleaned:
            continue
        if "коротк" in cleaned.lower():
            continue
        return _to_lower_sentence_start(cleaned)
    return None


def _pick_dominant_user(messages_by_user: Mapping[int, int]) -> int | None:
    if not messages_by_user:
        return None

    ordered = sorted(messages_by_user.items(), key=lambda item: item[1], reverse=True)
    top_user_id, top_count = ordered[0]
    total = sum(messages_by_user.values())
    if total <= 0 or top_count < 2:
        return None

    top_share = top_count / total
    second_count = ordered[1][1] if len(ordered) > 1 else 0
    if top_share >= 0.4 and top_count > second_count:
        return top_user_id
    return None


def _resolve_user_name(user_id: int, user_names: Mapping[int, str] | None) -> str:
    if user_names and user_id in user_names:
        name = user_names[user_id].strip()
        if name:
            return name
    return f"user_id={user_id}"


def _to_lower_sentence_start(text: str) -> str:
    if not text:
        return text
    return text[:1].lower() + text[1:]


def _format_report_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m")
    if isinstance(value, date):
        return value.strftime("%d.%m")

    text_value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text_value, fmt)
            return parsed.strftime("%d.%m")
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text_value).strftime("%d.%m")
    except ValueError:
        return text_value


def _parse_created_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value

    text_value = str(value).strip()
    if text_value.endswith("Z"):
        text_value = text_value[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(text_value)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text_value, fmt)
        except ValueError:
            continue

    raise ValueError(f"Unsupported datetime format: {value!r}")
