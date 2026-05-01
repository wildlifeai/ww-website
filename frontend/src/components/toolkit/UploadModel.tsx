import { useState, useRef, useEffect, useMemo } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiClient } from '../../lib/apiClient'
import { JobProgress } from '../common'

const PRETRAINED_MODELS: Record<string, { resolutions: string[] }> = {
  "Person Detection": { resolutions: ["96x96"] },
  "YOLOv8 Object Detection": { resolutions: ["192x192"] },
  "YOLOv11 Object Detection": { resolutions: ["192x192", "224x224"] },
  "YOLOv8 Pose Estimation": { resolutions: ["256x256"] }
}

export function UploadModel() {
  const [modelSource, setModelSource] = useState('Custom Upload') // 'Custom Upload' | 'SenseCap Models' | 'Pre-trained Model'
  const [sscmaModelId, setSscmaModelId] = useState('')
  const [selectedOrgId, setSelectedOrgId] = useState('')

  // Pre-trained state
  const [ptArchitecture, setPtArchitecture] = useState('Person Detection')
  const [ptResolution, setPtResolution] = useState('96x96')

  const [file, setFile] = useState<File | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const [modelName, setModelName] = useState('')
  const [description, setDescription] = useState('')

  // Fetch organisations where the user is a manager
  const { data: managedOrgs, isLoading: isLoadingOrgs } = useQuery({
    queryKey: ['managedOrgs'],
    queryFn: async () => {
      const res = await apiClient.get('/api/models/managed-orgs')
      return (res.data || []) as { id: string; name: string }[]
    },
  })

  // Auto-select first org when loaded
  useEffect(() => {
    if (managedOrgs && managedOrgs.length > 0 && !selectedOrgId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedOrgId(managedOrgs[0].id)
    }
  }, [managedOrgs, selectedOrgId])

  // Automatically update resolution based on architecture
  useEffect(() => {
    if (modelSource === 'Pre-trained Model') {
      const availableRes = PRETRAINED_MODELS[ptArchitecture]?.resolutions || []
      if (!availableRes.includes(ptResolution) && availableRes.length > 0) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setPtResolution(availableRes[0])
      }
    }
  }, [ptArchitecture, modelSource, ptResolution])

  // Fetch SenseCap Models
  interface SscmaModel {
    uuid: string
    name: string
    network?: {
      input?: {
        shape?: number[]
      }
    }
  }

  const { data: sscmaModels, isLoading: isLoadingSscma } = useQuery({
    queryKey: ['sscmaModels'],
    queryFn: async () => {
      const res = await apiClient.get('/api/models/sscma/catalog')
      return res.data || []
    },
    enabled: modelSource === 'SenseCap Models'
  })

  // Auto-fill Model Name when SenseCap model is selected
  useEffect(() => {
    if (modelSource === 'SenseCap Models' && sscmaModelId && sscmaModels) {
      const model = (sscmaModels as SscmaModel[]).find(m => m.uuid === sscmaModelId || m.name === sscmaModelId)
      if (model) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setModelName(model.name)
      }
    }
  }, [sscmaModelId, modelSource, sscmaModels])

  const computedResolution = useMemo(() => {
    if (modelSource === 'SenseCap Models' && sscmaModelId && sscmaModels) {
      const model = (sscmaModels as SscmaModel[]).find(m => m.uuid === sscmaModelId || m.name === sscmaModelId)
      if (model) {
        try {
          const shape = model.network?.input?.shape || []
          if (shape.length >= 2) return `${shape[0]}x${shape[1]}`
        } catch { /* ignore */ }
      }
    }
    return null
  }, [modelSource, sscmaModelId, sscmaModels])

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (modelSource === 'SenseCap Models') {
        const model = (sscmaModels as SscmaModel[]).find(m => m.uuid === sscmaModelId || m.name === sscmaModelId)
        return apiClient.post('/api/models/pretrained', {
          source_type: 'sscma',
          sscma_uuid: model?.uuid || sscmaModelId,
          model_name: modelName,
          description: description,
          organisation_id: selectedOrgId
        })
      } else if (modelSource === 'Pre-trained Model') {
        return apiClient.post('/api/models/pretrained', {
          source_type: 'pretrained',
          architecture: ptArchitecture,
          resolution: ptResolution,
          model_name: `${ptArchitecture} (${ptResolution})`,
          description: description,
          organisation_id: selectedOrgId
        })
      } else {
        const formData = new FormData()
        formData.append('file', file!)
        formData.append('model_name', modelName)
        if (description) {
          formData.append('description', description)
        }
        if (selectedOrgId) {
          formData.append('organisation_id', selectedOrgId)
        }
        return apiClient.upload('/api/models/convert', formData)
      }
    },
    onSuccess: (response: { data?: { job_id?: string; status?: string } }) => {
      if (response?.data?.job_id) {
        setJobId(response.data.job_id)
      } else if (response?.data?.status === 'validated') {
        alert('Model successfully registered!')
        setModelName('')
        setDescription('')
        setSscmaModelId('')
      } else {
        setJobId('done')
      }
    },
  })

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(false)
    const dropped = e.dataTransfer.files[0]
    if (dropped?.name.endsWith('.zip') || dropped?.name.endsWith('.tflite') || dropped?.name.endsWith('.cc')) {
      setFile(dropped)
    }
  }

  let isFormValid = false
  if (modelSource === 'SenseCap Models') {
    isFormValid = !!sscmaModelId && modelName.trim().length > 0 && !!selectedOrgId
  } else if (modelSource === 'Pre-trained Model') {
    isFormValid = !!selectedOrgId
  } else {
    isFormValid = !!file && modelName.trim().length > 0
  }

  // Permission gate: show access denied if user has no managed orgs
  if (isLoadingOrgs) {
    return (
      <div style={{ textAlign: 'center', padding: '3rem', opacity: 0.6 }}>
        <p>Checking permissions…</p>
      </div>
    )
  }

  if (!managedOrgs || managedOrgs.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '3rem' }}>
        <div style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>🔒</div>
        <h3 style={{ marginBottom: '0.5rem' }}>Access Restricted</h3>
        <p style={{ opacity: 0.7, maxWidth: '400px', margin: '0 auto' }}>
          You need the <strong>Organisation Manager</strong> role to upload AI models.
          Contact your organisation administrator to request access.
        </p>
      </div>
    )
  }

  return (
    <div>
      <h3 style={{ marginBottom: '0.5rem' }}>Upload AI Model</h3>
      <p style={{ opacity: 0.7, marginBottom: '1.5rem' }}>
        Import an Edge Impulse model, upload raw model files, or select a pre-trained model from the SenseCap Zoo. It will be compiled, packaged, and versioned securely under your organisation.
      </p>

      {/* Organisation Selector */}
      {managedOrgs.length > 1 && (
        <div style={{ marginBottom: '1.5rem' }}>
          <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Organisation</label>
          <select
            value={selectedOrgId}
            onChange={(e) => setSelectedOrgId(e.target.value)}
            style={{
              width: '100%',
              padding: '0.75rem',
              borderRadius: '4px',
              border: '1px solid var(--border)',
              backgroundColor: 'var(--surface)',
              color: 'var(--text-color)',
            }}
          >
            {managedOrgs.map(org => (
              <option key={org.id} value={org.id}>{org.name}</option>
            ))}
          </select>
        </div>
      )}

      <div style={{ marginBottom: '1.5rem' }}>
        <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Model Source</label>
        <select
          value={modelSource}
          onChange={(e) => setModelSource(e.target.value)}
          style={{
            width: '100%',
            padding: '0.75rem',
            borderRadius: '4px',
            border: '1px solid var(--border)',
            backgroundColor: 'var(--surface)',
            color: 'var(--text-color)',
          }}
        >
          <option value="Custom Upload">Custom Upload</option>
          <option value="Pre-trained Model">Pre-trained Model</option>
          <option value="SenseCap Models">SenseCap Models</option>
        </select>
      </div>

      {modelSource === 'SenseCap Models' && (
        <div style={{ marginBottom: '1.5rem', padding: '1rem', backgroundColor: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
          <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>SenseCap Zoo Model</label>
          {isLoadingSscma ? (
            <div style={{ padding: '0.5rem', opacity: 0.6 }}>Loading models…</div>
          ) : (
            <select
              value={sscmaModelId}
              onChange={(e) => setSscmaModelId(e.target.value)}
              style={{ width: '100%', padding: '0.75rem', borderRadius: '4px', border: '1px solid var(--border)', backgroundColor: 'transparent', color: 'var(--text-color)' }}
            >
              <option value="">Select a SenseCap Model…</option>
              {(sscmaModels as SscmaModel[])?.map(m => (
                <option key={m.uuid || m.name} value={m.uuid || m.name}>{m.name}</option>
              ))}
            </select>
          )}
          {computedResolution && (
            <p style={{ fontSize: '0.75rem', opacity: 0.8, marginTop: '0.5rem' }}>Resolution: {computedResolution}</p>
          )}
        </div>
      )}

      {modelSource === 'Pre-trained Model' && (
        <div style={{ marginBottom: '1.5rem', padding: '1rem', backgroundColor: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
          <div style={{ display: 'flex', gap: '1rem' }}>
            <div style={{ flex: 2 }}>
              <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>Model Architecture</label>
              <select
                value={ptArchitecture}
                onChange={(e) => setPtArchitecture(e.target.value)}
                style={{ width: '100%', padding: '0.75rem', borderRadius: '4px', border: '1px solid var(--border)', backgroundColor: 'transparent', color: 'var(--text-color)' }}
              >
                {Object.keys(PRETRAINED_MODELS).map(k => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', fontSize: '0.8125rem', fontWeight: 500, marginBottom: '0.25rem' }}>Resolution</label>
              <select
                value={ptResolution}
                onChange={(e) => setPtResolution(e.target.value)}
                style={{ width: '100%', padding: '0.75rem', borderRadius: '4px', border: '1px solid var(--border)', backgroundColor: 'transparent', color: 'var(--text-color)' }}
              >
                {PRETRAINED_MODELS[ptArchitecture]?.resolutions.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          </div>
        </div>
      )}

      {modelSource !== 'Pre-trained Model' && (
        <div style={{ marginBottom: '1.5rem' }}>
          <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Model Name</label>
          <input
            type="text"
            value={modelName}
            onChange={e => setModelName(e.target.value)}
            placeholder="e.g. Rat Detection"
            className="input"
            style={{ width: '100%', padding: '0.75rem', borderRadius: '4px', border: '1px solid var(--border)' }}
          />
          <p style={{ fontSize: '0.8rem', opacity: 0.6, marginTop: '0.25rem' }}>
            This groups versions together. If a model with this name exists, this upload becomes the next version.
          </p>
        </div>
      )}

      <div style={{ marginBottom: '1.5rem' }}>
        <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Description (Optional)</label>
        <textarea
          value={description}
          onChange={e => setDescription(e.target.value)}
          placeholder="What's new in this version?"
          className="input"
          rows={3}
          style={{ width: '100%', padding: '0.75rem', borderRadius: '4px', border: '1px solid var(--border)' }}
        />
      </div>

      {modelSource === 'Custom Upload' && (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragActive(true) }}
          onDragLeave={() => setDragActive(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className="card"
          style={{
            textAlign: 'center',
            padding: '3rem 2rem',
            cursor: 'pointer',
            borderStyle: 'dashed',
            borderWidth: '2px',
            borderColor: dragActive ? 'var(--primary)' : 'var(--border)',
            backgroundColor: dragActive ? 'rgba(76,175,80,0.05)' : 'var(--surface)',
            transition: 'border-color 0.2s, background-color 0.2s',
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".zip,.tflite,.cc"
            style={{ display: 'none' }}
            onChange={(e) => {
              const selected = e.target.files?.[0]
              if (selected) setFile(selected)
            }}
          />
          {file ? (
            <div>
              <div style={{ fontSize: '1.5rem', marginBottom: '0.25rem' }}>📦</div>
              <strong>{file.name}</strong>
              <div style={{ fontSize: '0.75rem', opacity: 0.6, marginTop: '0.25rem' }}>
                {(file.size / 1024 / 1024).toFixed(2)} MB
              </div>
            </div>
          ) : (
            <div>
              <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>📤</div>
              <p>Drag & drop your model file here, or click to browse</p>
              <p style={{ fontSize: '0.75rem', opacity: 0.5 }}>Max 50 MB · .zip, .tflite, .cc</p>
            </div>
          )}
        </div>
      )}

      {!jobId && (
        <div style={{ marginTop: '1.5rem', textAlign: 'center' }}>
          <button
            className="btn"
            disabled={!isFormValid || uploadMutation.isPending}
            onClick={() => uploadMutation.mutate()}
            style={{ padding: '0.75rem 2rem' }}
          >
            {uploadMutation.isPending
              ? (modelSource === 'Pre-trained Model' ? '⏳ Registering model…' : modelSource === 'SenseCap Models' ? '⏳ Importing from SenseCap…' : '⏳ Uploading & converting…')
              : (modelSource === 'Custom Upload' ? 'Upload & Process Model' : 'Import & Process Model')}
          </button>
        </div>
      )}

      {/* Status Log */}
      {(uploadMutation.isPending || uploadMutation.isError || uploadMutation.isSuccess) && !jobId && (
        <div style={{
          marginTop: '1rem',
          padding: '1rem',
          backgroundColor: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          fontSize: '0.8125rem',
          fontFamily: 'monospace',
          lineHeight: '1.8',
        }}>
          {/* Steps shown during/after mutation */}
          <div style={{ opacity: 0.9 }}>✅ Authenticated as current user</div>
          <div style={{ opacity: 0.9 }}>
            ✅ Source: <strong>{modelSource}</strong>
            {modelSource === 'Pre-trained Model' && <> — {ptArchitecture} ({ptResolution})</>}
            {modelSource === 'SenseCap Models' && sscmaModelId && <> — {modelName}</>}
          </div>

          {uploadMutation.isPending && (
            <div style={{ opacity: 0.7 }}>
              ⏳ {modelSource === 'Pre-trained Model'
                ? 'Registering model in your organisation…'
                : modelSource === 'SenseCap Models'
                  ? 'Downloading and packaging model from SenseCap Zoo…'
                  : 'Uploading file and starting conversion job…'}
            </div>
          )}

          {uploadMutation.isSuccess && (
            <div style={{ color: 'var(--primary)' }}>
              ✅ Model registered successfully!
            </div>
          )}

          {uploadMutation.isError && (
            <div style={{ color: 'var(--error)' }}>
              ❌ <strong>Failed:</strong> {(uploadMutation.error as Error).message}
              <div style={{ marginTop: '0.5rem', opacity: 0.7, fontSize: '0.75rem' }}>
                Check that the backend is running on port 8000 and your user has the <code>organisation_manager</code> role.
              </div>
            </div>
          )}
        </div>
      )}

      <JobProgress jobId={jobId} />
    </div>
  )
}

