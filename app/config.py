from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


def _resolve_toml_settings_path() -> Path:
    env_override = os.environ.get("APP_CONFIG_FILE", "").strip()
    if env_override:
        return Path(env_override)

    dotenv_path = Path(".env")
    if dotenv_path.exists():
        dotenv_values_map = dotenv_values(dotenv_path)
        raw_dotenv_override = dotenv_values_map.get("APP_CONFIG_FILE")
        if raw_dotenv_override is not None:
            dotenv_override = str(raw_dotenv_override).strip()
            if dotenv_override:
                return Path(dotenv_override)

    return Path("config/defaults.toml")


class Settings(BaseSettings):
    bot_token: str
    bot_username: str = ""
    database_url: str = "postgresql+asyncpg://auction:auction@db:5432/auction"
    redis_url: str = "redis://redis:6379/0"
    tz: str = "Asia/Tashkent"
    app_config_file: str = "config/defaults.toml"
    log_level: str = "INFO"
    admin_user_ids: str = ""
    admin_operator_user_ids: str = ""
    moderation_chat_id: str = ""
    moderation_thread_id: str = ""
    moderation_topic_complaints_id: str = ""
    moderation_topic_suggestions_id: str = ""
    moderation_topic_bugs_id: str = ""
    moderation_topic_fraud_id: str = ""
    moderation_topic_channel_dm_guard_id: str = ""
    moderation_topic_guarantors_id: str = ""
    moderation_topic_appeals_id: str = ""
    moderation_topic_auctions_active_id: str = ""
    moderation_topic_auctions_frozen_id: str = ""
    moderation_topic_auctions_closed_id: str = ""
    admin_panel_token: str = ""
    ui_emoji_create_auction_id: str = ""
    ui_emoji_publish_id: str = ""
    ui_emoji_bid_id: str = ""
    ui_emoji_bid_x1_id: str = ""
    ui_emoji_bid_x3_id: str = ""
    ui_emoji_bid_x5_id: str = ""
    ui_emoji_buyout_id: str = ""
    ui_emoji_report_id: str = ""
    ui_emoji_copy_publish_id: str = ""
    ui_emoji_gallery_id: str = ""
    ui_emoji_new_lot_id: str = ""
    ui_emoji_photos_done_id: str = ""
    ui_emoji_mod_panel_id: str = ""
    ui_emoji_mod_complaints_id: str = ""
    ui_emoji_mod_signals_id: str = ""
    ui_emoji_mod_frozen_id: str = ""
    ui_emoji_mod_appeals_id: str = ""
    ui_emoji_mod_stats_id: str = ""
    ui_emoji_mod_refresh_id: str = ""
    ui_emoji_mod_freeze_id: str = ""
    ui_emoji_mod_unfreeze_id: str = ""
    ui_emoji_mod_remove_top_id: str = ""
    ui_emoji_mod_ban_id: str = ""
    ui_emoji_mod_ignore_id: str = ""
    ui_emoji_mod_take_id: str = ""
    ui_emoji_mod_approve_id: str = ""
    ui_emoji_mod_reject_id: str = ""
    ui_emoji_mod_assign_guarantor_id: str = ""
    ui_emoji_mod_back_id: str = ""
    ui_emoji_mod_menu_id: str = ""
    ui_emoji_mod_prev_id: str = ""
    ui_emoji_mod_next_id: str = ""
    admin_web_session_secret: str = ""
    admin_web_auth_max_age_seconds: int = 86400
    admin_web_cookie_secure: bool = False
    admin_web_csrf_ttl_seconds: int = 7200
    anti_sniper_window_minutes: int = 2
    anti_sniper_extend_minutes: int = 3
    anti_sniper_max_extensions: int = 3
    bid_cooldown_seconds: int = 2
    outbid_notification_debounce_seconds: int = 60
    outbid_notification_digest_window_seconds: int = 180
    duplicate_bid_window_seconds: int = 15
    confirmation_ttl_seconds: int = 5
    complaint_cooldown_seconds: int = 60
    soft_gate_require_private_start: bool = True
    soft_gate_mode: str = "grace"
    soft_gate_hint_interval_hours: int = 24
    private_topics_enabled: bool = True
    private_topics_strict_routing: bool = True
    private_topics_autocreate_on_start: bool = True
    private_topics_user_topic_policy: str = "auto"
    private_topic_title_auctions: str = "Аукционы"
    private_topic_title_support: str = "Уведомления"
    private_topic_title_points: str = "Баллы"
    private_topic_title_trades: str = "Сделки"
    private_topic_title_moderation: str = "Модерация"
    bot_profile_photo_presets: str = ""
    bot_profile_photo_default_preset: str = "default"
    auction_message_effects_enabled: bool = False
    auction_effect_default_id: str = ""
    auction_effect_outbid_id: str = ""
    auction_effect_buyout_seller_id: str = ""
    auction_effect_buyout_winner_id: str = ""
    auction_effect_ended_seller_id: str = ""
    auction_effect_ended_winner_id: str = ""
    channel_dm_intake_enabled: bool = False
    channel_dm_intake_chat_id: int = 0
    message_drafts_enabled: bool = True
    auction_watcher_interval_seconds: int = 5
    fraud_alert_threshold: int = 60
    fraud_rapid_window_seconds: int = 120
    fraud_rapid_min_bids: int = 5
    fraud_dominance_window_seconds: int = 300
    fraud_dominance_min_total_bids: int = 8
    fraud_dominance_ratio: float = 0.7
    fraud_duopoly_window_seconds: int = 300
    fraud_duopoly_min_total_bids: int = 10
    fraud_duopoly_pair_ratio: float = 0.85
    fraud_alternating_recent_bids: int = 8
    fraud_alternating_min_switches: int = 6
    fraud_baseline_window_seconds: int = 3600
    fraud_baseline_min_bids: int = 6
    fraud_baseline_spike_factor: float = 4.0
    fraud_baseline_min_increment: int = 50
    fraud_baseline_spike_score: int = 25
    fraud_historical_completed_auctions: int = 30
    fraud_historical_min_points: int = 25
    fraud_historical_spike_factor: float = 3.0
    fraud_historical_min_increment: int = 40
    fraud_historical_spike_score: int = 20
    fraud_historical_start_ratio_low: float = 0.5
    fraud_historical_start_ratio_high: float = 2.0
    appeal_sla_open_hours: int = 24
    appeal_sla_in_review_hours: int = 12
    appeal_escalation_enabled: bool = True
    appeal_escalation_interval_seconds: int = 60
    appeal_escalation_batch_size: int = 50
    appeal_escalation_actor_tg_user_id: int = -1
    feedback_intake_min_length: int = 10
    feedback_intake_cooldown_seconds: int = 90
    feedback_bug_reward_points: int = 30
    feedback_suggestion_reward_points: int = 20
    feedback_priority_boost_enabled: bool = True
    feedback_priority_boost_cost_points: int = 25
    feedback_priority_boost_daily_limit: int = 2
    feedback_priority_boost_cooldown_seconds: int = 0
    points_redemption_enabled: bool = True
    points_redemption_cooldown_seconds: int = 60
    points_redemption_daily_limit: int = 0
    points_redemption_weekly_limit: int = 0
    points_redemption_daily_spend_cap: int = 0
    points_redemption_weekly_spend_cap: int = 0
    points_redemption_monthly_spend_cap: int = 0
    points_redemption_min_balance: int = 0
    points_redemption_min_account_age_seconds: int = 0
    points_redemption_min_earned_points: int = 0
    appeal_priority_boost_enabled: bool = True
    appeal_priority_boost_cost_points: int = 20
    appeal_priority_boost_daily_limit: int = 1
    appeal_priority_boost_cooldown_seconds: int = 0
    guarantor_intake_min_length: int = 10
    guarantor_intake_cooldown_seconds: int = 180
    guarantor_priority_boost_enabled: bool = True
    guarantor_priority_boost_cost_points: int = 40
    guarantor_priority_boost_daily_limit: int = 1
    guarantor_priority_boost_cooldown_seconds: int = 0
    publish_high_risk_requires_guarantor: bool = True
    publish_guarantor_assignment_max_age_days: int = 30
    github_automation_enabled: bool = False
    github_token: str = ""
    github_repo_owner: str = "Nombah501"
    github_repo_name: str = "LiteAuction"
    outbox_watcher_interval_seconds: int = 20
    outbox_batch_size: int = 20
    outbox_max_attempts: int = 5
    outbox_retry_base_seconds: int = 30
    outbox_retry_max_seconds: int = 1800
    feedback_github_actor_tg_user_id: int = -998

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=_resolve_toml_settings_path()),
            file_secret_settings,
        )

    def parsed_admin_user_ids(self) -> list[int]:
        raw = [x.strip() for x in self.admin_user_ids.split(",") if x.strip()]
        return [int(x) for x in raw]

    def parsed_admin_operator_user_ids(self) -> list[int]:
        raw = [x.strip() for x in self.admin_operator_user_ids.split(",") if x.strip()]
        if not raw:
            return self.parsed_admin_user_ids()
        return [int(x) for x in raw]

    def parsed_moderation_chat_id(self) -> int | None:
        value = self.moderation_chat_id.strip()
        if not value:
            return None
        return int(value)

    def parsed_moderation_thread_id(self) -> int | None:
        value = self.moderation_thread_id.strip()
        if not value:
            return None
        return int(value)

    def parsed_moderation_topic_ids(self) -> dict[str, int]:
        raw_map = {
            "complaints": self.moderation_topic_complaints_id,
            "suggestions": self.moderation_topic_suggestions_id,
            "bugs": self.moderation_topic_bugs_id,
            "fraud": self.moderation_topic_fraud_id,
            "channel_dm_guard": self.moderation_topic_channel_dm_guard_id,
            "guarantors": self.moderation_topic_guarantors_id,
            "appeals": self.moderation_topic_appeals_id,
            "auctions_active": self.moderation_topic_auctions_active_id,
            "auctions_frozen": self.moderation_topic_auctions_frozen_id,
            "auctions_closed": self.moderation_topic_auctions_closed_id,
        }
        parsed: dict[str, int] = {}
        for section, value in raw_map.items():
            normalized = value.strip()
            if not normalized:
                continue
            parsed[section] = int(normalized)
        return parsed

    def parsed_moderation_topic_id(self, section: str) -> int | None:
        normalized = section.strip().lower()
        if not normalized:
            return None
        return self.parsed_moderation_topic_ids().get(normalized)

    def parsed_bot_profile_photo_presets(self) -> dict[str, str]:
        presets: dict[str, str] = {}
        for item in self.bot_profile_photo_presets.split(","):
            normalized = item.strip()
            if not normalized or "=" not in normalized:
                continue
            key_raw, value_raw = normalized.split("=", maxsplit=1)
            key = key_raw.strip().lower()
            value = value_raw.strip()
            if not key or not value:
                continue
            presets[key] = value
        return presets

    def parsed_bot_profile_photo_default_preset(self) -> str | None:
        normalized = self.bot_profile_photo_default_preset.strip().lower()
        if not normalized:
            return None
        return normalized

    def parsed_auction_effect_ids(self) -> dict[str, str]:
        default_effect_id = self.auction_effect_default_id.strip()
        raw_map = {
            "outbid": self.auction_effect_outbid_id,
            "buyout_seller": self.auction_effect_buyout_seller_id,
            "buyout_winner": self.auction_effect_buyout_winner_id,
            "ended_seller": self.auction_effect_ended_seller_id,
            "ended_winner": self.auction_effect_ended_winner_id,
        }
        parsed: dict[str, str] = {}
        for event, value in raw_map.items():
            normalized = value.strip()
            if normalized:
                parsed[event] = normalized
                continue
            if default_effect_id:
                parsed[event] = default_effect_id
        return parsed

    def parsed_auction_effect_id(self, event: str) -> str | None:
        normalized = event.strip().lower()
        if not normalized:
            return None
        return self.parsed_auction_effect_ids().get(normalized)


@lru_cache(1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
