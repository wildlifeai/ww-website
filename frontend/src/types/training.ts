/**
 * Types for the "Create species ID model" action (Annotations → Actions).
 *
 * Mirror of the backend contract in `backend/app/schemas/model.py` (TrainModelRequest)
 * and `backend/app/domain/training.py` (training_status). Keep the two in step.
 */

export type TrainingMode = 'edge_impulse' | 'export_only'

/** GET /api/models/train/status */
export interface TrainingStatus {
  /** FF_MODEL_TRAINING_ENABLED on the server the frontend talks to. */
  enabled: boolean
  /** edge_impulse = trained end to end; export_only = the dataset ZIP is prepared for a manual run. */
  mode: TrainingMode
  min_images_per_class: number
  recommended_images_per_class: number
  max_images: number
  /** Device MAX_CLASSES, background included. */
  max_classes: number
  image_sizes: number[]
  recipe: {
    learning_block: string
    epochs: number
    learning_rate: number
    augmentation: boolean
    validation_split: number
  }
}

export interface TrainClassSpec {
  /** Device class label (one line of labels.txt); sanitised server-side. */
  label: string
  /** Observation scientific_name the class is built from. */
  scientific_name: string
  taxon_id?: string | null
  vernacular_name?: string | null
}

/** POST /api/models/train body */
export interface TrainModelRequest {
  media_ids: string[]
  model_name: string
  description?: string
  organisation_id?: string
  classes: TrainClassSpec[]
  include_background: boolean
  background_label?: string
  image_size: 96 | 160
  colour: 'grayscale' | 'rgb'
  epochs: number
  learning_rate: number
}

/** POST /api/models/train response data */
export interface TrainModelResponse {
  job_id: string
  /** null in export_only mode (no ai_models row is created). */
  model_id: string | null
  mode: TrainingMode
  status: string
  poll_url: string
}
