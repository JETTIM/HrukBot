from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import date, timedelta
from html import escape

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import Message

from app.config import get_settings
from app.db import (
    close_db,
    delete_messages_older_than,
    get_image_event_by_message,
    get_messages_by_day,
    get_top_image_clusters,
    init_db,
    save_message,
)
try:
    from app.images import get_image_file_id, process_message_image
    IMAGE_PIPELINE_AVAILABLE = True
except Exception:  # noqa: BLE001 - optional image deps may be absent on VPS
    IMAGE_PIPELINE_AVAILABLE = False

    def get_image_file_id(message: Message) -> str | None:  # type: ignore[override]
        return None

    async def process_message_image(bot: Bot, message: Message, settings: object) -> None:  # type: ignore[override]
        return
from app.llm_topics import (
    try_answer_question,
    try_generate_mood_summary,
    try_generate_roast,
    try_generate_svin_comment,
    try_generate_svin_joke,
    try_prepare_visual_task,
    try_rewrite_assistant_answer,
)
from app.report import activity_by_hour, calculate_daily_stats, format_daily_report
from daily_report import build_topics_and_summary_lines, build_user_names

logger = logging.getLogger(__name__)
_last_cleanup_ts: float = 0.0
_messages_since_svin_reply = 10
_image_processing_semaphore: asyncio.Semaphore | None = None
_svin_reply_chance = 0.03
PENDING_TEXT = "⏳ ща напишу..."
PENDING_MIN_SECONDS = 1.0
SHORT_MEMORY_MESSAGES = 10
VISUAL_CONTEXT_WAIT_SECONDS = 3.0
VISUAL_CONTEXT_POLL_SECONDS = 0.5
ENABLE_VISUAL_FEATURES = False


def _detect_message_type(message: Message) -> str:
    if message.text:
        return "text"
    if message.photo:
        return "photo"
    if message.animation:
        return "animation"
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
    return f"{hour:02d}:00-{next_hour:02d}:00"


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
    return (
        today_messages_count >= 10
        and _messages_since_svin_reply >= 10
        and random.random() < _svin_reply_chance
    )


def _extract_command_args(message: Message) -> str:
    raw_text = message.text or message.caption
    if not raw_text:
        return ""
    text = raw_text.strip()
    parts = text.split(maxsplit=1)
    if not parts:
        return ""
    command = parts[0].lower()
    if command.startswith("/ask"):
        return parts[1].strip() if len(parts) > 1 else ""
    return parts[1].strip() if len(parts) > 1 else ""


def _extract_command_text(message: Message) -> str:
    raw_text = (message.text or message.caption or "").strip()
    if not raw_text.startswith("/"):
        return ""
    return raw_text


def _log_incoming_command(message: Message) -> None:
    command_text = _extract_command_text(message)
    if not command_text:
        return

    user = message.from_user
    logger.info(
        "Incoming command: chat_id=%s message_id=%s user_id=%s username=%s full_name=%s text=%r",
        message.chat.id,
        message.message_id,
        user.id if user else None,
        user.username if user else None,
        user.full_name if user else None,
        command_text[:500],
    )


def _reply_has_visual_file(message: Message) -> bool:
    if not ENABLE_VISUAL_FEATURES:
        return False
    source = _find_visual_source_message(message, include_self=False)
    return source is not None


def _message_has_visual_file(message: Message) -> bool:
    if not ENABLE_VISUAL_FEATURES:
        return False
    return get_image_file_id(message) is not None


def _find_visual_source_message(message: Message, *, include_self: bool) -> Message | None:
    current = message if include_self else message.reply_to_message
    depth = 0
    while current is not None and depth < 6:
        if get_image_file_id(current) is not None:
            return current
        current = current.reply_to_message
        depth += 1
    return None


def _extract_question_or_visual_default(message: Message) -> str:
    question = _extract_command_args(message)
    if question.strip():
        return question
    if _message_has_visual_file(message):
        return "что на картинке?"
    if _reply_has_visual_file(message):
        return "что на картинке?"
    return ""


def _is_addressed_to_bot(message: Message, bot_username: str | None, bot_id: int) -> bool:
    text = (message.text or message.caption or "").lower()
    username_hit = bool(bot_username and f"@{bot_username.lower()}" in text)
    reply_hit = _is_reply_to_bot(message, bot_username, bot_id)
    return username_hit or reply_hit


def _is_reply_to_bot(message: Message, bot_username: str | None, bot_id: int) -> bool:
    reply_user = message.reply_to_message.from_user if message.reply_to_message else None
    if not reply_user:
        return False
    if reply_user.id == bot_id:
        return True
    if bot_username and reply_user.username and reply_user.username.lower() == bot_username.lower():
        return True
    if getattr(reply_user, "is_bot", False):
        return True
    return False


def _strip_bot_mention(text: str, bot_username: str | None) -> str:
    if not bot_username:
        return text.strip()
    return text.replace(f"@{bot_username}", "").replace(f"@{bot_username.lower()}", "").strip()


def _is_low_info_question(text: str) -> bool:
    cleaned = " ".join((text or "").split()).strip().lower()
    if not cleaned:
        return True
    if len(cleaned) <= 2:
        return True
    low_info_tokens = {"а", "ну", "ок", "ага", "чо", "че", "?", ".", "??", "..."}
    return cleaned in low_info_tokens


def _normalize_reply_question(question: str, reply_text_context: str | None) -> str:
    if not _is_low_info_question(question):
        return question
    if not reply_text_context:
        return question
    return (
        "Пользователь дал очень короткую реплику в ответ на предыдущее сообщение бота. "
        "Продолжи мысль по контексту предыдущего ответа и уточни, что именно имелось в виду.\n\n"
        f"Короткая реплика пользователя: {question}\n"
        f"Предыдущее сообщение бота: {reply_text_context}"
    )


def _build_reply_visual_context(message: Message) -> str | None:
    if not ENABLE_VISUAL_FEATURES:
        return None
    reply = _find_visual_source_message(message, include_self=False)
    if not reply:
        return None

    event = get_image_event_by_message(chat_id=reply.chat.id, message_id=reply.message_id)
    if not event:
        return None

    status = str(event.get("processing_status") or "")
    if status != "processed":
        return "Визуальный контекст reply-сообщения пока не обработан."

    parts: list[str] = []
    cluster_id = event.get("cluster_id")
    cluster_summary = str(event.get("cluster_summary") or "").strip()
    summary_text = str(event.get("summary_text") or "").strip()
    ocr_text = str(event.get("ocr_text") or "").strip()
    context_text = str(event.get("context_text") or "").strip()
    usage_count = event.get("usage_count")

    if cluster_id:
        parts.append(f"visual cluster #{cluster_id}")
    if usage_count:
        parts.append(f"повторов в памяти: {usage_count}")
    if cluster_summary:
        parts.append(f"описание кластера: {cluster_summary}")
    if summary_text and summary_text != cluster_summary:
        parts.append(f"OCR/label: {summary_text}")
    if ocr_text:
        parts.append(f"OCR текст: {' '.join(ocr_text.split())[:500]}")
    if context_text:
        parts.append(f"caption/context: {context_text[:500]}")

    return "\n".join(parts) if parts else "Картинка знакома, но осмысленного описания пока нет."


def _build_message_visual_context(message: Message) -> str | None:
    if not ENABLE_VISUAL_FEATURES:
        return None
    event = get_image_event_by_message(chat_id=message.chat.id, message_id=message.message_id)
    if not event:
        return None

    status = str(event.get("processing_status") or "")
    if status != "processed":
        return "Визуальный контекст этой картинки пока не обработан."

    parts: list[str] = []
    cluster_id = event.get("cluster_id")
    cluster_summary = str(event.get("cluster_summary") or "").strip()
    summary_text = str(event.get("summary_text") or "").strip()
    ocr_text = str(event.get("ocr_text") or "").strip()
    context_text = str(event.get("context_text") or "").strip()
    usage_count = event.get("usage_count")

    if cluster_id:
        parts.append(f"visual cluster #{cluster_id}")
    if usage_count:
        parts.append(f"повторов в памяти: {usage_count}")
    if cluster_summary:
        parts.append(f"описание кластера: {cluster_summary}")
    if summary_text and summary_text != cluster_summary:
        parts.append(f"OCR/label: {summary_text}")
    if ocr_text:
        parts.append(f"OCR текст: {' '.join(ocr_text.split())[:500]}")
    if context_text:
        parts.append(f"caption/context: {context_text[:500]}")

    return "\n".join(parts) if parts else "Картинка знакома, но осмысленного описания пока нет."


async def _wait_for_reply_visual_context(message: Message) -> str | None:
    if not ENABLE_VISUAL_FEATURES:
        return None
    await _ensure_reply_visual_processing(message)
    if not _reply_has_visual_file(message):
        return _build_reply_visual_context(message)

    deadline = time.monotonic() + VISUAL_CONTEXT_WAIT_SECONDS
    while True:
        visual_context = _build_reply_visual_context(message)
        if visual_context is not None:
            return visual_context
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(VISUAL_CONTEXT_POLL_SECONDS)


async def _wait_for_message_visual_context(message: Message, settings: object) -> str | None:
    if not ENABLE_VISUAL_FEATURES:
        return None
    if not _message_has_visual_file(message):
        return _build_message_visual_context(message)

    await _ensure_message_visual_processing(message, settings)
    deadline = time.monotonic() + VISUAL_CONTEXT_WAIT_SECONDS
    while True:
        visual_context = _build_message_visual_context(message)
        if visual_context is not None:
            return visual_context
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(VISUAL_CONTEXT_POLL_SECONDS)


async def _ensure_reply_visual_processing(message: Message) -> bool:
    if not ENABLE_VISUAL_FEATURES:
        return False
    reply = _find_visual_source_message(message, include_self=False)
    if not reply:
        return False

    existing_event = get_image_event_by_message(chat_id=reply.chat.id, message_id=reply.message_id)
    if existing_event is not None:
        return False

    logger.info(
        "Scheduling visual processing from reply command: chat_id=%s message_id=%s trigger_message_id=%s",
        reply.chat.id,
        reply.message_id,
        message.message_id,
    )
    asyncio.create_task(_process_message_image_limited(message.bot, reply, settings=get_settings()))
    return True


async def _ensure_message_visual_processing(message: Message, settings: object) -> bool:
    if not ENABLE_VISUAL_FEATURES:
        return False
    if get_image_file_id(message) is None:
        return False

    existing_event = get_image_event_by_message(chat_id=message.chat.id, message_id=message.message_id)
    if existing_event is not None:
        return False

    logger.info(
        "Scheduling visual processing from command message: chat_id=%s message_id=%s",
        message.chat.id,
        message.message_id,
    )
    asyncio.create_task(_process_message_image_limited(message.bot, message, settings=settings))
    return True


def _format_reply_visual_status(message: Message) -> str:
    if not ENABLE_VISUAL_FEATURES:
        return "Визуальная обработка временно отключена."
    reply = _find_visual_source_message(message, include_self=False)
    if not reply:
        return "Ответь командой /seen на картинку, GIF или image-файл."

    event = get_image_event_by_message(chat_id=reply.chat.id, message_id=reply.message_id)
    if not event:
        return (
            "Этот файл ещё не изучался.\n"
            "Возможные причины: бот получил его до включения визуальной памяти, обработка отключена или событие ещё не дошло до фоновой задачи."
        )

    status = str(event.get("processing_status") or "unknown")
    file_size = event.get("file_size")
    cluster_id = event.get("cluster_id")
    usage_count = event.get("usage_count")
    cluster_summary = str(event.get("cluster_summary") or "").strip()
    summary_text = str(event.get("summary_text") or "").strip()
    processed_at = str(event.get("processed_at") or "").strip()

    lines = ["Проверка файла:"]
    if status == "processed":
        lines.append("— статус: изучен")
    elif status == "failed":
        lines.append("— статус: не изучен, обработка не удалась или файл был пропущен")
    else:
        lines.append(f"— статус: {_format_visual_processing_stage(status)}")

    if cluster_id:
        lines.append(f"— cluster: #{cluster_id}")
    if usage_count:
        lines.append(f"— повторов в памяти: {usage_count}")
    if file_size:
        lines.append(f"— размер: ~{int(file_size) // 1024} KB")
    if cluster_summary:
        lines.append(f"— описание: {cluster_summary}")
    elif summary_text:
        lines.append(f"— OCR/label: {summary_text}")
    else:
        lines.append("— описание: пока нет")
    if processed_at:
        lines.append(f"— обработан: {processed_at}")

    return "\n".join(lines)


async def _wait_and_refresh_visual_status(
    pending_message: Message,
    started_at: float,
    source_message: Message,
    *,
    initial_text: str,
) -> None:
    await _finish_pending(pending_message, started_at, escape(initial_text))

    deadline = time.monotonic() + VISUAL_CONTEXT_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(VISUAL_CONTEXT_POLL_SECONDS)
        status_text = _format_reply_visual_status(source_message)
        if "изучен" in status_text or "не удалась" in status_text:
            try:
                await pending_message.edit_text(escape(status_text))
            except Exception:
                return
            return
    try:
        await pending_message.edit_text(
            escape(
                "Ладно, спроси позже, я устала.\n"
                "Обработка картинки что-то затянулась."
            )
        )
    except Exception:
        return


async def _get_visual_context_for_ask(message: Message, settings: object) -> str | None:
    if _message_has_visual_file(message):
        return await _wait_for_message_visual_context(message, settings)
    if _reply_has_visual_file(message):
        return await _wait_for_reply_visual_context(message)
    return None


def _build_visual_task_question(question: str, visual_context: str | None, settings: object) -> str:
    if not visual_context:
        return question

    rewrite_model = str(getattr(settings, "llm_chat_model", "") or "").strip()
    rewrite_endpoint = str(getattr(settings, "llm_chat_endpoint", "") or "").strip()
    primary_model = str(getattr(settings, "llm_model", "") or "").strip()
    primary_endpoint = str(getattr(settings, "llm_endpoint", "") or "").strip()
    if not rewrite_model or not rewrite_endpoint:
        return question
    if rewrite_model == primary_model and rewrite_endpoint == primary_endpoint:
        return question

    rewritten = try_prepare_visual_task(
        question,
        backend=getattr(settings, "llm_backend", "llama_cpp"),
        model=rewrite_model,
        endpoint=rewrite_endpoint,
        timeout=float(getattr(settings, "llm_timeout", 10.0)),
    )
    if rewritten:
        logger.info("Visual ask task rewritten by chat model: original=%r rewritten=%r", question[:200], rewritten[:200])
        return rewritten
    return question


def _format_visual_processing_stage(status: str) -> str:
    normalized = (status or "").strip().lower()
    stage_map = {
        "pending": "ожидает обработки (0%)",
        "queued": "в очереди (10%)",
        "downloading": "скачивает файл (30%)",
        "hashing": "считает хэш (50%)",
        "ocr": "распознаёт текст (70%)",
        "clustering": "сопоставляет с визуальной памятью (85%)",
        "processed": "изучен (100%)",
        "failed": "обработка не удалась (100%)",
    }
    return stage_map.get(normalized, normalized or "неизвестно")


def _build_reply_text_context(message: Message, bot_username: str | None, bot_id: int) -> str | None:
    reply = message.reply_to_message
    if not reply or not _is_reply_to_bot(message, bot_username, bot_id):
        return None
    text = (reply.text or reply.caption or "").strip()
    if not text:
        return None
    return text[:1200]


def _build_short_chat_memory(chat_id: int, current_message_id: int, limit: int = SHORT_MEMORY_MESSAGES) -> str | None:
    rows = _get_chat_messages_by_day(date.today(), chat_id)
    memory_rows: list[dict] = []
    for row in reversed(rows):
        if int(row.get("message_id") or 0) == current_message_id:
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        memory_rows.append(row)
        if len(memory_rows) >= limit:
            break

    if not memory_rows:
        return None

    lines: list[str] = []
    for row in reversed(memory_rows):
        text = " ".join(str(row.get("text") or "").split())
        lines.append(f"Сообщение из чата: {text[:300]}")
    return "\n".join(lines)


def _build_question_with_context(
    question: str,
    visual_context: str | None,
    reply_text_context: str | None = None,
    short_memory: str | None = None,
) -> str:
    question = _inject_question_hints(question)
    text_extraction_question = _looks_like_text_extraction_question(question)
    image_comment_question = _looks_like_image_comment_question(question)
    if not visual_context and not reply_text_context and not short_memory:
        return question
    parts = [
        "Пользователь спрашивает в Telegram-чате.",
        "Ответь на русском, коротко и по делу.",
        "Не обращайся к пользователю по имени, если он сам не представился в текущем сообщении.",
        f"Вопрос пользователя: {question}",
    ]
    if visual_context and text_extraction_question:
        parts.append(
            "Пользователь хочет прочитать текст с reply-картинки. "
            "Опирайся только на OCR/label/caption из визуального контекста ниже. "
            "Не используй короткую память чата, если она не относится к изображению."
        )
    elif visual_context and image_comment_question:
        parts.append(
            "Пользователь просит кратко объяснить или прокомментировать картинку. "
            "Дай 1-2 коротких осмысленных предложения по содержимому изображения. "
            "Скажи, что на ней изображено или в чём шутка/смысл, если это понятно по OCR/label/caption. "
            "Не отвечай слишком общими фразами вроде 'это ОИ', 'это картинка' или 'это мем'. "
            "Можно с лёгкой иронией, но без выдумывания деталей, которых нет в OCR/label/caption."
        )
    elif short_memory:
        parts.append(
            "Короткий контекст последних сообщений. Используй его только если он помогает понять вопрос:\n"
            f"{short_memory}"
        )
    if reply_text_context:
        parts.append(f"Сообщение бота, на которое ответил пользователь:\n{reply_text_context}")
    if visual_context:
        parts.append(
            "Если вопрос относится к картинке, используй только сохранённый визуальный контекст ниже. "
            "Не утверждай, что видишь изображение напрямую.\n"
            f"Визуальный контекст изображения:\n{visual_context}"
        )
    return "\n\n".join(parts)


def _looks_like_text_extraction_question(question: str) -> bool:
    lowered = " ".join(question.lower().split())
    markers = (
        "что тут написано",
        "что написано",
        "какой текст",
        "прочитай текст",
        "что за текст",
        "что на скрине написано",
        "что на картинке написано",
        "прочитай",
        "ocr",
        "текст с картинки",
        "текст на картинке",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_image_comment_question(question: str) -> bool:
    lowered = " ".join(question.lower().split())
    markers = (
        "что тут",
        "что это",
        "что за",
        "что тут за",
        "чо тут",
        "чо это",
        "что на картинке",
        "что на фото",
        "что изображено",
        "что нарисовано",
        "что за мем",
        "в чем прикол",
        "в чём прикол",
        "прикол в чем",
        "объясни мем",
        "объясни картинку",
        "прокомментируй",
        "комментани",
        "что сказать",
        "что происходит",
    )
    return any(marker in lowered for marker in markers) and not _looks_like_text_extraction_question(question)


def _inject_question_hints(question: str) -> str:
    normalized = " ".join(question.lower().split())
    if "норвуд" in normalized and "шкал" not in normalized:
        return f"{question}\n(Подсказка: вероятнее всего речь про шкалу Норвуда для стадий облысения.)"
    return question


def _normalize_llm_text_block(text: str, max_lines: int = 3) -> str:
    lines: list[str] = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        line = line.replace("**", "").replace("__", "").replace("`", "")
        for prefix in ("- ", "— ", "• ", "* ", ". "):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
        if line and line[0].isdigit() and len(line) > 2 and line[1] in {".", ")"}:
            line = line[2:].strip()
        if line:
            lines.append(line)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines) if lines else text.strip()


def _call_llm_with_chat_fallback(
    generator: object,
    *,
    settings: object,
    primary_kwargs: dict[str, object],
) -> str | None:
    result = generator(
        **primary_kwargs,
        backend=getattr(settings, "llm_backend", "llama_cpp"),
        model=getattr(settings, "llm_model", ""),
        endpoint=getattr(settings, "llm_endpoint", ""),
        timeout=float(getattr(settings, "llm_timeout", 10.0)),
    )
    if result is not None:
        return result

    rewrite_model = str(getattr(settings, "llm_chat_model", "") or "").strip()
    rewrite_endpoint = str(getattr(settings, "llm_chat_endpoint", "") or "").strip()
    primary_model = str(getattr(settings, "llm_model", "") or "").strip()
    primary_endpoint = str(getattr(settings, "llm_endpoint", "") or "").strip()
    if not rewrite_model or not rewrite_endpoint:
        return None
    if rewrite_model == primary_model and rewrite_endpoint == primary_endpoint:
        return None

    logger.info(
        "Primary LLM command failed; retrying with chat model generator=%s rewrite_model=%s rewrite_endpoint=%s",
        getattr(generator, "__name__", type(generator).__name__),
        rewrite_model,
        rewrite_endpoint,
    )
    return generator(
        **primary_kwargs,
        backend=getattr(settings, "llm_backend", "llama_cpp"),
        model=rewrite_model,
        endpoint=rewrite_endpoint,
        timeout=float(getattr(settings, "llm_timeout", 10.0)),
    )


def _maybe_rewrite_chat_answer(answer: str, settings: object) -> str:
    primary_endpoint = str(getattr(settings, "llm_endpoint", "") or "").strip()
    primary_model = str(getattr(settings, "llm_model", "") or "").strip()
    rewrite_endpoint = str(getattr(settings, "llm_chat_endpoint", "") or "").strip()
    rewrite_model = str(getattr(settings, "llm_chat_model", "") or "").strip()

    if not rewrite_endpoint or not rewrite_model:
        logger.info("LLM chat rewrite skipped: rewrite endpoint/model is empty")
        return answer
    if rewrite_endpoint == primary_endpoint and rewrite_model == primary_model:
        logger.info("LLM chat rewrite skipped: rewrite model matches primary model")
        return answer

    logger.info(
        "LLM chat rewrite attempt: primary_model=%s primary_endpoint=%s rewrite_model=%s rewrite_endpoint=%s",
        primary_model,
        primary_endpoint,
        rewrite_model,
        rewrite_endpoint,
    )
    rewritten = try_rewrite_assistant_answer(
        answer,
        backend=getattr(settings, "llm_backend", "llama_cpp"),
        model=rewrite_model,
        endpoint=rewrite_endpoint,
        timeout=float(getattr(settings, "llm_timeout", 10.0)),
    )
    if rewritten:
        if _looks_like_rewrite_refusal(rewritten):
            logger.info("LLM chat rewrite returned refusal/meta text, using primary answer")
            return answer
        logger.info("LLM chat rewrite applied successfully")
        return rewritten
    logger.info("LLM chat rewrite failed, using primary answer")
    return answer


def _looks_like_rewrite_refusal(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    refusal_markers = (
        "извините, но я не могу",
        "не могу переписать",
        "не имею доступа",
        "не могу помочь с этим",
        "я должен сообщить",
        "я должен объявить",
        "i must inform",
        "i should disclose",
        "как языковая модель",
        "как ии",
        "как модель",
        "as an ai",
        "as a language model",
    )
    return any(marker in lowered for marker in refusal_markers)


def _sanitize_chat_answer(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        line = line.replace("**", "").replace("__", "").replace("`", "").strip()
        if re.match(r"^\*?\s*attempt\s*\*?$", line, flags=re.IGNORECASE):
            continue
        line = re.sub(r"^\s*(input message|message|context)\s*:\s*", "", line, flags=re.IGNORECASE)
        if _looks_like_meta_llm_line(line):
            continue
        if line:
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def _looks_like_meta_llm_line(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    markers = (
        "thinking process",
        "reasoning:",
        "chain of thought",
        "since i cannot see the photo",
        "i cannot see the photo",
        "i cannot see the image",
        "cannot view the image",
        "i cannot view images",
        "i can't view images",
        "i do not have access",
        "i should disclose",
        "i must inform",
        "as an ai language model",
        "as a language model",
        "as an ai",
        "как ии",
        "как языковая модель",
        "я должен объявить",
        "я должен сообщить",
        "я обязан сообщить",
        "не имею доступа",
        "я не вижу фото",
        "я не вижу изображение",
        "я не могу видеть изображение",
        "я ее не могу видеть",
        "я её не могу видеть",
        "я не могу просматривать изображения",
        "не могу увидеть фото",
    )
    return any(marker in lowered for marker in markers)


def _asks_for_photo_upload(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    markers = (
        "пришлите картинку",
        "пришли картинку",
        "пришли фото",
        "пришлите фото",
        "скинь фото",
        "скидывай фото",
        "отправь фото",
        "фото не прислано",
        "фото не отправлено",
        "изображение не приложено",
        "картинка не приложена",
        "send the image",
        "send me the photo",
    )
    if any(marker in lowered for marker in markers):
        return True
    return bool(re.search(r"\bне вижу\b.*\b(фото|изображ|картин)\b", lowered))


def _is_usable_chat_answer(
    text: str,
    *,
    looks_like_photo_question: bool,
    has_visual_context: bool,
) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    if _looks_like_meta_llm_line(normalized) or _looks_like_rewrite_refusal(normalized):
        return False
    if looks_like_photo_question and has_visual_context and _asks_for_photo_upload(normalized):
        return False
    return True


def _looks_like_photo_question(question: str) -> bool:
    lowered = " ".join(question.lower().split())
    photo_markers = ("фото", "фотке", "картинк", "изображен", "скрин", "на снимке", "на фото")
    return any(marker in lowered for marker in photo_markers)


def _chat_fallback_answer(*, looks_like_photo_question: bool, has_visual_context: bool) -> str:
    if looks_like_photo_question and has_visual_context:
        return "По фото пока не очень понятно, уточни что именно разобрать."
    if looks_like_photo_question:
        return "По фото пока мало понятно, опиши что именно важно."
    return "Сформулируй вопрос чуть точнее, и я отвечу."


def _finalize_chat_answer(
    primary_answer: str,
    *,
    question: str,
    has_visual_context: bool,
    settings: object,
) -> str:
    looks_like_photo_question = _looks_like_photo_question(question)
    sanitized_primary = _sanitize_chat_answer(primary_answer)
    if _is_usable_chat_answer(
        sanitized_primary,
        looks_like_photo_question=looks_like_photo_question,
        has_visual_context=has_visual_context,
    ):
        return sanitized_primary

    rewritten = _maybe_rewrite_chat_answer(primary_answer, settings)
    sanitized_rewritten = _sanitize_chat_answer(rewritten)
    if _is_usable_chat_answer(
        sanitized_rewritten,
        looks_like_photo_question=looks_like_photo_question,
        has_visual_context=has_visual_context,
    ):
        return sanitized_rewritten

    return _chat_fallback_answer(
        looks_like_photo_question=looks_like_photo_question,
        has_visual_context=has_visual_context,
    )


async def _safe_delete_command_message(bot: Bot, message: Message) -> None:
    text = (message.text or "").strip()
    if not text.startswith("/"):
        return
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        return


async def _process_message_image_limited(bot: Bot, message: Message, settings: object) -> None:
    if _image_processing_semaphore is None:
        await process_message_image(bot, message, settings=settings)
        return

    async with _image_processing_semaphore:
        await process_message_image(bot, message, settings=settings)


async def _send_pending(message: Message, *, reply: bool = False) -> tuple[Message, float]:
    started_at = time.monotonic()
    if reply:
        return await message.reply(PENDING_TEXT, disable_notification=True), started_at
    return await message.answer(PENDING_TEXT, disable_notification=True), started_at


async def _finish_pending(pending_message: Message, started_at: float, text: str) -> None:
    elapsed = time.monotonic() - started_at
    if elapsed < PENDING_MIN_SECONDS:
        await asyncio.sleep(PENDING_MIN_SECONDS - elapsed)
    await pending_message.edit_text(text)


def _format_visual_memory(limit: int = 8) -> str:
    if not ENABLE_VISUAL_FEATURES:
        return "Визуальная память временно отключена."
    clusters = get_top_image_clusters(limit=limit)
    if not clusters:
        return "Визуальная память пока пустая."

    lines = ["Визуальная память:"]
    for cluster in clusters:
        cluster_id = int(cluster["cluster_id"])
        usage_count = int(cluster["usage_count"])
        summary = str(cluster.get("cluster_summary") or "").strip()
        label = summary if summary else "пока без описания"
        lines.append(f"— #{cluster_id}: {label} ({usage_count} раз)")
    return "\n".join(lines)


async def main() -> None:
    global _image_processing_semaphore, ENABLE_VISUAL_FEATURES
    settings = get_settings()
    ENABLE_VISUAL_FEATURES = IMAGE_PIPELINE_AVAILABLE and settings.enable_image_processing

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    init_db(settings.db_path)
    _image_processing_semaphore = asyncio.Semaphore(1)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=settings.bot_parse_mode),
    )
    bot_info = await bot.get_me()
    logger.info(
        "Bot runtime config: username=@%s id=%s use_llm_topics=%s enable_image_processing=%s visual_features=%s image_pipeline_available=%s llm_endpoint=%s llm_chat_endpoint=%s",
        bot_info.username,
        bot_info.id,
        settings.use_llm_topics,
        settings.enable_image_processing,
        ENABLE_VISUAL_FEATURES,
        IMAGE_PIPELINE_AVAILABLE,
        settings.llm_endpoint,
        settings.llm_chat_endpoint,
    )
    dp = Dispatcher()
    router = Router()

    @router.message(Command("stats"))
    async def on_stats(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        _log_incoming_command(message)
        await _safe_delete_command_message(bot, message)

        pending_message, pending_started = await _send_pending(message)
        try:
            today = date.today()
            messages = _get_chat_messages_by_day(today, settings.allowed_chat_id)
            user_names = build_user_names(messages)

            stats = calculate_daily_stats(messages)
            topics, character, used_llm_topics = build_topics_and_summary_lines(
                messages=messages,
                stats=stats,
                user_names=user_names,
                settings=settings,
                return_meta=True,
            )

            report_text = format_daily_report(
                report_date=today,
                stats=stats,
                topics=topics,
                character_phrases=character,
                user_names=user_names,
                title_prefix="📊 На данный момент за",
            )
            if settings.use_llm_topics and stats.total_messages > 0 and not used_llm_topics:
                report_text = f"{report_text}\n\nпу-пу-пу технические шоколадки"
            await _finish_pending(pending_message, pending_started, report_text)
        except Exception:
            logger.exception("Failed to build /stats response")
            await _finish_pending(
                pending_message,
                pending_started,
                "Не смогла собрать /stats, используй ещё раз через пару секунд.",
            )

    @router.message(Command("dead"))
    async def on_dead(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        await _safe_delete_command_message(bot, message)

        pending_message, pending_started = await _send_pending(message)
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
        await _finish_pending(pending_message, pending_started, text)

    @router.message(Command("time"))
    async def on_time(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        await _safe_delete_command_message(bot, message)

        pending_message, pending_started = await _send_pending(message)
        messages_count = len(_get_chat_messages_by_day(date.today(), settings.allowed_chat_id))
        text = (
            "⏳ Сегодня:\n\n"
            f"— сообщений: {messages_count}\n"
            f"— примерное время: ~{_estimate_minutes(messages_count)} минут"
        )
        await _finish_pending(pending_message, pending_started, text)

    @router.message(Command("when"))
    async def on_when(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        await _safe_delete_command_message(bot, message)

        pending_message, pending_started = await _send_pending(message)
        messages = _get_chat_messages_by_day(date.today(), settings.allowed_chat_id)
        hourly = activity_by_hour(messages)
        peak_hour, peak_count = max(hourly.items(), key=lambda item: item[1])
        quiet_hour, _ = min(hourly.items(), key=lambda item: item[1])

        text = (
            "⏰ Пик активности:\n\n"
            f"— {_format_hour_window(peak_hour)} ({peak_count} сообщений)\n\n"
            "📉 Самое тихое время:\n"
            f"— {_format_hour_window(quiet_hour)}"
        )
        await _finish_pending(pending_message, pending_started, text)

    @router.message(Command("mood"))
    async def on_mood(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        _log_incoming_command(message)
        await _safe_delete_command_message(bot, message)

        pending_message, pending_started = await _send_pending(message)
        messages = _get_chat_messages_by_day(date.today(), settings.allowed_chat_id)
        mood = None
        if settings.use_llm_topics:
            mood = _call_llm_with_chat_fallback(
                try_generate_mood_summary,
                settings=settings,
                primary_kwargs={
                    "messages": _build_topics_source(messages),
                },
            )
        if mood is None:
            mood = _fallback_mood(len(messages))
        await _finish_pending(pending_message, pending_started, escape(_normalize_llm_text_block(mood, max_lines=3)))

    @router.message(Command("svin"))
    async def on_svin(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        _log_incoming_command(message)
        await _safe_delete_command_message(bot, message)

        pending_message, pending_started = await _send_pending(message)
        joke = None
        if settings.use_llm_topics:
            joke = _call_llm_with_chat_fallback(
                try_generate_svin_joke,
                settings=settings,
                primary_kwargs={},
            )
        if joke is not None:
            await _finish_pending(pending_message, pending_started, escape(joke))
            return
        await _finish_pending(pending_message, pending_started, "Не смогла придумать нормальный анекдот.")

    @router.message(Command("ask"))
    async def on_ask(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        _log_incoming_command(message)

        question = _extract_question_or_visual_default(message)
        reply_text_context = _build_reply_text_context(message, bot_info.username, bot_info.id)
        if not question and reply_text_context:
            question = "продолжи предыдущую мысль и уточни ответ"
        if not question:
            await message.answer("Напиши вопрос после команды: /ask что спросить", disable_notification=True)
            return
        if not settings.use_llm_topics:
            await message.answer("LLM сейчас выключена.", disable_notification=True)
            return

        visual_context = await _get_visual_context_for_ask(message, settings)
        if (_reply_has_visual_file(message) or _message_has_visual_file(message)) and visual_context is None:
            await message.answer("Визуальный контекст этой картинки ещё не готов.", disable_notification=True)
            return

        pending_message, pending_started = await _send_pending(message)
        short_memory = _build_short_chat_memory(message.chat.id, message.message_id)
        normalized_question = _normalize_reply_question(question, reply_text_context)
        llm_question = _build_visual_task_question(normalized_question, visual_context, settings)
        answer = try_answer_question(
            _build_question_with_context(
                llm_question,
                visual_context,
                reply_text_context=reply_text_context,
                short_memory=short_memory,
            ),
            backend=settings.llm_backend,
            model=settings.llm_model,
            endpoint=settings.llm_endpoint,
            timeout=settings.llm_timeout,
        )
        if answer is None:
            await _finish_pending(pending_message, pending_started, "Не смогла получить ответ от LLM.")
            return
        final_answer = _finalize_chat_answer(
            answer,
            question=normalized_question,
            has_visual_context=bool(visual_context),
            settings=settings,
        )
        await _finish_pending(pending_message, pending_started, escape(final_answer))

    @router.message(Command("visual"))
    async def on_visual(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        _log_incoming_command(message)
        await _safe_delete_command_message(bot, message)

        pending_message, pending_started = await _send_pending(message)
        await _finish_pending(pending_message, pending_started, escape(_format_visual_memory()))

    @router.message(Command("seen"))
    async def on_seen(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        _log_incoming_command(message)
        await _safe_delete_command_message(bot, message)

        scheduled = await _ensure_reply_visual_processing(message)
        pending_message, pending_started = await _send_pending(message)
        status_text = _format_reply_visual_status(message)
        if scheduled:
            status_text = (
                "Запустила обработку reply-картинки.\n"
                "Сейчас сама обновлю статус, если обработка успеет закончиться.\n\n"
                f"{status_text}"
            )
            await _wait_and_refresh_visual_status(
                pending_message,
                pending_started,
                message,
                initial_text=status_text,
            )
            return
        await _finish_pending(pending_message, pending_started, escape(status_text))

    @router.message(Command("chance"))
    async def on_chance(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        _log_incoming_command(message)
        await _safe_delete_command_message(bot, message)

        global _svin_reply_chance
        args = _extract_command_args(message).strip().lower().replace(",", ".")
        step = 0.01

        if not args:
            await message.answer(
                f"Текущий шанс случайного комментария: {_svin_reply_chance * 100:.1f}%",
                disable_notification=True,
            )
            return

        if args in {"+", "up", "more", "plus"}:
            _svin_reply_chance = min(1.0, _svin_reply_chance + step)
        elif args in {"-", "down", "less", "minus"}:
            _svin_reply_chance = max(0.0, _svin_reply_chance - step)
        else:
            try:
                raw = float(args)
                _svin_reply_chance = raw / 100.0 if raw > 1 else raw
                _svin_reply_chance = max(0.0, min(1.0, _svin_reply_chance))
            except ValueError:
                await message.answer(
                    "Используй: /chance, /chance +, /chance -, /chance 7",
                    disable_notification=True,
                )
                return

        await message.answer(
            f"Шанс случайного комментария: {_svin_reply_chance * 100:.1f}%\n"
            "Значение действует до перезапуска бота.",
            disable_notification=True,
        )

    @router.message(Command("llmroute"))
    async def on_llmroute(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        _log_incoming_command(message)
        await _safe_delete_command_message(bot, message)

        primary = f"{settings.llm_model or '-'} @ {settings.llm_endpoint or '-'}"
        rewrite = f"{settings.llm_chat_model or '-'} @ {settings.llm_chat_endpoint or '-'}"
        rewrite_enabled = (
            bool(settings.llm_chat_model and settings.llm_chat_endpoint)
            and not (
                settings.llm_chat_model == settings.llm_model
                and settings.llm_chat_endpoint == settings.llm_endpoint
            )
        )
        text = (
            "🧠 Текущий LLM маршрут:\n"
            f"• primary: {primary}\n"
            f"• rewrite: {rewrite}\n"
            f"• rewrite_enabled: {'yes' if rewrite_enabled else 'no'}"
        )
        await message.answer(escape(text), disable_notification=True)

    @router.message(Command("roast"))
    async def on_roast(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        _log_incoming_command(message)
        await _safe_delete_command_message(bot, message)

        target = _extract_command_args(message).strip()
        if not target:
            await message.answer("Используй: /roast @username", disable_notification=True)
            return
        if not settings.use_llm_topics:
            await message.answer("LLM сейчас выключена.", disable_notification=True)
            return

        pending_message, pending_started = await _send_pending(message)
        roast = _call_llm_with_chat_fallback(
            try_generate_roast,
            settings=settings,
            primary_kwargs={
                "target": target,
            },
        )
        if roast is None:
            await _finish_pending(pending_message, pending_started, "Не смогла сделать прожарку, попробуй ещё раз.")
            return
        await _finish_pending(pending_message, pending_started, escape(_normalize_llm_text_block(roast, max_lines=2)))

    @router.message()
    async def on_message(message: Message) -> None:
        if message.chat.id != settings.allowed_chat_id:
            return
        if _extract_command_text(message):
            _log_incoming_command(message)
        if message.text and message.text.strip().lower().startswith((
            "/stats",
            "/svin",
            "/dead",
            "/time",
            "/when",
            "/mood",
            "/ask",
            "/visual",
            "/seen",
            "/chance",
            "/llmroute",
            "/roast",
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

        if ENABLE_VISUAL_FEATURES and IMAGE_PIPELINE_AVAILABLE and settings.enable_image_processing and get_image_file_id(message) is not None:
            asyncio.create_task(_process_message_image_limited(bot, message, settings=settings))

        global _last_cleanup_ts, _messages_since_svin_reply
        _messages_since_svin_reply += 1
        now_ts = time.time()
        if now_ts - _last_cleanup_ts >= 3600:
            delete_messages_older_than(7)
            _last_cleanup_ts = now_ts

        addressed_to_bot = _is_addressed_to_bot(message, bot_info.username, bot_info.id)
        if message.reply_to_message:
            reply_user = message.reply_to_message.from_user
            logger.info(
                "Reply detected: addressed_to_bot=%s use_llm_topics=%s reply_user_id=%s reply_username=%s reply_is_bot=%s",
                addressed_to_bot,
                settings.use_llm_topics,
                reply_user.id if reply_user else None,
                reply_user.username if reply_user else None,
                getattr(reply_user, "is_bot", None) if reply_user else None,
            )

        if addressed_to_bot:
            if not settings.use_llm_topics:
                await message.reply("LLM сейчас выключена.", disable_notification=True)
                return

            question_raw = _strip_bot_mention(text_content, bot_info.username)
            reply_text_context = _build_reply_text_context(message, bot_info.username, bot_info.id)
            question = _normalize_reply_question(question_raw, reply_text_context)
            if question.strip():
                visual_context = await _wait_for_reply_visual_context(message)
                short_memory = _build_short_chat_memory(message.chat.id, message.message_id)
                if _reply_has_visual_file(message) and visual_context is None:
                    await message.reply("Визуальный контекст этой картинки ещё не готов.", disable_notification=True)
                    return

                pending_message, pending_started = await _send_pending(message, reply=True)
                answer = try_answer_question(
                    _build_question_with_context(question, visual_context, reply_text_context, short_memory),
                    backend=settings.llm_backend,
                    model=settings.llm_model,
                    endpoint=settings.llm_endpoint,
                    timeout=settings.llm_timeout,
                )
                if answer is not None:
                    final_answer = _finalize_chat_answer(
                        answer,
                        question=question,
                        has_visual_context=bool(visual_context),
                        settings=settings,
                    )
                    await _finish_pending(pending_message, pending_started, escape(final_answer))
                    return
                await _finish_pending(pending_message, pending_started, "Не смогла получить ответ от LLM.")
                return

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
                await message.reply(escape(comment), disable_notification=True)
                _messages_since_svin_reply = 0

    dp.include_router(router)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        close_db()


if __name__ == "__main__":
    asyncio.run(main())
