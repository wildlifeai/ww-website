# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Centralised configuration via Pydantic BaseSettings.

All environment variables are declared here with sensible defaults.
Validated at startup — the app refuses to boot if required vars are missing.
"""

from typing import Literal, Optional

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
    FF_PUBLIC_API_ENABLED: bool = Field(False, description="Enable the public API (/api/v1/*) and its key management")
    PUBLIC_API_RATE_LIMIT_PER_MINUTE: int = Field(60, ge=1, description="Calls a minute one API key may make to the /api/v1 data endpoints")
    FF_CAMTRAPDP_IMPORT_ENABLED: bool = Field(True, description="Enable CamtrapDP package import endpoint")
    FF_CAMTRAPDP_EXPORT_ENABLED: bool = Field(
        False,
        description="POST /api/exports/camtrapdp starts the export job with the original photos (#328). Off: FEATURE_DISABLED",
    )
    CAMTRAPDP_EXPORT_BUCKET: str = Field("exports", description="Private Supabase Storage bucket the export ZIPs go to")
    CAMTRAPDP_EXPORT_MAX_BYTES: int = Field(
        4 * 1024**3,
        ge=1,
        description="Largest export ZIP, spooled on the job's local disk and sent as one standard Storage upload",
    )
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

    # ── Species Brain training ("Create species ID model" on the Annotations page) ──
    FF_MODEL_TRAINING_ENABLED: bool = Field(
        False,
        description=(
            "Enable POST /api/models/train: build a labelled dataset from selected annotated images, "
            "train an int8 image classifier through Edge Impulse, compile it with Vela and register it "
            "as a Species Brain (ai_models row). Without Edge Impulse credentials the endpoint still "
            "works in export-only mode (an Edge Impulse-ready dataset ZIP)."
        ),
    )
    EDGE_IMPULSE_API_KEY: str = Field("", description="Edge Impulse *project* API key (ei_…) for the training-bench project")
    EDGE_IMPULSE_PROJECT_ID: int = Field(0, description="Edge Impulse project ID the trainer uploads to and trains in (0 = not configured)")
    EDGE_IMPULSE_STUDIO_URL: str = Field("https://studio.edgeimpulse.com/v1", description="Edge Impulse Studio API base URL")
    EDGE_IMPULSE_INGESTION_URL: str = Field("https://ingestion.edgeimpulse.com", description="Edge Impulse ingestion API base URL")
    EDGE_IMPULSE_DEPLOY_FORMAT: str = Field(
        "custom",
        description="Deployment target `format` to build and download (the 'Custom' zip carries trained.tflite + model-parameters/)",
    )
    EDGE_IMPULSE_TRANSFER_MODEL: str = Field(
        "transfer_mobilenetv2_a35",
        description="Keras visual layer type for the transfer-learning block (MobileNetV2 0.35, the recipe used for the rat model)",
    )
    EDGE_IMPULSE_JOB_TIMEOUT_S: int = Field(1800, ge=60, description="Max seconds to wait for one Edge Impulse job (features, training, build)")
    MODEL_TRAINING_MIN_IMAGES_PER_CLASS: int = Field(20, ge=2, description="Refuse to train a class with fewer samples than this")
    MODEL_TRAINING_RECOMMENDED_IMAGES_PER_CLASS: int = Field(100, ge=1, description="Below this the UI warns (guide: 100 to 1000 per class)")
    MODEL_TRAINING_MAX_IMAGES: int = Field(3000, ge=10, description="Max samples in one training run")
    MODEL_TRAINING_MAX_CLASSES: int = Field(16, ge=2, le=16, description="Device MAX_CLASSES (firmware result buffer), never raise above 16")
    MODEL_ARENA_BYTES: int = Field(
        512 * 1024,
        ge=1,
        description=(
            "Tensor arena the ww500_md firmware reserves (ww500_md.ld: `. = . + 512K;`). services/vela.py refuses any model "
            "whose Vela SRAM estimate exceeds it, whatever its source (upload, Edge Impulse, gcp trainer)"
        ),
    )

    # ── Native Species Brain training on Google Cloud (MODEL_TRAINER=gcp) ──
    # Report: documentation/development reports/2026-09_gcp-native-model-training. The job
    # runs on the ARQ worker, so set these there as well as on the API.
    FF_NATIVE_TRAINING_ENABLED: bool = Field(False, description="Allow MODEL_TRAINER=gcp; off = POST /api/models/train behaves as without it")
    MODEL_TRAINER: Literal["edge_impulse", "gcp"] = Field(
        "edge_impulse",
        description="Trainer behind POST /api/models/train: 'edge_impulse' or 'gcp' (the Cloud Run job built from backend/training/)",
    )
    # GOOGLE_CLOUD_PROJECT and CLOUD_RUN_JOB_REGION are the names the Google Cloud migration plan
    # (2026-09_gcp-pilot-and-migration, §8) uses for the ML-worker job; the trainer shares them.
    # Credentials are Application Default Credentials (the worker's runtime identity), no key.
    GOOGLE_CLOUD_PROJECT: str = Field("", description="Google Cloud project of the Cloud Run jobs (ww-pilot-dev, later ww-dev / ww-prod)")
    CLOUD_RUN_JOB_REGION: str = Field("asia-southeast1", description="Region of the Cloud Run jobs (Singapore: no Cloud Run L4 in Australia)")
    GCS_TRAINING_BUCKET: str = Field("", description="Bucket for runs/<run_key>/dataset (manifest + images) and runs/<run_key>/output (artefacts)")
    TRAINING_JOB_NAME: str = Field("ww-species-trainer", description="Cloud Run job built from backend/training/Dockerfile")
    TRAINING_POLL_INTERVAL_S: float = Field(15.0, ge=1.0, description="Seconds between Cloud Run execution polls")
    TRAINING_RUN_TIMEOUT_S: int = Field(3600, ge=60, le=3600, description="Max seconds per training run; Cloud Run caps GPU job tasks at 1 hour")

    # ── Motion ROI (SpeciesNet-free crop fallback) ───────────────────
    FF_MOTION_ROI_FALLBACK_ENABLED: bool = Field(
        False,
        description=(
            "In the Animal Crop step, when SpeciesNet produced no detection bbox for a frame, crop a "
            "pure-numpy/Pillow motion ROI computed across the frame's burst so DINOv3 still gets an "
            "animal region. No ML — works on the lean dev-cloud image where SpeciesNet is unavailable."
        ),
    )
    # One burst grouper, one gap (domain/burst_evidence.py::group_bursts), shared by the
    # motion-ROI crop fallback, the Gemini contact sheet and evidence fusion. 10 s because
    # today's firmware spaces the frames of one trigger 3 to 5 s apart; the firmware
    # sequence tag, once it lands in EXIF, makes the gap irrelevant.
    BURST_GAP_SECONDS: float = Field(
        10.0,
        ge=0.0,
        description="Max seconds between consecutive frames of one trigger burst (used when the firmware sequence tag is absent)",
    )

    # ── Gemini presence filter (Cloud AI, VLM blank audit) ────────────
    # Runs BEFORE SpeciesNet on every frame in the batch and records a per-frame
    # animal-present verdict as its own observation row (source_model_version = the
    # Gemini model id). Does not alter SpeciesNet's rows: the two are compared by
    # backend/scripts/eval_presence.py before any production wiring is decided.
    # Set on the ARQ worker, not just the API, or the step silently no-ops.
    FF_GEMINI_PRESENCE_ENABLED: bool = Field(
        False,
        description="Run the Gemini animal-presence step before SpeciesNet (needs GEMINI_API_KEY)",
    )
    GEMINI_API_KEY: str = Field("", description="Google AI Studio API key for the google-genai SDK (empty = feature disabled)")
    GEMINI_PRESENCE_MODEL: str = Field(
        "gemini-3.1-flash-lite",
        description="Gemini model id for the presence step; must have a row in services/gemini_pricing.py",
    )
    GEMINI_PRESENCE_VARIANT: str = Field(
        "single",
        description="Token-saving variant: 'single' (one downscaled frame per call), 'contact_sheet' (one burst per call), 'batch' (Batch API)",
    )

    # ── SpeciesNet box filters (beside the run's confidence_threshold) ──
    # MegaDetector inside SpeciesNet answers an empty night scene with a box round the
    # whole frame, often "vehicle" (#285). These drop such boxes before the photo label
    # and presence are built; the run's config can override each one, and the evidence
    # fusion audit line records the values used.
    SPECIESNET_WHOLE_FRAME_AREA: float = Field(
        0.9,
        ge=0.0,
        le=1.0,
        description="Box area, as a fraction of the frame, above which a low-confidence detection is dropped (any class)",
    )
    SPECIESNET_WHOLE_FRAME_MIN_CONFIDENCE: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="A box larger than SPECIESNET_WHOLE_FRAME_AREA is kept only at or above this confidence (an animal at the lens)",
    )
    SPECIESNET_DROP_VEHICLES: bool = Field(
        True,
        description="Drop every vehicle detection: vehicles never matter to a deployment, and night IR scenes score up to 0.95 as one",
    )

    # ── Evidence fusion (consensus verdict per frame) ─────────────────
    # Runs after Gemini, SpeciesNet (and BioCLIP): groups the batch into trigger
    # bursts, scores every frame from the rows the other steps wrote plus motion
    # between the burst's frames, and writes ONE consensus observation per media
    # (source_type='consensus', classified_by='evidence_fusion_v1'). Signals are
    # also written to the ww-backend table media_evidence when it exists. Set on
    # the ARQ worker, not just the API.
    FF_EVIDENCE_FUSION_ENABLED: bool = Field(
        False,
        description="Write a consensus observation per frame from SpeciesNet + Gemini + burst evidence (domain/burst_evidence.py)",
    )
    EVIDENCE_FUSION_THRESHOLD: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Evidence score at or above which the consensus row says animal (weights are hand-set guesses, version v1)",
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


# The required fields come from the environment, which pyright cannot see.
settings = Settings()  # pyright: ignore[reportCallIssue]
