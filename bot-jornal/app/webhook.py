"""Recebe os eventos do Asaas.

Configure no painel do Asaas (Integracoes > Webhooks):
  URL:   https://SEU-APP.up.railway.app/webhook/asaas
  Token: o mesmo valor de ASAAS_WEBHOOK_TOKEN no .env
"""
from __future__ import annotations

import hmac
import logging

from aiogram import Bot
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.models import Payment, PaymentStatus
from app.services import billing

log = logging.getLogger(__name__)
router = APIRouter()

PAID_EVENTS = {"PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"}
REFUND_EVENTS = {"PAYMENT_REFUNDED", "PAYMENT_PARTIALLY_REFUNDED"}
CHARGEBACK_EVENTS = {
    "PAYMENT_CHARGEBACK_REQUESTED",
    "PAYMENT_CHARGEBACK_DISPUTE",
    "PAYMENT_AWAITING_CHARGEBACK_REVERSAL",
}


def get_bot(request: Request) -> Bot:
    return request.app.state.bot


@router.post("/webhook/asaas")
async def asaas_webhook(
    request: Request,
    asaas_access_token: str | None = Header(default=None, alias="asaas-access-token"),
):
    settings = get_settings()
    expected = settings.asaas_webhook_token

    if expected and not (asaas_access_token and hmac.compare_digest(asaas_access_token, expected)):
        log.warning("Webhook recusado: token invalido")
        raise HTTPException(status_code=401, detail="unauthorized")

    body = await request.json()
    event = body.get("event", "")
    data = body.get("payment") or {}
    asaas_id = data.get("id")

    if not asaas_id:
        return {"ok": True, "ignored": "sem id de pagamento"}

    bot = get_bot(request)

    async with session_scope() as session:
        result = await session.execute(select(Payment).where(Payment.asaas_id == asaas_id))
        payment = result.scalar_one_or_none()

        if payment is None:
            log.info("Webhook %s para cobranca desconhecida %s", event, asaas_id)
            return {"ok": True, "ignored": "cobranca fora do bot"}

        if event in PAID_EVENTS:
            await billing.confirm_payment(session, bot, payment)
        elif event in REFUND_EVENTS:
            if payment.status != PaymentStatus.REFUNDED:
                await _mark_refunded_externally(session, bot, payment)
        elif event in CHARGEBACK_EVENTS:
            await billing.handle_chargeback(session, bot, payment)
        elif event == "PAYMENT_OVERDUE":
            if payment.status == PaymentStatus.PENDING:
                payment.status = PaymentStatus.EXPIRED
        else:
            log.debug("Evento %s ignorado", event)

    return {"ok": True}


async def _mark_refunded_externally(session, bot: Bot, payment: Payment) -> None:
    """Estorno feito direto no painel do Asaas: espelha aqui e remove o acesso."""
    from app.models import SubStatus, utcnow

    payment.status = PaymentStatus.REFUNDED
    payment.closed_at = utcnow()

    sub = await billing.get_subscription(session, payment.user_id)
    if sub:
        sub.status = SubStatus.REFUNDED
        sub.removed_at = utcnow()
        sub.period_end = utcnow()

    from app.services import access

    await access.remove_from_channel(bot, payment.user_id, permanent=False)
    await billing.log_event(session, "refund_external", payment.user_id, f"R${payment.value}")


@router.get("/health")
async def health():
    return {"status": "ok"}
