from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

MAX_MESSAGES = 500
MAX_INPUT_CHARS = 3500
MIN_INPUT_CHARS = 700
MIN_MAX_TOKENS = 48
TOPICS_INPUT_CHARS = 1200
TOPICS_MESSAGE_CHARS = 90

SYSTEM_PROMPT = (
    "Ты анализируешь переписку Telegram-чата.\n"
    "Отвечай только на русском языке.\n"
    "Верни только JSON без markdown, без пояснений и без лишнего текста.\n"
    "Формат ответа строго такой:\n"
    "{\n"
    "  \"topics\": [\"...\"],\n"
    "  \"summary_lines\": [\"...\"]\n"
    "}\n"
    "Требования:\n"
    "- topics: 2-5 коротких темы обсуждения;\n"
    "- summary_lines: 1-2 короткие фразы о характере обсуждения;\n"
    "- не придумывай темы, которых нет в сообщениях;\n"
    "- формулируй коротко и естественно."
)

TOPICS_SYSTEM_PROMPT = (
    "Проанализируй чат. "
    "Ответь только JSON вида "
    '{"topics":["..."],"summary_lines":["..."]}. '
    "Пиши кратко по-русски."
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

    prompt_input = _build_topics_corpus(messages)
    if not prompt_input:
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": TOPICS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_topics_user_prompt(prompt_input),
            },
        ],
        "temperature": 0.3,
        "max_tokens": 160,
    }
    logger.info(
        "LLM topics request: source_items=%s source_chars=%s prompt_chars=%s max_tokens=%s model=%s endpoint=%s",
        len(messages),
        sum(len(str(item)) for item in messages),
        len(prompt_input),
        payload.get("max_tokens"),
        model,
        endpoint,
    )

    try:
        raw_content = _call_llama_cpp_with_retries(
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
            raw_content = _call_llama_cpp_with_retries(endpoint=endpoint, payload=payload, timeout=timeout)
        except HTTPError as retry_exc:
            logger.exception("LLM topics retry failed: %s", _read_http_error(retry_exc))
            return None
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            logger.exception("LLM topics retry failed")
            return None
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        logger.exception("LLM topics request failed")
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
                    "Без политики, грязной брани и оскорблений реальных людей."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Сочини один короткий анекдот про свинью на русском языке. "
                    "2-4 предложения. Должен быть понятный смешной финал. "
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


def try_generate_mood_summary(
    messages: Sequence[str],
    *,
    backend: str,
    model: str,
    endpoint: str,
    timeout: float,
) -> str | None:
    prompt_input = _build_corpus(messages)
    if not prompt_input:
        return None

    return _try_generate_plain_text(
        backend=backend,
        model=model,
        endpoint=endpoint,
        timeout=timeout,
        system_prompt=(
            "Ты кратко оцениваешь настроение и активность маленького Telegram-чата. "
            "Пиши естественно на русском, без markdown."
        ),
        user_prompt=(
            "Проанализируй сообщения чата за день.\n"
            "Коротко оцени:\n"
            "- уровень активности\n"
            "- характер общения (обсуждение, троллинг, болтовня и т.д.)\n"
            "Ответ 2-3 строками на русском.\n\n"
            f"Сообщения:\n{prompt_input}"
        ),
        temperature=0.45,
        max_tokens=180,
        max_chars=900,
    )


def try_generate_svin_comment(
    message_text: str,
    *,
    backend: str,
    model: str,
    endpoint: str,
    timeout: float,
) -> str | None:
    if not message_text.strip():
        return None

    comment = _try_generate_plain_text(
        backend=backend,
        model=model,
        endpoint=endpoint,
        timeout=timeout,
        system_prompt=(
            "Ты отвечаешь как дерзкий Telegram-бот. "
            "Стиль: колко, язвительно, с прожаркой. "
            "Без угроз, без призывов к насилию, без hate speech. "
            "Одна короткая фраза на русском, без объяснений."
        ),
        user_prompt=(
            "Это сообщение из Telegram-чата.\n\n"
            f"{message_text.strip()[:500]}\n\n"
            "Ответь ОДНОЙ короткой агрессивно-ироничной фразой.\n"
            "Без пояснений.\n"
            "Без цитирования.\n"
            "На русском языке.\n"
            "Максимум 8 слов."
        ),
        temperature=0.85,
        max_tokens=60,
        max_chars=120,
    )
    if comment is None:
        return None
    return " ".join(comment.split()[:8])


def try_generate_roast(
    target: str,
    *,
    backend: str,
    model: str,
    endpoint: str,
    timeout: float,
) -> str | None:
    target = target.strip()
    if not target:
        return None

    return _try_generate_plain_text(
        backend=backend,
        model=model,
        endpoint=endpoint,
        timeout=timeout,
        system_prompt=(
            "Ты пишешь короткую прожарку в чате. "
            "Стиль: едко, остро, дерзко, но без угроз, без призывов к насилию и без hate speech."
        ),
        user_prompt=(
            f"Сделай короткую прожарку пользователя {target}.\n"
            "1-2 строки на русском, без markdown."
        ),
        temperature=0.9,
        max_tokens=90,
        max_chars=220,
    )


def try_answer_question(
    question: str,
    *,
    backend: str,
    model: str,
    endpoint: str,
    timeout: float,
) -> str | None:
    question = question.strip()
    if not question:
        return None

    return _try_generate_plain_text(
        backend=backend,
        model=model,
        endpoint=endpoint,
        timeout=timeout,
        system_prompt=(
            "Ты отвечаешь на вопросы в маленьком Telegram-чате. "
            "Пиши на русском, кратко и понятно. "
            "Допускается легкая ирония, но без угроз и без hate speech. "
            "Без markdown."
        ),
        user_prompt=question[:1500],
        temperature=0.5,
        max_tokens=500,
        max_chars=1800,
    )


def try_rewrite_assistant_answer(
    answer: str,
    *,
    backend: str,
    model: str,
    endpoint: str,
    timeout: float,
) -> str | None:
    answer = answer.strip()
    if not answer:
        return None

    return _try_generate_plain_text(
        backend=backend,
        model=model,
        endpoint=endpoint,
        timeout=timeout,
        system_prompt=(
            "Ты редактор текста для Telegram-чата. "
            "Перефразируй текст естественно и кратко, без отказов и без метакомментариев. "
            "Удали служебный мусор вроде Attempt/Context. "
            "Верни только итоговый ответ на русском, 1-3 предложения, без markdown."
        ),
        user_prompt=(
            "Перепиши этот текст в нормальный чатовый ответ:\n\n"
            f"{answer[:1500]}"
        ),
        temperature=0.25,
        max_tokens=220,
        max_chars=900,
    )


def try_generate_image_cluster_summary(
    context_lines: Sequence[str],
    *,
    backend: str,
    model: str,
    endpoint: str,
    timeout: float,
) -> str | None:
    context = "\n".join(line.strip() for line in context_lines if line.strip())
    if not context:
        return None

    return _try_generate_plain_text(
        backend=backend,
        model=model,
        endpoint=endpoint,
        timeout=timeout,
        system_prompt=(
            "Ты даешь короткий человекопонятный label визуальному шаблону в Telegram-чате. "
            "Используй только подписи, OCR и соседний текст. "
            "Не выдумывай детали изображения."
        ),
        user_prompt=(
            "Нужно дать короткий label для повторяющегося визуального кластера.\n"
            "Контекст последних появлений:\n"
            f"{context[:2500]}\n\n"
            "Ответь 3-8 словами. Без markdown."
        ),
        temperature=0.35,
        max_tokens=80,
        max_chars=160,
    )


def _try_generate_plain_text(
    *,
    backend: str,
    model: str,
    endpoint: str,
    timeout: float,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    max_chars: int,
) -> str | None:
    if backend.strip().lower() != "llama_cpp":
        logger.warning("Unsupported LLM backend: %s", backend)
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        text = _call_llama_cpp_with_retries(endpoint=endpoint, payload=payload, timeout=timeout)
    except HTTPError as exc:
        logger.exception("LLM plain text request failed: %s", _read_http_error(exc))
        return None
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        logger.exception("LLM plain text request failed")
        return None

    return _clean_plain_text(text, max_chars=max_chars)


def _clean_plain_text(text: str, *, max_chars: int) -> str | None:
    cleaned = _strip_markdown_fence(text).strip().strip('"')
    cleaned = _sanitize_reasoning_output(cleaned)
    if not cleaned:
        return None
    return cleaned[:max_chars]


def _read_http_error(exc: HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except OSError:
        return f"HTTP {exc.code}"


def _build_topics_user_prompt(prompt_input: str) -> str:
    return (
        "Выдели 2-4 темы и 1-2 короткие фразы о характере обсуждения.\n"
        "Не выдумывай лишнего.\n"
        "Сообщения:\n"
        f"{prompt_input}"
    )


def _build_topics_corpus(messages: Sequence[str]) -> str:
    chunks: list[str] = []
    total_chars = 0

    for raw in messages[-MAX_MESSAGES:]:
        value = " ".join(raw.split()).strip()
        if not value:
            continue
        value = value[:TOPICS_MESSAGE_CHARS]
        line = f"- {value}"
        add_len = len(line) + (1 if chunks else 0)
        if total_chars + add_len > TOPICS_INPUT_CHARS:
            remain = TOPICS_INPUT_CHARS - total_chars
            if remain <= 4:
                break
            chunks.append(line[:remain].rstrip())
            break
        chunks.append(line)
        total_chars += add_len

    return "\n".join(chunks)


def _call_llama_cpp_with_retries(*, endpoint: str, payload: dict[str, Any], timeout: float) -> str:
    current_payload = json.loads(json.dumps(payload))
    last_error: Exception | None = None

    for _attempt in range(3):
        try:
            return _call_llama_cpp(endpoint=endpoint, payload=current_payload, timeout=timeout)
        except HTTPError as exc:
            last_error = exc
            if _is_context_size_error(exc) and _shrink_payload_user_prompt(current_payload):
                logger.warning(
                    "LLM request exceeded context size, retrying with shorter prompt prompt_chars=%s max_tokens=%s",
                    _get_user_prompt_chars(current_payload),
                    current_payload.get("max_tokens"),
                )
                continue
            raise
        except ValueError as exc:
            last_error = exc
            if _is_empty_length_response_error(exc) and _shrink_payload_user_prompt(current_payload):
                logger.warning(
                    "LLM returned truncated reasoning before final content, retrying with shorter prompt prompt_chars=%s max_tokens=%s",
                    _get_user_prompt_chars(current_payload),
                    current_payload.get("max_tokens"),
                )
                continue
            raise

    if last_error is not None:
        raise last_error
    raise ValueError("LLM request failed after retries")


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

    choice = choices[0]
    text = _extract_choice_text(choice)
    if text is None:
        raise ValueError(_build_empty_content_error(choice))
    return text


def _is_context_size_error(exc: HTTPError) -> bool:
    if exc.code not in {400, 413, 422}:
        return False
    error_text = _read_http_error(exc).lower()
    return "context size" in error_text or "exceeds the available context size" in error_text


def _is_empty_length_response_error(exc: ValueError) -> bool:
    message = str(exc)
    return "finish_reason='length'" in message or "LLM exhausted tokens before final content was produced" in message


def _shrink_payload_user_prompt(payload: dict[str, Any]) -> bool:
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return False

    user_message = messages[-1]
    if not isinstance(user_message, dict):
        return False

    content = user_message.get("content")
    if not isinstance(content, str):
        return False

    current_length = len(content)
    if current_length <= MIN_INPUT_CHARS:
        return False

    new_length = max(MIN_INPUT_CHARS, int(current_length * 0.7))
    user_message["content"] = content[:new_length].rstrip()
    return True


def _reduce_payload_max_tokens(payload: dict[str, Any]) -> bool:
    max_tokens = payload.get("max_tokens")
    if not isinstance(max_tokens, int):
        return False
    if max_tokens <= MIN_MAX_TOKENS:
        return False
    payload["max_tokens"] = max(MIN_MAX_TOKENS, int(max_tokens * 0.5))
    return True


def _get_user_prompt_chars(payload: dict[str, Any]) -> int:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return 0
    message = messages[-1]
    if not isinstance(message, dict):
        return 0
    content = message.get("content")
    return len(content) if isinstance(content, str) else 0


def _build_empty_content_error(choice: Any) -> str:
    description = _describe_choice_shape(choice)
    if not isinstance(choice, dict):
        return f"Empty content in LLM response ({description})"

    finish_reason = choice.get("finish_reason")
    message = choice.get("message")
    reasoning_len = 0
    if isinstance(message, dict):
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str):
            reasoning_len = len(reasoning.strip())

    if finish_reason == "length" and reasoning_len > 0:
        return (
            "LLM exhausted tokens before final content was produced "
            f"(reasoning_len={reasoning_len}; {description})"
        )
    return f"Empty content in LLM response ({description})"


def _describe_choice_shape(choice: Any) -> str:
    if not isinstance(choice, dict):
        return f"choice_type={type(choice).__name__}"

    keys = ",".join(sorted(str(key) for key in choice.keys()))
    finish_reason = choice.get("finish_reason")
    message = choice.get("message")
    if isinstance(message, dict):
        message_keys = ",".join(sorted(str(key) for key in message.keys()))
    else:
        message_keys = "-"

    return (
        f"choice_keys=[{keys}] "
        f"finish_reason={finish_reason!r} "
        f"message_type={type(message).__name__} "
        f"message_keys=[{message_keys}]"
    )


def _extract_choice_text(choice: Any) -> str | None:
    if not isinstance(choice, dict):
        return None

    message = choice.get("message")
    if isinstance(message, dict):
        message_text = _extract_message_text(message)
        if message_text:
            return message_text

    delta = choice.get("delta")
    if isinstance(delta, dict):
        delta_text = _extract_message_text(delta)
        if delta_text:
            return delta_text

    text = choice.get("text")
    if isinstance(text, str):
        normalized_text = text.strip()
        if normalized_text:
            return normalized_text

    return None


def _extract_message_text(message: dict[str, Any]) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
        if text:
            return text
    elif isinstance(content, dict):
        dict_text = _extract_text_value(content)
        if dict_text:
            return dict_text
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                normalized_item = item.strip()
                if normalized_item:
                    parts.append(normalized_item)
                continue
            if not isinstance(item, dict):
                continue
            normalized = _extract_text_value(item)
            if normalized:
                parts.append(normalized)
        if parts:
            return "\n".join(parts)

    text_field = message.get("text")
    if isinstance(text_field, str):
        normalized_text_field = text_field.strip()
        if normalized_text_field:
            return normalized_text_field

    return None


def _extract_text_value(payload: dict[str, Any]) -> str | None:
    for key in ("text", "content", "value", "output_text"):
        value = payload.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _looks_like_reasoning_text(text: str) -> bool:
    lowered = text.strip().lstrip("\"'“”`").lower()
    reasoning_prefixes = (
        "thinking process",
        "reasoning:",
        "chain of thought",
        "analysis:",
        "<think>",
    )
    return lowered.startswith(reasoning_prefixes)


def _sanitize_reasoning_output(text: str) -> str:
    if not text:
        return text

    stripped = text.strip().strip('"')
    if not _looks_like_reasoning_text(stripped):
        return stripped

    option = _extract_best_draft_option(stripped)
    if option:
        return option

    lines: list[str] = []
    for raw in stripped.splitlines():
        line = raw.strip().strip('"')
        if not line:
            continue
        lowered = line.lower()
        if _looks_like_reasoning_text(line):
            continue
        if lowered.startswith("draft response options"):
            continue
        if re.match(r"^\d+\.\s", line):
            continue
        if line.startswith(("*", "-", "•")):
            continue
        lines.append(line)

    return lines[-1] if lines else ""


def _extract_best_draft_option(text: str) -> str | None:
    options: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().strip('"')
        if not line:
            continue
        if not line.startswith(("*", "-", "•")):
            continue
        value = line[1:].strip()
        value = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()
        if len(value) < 4:
            continue
        if _looks_like_reasoning_text(value):
            continue
        if not any(ch.isalpha() for ch in value):
            continue
        options.append(value)
    if not options:
        return None
    return min(options, key=len)


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
