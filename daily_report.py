from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from app.config import get_settings
from app.db import close_db, delete_messages_older_than, get_messages_by_day, init_db
from app.llm_topics import try_extract_topics_and_summary
from app.report import (
    build_discussion_character,
    calculate_daily_stats,
    format_daily_report,
)
from app.topics import extract_main_topics

MAX_TELEGRAM_MESSAGE_LENGTH = 4096
logger = logging.getLogger(__name__)


def get_previous_day(reference: datetime | None = None) -> date:
    now = reference or datetime.now()
    return (now - timedelta(days=1)).date()


def filter_messages_by_chat(messages: list[dict[str, Any]], chat_id: int) -> list[dict[str, Any]]:
    return [row for row in messages if int(row.get("chat_id", 0)) == chat_id]


def build_user_names(messages: list[dict[str, Any]]) -> dict[int, str]:
    result: dict[int, str] = {}
    for row in messages:
        user_id = int(row.get("user_id", 0))
        if not user_id:
            continue

        full_name = str(row.get("full_name") or "").strip()
        username = str(row.get("username") or "").strip()
        if full_name:
            result[user_id] = full_name
        elif username:
            result[user_id] = f"@{username}"
        else:
            result[user_id] = f"user_id={user_id}"
    return result


def build_topics_source(messages: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("text") or "") for row in messages]


def _is_summary_too_similar_to_topics(topics: list[str], summary_lines: list[str]) -> bool:
    normalized_topics = {topic.strip().lower() for topic in topics if topic.strip()}
    normalized_summary = [line.strip().lower() for line in summary_lines if line.strip()]
    if not normalized_topics or not normalized_summary:
        return False

    equal_hits = sum(1 for line in normalized_summary if line in normalized_topics)
    return equal_hits >= min(2, len(normalized_summary))


def build_topics_and_summary_lines(
    *,
    messages: list[dict[str, Any]],
    stats: Any,
    user_names: dict[int, str],
    settings: Any,
    return_meta: bool = False,
) -> tuple[list[str], list[str]] | tuple[list[str], list[str], bool]:
    used_llm = False
    if stats.total_messages == 0:
        topics = ["Обсуждение пока короткое, выраженные темы не выделяются."]
        summary_lines = ["на текущий момент сообщений почти нет"]
        return (topics, summary_lines, used_llm) if return_meta else (topics, summary_lines)

    topic_source = build_topics_source(messages)
    if not settings.use_llm_topics:
        logger.info("LLM topics disabled; using rule-based topics")
        topics = extract_main_topics(topic_source, top_k=4)
        summary_lines = build_discussion_character(stats=stats, topics=topics, user_names=user_names)
        return (topics, summary_lines, used_llm) if return_meta else (topics, summary_lines)

    llm_result = try_extract_topics_and_summary(
        topic_source,
        backend=settings.llm_backend,
        model=settings.llm_model,
        endpoint=settings.llm_endpoint,
        timeout=settings.llm_timeout,
    )
    if llm_result is not None:
        logger.info("LLM topics used")
        used_llm = True
        topics, summary_lines = llm_result
        if _is_summary_too_similar_to_topics(topics, summary_lines):
            logger.info("LLM summary duplicated topics; using rule-based character lines")
            summary_lines = build_discussion_character(
                stats=stats,
                topics=topics,
                user_names=user_names,
                max_phrases=3,
            )
        return (topics, summary_lines, used_llm) if return_meta else (topics, summary_lines)

    logger.warning("LLM topics fallback used")
    topics = extract_main_topics(topic_source, top_k=4)
    summary_lines = build_discussion_character(stats=stats, topics=topics, user_names=user_names)
    return (topics, summary_lines, used_llm) if return_meta else (topics, summary_lines)

def split_for_telegram(text: str, limit: int = MAX_TELEGRAM_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.splitlines():
        add_len = len(line) + (1 if current else 0)
        if current and current_len + add_len > limit:
            parts.append("\n".join(current))
            current = [line]
            current_len = len(line)
            continue
        current.append(line)
        current_len += add_len

    if current:
        parts.append("\n".join(current))

    return parts


async def send_report(bot: Bot, chat_id: int, text: str) -> None:
    for part in split_for_telegram(text):
        await bot.send_message(chat_id=chat_id, text=part, disable_notification=True)


async def run_daily_report() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    init_db(settings.db_path)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=settings.bot_parse_mode),
    )

    previous_day = get_previous_day()

    try:
        day_messages = get_messages_by_day(previous_day)
        messages = filter_messages_by_chat(day_messages, settings.allowed_chat_id)
        user_names = build_user_names(messages)

        stats = calculate_daily_stats(messages)
        topics, character = build_topics_and_summary_lines(
            messages=messages,
            stats=stats,
            user_names=user_names,
            settings=settings,
        )
        report_text = format_daily_report(
            report_date=previous_day,
            stats=stats,
            topics=topics,
            character_phrases=character,
            user_names=user_names,
        )

        await send_report(bot, settings.allowed_chat_id, report_text)
        deleted = delete_messages_older_than(7)
        logger.info("Daily report sent for %s. Deleted old records: %s", previous_day, deleted)
    except Exception:
        logger.exception("Daily report failed for %s. Old records were not deleted.", previous_day)
        raise
    finally:
        await bot.session.close()
        close_db()


def main() -> None:
    asyncio.run(run_daily_report())


if __name__ == "__main__":
    main()
