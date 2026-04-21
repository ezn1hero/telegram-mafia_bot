from __future__ import annotations

from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from bot.gifs import DAY_GIF, NIGHT_GIF
from bot.keyboards import language_keyboard
from config import settings
from db.models import (
    ClipShare, Language, ModerationAction, Perk, PerkTranslation, User, UserPerk,
)
from db.session import SessionLocal
from services import economy
from services.i18n import SUPPORTED, normalize_lang, t

router = Router()


async def _get_user(s, tg_id: int) -> User | None:
    return (await s.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()


@router.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    lang = normalize_lang(msg.from_user.language_code)
    async with SessionLocal() as s:
        user, created = await economy.get_or_create_user(
            s, telegram_id=msg.from_user.id,
            username=msg.from_user.username or msg.from_user.full_name,
            language=lang, starter_coins=100,
        )
        await s.commit()
        lang = user.language.value

    if created:
        await msg.answer(
            t(lang, "welcome", name=msg.from_user.full_name, coins=100),
            reply_markup=language_keyboard(),
        )

    # Always open the main menu in private chats
    if msg.chat.type == "private":
        from bot.menu import _menu_keyboard
        await msg.answer(
            t(lang, "menu_header", name=msg.from_user.full_name),
            reply_markup=_menu_keyboard(lang),
        )


@router.message(Command("language"))
async def cmd_language(msg: Message) -> None:
    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
    lang = user.language.value if user else normalize_lang(msg.from_user.language_code)
    await msg.answer(t(lang, "pick_language"), reply_markup=language_keyboard())


@router.callback_query(F.data.startswith("setlang:"))
async def cb_setlang(cb: CallbackQuery) -> None:
    code = cb.data.split(":", 1)[1]
    if code not in SUPPORTED:
        await cb.answer("Unsupported")
        return
    async with SessionLocal() as s:
        user = await _get_user(s, cb.from_user.id)
        if not user:
            await cb.answer("Use /start first", show_alert=True)
            return
        user.language = Language(code)
        await s.commit()
    await cb.message.answer(t(code, "language_set"))
    await cb.answer()


@router.message(Command("balance"))
async def cmd_balance(msg: Message) -> None:
    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
    if not user:
        await msg.answer("Use /start first.")
        return
    await msg.answer(t(user.language.value, "balance", coins=user.coin_balance))


@router.message(Command("shop"))
async def cmd_shop(msg: Message) -> None:
    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
        lang = user.language.value if user else "en"
        rows = (await s.execute(
            select(Perk, PerkTranslation)
            .join(PerkTranslation, (PerkTranslation.perk_id == Perk.perk_id) & (PerkTranslation.language == Language(lang)))
            .where(Perk.is_active == True)  # noqa: E712
            .order_by(Perk.cost_coins)
        )).all()

    lines = [t(lang, "shop_header")]
    for perk, tr in rows:
        lines.append(t(lang, "shop_item", code=perk.code, name=tr.name, cost=perk.cost_coins, desc=tr.description))
    await msg.answer("\n\n".join(lines))


@router.message(Command("buy"))
async def cmd_buy(msg: Message) -> None:
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer("Usage: /buy <perk_code>")
        return
    code = parts[1].strip()

    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
        if not user:
            await msg.answer("Use /start first.")
            return
        lang = user.language.value
        perk = (await s.execute(select(Perk).where(Perk.code == code, Perk.is_active == True))).scalar_one_or_none()  # noqa: E712
        if not perk:
            await msg.answer(t(lang, "buy_not_found", code=code))
            return
        tr = (await s.execute(
            select(PerkTranslation).where(
                PerkTranslation.perk_id == perk.perk_id,
                PerkTranslation.language == Language(lang),
            )
        )).scalar_one_or_none()
        name = tr.name if tr else perk.code
        try:
            await economy.spend_on_perk(s, user, perk)
        except economy.InsufficientFunds as e:
            await msg.answer(t(lang, "buy_insufficient", cost=e.cost, coins=e.balance))
            return
        await s.commit()
        await msg.answer(t(lang, "buy_success", name=name, cost=perk.cost_coins, coins=user.coin_balance))


@router.message(Command("inventory"))
async def cmd_inventory(msg: Message) -> None:
    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
        if not user:
            await msg.answer("Use /start first.")
            return
        lang = user.language.value
        rows = (await s.execute(
            select(UserPerk, PerkTranslation)
            .join(PerkTranslation, (PerkTranslation.perk_id == UserPerk.perk_id) & (PerkTranslation.language == Language(lang)))
            .where(UserPerk.user_id == user.user_id)
            .order_by(UserPerk.acquired_at.desc())
        )).all()

    if not rows:
        await msg.answer(t(lang, "inventory_empty"))
        return
    lines = [t(lang, "inventory_header")]
    for up, tr in rows:
        lines.append(t(lang, "inventory_item", name=tr.name, date=up.acquired_at.strftime("%Y-%m-%d")))
    await msg.answer("\n".join(lines))


@router.message(Command("daily"))
async def cmd_daily(msg: Message) -> None:
    today = datetime.now(timezone.utc).date()
    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
        if not user:
            await msg.answer("Use /start first.")
            return
        lang = user.language.value
        if user.last_daily_at == today:
            await msg.answer(t(lang, "daily_already"))
            return
        user.daily_streak = user.daily_streak + 1 if user.last_daily_at == today - timedelta(days=1) else 1
        user.last_daily_at = today
        reward = 50 + (300 if user.daily_streak % 7 == 0 else 0)
        try:
            credited = await economy.earn(s, user, reward, "daily", settings.daily_earn_cap)
        except economy.DailyCapReached:
            await msg.answer(t(lang, "daily_cap"))
            return
        await s.commit()
        await msg.answer(t(lang, "daily_claimed", coins=credited, streak=user.daily_streak, balance=user.coin_balance))


@router.message(Command("clip"))
async def cmd_clip(msg: Message) -> None:
    parts = (msg.text or "").split(maxsplit=1)
    cycle = parts[1].strip().lower() if len(parts) > 1 else ""
    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
        lang = user.language.value if user else "en"
        if not user or cycle not in ("day", "night"):
            await msg.answer(t(lang, "clip_usage"))
            return

        today = datetime.now(timezone.utc).date()
        count_today = (await s.execute(
            select(func.count()).select_from(ClipShare).where(
                ClipShare.user_id == user.user_id,
                func.date(ClipShare.created_at) == today,
            )
        )).scalar_one()

        s.add(ClipShare(user_id=user.user_id, cycle=cycle))
        gif = DAY_GIF if cycle == "day" else NIGHT_GIF
        caption = t(lang, "day_caption" if cycle == "day" else "night_caption")
        await msg.answer_animation(animation=gif, caption=caption)

        if count_today >= 3:
            await s.commit()
            await msg.answer(t(lang, "clip_cap"))
            return
        try:
            credited = await economy.earn(s, user, 5, f"clip_{cycle}", settings.daily_earn_cap)
        except economy.DailyCapReached:
            await s.commit()
            await msg.answer(t(lang, "clip_cap"))
            return
        await s.commit()
        await msg.answer(t(lang, "clip_shared", cycle=cycle, coins=credited, balance=user.coin_balance))


@router.message(Command("brag"))
async def cmd_brag(msg: Message) -> None:
    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
        if not user:
            await msg.answer("Use /start first.")
            return
        lang = user.language.value
        perks_count = (await s.execute(
            select(func.count()).select_from(UserPerk).where(UserPerk.user_id == user.user_id)
        )).scalar_one()
    await msg.answer(t(lang, "brag", name=msg.from_user.full_name, coins=user.coin_balance,
                       perks=perks_count, streak=user.daily_streak))


@router.message(Command("report"))
async def cmd_report(msg: Message) -> None:
    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
    lang = user.language.value if user else "en"
    if not msg.reply_to_message:
        await msg.answer(t(lang, "report_need_reply"))
        return
    async with SessionLocal() as s:
        if user:
            s.add(ModerationAction(user_id=user.user_id, action="report",
                                   reason=f"msg_id={msg.reply_to_message.message_id}"))
            await s.commit()
    await msg.answer(t(lang, "report_ok"))


@router.message(Command("translate"))
async def cmd_translate(msg: Message) -> None:
    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
    lang = user.language.value if user else "en"
    if not msg.reply_to_message:
        await msg.answer(t(lang, "translate_need_reply"))
        return
    # Stub: integrate a translation API (DeepL / Google / LibreTranslate) here.
    await msg.answer(f"{t(lang, 'translate_stub')}\n> {msg.reply_to_message.text or ''}")


@router.message(Command("rules"))
async def cmd_rules(msg: Message) -> None:
    async with SessionLocal() as s:
        user = await _get_user(s, msg.from_user.id)
    lang = user.language.value if user else normalize_lang(msg.from_user.language_code)
    await msg.answer(t(lang, "rules"))
