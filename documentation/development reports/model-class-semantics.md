# Model class semantics: what a label actually asserts

> **Status:** ✅ Decided 9 Oct 2026, v1 built: a class predicts a taxon or a type, behaviour
> is rejected (LM-10). Requiring a `taxon_id`, a server-side write gate and the GBIF export
> are still open, see [Outcome](#outcome).

Started from [#134](https://github.com/wildlifeai/ww-website/issues/134), where a two-class
person detector shipped with a one-line labels file. Fixing that exposed a larger question
the label chain never answered: **what kind of thing is a model class allowed to assert?**

## 1. What the chain guarantees, and where it stops

[`dual-layer-ai-architecture-proposal.md`](dual-layer-ai-architecture-proposal.md) states the
chain that must never break:

> model output tensor index *i* → labels.txt line *i* → `ai_models.label_map[label]` → taxon
> → alert payload `class_index=i`

LM-1 through LM-9 guard every link except the last named one. **Nothing checks "→ taxon".**
LM-8 verifies an observation resolves to one `ai_models` row, which is provenance, not
meaning. So a class can carry any label, or none, and still flow through to an observation.

LM-1 is now enforced properly (output tensor parsed, compared to the labels list) so a class
without a label can no longer ship. That closes #134 but says nothing about what the label
*means*.

## 2. Evidence from dev

The taxonomic backbone exists and is in reasonable shape:

| | |
|---|---|
| `taxa` rows | 206, of which **190 carry a `gbif_taxon_id`** |
| `Rattus norvegicus` | present, GBIF `2439261` |
| `Rattus rattus` | present, GBIF `2439270` |
| `Homo sapiens` | present, **`gbif_taxon_id` is null** |

It is not connected to anything:

```
observations sampled: 654
  taxon_id set:        0
  scientific_name set: 409
  top names: Anas platyrhynchos (222), Ardea cinerea (41),
             Homo sapiens (33), Rattus norvegicus (30)
```

**Zero of 654 observations link to a taxon.** Every one carries a free-text
`scientific_name`, so the 190 GBIF keys in `taxa` are unreachable from any observation and
no export can resolve one.

The model side matches. `Rat Detection`'s `label_map` holds
`{'rat': {'role': 'target', 'taxon_id': None, 'scientific_name': 'Rattus rattus', ...}}`:
a typed name with no backbone link.

## 3. Why "require a taxon" is the wrong rule

The obvious fix, making `taxon_id` mandatory for every `role: 'target'` class, was the first
proposal here and it is wrong. It rejects a valid model: one classifying **behaviour**
(foraging, grooming, alert) has no species to point at, and neither does one classifying
life stage or sex.

CamtrapDP already models this correctly, and the schema already follows it. Observations in
dev use the full shape:

```
observation_type: animal 377, blank 205, human 36, vehicle 22, unknown 14
behavior:         foraging 12
life_stage:       adult 208, subadult 71, juvenile 11
sex:              female 94, male 53
```

An observation is a **type**, optionally a **taxon**, and optionally **behaviour, life
stage, sex, count**. A model class can legitimately target any of those fields.

## 4. Proposed shape

Each target class declares which observation field it predicts, and is validated against
that field's vocabulary:

```jsonc
"rat":      { "role":"target", "predicts":"taxon",      "taxon_id":"<uuid>" },
"person":   { "role":"target", "predicts":"type",       "observation_type":"human" },
"foraging": { "role":"target", "predicts":"behavior",   "behavior":"foraging" },
"adult":    { "role":"target", "predicts":"life_stage", "life_stage":"adult" },
"not rat":  { "role":"background" }
```

The check (**LM-10**) becomes: *a target class must resolve to exactly one CamtrapDP
observation field, with a value drawn from a controlled source* — `taxa.id` for taxon, the
CamtrapDP vocabulary for type/life-stage/sex, a project-level list for behaviour. That
yields a resolvable identifier for both display and export without pretending "foraging" is
a species.

For GBIF, resolve `taxa.gbif_taxon_id` at export time by joining, rather than denormalising
a key onto `observations`. Correcting a taxon then fixes every past observation at once.

## 5. Defects this exposes

- **`edge_reflection.py:97` hardcodes `"observation_type": "animal"`.** Every edge
  observation is typed animal regardless of class, so a person detection writes a human as
  an animal. The value has to come from the label map.
- **A behaviour class should not mint its own observation row.** `_reflect_media` creates
  one observation per target label above threshold; for behaviour that produces rows with no
  species, meaningless as a GBIF occurrence and noise in the UI. Behaviour is an attribute
  of an occurrence, so it attaches to an existing observation or is not an observation.
  **This is the decision that determines whether behaviour models fit the pipeline at all.**
- **`label_map` has no server-side gate.** `ModelLabelMapper.tsx` writes it straight from
  the browser (`supabase.from('ai_models').update({ label_map: map })`). Its only validation
  checks `scientific_name` rather than `taxon_id`, and it does not block Save, so an
  unmapped target saves cleanly. Any rule needs an endpoint or a constraint, not a hint.
- **The CamtrapDP import wrote `gbif_taxon_key`, a column `observations` does not have.**
  Fixed in #138: `taxonID` is no longer written, and mapping it to `taxon_id` is this
  report's question.

## 6. Person detection and GBIF

`human` is already an `observation_type` in use, so the CamtrapDP-native mapping for a
person class is `observationType: human` with no taxon. Camera-trap datasets published to
GBIF normally exclude or anonymise human detections rather than publishing them as
occurrence records, so a `Homo sapiens` GBIF key is arguably the thing to avoid rather than
the thing to add. Recorded here as the reasoning; the call belongs to whoever owns the
publishing policy.

This also settles #134's labelling without a taxon decision: class 1 is
`predicts: type, observation_type: human`, class 0 is background.

## Open items

- [#135](https://github.com/wildlifeai/ww-website/issues/135) — LM-10: declare what each
  model class predicts, and validate it. Carries the `edge_reflection` observation_type fix
  and the behaviour-model decision.
- Backfilling `gbif_taxon_id` for the 16 `taxa` rows missing one is data work, not code, and
  is only worth doing once the export path exists.

## Outcome

Decided on [#135](https://github.com/wildlifeai/ww-website/issues/135) (9 Oct 2026): in v1 a
model class predicts a **taxon** or a **type** (`predicts: taxon|type`). A behaviour
prediction is not an observation, so section 5's second bullet resolves to "not an
observation" and LM-10 rejects `predicts: behavior` for now. Life stage and sex are out of v1
for the same reason. The rat classifier and the person detector both fit.

Built (website only, no schema change, `predicts` lives inside `label_map`):
`domain/label_map.py` (LM-10), edge reflection typing each row by what its class predicts
(`edge_reflection.py:97` no longer hardcodes `animal`), `ModelLabelMapper` offering a type, and
`GET /api/models/{model_id}/label-map`. The rules and the default for maps saved before
`predicts` are in [what a class predicts](../resources/ai-model-pipeline.md#what-a-class-predicts-lm-10).

Also decided (9 Oct 2026): a class's `predicts` is set per class, so one model can mix a taxon
and a type; a Camera AI `human` or `vehicle` row does not count as presence in evidence fusion
(`burst_evidence.edge_signals`); existing person models are fixed by re-saving the person class
as a type in the mapper, not by a shipped default.

Still open:

- LM-10 accepts a taxon class with only a `scientific_name`, because `Rat Detection` and
  every trained model can carry `taxon_id: null`. Requiring a `taxa.id` is what makes the GBIF
  keys reachable; deferred until those models carry one.
- No server-side write gate: `ModelLabelMapper` writes `label_map` straight from the browser,
  and LM-10 is reported, not enforced, on a save
  ([#324](https://github.com/wildlifeai/ww-website/issues/324)).
