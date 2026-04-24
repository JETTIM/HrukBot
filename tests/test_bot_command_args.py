from __future__ import annotations

from types import SimpleNamespace

from bot import _extract_command_args, _format_media_requirements


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
