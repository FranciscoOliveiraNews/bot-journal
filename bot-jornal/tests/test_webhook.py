"""Testes do endpoint que recebe os eventos do Asaas."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import session_scope
from app.models import Payment, PaymentStatus, SubStatus, User
from app.services import billing
from app.webhook import router as webhook_router

TOKEN = "token-secreto"


@pytest.fixture
def client(bot):
    app = FastAPI()
    app.include_router(webhook_router)
    app.state.bot = bot
    return TestClient(app)


async def _pix_pendente(tg_user, gateway):
    settings = get_settings()
    async with session_scope() as session:
        user = await billing.get_or_create_user(session, tg_user)
        user.cpf = "52998224725"
        payment, _, _ = await billing.create_checkout(
            session, user, settings.plan("mensal"), client=gateway
        )
        return payment.id, payment.asaas_id


def _post(client, event, asaas_id, token=TOKEN):
    headers = {"asaas-access-token": token} if token else {}
    return client.post(
        "/webhook/asaas",
        json={"event": event, "payment": {"id": asaas_id, "value": 27.90}},
        headers=headers,
    )


async def test_webhook_sem_token_e_recusado(db, tg_user, gateway, client):
    _, asaas_id = await _pix_pendente(tg_user, gateway)
    assert _post(client, "PAYMENT_RECEIVED", asaas_id, token=None).status_code == 401
    assert _post(client, "PAYMENT_RECEIVED", asaas_id, token="errado").status_code == 401

    async with session_scope() as session:
        assert (await session.get(Payment, 1)).status is PaymentStatus.PENDING


async def test_webhook_confirma_pagamento_e_libera(db, tg_user, gateway, client, bot):
    payment_id, asaas_id = await _pix_pendente(tg_user, gateway)

    resp = _post(client, "PAYMENT_RECEIVED", asaas_id)
    assert resp.status_code == 200

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        sub = await billing.get_subscription(session, tg_user.id)

    assert payment.status is PaymentStatus.PAID
    assert sub.status is SubStatus.ACTIVE
    assert bot.invites, "webhook deveria ter gerado o convite"


async def test_webhook_repetido_nao_duplica_periodo(db, tg_user, gateway, client, bot):
    payment_id, asaas_id = await _pix_pendente(tg_user, gateway)

    _post(client, "PAYMENT_RECEIVED", asaas_id)
    async with session_scope() as session:
        primeiro = (await billing.get_subscription(session, tg_user.id)).period_end

    _post(client, "PAYMENT_CONFIRMED", asaas_id)
    async with session_scope() as session:
        segundo = (await billing.get_subscription(session, tg_user.id)).period_end

    assert primeiro == segundo
    assert len(bot.invites) == 1


async def test_webhook_de_estorno_remove_do_canal(db, tg_user, gateway, client, bot):
    payment_id, asaas_id = await _pix_pendente(tg_user, gateway)
    _post(client, "PAYMENT_RECEIVED", asaas_id)

    resp = _post(client, "PAYMENT_REFUNDED", asaas_id)
    assert resp.status_code == 200

    async with session_scope() as session:
        payment = await session.get(Payment, payment_id)
        sub = await billing.get_subscription(session, tg_user.id)

    assert payment.status is PaymentStatus.REFUNDED
    assert sub.status is SubStatus.REFUNDED
    assert bot.banned == [tg_user.id]


async def test_webhook_de_chargeback_bloqueia(db, tg_user, gateway, client, bot):
    payment_id, asaas_id = await _pix_pendente(tg_user, gateway)
    _post(client, "PAYMENT_RECEIVED", asaas_id)

    _post(client, "PAYMENT_CHARGEBACK_REQUESTED", asaas_id)

    async with session_scope() as session:
        user = await session.get(User, tg_user.id)
        sub = await billing.get_subscription(session, tg_user.id)

    assert user.blocked is True
    assert sub.status is SubStatus.BANNED
    assert bot.unbanned == []


async def test_cobranca_de_fora_do_bot_e_ignorada(db, tg_user, gateway, client):
    resp = _post(client, "PAYMENT_RECEIVED", "pay_de_outro_sistema")
    assert resp.status_code == 200
    assert "ignored" in resp.json()


async def test_pix_vencido_marca_como_perdido(db, tg_user, gateway, client):
    payment_id, asaas_id = await _pix_pendente(tg_user, gateway)
    _post(client, "PAYMENT_OVERDUE", asaas_id)
    async with session_scope() as session:
        assert (await session.get(Payment, payment_id)).status is PaymentStatus.EXPIRED
