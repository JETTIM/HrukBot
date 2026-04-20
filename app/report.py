from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence

MessageRow = Mapping[str, Any]

PERIODS_ORDER = ("night", "morning", "day", "evening")
PERIOD_LABELS = {
    "night": "РЅРѕС‡СЊ",
    "morning": "СѓС‚СЂРѕ",
    "day": "РґРµРЅСЊ",
    "evening": "РІРµС‡РµСЂ",
}
PERIOD_INSTRUMENTAL = {
    "night": "РЅРѕС‡СЊСЋ",
    "morning": "СѓС‚СЂРѕРј",
    "day": "РґРЅРµРј",
    "evening": "РІРµС‡РµСЂРѕРј",
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
    max_phrases: int = 3,
) -> list[str]:
    """
    Build 1-2 short phrases for "РҐР°СЂР°РєС‚РµСЂ РѕР±СЃСѓР¶РґРµРЅРёСЏ" block.
    Rules are simple and deterministic (no NLP).
    """
    phrases_limit = max(1, min(3, max_phrases))

    if stats.total_messages < 3 or _topics_indicate_short_discussion(topics):
        return ["РѕР±СЃСѓР¶РґРµРЅРёРµ Р±С‹Р»Рѕ РєРѕСЂРѕС‚РєРёРј Рё Р±РµР· РІС‹СЂР°Р¶РµРЅРЅРѕРіРѕ РґРѕРјРёРЅРёСЂРѕРІР°РЅРёСЏ С‚РµРј"]

    phrases: list[str] = []

    main_topic = _pick_main_topic(topics)
    if main_topic:
        phrases.append(f"Р±РѕР»СЊС€Рµ РІСЃРµРіРѕ РѕР±СЃСѓР¶РґР°Р»Рё {main_topic}")
        second_topic = _pick_secondary_topic(topics, main_topic)
        if second_topic and len(phrases) < phrases_limit:
            phrases.append(f"РїР°СЂР°Р»Р»РµР»СЊРЅРѕ РѕР±СЃСѓР¶РґР°Р»Рё {second_topic}")

    dominant_user = _pick_dominant_user(stats.messages_by_user)
    if dominant_user is not None and len(phrases) < phrases_limit:
        user_name = _resolve_user_name(dominant_user, user_names)
        phrases.append(f"РѕСЃРЅРѕРІРЅСѓСЋ Р°РєС‚РёРІРЅРѕСЃС‚СЊ Р·Р°РґР°РІР°Р» {user_name}")

    if len(phrases) < phrases_limit and stats.most_active_period in PERIOD_INSTRUMENTAL:
        period_text = PERIOD_INSTRUMENTAL[stats.most_active_period]
        phrases.append(f"РїРёРє Р°РєС‚РёРІРЅРѕСЃС‚Рё Р±С‹Р» {period_text}")

    if not phrases:
        return ["РѕР±СЃСѓР¶РґРµРЅРёРµ Р±С‹Р»Рѕ Р±РµР· РІС‹СЂР°Р¶РµРЅРЅРѕРіРѕ РґРѕРјРёРЅРёСЂРѕРІР°РЅРёСЏ С‚РµРј"]

    return phrases[:phrases_limit]


def format_daily_report(
    report_date: date | datetime | str,
    stats: DailyStats,
    topics: Sequence[str],
    character_phrases: Sequence[str] | None = None,
    user_names: Mapping[int, str] | None = None,
    title_prefix: str = "рџ“Љ РС‚РѕРіРё Р·Р°",
) -> str:
    """Format daily report in Telegram-friendly text."""
    lines: list[str] = [f"{title_prefix} {_format_report_date(report_date)}", ""]
    lines.append(f"Р’СЃРµРіРѕ СЃРѕРѕР±С‰РµРЅРёР№: {stats.total_messages}")
    lines.append("")

    lines.append("рџ‘¤ РђРєС‚РёРІРЅРѕСЃС‚СЊ:")
    lines.extend(_format_activity_block(stats.messages_by_user, user_names))
    lines.append("")

    lines.append("рџ“ќ РџРѕ РѕР±СЉС‘РјСѓ:")
    lines.extend(_format_volume_block(stats.chars_by_user, user_names))
    lines.append("")

    lines.append("рџ”Ґ РћСЃРЅРѕРІРЅС‹Рµ С‚РµРјС‹:")
    lines.extend(_format_topics_block(topics))
    lines.append("")

    lines.append("рџ’¬ РҐР°СЂР°РєС‚РµСЂ РѕР±СЃСѓР¶РґРµРЅРёСЏ:")
    if character_phrases is None:
        character_phrases = build_discussion_character(stats=stats, topics=topics, user_names=user_names)
    lines.extend(_format_character_block(character_phrases))

    return "\n".join(lines)


def _format_activity_block(
    messages_by_user: Mapping[int, int],
    user_names: Mapping[int, str] | None,
) -> list[str]:
    if not messages_by_user:
        return ["1. РќРµС‚ РґР°РЅРЅС‹С… вЂ” 0"]

    ordered = sorted(messages_by_user.items(), key=lambda item: item[1], reverse=True)
    result: list[str] = []
    for idx, (user_id, count) in enumerate(ordered[:3], start=1):
        name = _resolve_user_name(user_id, user_names)
        result.append(f"{idx}. {name} вЂ” {count}")
    return result


def _format_volume_block(
    chars_by_user: Mapping[int, int],
    user_names: Mapping[int, str] | None,
) -> list[str]:
    if not chars_by_user:
        return ["вЂ” РќРµС‚ РґР°РЅРЅС‹С…: 0 СЃРёРјРІРѕР»РѕРІ"]

    ordered = sorted(chars_by_user.items(), key=lambda item: item[1], reverse=True)
    result: list[str] = []
    for user_id, chars in ordered[:3]:
        name = _resolve_user_name(user_id, user_names)
        result.append(f"вЂ” {name}: {chars} СЃРёРјРІРѕР»РѕРІ")
    return result


def _format_topics_block(topics: Sequence[str]) -> list[str]:
    cleaned_topics = [topic.strip() for topic in topics if topic and topic.strip()]
    if not cleaned_topics:
        return ["вЂ” РќРµС‚ РІС‹СЂР°Р¶РµРЅРЅС‹С… С‚РµРј"]

    result: list[str] = []
    for topic in cleaned_topics[:4]:
        result.append(f"вЂ” {topic}")
    return result


def _format_character_block(character_phrases: Sequence[str]) -> list[str]:
    cleaned = [phrase.strip() for phrase in character_phrases if phrase and phrase.strip()]
    if not cleaned:
        return ["вЂ” РѕР±СЃСѓР¶РґРµРЅРёРµ Р±С‹Р»Рѕ Р±РµР· РІС‹СЂР°Р¶РµРЅРЅРѕРіРѕ РґРѕРјРёРЅРёСЂРѕРІР°РЅРёСЏ С‚РµРј"]
    return [f"вЂ” {phrase}" for phrase in cleaned[:3]]


def _topics_indicate_short_discussion(topics: Sequence[str]) -> bool:
    if not topics:
        return True
    joined = " ".join(topic.lower() for topic in topics)
    return "РєРѕСЂРѕС‚Рє" in joined and "С‚РµРј" in joined


def _pick_main_topic(topics: Sequence[str]) -> str | None:
    for topic in topics:
        cleaned = topic.strip().strip(".")
        if not cleaned:
            continue
        if "РєРѕСЂРѕС‚Рє" in cleaned.lower():
            continue
        return _to_lower_sentence_start(cleaned)
    return None


def _pick_secondary_topic(topics: Sequence[str], main_topic: str) -> str | None:
    normalized_main = main_topic.strip().lower()
    for topic in topics:
        cleaned = topic.strip().strip(".")
        if not cleaned:
            continue
        if "РєРѕСЂРѕС‚Рє" in cleaned.lower():
            continue
        lowered = _to_lower_sentence_start(cleaned)
        if lowered.lower() == normalized_main:
            continue
        return lowered
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



