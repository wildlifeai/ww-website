# Spec — Per-Organisation Storage Quota (`organisation_usage`)

> **Status:** 📋 Proposal, not scheduled (2026-11-26): nothing built since July 2026, and no issue on
> either side; the ww-backend half is item 8 of the [schema hand-off](ww-backend-schema-handoff.md).
> ww-backend (schema) + ww-website (enforce). It assumes Drive originals; on the AWS path the
> originals are in S3 ([2026-10_aws-reannz-migration](2026-10_aws-reannz-migration/README.md)).

**Goal:** cap how much image data an organisation can store, to prevent abuse and back the
"free for conservation, paid tier for large-scale" model in the FAQ. The per-request upload cap is
already enforced in the website (`MAX_UPLOAD_IMAGES_PER_REQUEST`); this spec is the **cumulative**
quota, which needs a schema (ww-backend) and an enforcement hook + UI (ww-website).

## Design at a glance

- Store the **byte size per media** (`media.file_bytes`) so usage is recomputable and auditable.
- Maintain a **per-org running total** (`organisation_usage`) via a **DB trigger** on `media` — atomic,
  can't drift, no app bookkeeping. The org is resolved `media → deployment → project → organisation`.
- **Enforce** in the website's upload path: before registering media, check
  `bytes_stored + incoming ≤ quota`; reject with `QUOTA_EXCEEDED`.
- **Quota** is per-org with a global default (`quota_bytes NULL → default`), so a paid tier is just a
  per-row override.

## ww-backend (owns schema, RLS, GRANTs, triggers)

### 1. Record size per media

```sql
ALTER TABLE public.media ADD COLUMN IF NOT EXISTS file_bytes bigint;
-- backfill is optional; rows with NULL count as 0 until reprocessed.
```

### 2. Usage table

```sql
CREATE TABLE IF NOT EXISTS public.organisation_usage (
  organisation_id uuid PRIMARY KEY REFERENCES public.organisations(id) ON DELETE CASCADE,
  bytes_stored    bigint  NOT NULL DEFAULT 0,
  media_count     integer NOT NULL DEFAULT 0,
  quota_bytes     bigint,                       -- NULL → use the global default
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT organisation_usage_bytes_nonneg CHECK (bytes_stored >= 0),
  CONSTRAINT organisation_usage_count_nonneg CHECK (media_count >= 0)
);

-- Global default quota (one row). 5 GiB shown; tune to taste.
CREATE TABLE IF NOT EXISTS public.app_settings (key text PRIMARY KEY, value jsonb NOT NULL);
INSERT INTO public.app_settings (key, value)
VALUES ('default_org_quota_bytes', to_jsonb(5368709120::bigint))
ON CONFLICT (key) DO NOTHING;
```

### 3. Trigger to keep the total current

```sql
CREATE OR REPLACE FUNCTION public.fn_media_usage_delta() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  org uuid;
  delta_bytes bigint := 0;
  delta_count int := 0;
BEGIN
  -- "active" = not soft-deleted. Compute the change in active footprint.
  IF (TG_OP = 'INSERT') THEN
    IF NEW.deleted_at IS NULL THEN delta_bytes := COALESCE(NEW.file_bytes, 0); delta_count := 1; END IF;
  ELSIF (TG_OP = 'DELETE') THEN
    IF OLD.deleted_at IS NULL THEN delta_bytes := -COALESCE(OLD.file_bytes, 0); delta_count := -1; END IF;
  ELSE  -- UPDATE: react to soft-delete/undelete and to a file_bytes correction
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
      delta_bytes := -COALESCE(OLD.file_bytes, 0); delta_count := -1;
    ELSIF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL THEN
      delta_bytes := COALESCE(NEW.file_bytes, 0); delta_count := 1;
    ELSIF NEW.deleted_at IS NULL THEN
      delta_bytes := COALESCE(NEW.file_bytes, 0) - COALESCE(OLD.file_bytes, 0);
    END IF;
  END IF;

  IF delta_bytes = 0 AND delta_count = 0 THEN RETURN NULL; END IF;

  SELECT p.organisation_id INTO org
  FROM public.deployments d JOIN public.projects p ON p.id = d.project_id
  WHERE d.id = COALESCE(NEW.deployment_id, OLD.deployment_id);
  IF org IS NULL THEN RETURN NULL; END IF;

  INSERT INTO public.organisation_usage (organisation_id, bytes_stored, media_count)
  VALUES (org, GREATEST(delta_bytes, 0), GREATEST(delta_count, 0))
  ON CONFLICT (organisation_id) DO UPDATE
    SET bytes_stored = GREATEST(public.organisation_usage.bytes_stored + delta_bytes, 0),
        media_count  = GREATEST(public.organisation_usage.media_count  + delta_count, 0),
        updated_at   = now();
  RETURN NULL;
END $$;

CREATE TRIGGER trg_media_usage
  AFTER INSERT OR DELETE OR UPDATE OF deleted_at, file_bytes ON public.media
  FOR EACH ROW EXECUTE FUNCTION public.fn_media_usage_delta();
```

### 4. RLS + GRANTs

```sql
ALTER TABLE public.organisation_usage ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.organisation_usage TO authenticated;   -- read your own org's usage (UI meter)
-- No INSERT/UPDATE/DELETE grant to `authenticated`: only the SECURITY DEFINER trigger and
-- service-role write it.
CREATE POLICY org_usage_read ON public.organisation_usage FOR SELECT TO authenticated
  USING (organisation_id IN (
    SELECT scope_id FROM public.user_roles
    WHERE user_id = auth.uid() AND scope_type = 'organisation' AND is_active AND deleted_at IS NULL
  ));
```

### 5. Reconcile job (drift safety net)

A scheduled routine recomputes the truth, so a missed trigger or a manual Drive delete can't desync:

```sql
INSERT INTO public.organisation_usage (organisation_id, bytes_stored, media_count)
SELECT p.organisation_id, COALESCE(SUM(m.file_bytes), 0), COUNT(*)
FROM public.media m
JOIN public.deployments d ON d.id = m.deployment_id
JOIN public.projects p ON p.id = d.project_id
WHERE m.deleted_at IS NULL
GROUP BY p.organisation_id
ON CONFLICT (organisation_id) DO UPDATE
  SET bytes_stored = EXCLUDED.bytes_stored, media_count = EXCLUDED.media_count, updated_at = now();
```

## ww-website (owns enforcement + UI)

1. **Populate `media.file_bytes`** at registration — the upload job already has the final byte length
   of each stored image (`len(content)`, post-BMP→JPEG). Set `file_bytes` on the `media` insert in
   `jobs/definitions.py` (the `REGISTER MEDIA` step).
2. **Enforce before upload** — in `routers/exif.py` (or the Drive job), resolve the org for the target
   deployment, read `organisation_usage.bytes_stored` + the effective quota
   (`quota_bytes` or the `default_org_quota_bytes` setting), and reject the batch with a `403
   QUOTA_EXCEEDED` when `bytes_stored + sum(incoming bytes) > quota`. Mirror the existing
   `IMAGE_LIMIT_EXCEEDED` shape so the frontend can show a clear message.
3. **Surface it** — show "X of Y GB used" on the upload screen (read `organisation_usage` directly via
   Supabase RLS; the SELECT grant + policy above allow it).

## Notes / decisions

- **Why a column + trigger, not app-only counting:** the trigger is atomic and self-heals on
  soft-delete; app-only counting drifts and needs the reconcile job to be the source of truth. With the
  column, the reconcile job is a cheap `SUM` and the trigger keeps the hot path correct.
- **Renditions** (`media-renditions` bucket) and **Drive originals** are both derived from the same
  media rows, so counting `media.file_bytes` (the stored original size) is the right single number;
  renditions are a bounded multiple and can be folded into the quota factor if needed.
- **Reclaiming space:** purging soft-deleted media from Drive should clear or hard-delete the row; the
  trigger already decremented usage when `deleted_at` was set, so no double counting.
- Quota is on the **organisation** (the tenant/billing unit). A paid tier just sets a higher
  `quota_bytes` on that org's row.
