"""Main DM menu: Profile / Stats / Shop / Rating / Achievements / Settings / Language / Rules / Help.

All navigation via inline keyboards. Opens on /menu or /start in private chat.
"""
from __future__ import annotations

from contextlib import suppress
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from sqlalchemy import select

from bot.keyboards import language_keyboard
from db.models import Language, Perk, PerkTranslation, User, UserPerk
from db.session import SessionLocal
from services import stats as stats_service
from services.i18n import t

router = Router()


# ------------- helpers -------------

async def _user(tg_id: int) -> User | None:
    async with SessionLocal() as s:
        return (await s.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()


async def _lang_for(tg_id: int) -> str:
    u = await _user(tg_id)
    return u.language.value if u else "en"


def _menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    def b(label_key: str, data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=t(lang, label_key), callback_data=data)
    return InlineKeyboardMarkup(inline_keyboard=[
        [b("menu_profile", "menu:profile"), b("menu_stats", "menu:stats")],
        [b("menu_shop", "menu:shop"), b("menu_rating", "menu:rating")],
        [b("menu_achievements", "menu:ach"), b("menu_inventory", "menu:inv")],
        [b("menu_language", "menu:lang"), b("menu_rules", "menu:rules")],
        [b("menu_help", "menu:help")],
    ])


def _back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="menu:home")],
    ])


# ------------- /menu command -------------

@router.message(Command("menu"))
async def cmd_menu(msg: Message) -> None:
    if msg.chat.type != "private":
        return
    lang = await _lang_for(msg.from_user.id)
    await msg.answer(t(lang, "menu_header", name=msg.from_user.full_name),
                     reply_markup=_menu_keyboard(lang))


@router.callback_query(F.data == "menu:home")
async def cb_home(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    with suppress(Exception):
        await cb.message.edit_text(
            t(lang, "menu_header", name=cb.from_user.full_name),
            reply_markup=_menu_keyboard(lang),
        )
    await cb.answer()


# ------------- profile -------------

@router.callback_query(F.data == "menu:profile")
async def cb_profile(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    u = await _user(cb.from_user.id)
    if not u:
        await cb.answer(t(lang, "use_start"), show_alert=True)
        return
    async with SessionLocal() as s:
        st = await stats_service.get_stats(s, u.user_id)
    text = t(lang, "profile_body",
             name=cb.from_user.full_name,
             elo=u.elo,
             coins=u.coin_balance,
             streak=u.daily_streak,
             games=st.games_played,
             wins=st.wins,
             losses=st.losses)
    with suppress(Exception):
        await cb.message.edit_text(text, reply_markup=_back_keyboard(lang))
    await cb.answer()


# ------------- stats -------------

@router.callback_query(F.data == "menu:stats")
async def cb_stats(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    u = await _user(cb.from_user.id)
    if not u:
        await cb.answer(t(lang, "use_start"), show_alert=True)
        return
    async with SessionLocal() as s:
        st = await stats_service.get_stats(s, u.user_id)
    wr = (st.wins / st.games_played * 100) if st.games_played else 0
    text = t(lang, "stats_body",
             games=st.games_played, wins=st.wins, losses=st.losses,
             wr=f"{wr:.1f}", deaths=st.deaths,
             town_w=st.town_wins, mafia_w=st.mafia_wins, maniac_w=st.maniac_wins,
             as_don=st.as_don, as_mafia=st.as_mafia, as_sheriff=st.as_sheriff,
             as_doctor=st.as_doctor, as_lover=st.as_lover,
             as_maniac=st.as_maniac, as_civilian=st.as_civilian)
    with suppress(Exception):
        await cb.message.edit_text(text, reply_markup=_back_keyboard(lang))
    await cb.answer()


# ------------- rating -------------

@router.callback_query(F.data == "menu:rating")
async def cb_rating(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    async with SessionLocal() as s:
        top = await stats_service.top_rating(s, limit=10)
    lines = [t(lang, "rating_header")]
    for i, u in enumerate(top, start=1):
        lines.append(f"{i}. {u.username} — {u.elo} ELO")
    if not top:
        lines.append(t(lang, "rating_empty"))
    with suppress(Exception):
        await cb.message.edit_text("\n".join(lines), reply_markup=_back_keyboard(lang))
    await cb.answer()


# ------------- achievements -------------

@router.callback_query(F.data == "menu:ach")
async def cb_ach(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    u = await _user(cb.from_user.id)
    if not u:
        await cb.answer(t(lang, "use_start"), show_alert=True)
        return
    async with SessionLocal() as s:
        owned = set(await stats_service.user_achievements(s, u.user_id))
    lines = [t(lang, "ach_header")]
    for code, meta in stats_service.ACHIEVEMENTS.items():
        mark = "✅" if code in owned else "🔒"
        name = meta.get(f"name_{lang}", meta["name_en"])
        desc = meta.get(f"desc_{lang}", meta["desc_en"])
        lines.append(f"{mark} <b>{name}</b> — {desc} (+{meta['reward']})")
    with suppress(Exception):
        await cb.message.edit_text("\n".join(lines), reply_markup=_back_keyboard(lang))
    await cb.answer()


# ------------- shop -------------

@router.callback_query(F.data == "menu:shop")
async def cb_shop(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Perk, PerkTranslation)
            .join(PerkTranslation, (PerkTranslation.perk_id == Perk.perk_id) & (PerkTranslation.language == Language(lang)))
            .where(Perk.is_active.is_(True))
            .order_by(Perk.cost_coins)
        )).all()
    lines = [t(lang, "shop_header")]
    for perk, tr in rows:
        lines.append(t(lang, "shop_item", code=perk.code, name=tr.name,
                       cost=perk.cost_coins, desc=tr.description))
    with suppress(Exception):
        await cb.message.edit_text("\n\n".join(lines), reply_markup=_back_keyboard(lang))
    await cb.answer()


# ------------- inventory -------------

@router.callback_query(F.data == "menu:inv")
async def cb_inv(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    u = await _user(cb.from_user.id)
    if not u:
        await cb.answer(t(lang, "use_start"), show_alert=True)
        return
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(UserPerk, PerkTranslation)
            .join(Perk, Perk.perk_id == UserPerk.perk_id)
            .join(PerkTranslation, (PerkTranslation.perk_id == Perk.perk_id) & (PerkTranslation.language == Language(lang)))
            .where(UserPerk.user_id == u.user_id)
        )).all()
    if not rows:
        text = t(lang, "inventory_empty")
    else:
        lines = [t(lang, "inventory_header")]
        for up, tr in rows:
            lines.append(t(lang, "inventory_item", name=tr.name,
                           date=up.acquired_at.date().isoformat()))
        text = "\n".join(lines)
    with suppress(Exception):
        await cb.message.edit_text(text, reply_markup=_back_keyboard(lang))
    await cb.answer()


# ------------- language -------------

@router.callback_query(F.data == "menu:lang")
async def cb_lang(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    with suppress(Exception):
        await cb.message.edit_text(t(lang, "pick_language"),
                                   reply_markup=language_keyboard())
    await cb.answer()


# ------------- rules -------------

@router.callback_query(F.data == "menu:rules")
async def cb_rules(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    with suppress(Exception):
        await cb.message.edit_text(t(lang, "rules"), reply_markup=_back_keyboard(lang))
    await cb.answer()


# ------------- help -------------

@router.callback_query(F.data == "menu:help")
async def cb_help(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    with suppress(Exception):
        await cb.message.edit_text(t(lang, "help_body"),
                                   reply_markup=_back_keyboard(lang))
    await cb.answer()
