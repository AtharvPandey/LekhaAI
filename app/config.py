"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """All app settings. Loaded from .env file or environment."""

    # ── App ──
    app_env: str = "development"
    app_debug: bool = True
    app_secret_key: str = "change-me-in-production"
    log_level: str = "INFO"

    # ── WhatsApp Cloud API ──
    whatsapp_verify_token: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""

    # ── Google Gemini ──
    gemini_api_key: str = ""

    # ── Groq (fallback) ──
    groq_api_key: str = ""

    # ── Supabase ──
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_service_key: str = ""

    # ── Redis ──
    redis_url: str = ""

    # ── Storage ──
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket_name: str = "hisaab-invoices"

    # ── OCR Settings ──
    ocr_confidence_high: float = 90.0
    ocr_confidence_medium: float = 70.0
    invoice_image_max_size_mb: int = 5
    invoice_image_retention_hours: int = 24

    # ── GST Settings ──
    gstr1_due_day: int = 11
    gstr3b_due_day: int = 20  # varies by state/turnover
    reminder_days_before: list[int] = [5, 2, 0]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance. Call this everywhere."""
    return Settings()
