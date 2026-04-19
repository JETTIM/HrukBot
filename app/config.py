from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_chat_id: int
    bot_parse_mode: str = "HTML"
    db_path: Path = BASE_DIR / "data" / "bot.sqlite3"
    log_level: str = "INFO"
    enable_image_processing: bool = True
    use_llm_topics: bool = False
    llm_backend: str = "llama_cpp"
    llm_model: str = ""
    llm_endpoint: str = "http://127.0.0.1:8080/v1/chat/completions"
    llm_timeout: float = 10.0


def get_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("BOT_TOKEN is not set. Create .env from .env.example.")

    allowed_chat_id_raw = os.getenv("ALLOWED_CHAT_ID", "").strip()
    if not allowed_chat_id_raw:
        raise ValueError("ALLOWED_CHAT_ID is not set. Create .env from .env.example.")

    return Settings(
        bot_token=bot_token,
        allowed_chat_id=int(allowed_chat_id_raw),
        bot_parse_mode=os.getenv("BOT_PARSE_MODE", "HTML"),
        db_path=Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "bot.sqlite3"))),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        enable_image_processing=_as_bool(os.getenv("ENABLE_IMAGE_PROCESSING", "true")),
        use_llm_topics=_as_bool(os.getenv("USE_LLM_TOPICS", "false")),
        llm_backend=os.getenv("LLM_BACKEND", "llama_cpp"),
        llm_model=os.getenv("LLM_MODEL", ""),
        llm_endpoint=os.getenv("LLM_ENDPOINT", "http://127.0.0.1:8080/v1/chat/completions"),
        llm_timeout=float(os.getenv("LLM_TIMEOUT", "10")),
    )


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
