from __future__ import annotations

from types import SimpleNamespace

from bot import (
    _extract_command_args,
    _extract_command_text,
    _extract_reaction_emojis,
    _format_media_requirements,
    _pick_reaction_for_reply,
)


def test_extract_command_args_from_text() -> None:
    message = SimpleNamespace(text="/ask какой норвуд на фото?", caption=None)
    assert _extract_command_args(message) == "какой норвуд на фото?"


def test_extract_command_args_from_caption() -> None:
    message = SimpleNamespace(text=None, caption="/ask какой норвуд на фото?")
    assert _extract_command_args(message) == "какой норвуд на фото?"


def test_format_media_requirements_mentions_voice_and_video_limits() -> None:
    text = _format_media_requirements()
    assert "видео до 90 сек" in text
    assert "voice до 600 сек" in text


def test_extract_command_text_keeps_bot_mention_variant() -> None:
    message = SimpleNamespace(text="/relearn@SvinBot", caption=None)
    assert _extract_command_text(message) == "/relearn@SvinBot"


def test_extract_reaction_emojis_collects_emoji_items() -> None:
    reactions = [SimpleNamespace(emoji="🤡"), SimpleNamespace(emoji="👍"), SimpleNamespace(emoji=None)]
    assert _extract_reaction_emojis(reactions) == {"🤡", "👍"}


def test_extract_reaction_emojis_supports_dict_items() -> None:
    reactions = [{"emoji": "🤡"}, {"emoji": "🔥"}, {"type": "custom_emoji"}]
    assert _extract_reaction_emojis(reactions) == {"🤡", "🔥"}


def test_pick_reaction_for_reply_ignores_positive_only() -> None:
    assert _pick_reaction_for_reply({"👍", "❤️"}) is None


def test_pick_reaction_for_reply_prefers_hostile_priority() -> None:
    assert _pick_reaction_for_reply({"🔥", "🤡", "👎"}) == "🤡"
