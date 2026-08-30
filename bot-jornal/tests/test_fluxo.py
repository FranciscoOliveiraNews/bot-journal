"""Testes de ponta a ponta do fluxo de assinatura."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.jobs import expiry, recovery
from app.models import (
    Payment, PaymentStatus, RecoveryLog, RefundRequest, SubStatus, Subscription, User, utcnow,
)
from app.services import billing
from app.services.asaas import validate_cpf


async def _checkout(tg_user, gateway, plan_code="mensal", discount=0, cpf="52998224725"):
    settings = get_settings()
    plan = settings.plan(plan_code)
    async with session_scope() as session:
        user = await billing.get_or_create_user(session, tg_user)
        user.cpf = cpf
        payment, payload, _ = await billing.create_checkout(
            session, user, plan, discount_pct=discount, client=gateway
        )
        return payment.id, payload


# ------------------------------------------------------------------ checkout

async def test_checkout_gera_pix_pendente(db, tg_user, gateway):
    payment_id, payload = await _checkout(tg_user, gateway)
    assert payload.startswith("00020126")

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        assert payment.status is PaymentStatus.PENDING
        assert payment.value_cents == 2790          # R$ 27,90
        assert payment.asaas_id == "pay_1"
        assert payment.is_renewal is False


async def test_desconto_da_recuperacao_reduz_o_valor(db, tg_user, gateway):
    payment_id, _ = await _checkout(tg_user, gateway, discount=30)
    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        assert payment.value_cents == 1953          # 27,90 - 30%
        assert payment.discount_pct == 30


async def test_plano_anual_usa_o_preco_certo(db, tg_user, gateway):
    payment_id, _ = await _checkout(tg_user, gateway, plan_code="anual")
    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        assert payment.value_cents == 27900


# ------------------------------------------------------------- liberacao

async def test_pagamento_confirmado_libera_acesso(db, tg_user, gateway, bot):
    payment_id, _ = await _checkout(tg_user, gateway)

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        await billing.confirm_payment(session, bot, payment)

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        sub = await billing.get_subscription(session, tg_user.id)

    assert payment.status is PaymentStatus.PAID
    assert sub.status is SubStatus.ACTIVE
    assert 29 <= (sub.period_end - utcnow()).days <= 30
    assert bot.invites, "deveria ter gerado link de convite"
    assert "Pagamento confirmado" in bot.texts_for(tg_user.id)[0]


async def test_confirmacao_e_idempotente(db, tg_user, gateway, bot):
    """Webhook e botao 'ja paguei' podem chegar juntos — nao pode dobrar o periodo."""
    payment_id, _ = await _checkout(tg_user, gateway)

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        primeira = await billing.confirm_payment(session, bot, payment)

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        segunda = await billing.confirm_payment(session, bot, payment)
        sub = await billing.get_subscription(session, tg_user.id)

    assert primeira is True and segunda is False
    assert (sub.period_end - utcnow()).days <= 30
    assert len(bot.invites) == 1


async def test_renovacao_soma_no_fim_do_periodo(db, tg_user, gateway, bot):
    p1, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        await billing.confirm_payment(session, bot, await session.get(Payment, p1))

    bot.member_status = "member"  # ja esta no canal
    p2, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        await billing.confirm_payment(session, bot, await session.get(Payment, p2))
        sub = await billing.get_subscription(session, tg_user.id)

    assert 59 <= (sub.period_end - utcnow()).days <= 60
    assert sub.renewals == 1
    assert len(bot.invites) == 1, "renovacao de quem ja esta no canal nao gera convite novo"
    assert any("Renovacao confirmada" in t for t in bot.texts_for(tg_user.id))


# ------------------------------------------------------------- ciclo de vida

async def test_aviso_dispara_um_dia_antes_e_so_uma_vez(db, tg_user, gateway, bot):
    payment_id, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        await billing.confirm_payment(session, bot, await session.get(Payment, payment_id))
        sub = await billing.get_subscription(session, tg_user.id)
        sub.period_end = utcnow() + timedelta(hours=12)

    stats = await expiry.run(bot)
    assert stats["avisados"] == 1
    assert any("vence *amanha*" in t for t in bot.texts_for(tg_user.id))

    stats = await expiry.run(bot)
    assert stats["avisados"] == 0, "nao pode avisar de novo"


async def test_tolerancia_de_dois_dias_e_respeitada(db, tg_user, gateway, bot):
    payment_id, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        await billing.confirm_payment(session, bot, await session.get(Payment, payment_id))
        sub = await billing.get_subscription(session, tg_user.id)
        sub.period_end = utcnow() - timedelta(hours=1)   # venceu agora

    stats = await expiry.run(bot)
    assert stats["em_tolerancia"] == 1 and stats["removidos"] == 0

    # 1 dia depois do vencimento: ainda dentro da tolerancia
    async with session_scope() as session:
        sub = await billing.get_subscription(session, tg_user.id)
        sub.period_end = utcnow() - timedelta(days=1)
    stats = await expiry.run(bot)
    assert stats["removidos"] == 0, "removeu antes dos 2 dias de tolerancia"
    assert not bot.banned

    # 3 dias depois: passou da tolerancia
    async with session_scope() as session:
        sub = await billing.get_subscription(session, tg_user.id)
        sub.period_end = utcnow() - timedelta(days=3)
    stats = await expiry.run(bot)

    assert stats["removidos"] == 1
    assert bot.banned == [tg_user.id]
    assert bot.unbanned == [tg_user.id], "deve poder recomprar depois"

    async with session_scope() as session:
        sub = await billing.get_subscription(session, tg_user.id)
        assert sub.status is SubStatus.EXPIRED
        assert sub.invite_link is None


async def test_quem_renovou_na_tolerancia_nao_e_removido(db, tg_user, gateway, bot):
    payment_id, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        await billing.confirm_payment(session, bot, await session.get(Payment, payment_id))
        sub = await billing.get_subscription(session, tg_user.id)
        sub.period_end = utcnow() - timedelta(days=1)
        sub.status = SubStatus.GRACE

    bot.member_status = "member"
    p2, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        await billing.confirm_payment(session, bot, await session.get(Payment, p2))

    stats = await expiry.run(bot)
    assert stats["removidos"] == 0
    assert not bot.banned


# --------------------------------------------------------- recuperacao

async def test_recuperacao_dispara_nas_tres_etapas(db, tg_user, gateway, bot):
    payment_id, _ = await _checkout(tg_user, gateway)

    async def envelhecer(minutos):
        async with session_scope() as session:
            payment = await session.get(Payment, payment_id)
            payment.created_at = utcnow() - timedelta(minutes=minutos)

    await envelhecer(5)
    assert (await recovery.run(bot))["disparos"] == 0, "cedo demais"

    await envelhecer(20)
    assert (await recovery.run(bot))["disparos"] == 1
    assert "ainda esta valido" in bot.texts_for(tg_user.id)[-1]

    await envelhecer(180)
    assert (await recovery.run(bot))["disparos"] == 1

    await envelhecer(1500)
    assert (await recovery.run(bot))["disparos"] == 1
    ultima = bot.texts_for(tg_user.id)[-1]
    assert "30% de desconto" in ultima
    assert "19,53" in ultima, "valor com desconto errado"

    assert (await recovery.run(bot))["disparos"] == 0, "nao pode repetir etapa"

    async with session_scope() as session:
        logs = (await session.execute(select(RecoveryLog))).scalars().all()
        assert sorted(l.step for l in logs) == [1, 2, 3]


async def test_recuperacao_nao_incomoda_quem_ja_pagou(db, tg_user, gateway, bot):
    p1, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        await billing.confirm_payment(session, bot, await session.get(Payment, p1))

    p2, _ = await _checkout(tg_user, gateway)  # gerou outro Pix e nao pagou
    async with session_scope() as session:
        payment = await session.get(Payment, p2)
        payment.created_at = utcnow() - timedelta(minutes=30)

    antes = len(bot.sent)
    stats = await recovery.run(bot)
    assert stats["disparos"] == 0
    assert len(bot.sent) == antes


async def test_pix_velho_vira_perdido(db, tg_user, gateway, bot):
    payment_id, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        payment.created_at = utcnow() - timedelta(hours=72)

    stats = await recovery.run(bot)
    assert stats["expirados"] == 1
    async with session_scope() as session:
        assert (await session.get(Payment, payment_id)).status is PaymentStatus.EXPIRED


# ------------------------------------------------------------- reembolso

async def test_reembolso_dentro_da_janela_e_automatico(db, tg_user, gateway, bot):
    payment_id, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        await billing.confirm_payment(session, bot, payment)

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        ok = await billing.process_refund(session, bot, payment, client=gateway)

    assert ok
    assert gateway.refunded == ["pay_1"]
    assert bot.banned == [tg_user.id]
    assert bot.unbanned == [tg_user.id]

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        sub = await billing.get_subscription(session, tg_user.id)
    assert payment.status is PaymentStatus.REFUNDED
    assert sub.status is SubStatus.REFUNDED
    assert sub.period_end <= utcnow()


async def test_falha_no_gateway_nao_remove_o_acesso(db, tg_user, gateway, bot):
    payment_id, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        await billing.confirm_payment(session, bot, await session.get(Payment, payment_id))

    gateway.fail_refund = True
    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        ok = await billing.process_refund(session, bot, payment, client=gateway)

    assert ok is False
    assert not bot.banned, "nao pode tirar do canal se o dinheiro nao voltou"
    async with session_scope() as session:
        assert (await session.get(Payment, payment_id)).status is PaymentStatus.PAID


async def test_chargeback_bane_em_definitivo(db, tg_user, gateway, bot):
    payment_id, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        await billing.confirm_payment(session, bot, await session.get(Payment, payment_id))

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        await billing.handle_chargeback(session, bot, payment)

    async with session_scope() as session:
        user = await session.get(User, tg_user.id)
        sub = await billing.get_subscription(session, tg_user.id)

    assert user.blocked is True
    assert sub.status is SubStatus.BANNED
    assert bot.banned == [tg_user.id]
    assert bot.unbanned == [], "chargeback e ban permanente"


async def test_pedido_fora_da_janela_vai_para_a_fila(db, tg_user, gateway, bot):
    payment_id, _ = await _checkout(tg_user, gateway)
    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        await billing.confirm_payment(session, bot, payment)
        payment.paid_at = utcnow() - timedelta(days=20)

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        req = await billing.open_refund_request(session, bot, payment, "nao gostei")

    async with session_scope() as session:
        pedidos = (await session.execute(select(RefundRequest))).scalars().all()

    assert len(pedidos) == 1 and pedidos[0].status == "OPEN"
    assert any("Novo pedido de reembolso" in t for t in bot.texts_for(999))


# ------------------------------------------------------------- utilitarios

@pytest.mark.parametrize("cpf,valido", [
    ("529.982.247-25", True),
    ("52998224725", True),
    ("111.111.111-11", False),
    ("12345678900", False),
    ("123", False),
    ("", False),
])
def test_validacao_de_cpf(cpf, valido):
    assert validate_cpf(cpf) is valido
