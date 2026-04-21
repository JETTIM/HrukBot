from __future__ import annotations

from types import SimpleNamespace

from bot import _extract_command_args


def test_extract_command_args_from_text() -> None:
    message = SimpleNamespace(text="/ask какой норвуд на фото?", caption=None)
    assert _extract_command_args(message) == "какой норвуд на фото?"


def test_extract_command_args_from_caption() -> None:
    message = SimpleNamespace(text=None, caption="/ask какой норвуд на фото?")
    assert _extract_command_args(message) == "какой норвуд на фото?"
