"""Relatorios de venda para o painel de admin."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Event, Payment, PaymentStatus, RefundRequest, SubStatus, Subscription, User, utcnow,
)


@dataclass
class Report:
    label: str
    faturamento: float
    vendas: int
    novas: int
    renovacoes: int
    reembolsos: float
    reembolsos_qtd: int
    pix_gerados: int
    starts: int
    ativos: int
    em_tolerancia: int
    ticket_medio: float

    @property
    def conversao_pix(self) -> float:
        return (self.vendas / self.pix_gerados * 100) if self.pix_gerados else 0.0

    @property
    def conversao_start(self) -> float:
        return (self.vendas / self.starts * 100) if self.starts else 0.0

    @property
    def liquido(self) -> float:
        """Descontando a taxa fixa do Pix do Asaas (R$ 0,49) e os reembolsos."""
        return self.faturamento - (self.vendas * 0.49) - self.reembolsos


async def build(session: AsyncSession, days: int, label: str) -> Report:
    now = utcnow()
    since = now - timedelta(days=days)

    async def scalar(stmt, default=0):
        return (await session.execute(stmt)).scalar() or default

    faturamento_cents = await scalar(
        select(func.sum(Payment.value_cents)).where(
            Payment.status == PaymentStatus.PAID, Payment.paid_at >= since
        )
    )
    vendas = await scalar(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.PAID, Payment.paid_at >= since
        )
    )
    novas = await scalar(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.PAID,
            Payment.paid_at >= since,
            Payment.is_renewal.is_(False),
        )
    )
    reembolso_cents = await scalar(
        select(func.sum(Payment.value_cents)).where(
            Payment.status == PaymentStatus.REFUNDED, Payment.closed_at >= since
        )
    )
    reembolsos_qtd = await scalar(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.REFUNDED, Payment.closed_at >= since
        )
    )
    pix_gerados = await scalar(
        select(func.count(Payment.id)).where(Payment.created_at >= since)
    )
    starts = await scalar(
        select(func.count(Event.id)).where(Event.type == "start", Event.created_at >= since)
    )
    ativos = await scalar(
        select(func.count(Subscription.id)).where(Subscription.status == SubStatus.ACTIVE)
    )
    tolerancia = await scalar(
        select(func.count(Subscription.id)).where(Subscription.status == SubStatus.GRACE)
    )

    faturamento = (faturamento_cents or 0) / 100
    return Report(
        label=label,
        faturamento=faturamento,
        vendas=vendas,
        novas=novas,
        renovacoes=vendas - novas,
        reembolsos=(reembolso_cents or 0) / 100,
        reembolsos_qtd=reembolsos_qtd,
        pix_gerados=pix_gerados,
        starts=starts,
        ativos=ativos,
        em_tolerancia=tolerancia,
        ticket_medio=(faturamento / vendas) if vendas else 0.0,
    )


def format_report(r: Report) -> str:
    m = _money
    return (
        f"*{r.label}*\n\n"
        f"Faturamento: *R$ {m(r.faturamento)}*\n"
        f"Liquido estimado: R$ {m(r.liquido)}\n"
        f"Vendas: *{r.vendas}*  (novas {r.novas} · renovacoes {r.renovacoes})\n"
        f"Ticket medio: R$ {m(r.ticket_medio)}\n"
        f"Reembolsos: {r.reembolsos_qtd} (R$ {m(r.reembolsos)})\n\n"
        f"*Funil*\n"
        f"/start: {r.starts}\n"
        f"Pix gerados: {r.pix_gerados}\n"
        f"Start → venda: *{r.conversao_start:.1f}%*\n"
        f"Pix → venda: *{r.conversao_pix:.1f}%*\n\n"
        f"*Base agora*\n"
        f"Assinantes ativos: *{r.ativos}*\n"
        f"Em tolerancia: {r.em_tolerancia}"
    )


async def export_csv(session: AsyncSession) -> bytes:
    settings = get_settings()
    result = await session.execute(
        select(Payment, User).join(User, Payment.user_id == User.id)
        .order_by(Payment.created_at.desc())
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "pagamento_id", "asaas_id", "criado_em", "pago_em", "status", "plano",
        "valor", "desconto_pct", "renovacao", "user_id", "nome", "username",
        "cpf", "email", "origem",
    ])
    for payment, user in result.all():
        writer.writerow([
            payment.id,
            payment.asaas_id or "",
            _fmt(payment.created_at, settings),
            _fmt(payment.paid_at, settings),
            payment.status.value,
            payment.plan_code,
            f"{payment.value:.2f}".replace(".", ","),
            payment.discount_pct,
            "sim" if payment.is_renewal else "nao",
            user.id,
            user.first_name or "",
            user.username or "",
            user.cpf or "",
            user.email or "",
            user.source or "",
        ])
    return buffer.getvalue().encode("utf-8-sig")


async def open_refunds(session: AsyncSession) -> list[tuple[RefundRequest, Payment, User]]:
    result = await session.execute(
        select(RefundRequest, Payment, User)
        .join(Payment, RefundRequest.payment_id == Payment.id)
        .join(User, RefundRequest.user_id == User.id)
        .where(RefundRequest.status == "OPEN")
        .order_by(RefundRequest.created_at)
    )
    return list(result.all())


def _fmt(value: datetime | None, settings) -> str:
    if value is None:
        return ""
    return value.astimezone(settings.tz).strftime("%d/%m/%Y %H:%M")


def _money(value: float) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
