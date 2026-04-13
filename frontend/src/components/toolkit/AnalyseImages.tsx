import { useState, useRef } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiClient } from '../../lib/apiClient'

interface ExifResult {
  filename: string
  deployment_id: string | null
  gps_lat: number | null
  gps_lon: number | null
  datetime: string | null
  detection: string | null
  matched_deployment: string | null
}

export function AnalyseImages() {
  const [files, setFiles] = useState<File[]>([])
  const [results, setResults] = useState<ExifResult[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  const analyseMutation = useMutation({
    mutationFn: async (imageFiles: File[]) => {
      const formData = new FormData()
      for (const f of imageFiles) {
        formData.append('files', f)
      }
      return apiClient.upload('/api/exif/parse', formData)
    },
    onSuccess: (response: any) => {
      const raw: any[] = response.data ?? []
      const mapped: ExifResult[] = raw.map((item: any) => {
        const exif = item.exif ?? {}
        const lat = exif.latitude ?? null
        const lon = exif.longitude ?? null
        // Treat 0,0 as "no GPS" since the firmware writes 0,0 when no fix is available
        const hasGps = lat !== null && lon !== null && !(lat === 0 && lon === 0)
        return {
          filename: item.filename ?? 'unknown',
          deployment_id: exif.deployment_id ?? null,
          gps_lat: hasGps ? lat : null,
          gps_lon: hasGps ? lon : null,
          datetime: exif.date ?? exif.Datetime_Original ?? exif.DateTime ?? null,
          detection: exif.UserComment ?? null,
          matched_deployment: null,
        }
      })
      setResults(mapped)
    },
  })

  const handleFiles = (selectedFiles: FileList | null) => {
    if (!selectedFiles) return
    const imageFiles = Array.from(selectedFiles).filter((f) =>
      f.type.startsWith('image/')
    )
    setFiles(imageFiles)
    setResults([])
  }

  return (
    <div>
      <h3 style={{ marginBottom: '0.5rem' }}>Analyse Camera Trap Images</h3>
      <p style={{ opacity: 0.7, marginBottom: '1.5rem' }}>
        Upload JPEG images from your Wildlife Watcher device. The EXIF data will be
        parsed to extract deployment IDs, GPS coordinates, timestamps, and matched
        against your Supabase deployments.
      </p>

      <div
        className="card"
        style={{ textAlign: 'center', padding: '2rem', cursor: 'pointer', borderStyle: 'dashed', borderWidth: '2px' }}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept="image/jpeg,image/jpg"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => handleFiles(e.target.files)}
        />
        {files.length > 0 ? (
          <div>
            <strong>{files.length} image{files.length > 1 ? 's' : ''} selected</strong>
            <div style={{ fontSize: '0.75rem', opacity: 0.6, marginTop: '0.25rem' }}>
              {(files.reduce((s, f) => s + f.size, 0) / 1024 / 1024).toFixed(2)} MB total
            </div>
          </div>
        ) : (
          <div>
            <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>🖼️</div>
            <p>Select JPEG images to analyse</p>
          </div>
        )}
      </div>

      {files.length > 0 && results.length === 0 && (
        <div style={{ marginTop: '1.5rem', textAlign: 'center' }}>
          <button
            className="btn"
            disabled={analyseMutation.isPending}
            onClick={() => analyseMutation.mutate(files)}
            style={{ padding: '0.75rem 2rem' }}
          >
            {analyseMutation.isPending ? 'Analysing…' : `Analyse ${files.length} Image${files.length > 1 ? 's' : ''}`}
          </button>
        </div>
      )}

      {analyseMutation.isError && (
        <p style={{ color: 'var(--error)', marginTop: '1rem', textAlign: 'center' }}>
          {(analyseMutation.error as Error).message}
        </p>
      )}

      {results.length > 0 && (
        <div style={{ marginTop: '1.5rem', overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
            <thead>
              <tr style={{ borderBottom: '2px solid var(--border)', textAlign: 'left' }}>
                <th style={{ padding: '0.5rem' }}>File</th>
                <th style={{ padding: '0.5rem' }}>Deployment</th>
                <th style={{ padding: '0.5rem' }}>GPS</th>
                <th style={{ padding: '0.5rem' }}>Date/Time</th>
                <th style={{ padding: '0.5rem' }}>Detection</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '0.5rem', fontFamily: 'monospace', fontSize: '0.75rem' }}>{r.filename}</td>
                  <td style={{ padding: '0.5rem' }}>
                    {r.matched_deployment ? (
                      <span style={{ color: 'var(--success)' }}>✓ {r.matched_deployment}</span>
                    ) : r.deployment_id ? (
                      <span style={{ fontSize: '0.75rem', fontFamily: 'monospace' }}>{r.deployment_id.slice(0, 8)}…</span>
                    ) : (
                      <span style={{ opacity: 0.4 }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: '0.5rem', fontSize: '0.75rem' }}>
                    {r.gps_lat && r.gps_lon ? `${r.gps_lat.toFixed(4)}, ${r.gps_lon.toFixed(4)}` : '—'}
                  </td>
                  <td style={{ padding: '0.5rem', fontSize: '0.75rem' }}>{r.datetime || '—'}</td>
                  <td style={{ padding: '0.5rem', fontSize: '0.75rem' }}>{r.detection || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
