from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import date, timedelta
from html import escape

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import Message

from app.config import get_settings
from app.db import close_db, delete_messages_older_than, get_messages_by_day, init_db, save_message
from app.images import get_image_file_id, process_message_image
from app.llm_topics import (
    try_answer_question,
    try_generate_mood_summary,
    try_generate_svin_comment,
    try_generate_svin_joke,
)
from app.report import activity_by_hour, calculate_daily_stats, format_daily_report
from daily_report import build_topics_and_summary_lines, build_user_names

logger = logging.getLogger(__name__)
_last_cleanup_ts: float = 0.0
_messages_since_svin_reply = 10


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


def _get_chat_messages_by_day(day_value: date, chat_id: int) -> list[dict]:
    rows = get_messages_by_day(day_value)
    return [row for row in rows if int(row.get("chat_id", 0)) == chat_id]


def _format_hour_window(hour: int) -> str:
    next_hour = (hour + 1) % 24
    return f"{hour:02d}:00–{next_hour:02d}:00"


def _estimate_minutes(messages_count: int) -> int:
    seconds = messages_count * 7
    return (seconds + 59) // 60 if seconds else 0


def _fallback_mood(messages_count: int) -> str:
    if messages_count >= 40:
        return "чат активный"
    if messages_count >= 10:
        return "чат умеренный"
    return "чат тихий"


def _build_topics_source(messages: list[dict]) -> list[str]:
    return [str(row.get("text") or "") for row in messages]


def _should_try_svin_reply(today_messages_count: int) -> bool:
    return today_messages_count >= 10 and _messages_since_svin_reply >= 10 and random.random() < 0.03


def _extract_command_args(message: Message) -> str:
    if not message.text:
        return ""
    parts = message.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


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
    bot_info = await bot.get_me()
    dp = Dispatcher()
    router = Router()

    @router.message(Command("stats"))
    async def on_stats(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return

        today = date.today()
        messages = _get_chat_messages_by_day(today, settings.allowed_chat_id)
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

    @router.message(Command("dead"))
    async def on_dead(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return

        today = date.today()
        today_count = len(_get_chat_messages_by_day(today, settings.allowed_chat_id))
        yesterday_count = len(_get_chat_messages_by_day(today - timedelta(days=1), settings.allowed_chat_id))

        if yesterday_count == 0:
            text = (
                "☠️ Активность чата\n\n"
                f"— сегодня: {today_count} сообщений\n"
                f"— вчера: {yesterday_count} сообщений"
            )
        else:
            death_percent = 100 - (today_count / yesterday_count * 100)
            text = (
                f"☠️ Чат мёртв на {death_percent:.0f}%\n\n"
                f"— сегодня: {today_count} сообщений\n"
                f"— вчера: {yesterday_count} сообщений"
            )
        await message.answer(text)

    @router.message(Command("time"))
    async def on_time(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return

        messages_count = len(_get_chat_messages_by_day(date.today(), settings.allowed_chat_id))
        await message.answer(
            "⏳ Сегодня:\n\n"
            f"— сообщений: {messages_count}\n"
            f"— примерное время: ~{_estimate_minutes(messages_count)} минут"
        )

    @router.message(Command("when"))
    async def on_when(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return

        messages = _get_chat_messages_by_day(date.today(), settings.allowed_chat_id)
        hourly = activity_by_hour(messages)
        peak_hour, peak_count = max(hourly.items(), key=lambda item: item[1])
        quiet_hour, _ = min(hourly.items(), key=lambda item: item[1])

        await message.answer(
            "⏰ Пик активности:\n\n"
            f"— {_format_hour_window(peak_hour)} ({peak_count} сообщений)\n\n"
            "📉 Самое тихое время:\n"
            f"— {_format_hour_window(quiet_hour)}"
        )

    @router.message(Command("mood"))
    async def on_mood(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return

        messages = _get_chat_messages_by_day(date.today(), settings.allowed_chat_id)
        mood = None
        if settings.use_llm_topics:
            mood = try_generate_mood_summary(
                _build_topics_source(messages),
                backend=settings.llm_backend,
                model=settings.llm_model,
                endpoint=settings.llm_endpoint,
                timeout=settings.llm_timeout,
            )
        if mood is None:
            mood = _fallback_mood(len(messages))
        await message.answer(escape(mood))

    @router.message(Command("svin"))
    async def on_svin(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return

        joke = None
        if settings.use_llm_topics:
            joke = try_generate_svin_joke(
                backend=settings.llm_backend,
                model=settings.llm_model,
                endpoint=settings.llm_endpoint,
                timeout=settings.llm_timeout,
            )
        if joke is not None:
            await message.answer(escape(joke))

    @router.message(Command("ask"))
    async def on_ask(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return

        question = _extract_command_args(message)
        if not question:
            await message.answer("Напиши вопрос после команды: /ask что спросить")
            return
        if not settings.use_llm_topics:
            await message.answer("LLM сейчас выключена.")
            return

        answer = try_answer_question(
            question,
            backend=settings.llm_backend,
            model=settings.llm_model,
            endpoint=settings.llm_endpoint,
            timeout=settings.llm_timeout,
        )
        if answer is None:
            await message.answer("Не смогла получить ответ от LLM.")
            return
        await message.answer(escape(answer))

    @router.message()
    async def on_message(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        if message.text and message.text.strip().lower().startswith((
            "/stats",
            "/svin",
            "/dead",
            "/time",
            "/when",
            "/mood",
            "/ask",
        )):
            return
        if message.from_user and message.from_user.id == bot_info.id:
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

        if get_image_file_id(message) is not None:
            asyncio.create_task(process_message_image(bot, message))

        global _last_cleanup_ts, _messages_since_svin_reply
        _messages_since_svin_reply += 1
        now_ts = time.time()
        if now_ts - _last_cleanup_ts >= 3600:
            delete_messages_older_than(7)
            _last_cleanup_ts = now_ts

        today_messages_count = len(_get_chat_messages_by_day(date.today(), settings.allowed_chat_id))
        if settings.use_llm_topics and _should_try_svin_reply(today_messages_count):
            comment = try_generate_svin_comment(
                text_content,
                backend=settings.llm_backend,
                model=settings.llm_model,
                endpoint=settings.llm_endpoint,
                timeout=settings.llm_timeout,
            )
            if comment is not None:
                await message.answer(
                    escape(comment),
                    reply_to_message_id=message.message_id,
                )
                _messages_since_svin_reply = 0

    dp.include_router(router)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        close_db()


if __name__ == "__main__":
    asyncio.run(main())
