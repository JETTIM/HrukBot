from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

MAX_MESSAGES = 500
MAX_INPUT_CHARS = 3500

SYSTEM_PROMPT = (
    "Ты анализируешь переписку Telegram-чата.\n"
    "Отвечай только на русском языке.\n"
    "Верни только JSON без markdown, без пояснений и без лишнего текста.\n"
    "Формат ответа строго такой:\n"
    '{\n'
    '  "topics": ["..."],\n'
    '  "summary_lines": ["..."]\n'
    '}\n'
    'Требования:\n'
    '- "topics": 2-5 короткие темы обсуждения;\n'
    '- "summary_lines": несколько предложений о характере обсуждения;\n'
    '- не придумывай темы, которых нет в сообщениях;\n'
    '- не пересказывай диалог подробно;\n'
    '- формулируй темы коротко и естественно.'
)


def try_extract_topics_and_summary(
    messages: Sequence[str],
    *,
    backend: str,
    model: str,
    endpoint: str,
    timeout: float,
) -> tuple[list[str], list[str]] | None:
    if backend.strip().lower() != "llama_cpp":
        logger.warning("Unsupported LLM backend: %s", backend)
        return None

    prompt_input = _build_corpus(messages)
    if not prompt_input:
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Ниже сообщения Telegram-чата за день.\n"
                    "Нужно выделить 2-5 основные темы обсуждения и нескольк предложений "
                    "для блока 'Характер обсуждения'.\n"
                    "Ответ верни строго в JSON с полями topics и summary_lines.\n\n"
                    "Сообщения:\n"
                    f"{prompt_input}"
                ),
            },
        ],
        "temperature": 0.3,
        "max_tokens": 700,
    }

    try:
        raw_content = _call_llama_cpp(
            endpoint=endpoint,
            payload={**payload, "response_format": {"type": "json_object"}},
            timeout=timeout,
        )
    except HTTPError as exc:
        if exc.code not in {400, 422}:
            logger.exception("LLM topics request failed: %s", _read_http_error(exc))
            return None
        logger.warning(
            "LLM endpoint rejected response_format, retrying without it: %s",
            _read_http_error(exc),
        )
        try:
            raw_content = _call_llama_cpp(endpoint=endpoint, payload=payload, timeout=timeout)
        except HTTPError as retry_exc:
            logger.exception("LLM topics retry failed: %s", _read_http_error(retry_exc))
            return None
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            logger.exception("LLM topics retry failed")
            return None

    try:
        parsed = _parse_response_json(raw_content)
        if parsed is None:
            logger.warning("LLM returned invalid topics JSON shape")
            return None
        topics, summary_lines = parsed
        if not topics or not summary_lines:
            logger.warning("LLM returned empty topics or summary")
            return None
        return topics, summary_lines
    except (ValueError, json.JSONDecodeError):
        logger.exception("LLM topics request failed")
        return None


def try_generate_svin_joke(
    *,
    backend: str,
    model: str,
    endpoint: str,
    timeout: float,
) -> str | None:
    if backend.strip().lower() != "llama_cpp":
        logger.warning("Unsupported LLM backend: %s", backend)
        return None

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты пишешь короткие русские анекдоты с понятной структурой: завязка и панчлайн. "
                    "Юмор должен быть бытовым, естественным и связным. "
                    "Без абсурда, случайных предметов, политики, грязной брани и оскорблений реальных людей."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Сочини один короткий нормальный анекдот про свинью на русском языке. "
                    "2-4 предложения. Обязательно должен быть понятный смешной финал. "
                    "Не используй слова: картофель, мясо, рынок. "
                    "Ответь только текстом анекдота."
                ),
            },
        ],
        "temperature": 0.55,
        "max_tokens": 180,
    }

    try:
        joke = _call_llama_cpp(endpoint=endpoint, payload=payload, timeout=timeout)
    except HTTPError as exc:
        logger.exception("LLM svin joke request failed: %s", _read_http_error(exc))
        return None
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        logger.exception("LLM svin joke request failed")
        return None

    return _clean_plain_text(joke, max_chars=900)


def _clean_plain_text(text: str, *, max_chars: int) -> str | None:
    cleaned = _strip_markdown_fence(text).strip().strip('"')
    if not cleaned:
        return None
    if _looks_like_bad_joke(cleaned):
        logger.warning("LLM svin joke rejected by simple quality filter")
        return None
    return cleaned[:max_chars]


def _looks_like_bad_joke(text: str) -> bool:
    lowered = text.lower()
    banned_fragments = ("картоф", "мяс", "рынд", "рынок")
    if any(fragment in lowered for fragment in banned_fragments):
        return True
    return len(text.split()) < 12


def _read_http_error(exc: HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except OSError:
        return f"HTTP {exc.code}"


def _call_llama_cpp(*, endpoint: str, payload: dict[str, Any], timeout: float) -> str:
    req = Request(
        url=endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as response:
        response_body = response.read().decode("utf-8")

    response_data = json.loads(response_body)
    choices = response_data.get("choices")
    if not choices:
        raise ValueError("No choices in LLM response")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Empty content in LLM response")
    return content.strip()


def _parse_response_json(raw_content: str) -> tuple[list[str], list[str]] | None:
    json_text = _strip_markdown_fence(raw_content)
    data = json.loads(json_text)

    topics_raw = data.get("topics")
    summary_raw = data.get("summary_lines")
    if not isinstance(topics_raw, list) or not isinstance(summary_raw, list):
        return None

    topics = _clean_lines(topics_raw, min_items=2, max_items=4)
    summary_lines = _clean_lines(summary_raw, min_items=1, max_items=2)
    if topics is None or summary_lines is None:
        return None
    return topics, summary_lines


def _clean_lines(items: list[Any], *, min_items: int, max_items: int) -> list[str] | None:
    cleaned: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value:
            continue
        if value not in cleaned:
            cleaned.append(value)
        if len(cleaned) >= max_items:
            break

    if len(cleaned) < min_items:
        return None
    return cleaned


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _build_corpus(messages: Sequence[str]) -> str:
    chunks: list[str] = []
    total_chars = 0
    for raw in messages[-MAX_MESSAGES:]:
        value = raw.strip()
        if not value:
            continue
        if total_chars + len(value) > MAX_INPUT_CHARS:
            remain = MAX_INPUT_CHARS - total_chars
            if remain <= 0:
                break
            chunks.append(value[:remain])
            break
        chunks.append(value)
        total_chars += len(value)
    return "\n".join(chunks)
