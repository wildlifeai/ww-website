"""LM-10 and the label-map read endpoint: what a model class may assert (#135)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.label_map import (
    describe_label_map,
    entry_problem,
    label_map_problems,
    model_predicts,
    target_observation_fields,
)

RAT_ID = "22222222-2222-2222-2222-222222222222"
MODEL_ID = "11111111-1111-1111-1111-111111111111"


# ── LM-10 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "entry",
    [
        {"role": "background"},
        {"role": "target", "predicts": "taxon", "taxon_id": RAT_ID},
        {"role": "target", "predicts": "taxon", "scientific_name": "Rattus rattus"},
        {"role": "target", "taxon_id": None, "scientific_name": "Rattus rattus"},  # pre-#135: no predicts → taxon
        {"role": "target", "predicts": "type", "observation_type": "human"},
        {"role": "target", "predicts": "type", "observation_type": "vehicle"},
        {"role": "target", "predicts": "type", "observation_type": "animal"},
    ],
)
def test_lm10_accepts_taxon_type_and_background(entry):
    assert entry_problem("x", entry) is None


@pytest.mark.parametrize("value", ["behavior", "behaviour", "Behavior"])
def test_lm10_rejects_behaviour_with_a_clear_message(value):
    problem = entry_problem("foraging", {"role": "target", "predicts": value, "behavior": "foraging"})
    assert problem is not None
    assert problem.startswith("LM-10: class 'foraging'")
    assert "behaviour" in problem and "not an observation" in problem and "background" in problem


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ({"role": "target", "predicts": "life_stage", "life_stage": "adult"}, "'taxon' or 'type'"),
        ({"role": "target", "predicts": "", "taxon_id": RAT_ID}, "'taxon' or 'type'"),
        ({"role": "target", "predicts": 3}, "'taxon' or 'type'"),
        ({"role": "target", "predicts": "taxon"}, "names none"),
        ({"role": "target", "scientific_name": "  "}, "names none"),
        ({"role": "target", "predicts": "type"}, "no observation_type"),
        ({"role": "target", "predicts": "type", "observation_type": ""}, "no observation_type"),
        ({"role": "target", "predicts": "type", "observation_type": "blank"}, "mark the class background"),
        ({"role": "target", "predicts": "type", "observation_type": "unknown"}, "mark the class background"),
        ({"role": "target", "predicts": "type", "observation_type": "person"}, "use one of animal, human, vehicle"),
        ({"role": "negative"}, "'target' or 'background'"),
        ("target", "must be an object"),
    ],
)
def test_lm10_rejects_targets_that_do_not_resolve(entry, fragment):
    problem = entry_problem("c", entry)
    assert problem is not None and fragment in problem


def test_label_map_problems_lists_one_message_per_bad_class():
    label_map = {
        "rat": {"role": "target", "predicts": "taxon", "taxon_id": RAT_ID},
        "grooming": {"role": "target", "predicts": "behavior"},
        "empty": {"role": "target", "predicts": "type", "observation_type": "blank"},
        "not rat": {"role": "background"},
    }
    problems = label_map_problems(label_map)
    assert len(problems) == 2
    assert any("'grooming'" in p for p in problems) and any("'empty'" in p for p in problems)
    assert label_map_problems({}) == []
    assert label_map_problems(["rat"]) == ["LM-10: label_map must be an object keyed by class label"]


# ── What a class writes ──────────────────────────────────────────────


def test_type_class_writes_a_type_and_no_taxon():
    entry = {"role": "target", "predicts": "type", "observation_type": "human", "scientific_name": "Homo sapiens"}
    assert target_observation_fields(entry) == {"observation_type": "human", "taxon_id": None, "scientific_name": None, "vernacular_name": None}


def test_unnamed_legacy_target_still_writes_an_animal_row():
    """Today's behaviour for a target nobody mapped: LM-10 flags it, reflection keeps writing it."""
    entry = {"role": "target"}
    assert entry_problem("person", entry) is not None
    assert target_observation_fields(entry) == {"observation_type": "animal", "taxon_id": None, "scientific_name": None, "vernacular_name": None}


@pytest.mark.parametrize(
    "entry",
    [
        None,
        {"role": "background"},
        {"role": "target", "predicts": "behavior", "behavior": "foraging"},
        {"role": "target", "predicts": "type"},
        {"role": "target", "predicts": "type", "observation_type": "blank"},
        {"role": "target", "predicts": "life_stage"},
    ],
)
def test_classes_that_assert_nothing_write_nothing(entry):
    assert target_observation_fields(entry) is None


def test_model_predicts_lists_what_valid_targets_predict():
    assert model_predicts({"rat": {"role": "target", "scientific_name": "Rattus rattus"}, "not rat": {"role": "background"}}) == ["taxon"]
    assert model_predicts({"person": {"role": "target", "predicts": "type", "observation_type": "human"}}) == ["type"]
    mixed = {
        "person": {"role": "target", "predicts": "type", "observation_type": "human"},
        "rat": {"role": "target", "predicts": "taxon", "taxon_id": RAT_ID},
        "grooming": {"role": "target", "predicts": "behavior"},
    }
    assert model_predicts(mixed) == ["taxon", "type"]
    assert model_predicts({"not rat": {"role": "background"}}) == []


def test_describe_fills_the_default_predicts_and_reports_lm10():
    row = {
        "id": MODEL_ID,
        "name": "Rat Detection",
        "detection_capabilities": ["not rat", "rat"],
        "label_map": {
            "rat": {"role": "target", "taxon_id": None, "scientific_name": "Rattus rattus"},
            "not rat": {"role": "background"},
        },
    }
    out = describe_label_map(row)
    assert out["model_id"] == MODEL_ID
    assert out["labels"] == ["not rat", "rat"]
    assert out["label_map"]["rat"]["predicts"] == "taxon"
    assert "predicts" not in out["label_map"]["not rat"]
    assert "predicts" not in row["label_map"]["rat"]  # the stored row is not mutated
    assert out["predicts"] == ["taxon"]
    assert out["problems"] == []


# ── GET /api/models/{model_id}/label-map ─────────────────────────────


def _user_client(rows):
    """A user-scoped client stub whose ai_models read returns ``rows``; records the filters."""
    table = MagicMock()
    for name in ("select", "eq", "is_", "limit"):
        getattr(table, name).return_value = table
    table.execute.return_value = SimpleNamespace(data=rows)
    client = MagicMock()
    client.table.return_value = table
    return client, table


@pytest.fixture
def as_user():
    from app.dependencies import get_current_user, get_user_client
    from app.main import app

    def install(user_client):
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", email="u@ww.ai")
        app.dependency_overrides[get_user_client] = lambda: user_client

    yield install
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_user_client, None)


def test_label_map_endpoint_returns_the_map_and_predicts(client, as_user, monkeypatch):
    import app.routers.models as models_router
    import app.services.supabase_client as supabase_client

    service = MagicMock(side_effect=AssertionError("the label-map read must not use the service role"))
    monkeypatch.setattr(supabase_client, "create_service_client", service)
    monkeypatch.setattr(models_router, "create_service_client", service)
    row = {
        "id": MODEL_ID,
        "name": "Person Detection",
        "detection_capabilities": ["no person", "person"],
        "label_map": {
            "no person": {"role": "background"},
            "person": {"role": "target", "predicts": "type", "observation_type": "human"},
        },
    }
    user_client, table = _user_client([row])
    as_user(user_client)

    res = client.get(f"/api/models/{MODEL_ID}/label-map", headers={"Authorization": "Bearer t"})

    assert res.status_code == 200
    data = res.json()["data"]
    assert data["predicts"] == ["type"]
    assert data["label_map"]["person"]["observation_type"] == "human"
    assert data["problems"] == []
    user_client.table.assert_called_once_with("ai_models")
    table.eq.assert_called_once_with("id", MODEL_ID)
    table.is_.assert_called_once_with("deleted_at", "null")
    service.assert_not_called()


def test_label_map_endpoint_reports_lm10_problems(client, as_user):
    label_map = {"grooming": {"role": "target", "predicts": "behavior"}}
    row = {"id": MODEL_ID, "name": "Grooming", "detection_capabilities": ["grooming"], "label_map": label_map}
    as_user(_user_client([row])[0])
    data = client.get(f"/api/models/{MODEL_ID}/label-map", headers={"Authorization": "Bearer t"}).json()["data"]
    assert data["predicts"] == []
    assert len(data["problems"]) == 1 and "behaviour" in data["problems"][0]


def test_label_map_endpoint_404s_a_model_rls_hides(client, as_user):
    as_user(_user_client([])[0])
    res = client.get(f"/api/models/{MODEL_ID}/label-map", headers={"Authorization": "Bearer t"})
    assert res.status_code == 404


def test_label_map_endpoint_rejects_a_non_uuid_id(client, as_user):
    as_user(_user_client([])[0])
    assert client.get("/api/models/not-a-uuid/label-map", headers={"Authorization": "Bearer t"}).status_code == 422


def test_label_map_endpoint_requires_auth(client):
    assert client.get(f"/api/models/{MODEL_ID}/label-map").status_code in (401, 403, 422)
