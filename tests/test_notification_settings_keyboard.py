from __future__ import annotations

from app.bot.keyboards.auction import notification_onboarding_keyboard, notification_settings_keyboard


def test_notification_settings_keyboard_contains_core_actions() -> None:
    keyboard = notification_settings_keyboard(
        master_enabled=True,
        preset="recommended",
        outbid_enabled=True,
        auction_finish_enabled=True,
        auction_win_enabled=True,
        auction_mod_actions_enabled=True,
        points_enabled=False,
        support_enabled=True,
    )

    callback_data = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }

    assert "dash:settings:master:0" in callback_data
    assert "dash:settings:preset:recommended" in callback_data
    assert "dash:settings:preset:important" in callback_data
    assert "dash:settings:toggle:outbid" in callback_data
    assert "dash:settings:toggle:points" in callback_data
    assert "dash:home" in callback_data


def test_notification_onboarding_keyboard_contains_presets() -> None:
    keyboard = notification_onboarding_keyboard(preset="important")

    callback_data = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }

    assert "dash:settings:preset:recommended" in callback_data
    assert "dash:settings:preset:important" in callback_data
    assert "dash:settings:preset:all" in callback_data
    assert "dash:settings:preset:custom" in callback_data
    assert "dash:settings" in callback_data
