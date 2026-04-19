import { useState, useRef } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiClient } from '../../lib/apiClient'
import { JobProgress } from '../common'

export function UploadModel() {
  const [file, setFile] = useState<File | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const uploadMutation = useMutation({
    mutationFn: async (zipFile: File) => {
      const formData = new FormData()
      formData.append('file', zipFile)
      return apiClient.upload('/api/models/convert', formData)
    },
    onSuccess: (response: any) => {
      setJobId(response.data?.job_id)
    },
  })

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(false)
    const dropped = e.dataTransfer.files[0]
    if (dropped?.name.endsWith('.zip')) {
      setFile(dropped)
    }
  }

  return (
    <div>
      <h3 style={{ marginBottom: '0.5rem' }}>Upload & Convert Model</h3>
      <p style={{ opacity: 0.7, marginBottom: '1.5rem' }}>
        Upload an Edge Impulse model ZIP. It will be compiled through Vela for the
        Ethos-U55 NPU, packaged as <code>ai_model.zip</code>, and registered in your
        organisation.
      </p>

      {/* Dropzone */}
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
          accept=".zip"
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
            <p>Drag & drop your Edge Impulse ZIP here, or click to browse</p>
            <p style={{ fontSize: '0.75rem', opacity: 0.5 }}>Max 50 MB · .zip files only</p>
          </div>
        )}
      </div>

      {file && !jobId && (
        <div style={{ marginTop: '1.5rem', textAlign: 'center' }}>
          <button
            className="btn"
            disabled={uploadMutation.isPending}
            onClick={() => uploadMutation.mutate(file)}
            style={{ padding: '0.75rem 2rem' }}
          >
            {uploadMutation.isPending ? 'Uploading…' : 'Convert Model'}
          </button>
        </div>
      )}

      {uploadMutation.isError && (
        <p style={{ color: 'var(--error)', marginTop: '1rem', textAlign: 'center' }}>
          {(uploadMutation.error as Error).message}
        </p>
      )}

      <JobProgress jobId={jobId} />
    </div>
  )
}
