from __future__ import annotations

from urllib.error import HTTPError

import pytest

from app.llm_topics import (
    _clean_plain_text,
    _call_llama_cpp_with_retries,
    _describe_choice_shape,
    _extract_choice_text,
    _extract_message_text,
    _reduce_payload_max_tokens,
    _shrink_payload_user_prompt,
)


def test_extract_message_text_prefers_content() -> None:
    message = {"content": "  РіРѕС‚РѕРІС‹Р№ РѕС‚РІРµС‚  ", "reasoning_content": "РІРЅСѓС‚СЂРµРЅРЅРёРµ СЂР°СЃСЃСѓР¶РґРµРЅРёСЏ"}
    assert _extract_message_text(message) == "РіРѕС‚РѕРІС‹Р№ РѕС‚РІРµС‚"


def test_extract_message_text_does_not_fallback_to_reasoning_content() -> None:
    message = {"content": "   ", "reasoning_content": "  РѕС‚РІРµС‚ РёР· reasoning  "}
    assert _extract_message_text(message) is None


def test_extract_message_text_returns_none_for_empty_payload() -> None:
    message = {"content": "", "reasoning_content": "   "}
    assert _extract_message_text(message) is None


def test_extract_message_text_keeps_non_empty_content_even_if_reasoning_like() -> None:
    message = {"content": "Thinking Process: СЃРЅР°С‡Р°Р»Р° РїРѕРґСѓРјР°СЋ, РїРѕС‚РѕРј РѕС‚РІРµС‡Сѓ"}
    assert _extract_message_text(message) == "Thinking Process: СЃРЅР°С‡Р°Р»Р° РїРѕРґСѓРјР°СЋ, РїРѕС‚РѕРј РѕС‚РІРµС‡Сѓ"


def test_extract_message_text_supports_structured_content_parts() -> None:
    message = {
        "content": [
            {"type": "text", "text": "РїРµСЂРІР°СЏ СЃС‚СЂРѕРєР°"},
            {"type": "text", "text": "  РІС‚РѕСЂР°СЏ СЃС‚СЂРѕРєР°  "},
            {"type": "input_text", "text": "   "},
        ]
    }
    assert _extract_message_text(message) == "РїРµСЂРІР°СЏ СЃС‚СЂРѕРєР°\nРІС‚РѕСЂР°СЏ СЃС‚СЂРѕРєР°"


def test_extract_message_text_supports_content_dict() -> None:
    message = {"content": {"type": "text", "text": "  РѕС‚РІРµС‚ РёР· dict  "}}
    assert _extract_message_text(message) == "РѕС‚РІРµС‚ РёР· dict"


def test_extract_message_text_supports_list_with_output_text() -> None:
    message = {"content": [{"type": "output_text", "output_text": "  РѕС‚РІРµС‚ РёР· output_text  "}]}
    assert _extract_message_text(message) == "РѕС‚РІРµС‚ РёР· output_text"


def test_extract_message_text_falls_back_to_message_text_field() -> None:
    message = {"content": [], "text": "  С‚РµРєСЃС‚ РёР· message.text  "}
    assert _extract_message_text(message) == "С‚РµРєСЃС‚ РёР· message.text"


def test_extract_choice_text_supports_completion_text_field() -> None:
    choice = {"text": "  РѕС‚РІРµС‚ РёР· completion  "}
    assert _extract_choice_text(choice) == "РѕС‚РІРµС‚ РёР· completion"


def test_extract_choice_text_supports_delta_content() -> None:
    choice = {"delta": {"content": "  РѕС‚РІРµС‚ РёР· delta  "}}
    assert _extract_choice_text(choice) == "РѕС‚РІРµС‚ РёР· delta"


def test_extract_choice_text_prefers_message_content_when_reasoning_content_present() -> None:
    choice = {
        "message": {
            "role": "assistant",
            "content": "РЇ РЅРµ СЃРїР»СЋ. Р’СЃРµРіРґР° РіРѕС‚РѕРІ РїРѕРјРѕС‡СЊ!",
            "reasoning_content": "Thinking Process: ...",
        }
    }
    assert _extract_choice_text(choice) == "РЇ РЅРµ СЃРїР»СЋ. Р’СЃРµРіРґР° РіРѕС‚РѕРІ РїРѕРјРѕС‡СЊ!"


def test_extract_message_text_ignores_reasoning_content_when_content_is_empty() -> None:
    message = {
        "content": "   ",
        "reasoning_content": "Thinking Process:\nDraft response options:\n* РїРµСЂРІС‹Р№\n* РІС‚РѕСЂРѕР№",
    }
    assert _extract_message_text(message) is None


def test_describe_choice_shape_includes_useful_keys() -> None:
    choice = {
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": "ok", "reasoning_content": "think"},
    }
    description = _describe_choice_shape(choice)
    assert "choice_keys=[finish_reason,message]" in description
    assert "finish_reason='stop'" in description
    assert "message_keys=[content,reasoning_content,role]" in description


def test_clean_plain_text_extracts_best_draft_option() -> None:
    raw = (
        "Thinking Process:\n\n"
        "1. Analyze request\n"
        "Draft response options:\n"
        "* РЎРїР»СЋ? РЇ РЅРµР№СЂРѕСЃРµС‚СЊ, СЃРїР»СЋ РЅРµ СЃРїР»СЋ. (СЃР»РёС€РєРѕРј С„РѕСЂРјР°Р»СЊРЅРѕ)\n"
        "* РќРµС‚, СЏ РЅРµ СЃРїР»СЋ.\n"
        "* \"\n"
    )
    assert _clean_plain_text(raw, max_chars=900) == "РќРµС‚, СЏ РЅРµ СЃРїР»СЋ."


def test_shrink_payload_user_prompt_reduces_prompt_length() -> None:
    payload = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "x" * 2000},
        ]
    }

    changed = _shrink_payload_user_prompt(payload)

    assert changed is True
    assert len(payload["messages"][-1]["content"]) < 2000


def test_reduce_payload_max_tokens_halves_value() -> None:
    payload = {"max_tokens": 180}

    changed = _reduce_payload_max_tokens(payload)

    assert changed is True
    assert payload["max_tokens"] == 90


def test_call_llama_cpp_with_retries_retries_after_empty_length_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_call(*, endpoint: str, payload: dict, timeout: float) -> str:
        calls.append(int(payload["max_tokens"]))
        if len(calls) == 1:
            raise ValueError(
                "Empty content in LLM response (choice_keys=[finish_reason,message] "
                "finish_reason='length' message_type=dict message_keys=[content])"
            )
        return "ok"

    monkeypatch.setattr("app.llm_topics._call_llama_cpp", fake_call)

    result = _call_llama_cpp_with_retries(
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        payload={
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hello"},
            ],
            "max_tokens": 120,
        },
        timeout=5.0,
    )

    assert result == "ok"
    assert calls == [120, 60]


def test_call_llama_cpp_with_retries_retries_after_context_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_call(*, endpoint: str, payload: dict, timeout: float) -> str:
        calls.append(len(payload["messages"][-1]["content"]))
        if len(calls) == 1:
            raise HTTPError(
                endpoint,
                400,
                "Bad Request",
                hdrs=None,
                fp=None,
            )
        return "ok"

    monkeypatch.setattr("app.llm_topics._call_llama_cpp", fake_call)
    monkeypatch.setattr(
        "app.llm_topics._read_http_error",
        lambda exc: '{"error":{"message":"request exceeds the available context size (1024 tokens)"}}',
    )

    result = _call_llama_cpp_with_retries(
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        payload={
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "x" * 2200},
            ],
            "max_tokens": 180,
        },
        timeout=5.0,
    )

    assert result == "ok"
    assert calls[1] < calls[0]
