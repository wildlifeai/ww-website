# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Centralised configuration via Pydantic BaseSettings.

All environment variables are declared here with sensible defaults.
Validated at startup — the app refuses to boot if required vars are missing.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    """Application settings, loaded from environment variables / .env file."""

    # ── Supabase ─────────────────────────────────────────────────────
    SUPABASE_URL: str = Field(..., description="Supabase project URL")
    SUPABASE_ANON_KEY: str = Field(..., description="Supabase anonymous/public key")
    SUPABASE_SERVICE_ROLE_KEY: str = Field(
        ..., description="Supabase service-role key (admin ops only)"
    )


    # ── Security ─────────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = Field(
        "https://wildlifewatcher.ai,http://localhost:5173",
        description="Comma-separated CORS origins",
    )
    RATE_LIMIT_PER_MINUTE: int = Field(60, description="Default per-IP rate limit")

    # ── LoRaWAN Webhooks ─────────────────────────────────────────────
    LORAWAN_WEBHOOK_SECRET: str = Field(
        "", description="Generic LoRaWAN webhook shared secret"
    )
    LORAWAN_TTN_WEBHOOK_SECRET: str = Field(
        "", description="TTN-specific webhook secret"
    )
    LORAWAN_CHIRPSTACK_WEBHOOK_SECRET: str = Field(
        "", description="Chirpstack-specific webhook secret"
    )

    # ── Public API ───────────────────────────────────────────────────
    PUBLIC_API_ENABLED: bool = Field(False, description="Enable /api/v1/* endpoints")
    API_KEY_HASH_ROUNDS: int = Field(12, description="bcrypt rounds for API key hashing")

    # ── Observability ────────────────────────────────────────────────
    SENTRY_DSN: Optional[str] = Field(None, description="Sentry DSN for error tracking")
    LOG_LEVEL: str = Field("info", description="Logging level")

    # ── Feature Flags ────────────────────────────────────────────────
    FF_INAT_ENABLED: bool = Field(False)
    FF_ML_ENABLED: bool = Field(False)
    FF_CLUSTERING_ENABLED: bool = Field(False)
    FF_LORAWAN_WEBHOOKS_ENABLED: bool = Field(True)
    FF_PUBLIC_API_ENABLED: bool = Field(False)

    # ── General ──────────────────────────────────────────────────────
    GENERAL_ORG_ID: str = Field(
        "b0000000-0000-0000-0000-000000000001",
        description="General organisation UUID from seed data",
    )
    UPLOADER_EMAIL: str = Field("apps@wildlife.ai")
    UPLOADER_PASSWORD: str = Field("")

    # ── Google Drive ──────────────────────────────────────────────────
    GOOGLE_DRIVE_ENABLED: bool = Field(
        False, description="Enable async Google Drive upload of analysed images"
    )
    GOOGLE_DRIVE_FOLDER_ID: str = Field(
        "1jIWV3OjSEnBK4Z64syHd2ugoRuXdVrK5",
        description="Root Google Drive folder ID for uploads",
    )
    GOOGLE_SERVICE_ACCOUNT_JSON: str = Field(
        "",
        description="Path to service account JSON file, or inline JSON string",
    )
    GOOGLE_DRIVE_MAX_FILE_SIZE_MB: int = Field(
        50, description="Max file size in MB accepted for Drive upload"
    )

    # ── iNaturalist (Phase 6) ────────────────────────────────────────
    INAT_CLIENT_ID: str = Field("")
    INAT_CLIENT_SECRET: str = Field("")
    INAT_REDIRECT_URI: str = Field("https://wildlifewatcher.ai/inat/callback")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def cors_origins(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


settings = Settings()
