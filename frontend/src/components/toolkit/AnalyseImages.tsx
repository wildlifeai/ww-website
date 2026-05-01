import { useState, useRef, useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiClient } from '../../lib/apiClient'
import { supabase } from '../../config/supabase'
import { useDragAndDrop } from '../../hooks/useDragAndDrop'
import { PipelineStatusBox, type PipelineState, type LogEntry } from './PipelineStatusBox'
import { INaturalistPanel } from './INaturalistPanel'
import { ImageClustering } from './ImageClustering'

interface Deployment {
  id: string
  project_id: string
  location_name: string | null
  latitude: number | null
  longitude: number | null
  deployment_start: string | null
}

interface ExifResult {
  filename: string
  deployment_id: string | null
  gps_lat: number | null
  gps_lon: number | null
  datetime: string | null
  detection: string | null
  matched_deployment: string | null
}

function derivePhase(state: PipelineState): 'idle' | 'uploading' | 'processing' | 'completed' | 'failed' | 'stalled' {
  if (state.jobs.some(j => j.status === 'failed')) return 'failed'

  if (state.jobs.length > 0 && state.jobs.every(j =>
    ['completed', 'completed_with_errors', 'failed', 'skipped'].includes(j.status)
  )) return 'completed'

  // Edge Case: 0 jobs generated (all files were skipped as duplicates)
  if (state.totalFiles > 0 && state.uploadedFiles === state.totalFiles && state.jobs.length === 0) return 'completed'

  if (state.uploadedFiles < state.totalFiles) return 'uploading'

  const lastUpdateAge = Date.now() - state.lastUpdateTs
  if (lastUpdateAge > 15000 && state.jobs.some(j => j.status === 'processing')) return 'stalled'

  if (state.jobs.length > 0) return 'processing'
  return 'idle'
}

export function AnalyseImages() {
  const [files, setFiles] = useState<File[]>([])
  const [filePaths, setFilePaths] = useState<string[]>([])
  const [results, setResults] = useState<ExifResult[]>([])
  
  // Progress states
  const [pipelineState, setPipelineState] = useState<PipelineState>({
    totalFiles: 0,
    uploadedFiles: 0,
    jobs: [],
    logs: [],
    lastUpdateTs: 0
  })
  
  const [deployments, setDeployments] = useState<Deployment[]>([])
  const folderInputRef = useRef<HTMLInputElement>(null)
  const lastSeenSeqRef = useRef<Record<string, number>>({})

  // Drive upload options
  const [uploadToDrive, setUploadToDrive] = useState(false)

  // Fetch all deployments from Supabase on mount for coordinate mapping
  useEffect(() => {
    supabase
      .from('deployments')
      .select('id, project_id, location_name, latitude, longitude, deployment_start')
      .is('deleted_at', null)
      .then(({ data }) => {
        if (data) setDeployments(data)
      })
  }, [])

  // Poll active Google Drive upload jobs
  useEffect(() => {
    const jobs = pipelineState.jobs
    if (jobs.length === 0) return

    const incompleteJobs = jobs.filter(j =>
      j.status === 'queued' ||
      j.status === 'in_progress' ||
      j.status === 'processing' ||
      j.status === 'deferred'
    )
    if (incompleteJobs.length === 0) return

    const timer = setInterval(async () => {
      try {
        const updates = await Promise.all(
          jobs.map(async (job) => {
            if (['completed', 'completed_with_errors', 'failed', 'skipped'].includes(job.status)) return job

            try {
              const res = await apiClient.get(`/api/jobs/${job.id}`)
              const j = res.data?.data ?? res.data ?? {}

              let status = j.status ?? job.status
              let progress = j.progress ?? job.progress

              if (progress >= 0.999 && (status === 'in_progress' || status === 'processing')) {
                status = 'completed'
                progress = 1.0
              }

              return {
                ...job,
                status,
                progress,
                error: j.error ? String(j.error) : job.error,
                message: j.message ?? job.message,
                updatedAt: j.updated_at ?? job.updatedAt,
                currentPhase: j.current_phase ?? job.currentPhase,
                summary: j.summary ?? job.summary,
                eventCount: j.event_count ?? job.eventCount ?? 0,
                // events from this poll (consumed below, not stored in state)
                _events: j.events ?? [],
              } as typeof job & { _events: Array<{ seq: number; type: string; phase: string; timestamp: string; message: string }> }
            } catch {
              return job
            }
          })
        )

        let changed = false
        const logsToAdd: LogEntry[] = []

        for (let i = 0; i < jobs.length; i++) {
          const prev = jobs[i]
          const next = updates[i] as any

          if (
            prev.status !== next.status ||
            prev.progress !== next.progress ||
            prev.message !== next.message ||
            (next.eventCount || 0) > (prev.eventCount || 0)
          ) {
            changed = true
          }

          // ── Structured event consumption (seq-based) ──────
          const events: Array<{ seq: number; type: string; phase: string; timestamp: string; message: string }> = next._events || []
          const prevSeq = lastSeenSeqRef.current[next.id] ?? -1

          // Sort by seq defensively, then filter to only new events
          const newEvents = events
            .sort((a, b) => a.seq - b.seq)
            .filter(e => e.seq > prevSeq)

          if (newEvents.length > 0) {
            lastSeenSeqRef.current[next.id] = newEvents[newEvents.length - 1].seq
            changed = true

            for (const evt of newEvents) {
              let level: LogEntry['level'] = 'info'
              if (evt.type === 'file_success' || evt.type === 'phase_complete') level = 'success'
              if (evt.type === 'file_failure' || evt.type === 'stall_warning') level = 'error'
              if (evt.type === 'file_skip') level = 'warning'
              if (evt.type === 'folder_created') level = 'info'
              if (evt.type === 'heartbeat') level = 'warning'

              logsToAdd.push({
                ts: new Date(evt.timestamp).getTime() || Date.now(),
                level,
                message: evt.message,
              })
            }
          } else if (next.message && next.message !== prev.message && events.length === 0) {
            // Fallback for jobs without structured events (other job types)
            let level: LogEntry['level'] = 'info'
            if (next.status === 'completed' || next.status === 'completed_with_errors') level = 'success'
            if (next.status === 'failed') level = 'error'
            logsToAdd.push({ ts: Date.now(), level, message: next.message })
          }

          // Strip _events before storing in state
          delete next._events
        }

        // Deduplicate simultaneous identical logs
        const uniqueLogs = new Set<string>()
        const filteredLogs = logsToAdd.filter(log => {
          if (uniqueLogs.has(log.message)) return false
          uniqueLogs.add(log.message)
          return true
        })

        if (changed) {
          setPipelineState(prev => ({
            ...prev,
            jobs: updates,
            logs: [...prev.logs, ...filteredLogs],
            lastUpdateTs: Date.now(),
          }))
        }
      } catch (e) {
        console.error('Polling failed', e)
      }
    }, 2000)

    return () => clearInterval(timer)
  }, [pipelineState.jobs])

  const analyseMutation = useMutation({
    mutationFn: async (imageFiles: File[]) => {
      const chunkSize = 10
      const totalBatches = Math.ceil(imageFiles.length / chunkSize)
      lastSeenSeqRef.current = {}

      setPipelineState({
          totalFiles: imageFiles.length,
          uploadedFiles: 0,
          jobs: [],
          logs: [{ ts: Date.now(), level: 'info', message: `🚀 Starting pipeline for ${imageFiles.length} images (${totalBatches} batch${totalBatches > 1 ? 'es' : ''} of ${chunkSize})…` }],
          lastUpdateTs: Date.now()
      })
      setResults([])

      const allResults: ExifResult[] = []

      for (let i = 0; i < imageFiles.length; i += chunkSize) {
        const batchNum = Math.floor(i / chunkSize) + 1
        const batchEnd = Math.min(i + chunkSize, imageFiles.length)

        // Log batch start before API call
        setPipelineState(prev => ({
          ...prev,
          logs: [...prev.logs, { ts: Date.now(), level: 'info' as const, message: `📦 Processing batch ${batchNum}/${totalBatches} (images ${i + 1}–${batchEnd})…` }],
          lastUpdateTs: Date.now(),
        }))

        const chunk = imageFiles.slice(i, i + chunkSize)
        const chunkPaths = filePaths.slice(i, i + chunkSize)
        const formData = new FormData()
        for (const f of chunk) {
          formData.append('files', f)
        }
        for (const p of chunkPaths) {
          formData.append('paths', p)
        }
        if (uploadToDrive) {
          formData.append('upload_to_drive', 'true')
        }
        
        try {
          const response = await apiClient.upload('/api/exif/parse', formData)
          const data = response.data ?? {}
          const raw: any[] = data.images ?? data ?? []
          
          const mapped: ExifResult[] = raw.map((item: any) => {
            const exif = item.exif ?? {}
            const lat = exif.latitude ?? null
            const lon = exif.longitude ?? null
            const hasExifGps = lat !== null && lon !== null && !(lat === 0 && lon === 0)
            const depId = exif.deployment_id ?? null
            
            const matchedDep = depId
              ? deployments.find((d) => 
                  d.id.toLowerCase() === depId.toLowerCase() ||
                  (depId.length === 8 && d.id.toLowerCase().startsWith(depId.toLowerCase()))
                )
              : null

            let finalLat = hasExifGps ? Number(lat) : null
            let finalLon = hasExifGps ? Number(lon) : null
            if (finalLat === null && finalLon === null && matchedDep?.latitude && matchedDep?.longitude) {
              finalLat = matchedDep.latitude
              finalLon = matchedDep.longitude
            }

            return {
              filename: item.filename ?? 'unknown',
              deployment_id: depId,
              gps_lat: finalLat,
              gps_lon: finalLon,
              datetime: exif.date ?? exif.Datetime_Original ?? exif.DateTime ?? null,
              detection: exif.UserComment ?? null,
              matched_deployment: matchedDep?.location_name ?? null,
            }
          })
          
          allResults.push(...mapped)
          setResults([...allResults])
          
          const driveInfo = data.drive_upload
          
          setPipelineState(prev => {
              const logs = [...prev.logs]
              const jobs = [...prev.jobs]
              const startIdx = i + 1
              const endIdx = Math.min(i + chunkSize, imageFiles.length)
              
              if (driveInfo) {
                if (driveInfo.status === 'skipped') {
                    const reason = driveInfo.reason === 'no_files_stored' ? 'Images already exist in system (duplicates)' : driveInfo.reason
                    logs.push({ ts: Date.now(), level: 'warning', message: `⏭️ Images ${startIdx}-${endIdx} skipped: ${reason}` })
                } else if (driveInfo.job_id) {
                    jobs.push({ 
                        id: driveInfo.job_id, 
                        status: driveInfo.status || 'queued', 
                        progress: 0, 
                        fileCount: driveInfo.file_count || chunk.length 
                    })
                    
                    if (driveInfo.duplicates_skipped > 0) {
                        logs.push({ ts: Date.now(), level: 'warning', message: `⏭️ ${driveInfo.duplicates_skipped} images in batch already exist in system.` })
                    }
                    
                    if (driveInfo.file_count > 0) {
                        logs.push({ ts: Date.now(), level: 'success', message: `✅ Buffered locally. Drive sync queued for ${driveInfo.file_count} images.` })
                    }
                } else if (driveInfo.status === 'error') {
                    logs.push({ ts: Date.now(), level: 'error', message: `❌ Azure/Drive integration failed: ${driveInfo.error || 'Unknown error'}` })
                }
              } else if (!uploadToDrive) {
                  logs.push({ ts: Date.now(), level: 'success', message: `✅ Images ${startIdx}-${endIdx} extracted.` })
              }
              
              return {
                  ...prev,
                  uploadedFiles: endIdx,
                  jobs,
                  logs,
                  lastUpdateTs: Date.now()
              }
          })

        } catch (e: any) {
          console.error("Chunk failed", e)
          const errorMessage = e.response?.data?.detail || e.response?.data?.error?.message || e.message || String(e)
          setPipelineState(prev => ({
              ...prev,
              logs: [...prev.logs, { ts: Date.now(), level: 'error', message: `❌ Failed to process images ${i+1}-${Math.min(i+chunkSize, imageFiles.length)}: ${errorMessage}` }],
              uploadedFiles: Math.min(i + chunkSize, prev.totalFiles)
          }))
        }
      }

      return allResults
    }
  })

  const processFiles = (incoming: File[]) => {
    const imageFiles = incoming.filter((f) =>
      f.type.startsWith('image/') || f.name.toLowerCase().endsWith('.jpg') || f.name.toLowerCase().endsWith('.jpeg')
    )
    const paths = imageFiles.map((f) => (f as any).entryPath || f.webkitRelativePath || f.name)
    setFiles(imageFiles)
    setFilePaths(paths)
    setResults([])
    setPipelineState({ totalFiles: 0, uploadedFiles: 0, jobs: [], logs: [], lastUpdateTs: Date.now() })
    lastSeenSeqRef.current = {}
  }

  const { isDragging, bind } = useDragAndDrop(processFiles)

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      processFiles(Array.from(e.target.files))
    }
  }

  // Count unique deployment prefixes from paths
  const folderDepCount = new Set(
    filePaths
      .map((p) => {
        const m = p.match(/MEDIA[/\\]([A-Fa-f0-9]{8})[/\\]/i)
        return m ? m[1].toUpperCase() : null
      })
      .filter(Boolean)
  ).size

  return (
    <div>
      {/* iNaturalist connection panel (auto-hides when disabled) */}
      <INaturalistPanel />

      <h3 style={{ marginBottom: '0.5rem' }}>Analyse Camera Trap Images</h3>
      <p style={{ opacity: 0.7, marginBottom: '1.5rem' }}>
        Upload JPEG images or drag-and-drop an entire SD card folder from your
        Wildlife Watcher device. Deployment IDs are automatically extracted from
        the folder structure (<code>MEDIA/&lt;id&gt;/IMAGES.NNN/</code>).
      </p>

      <div
        className="card"
        {...bind}
        style={{
          textAlign: 'center',
          padding: '2rem',
          cursor: 'pointer',
          borderStyle: 'dashed',
          borderWidth: '2px',
          borderColor: isDragging ? 'var(--primary)' : undefined,
          backgroundColor: isDragging ? 'rgba(var(--primary-rgb, 59,130,246), 0.05)' : undefined,
          transition: 'border-color 0.2s, background-color 0.2s',
        }}
        onClick={() => folderInputRef.current?.click()}
      >
        <div style={{ pointerEvents: 'none' }}>
          <div style={{ fontSize: '2.5rem', marginBottom: '1rem', opacity: 0.8 }}>
            {isDragging ? '📥' : '📂'}
          </div>
          <p style={{ fontWeight: 500, marginBottom: '0.25rem' }}>
            {isDragging ? 'Drop to analyse' : 'Click to select folder or drag-and-drop here'}
          </p>
          <p style={{ fontSize: '0.75rem', opacity: 0.6 }}>
            Supports SD card directory structure (MEDIA/...)
          </p>
        </div>
        <input
          ref={folderInputRef}
          type="file"
          multiple
          {...{ webkitdirectory: "", directory: "" } as any}
          style={{ display: 'none' }}
          onChange={handleInputChange}
        />
      </div>

      {/* Selected files summary */}
      {files.length > 0 && (
        <div style={{ textAlign: 'center', marginTop: '1rem', fontSize: '0.8125rem' }}>
          <strong>{files.length} images selected</strong>
          <div style={{ opacity: 0.6, fontSize: '0.75rem', marginTop: '0.25rem' }}>
            {(files.reduce((acc, f) => acc + f.size, 0) / (1024 * 1024)).toFixed(2)} MB total
            {folderDepCount > 0 && <span style={{ color: 'var(--success)', marginLeft: '0.5rem' }}>• {folderDepCount} deployment{folderDepCount > 1 ? 's' : ''} detected from folders</span>}
          </div>
        </div>
      )}

      {/* ── Google Drive upload options ──────────────────────────── */}
      {files.length > 0 && results.length === 0 && (
        <div
          className="card"
          style={{
            marginTop: '1rem',
            padding: '1rem 1.25rem',
            borderLeft: uploadToDrive ? '3px solid var(--primary)' : '3px solid var(--border)',
            transition: 'border-color 0.2s',
          }}
        >
          {/* Toggle */}
          <label
            id="drive-upload-toggle"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.625rem',
              cursor: 'pointer',
              userSelect: 'none',
              fontWeight: 500,
              fontSize: '0.875rem',
            }}
          >
            <input
              type="checkbox"
              checked={uploadToDrive}
              onChange={(e) => {
                setUploadToDrive(e.target.checked)
              }}
              style={{ width: '1rem', height: '1rem', accentColor: 'var(--primary)' }}
            />
            ☁️ Upload images to Google Drive
          </label>
        </div>
      )}

      {/* ── Analyse button ──────────────────────────────────────── */}
      {files.length > 0 && results.length === 0 && pipelineState.totalFiles === 0 && (
        <div style={{ marginTop: '1.5rem', textAlign: 'center' }}>
          <button
            className="btn"
            disabled={analyseMutation.isPending}
            onClick={() => analyseMutation.mutate(files)}
            style={{ 
              padding: '0.75rem 2rem', 
              opacity: analyseMutation.isPending ? 0.7 : 1,
              transition: 'opacity 0.2s',
              width: analyseMutation.isPending ? '100%' : 'auto',
              maxWidth: '300px'
            }}
          >
            {analyseMutation.isPending ? `Starting Pipeline...` : `Analyse ${files.length} Image${files.length > 1 ? 's' : ''}`}
          </button>
        </div>
      )}

      {analyseMutation.isError && (
        <p style={{ color: 'var(--error)', marginTop: '1rem', textAlign: 'center' }}>
          {(analyseMutation.error as Error).message}
        </p>
      )}

      {/* ── Pipeline status ─────────────────────────────────────── */}
      {pipelineState.totalFiles > 0 && (
          <PipelineStatusBox state={pipelineState} phase={derivePhase(pipelineState)} />
      )}

      {/* ── Results table ───────────────────────────────────────── */}
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
                    {r.gps_lat !== null && r.gps_lon !== null ? (
                      `${Number(r.gps_lat).toFixed(4)}, ${Number(r.gps_lon).toFixed(4)}`
                    ) : (
                      <span style={{ opacity: 0.8, color: 'var(--error)' }}>⚠️ No GPS Info</span>
                    )}
                  </td>
                  <td style={{ padding: '0.5rem', fontSize: '0.75rem' }}>{r.datetime || '—'}</td>
                  <td style={{ padding: '0.5rem', fontSize: '0.75rem' }}>{r.detection || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Near-duplicate clustering ─────────────────────────── */}
      <div
        style={{
          marginTop: '2.5rem',
          paddingTop: '2rem',
          borderTop: '1px solid var(--border)',
        }}
      >
        <ImageClustering />
      </div>
    </div>
  )
}
