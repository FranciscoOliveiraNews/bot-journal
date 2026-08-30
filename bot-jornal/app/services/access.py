"""Controle de acesso ao canal: convite unico e remocao."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.config import get_settings

log = logging.getLogger(__name__)


async def create_single_use_invite(bot: Bot, user_id: int, *, hours_valid: int = 24) -> str | None:
    """Link de convite que serve para uma pessoa so e vence em 24h.

    member_limit=1 impede que o comprador repasse o link para o grupo do zap.
    """
    s = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=hours_valid)
    try:
        link = await bot.create_chat_invite_link(
            chat_id=s.channel_id,
            name=f"u{user_id}"[:32],
            expire_date=expire,
            member_limit=1,
            creates_join_request=False,
        )
        return link.invite_link
    except TelegramAPIError as exc:
        log.error("Falha ao criar convite para %s: %s", user_id, exc)
        return None


async def revoke_invite(bot: Bot, invite_link: str) -> None:
    s = get_settings()
    try:
        await bot.revoke_chat_invite_link(chat_id=s.channel_id, invite_link=invite_link)
    except TelegramAPIError as exc:
        log.warning("Nao consegui revogar convite: %s", exc)


async def remove_from_channel(bot: Bot, user_id: int, *, permanent: bool = False) -> bool:
    """Tira o usuario do canal.

    permanent=False -> ban seguido de unban: remove mas permite recomprar depois.
    permanent=True  -> ban de verdade (chargeback / fraude).
    """
    s = get_settings()
    try:
        await bot.ban_chat_member(chat_id=s.channel_id, user_id=user_id)
        if not permanent:
            await bot.unban_chat_member(
                chat_id=s.channel_id, user_id=user_id, only_if_banned=True
            )
        return True
    except TelegramAPIError as exc:
        # "user not found" e normal: o cara pagou e nunca entrou no canal
        log.info("Remocao de %s nao aplicada: %s", user_id, exc)
        return False


async def check_bot_permissions(bot: Bot) -> tuple[bool, str]:
    """Diagnostico usado pelo /diagnostico do admin."""
    s = get_settings()
    if not s.channel_id:
        return False, "CHANNEL_ID nao configurado."
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=s.channel_id, user_id=me.id)
    except TelegramAPIError as exc:
        return False, f"Nao consigo ler o canal: {exc}"

    if member.status != "administrator":
        return False, "O bot nao e administrador do canal."

    faltando = []
    if not getattr(member, "can_invite_users", False):
        faltando.append("Adicionar assinantes")
    if not getattr(member, "can_restrict_members", False):
        faltando.append("Banir usuarios")
    if faltando:
        return False, "Faltam permissoes: " + ", ".join(faltando)
    return True, "Canal e permissoes ok."
