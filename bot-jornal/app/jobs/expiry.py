"""Ciclo de vida da assinatura: aviso D-1, tolerancia e remocao automatica."""
from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from sqlalchemy import select

from app.bot import keyboards as kb
from app.bot import texts as T
from app.config import get_settings
from app.db import session_scope
from app.models import SubStatus, Subscription, utcnow
from app.services import access, billing

log = logging.getLogger(__name__)


async def run(bot: Bot) -> dict[str, int]:
    """Roda de hora em hora. Devolve um resumo do que fez, util para log e teste."""
    settings = get_settings()
    now = utcnow()
    stats = {"avisados": 0, "em_tolerancia": 0, "removidos": 0}

    async with session_scope() as session:
        # 1) aviso de vencimento (D-1 por padrao)
        limite = now + timedelta(days=settings.warn_days_before)
        result = await session.execute(
            select(Subscription).where(
                Subscription.status == SubStatus.ACTIVE,
                Subscription.period_end <= limite,
                Subscription.period_end > now,
                Subscription.warned_at.is_(None),
            )
        )
        for sub in result.scalars():
            texto = T.WARN_EXPIRING.format(
                canal=T.CANAL_NOME,
                date=sub.period_end.astimezone(settings.tz).strftime("%d/%m"),
                grace=settings.grace_days,
            )
            await billing.safe_send(
                bot, sub.user_id, texto, reply_markup=kb.renew_kb(sub.plan_code)
            )
            sub.warned_at = now
            stats["avisados"] += 1

        # 2) venceu -> entra na tolerancia (continua no canal)
        result = await session.execute(
            select(Subscription).where(
                Subscription.status == SubStatus.ACTIVE,
                Subscription.period_end <= now,
            )
        )
        for sub in result.scalars():
            sub.status = SubStatus.GRACE
            stats["em_tolerancia"] += 1
            await billing.log_event(session, "grace_start", sub.user_id, sub.plan_code)

        # 3) acabou a tolerancia -> sai do canal
        corte = now - timedelta(days=settings.grace_days)
        result = await session.execute(
            select(Subscription).where(
                Subscription.status == SubStatus.GRACE,
                Subscription.period_end <= corte,
            )
        )
        for sub in result.scalars():
            await access.remove_from_channel(bot, sub.user_id, permanent=False)
            if sub.invite_link:
                await access.revoke_invite(bot, sub.invite_link)
                sub.invite_link = None
            sub.status = SubStatus.EXPIRED
            sub.removed_at = now
            await billing.safe_send(
                bot,
                sub.user_id,
                T.REMOVED.format(canal=T.CANAL_NOME),
                reply_markup=kb.renew_kb(sub.plan_code),
            )
            await billing.log_event(session, "removed_expired", sub.user_id, sub.plan_code)
            stats["removidos"] += 1

    if any(stats.values()):
        log.info("Ciclo de assinatura: %s", stats)
    return stats
