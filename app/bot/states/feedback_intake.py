from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class FeedbackIntakeStates(StatesGroup):
    waiting_bug_text = State()
    waiting_suggestion_text = State()
