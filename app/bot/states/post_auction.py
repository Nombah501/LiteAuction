from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class FeedbackFSM(StatesGroup):
    waiting_rating = State()
    waiting_comment = State()


class DealGuarantorFSM(StatesGroup):
    waiting_details = State()
