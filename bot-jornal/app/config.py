"""Configuracao central. Tudo vem de variaveis de ambiente / .env."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _int_list(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _chat_id(raw: str) -> int | str:
    """Aceita tanto o ID numerico (-100...) quanto o @usuario de canal publico."""
    raw = raw.strip()
    if not raw:
        return 0
    if raw.startswith("@"):
        return raw
    try:
        return int(raw)
    except ValueError:
        return f"@{raw}"


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    price: float          # em reais
    days: int             # duracao do acesso
    highlight: bool
    label: str

    @property
    def cents(self) -> int:
        return round(self.price * 100)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    channel_id: int | str
    admin_ids: list[int]

    asaas_env: str
    asaas_api_key: str
    asaas_webhook_token: str

    database_url: str
    tz: ZoneInfo

    grace_days: int
    warn_days_before: int
    refund_window_days: int
    recovery_discount_pct: int
    quiet_mode: bool

    plans: list[Plan]

    @property
    def asaas_base_url(self) -> str:
        if self.asaas_env.lower() == "production":
            return "https://api.asaas.com/v3"
        return "https://api-sandbox.asaas.com/v3"

    def plan(self, code: str) -> Plan | None:
        return next((p for p in self.plans if p.code == code), None)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


def _load_plans() -> list[Plan]:
    path = BASE_DIR / "plans.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Plan(**p) for p in data["plans"]]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot.db")
    # O Railway entrega postgres:// ; o SQLAlchemy async precisa de postgresql+asyncpg://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    return Settings(
        bot_token=os.getenv("BOT_TOKEN", ""),
        channel_id=_chat_id(os.getenv("CHANNEL_ID", "")),
        admin_ids=_int_list(os.getenv("ADMIN_IDS", "")),
        asaas_env=os.getenv("ASAAS_ENV", "sandbox"),
        asaas_api_key=os.getenv("ASAAS_API_KEY", ""),
        asaas_webhook_token=os.getenv("ASAAS_WEBHOOK_TOKEN", ""),
        database_url=db_url,
        tz=ZoneInfo(os.getenv("TZ", "America/Sao_Paulo")),
        grace_days=int(os.getenv("GRACE_DAYS", "2")),
        warn_days_before=int(os.getenv("WARN_DAYS_BEFORE", "1")),
        refund_window_days=int(os.getenv("REFUND_WINDOW_DAYS", "7")),
        recovery_discount_pct=int(os.getenv("RECOVERY_DISCOUNT_PCT", "30")),
        quiet_mode=os.getenv("QUIET_MODE", "false").lower() == "true",
        plans=_load_plans(),
    )
