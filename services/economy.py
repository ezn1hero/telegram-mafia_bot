from __future__ import annotations

from datetime import datetime, timezone
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    CoinTransaction, DailyEarnCap, Language, Perk, TxnType, User, UserPerk,
)


class InsufficientFunds(Exception):
    def __init__(self, cost: int, balance: int):
        self.cost = cost
        self.balance = balance
        super().__init__(f"need {cost}, have {balance}")


class DailyCapReached(Exception):
    pass


async def get_or_create_user(
    s: AsyncSession, *, telegram_id: int, username: str, language: str, starter_coins: int = 0,
) -> Tuple[User, bool]:
    user = (await s.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
    if user:
        return user, False
    user = User(
        telegram_id=telegram_id,
        username=(username or f"tg_{telegram_id}")[:32],
        language=Language(language),
        coin_balance=0,
    )
    s.add(user)
    await s.flush()
    if starter_coins > 0:
        await _credit(s, user, starter_coins, "starter", TxnType.earn)
    return user, True


async def _credit(s: AsyncSession, user: User, amount: int, reason: str, ttype: TxnType,
                  perk_id: int | None = None) -> CoinTransaction:
    user.coin_balance += amount if ttype in (TxnType.earn, TxnType.refund) else -amount
    txn = CoinTransaction(
        user_id=user.user_id, type=ttype,
        amount=amount if ttype in (TxnType.earn, TxnType.refund) else -amount,
        perk_id=perk_id, reason=reason,
    )
    s.add(txn)
    await s.flush()
    return txn


async def earn(s: AsyncSession, user: User, amount: int, reason: str, daily_cap: int) -> int:
    """Credit `amount` (respecting daily cap). Returns actually credited amount.
    Raises DailyCapReached if no room left."""
    today = datetime.now(timezone.utc).date()
    cap_row = (await s.execute(
        select(DailyEarnCap).where(DailyEarnCap.user_id == user.user_id, DailyEarnCap.earn_date == today)
    )).scalar_one_or_none()
    earned_today = cap_row.earned if cap_row else 0
    remaining = daily_cap - earned_today
    if remaining <= 0:
        raise DailyCapReached()
    credited = min(amount, remaining)
    await _credit(s, user, credited, reason, TxnType.earn)
    if cap_row:
        cap_row.earned = earned_today + credited
    else:
        s.add(DailyEarnCap(user_id=user.user_id, earn_date=today, earned=credited))
    return credited


async def spend_on_perk(s: AsyncSession, user: User, perk: Perk) -> UserPerk:
    if user.coin_balance < perk.cost_coins:
        raise InsufficientFunds(perk.cost_coins, user.coin_balance)
    txn = await _credit(s, user, perk.cost_coins, f"shop:{perk.code}", TxnType.spend, perk.perk_id)
    up = UserPerk(user_id=user.user_id, perk_id=perk.perk_id, source_txn_id=txn.txn_id)
    s.add(up)
    await s.flush()
    return up
