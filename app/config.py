from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    bot_username: str = ""
    database_url: str = "postgresql+asyncpg://auction:auction@db:5432/auction"
    redis_url: str = "redis://redis:6379/0"
    tz: str = "Asia/Tashkent"
    log_level: str = "INFO"
    admin_user_ids: str = ""
    admin_operator_user_ids: str = ""
    moderation_chat_id: str = ""
    moderation_thread_id: str = ""
    admin_panel_token: str = ""
    ui_emoji_create_auction_id: str = ""
    ui_emoji_publish_id: str = ""
    ui_emoji_bid_id: str = ""
    ui_emoji_buyout_id: str = ""
    ui_emoji_report_id: str = ""
    ui_emoji_mod_panel_id: str = ""
    admin_web_session_secret: str = ""
    admin_web_auth_max_age_seconds: int = 86400
    admin_web_cookie_secure: bool = False
    admin_web_csrf_ttl_seconds: int = 7200
    anti_sniper_window_minutes: int = 2
    anti_sniper_extend_minutes: int = 3
    anti_sniper_max_extensions: int = 3
    bid_cooldown_seconds: int = 2
    duplicate_bid_window_seconds: int = 15
    confirmation_ttl_seconds: int = 5
    complaint_cooldown_seconds: int = 60
    soft_gate_require_private_start: bool = True
    soft_gate_mode: str = "grace"
    soft_gate_hint_interval_hours: int = 24
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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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


@lru_cache(1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
