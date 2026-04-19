from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

URL_RE = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+)", re.IGNORECASE)
NON_RU_RE = re.compile(r"[^а-яё\s]", re.IGNORECASE)
MULTI_SPACE_RE = re.compile(r"\s+")

STOP_WORDS = {
    "и",
    "в",
    "во",
    "на",
    "по",
    "под",
    "до",
    "от",
    "за",
    "из",
    "у",
    "о",
    "об",
    "к",
    "ко",
    "для",
    "с",
    "со",
    "а",
    "но",
    "или",
    "не",
    "ни",
    "да",
    "что",
    "это",
    "как",
    "так",
    "же",
    "ли",
    "бы",
    "то",
    "вот",
    "там",
    "тут",
    "уже",
    "еще",
    "ещё",
    "только",
    "очень",
    "просто",
    "если",
    "когда",
    "где",
    "кто",
    "он",
    "она",
    "они",
    "мы",
    "вы",
    "я",
    "ты",
    "мой",
    "твой",
    "наш",
    "ваш",
    "его",
    "ее",
    "её",
    "их",
    "меня",
    "тебя",
    "нас",
    "вас",
    "мне",
    "тебе",
    "нам",
    "вам",
    "сам",
    "сама",
    "сами",
    "тоже",
    "можно",
    "нужно",
    "надо",
    "будет",
    "было",
    "быть",
    "есть",
    "нет",
    "ну",
    "ок",
}


@dataclass(frozen=True)
class TopicDefinition:
    title: str
    keywords: tuple[str, ...]


TOPIC_DICTIONARY: tuple[TopicDefinition, ...] = (
    TopicDefinition(
        title="Работа и проекты",
        keywords=("проект", "задач", "дедлайн", "релиз", "клиент", "команд"),
    ),
    TopicDefinition(
        title="Разработка и технологии",
        keywords=(
            "код",
            "баг",
            "фикс",
            "тест",
            "деплой",
            "сервер",
            "база данных",
            "бот",
            "api",
            "интеграц",
        ),
    ),
    TopicDefinition(
        title="Учеба и развитие",
        keywords=("курс", "обучен", "урок", "лекц", "экзамен", "книг", "навык"),
    ),
    TopicDefinition(
        title="Финансы",
        keywords=("бюджет", "деньг", "оплат", "стоим", "расход", "доход", "зарплат"),
    ),
    TopicDefinition(
        title="Маркетинг и контент",
        keywords=("реклам", "контент", "пост", "подписчик", "охват", "продвижен"),
    ),
    TopicDefinition(
        title="Организация встреч",
        keywords=("встреч", "созвон", "звонок", "митинг", "календар", "расписан"),
    ),
)

SHORT_DISCUSSION_MESSAGE = "Обсуждение было коротким, выраженные темы не выделяются."


def extract_main_topics(messages: Sequence[str], top_k: int = 4) -> list[str]:
    """
    Extract 2-3 main topics from Russian text messages without neural models.

    Fallbacks:
    - Too few messages/content -> short discussion message.
    - No explicit dictionary topics -> frequent meaningful words/phrases.
    """
    if not messages:
        return [SHORT_DISCUSSION_MESSAGE]

    cleaned = [clean_text(text) for text in messages if text and text.strip()]
    tokenized = [tokenize(text) for text in cleaned if text]
    flattened_tokens = [token for row in tokenized for token in row]

    if len(messages) < 3 or len(flattened_tokens) < 12:
        return [SHORT_DISCUSSION_MESSAGE]

    word_freq = get_frequent_words(flattened_tokens, top_n=12)
    bigram_freq = get_frequent_bigrams(tokenized, top_n=8)
    topic_scores = score_topics(word_freq, bigram_freq)
    explicit_topics = select_top_topics(topic_scores, top_k=top_k)

    if explicit_topics:
        return explicit_topics[: max(2, min(4, top_k))]

    fallback = build_fallback_topics(word_freq, bigram_freq, top_k=max(2, min(4, top_k)))
    if fallback:
        return fallback
    return [SHORT_DISCUSSION_MESSAGE]


def clean_text(text: str) -> str:
    text = text.lower()
    text = URL_RE.sub(" ", text)
    text = NON_RU_RE.sub(" ", text)
    text = MULTI_SPACE_RE.sub(" ", text).strip()
    return text


def tokenize(text: str) -> list[str]:
    tokens = text.split()
    return [token for token in tokens if is_meaningful_token(token)]


def is_meaningful_token(token: str) -> bool:
    if len(token) < 3:
        return False
    return token not in STOP_WORDS


def get_frequent_words(tokens: Sequence[str], top_n: int = 10) -> list[tuple[str, int]]:
    counts = Counter(tokens)
    return counts.most_common(top_n)


def get_frequent_bigrams(
    tokenized_messages: Sequence[Sequence[str]], top_n: int = 8
) -> list[tuple[str, int]]:
    bigrams: Counter[str] = Counter()
    for tokens in tokenized_messages:
        if len(tokens) < 2:
            continue
        for idx in range(len(tokens) - 1):
            first = tokens[idx]
            second = tokens[idx + 1]
            bigrams[f"{first} {second}"] += 1
    return bigrams.most_common(top_n)


def score_topics(
    word_freq: Sequence[tuple[str, int]],
    bigram_freq: Sequence[tuple[str, int]],
) -> dict[str, int]:
    word_map = dict(word_freq)
    bigram_map = dict(bigram_freq)
    scores: dict[str, int] = {}

    for topic in TOPIC_DICTIONARY:
        score = 0
        for keyword in topic.keywords:
            if " " in keyword:
                score += bigram_map.get(keyword, 0) * 2
                continue

            for word, freq in word_map.items():
                if word.startswith(keyword):
                    score += freq

        if score > 0:
            scores[topic.title] = score

    return scores


def select_top_topics(topic_scores: dict[str, int], top_k: int = 3) -> list[str]:
    if not topic_scores:
        return []
    ordered = sorted(topic_scores.items(), key=lambda item: (-item[1], item[0]))
    return [title for title, _ in ordered[:top_k]]


def build_fallback_topics(
    word_freq: Sequence[tuple[str, int]],
    bigram_freq: Sequence[tuple[str, int]],
    top_k: int = 3,
) -> list[str]:
    candidates: list[str] = []

    for phrase, freq in bigram_freq:
        if freq >= 2:
            candidates.append(phrase)
        if len(candidates) >= top_k:
            return candidates[:top_k]

    for word, freq in word_freq:
        if freq >= 2 and word not in candidates:
            candidates.append(word)
        if len(candidates) >= top_k:
            break

    return candidates[:top_k]
