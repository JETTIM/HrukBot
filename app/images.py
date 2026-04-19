from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import imagehash
from aiogram import Bot
from aiogram.types import Message
from PIL import Image

from app.db import (
    create_image_cluster,
    create_image_event,
    get_image_cluster,
    get_image_cluster_context,
    get_image_clusters,
    update_image_cluster_usage,
    update_image_cluster_summary,
    update_image_event_failed,
    update_image_event_processed,
)
from app.llm_topics import try_generate_image_cluster_summary

logger = logging.getLogger(__name__)

HASH_DISTANCE_THRESHOLD = 8

try:
    import pytesseract
except ImportError:
    pytesseract = None


def get_image_file_id(message: Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id
    if message.document and message.document.mime_type:
        if message.document.mime_type.startswith("image/"):
            return message.document.file_id
    return None


async def process_message_image(bot: Bot, message: Message, settings: object | None = None) -> None:
    file_id = get_image_file_id(message)
    if file_id is None:
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="telegram-stats-image-"))
    file_deleted = False
    event_id: int | None = None
    local_path = temp_dir / f"{message.message_id}.image"

    try:
        event_id = create_image_event(
            message_id=message.message_id,
            chat_id=message.chat.id,
            file_id=file_id,
            created_at=message.date,
            local_path=str(local_path),
        )

        telegram_file = await bot.get_file(file_id)
        await bot.download_file(telegram_file.file_path, destination=local_path)

        phash = calculate_phash(local_path)
        ocr_text = extract_ocr_text(local_path)
        summary_text = build_image_summary(ocr_text)
        cluster_id = find_or_create_cluster(
            phash=phash,
            summary_text=summary_text,
            seen_at=message.date,
        )

        file_deleted = delete_temp_path(temp_dir)
        update_image_event_processed(
            event_id=event_id,
            phash=phash,
            cluster_id=cluster_id,
            ocr_text=ocr_text,
            summary_text=summary_text,
            processed_at=datetime.utcnow(),
            file_deleted=file_deleted,
        )
        maybe_update_cluster_summary(cluster_id=cluster_id, settings=settings)
    except Exception:
        logger.exception("Image processing failed")
        file_deleted = delete_temp_path(temp_dir)
        if event_id is not None:
            update_image_event_failed(
                event_id=event_id,
                processed_at=datetime.utcnow(),
                file_deleted=file_deleted,
            )


def calculate_phash(path: Path) -> str:
    with Image.open(path) as image:
        return str(imagehash.phash(image))


def extract_ocr_text(path: Path) -> str | None:
    if pytesseract is None:
        return None
    try:
        with Image.open(path) as image:
            text = pytesseract.image_to_string(image, lang="rus+eng").strip()
    except Exception:
        logger.exception("OCR failed")
        return None
    return text or None


def build_image_summary(ocr_text: str | None) -> str | None:
    if not ocr_text:
        return None
    compact = " ".join(ocr_text.split())
    return compact[:160]


def find_or_create_cluster(*, phash: str, summary_text: str | None, seen_at: datetime) -> int:
    best_cluster_id: int | None = None
    best_distance: int | None = None

    for cluster in get_image_clusters():
        distance = hash_distance(phash, str(cluster["canonical_hash"]))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_cluster_id = int(cluster["cluster_id"])

    if best_cluster_id is not None and best_distance is not None:
        if best_distance <= HASH_DISTANCE_THRESHOLD:
            update_image_cluster_usage(cluster_id=best_cluster_id, seen_at=seen_at)
            return best_cluster_id

    return create_image_cluster(
        canonical_hash=phash,
        cluster_summary=summary_text,
        seen_at=seen_at,
    )


def maybe_update_cluster_summary(*, cluster_id: int, settings: object | None) -> None:
    if settings is None or not getattr(settings, "use_llm_topics", False):
        return

    cluster = get_image_cluster(cluster_id)
    if not cluster:
        return

    usage_count = int(cluster.get("usage_count") or 0)
    current_summary = str(cluster.get("cluster_summary") or "").strip()
    if usage_count < 2:
        return
    if current_summary and usage_count not in {3, 5, 10, 20, 50}:
        return

    context_lines = build_cluster_context_lines(get_image_cluster_context(cluster_id))
    summary = try_generate_image_cluster_summary(
        context_lines,
        backend=getattr(settings, "llm_backend", "llama_cpp"),
        model=getattr(settings, "llm_model", ""),
        endpoint=getattr(settings, "llm_endpoint", ""),
        timeout=getattr(settings, "llm_timeout", 10),
    )
    if summary:
        update_image_cluster_summary(cluster_id=cluster_id, cluster_summary=summary)


def build_cluster_context_lines(rows: list[dict]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        parts: list[str] = []
        message_text = str(row.get("message_text") or "").strip()
        ocr_text = str(row.get("ocr_text") or "").strip()
        if message_text:
            parts.append(f"caption/context: {message_text[:220]}")
        if ocr_text:
            parts.append(f"ocr: {' '.join(ocr_text.split())[:220]}")
        if parts:
            lines.append("; ".join(parts))
    return lines


def hash_distance(left_hash: str, right_hash: str) -> int:
    return bin(int(left_hash, 16) ^ int(right_hash, 16)).count("1")


def delete_temp_path(path: Path) -> bool:
    try:
        if path.exists():
            shutil.rmtree(path)
        return True
    except OSError:
        logger.exception("Failed to delete temporary image path")
        return False
