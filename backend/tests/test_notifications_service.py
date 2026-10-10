# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Notification emitter is strictly opt-in — no rule means no notification."""

from unittest.mock import MagicMock, patch

import pytest

from app.services import notifications_service as ns


def _svc_with(observations, members, rules):
    """Build a mock service client whose table(...) chains return canned data.

    The emitter calls: deployments, observations (the run's rows, then every live row
    of those photos), user_roles, notification_rules, users, then inserts into
    notifications. We route by table name and capture the rows inserted into
    `notifications`. Both observation reads get the same rows, so each is recent;
    rows default to one SpeciesNet row per photo.
    """
    inserted: list = []
    observations = [{"id": f"o{i}", "media_id": f"m{i}", "source_type": "ai", **o} for i, o in enumerate(observations)]

    def table(name):
        t = MagicMock()
        # All filter methods are chainable and return the same mock.
        for m in ("select", "eq", "gte", "in_", "is_", "not_", "limit"):
            getattr(t, m).return_value = t
        t.not_.is_.return_value = t

        if name == "deployments":
            t.execute.return_value = MagicMock(data=[{"project_id": "proj-1", "location_name": "New Plymouth"}])
        elif name == "observations":
            t.execute.return_value = MagicMock(data=observations)
        elif name == "user_roles":
            t.execute.return_value = MagicMock(data=[{"user_id": u} for u in members])
        elif name == "notification_rules":
            t.execute.return_value = MagicMock(data=rules)
        elif name == "users":
            t.execute.return_value = MagicMock(data=[{"id": u, "email": f"{u}@ww.org"} for u in members])
        elif name == "notifications":

            def _insert(rows):
                inserted.extend(rows)
                return MagicMock(execute=lambda: MagicMock(data=rows))

            t.insert.side_effect = _insert
        return t

    svc = MagicMock()
    svc.table.side_effect = table
    return svc, inserted


@pytest.mark.asyncio
async def test_member_without_rule_gets_no_notification():
    """A project member who has selected nothing must not be notified."""
    svc, inserted = _svc_with(
        observations=[{"scientific_name": "Rattus rattus", "vernacular_name": "ship rat"}],
        members=["tui"],  # member of the project
        rules=[],  # but has NO active notification rule
    )
    with patch.object(ns, "create_service_client", return_value=svc):
        count = await ns.emit_detection_notifications("dep-1")
    assert count == 0
    assert inserted == []


@pytest.mark.asyncio
async def test_member_with_web_rule_is_notified():
    svc, inserted = _svc_with(
        observations=[{"scientific_name": "Rattus rattus", "vernacular_name": "ship rat"}],
        members=["tui"],
        rules=[{"user_id": "tui", "species_filter": None, "channels": ["web"], "digest": "immediate"}],
    )
    with patch.object(ns, "create_service_client", return_value=svc):
        count = await ns.emit_detection_notifications("dep-1")
    assert count == 1
    assert inserted[0]["user_id"] == "tui"
    assert inserted[0]["type"] == "species_detection"


@pytest.mark.asyncio
async def test_active_rule_with_empty_channels_is_not_notified():
    """An empty channel set means 'off' — never silently fall back to web."""
    svc, inserted = _svc_with(
        observations=[{"scientific_name": "Rattus rattus", "vernacular_name": "ship rat"}],
        members=["tui"],
        rules=[{"user_id": "tui", "species_filter": None, "channels": [], "digest": "immediate"}],
    )
    with patch.object(ns, "create_service_client", return_value=svc):
        count = await ns.emit_detection_notifications("dep-1")
    assert count == 0
    assert inserted == []


@pytest.mark.asyncio
async def test_species_filter_limits_matches():
    """A rule filtered to 'rat' is not notified about a possum detection."""
    svc, inserted = _svc_with(
        observations=[{"scientific_name": "Trichosurus vulpecula", "vernacular_name": "possum"}],
        members=["tui"],
        rules=[{"user_id": "tui", "species_filter": "rat", "channels": ["web"], "digest": "immediate"}],
    )
    with patch.object(ns, "create_service_client", return_value=svc):
        count = await ns.emit_detection_notifications("dep-1")
    assert count == 0
    assert inserted == []


# ── Presence precedence per photo (#170): human verdict, then consensus, then per-model rows ──

RAT = {"id": "sn", "source_type": "ai", "review_status": "ai_reviewed", "observation_type": "animal", "scientific_name": "Rattus rattus"}
SN_BLANK = {"id": "sn", "source_type": "ai", "review_status": "ai_reviewed", "observation_type": "blank"}
GEMINI_ANIMAL = {"id": "gem", "source_type": "ai", "review_status": "ai_reviewed", "observation_type": "animal"}


def _consensus(obs_type):
    return {"id": "fusion", "source_type": "consensus", "review_status": "ai_reviewed", "observation_type": obs_type}


def test_no_consensus_counts_every_named_row_of_the_run():
    """Without a consensus row or a verdict, today's rule: every named per-model row of the run."""
    assert ns.photo_detections([RAT, GEMINI_ANIMAL], {"sn", "gem"}) == ["Rattus rattus"]
    assert ns.photo_detections([SN_BLANK], {"sn"}) == []


def test_rows_from_an_earlier_run_do_not_count():
    assert ns.photo_detections([RAT], set()) == []


def test_consensus_blank_overrules_a_speciesnet_animal():
    assert ns.photo_detections([RAT, _consensus("blank")], {"sn", "fusion"}) == []


def test_consensus_animal_keeps_the_speciesnet_name():
    assert ns.photo_detections([RAT, _consensus("animal")], {"sn", "fusion"}) == ["Rattus rattus"]


def test_consensus_animal_over_a_speciesnet_blank_counts_an_unidentified_animal():
    """The recall case: Gemini and the burst found an animal SpeciesNet missed, and nobody named it."""
    rows = [SN_BLANK, GEMINI_ANIMAL, _consensus("animal")]
    assert ns.photo_detections(rows, {"sn", "gem", "fusion"}) == [ns.UNIDENTIFIED_ANIMAL]


def test_consensus_human_or_vehicle_without_a_name_adds_nothing():
    assert ns.photo_detections([_consensus("human")], {"fusion"}) == []


def test_human_blank_verdict_overrules_a_consensus_animal():
    human_blank = {"id": "h", "source_type": "human", "review_status": "human_reviewed", "observation_type": "blank"}
    rows = [RAT, _consensus("animal"), human_blank]
    assert ns.photo_detections(rows, {"sn", "fusion"}) == []


def test_human_verdict_names_the_photo_over_a_consensus_blank():
    corrected = {**RAT, "scientific_name": "Rattus norvegicus", "review_status": "human_reviewed"}
    assert ns.photo_detections([corrected, _consensus("blank")], {"sn", "fusion"}) == ["Rattus norvegicus"]


def test_consensus_approved_counts_as_a_human_verdict():
    """consensus_approved is human truth; the consensus row itself is ai_reviewed and is not."""
    approved_blank = {**SN_BLANK, "review_status": "consensus_approved"}
    assert ns.photo_detections([approved_blank, GEMINI_ANIMAL, _consensus("animal")], {"sn", "gem", "fusion"}) == []


def test_legacy_human_row_without_review_status_is_a_verdict():
    legacy = {"id": "h", "source_type": "human", "review_status": None, "observation_type": "blank"}
    assert ns.photo_detections([RAT, legacy], {"sn"}) == []


def test_human_verdict_states_match_the_pipeline():
    from app.domain.pipeline import HUMAN_VERDICT_STATES

    assert ns.HUMAN_VERDICT_STATES == HUMAN_VERDICT_STATES


@pytest.mark.asyncio
async def test_consensus_blank_photo_is_not_notified():
    """A SpeciesNet rat the consensus calls blank sends nothing; the run's other photo still counts."""
    svc, inserted = _svc_with(
        observations=[
            {"id": "sn1", "media_id": "m1", "observation_type": "animal", "scientific_name": "Rattus rattus"},
            {"id": "c1", "media_id": "m1", "source_type": "consensus", "observation_type": "blank"},
            {"id": "sn2", "media_id": "m2", "observation_type": "animal", "scientific_name": "Trichosurus vulpecula"},
        ],
        members=["tui"],
        rules=[{"user_id": "tui", "species_filter": None, "channels": ["web"], "digest": "immediate"}],
    )
    with patch.object(ns, "create_service_client", return_value=svc):
        count = await ns.emit_detection_notifications("dep-1")
    assert count == 1
    assert inserted[0]["data"]["species"] == {"Trichosurus vulpecula": 1}
