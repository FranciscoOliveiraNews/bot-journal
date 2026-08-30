from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions,
)

from app.config import Plan, get_settings

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def plans_kb(discount_pct: int = 0) -> InlineKeyboardMarkup:
    s = get_settings()
    rows = []
    for plan in s.plans:
        label = plan.label
        if discount_pct:
            new_value = plan.price * (100 - discount_pct) / 100
            label = f"{plan.name} — R$ {_money(new_value)} (-{discount_pct}%)"
        if plan.highlight and not discount_pct:
            label = f"★ {label}"
        cb = f"buy:{plan.code}:{discount_pct}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def paid_check_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ja paguei", callback_data=f"check:{payment_id}")],
            [InlineKeyboardButton(text="Cancelar", callback_data="cancel")],
        ]
    )


def join_kb(invite_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Entrar no canal", url=invite_link)]]
    )


def renew_kb(plan_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Renovar agora", callback_data=f"buy:{plan_code}:0")],
            [InlineKeyboardButton(text="Ver outros planos", callback_data="plans")],
        ]
    )


def subscribe_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Ver planos", callback_data="plans")]]
    )


def recovery_kb(plan_code: str, discount_pct: int = 0) -> InlineKeyboardMarkup:
    text = "Gerar Pix com desconto" if discount_pct else "Finalizar pagamento"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=f"buy:{plan_code}:{discount_pct}")]
        ]
    )


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Hoje", callback_data="rep:today"),
                InlineKeyboardButton(text="7 dias", callback_data="rep:week"),
                InlineKeyboardButton(text="30 dias", callback_data="rep:month"),
            ],
            [InlineKeyboardButton(text="Fila de reembolso", callback_data="rep:refunds")],
            [InlineKeyboardButton(text="Exportar CSV", callback_data="rep:csv")],
        ]
    )


def refund_decision_kb(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Aprovar", callback_data=f"rf:ok:{request_id}"),
                InlineKeyboardButton(text="Negar", callback_data=f"rf:no:{request_id}"),
            ]
        ]
    )


def _money(value: float) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def money(value: float) -> str:
    return _money(value)


def plan_from_cb(code: str) -> Plan | None:
    return get_settings().plan(code)
