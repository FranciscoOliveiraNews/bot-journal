"""Ajuda de configuracao: descobrir o ID do canal usando o proprio bot.

Evita depender de bots de terceiros para ler o ID do seu canal.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.config import get_settings

log = logging.getLogger(__name__)
router = Router(name="setup")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """Mostra o ID de quem falou e do chat atual."""
    linhas = [
        f"Seu ID de usuario: `{message.from_user.id}`",
        f"ID deste chat: `{message.chat.id}`",
    ]
    await message.answer("\n".join(linhas), parse_mode="Markdown")


@router.message(F.forward_origin, F.chat.type == ChatType.PRIVATE)
async def on_forward(message: Message) -> None:
    """Encaminhe um post do canal para o bot e ele devolve o ID do canal."""
    if not get_settings().is_admin(message.from_user.id):
        return

    origem = message.forward_origin
    chat = getattr(origem, "chat", None) or getattr(origem, "sender_chat", None)

    if chat is None:
        await message.answer(
            "Essa mensagem veio de uma pessoa, nao de um canal — e o Telegram nao "
            "expoe o ID nesse caso.\n\n"
            "Encaminhe um *post publicado no canal* que eu leio o ID dele.",
            parse_mode="Markdown",
        )
        return

    await message.answer(
        f"*{chat.title or 'Chat'}*\n\n"
        f"CHANNEL\\_ID = `{chat.id}`\n"
        f"Tipo: {chat.type}\n\n"
        f"Copie esse numero e cole na variavel `CHANNEL_ID`.",
        parse_mode="Markdown",
    )


@router.channel_post()
async def on_channel_post(message: Message) -> None:
    """Enquanto CHANNEL_ID nao estiver configurado, avisa os admins qual e o ID.

    Assim que voce configurar, esse aviso para sozinho.
    """
    settings = get_settings()
    if settings.channel_id:
        return

    texto = (
        f"Detectei um post no canal *{message.chat.title or 'sem titulo'}*.\n\n"
        f"CHANNEL\\_ID = `{message.chat.id}`\n\n"
        f"Cole esse valor na variavel `CHANNEL_ID` e faca o deploy de novo."
    )
    for admin_id in settings.admin_ids:
        try:
            await message.bot.send_message(admin_id, texto, parse_mode="Markdown")
        except Exception:  # noqa: BLE001
            log.info("Nao consegui avisar o admin %s do ID do canal", admin_id)
