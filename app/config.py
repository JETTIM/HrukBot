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


def get_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "535857234:AAEPgX015Xg3EAve7KnHTqDWGygatDikxA0").strip()
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
    )
