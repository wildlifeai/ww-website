"""What a model class asserts: ``ai_models.label_map`` semantics and LM-10 (#135).

``label_map`` is keyed by device label (the model's own class order lives in
``detection_capabilities``). Each entry is either a background class, which is
never reflected, or a target class that declares what it predicts:

```jsonc
"rat":     { "role": "target", "predicts": "taxon", "taxon_id": "<uuid>", "scientific_name": "Rattus rattus" },
"person":  { "role": "target", "predicts": "type",  "observation_type": "human" },
"not rat": { "role": "background" }
```

In v1 a target predicts a **taxon** or a **type** (decision of 9 Oct 2026 on
#135). A behaviour prediction is not an observation, so ``predicts: behavior``
is rejected for now rather than minting rows with no species.

A target written before ``predicts`` existed carries no key and is read as
``taxon``: that is what edge reflection did with every target until now
(``observation_type: animal`` plus the entry's taxon fields), so stored models
keep behaving as they did. A person class saved that way still reflects as an
animal until it is re-saved as ``predicts: type``.

LM-10: a target class resolves to exactly one observation field, with a value
from a controlled source. ``taxon`` needs a ``taxon_id`` or a
``scientific_name``; ``type`` needs an ``observation_type`` from
``TARGET_OBSERVATION_TYPES``.
"""

from __future__ import annotations

from typing import Any

TARGET_ROLE = "target"
BACKGROUND_ROLE = "background"

PREDICTS_TAXON = "taxon"
PREDICTS_TYPE = "type"
PREDICTS = (PREDICTS_TAXON, PREDICTS_TYPE)
# A target with no ``predicts`` key predates #135; this keeps its old meaning.
DEFAULT_PREDICTS = PREDICTS_TAXON

# ``observations_observation_type_check`` (ww-backend supabase/schema.sql) allows
# animal, human, vehicle, blank and unknown. A class can only *assert* the first
# three: "blank" means nothing is there, which is what a background class says,
# and "unknown" is not a prediction.
TARGET_OBSERVATION_TYPES = ("animal", "human", "vehicle")
_NOT_A_PREDICTION = ("blank", "unknown")
_BEHAVIOUR = ("behavior", "behaviour")


def _text(value: Any) -> str | None:
    """A stripped non-empty string, else ``None``."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def entry_predicts(entry: dict) -> str | None:
    """The field a target entry predicts, with the pre-#135 default; ``None`` when the value is not a string."""
    raw = entry.get("predicts")
    if raw is None:
        return DEFAULT_PREDICTS
    return raw.strip().lower() if isinstance(raw, str) else None


def entry_problem(label: str, entry: Any) -> str | None:
    """LM-10 for one ``label_map`` entry: the reason it is invalid, or ``None``."""
    if not isinstance(entry, dict):
        return f"LM-10: class '{label}' must be an object with a role"
    role = entry.get("role")
    if role == BACKGROUND_ROLE:
        return None
    if role != TARGET_ROLE:
        return f"LM-10: class '{label}' has role {role!r}; it must be 'target' or 'background'"

    predicts = entry_predicts(entry)
    if predicts in _BEHAVIOUR:
        return (
            f"LM-10: class '{label}' predicts behaviour, which is not supported: a behaviour prediction "
            "is not an observation. A class can predict a taxon or a type; mark this class background."
        )
    if predicts == PREDICTS_TAXON:
        if _text(entry.get("taxon_id")) or _text(entry.get("scientific_name")):
            return None
        return f"LM-10: class '{label}' predicts a taxon but names none; map it to a species or mark it background"
    if predicts == PREDICTS_TYPE:
        obs_type = _text(entry.get("observation_type"))
        if obs_type in TARGET_OBSERVATION_TYPES:
            return None
        allowed = ", ".join(TARGET_OBSERVATION_TYPES)
        if obs_type in _NOT_A_PREDICTION:
            return f"LM-10: class '{label}' predicts type '{obs_type}', which is not a detection; mark the class background"
        if obs_type is None:
            return f"LM-10: class '{label}' predicts a type but has no observation_type; use one of {allowed}"
        return f"LM-10: class '{label}' predicts type {obs_type!r}; use one of {allowed}"
    return f"LM-10: class '{label}' predicts {entry.get('predicts')!r}; a class can predict 'taxon' or 'type'"


def label_map_problems(label_map: Any) -> list[str]:
    """LM-10 over a whole ``label_map``: one message per invalid class, empty when valid."""
    if not isinstance(label_map, dict):
        return ["LM-10: label_map must be an object keyed by class label"]
    return [p for label, entry in label_map.items() if (p := entry_problem(str(label), entry))]


def target_observation_fields(entry: Any) -> dict | None:
    """The observation fields a target class writes, or ``None`` when it writes nothing.

    ``None`` for background classes, behaviour, an unknown ``predicts`` and a
    type with no detection value, so a bad label map yields no row rather than a
    wrong one. A taxon class with no name still writes an ``animal`` row with no
    taxon, as every target did before #135: LM-10 reports it, reflection keeps it.
    """
    if not isinstance(entry, dict) or entry.get("role") != TARGET_ROLE:
        return None
    predicts = entry_predicts(entry)
    if predicts == PREDICTS_TYPE:
        obs_type = _text(entry.get("observation_type"))
        if obs_type not in TARGET_OBSERVATION_TYPES:
            return None
        # A type says what kind of thing is there, never which species: a person
        # class writes observation_type 'human' with no taxon (CamtrapDP, and
        # camera-trap GBIF exports leave people out rather than key them).
        return {"observation_type": obs_type, "taxon_id": None, "scientific_name": None, "vernacular_name": None}
    if predicts == PREDICTS_TAXON:
        return {
            "observation_type": "animal",
            "taxon_id": _text(entry.get("taxon_id")),
            "scientific_name": _text(entry.get("scientific_name")),
            "vernacular_name": _text(entry.get("vernacular_name")),
        }
    return None


def model_predicts(label_map: Any) -> list[str]:
    """The distinct fields a model's valid target classes predict, in ``PREDICTS`` order."""
    if not isinstance(label_map, dict):
        return []
    found = {entry_predicts(e) for e in label_map.values() if target_observation_fields(e) is not None}
    return [p for p in PREDICTS if p in found]


# ── Read (GET /api/models/{model_id}/label-map) ──────────────────────

LABEL_MAP_SELECT = "id, name, detection_capabilities, label_map"


def describe_label_map(row: dict) -> dict:
    """A model's label map as the API returns it (pure).

    ``label_map`` is the stored map with the default ``predicts`` written into any
    target that predates it, so a reader never has to know the default.
    ``predicts`` lists what the model's valid targets predict and ``problems`` is
    LM-10 over the stored map (empty when the map is valid).
    """
    stored = row.get("label_map") if isinstance(row.get("label_map"), dict) else {}

    def with_default(entry: Any) -> Any:
        if isinstance(entry, dict) and entry.get("role") == TARGET_ROLE and "predicts" not in entry:
            return {**entry, "predicts": DEFAULT_PREDICTS}
        return entry

    return {
        "model_id": row.get("id"),
        "name": row.get("name"),
        "labels": list(row.get("detection_capabilities") or []),
        "label_map": {label: with_default(entry) for label, entry in stored.items()},
        "predicts": model_predicts(stored),
        "problems": label_map_problems(row.get("label_map") or {}),
    }


def fetch_model_label_map(client, model_id: str) -> dict | None:
    """``describe_label_map`` for one model, read through ``client``; ``None`` when it cannot be seen.

    Pass the caller's own client (``get_user_client``) so RLS decides which models
    are visible: a model the caller may not read is indistinguishable from one
    that does not exist.
    """
    res = client.table("ai_models").select(LABEL_MAP_SELECT).eq("id", model_id).is_("deleted_at", "null").limit(1).execute()
    row = (res.data or [None])[0]
    return describe_label_map(row) if row else None
