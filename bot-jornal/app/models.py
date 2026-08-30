"""Modelo de dados."""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Garante datetime com timezone na ida e na volta.

    O SQLite devolve datetime ingenuo, o que quebraria comparacoes com utcnow().
    Isso mantem o comportamento identico em SQLite (dev) e Postgres (producao).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"        # Pix gerado, aguardando pagamento
    PAID = "PAID"              # confirmado
    EXPIRED = "EXPIRED"        # Pix venceu sem pagamento
    REFUNDED = "REFUNDED"      # estornado
    CHARGEBACK = "CHARGEBACK"  # contestado no cartao


class SubStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"          # dentro do periodo pago
    GRACE = "GRACE"            # venceu, dentro da tolerancia
    EXPIRED = "EXPIRED"        # removido por falta de pagamento
    REFUNDED = "REFUNDED"      # removido por reembolso
    BANNED = "BANNED"          # removido por chargeback/fraude, nao pode recomprar


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram user id
    first_name: Mapped[str | None] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(64))
    cpf: Mapped[str | None] = mapped_column(String(14), index=True)
    email: Mapped[str | None] = mapped_column(String(180))
    asaas_customer_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(120))  # utm vinda do /start
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    payments: Mapped[list["Payment"]] = relationship(back_populates="user")
    subscription: Mapped["Subscription | None"] = relationship(
        back_populates="user", uselist=False
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asaas_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    plan_code: Mapped[str] = mapped_column(String(32))
    value_cents: Mapped[int] = mapped_column(Integer)
    discount_pct: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False), default=PaymentStatus.PENDING, index=True
    )
    is_renewal: Mapped[bool] = mapped_column(Boolean, default=False)
    pix_payload: Mapped[str | None] = mapped_column(Text)
    pix_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, index=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    user: Mapped[User] = relationship(back_populates="payments")

    @property
    def value(self) -> float:
        return self.value_cents / 100


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), unique=True, index=True
    )
    plan_code: Mapped[str] = mapped_column(String(32))
    status: Mapped[SubStatus] = mapped_column(
        Enum(SubStatus, native_enum=False), default=SubStatus.ACTIVE, index=True
    )
    period_end: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    warned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    invite_link: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    renewals: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="subscription")


class RecoveryLog(Base):
    """Uma linha por disparo de recuperacao de carrinho, para nao repetir etapa."""
    __tablename__ = "recovery_logs"
    __table_args__ = (UniqueConstraint("payment_id", "step", name="uq_recovery_step"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(Integer, ForeignKey("payments.id"), index=True)
    step: Mapped[int] = mapped_column(Integer)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RefundRequest(Base):
    """Pedidos fora da janela automatica, para o admin aprovar com um toque."""
    __tablename__ = "refund_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    payment_id: Mapped[int] = mapped_column(Integer, ForeignKey("payments.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)  # OPEN/APPROVED/DENIED
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger)


class Event(Base):
    """Trilha de auditoria e base do funil (start -> checkout -> pago)."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(48), index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, index=True
    )
