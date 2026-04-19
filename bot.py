from __future__ import annotations

import asyncio
import logging
import time
from datetime import date

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import Message

from app.config import get_settings
from app.db import close_db, delete_messages_older_than, get_messages_by_day, init_db, save_message
from app.report import calculate_daily_stats, format_daily_report
from daily_report import build_topics_and_summary_lines, build_user_names

logger = logging.getLogger(__name__)
_last_cleanup_ts: float = 0.0


def _detect_message_type(message: Message) -> str:
    if message.text:
        return "text"
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.sticker:
        return "sticker"
    if message.voice:
        return "voice"
    if message.document:
        return "document"
    return "other"


def _extract_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


async def main() -> None:
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
    dp = Dispatcher()
    router = Router()

    @router.message(Command("stats"))
    async def on_stats(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return

        today = date.today()
        day_messages = get_messages_by_day(today)
        messages = [row for row in day_messages if int(row.get("chat_id", 0)) == settings.allowed_chat_id]
        user_names = build_user_names(messages)

        stats = calculate_daily_stats(messages)
        topics, character = build_topics_and_summary_lines(
            messages=messages,
            stats=stats,
            user_names=user_names,
            settings=settings,
        )

        report_text = format_daily_report(
            report_date=today,
            stats=stats,
            topics=topics,
            character_phrases=character,
            user_names=user_names,
            title_prefix="📊 На данный момент за",
        )
        await message.answer(report_text)

    @router.message()
    async def on_message(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        if message.text and message.text.strip().lower().startswith("/stats"):
            return

        text_content = _extract_text(message)
        message_type = _detect_message_type(message)

        user = message.from_user
        user_id = user.id if user else 0
        username = user.username if user else None
        full_name = user.full_name if user else "Unknown"

        save_message(
            chat_id=message.chat.id,
            user_id=user_id,
            username=username,
            full_name=full_name,
            message_id=message.message_id,
            text=text_content,
            message_type=message_type,
            text_length=len(text_content),
            word_count=_word_count(text_content),
            created_at=message.date,
        )
        logger.debug(
            "Saved message chat_id=%s message_id=%s type=%s",
            message.chat.id,
            message.message_id,
            message_type,
        )

        global _last_cleanup_ts
        now_ts = time.time()
        if now_ts - _last_cleanup_ts >= 3600:
            delete_messages_older_than(7)
            _last_cleanup_ts = now_ts

    dp.include_router(router)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        close_db()


if __name__ == "__main__":
    asyncio.run(main())
