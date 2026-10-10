# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""In-app notifications — emit rows into the `notifications` table (service role).

After an AI run, notify a project's users of species detections. Notifications are
strictly **opt-in**: a user is notified only when they have an active
`notification_rules` row for the event with at least one channel selected. Users who
have not configured (or have cleared) their preferences get nothing — no default watch
set. Everything is best-effort and resilient — missing tables or query failures silently
no-op so the pipeline is never affected. Email goes through the provider-agnostic
`email_channel` (a no-op stub until a provider is configured). Phase 5.
"""

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone

import structlog

from app.services.db_utils import rows_of
from app.services.email_channel import send_email
from app.services.supabase_client import create_service_client

logger = structlog.get_logger()


def _project_member_ids(svc, project_id: str) -> list[str]:
    try:
        rows = (
            svc.table("user_roles")
            .select("user_id")
            .eq("scope_type", "project")
            .eq("scope_id", project_id)
            .eq("is_active", True)
            .is_("deleted_at", "null")
            .execute()
            .data
            or []
        )
    except Exception:
        return []
    return sorted({r["user_id"] for r in rows if r.get("user_id")})


def _species_rules(svc, project_id: str) -> dict:
    """user_id → rule, for active species_detection rules on the project."""
    try:
        rows = (
            svc.table("notification_rules")
            .select("user_id, species_filter, channels, digest")
            .eq("project_id", project_id)
            .eq("event_type", "species_detection")
            .eq("is_active", True)
            .execute()
            .data
            or []
        )
    except Exception:
        return {}  # table not deployed / no access → no explicit rules
    return {r["user_id"]: r for r in rows if r.get("user_id")}


# Review states that mean a person has ruled on the photo: domain.pipeline.HUMAN_VERDICT_STATES
# and the frontend's HUMAN_REVIEWED_STATES (services do not import domain, so it is repeated here).
HUMAN_VERDICT_STATES = ("human_reviewed", "expert_reviewed", "consensus_approved")

# The name a photo is counted under when the consensus finds an animal no per-model row named.
UNIDENTIFIED_ANIMAL = "Unidentified animal"

_PRESENCE_COLUMNS = "id, media_id, observation_type, scientific_name, vernacular_name, source_type, review_status, classification_method"


def _is_human_verdict(o: dict) -> bool:
    """A reviewed row, or a human-authored row from before review_status existed (as the frontend's isHumanReviewed)."""
    status = o.get("review_status")
    if status:
        return status in HUMAN_VERDICT_STATES
    return o.get("source_type") == "human" or o.get("classification_method") == "human"


def _name(o: dict) -> str | None:
    return o.get("scientific_name") or o.get("vernacular_name")


def photo_detections(rows: list[dict], recent_ids: set[str]) -> list[str]:
    """The species names one photo adds to a detection notification (#170).

    ``rows`` are the photo's live observations; ``recent_ids`` the ids of its rows the
    latest run wrote. Precedence: a human verdict, then the consensus row
    (``source_type='consensus'``, evidence fusion), then the per-model rows as before.
    A verdict that says blank adds nothing. A human verdict names its own rows. A
    consensus that says present takes the names on the run's per-model rows, or counts
    the photo once as an unidentified animal when none named it. Without either, every
    named per-model row of the run counts, as before.
    """
    run_names = [n for o in rows if o.get("id") in recent_ids and o.get("source_type") == "ai" and (n := _name(o))]
    human = [o for o in rows if _is_human_verdict(o)]
    consensus = next((o for o in rows if o.get("source_type") == "consensus"), None)
    if human:
        if all(o.get("observation_type") == "blank" and not _name(o) for o in human):
            return []
        return [n for o in human if (n := _name(o))]
    if consensus is None:
        return run_names
    if consensus.get("observation_type") == "blank":
        return []
    if run_names:
        return run_names
    return [UNIDENTIFIED_ANIMAL] if consensus.get("observation_type") == "animal" else []


def _emails(svc, user_ids: list[str]) -> dict:
    if not user_ids:
        return {}
    try:
        rows = svc.table("users").select("id, email").in_("id", user_ids).execute().data or []
    except Exception:
        return {}
    return {r["id"]: r["email"] for r in rows if r.get("email")}


async def emit_detection_notifications(deployment_id: str, recent_minutes: int = 30) -> int:
    """Notify project users of species detections from the latest AI run.

    Opt-in only: a user is notified solely for species matching their active
    notification_rules, on the channels they selected. Members without an active rule
    are skipped. Only photos with an observation created in the last ``recent_minutes``
    count, so re-runs don't re-notify, and each photo counts by ``photo_detections``.
    Returns the number of web notifications created.
    """

    def _gather() -> tuple[list[tuple[str, str, str]], int]:
        svc = create_service_client()
        dep = rows_of(svc.table("deployments").select("project_id, location_name").eq("id", deployment_id).limit(1).execute())
        if not dep or not dep[0].get("project_id"):
            return [], 0
        project_id = dep[0]["project_id"]
        location = dep[0].get("location_name") or "a deployment"

        since = (datetime.now(timezone.utc) - timedelta(minutes=recent_minutes)).isoformat()
        recent = rows_of(
            svc.table("observations")
            .select("id, media_id")
            .eq("deployment_id", deployment_id)
            .in_("source_type", ["ai", "consensus"])
            .gte("created_at", since)
            .execute()
        )
        recent_ids = {o["id"] for o in recent if o.get("id")}
        media_ids = sorted({o["media_id"] for o in recent if o.get("media_id")})
        # Each photo's verdict reads all its live rows: a human verdict or a consensus
        # row can predate the run.
        by_media: dict[str, list[dict]] = {}
        for i in range(0, len(media_ids), 100):
            rows = rows_of(
                svc.table("observations").select(_PRESENCE_COLUMNS).in_("media_id", media_ids[i : i + 100]).is_("deleted_at", "null").execute()
            )
            for o in rows:
                by_media.setdefault(o["media_id"], []).append(o)
        detected: Counter = Counter()
        for rows in by_media.values():
            detected.update(photo_detections(rows, recent_ids))
        if not detected:
            return [], 0

        members = _project_member_ids(svc, project_id)
        if not members:
            return [], 0
        rules = _species_rules(svc, project_id)
        emails = _emails(svc, [u for u in members if rules.get(u, {}).get("channels") and "email" in rules[u]["channels"]])

        web_rows: list[dict] = []
        email_tasks: list[tuple[str, str, str]] = []
        link = f"/annotations?deployment={deployment_id}"

        for uid in members:
            rule = rules.get(uid)
            # Opt-in: no active rule → no notification (no default watch set).
            if rule is None:
                continue
            # Respect the selected channels literally — an empty set means "off",
            # never silently fall back to web.
            channels = rule.get("channels") or []
            if not channels:
                continue
            filt = (rule.get("species_filter") or "").lower()
            matching = {n: c for n, c in detected.items() if not filt or filt in n.lower()}
            if not matching:
                continue

            summary = ", ".join(f"{n} (×{c})" for n, c in sorted(matching.items(), key=lambda kv: -kv[1]))
            title = f"⚠ Species detected at {location}"
            body = f"{summary} in your latest upload."

            if "web" in channels:
                web_rows.append(
                    {
                        "user_id": uid,
                        "project_id": project_id,
                        "deployment_id": deployment_id,
                        "type": "species_detection",
                        "title": title,
                        "body": body,
                        "data": {"species": matching, "count": sum(matching.values()), "link": link},
                    }
                )
            if "email" in channels and rule.get("digest", "immediate") == "immediate":
                to = emails.get(uid)
                if to:
                    email_tasks.append((to, title, f"{body}\n\nView in Wildlife Watcher: {link}"))

        if web_rows:
            try:
                svc.table("notifications").insert(web_rows).execute()
            except Exception as exc:
                logger.warning("notifications_emit_skipped", error=str(exc), deployment_id=deployment_id)
                web_rows = []

        return email_tasks, len(web_rows)

    try:
        email_tasks, web_count = await asyncio.to_thread(_gather)
    except Exception as exc:  # noqa: BLE001 — notifications are non-critical
        logger.warning("notifications_emit_failed", error=str(exc), deployment_id=deployment_id)
        return 0

    for to, subject, body in email_tasks:
        await send_email(to, subject, body)

    if web_count or email_tasks:
        logger.info("notifications_emitted", web=web_count, emails=len(email_tasks), deployment_id=deployment_id)
    return web_count
