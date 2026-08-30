"""Painel do admin: relatorios, fila de reembolso, diagnostico, broadcast."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select

from app.bot import keyboards as kb
from app.config import get_settings
from app.db import session_scope
from app.models import Payment, RefundRequest, SubStatus, Subscription, User, utcnow
from app.services import access, billing, reports

log = logging.getLogger(__name__)
router = Router(name="admin")

PERIODS = {"today": (1, "Hoje"), "week": (7, "Ultimos 7 dias"), "month": (30, "Ultimos 30 dias")}


def _is_admin(user_id: int) -> bool:
    return get_settings().is_admin(user_id)


@router.message(Command("painel"))
async def cmd_panel(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    async with session_scope() as session:
        report = await reports.build(session, 1, "Hoje")
        text = reports.format_report(report)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.admin_kb())


@router.callback_query(F.data.startswith("rep:"))
async def cb_report(query: CallbackQuery) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer()
        return

    what = query.data.split(":")[1]
    await query.answer()

    if what in PERIODS:
        days, label = PERIODS[what]
        async with session_scope() as session:
            report = await reports.build(session, days, label)
            text = reports.format_report(report)
        await query.message.edit_text(
            text, parse_mode="Markdown", reply_markup=kb.admin_kb()
        )
        return

    if what == "csv":
        async with session_scope() as session:
            data = await reports.export_csv(session)
        stamp = utcnow().astimezone(get_settings().tz).strftime("%Y-%m-%d")
        await query.message.answer_document(
            BufferedInputFile(data, filename=f"vendas-{stamp}.csv"),
            caption="Todas as cobrancas, separadas por ponto e virgula (abre direto no Excel).",
        )
        return

    if what == "refunds":
        async with session_scope() as session:
            pendentes = await reports.open_refunds(session)
        if not pendentes:
            await query.message.answer("Nenhum pedido de reembolso na fila.")
            return
        for req, payment, user in pendentes[:20]:
            await query.message.answer(
                f"*Pedido #{req.id}*\n"
                f"Usuario: {user.first_name or ''} "
                f"(@{user.username or 'sem username'} · `{user.id}`)\n"
                f"Valor: R$ {kb.money(payment.value)}\n"
                f"Pago em: {payment.paid_at:%d/%m/%Y}\n"
                f"Motivo: {req.reason or '(nao informado)'}",
                parse_mode="Markdown",
                reply_markup=kb.refund_decision_kb(req.id),
            )


@router.callback_query(F.data.startswith("rf:"))
async def cb_refund_decision(query: CallbackQuery) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer()
        return

    _, decision, raw_id = query.data.split(":")
    request_id = int(raw_id)

    async with session_scope() as session:
        req = await session.get(RefundRequest, request_id)
        if req is None or req.status != "OPEN":
            await query.answer("Pedido ja resolvido.", show_alert=True)
            return

        payment = await session.get(Payment, req.payment_id)
        req.resolved_at = utcnow()
        req.resolved_by = query.from_user.id

        if decision == "ok" and payment is not None:
            ok = await billing.process_refund(
                session, query.bot, payment, reason=f"Aprovado por admin {query.from_user.id}"
            )
            req.status = "APPROVED" if ok else "OPEN"
            resultado = "Reembolso aprovado e processado." if ok else "Falhou no gateway."
        else:
            req.status = "DENIED"
            resultado = "Pedido negado."
            await billing.safe_send(
                query.bot,
                req.user_id,
                "Analisamos seu pedido de reembolso e ele nao se enquadra na politica "
                "de devolucao. Seu acesso continua ativo ate o fim do periodo pago.",
            )

    await query.answer(resultado, show_alert=True)
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass


@router.message(Command("diagnostico"))
async def cmd_diag(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    settings = get_settings()
    ok, detalhe = await access.check_bot_permissions(message.bot)
    linhas = [
        f"Canal: {'ok' if ok else 'PROBLEMA'} — {detalhe}",
        f"Ambiente Asaas: *{settings.asaas_env}*",
        f"Planos: {', '.join(f'{p.name} R$ {kb.money(p.price)}' for p in settings.plans)}",
        f"Tolerancia: {settings.grace_days} dias · aviso D-{settings.warn_days_before}",
        f"Janela de reembolso: {settings.refund_window_days} dias",
        f"Modo silencio: {'LIGADO' if settings.quiet_mode else 'desligado'}",
        f"Admins: {len(settings.admin_ids)}",
    ]
    await message.answer("\n".join(linhas), parse_mode="Markdown")


@router.message(Command("avisar"))
async def cmd_broadcast(message: Message, command: CommandObject) -> None:
    """/avisar <texto> — manda para todos os assinantes ativos."""
    if not _is_admin(message.from_user.id):
        return
    texto = (command.args or "").strip()
    if not texto:
        await message.answer("Use: /avisar sua mensagem aqui")
        return

    async with session_scope() as session:
        result = await session.execute(
            select(Subscription.user_id).where(
                Subscription.status.in_([SubStatus.ACTIVE, SubStatus.GRACE])
            )
        )
        destinatarios = list(result.scalars())

    enviados = 0
    for user_id in destinatarios:
        if await billing.safe_send(message.bot, user_id, texto):
            enviados += 1
    await message.answer(f"Enviado para {enviados} de {len(destinatarios)} assinantes.")
