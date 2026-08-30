"""Regras de negocio: checkout, liberacao de acesso, reembolso, chargeback."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards as kb
from app.bot import texts as T
from app.config import Plan, get_settings
from app.models import (
    Event, Payment, PaymentStatus, RefundRequest, SubStatus, Subscription, User, utcnow,
)
from app.services import access
from app.services.asaas import AsaasClient

log = logging.getLogger(__name__)


async def log_event(session: AsyncSession, type_: str, user_id: int | None, detail: str = "") -> None:
    session.add(Event(type=type_, user_id=user_id, detail=detail[:2000]))


async def get_or_create_user(session: AsyncSession, tg_user, source: str | None = None) -> User:
    user = await session.get(User, tg_user.id)
    if user is None:
        user = User(
            id=tg_user.id,
            first_name=tg_user.first_name,
            username=tg_user.username,
            source=source,
        )
        session.add(user)
        await session.flush()
        await log_event(session, "user_created", user.id, source or "")
    else:
        user.first_name = tg_user.first_name
        user.username = tg_user.username
        if source and not user.source:
            user.source = source
    return user


# ------------------------------------------------------------------ checkout

async def create_checkout(
    session: AsyncSession,
    user: User,
    plan: Plan,
    *,
    discount_pct: int = 0,
    client: AsaasClient | None = None,
) -> tuple[Payment, str, str]:
    """Cria a cobranca no Asaas e devolve (payment, copia_e_cola, qr_base64)."""
    client = client or AsaasClient()
    settings = get_settings()

    value = plan.price * (100 - discount_pct) / 100
    value = round(value, 2)

    if not user.asaas_customer_id:
        user.asaas_customer_id = await client.create_customer(
            name=user.first_name or f"Assinante {user.id}",
            cpf=user.cpf or "",
            email=user.email,
            external_ref=str(user.id),
        )
        await session.flush()

    sub = await get_subscription(session, user.id)
    is_renewal = sub is not None and sub.renewals >= 0 and sub.status in {
        SubStatus.ACTIVE, SubStatus.GRACE, SubStatus.EXPIRED
    }

    payment = Payment(
        user_id=user.id,
        plan_code=plan.code,
        value_cents=round(value * 100),
        discount_pct=discount_pct,
        status=PaymentStatus.PENDING,
        is_renewal=bool(is_renewal),
    )
    session.add(payment)
    await session.flush()

    charge = await client.create_pix_charge(
        customer_id=user.asaas_customer_id,
        value=value,
        description=f"{T.CANAL_NOME} — {plan.name}",
        external_ref=f"pay:{payment.id}",
    )

    payment.asaas_id = charge.asaas_id
    payment.pix_payload = charge.payload
    payment.pix_expires_at = charge.expiration
    await session.flush()

    await log_event(
        session, "checkout_created", user.id, f"{plan.code} R${value} desc={discount_pct}%"
    )
    return payment, charge.payload, charge.encoded_image


# ------------------------------------------------------------------ liberacao

async def get_subscription(session: AsyncSession, user_id: int) -> Subscription | None:
    result = await session.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def confirm_payment(session: AsyncSession, bot: Bot, payment: Payment) -> bool:
    """Marca como pago e libera o acesso. Idempotente: webhook e botao podem correr juntos."""
    if payment.status == PaymentStatus.PAID:
        return False

    settings = get_settings()
    plan = settings.plan(payment.plan_code)
    if plan is None:
        log.error("Plano %s sumiu do plans.json", payment.plan_code)
        return False

    payment.status = PaymentStatus.PAID
    payment.paid_at = utcnow()

    now = utcnow()
    sub = await get_subscription(session, payment.user_id)
    renewed = False

    if sub is None:
        sub = Subscription(
            user_id=payment.user_id,
            plan_code=plan.code,
            status=SubStatus.ACTIVE,
            period_end=now + timedelta(days=plan.days),
        )
        session.add(sub)
    else:
        # se ainda esta no ar, soma no fim do periodo; se ja caiu, comeca de hoje
        base = sub.period_end if sub.period_end > now else now
        renewed = sub.status in {SubStatus.ACTIVE, SubStatus.GRACE}
        sub.period_end = base + timedelta(days=plan.days)
        sub.plan_code = plan.code
        sub.status = SubStatus.ACTIVE
        sub.warned_at = None
        sub.removed_at = None
        sub.renewals += 1

    await session.flush()
    await log_event(session, "payment_paid", payment.user_id, f"{plan.code} R${payment.value}")

    until = sub.period_end.astimezone(settings.tz).strftime("%d/%m/%Y")

    if renewed and await _is_member(bot, payment.user_id):
        await _safe_send(bot, payment.user_id, T.RENEWED.format(until=until))
    else:
        link = await access.create_single_use_invite(bot, payment.user_id)
        if link:
            sub.invite_link = link
            await _safe_send(
                bot,
                payment.user_id,
                T.PAYMENT_OK.format(until=until),
                reply_markup=kb.join_kb(link),
            )
        else:
            await _safe_send(
                bot,
                payment.user_id,
                "Pagamento confirmado, mas nao consegui gerar seu link de acesso. "
                "Ja avisei a equipe — voce recebe o link aqui em instantes.",
            )
            await _notify_admins(
                bot, f"FALHA ao gerar convite para o usuario {payment.user_id} apos pagamento."
            )
    return True


async def _is_member(bot: Bot, user_id: int) -> bool:
    s = get_settings()
    try:
        member = await bot.get_chat_member(chat_id=s.channel_id, user_id=user_id)
        return member.status in {"member", "administrator", "creator", "restricted"}
    except TelegramAPIError:
        return False


# ------------------------------------------------------------------ reembolso

async def process_refund(
    session: AsyncSession,
    bot: Bot,
    payment: Payment,
    *,
    reason: str = "Reembolso solicitado pelo assinante",
    client: AsaasClient | None = None,
) -> bool:
    client = client or AsaasClient()
    try:
        await client.refund(payment.asaas_id or "", description=reason)
    except Exception as exc:  # noqa: BLE001
        log.error("Estorno falhou para payment %s: %s", payment.id, exc)
        await _notify_admins(bot, f"Estorno FALHOU no pagamento #{payment.id}: {exc}")
        return False

    payment.status = PaymentStatus.REFUNDED
    payment.closed_at = utcnow()

    sub = await get_subscription(session, payment.user_id)
    if sub:
        sub.status = SubStatus.REFUNDED
        sub.removed_at = utcnow()
        sub.period_end = utcnow()

    await access.remove_from_channel(bot, payment.user_id, permanent=False)
    await log_event(session, "refund_done", payment.user_id, f"R${payment.value} — {reason}")
    await _safe_send(bot, payment.user_id, T.REFUND_OK)
    return True


async def handle_chargeback(session: AsyncSession, bot: Bot, payment: Payment) -> None:
    payment.status = PaymentStatus.CHARGEBACK
    payment.closed_at = utcnow()

    user = await session.get(User, payment.user_id)
    if user:
        user.blocked = True

    sub = await get_subscription(session, payment.user_id)
    if sub:
        sub.status = SubStatus.BANNED
        sub.removed_at = utcnow()
        sub.period_end = utcnow()

    await access.remove_from_channel(bot, payment.user_id, permanent=True)
    await log_event(session, "chargeback", payment.user_id, f"R${payment.value}")
    await _notify_admins(
        bot, f"Chargeback no pagamento #{payment.id} (R$ {kb.money(payment.value)}). "
             f"Usuario {payment.user_id} removido e bloqueado."
    )


async def open_refund_request(
    session: AsyncSession, bot: Bot, payment: Payment, reason: str
) -> RefundRequest:
    req = RefundRequest(user_id=payment.user_id, payment_id=payment.id, reason=reason)
    session.add(req)
    await session.flush()
    await _notify_admins(
        bot,
        f"Novo pedido de reembolso #{req.id}\n"
        f"Usuario: {payment.user_id}\n"
        f"Valor: R$ {kb.money(payment.value)}\n"
        f"Pago em: {payment.paid_at:%d/%m/%Y}\n"
        f"Motivo: {reason or '(nao informado)'}",
        reply_markup=kb.refund_decision_kb(req.id),
    )
    return req


# ------------------------------------------------------------------ utilitarios

async def _safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    """Envia sem derrubar o job se o usuario bloqueou o bot."""
    try:
        await bot.send_message(
            chat_id, text, parse_mode="Markdown",
            link_preview_options=kb.NO_PREVIEW, **kwargs
        )
        return True
    except TelegramAPIError as exc:
        log.info("Nao entreguei mensagem para %s: %s", chat_id, exc)
        return False


async def _notify_admins(bot: Bot, text: str, **kwargs) -> None:
    for admin_id in get_settings().admin_ids:
        await _safe_send(bot, admin_id, text, **kwargs)


safe_send = _safe_send
notify_admins = _notify_admins
