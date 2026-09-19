# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Centralised configuration via Pydantic BaseSettings.

All environment variables are declared here with sensible defaults.
Validated at startup — the app refuses to boot if required vars are missing.
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, loaded from environment variables / .env file."""

    # ── Supabase ─────────────────────────────────────────────────────
    SUPABASE_URL: str = Field(..., description="Supabase project URL")
    SUPABASE_ANON_KEY: str = Field(..., description="Supabase anonymous/public key")
    SUPABASE_SERVICE_ROLE_KEY: str = Field(..., description="Supabase service-role key (admin ops only)")

    # ── Redis (future — not yet required) ────────────────────────────
    REDIS_URL: str = Field("", description="Redis connection URL (empty = in-memory fallback)")

    # ── Security ─────────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = Field(
        "https://wildlifewatcher.ai,http://localhost:5173",
        description="Comma-separated CORS origins",
    )
    RATE_LIMIT_PER_MINUTE: int = Field(60, description="Default per-IP rate limit")

    # ── LoRaWAN Webhooks ─────────────────────────────────────────────
    LORAWAN_WEBHOOK_SECRET: str = Field("", description="Generic LoRaWAN webhook shared secret")
    LORAWAN_TTN_WEBHOOK_SECRET: str = Field("", description="TTN-specific webhook secret")
    LORAWAN_CHIRPSTACK_WEBHOOK_SECRET: str = Field("", description="Chirpstack-specific webhook secret")

    # ── Public API ───────────────────────────────────────────────────
    PUBLIC_API_ENABLED: bool = Field(False, description="Enable /api/v1/* endpoints")
    API_KEY_HASH_ROUNDS: int = Field(12, description="bcrypt rounds for API key hashing")

    # ── Demo account ─────────────────────────────────────────────────
    # Credentials for the shared read-only demo user (seeded by
    # scripts/seed_demo.py). The /api/auth/demo-session endpoint is
    # disabled unless both are set.
    DEMO_EMAIL: str = Field("", description="Email of the shared demo account")
    DEMO_PASSWORD: str = Field("", description="Password of the shared demo account")

    # ── Observability ────────────────────────────────────────────────
    SENTRY_DSN: Optional[str] = Field(None, description="Sentry DSN for error tracking")
    LOG_LEVEL: str = Field("info", description="Logging level")

    # ── Feature Flags ────────────────────────────────────────────────
    FF_INAT_ENABLED: bool = Field(False)
    FF_ML_ENABLED: bool = Field(False)
    FF_CLUSTERING_ENABLED: bool = Field(False)
    FF_LORAWAN_WEBHOOKS_ENABLED: bool = Field(True)
    FF_PUBLIC_API_ENABLED: bool = Field(False)
    FF_CAMTRAPDP_IMPORT_ENABLED: bool = Field(True, description="Enable CamtrapDP package import endpoint")
    FF_PIPELINE_ENABLED: bool = Field(False, description="Enable AI pipeline inference endpoints")

    # ── v4 Wildlife Brain feature flags ──────────────────────────────
    FF_SPECIESNET_ENABLED: bool = Field(False, description="Use SpeciesNet (detector + classifier) as the pipeline step")
    SPECIESNET_RUN_MODE: str = Field(
        "single_thread",
        description=(
            "SpeciesNet predict() run mode. 'single_thread' is required when running in-process "
            "in the API server: 'multi_thread'/'multi_process' race the torch.fx detector trace "
            "across threads/forks and fail with NameError('module is not installed as a submodule')."
        ),
    )
    FF_BIOCLIP_ENABLED: bool = Field(False, description="Enable the BioCLIP secondary/zero-shot classifier pipeline step")
    FF_PER_CROP_CLASSIFY_ENABLED: bool = Field(False, description="One AI observation per detection (not collapsed per image)")
    FF_EDGE_REFLECT_ENABLED: bool = Field(
        False,
        description=(
            "Reflect on-device (edge) model EXIF scores as ai_origin='edge' observations beside the "
            "cloud pipeline's — requires the ww-backend observations.ai_origin column (dual_ai_v0)"
        ),
    )
    FF_WILDLIFE_BRAIN_ENABLED: bool = Field(False, description="Enable DINOv3 embedding / clustering / similarity endpoints")
    FF_MEDIA_REGISTRY_ENABLED: bool = Field(False, description="Enable Media Registry thumbnails/crops + resolve endpoints")
    FF_ACTIVE_LEARNING_ENABLED: bool = Field(False, description="Enable active-learning review queue scoring")
    FF_INTELLIGENCE_ENABLED: bool = Field(False, description="Enable conservation intelligence endpoints (health, alerts, shift)")
    FF_LOCAL_EMBEDDING_ENABLED: bool = Field(False, description="Accept client-computed (WebGPU) embedding vectors")

    # ── Motion ROI (SpeciesNet-free crop fallback) ───────────────────
    FF_MOTION_ROI_FALLBACK_ENABLED: bool = Field(
        False,
        description=(
            "In the Animal Crop step, when SpeciesNet produced no detection bbox for a frame, crop a "
            "pure-numpy/Pillow motion ROI computed across the frame's burst so DINOv3 still gets an "
            "animal region. No ML — works on the lean dev-cloud image where SpeciesNet is unavailable."
        ),
    )
    MOTION_ROI_BURST_GAP_SECONDS: float = Field(
        10.0,
        ge=0.0,
        description="Max seconds between consecutive frames to treat them as one motion burst for ROI cropping",
    )

    # Vector store is pgvector in Supabase (media_embeddings.embedding) — no separate
    # service or config; it reuses the SUPABASE_* connection above.

    # ── DINOv3 embedding compute ──────────────────────────────────────
    HF_TOKEN: str = Field("", description="HuggingFace token for gated DINOv3 model access")
    EMBEDDING_DEFAULT_MODEL: str = Field("dinov3-vith", description="Default server embedding variant (see embedding_registry)")
    EMBEDDING_DEVICE: str = Field("cpu", description="Torch device for server embedding ('cpu' or 'cuda')")

    # ── BioCLIP (zero-shot / secondary classifier) ───────────────────
    BIOCLIP_DEVICE: str = Field("cpu", description="Torch device for BioCLIP ('cpu' or 'cuda')")
    BIOCLIP_RANK: str = Field("species", description="Default taxonomic rank for Tree-of-Life predictions")
    EMBEDDING_BATCH_SIZE: int = Field(32, description="Batch size for GPU/CPU embedding extraction")
    EMBEDDING_CHECKPOINT_EVERY: int = Field(1000, description="Write embeddings + Supabase every N images for restartable jobs")

    # ── General ──────────────────────────────────────────────────────
    GENERAL_ORG_ID: str = Field(
        "b0000000-0000-0000-0000-000000000001",
        description="General organisation UUID from seed data",
    )
    # ── Google Drive ──────────────────────────────────────────────────
    GOOGLE_DRIVE_ENABLED: bool = Field(False, description="Enable async Google Drive upload of analysed images")
    GOOGLE_DRIVE_FOLDER_ID: str = Field(
        "",
        description=(
            "Root Google Drive folder ID this environment archives into. No default on "
            "purpose: every environment gets its own subfolder of the shared 'Data' folder "
            "(dev, Production), so a default would silently write into whichever folder it "
            "named. Unset + Drive enabled fails loudly at upload time."
        ),
    )
    GOOGLE_SERVICE_ACCOUNT_JSON: str = Field(
        "",
        description="Path to service account JSON file, or inline JSON string",
    )
    GOOGLE_DRIVE_MAX_FILE_SIZE_MB: int = Field(50, description="Max file size in MB accepted for Drive upload")
    MAX_UPLOAD_IMAGES_PER_REQUEST: int = Field(
        500, ge=1, description="Max images an authenticated user may submit in one /api/exif/parse call (anti-abuse)"
    )

    # ── BMP ingest (raw device frames → JPEG in the upload pipeline) ───
    FF_BMP_INGEST_ENABLED: bool = Field(
        False,
        description="Accept raw BMP frames on upload, re-compressing them to JPEG in-pipeline. When off, BMP files are ignored (not stored).",
    )
    BMP_JPEG_QUALITY: int = Field(90, ge=1, le=100, description="JPEG quality for re-compressed BMP frames")

    # ── Azure Storage (Temporary Image Buffer) ────────────────────────
    AZURE_STORAGE_CONNECTION_STRING: str = Field("", description="Azure Storage Account connection string for blob buffering")
    AZURE_STORAGE_CONTAINER_NAME: str = Field("wildlife-watcher-uploads", description="Default container name in Azure Blob Storage")

    # ── Media Registry renditions (Supabase Storage public bucket; originals stay in Drive) ──
    SUPABASE_MEDIA_BUCKET: str = Field(
        "media-renditions",
        description="Public Supabase Storage bucket for thumbnails/previews/animal crops",
    )

    # ── iNaturalist (Phase 6) ────────────────────────────────────────
    INAT_CLIENT_ID: str = Field("")
    INAT_CLIENT_SECRET: str = Field("")
    INAT_REDIRECT_URI: str = Field("https://wildlifewatcher.ai/inat/callback")

    # ── Notifications: email channel ─────────────────────────────────
    # Provider for the email notification channel. 'none' = no-op stub (logs instead of
    # sending). Set to 'azure_acs' | 'resend' | 'sendgrid' + the provider's credentials
    # to enable real delivery (see services/email_channel.py).
    EMAIL_PROVIDER: str = Field("none", description="Email provider: none|azure_acs|resend|sendgrid")
    EMAIL_FROM: str = Field("", description="From address for notification emails")
    RESEND_API_KEY: str = Field("")
    SENDGRID_API_KEY: str = Field("")
    ACS_CONNECTION_STRING: str = Field("")

    model_config = {"env_file": ("../.env", ".env"), "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def cors_origins(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


settings = Settings()
