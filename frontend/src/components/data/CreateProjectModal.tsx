/* eslint-disable react-hooks/set-state-in-effect */
/**
 * CreateProjectModal
 *
 * Creates a new project using direct Supabase writes, the same underlying
 * schema the mobile app uses (no extra backend endpoint needed).
 *
 * Data flow:
 *   organisation_id  ← useCreatableOrgs (lib/projectCreation: the organisations the
 *                      projects INSERT policy accepts; a choice when there are several)
 *   capture_methods / sampling_designs / activity_sensitivity / ai_models
 *                    ← queried directly from Supabase reference tables
 *   insert           → projects table
 */
import { useState, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Modal } from '../ui/Modal'
import { supabase } from '../../config/supabase'
import { useAuth } from '../../hooks/useAuth'
import { useCreatableOrgs } from '../../hooks/useCreatableOrgs'
import { PROJECT_ROLES_KEY } from '../../hooks/useProjectRoles'
import { createProjectMessage } from '../../lib/projectCreation'

// ── Reference data types (mirrors mobile ReferenceDataService) ───────────────

interface CaptureMethod      { id: number; value: string; description: string }
interface SamplingDesign     { id: number; value: string; description: string }
interface ActivitySensitivity{ id: number; value: string; description: string }
interface AiModel            { id: string; name: string; version: string }

export interface CreatedProject { id: string; name: string }

// ── Props ────────────────────────────────────────────────────────────────────

interface Props {
  open: boolean
  onClose: () => void
  onCreated: (project: CreatedProject) => void
}

// ── Helpers ──────────────────────────────────────────────────────────────────

const INPUT: React.CSSProperties = {
  width: '100%',
  padding: '0.5rem 0.625rem',
  borderRadius: 'var(--radius)',
  border: '1px solid var(--border)',
  backgroundColor: 'var(--surface)',
  color: 'var(--text-color)',
  fontSize: '0.875rem',
  boxSizing: 'border-box',
}

const SELECT: React.CSSProperties = { ...INPUT, cursor: 'pointer' }

const LABEL: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '0.3rem',
  fontSize: '0.875rem',
  fontWeight: 500,
}

const ROW2: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr',
  gap: '1rem',
}

// ── Component ────────────────────────────────────────────────────────────────

export function CreateProjectModal({ open, onClose, onCreated }: Props) {
  const { user } = useAuth()
  const queryClient = useQueryClient()

  // ── Org resolution ─────────────────────────────────────────────────────────
  const { orgs, loading: orgsLoading } = useCreatableOrgs()
  const [pickedOrg, setPickedOrg] = useState('')
  const orgId = orgs.some(o => o.id === pickedOrg) ? pickedOrg : (orgs[0]?.id ?? '')

  // ── Reference data ─────────────────────────────────────────────────────────
  const [captureMethods,  setCaptureMethods]  = useState<CaptureMethod[]>([])
  const [samplingDesigns, setSamplingDesigns] = useState<SamplingDesign[]>([])
  const [sensitivities,   setSensitivities]   = useState<ActivitySensitivity[]>([])
  const [aiModels,        setAiModels]        = useState<AiModel[]>([])
  const [refLoading,      setRefLoading]      = useState(false)

  // ── Form state ─────────────────────────────────────────────────────────────
  const [name,              setName]             = useState('')
  const [description,       setDescription]      = useState('')
  const [captureMethodId,   setCaptureMethodId]  = useState('')
  const [samplingDesignId,  setSamplingDesignId] = useState('')
  const [sensitivityId,     setSensitivityId]    = useState('')
  const [timelapseInterval, setTimelapseInterval] = useState('30')
  const [aiModelId,         setAiModelId]        = useState('')
  const [isBaited,          setIsBaited]         = useState(false)
  const [isMonitoringMarked, setIsMonitoringMarked] = useState(false)

  const [saving, setSaving] = useState(false)
  const [error,  setError]  = useState<string | null>(null)

  // ── Load reference data when modal opens ──────────────────────────────────
  useEffect(() => {
    if (!open || !user) return
    setRefLoading(true)
    setError(null)

    Promise.all([
      supabase.from('capture_methods').select('id, value, description').is('deleted_at', null).order('id'),
      supabase.from('sampling_designs').select('id, value, description').is('deleted_at', null).order('id'),
      supabase.from('activity_sensitivity').select('id, value, description').is('deleted_at', null).order('id'),
      supabase.from('ai_models').select('id, name, version').order('name'),
    ]).then(([cm, sd, as_, ai]) => {
      const methods = (cm.data ?? []) as CaptureMethod[]
      const designs = (sd.data ?? []) as SamplingDesign[]

      setCaptureMethods(methods)
      setSamplingDesigns(designs)
      setSensitivities((as_.data ?? []) as ActivitySensitivity[])
      setAiModels((ai.data ?? []) as AiModel[])

      // Set defaults
      if (methods.length)  setCaptureMethodId(String(methods[0].id))
      if (designs.length)  setSamplingDesignId(String(designs[0].id))

      setRefLoading(false)
    })
  }, [open, user])

  const resetForm = () => {
    setName(''); setDescription('')
    setCaptureMethodId(''); setSamplingDesignId('')
    setSensitivityId(''); setTimelapseInterval('30')
    setAiModelId(''); setIsBaited(false); setIsMonitoringMarked(false)
    setPickedOrg(''); setError(null)
  }

  // Determine which conditional fields to show based on capture method label
  const selectedMethod = captureMethods.find(cm => String(cm.id) === captureMethodId)
  const isMotionDetection = selectedMethod?.value?.toLowerCase().includes('motion') ?? false
  const isTimeLapse       = selectedMethod?.value?.toLowerCase().includes('lapse')  ?? false

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!user) return
    if (!orgId) { setError('You cannot create projects yet. Ask an organisation manager to add you.'); return }
    if (!name.trim()) { setError('Project name is required.'); return }

    setSaving(true)
    setError(null)

    const { data, error: err } = await supabase
      .from('projects')
      .insert({
        name:              name.trim(),
        description:       description.trim() || null,
        organisation_id:   orgId,
        capture_method_id: captureMethodId ? Number(captureMethodId) : null,
        sampling_design_id: samplingDesignId ? Number(samplingDesignId) : null,
        activity_detection_sensitivity_id:
          isMotionDetection && sensitivityId ? Number(sensitivityId) : null,
        timelapse_interval_seconds:
          isTimeLapse && timelapseInterval ? Number(timelapseInterval) : null,
        model_id:          aiModelId || null,
        is_baited:         isBaited,
        is_monitoring_marked_individuals: isMonitoringMarked,
        is_active:         true,
        is_archived:       false,
        created_by:        user.id,
        modified_by:       user.id,
      })
      .select('id, name')
      .single()

    setSaving(false)

    if (err) { setError(createProjectMessage(err)); return }
    if (data) {
      // The database made the creator project_admin (handle_new_project): read the roles again so
      // the new project is writable at once, not after the next refetch.
      void queryClient.invalidateQueries({ queryKey: [PROJECT_ROLES_KEY] })
      onCreated(data as CreatedProject)
      resetForm()
      onClose()
    }
  }

  const handleClose = () => { resetForm(); onClose() }

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="New Project"
      size="md"
      footer={
        <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={handleClose}
            style={{ padding: '0.5rem 1rem', fontSize: '0.875rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'transparent', cursor: 'pointer', color: 'var(--text-color)' }}
          >
            Cancel
          </button>
          <button
            type="submit"
            form="create-project-form"
            disabled={saving || refLoading || !orgId}
            style={{ padding: '0.5rem 1.25rem', fontSize: '0.875rem', border: 'none', borderRadius: 'var(--radius)', background: 'var(--primary)', color: '#fff', cursor: saving ? 'wait' : 'pointer', opacity: saving || !orgId ? 0.7 : 1 }}
          >
            {saving ? 'Creating…' : 'Create project'}
          </button>
        </div>
      }
    >
      {refLoading || orgsLoading ? (
        <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '2rem 0' }}>Loading…</p>
      ) : !orgId ? (
        <p style={{ padding: '1.5rem', textAlign: 'center', color: 'var(--text-muted)' }}>
          You cannot create projects yet. Ask an organisation manager to add you.
        </p>
      ) : (
        <form id="create-project-form" onSubmit={handleSubmit}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.125rem' }}>

            {/* An organisation choice only when the user may create in more than one. */}
            {orgs.length > 1 && (
              <label style={LABEL}>
                Organisation
                <select value={orgId} onChange={e => setPickedOrg(e.target.value)} style={SELECT}>
                  {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
                </select>
              </label>
            )}

            {/* ── Name + Description ─────────────────────────────── */}
            <label style={LABEL}>
              Project name <span style={{ color: 'var(--error, red)', fontWeight: 400 }}>*</span>
              <input
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                required
                minLength={3}
                maxLength={100}
                placeholder="e.g. Waitakere Ranges Survey 2025"
                style={INPUT}
              />
            </label>

            <label style={LABEL}>
              Description
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                maxLength={500}
                rows={3}
                placeholder="Optional project description, objectives, or location notes…"
                style={{ ...INPUT, resize: 'vertical', minHeight: '70px' }}
              />
            </label>

            {/* ── Capture method + Sampling design ──────────────── */}
            <div style={ROW2}>
              <label style={LABEL}>
                Capture method
                <select value={captureMethodId} onChange={e => setCaptureMethodId(e.target.value)} style={SELECT}>
                  <option value="">— select —</option>
                  {captureMethods.map(cm => (
                    <option key={cm.id} value={cm.id}>{cm.value}</option>
                  ))}
                </select>
              </label>

              <label style={LABEL}>
                Sampling design
                <select value={samplingDesignId} onChange={e => setSamplingDesignId(e.target.value)} style={SELECT}>
                  <option value="">— select —</option>
                  {samplingDesigns.map(sd => (
                    <option key={sd.id} value={sd.id}>{sd.value}</option>
                  ))}
                </select>
              </label>
            </div>

            {/* ── Conditional: sensitivity (motion) / timelapse interval ── */}
            {isMotionDetection && (
              <label style={LABEL}>
                Detection sensitivity
                <select value={sensitivityId} onChange={e => setSensitivityId(e.target.value)} style={SELECT}>
                  <option value="">— select —</option>
                  {sensitivities.map(s => (
                    <option key={s.id} value={s.id}>{s.value}</option>
                  ))}
                </select>
              </label>
            )}

            {isTimeLapse && (
              <label style={LABEL}>
                Timelapse interval (seconds)
                <input
                  type="number"
                  value={timelapseInterval}
                  onChange={e => setTimelapseInterval(e.target.value)}
                  min={5}
                  max={3600}
                  style={{ ...INPUT, width: '140px' }}
                />
              </label>
            )}

            {/* ── AI Model ──────────────────────────────────────── */}
            <label style={LABEL}>
              AI model <span style={{ fontWeight: 400, opacity: 0.6, fontSize: '0.8125rem' }}>(optional)</span>
              <select value={aiModelId} onChange={e => setAiModelId(e.target.value)} style={SELECT}>
                <option value="">Default</option>
                {aiModels.map(m => (
                  <option key={m.id} value={m.id}>{m.name} v{m.version}</option>
                ))}
              </select>
            </label>

            {/* ── Checkboxes ────────────────────────────────────── */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', cursor: 'pointer', fontSize: '0.875rem', userSelect: 'none' }}>
                <input type="checkbox" checked={isBaited} onChange={e => setIsBaited(e.target.checked)} style={{ accentColor: 'var(--primary)', width: 15, height: 15 }} />
                Baited deployment
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', cursor: 'pointer', fontSize: '0.875rem', userSelect: 'none' }}>
                <input type="checkbox" checked={isMonitoringMarked} onChange={e => setIsMonitoringMarked(e.target.checked)} style={{ accentColor: 'var(--primary)', width: 15, height: 15 }} />
                Monitoring marked individuals
              </label>
            </div>

            {error && (
              <p role="alert" style={{ color: 'var(--error, #f44336)', fontSize: '0.875rem', margin: 0 }}>{error}</p>
            )}
          </div>
        </form>
      )}
    </Modal>
  )
}

