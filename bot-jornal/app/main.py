"""Entrada unica: sobe a API do webhook, o bot em polling e os jobs agendados."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

from app.bot.handlers import admin, checkout, setup, subscription
from app.config import get_settings
from app.db import init_db
from app.jobs import expiry, recovery
from app.webhook import router as webhook_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("jornal-bot")


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin.router)        # admin primeiro: comandos privilegiados
    dp.include_router(subscription.router)
    dp.include_router(checkout.router)
    dp.include_router(setup.router)   # por ultimo: nao pode atropelar o FSM do checkout
    return dp


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN nao configurado. Veja o .env.example.")

    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = build_dispatcher()
    app.state.bot = bot

    scheduler = AsyncIOScheduler(timezone=settings.tz)
    scheduler.add_job(
        recovery.run, IntervalTrigger(minutes=5), args=[bot],
        id="recovery", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        expiry.run, CronTrigger(minute=7), args=[bot],
        id="expiry", max_instances=1, coalesce=True,
    )
    scheduler.start()

    polling = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    log.info("Bot no ar. Ambiente Asaas: %s", settings.asaas_env)

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await dp.stop_polling()
        polling.cancel()
        await bot.session.close()
        log.info("Encerrado.")


app = FastAPI(title="Bot de assinatura — Telegram", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/")
async def root():
    return {"service": "jornal-bot", "status": "running"}
