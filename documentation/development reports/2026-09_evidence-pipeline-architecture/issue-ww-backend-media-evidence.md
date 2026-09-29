# Draft issue for wildlifeai/wildlife-watcher-backend (not filed)

Title: `media_evidence`: per-frame signal table for the evidence pipeline

Labels: review-finding, schema

---

**What:** Add a `media_evidence` table, one row per (media, signal, source, source_version), so
the website can persist the inputs and output of its presence score without new columns on
`observations` or writes into `media.exif_metadata`.

**Where:** `supabase/schemas/public/tables/` (suggested `47b_media_evidence.sql`, beside
`47_media_embeddings.sql`), a policy under `yyy_policies/`, a pgTAP test.

**Context:** The website's evidence pipeline scores each frame from per-model verdicts, burst
neighbours and motion, and writes the verdict as one `source_type='consensus'` row in
`observations`; the signals behind it need a home that grows without a migration per signal.
Contract and signal list: section 8 of the
[evidence-pipeline architecture report](https://github.com/wildlifeai/ww-website/blob/dev/documentation/development%20reports/2026-09_evidence-pipeline-architecture/README.md)
(lands with ww-website `docs/evidence-pipeline-architecture`).

## Proposed DDL

Constraints follow `media_embeddings` and `observations` (composite FK on
`(media_id, deployment_id)`).

```sql
CREATE TABLE media_evidence (
  id uuid PRIMARY KEY NOT NULL DEFAULT gen_random_uuid(),
  media_id uuid NOT NULL,
  deployment_id uuid NOT NULL REFERENCES deployments (id) ON DELETE CASCADE,
  signal text NOT NULL,
  value float4,
  value_text text,
  source text NOT NULL,
  source_version text,
  computed_at timestamptz NOT NULL DEFAULT now(),
  run_id uuid,

  -- (media, deployment) must match the media row, as in observations / media_embeddings.
  CONSTRAINT fk_media_evidence_media
    FOREIGN KEY (media_id, deployment_id) REFERENCES media (id, deployment_id) ON DELETE CASCADE,

  -- A row carries a number, a label, or both; never neither.
  CONSTRAINT chk_media_evidence_has_value CHECK (value IS NOT NULL OR value_text IS NOT NULL),

  -- One row per (media, signal, source, source_version). NULLS NOT DISTINCT so a NULL
  -- source_version cannot produce duplicates (Postgres 15+; otherwise make
  -- source_version NOT NULL DEFAULT '').
  CONSTRAINT uq_media_evidence UNIQUE NULLS NOT DISTINCT (media_id, signal, source, source_version)
);

CREATE INDEX idx_media_evidence_media ON media_evidence (media_id);
CREATE INDEX idx_media_evidence_deployment_signal ON media_evidence (deployment_id, signal);

COMMENT ON TABLE media_evidence IS 'Per-frame evidence signals for the presence pipeline: one row per (media, signal, source, source_version). Inputs and output of the website''s evidence score; the verdict itself is the source_type=consensus row in observations.';
COMMENT ON COLUMN media_evidence.signal IS 'Signal name, enumerated in the website (domain/burst_evidence.py): speciesnet_presence, speciesnet_max_conf, gemini_presence, gemini_visibility, gemini_size, motion_frac, edge_presence, edge_score, burst_id, burst_index, burst_len, burst_animal_count, neighbour_animal, evidence_score, evidence_threshold, evidence_weights_version.';
COMMENT ON COLUMN media_evidence.value IS 'Numeric value (0 to 1 for presence and score signals, integers for burst counts). NULL for text-only signals.';
COMMENT ON COLUMN media_evidence.value_text IS 'Text value for identities and labels (burst_id, visibility label, weights version).';
COMMENT ON COLUMN media_evidence.source IS 'Producer: speciesnet, gemini, edge, motion, bursts, fusion.';
COMMENT ON COLUMN media_evidence.source_version IS 'Model id or code version of the producer (speciesnet-v4.0.1a, gemini-3.1-flash-lite, motion_v1, bursts_v1, evidence_fusion_v1).';
COMMENT ON COLUMN media_evidence.run_id IS 'annotation_runs.id of the run that produced the row when one exists. Not a FK: that row is best-effort on the website.';

ALTER TABLE media_evidence ENABLE ROW LEVEL SECURITY;

GRANT SELECT ON public.media_evidence TO authenticated;

GRANT ALL ON public.media_evidence TO service_role;
```

Signal names are deliberately not a CHECK: the website owns the enumeration. If a CHECK is
preferred, the v1 list is in the column comment.

## RLS

Reads mirror `yyy_policies/71_observations.sql`; no INSERT or UPDATE policy for
`authenticated`, because only the website's service role writes.

```sql
-- *** media_evidence RLS Policies ***
-- Read access scoped via deployment -> project membership, as for observations.
CREATE POLICY "Project members can view media evidence"
  ON media_evidence
  FOR SELECT
  TO authenticated
  USING (
    EXISTS (
      SELECT 1
      FROM deployments AS d
      WHERE d.id = media_evidence.deployment_id
        AND d.deleted_at IS NULL
        AND has_project_role((SELECT auth.uid()), d.project_id, 'project_viewer')
    )
  );
```

## pgTAP test

In `supabase/tests/`, in the style of `09_storage_policy.test.sql`. Helper names for media and
deployment ids are placeholders for whatever the fixtures provide.

```sql
BEGIN;
SELECT plan(9);

-- Structure
SELECT has_table('public', 'media_evidence', 'media_evidence exists');
SELECT has_column('public', 'media_evidence', 'signal', 'has signal');
SELECT col_not_null('public', 'media_evidence', 'source', 'source is not null');
SELECT has_fk('public', 'media_evidence', 'has a foreign key');
SELECT is(
  (SELECT relrowsecurity FROM pg_class WHERE relname = 'media_evidence'),
  true, 'RLS is enabled');
SELECT policies_are('public', 'media_evidence',
  ARRAY['Project members can view media evidence'], 'only the read policy exists');

-- Behaviour: two users, two projects, one deployment and one media row each.
-- Insert one signal row per media as service_role, then:

-- 1. A viewer of project A sees A's row and not B's.
SELECT tests.authenticate_as('viewer_a');
SELECT results_eq(
  $$ SELECT count(*)::int FROM media_evidence $$,
  $$ VALUES (1) $$,
  'viewer of project A sees exactly A''s evidence rows');

-- 2. An authenticated user cannot insert (no policy): RLS rejects the write.
SELECT throws_ok(
  $$ INSERT INTO media_evidence (media_id, deployment_id, signal, value, source)
     VALUES (tests.media_id('a'), tests.deployment_id('a'), 'motion_frac', 0.01, 'motion') $$,
  '42501',
  'authenticated cannot insert evidence rows');

-- 3. The unique constraint holds with a NULL source_version.
SELECT tests.authenticate_as_service_role();
SELECT throws_ok(
  $$ INSERT INTO media_evidence (media_id, deployment_id, signal, value, source)
     SELECT media_id, deployment_id, signal, value, source FROM media_evidence LIMIT 1 $$,
  '23505',
  'duplicate (media, signal, source, NULL version) is rejected');

SELECT * FROM finish();
ROLLBACK;
```

## Acceptance criteria

- Table, indexes, unique constraint, RLS policy and grants merged through
  `npm run db:change media_evidence`; types regenerated.
- The pgTAP test above passes.
- Deployed on dev, after which the website's writer (`services/media_evidence.py`) stops logging
  `media_evidence_write_skipped`.

Non-breaking: a new table only; the mobile app does not read it.
