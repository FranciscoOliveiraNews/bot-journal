"""Comandos do assinante: /status, /renovar, /reembolso, /ajuda."""
from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select

from app.bot import keyboards as kb
from app.bot import texts as T
from app.config import get_settings
from app.db import session_scope
from app.models import Payment, PaymentStatus, SubStatus, utcnow
from app.services import billing

router = Router(name="subscription")

STATUS_LABEL = {
    SubStatus.ACTIVE: "ativa",
    SubStatus.GRACE: "vencida (dentro da tolerancia)",
    SubStatus.EXPIRED: "expirada",
    SubStatus.REFUNDED: "reembolsada",
    SubStatus.BANNED: "bloqueada",
}


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    settings = get_settings()
    async with session_scope() as session:
        user = await billing.get_or_create_user(session, message.from_user)
        sub = await billing.get_subscription(session, user.id)
        if sub is None:
            await message.answer(T.NO_SUB, reply_markup=kb.subscribe_kb())
            return
        plan = settings.plan(sub.plan_code)
        text = T.STATUS.format(
            plan=plan.name if plan else sub.plan_code,
            status=STATUS_LABEL.get(sub.status, sub.status.value),
            until=sub.period_end.astimezone(settings.tz).strftime("%d/%m/%Y"),
            renewals=sub.renewals,
        )
        markup = kb.renew_kb(sub.plan_code) if sub.status != SubStatus.ACTIVE else None
    await message.answer(text, parse_mode="Markdown", reply_markup=markup)


@router.message(Command("renovar"))
async def cmd_renew(message: Message) -> None:
    async with session_scope() as session:
        user = await billing.get_or_create_user(session, message.from_user)
        sub = await billing.get_subscription(session, user.id)
        plan_code = sub.plan_code if sub else None
    if plan_code:
        await message.answer("Escolha como renovar:", reply_markup=kb.renew_kb(plan_code))
    else:
        await message.answer(T.NO_SUB, reply_markup=kb.subscribe_kb())


@router.message(Command("reembolso"))
async def cmd_refund(message: Message, command: CommandObject) -> None:
    settings = get_settings()
    reason = (command.args or "").strip()

    async with session_scope() as session:
        user = await billing.get_or_create_user(session, message.from_user)

        result = await session.execute(
            select(Payment)
            .where(Payment.user_id == user.id, Payment.status == PaymentStatus.PAID)
            .order_by(Payment.paid_at.desc())
            .limit(1)
        )
        payment = result.scalar_one_or_none()
        if payment is None:
            await message.answer("Nao encontrei nenhum pagamento ativo nessa conta.")
            return

        # ja usou o reembolso automatico alguma vez?
        used = await session.execute(
            select(Payment).where(
                Payment.user_id == user.id, Payment.status == PaymentStatus.REFUNDED
            ).limit(1)
        )
        already_refunded = used.scalar_one_or_none() is not None

        within_window = (
            payment.paid_at is not None
            and utcnow() - payment.paid_at <= timedelta(days=settings.refund_window_days)
        )

        if already_refunded:
            await billing.open_refund_request(session, message.bot, payment, reason)
            await message.answer(T.REFUND_DENIED_LIMIT)
            return

        if within_window:
            ok = await billing.process_refund(
                session, message.bot, payment,
                reason=reason or "Direito de arrependimento (CDC art. 49)",
            )
            if not ok:
                await message.answer(
                    "Nao consegui processar o estorno automatico agora. "
                    "Seu pedido foi registrado e a equipe resolve manualmente."
                )
                await billing.open_refund_request(session, message.bot, payment, reason)
            return

        await billing.open_refund_request(session, message.bot, payment, reason)
        await message.answer(T.REFUND_QUEUED.format(days=settings.refund_window_days))


@router.message(Command("ajuda"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "*Comandos*\n\n"
        "/status — ver sua assinatura e vencimento\n"
        "/renovar — renovar antes de vencer\n"
        "/reembolso — pedir reembolso (automatico nos primeiros "
        f"{get_settings().refund_window_days} dias)\n"
        "/start — ver os planos\n\n"
        "Qualquer outra coisa, escreve aqui que a gente responde.",
        parse_mode="Markdown",
    )
