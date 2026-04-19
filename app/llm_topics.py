from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

MAX_MESSAGES = 120
MAX_INPUT_CHARS = 6000

SYSTEM_PROMPT = (
    "Ты анализируешь переписку на русском языке.\n"
    "Верни только JSON без markdown и без пояснений.\n"
    "Формат строго:\n"
    '{\n  "topics": ["..."],\n  "summary_lines": ["..."]\n}\n'
    'Требования: "topics" содержит 2-4 короткие темы. '
    '"summary_lines" содержит 1-2 короткие фразы о характере обсуждения.'
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
                    "Сообщения чата:\n"
                    f"{prompt_input}\n\n"
                    "Верни JSON с полями topics и summary_lines."
                ),
            },
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    try:
        raw_content = _call_llama_cpp(endpoint=endpoint, payload=payload, timeout=timeout)
        parsed = _parse_response_json(raw_content)
        if parsed is None:
            return None
        topics, summary_lines = parsed
        if not topics or not summary_lines:
            return None
        return topics, summary_lines
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        logger.exception("LLM topics request failed")
        return None


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
