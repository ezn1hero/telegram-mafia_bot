from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from sqlalchemy import select

from config import settings
from db.models import User
from db.session import SessionLocal
from game import manager
from game.engine import Game, MAFIA_TEAM, Phase, Player, Role, Winner
from services import economy, stats as stats_service
from services.i18n import t

log = logging.getLogger(__name__)
router = Router()

NIGHT_SECONDS = 45
DAY_SECONDS = 90
SURVIVOR_REWARD = 200


def _role_name(lang: str, role: Role) -> str:
    return t(lang, f"role_{role.value}")


async def _lang_for(tg_id: int) -> str:
    async with SessionLocal() as s:
        user = (await s.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()
    return user.language.value if user else "en"


async def _chat_lang(chat_id: int) -> str:
    game = manager.get(chat_id)
    if game:
        return await _lang_for(game.host_id)
    return "en"


def _host_name(game: Game) -> str:
    return getattr(game, "host_name", "Host")


def _players_block(game: Game) -> str:
    return "\n".join(
        f"{p.number or '?'}. {p.name}" + ("" if p.alive else " ☠️")
        for p in game.players
    )


def _lobby_text(lang: str, game: Game) -> str:
    lst = "\n".join(f"• {p.name}" for p in game.players) or "—"
    return t(lang, "lobby_board",
             host=_host_name(game), count=len(game.players),
             min=Game.MIN_PLAYERS, list=lst)


def _lobby_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "btn_join"), callback_data="lobby:join")],
        [InlineKeyboardButton(text=t(lang, "btn_start"), callback_data="lobby:start"),
         InlineKeyboardButton(text=t(lang, "btn_settings"), callback_data="lobby:settings")],
        [InlineKeyboardButton(text=t(lang, "btn_cancel"), callback_data="lobby:cancel")],
    ])


def _settings_keyboard(lang: str, game: Game) -> InlineKeyboardMarkup:
    s = game.settings
    def tog(label_key: str, flag: bool, action: str) -> InlineKeyboardButton:
        mark = "✅" if flag else "⬜️"
        return InlineKeyboardButton(text=f"{mark} {t(lang, label_key)}",
                                    callback_data=f"set:{action}")
    return InlineKeyboardMarkup(inline_keyboard=[
        [tog("set_don", s.allow_don, "don"),
         tog("set_lover", s.allow_lover, "lover"),
         tog("set_maniac", s.allow_maniac, "maniac")],
        [InlineKeyboardButton(text=t(lang, "set_mode", mode=s.mode), callback_data="set:mode")],
        [InlineKeyboardButton(text=t(lang, "set_night", secs=s.night_seconds), callback_data="set:night"),
         InlineKeyboardButton(text=t(lang, "set_day", secs=s.day_seconds), callback_data="set:day")],
        [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="set:back")],
    ])


def _targets_keyboard(game: Game, action: str, actor: Player, lang: str, *,
                      exclude_self: bool = False, exclude_mafia: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for p in game.alive_players():
        if exclude_self and p.tg_id == actor.tg_id:
            continue
        if exclude_mafia and p.role in MAFIA_TEAM:
            continue
        rows.append([InlineKeyboardButton(
            text=f"#{p.number} {p.name}",
            callback_data=f"night:{action}:{p.number}",
        )])
    rows.append([InlineKeyboardButton(text=t(lang, "btn_skip"), callback_data="night:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _vote_keyboard(game: Game) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"#{p.number} {p.name}",
                                  callback_data=f"day:vote:{p.number}")]
            for p in game.alive_players()]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tally_text(lang: str, game: Game) -> str:
    tally: dict[int, int] = {}
    for num in game.day_votes.votes.values():
        tally[num] = tally.get(num, 0) + 1
    if not tally:
        return ""
    lines = []
    for num, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        p = next((x for x in game.players if x.number == num), None)
        if p:
            lines.append(f"#{num} {p.name}: {count}")
    return t(lang, "vote_tally") + "\n" + "\n".join(lines)


# ----------------- lobby -----------------

@router.message(Command("newgame"))
async def cmd_newgame(msg: Message) -> None:
    lang = await _lang_for(msg.from_user.id)
    if msg.chat.type == "private":
        await msg.answer(t(lang, "game_need_group"))
        return
    existing = manager.get(msg.chat.id)
    if existing and not existing.is_over():
        await msg.answer(t(lang, "game_already"))
        return
    game = manager.create(msg.chat.id, msg.from_user.id)
    game.host_name = msg.from_user.full_name
    game.add_player(msg.from_user.id, msg.from_user.full_name)
    await msg.answer(_lobby_text(lang, game), reply_markup=_lobby_keyboard(lang))


@router.message(Command("cancelgame"))
async def cmd_cancel(msg: Message) -> None:
    lang = await _lang_for(msg.from_user.id)
    game = manager.get(msg.chat.id)
    if not game:
        await msg.answer(t(lang, "game_not_found"))
        return
    if msg.from_user.id != game.host_id:
        return
    manager.drop(msg.chat.id)
    await msg.answer(t(lang, "game_cancel"))


@router.message(Command("players"))
async def cmd_players(msg: Message) -> None:
    lang = await _lang_for(msg.from_user.id)
    game = manager.get(msg.chat.id) if msg.chat.type != "private" else manager.find_game_by_player(msg.from_user.id)
    if not game:
        await msg.answer(t(lang, "game_not_found"))
        return
    await msg.answer(t(lang, "game_players_list", list=_players_block(game)))


# ----------------- lobby callbacks -----------------

@router.callback_query(F.data == "lobby:join")
async def cb_join(cb: CallbackQuery, bot: Bot) -> None:
    lang = await _lang_for(cb.from_user.id)
    if not cb.message:
        await cb.answer()
        return
    game = manager.get(cb.message.chat.id)
    if not game or game.phase != Phase.LOBBY:
        await cb.answer(t(lang, "game_not_found"), show_alert=True)
        return
    if game.by_tg(cb.from_user.id):
        await cb.answer(t(lang, "game_already_joined"), show_alert=True)
        return
    # Verify DM channel (needed to send role + action buttons later)
    try:
        await bot.send_message(cb.from_user.id, t(lang, "game_dm_ready"))
    except Exception:
        await cb.answer(t(lang, "game_need_dm"), show_alert=True)
        return
    game.add_player(cb.from_user.id, cb.from_user.full_name)
    with suppress(Exception):
        await cb.message.edit_text(_lobby_text(lang, game), reply_markup=_lobby_keyboard(lang))
    await cb.answer(t(lang, "game_joined_short"))


@router.callback_query(F.data == "lobby:settings")
async def cb_lobby_settings(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    if not cb.message:
        await cb.answer(); return
    game = manager.get(cb.message.chat.id)
    if not game or game.phase != Phase.LOBBY:
        await cb.answer(t(lang, "game_not_found"), show_alert=True); return
    if cb.from_user.id != game.host_id:
        await cb.answer(t(lang, "not_host"), show_alert=True); return
    with suppress(Exception):
        await cb.message.edit_text(t(lang, "settings_header"),
                                   reply_markup=_settings_keyboard(lang, game))
    await cb.answer()


@router.callback_query(F.data.startswith("set:"))
async def cb_settings_action(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    if not cb.message:
        await cb.answer(); return
    game = manager.get(cb.message.chat.id)
    if not game or game.phase != Phase.LOBBY:
        await cb.answer(t(lang, "game_not_found"), show_alert=True); return
    if cb.from_user.id != game.host_id:
        await cb.answer(t(lang, "not_host"), show_alert=True); return
    action = cb.data.split(":", 1)[1]
    s = game.settings
    if action == "don":     s.allow_don = not s.allow_don
    elif action == "lover": s.allow_lover = not s.allow_lover
    elif action == "maniac": s.allow_maniac = not s.allow_maniac
    elif action == "mode":
        if s.mode == "classic":
            s.mode = "fast"; s.night_seconds = 25; s.day_seconds = 50
        else:
            s.mode = "classic"; s.night_seconds = 45; s.day_seconds = 90
    elif action == "night":
        cycle = [20, 30, 45, 60, 90]
        s.night_seconds = cycle[(cycle.index(s.night_seconds) + 1) % len(cycle)] if s.night_seconds in cycle else 45
    elif action == "day":
        cycle = [45, 60, 90, 120, 180]
        s.day_seconds = cycle[(cycle.index(s.day_seconds) + 1) % len(cycle)] if s.day_seconds in cycle else 90
    elif action == "back":
        with suppress(Exception):
            await cb.message.edit_text(_lobby_text(lang, game), reply_markup=_lobby_keyboard(lang))
        await cb.answer(); return
    with suppress(Exception):
        await cb.message.edit_text(t(lang, "settings_header"),
                                   reply_markup=_settings_keyboard(lang, game))
    await cb.answer()


@router.callback_query(F.data == "lobby:cancel")
async def cb_lobby_cancel(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    if not cb.message:
        await cb.answer()
        return
    game = manager.get(cb.message.chat.id)
    if not game:
        await cb.answer()
        return
    if cb.from_user.id != game.host_id:
        await cb.answer(t(lang, "not_host"), show_alert=True)
        return
    manager.drop(cb.message.chat.id)
    with suppress(Exception):
        await cb.message.edit_text(t(lang, "game_cancel"))
    await cb.answer()


@router.callback_query(F.data == "lobby:start")
async def cb_lobby_start(cb: CallbackQuery, bot: Bot) -> None:
    lang = await _lang_for(cb.from_user.id)
    if not cb.message:
        await cb.answer()
        return
    chat_id = cb.message.chat.id
    game = manager.get(chat_id)
    if not game or game.phase != Phase.LOBBY:
        await cb.answer(t(lang, "game_not_found"), show_alert=True)
        return
    if cb.from_user.id != game.host_id:
        await cb.answer(t(lang, "not_host"), show_alert=True)
        return
    if len(game.players) < Game.MIN_PLAYERS:
        await cb.answer(
            t(lang, "game_need_more", min=Game.MIN_PLAYERS, count=len(game.players)),
            show_alert=True,
        )
        return

    game.start()
    with suppress(Exception):
        await cb.message.edit_text(t(lang, "game_started"))
    await cb.answer()

    await _dm_roles(bot, game)
    manager.reset_phase_event(chat_id)
    await _announce_night(bot, chat_id, game)
    manager.set_task(chat_id, asyncio.create_task(_phase_loop(bot, chat_id)))


# ----------------- phase announcements -----------------

async def _dm_roles(bot: Bot, game: Game) -> None:
    mafia_allies = ", ".join(
        f"#{p.number} {p.name}" for p in game.players if p.role in {Role.DON, Role.MAFIA}
    )
    ROLE_KEY = {
        Role.DON: "game_role_don",
        Role.MAFIA: "game_role_mafia",
        Role.SHERIFF: "game_role_sheriff",
        Role.DOCTOR: "game_role_doctor",
        Role.LOVER: "game_role_lover",
        Role.MANIAC: "game_role_maniac",
        Role.CIVILIAN: "game_role_civilian",
    }
    for p in game.players:
        plang = await _lang_for(p.tg_id)
        key = ROLE_KEY[p.role]
        if p.role in {Role.DON, Role.MAFIA}:
            text = t(plang, key, allies=mafia_allies)
        else:
            text = t(plang, key)
        text += "\n\n" + t(plang, "game_players_list", list=_players_block(game))
        with suppress(Exception):
            await bot.send_message(p.tg_id, text)


async def _announce_night(bot: Bot, chat_id: int, game: Game) -> None:
    lang = await _chat_lang(chat_id)
    secs = game.settings.night_seconds
    await bot.send_message(
        chat_id,
        t(lang, "game_night", secs=secs) + "\n\n"
        + t(lang, "game_players_list", list=_players_block(game)),
    )
    for p in game.alive_players():
        plang = await _lang_for(p.tg_id)
        if p.role == Role.MAFIA:
            await _send_night_prompt(bot, p, plang, game, "kill", "prompt_kill",
                                     exclude_self=True, exclude_mafia=True)
        elif p.role == Role.DON:
            await _send_night_prompt(bot, p, plang, game, "donkill", "prompt_don_kill",
                                     exclude_self=True, exclude_mafia=True)
        elif p.role == Role.SHERIFF:
            await _send_night_prompt(bot, p, plang, game, "check", "prompt_check",
                                     exclude_self=True)
        elif p.role == Role.DOCTOR:
            await _send_night_prompt(bot, p, plang, game, "heal", "prompt_heal",
                                     exclude_self=True)
        elif p.role == Role.LOVER:
            await _send_night_prompt(bot, p, plang, game, "block", "prompt_block",
                                     exclude_self=True)
        elif p.role == Role.MANIAC:
            await _send_night_prompt(bot, p, plang, game, "mkill", "prompt_mkill",
                                     exclude_self=True)


async def _send_night_prompt(bot: Bot, actor: Player, lang: str, game: Game,
                             action: str, key: str, **kwargs) -> None:
    with suppress(Exception):
        await bot.send_message(
            actor.tg_id,
            t(lang, key),
            reply_markup=_targets_keyboard(game, action, actor, lang, **kwargs),
        )


async def _announce_day(bot: Bot, chat_id: int, game: Game) -> None:
    lang = await _chat_lang(chat_id)
    secs = game.settings.day_seconds
    await bot.send_message(chat_id, t(lang, "game_day", secs=secs))
    await bot.send_message(
        chat_id,
        t(lang, "prompt_vote") + "\n\n" + t(lang, "game_players_list", list=_players_block(game)),
        reply_markup=_vote_keyboard(game),
    )


# ----------------- night action callbacks (DM) -----------------

@router.callback_query(F.data.startswith("night:"))
async def cb_night(cb: CallbackQuery, bot: Bot) -> None:
    lang = await _lang_for(cb.from_user.id)
    game = manager.find_game_by_player(cb.from_user.id)
    if not game:
        await cb.answer(t(lang, "game_not_found"), show_alert=True)
        return
    if game.phase != Phase.NIGHT:
        await cb.answer(t(lang, "game_action_wrong_phase"), show_alert=True)
        return
    player = game.by_tg(cb.from_user.id)
    if not player or not player.alive:
        await cb.answer(t(lang, "game_action_dead"), show_alert=True)
        return

    parts = cb.data.split(":")
    action = parts[1]
    extra: str | None = None

    if action == "skip":
        if not game.skip_night_action(cb.from_user.id):
            await cb.answer(t(lang, "game_action_not_role"), show_alert=True)
            return
    else:
        try:
            target_num = int(parts[2])
        except (IndexError, ValueError):
            await cb.answer(t(lang, "game_action_invalid_target"), show_alert=True)
            return
        ok = False
        if action == "kill" and player.role == Role.MAFIA:
            ok = game.submit_mafia_kill(cb.from_user.id, target_num)
        elif action == "donkill" and player.role == Role.DON:
            ok = game.submit_don_kill(cb.from_user.id, target_num)
        elif action == "check" and player.role == Role.SHERIFF:
            res = game.submit_sheriff_check(cb.from_user.id, target_num)
            ok = res is not None
            if ok:
                tgt = game.by_number(target_num)
                if tgt:
                    key = "game_sheriff_result_mafia" if res else "game_sheriff_result_clean"
                    extra = t(lang, key, name=tgt.name)
        elif action == "heal" and player.role == Role.DOCTOR:
            ok = game.submit_doctor_heal(cb.from_user.id, target_num)
        elif action == "block" and player.role == Role.LOVER:
            ok = game.submit_lover_block(cb.from_user.id, target_num)
        elif action == "mkill" and player.role == Role.MANIAC:
            ok = game.submit_maniac_kill(cb.from_user.id, target_num)
        else:
            await cb.answer(t(lang, "game_action_not_role"), show_alert=True)
            return
        if not ok:
            await cb.answer(t(lang, "game_action_invalid_target"), show_alert=True)
            return

    await cb.answer(t(lang, "game_action_recorded"))
    if extra:
        with suppress(Exception):
            await bot.send_message(cb.from_user.id, extra)
    with suppress(Exception):
        await cb.message.edit_reply_markup(reply_markup=None)

    if game.night_complete():
        manager.phase_event(game.chat_id).set()


# ----------------- day vote callback (group) -----------------

@router.callback_query(F.data.startswith("day:vote:"))
async def cb_vote(cb: CallbackQuery) -> None:
    lang = await _lang_for(cb.from_user.id)
    if not cb.message:
        await cb.answer()
        return
    game = manager.get(cb.message.chat.id)
    if not game or game.phase != Phase.DAY:
        await cb.answer(t(lang, "game_action_wrong_phase"), show_alert=True)
        return
    try:
        target_num = int(cb.data.split(":")[2])
    except (IndexError, ValueError):
        await cb.answer(t(lang, "game_action_invalid_target"), show_alert=True)
        return
    if not game.submit_vote(cb.from_user.id, target_num):
        await cb.answer(t(lang, "game_action_invalid_target"), show_alert=True)
        return
    await cb.answer(t(lang, "game_action_recorded"))
    tally = _tally_text(lang, game)
    body = (
        t(lang, "prompt_vote") + "\n\n"
        + t(lang, "game_players_list", list=_players_block(game))
        + ("\n\n" + tally if tally else "")
    )
    with suppress(Exception):
        await cb.message.edit_text(body, reply_markup=_vote_keyboard(game))
    if game.day_complete():
        manager.phase_event(game.chat_id).set()


# ----------------- phase loop -----------------

async def _phase_loop(bot: Bot, chat_id: int) -> None:
    try:
        while True:
            game = manager.get(chat_id)
            if not game or game.is_over():
                return

            # NIGHT
            ev = manager.phase_event(chat_id)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ev.wait(), timeout=game.settings.night_seconds)
            game = manager.get(chat_id)
            if not game or game.phase != Phase.NIGHT:
                return
            res = game.resolve_night()
            lang = await _chat_lang(chat_id)
            # Notify blocked target privately
            if res.blocked:
                blang = await _lang_for(res.blocked.tg_id)
                with suppress(Exception):
                    await bot.send_message(res.blocked.tg_id, t(blang, "game_blocked_notice"))
            if res.killed:
                names = ", ".join(
                    f"{p.name} ({_role_name(lang, p.role)})" for p in res.killed
                )
                await bot.send_message(chat_id, t(lang, "game_killed_multi", names=names))
            elif res.saved:
                await bot.send_message(chat_id, t(lang, "game_saved"))
            else:
                await bot.send_message(chat_id, t(lang, "game_nokill"))
            if await _maybe_end(bot, chat_id, game, lang):
                return

            # DAY
            manager.reset_phase_event(chat_id)
            await _announce_day(bot, chat_id, game)
            ev = manager.phase_event(chat_id)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ev.wait(), timeout=game.settings.day_seconds)
            game = manager.get(chat_id)
            if not game or game.phase != Phase.DAY:
                return
            dres = game.resolve_day()
            if dres.lynched:
                await bot.send_message(
                    chat_id,
                    t(lang, "game_lynch", name=dres.lynched.name,
                      role=_role_name(lang, dres.lynched.role)),
                )
            else:
                await bot.send_message(chat_id, t(lang, "game_novote"))
            if await _maybe_end(bot, chat_id, game, lang):
                return

            manager.reset_phase_event(chat_id)
            await _announce_night(bot, chat_id, game)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("phase loop crashed for chat %s", chat_id)


async def _maybe_end(bot: Bot, chat_id: int, game: Game, lang: str) -> bool:
    if not game.is_over():
        return False
    key = {
        Winner.TOWN: "game_win_town",
        Winner.MAFIA: "game_win_mafia",
        Winner.MANIAC: "game_win_maniac",
    }[game.winner]
    await bot.send_message(chat_id, t(lang, key))
    # Reveal final roles
    roster = "\n".join(
        f"#{p.number} {p.name} — {_role_name(lang, p.role)}" + ("" if p.alive else " ☠️")
        for p in sorted(game.players, key=lambda x: x.number)
    )
    await bot.send_message(chat_id, t(lang, "game_final_roles") + "\n" + roster)
    # Survivor coin reward + stats/ELO/achievements in one DB transaction.
    async with SessionLocal() as s:
        survivors = [p for p in game.players if p.alive]
        for p in survivors:
            user = (await s.execute(select(User).where(User.telegram_id == p.tg_id))).scalar_one_or_none()
            if not user:
                continue
            try:
                await economy.earn(s, user, SURVIVOR_REWARD, "game_survivor", settings.daily_earn_cap)
            except economy.DailyCapReached:
                pass
        report = await stats_service.record_game_end(s, game, game.winner)
        await s.commit()

    if survivors:
        await bot.send_message(chat_id, t(lang, "game_rewards", coins=SURVIVOR_REWARD))

    # Per-player DM: ELO delta + any newly-unlocked achievements.
    for p in game.players:
        delta = report.deltas.get(p.tg_id, 0)
        unlocked = report.unlocked.get(p.tg_id, [])
        if delta == 0 and not unlocked:
            continue
        plang = await _lang_for(p.tg_id)
        lines = [t(plang, "game_elo_delta", delta=f"{delta:+d}")]
        for code in unlocked:
            meta = stats_service.ACHIEVEMENTS[code]
            lines.append(t(plang, "achievement_unlocked",
                           name=meta[f"name_{plang}" if f"name_{plang}" in meta else "name_en"],
                           reward=meta["reward"]))
        with suppress(Exception):
            await bot.send_message(p.tg_id, "\n".join(lines))

    manager.drop(chat_id)
    return True
