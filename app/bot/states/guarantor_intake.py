from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class GuarantorIntakeStates(StatesGroup):
    waiting_request_text = State()
