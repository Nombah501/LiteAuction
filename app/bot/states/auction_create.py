from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AuctionCreateStates(StatesGroup):
    waiting_photo = State()
    waiting_description = State()
    waiting_start_price = State()
    waiting_buyout_price = State()
    waiting_min_step = State()
    waiting_duration = State()
    waiting_anti_sniper = State()
