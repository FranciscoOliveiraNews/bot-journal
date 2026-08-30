"""Funil: /start -> plano -> CPF -> Pix -> acesso."""
from __future__ import annotations

import base64
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot import texts as T
from app.config import get_settings
from app.db import session_scope
from app.models import Payment, PaymentStatus, SubStatus
from app.services import billing
from app.services.asaas import AsaasClient, validate_cpf

log = logging.getLogger(__name__)
router = Router(name="checkout")


class Checkout(StatesGroup):
    cpf = State()
    email = State()


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject | None, state: FSMContext) -> None:
    await state.clear()
    source = (command.args if command else None) or "organico"
    async with session_scope() as session:
        user = await billing.get_or_create_user(session, message.from_user, source=source)
        await billing.log_event(session, "start", user.id, source)
        blocked = user.blocked

    if blocked:
        await message.answer(T.BLOCKED)
        return
    if get_settings().quiet_mode:
        await message.answer(T.QUIET_MODE)
        return

    await message.answer(
        T.WELCOME, parse_mode="Markdown",
        reply_markup=kb.plans_kb(), link_preview_options=kb.NO_PREVIEW,
    )


@router.callback_query(F.data == "plans")
async def cb_plans(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer(
        T.WELCOME, parse_mode="Markdown",
        reply_markup=kb.plans_kb(), link_preview_options=kb.NO_PREVIEW,
    )


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(query: CallbackQuery, state: FSMContext) -> None:
    _, plan_code, discount_raw = query.data.split(":")
    discount = int(discount_raw)
    settings = get_settings()
    plan = settings.plan(plan_code)
    if plan is None:
        await query.answer("Plano indisponivel.", show_alert=True)
        return

    await query.answer()

    async with session_scope() as session:
        user = await billing.get_or_create_user(session, query.from_user)
        blocked, has_cpf = user.blocked, bool(user.cpf)

    if blocked:
        await query.message.answer(T.BLOCKED)
        return
    if settings.quiet_mode:
        await query.message.answer(T.QUIET_MODE)
        return

    if not has_cpf:
        await state.set_state(Checkout.cpf)
        await state.update_data(plan_code=plan_code, discount=discount)
        await query.message.answer(T.ASK_CPF, parse_mode="Markdown")
        return

    await _generate_pix(query.message, query.from_user, plan_code, discount)


@router.message(Checkout.cpf, F.text)
async def on_cpf(message: Message, state: FSMContext) -> None:
    if not validate_cpf(message.text):
        await message.answer(T.CPF_INVALID)
        return

    cpf = "".join(c for c in message.text if c.isdigit())
    async with session_scope() as session:
        user = await billing.get_or_create_user(session, message.from_user)
        user.cpf = cpf

    await state.set_state(Checkout.email)
    await message.answer(T.ASK_EMAIL, parse_mode="Markdown")


@router.message(Checkout.email, Command("pular"))
@router.message(Checkout.email, F.text)
async def on_email(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text != "/pular":
        if "@" not in text or "." not in text.split("@")[-1]:
            await message.answer("Esse e-mail nao parece valido. Tenta de novo ou manda /pular.")
            return
        async with session_scope() as session:
            user = await billing.get_or_create_user(session, message.from_user)
            user.email = text[:180]

    data = await state.get_data()
    await state.clear()
    await _generate_pix(
        message, message.from_user, data.get("plan_code", ""), int(data.get("discount", 0))
    )


async def _generate_pix(message: Message, tg_user, plan_code: str, discount: int) -> None:
    settings = get_settings()
    plan = settings.plan(plan_code)
    if plan is None:
        await message.answer("Plano indisponivel. Toque em /start para recomecar.")
        return

    status = await message.answer(T.GENERATING)
    client = AsaasClient()

    try:
        async with session_scope() as session:
            user = await billing.get_or_create_user(session, tg_user)
            payment, payload, qr_b64 = await billing.create_checkout(
                session, user, plan, discount_pct=discount, client=client
            )
            payment_id, value = payment.id, payment.value
    except Exception as exc:  # noqa: BLE001
        log.exception("Falha ao gerar Pix")
        await status.edit_text(
            "Nao consegui gerar o Pix agora. Tenta de novo em um minuto — "
            "se persistir, me chama que a equipe resolve."
        )
        await billing.notify_admins(message.bot, f"ERRO ao gerar Pix: {exc}")
        return

    await status.delete()
    caption = T.PIX_READY.format(plan_name=plan.name, value=kb.money(value))

    if qr_b64:
        await message.answer_photo(
            BufferedInputFile(base64.b64decode(qr_b64), filename="pix.png"),
            caption=caption,
            parse_mode="Markdown",
        )
    else:
        await message.answer(caption, parse_mode="Markdown")

    await message.answer(
        f"{T.PIX_COPIA_COLA}\n\n`{payload}`",
        parse_mode="Markdown",
        reply_markup=kb.paid_check_kb(payment_id),
        link_preview_options=kb.NO_PREVIEW,
    )


@router.callback_query(F.data.startswith("check:"))
async def cb_check(query: CallbackQuery) -> None:
    payment_id = int(query.data.split(":")[1])
    client = AsaasClient()

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        if payment is None:
            await query.answer("Cobranca nao encontrada.", show_alert=True)
            return
        if payment.status == PaymentStatus.PAID:
            await query.answer("Pagamento ja confirmado.", show_alert=True)
            return

        try:
            paid = await client.is_paid(payment.asaas_id or "")
        except Exception:  # noqa: BLE001
            await query.answer(T.NOT_PAID_YET, show_alert=True)
            return

        if not paid:
            await query.answer(T.NOT_PAID_YET, show_alert=True)
            return

        await billing.confirm_payment(session, query.bot, payment)

    await query.answer("Pagamento confirmado.")


@router.callback_query(F.data == "cancel")
async def cb_cancel(query: CallbackQuery) -> None:
    await query.answer("Cancelado.")
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
