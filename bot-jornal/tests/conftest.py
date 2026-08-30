"""Ambiente de teste: SQLite temporario, bot falso e gateway falso."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import pytest_asyncio

DB_FILE = Path(tempfile.gettempdir()) / "jornal_bot_test.db"

os.environ.update({
    "BOT_TOKEN": "123:TESTE",
    "CHANNEL_ID": "-1001234567890",
    "ADMIN_IDS": "999",
    "ASAAS_ENV": "sandbox",
    "ASAAS_API_KEY": "chave-de-teste",
    "ASAAS_WEBHOOK_TOKEN": "token-secreto",
    "DATABASE_URL": f"sqlite+aiosqlite:///{DB_FILE}",
    "TZ": "America/Sao_Paulo",
    "GRACE_DAYS": "2",
    "WARN_DAYS_BEFORE": "1",
    "REFUND_WINDOW_DAYS": "7",
    "RECOVERY_DISCOUNT_PCT": "30",
})


# ----------------------------------------------------------------- dublês

@dataclass
class FakeInviteLink:
    invite_link: str


@dataclass
class FakeMember:
    status: str = "member"
    can_invite_users: bool = True
    can_restrict_members: bool = True


@dataclass
class FakeBot:
    """Registra tudo o que o bot tentou fazer, para as assercoes."""
    sent: list[tuple[int, str]] = field(default_factory=list)
    invites: list[int] = field(default_factory=list)
    banned: list[int] = field(default_factory=list)
    unbanned: list[int] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)
    member_status: str = "left"

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return True

    async def create_chat_invite_link(self, chat_id, **kwargs):
        self.invites.append(chat_id)
        return FakeInviteLink(invite_link=f"https://t.me/+fake{len(self.invites)}")

    async def revoke_chat_invite_link(self, chat_id, invite_link):
        self.revoked.append(invite_link)

    async def ban_chat_member(self, chat_id, user_id, **kwargs):
        self.banned.append(user_id)

    async def unban_chat_member(self, chat_id, user_id, **kwargs):
        self.unbanned.append(user_id)

    async def get_chat_member(self, chat_id, user_id):
        return FakeMember(status=self.member_status)

    async def get_me(self):
        return FakeMember(status="administrator")

    def texts_for(self, user_id: int) -> list[str]:
        return [t for uid, t in self.sent if uid == user_id]


@dataclass
class FakeCharge:
    asaas_id: str
    value: float
    payload: str
    encoded_image: str
    expiration: object
    invoice_url: str | None = None


class FakeAsaas:
    """Gateway falso: nao toca a rede, mas registra as chamadas."""

    def __init__(self):
        self.counter = 0
        self.refunded: list[str] = []
        self.paid: set[str] = set()
        self.fail_refund = False

    async def create_customer(self, *, name, cpf, email, external_ref):
        return f"cus_{external_ref}"

    async def create_pix_charge(self, *, customer_id, value, description, external_ref, **kw):
        from datetime import datetime, timedelta, timezone
        self.counter += 1
        return FakeCharge(
            asaas_id=f"pay_{self.counter}",
            value=value,
            payload=f"00020126BR.GOV.BCB.PIX{self.counter}",
            encoded_image="",
            expiration=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    async def is_paid(self, asaas_id):
        return asaas_id in self.paid

    async def get_payment(self, asaas_id):
        return {"id": asaas_id, "status": "RECEIVED" if asaas_id in self.paid else "PENDING"}

    async def refund(self, asaas_id, description=""):
        if self.fail_refund:
            raise RuntimeError("gateway fora do ar")
        self.refunded.append(asaas_id)
        return {"id": asaas_id, "status": "REFUNDED"}


@dataclass
class FakeTgUser:
    id: int
    first_name: str = "Teste"
    username: str | None = None


# ----------------------------------------------------------------- fixtures

@pytest_asyncio.fixture
async def db():
    if DB_FILE.exists():
        DB_FILE.unlink()
    from app.db import engine, init_db
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture
def gateway():
    return FakeAsaas()


@pytest.fixture
def tg_user():
    return FakeTgUser(id=555, first_name="Matheus", username="matheus")
