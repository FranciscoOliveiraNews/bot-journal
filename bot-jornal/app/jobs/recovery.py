"""Recuperacao de carrinho: 3 disparos para quem gerou Pix e nao pagou."""
from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.bot import keyboards as kb
from app.bot import texts as T
from app.config import get_settings
from app.db import session_scope
from app.models import Payment, PaymentStatus, RecoveryLog, utcnow
from app.services import billing

log = logging.getLogger(__name__)

# (etapa, minutos apos a criacao do Pix)
STEPS: list[tuple[int, int]] = [
    (1, 15),
    (2, 120),
    (3, 1440),
]

# Pix pendente por mais tempo que isso e considerado perdido
EXPIRE_AFTER_HOURS = 48


async def run(bot: Bot) -> dict[str, int]:
    """Roda a cada 5 minutos."""
    settings = get_settings()
    now = utcnow()
    stats = {"disparos": 0, "expirados": 0}

    async with session_scope() as session:
        for step, delay_min in STEPS:
            due_before = now - timedelta(minutes=delay_min)
            already = select(RecoveryLog.payment_id).where(RecoveryLog.step == step)
            result = await session.execute(
                select(Payment).where(
                    Payment.status == PaymentStatus.PENDING,
                    Payment.created_at <= due_before,
                    Payment.created_at >= now - timedelta(hours=EXPIRE_AFTER_HOURS),
                    Payment.id.not_in(already),
                )
            )
            for payment in result.scalars():
                # se o cara ja pagou outra cobranca no meio tempo, nao insiste
                paid = await session.execute(
                    select(Payment.id).where(
                        Payment.user_id == payment.user_id,
                        Payment.status == PaymentStatus.PAID,
                    ).limit(1)
                )
                if paid.scalar_one_or_none() is not None:
                    session.add(RecoveryLog(payment_id=payment.id, step=step))
                    continue

                sent = await _send_step(bot, payment, step, settings)
                session.add(RecoveryLog(payment_id=payment.id, step=step))
                if sent:
                    stats["disparos"] += 1
                    await billing.log_event(
                        session, f"recovery_{step}", payment.user_id, payment.plan_code
                    )

        # marca como perdidos os Pix antigos
        result = await session.execute(
            select(Payment).where(
                Payment.status == PaymentStatus.PENDING,
                Payment.created_at < now - timedelta(hours=EXPIRE_AFTER_HOURS),
            )
        )
        for payment in result.scalars():
            payment.status = PaymentStatus.EXPIRED
            payment.closed_at = now
            stats["expirados"] += 1

    if any(stats.values()):
        log.info("Recuperacao de carrinho: %s", stats)
    return stats


async def _send_step(bot: Bot, payment: Payment, step: int, settings) -> bool:
    plan = settings.plan(payment.plan_code)
    if plan is None:
        return False

    if step == 1:
        return await billing.safe_send(
            bot,
            payment.user_id,
            T.RECOVERY_1.format(value=kb.money(payment.value)),
            reply_markup=kb.recovery_kb(payment.plan_code),
        )
    if step == 2:
        return await billing.safe_send(
            bot,
            payment.user_id,
            T.RECOVERY_2.format(value=kb.money(payment.value)),
            reply_markup=kb.recovery_kb(payment.plan_code),
        )

    pct = settings.recovery_discount_pct
    novo = plan.price * (100 - pct) / 100
    return await billing.safe_send(
        bot,
        payment.user_id,
        T.RECOVERY_3.format(
            discount=pct, new_value=kb.money(novo), old_value=kb.money(plan.price)
        ),
        reply_markup=kb.recovery_kb(payment.plan_code, discount_pct=pct),
    )
