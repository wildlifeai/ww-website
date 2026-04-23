# Image clustering + motion ROI cropping

Date: 2026-04-23

This document describes how Wildlife Watcher clusters near-duplicate camera-trap images and how the motion-based ROI cropping improves cluster accuracy by ignoring static background.

## Goals

- Reduce uploads (e.g. to iNaturalist) by selecting **1 representative image per near-duplicate cluster**.
- Make clustering robust to static backgrounds by hashing **only the animal region** (ROI crop).
- Always preserve a clean mapping from any crop back to the **original image filename**.

## High-level pipeline

This is implemented in `backend/app/domain/clustering.py` and exposed via `POST /api/clustering/analyze`.

1. **Load frames** from uploaded bytes into `PIL.Image`.
2. (Optional) **Compute per-frame motion ROIs** across the burst.
3. For each frame:
   - Compute `dHash` (perceptual hash) on either:
     - full frame (default), or
     - ROI crop (if enabled).
   - Compute a sharpness score (Laplacian variance) on the same region.
4. **Cluster by visual similarity**:
   - Insert hashes into a **BK-tree** and union together images within a Hamming distance threshold.
5. **Pick representatives**:
   - For each cluster, choose the sharpest member as the representative.

6. **Frontend confidence cue (representative preview)**:
   - The UI renders a small thumbnail for each cluster representative.
   - When ROI-cropped hashing is enabled, the thumbnail is a downscaled frame with the
     ROI bbox drawn in red (overlay), so users can quickly verify the cluster target.

## Data model (backend)

### `ImageRecord`

Each uploaded frame becomes an `ImageRecord`:

- `filename`: original filename from upload (this is the stable reference)
- `dhash`: 64-bit dHash computed from the full frame or ROI crop
- `sharpness`: computed from the same region as hashing
- `width`, `height`: original frame size
- `roi`: optional `(x0, y0, x1, y1)` bbox **in full-resolution pixels**
- `index`: original position in the upload list

The `roi` field is what lets the frontend (or any downstream step) crop consistently while still referencing the original file.

## Clustering algorithm details

### Perceptual hash: dHash

`compute_dhash(img, hash_size=8)` computes an 8×8 horizontal difference hash (64 bits). Similar images have small Hamming distance between their hashes.

### Similarity search: BK-tree

To avoid O(N²) comparisons, hashes are inserted into a BK-tree where distance = Hamming distance. For each new image, we query existing hashes within `max_hamming` and union them into the same cluster.

This keeps clustering fast for typical burst sizes and scales well if a user uploads a larger batch.

### Representative selection

Within each cluster, `pick_representatives(...)` selects the member with the highest sharpness (with size as a tie-break).

## Motion ROI cropping (per-frame)

### When to use it

Enable ROI-cropped hashing when:

- frames belong to a single burst/event (shared background)
- the animal moves within the burst
- you want background-invariant clustering

### How it works

The per-frame ROI tracker is `compute_motion_roi_per_frame(...)`.

Key design choices for efficiency:

- All motion detection is performed on a downscaled version of the frames (default `320×240`).
- Motion mask uses `absdiff(current, reference) > diff_threshold`.
- A tiny morphology pass reduces speckle without adding dependencies.
- Connected components are extracted via a simple flood-fill on the small mask.
- Temporal gating prefers the component nearest the previous frame’s ROI center and rejects implausible jumps.
- The chosen bbox is padded slightly and mapped back to full-resolution coordinates.

### How crops are used for clustering

If `roi_crop_for_hashing` is enabled, for each frame with a valid ROI:

- `record.roi` is set to the per-frame bbox
- `record.dhash` is computed on `image.crop(record.roi)`
- `record.sharpness` is computed on `image.crop(record.roi)`

If a frame doesn’t get a valid ROI (e.g. animal stopped moving), the tracker may carry forward the last valid ROI, otherwise the full-frame hash remains.

## API surface

### `POST /api/clustering/analyze`

Form fields:

- `files`: uploaded images
- `max_hamming`: integer 0–20
- `roi_crop_for_hashing`: boolean (enables ROI-cropped hashing)
- `roi_per_frame`: boolean (currently the frontend always sends `true`)

Notes:

- The current UI exposes a **single toggle**: “Use motion ROI crop for clustering”.
  When enabled, it sends `roi_crop_for_hashing=true` and keeps `roi_per_frame=true`.

Response shape (simplified):

- `data.clusters[]` with `members[]` including:
  - `filename` (original)
  - `is_representative`
  - `sharpness`, `width`, `height`
  - `roi` (optional `[x0,y0,x1,y1]`)

### `POST /api/clustering/roi-debug.zip`

Returns a ZIP containing:

- `manifest.json`
- `overlay/*.png`
- `crop/*.png`

Filename behavior:

- Entries are written as PNG and the original extension is stripped to avoid double
  extensions. Example: `overlay/Flat_worm_B_..._frame000000.png` (not `.jpg.png`).

Used for tuning ROI parameters and collecting feedback on crop quality.

## Practical tips

- Start with `max_hamming` around 6–10 for burst near-duplicates.
- Turn on ROI-cropped hashing when backgrounds are identical and the animal is the only moving object.
- If you see whole-frame motion (camera shift, wind), ROI may be rejected by `max_motion_frac`.

## Code pointers

- Domain logic: `backend/app/domain/clustering.py`
  - `compute_dhash`, `BKTree`, `cluster_by_dhash`, `pick_representatives`
  - `compute_motion_roi_per_frame`
- Router: `backend/app/routers/clustering.py`
- UI: `frontend/src/components/toolkit/ImageClustering.tsx`

## UI behavior notes

- The representative preview thumbnails are generated **client-side** from the uploaded
  files plus the returned `member.roi` bboxes. This avoids returning large base64 images
  in the API response and scales better for large uploads.
