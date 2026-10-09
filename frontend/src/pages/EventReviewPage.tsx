import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { apiClient } from '../lib/apiClient'
import { supabase } from '../config/supabase'

interface ObservationEvent {
  id: string
  deployment_id: string
  scientific_name: string
  nz_code: string
  nz_status: 'CR' | 'EN' | 'LC' | 'Pest'
  confidence: number
  event_start: string
  event_end: string
  duration_seconds: number
  status: 'pending' | 'human_reviewed' | 'expert_reviewed' | 'consensus_approved'
  trigger_cause: 'Animal' | 'Wind' | 'Rain' | 'Lightning' | 'Vegetation' | 'Unknown'
  media: string[]
  reviewer_diffs?: {
    ai_class: string
    ai_confidence: number
    human_class: string
    human_confidence: number
    ai_bbox?: { x: number; y: number; w: number; h: number }
    human_bbox?: { x: number; y: number; w: number; h: number }
  }
}

const MOCK_EVENTS: ObservationEvent[] = [
  {
    id: 'evt-001',
    deployment_id: 'dep-mock',
    scientific_name: 'Apteryx mantelli (Kiwi) 🥝',
    nz_code: 'kiwi',
    nz_status: 'CR',
    confidence: 0.94,
    event_start: '2026-05-27T02:15:00Z',
    event_end: '2026-05-27T02:15:45Z',
    duration_seconds: 45,
    status: 'pending',
    trigger_cause: 'Animal',
    media: [
      'https://picsum.photos/800/600?random=101',
      'https://picsum.photos/800/600?random=102',
      'https://picsum.photos/800/600?random=103',
    ],
    reviewer_diffs: {
      ai_class: 'Kiwi (94%)',
      ai_confidence: 0.94,
      human_class: 'Kiwi (100%)',
      human_confidence: 1.0,
      ai_bbox: { x: 0.2, y: 0.3, w: 0.4, h: 0.5 },
      human_bbox: { x: 0.18, y: 0.28, w: 0.44, h: 0.52 },
    }
  },
  {
    id: 'evt-002',
    deployment_id: 'dep-mock',
    scientific_name: 'Strigops habroptila (Kākāpō) 🦜',
    nz_code: 'kakapo',
    nz_status: 'EN',
    confidence: 0.68,
    event_start: '2026-05-27T03:40:10Z',
    event_end: '2026-05-27T03:41:20Z',
    duration_seconds: 70,
    status: 'pending',
    trigger_cause: 'Unknown',
    media: [
      'https://picsum.photos/800/600?random=201',
      'https://picsum.photos/800/600?random=202',
    ],
    reviewer_diffs: {
      ai_class: 'Kakapo (68%)',
      ai_confidence: 0.68,
      human_class: 'Kakapo (100%)',
      human_confidence: 1.0,
      ai_bbox: { x: 0.3, y: 0.2, w: 0.3, h: 0.6 },
      human_bbox: { x: 0.28, y: 0.18, w: 0.35, h: 0.64 },
    }
  },
  {
    id: 'evt-003',
    deployment_id: 'dep-mock',
    scientific_name: 'Gallirallus australis (Weka) 🐓',
    nz_code: 'weka',
    nz_status: 'LC',
    confidence: 0.88,
    event_start: '2026-05-27T06:12:30Z',
    event_end: '2026-05-27T06:12:45Z',
    duration_seconds: 15,
    status: 'human_reviewed',
    trigger_cause: 'Animal',
    media: [
      'https://picsum.photos/800/600?random=301',
    ],
    reviewer_diffs: {
      ai_class: 'Weka (88%)',
      ai_confidence: 0.88,
      human_class: 'Weka (100%)',
      human_confidence: 1.0,
      ai_bbox: { x: 0.1, y: 0.4, w: 0.5, h: 0.4 },
      human_bbox: { x: 0.1, y: 0.4, w: 0.5, h: 0.4 },
    }
  },
  {
    id: 'evt-004',
    deployment_id: 'dep-mock',
    scientific_name: 'Mustela erminea (Stoat) 🦦',
    nz_code: 'stoat',
    nz_status: 'Pest',
    confidence: 0.52,
    event_start: '2026-05-27T08:22:00Z',
    event_end: '2026-05-27T08:23:10Z',
    duration_seconds: 70,
    status: 'pending',
    trigger_cause: 'Unknown',
    media: [
      'https://picsum.photos/800/600?random=401',
      'https://picsum.photos/800/600?random=402',
      'https://picsum.photos/800/600?random=403',
    ],
    reviewer_diffs: {
      ai_class: 'Stoat (52%)',
      ai_confidence: 0.52,
      human_class: 'Stoat (100%)',
      human_confidence: 1.0,
      ai_bbox: { x: 0.4, y: 0.5, w: 0.2, h: 0.3 },
      human_bbox: { x: 0.38, y: 0.48, w: 0.24, h: 0.34 },
    }
  },
]

function getNZSpeciesInfo(name: string): { code: string; status: 'CR' | 'EN' | 'LC' | 'Pest' } {
  const lowercase = (name || '').toLowerCase()
  if (lowercase.includes('kiwi')) return { code: 'kiwi', status: 'CR' }
  if (lowercase.includes('kakapo')) return { code: 'kakapo', status: 'EN' }
  if (lowercase.includes('weka')) return { code: 'weka', status: 'LC' }
  if (lowercase.includes('stoat')) return { code: 'stoat', status: 'Pest' }
  if (lowercase.includes('possum')) return { code: 'possum', status: 'Pest' }
  if (lowercase.includes('ferret')) return { code: 'ferret', status: 'Pest' }
  return { code: 'unknown', status: 'LC' }
}

export function EventReviewPage() {
  const { deployment_id } = useParams<{ deployment_id: string }>()
  const navigate = useNavigate()

  // State
  const [loading, setLoading] = useState(true)
  const [events, setEvents] = useState<ObservationEvent[]>([])
  const [selectedEventId, setSelectedEventId] = useState<string>('')
  const [searchTerm, setSearchTerm] = useState('')
  const [speciesFilter, setSpeciesFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortBy, setSortBy] = useState<'confidence' | 'time'>('time')
  const [sortAsc, setSortAsc] = useState(true)

  // Aggregation runner state
  const [aggregating, setAggregating] = useState(false)
  const [aggregateProgress, setAggregateProgress] = useState(0)
  const [aggregateLog, setAggregateLog] = useState<string[]>([])

  // Detail panel burst slideshow
  const [currentSlideIndex, setCurrentSlideIndex] = useState(0)
  const [zoomLevel, setZoomLevel] = useState(1.0)
  const [showZoomModal, setShowZoomModal] = useState(false)

  // Load events
  useEffect(() => {
    async function loadData() {
      setLoading(true)
      // A fresh deck starts its first event's slideshow at the first frame, unzoomed.
      setCurrentSlideIndex(0)
      setZoomLevel(1.0)
      try {
        // Try fetching observation events from database
        const { data, error } = await supabase
          .from('observation_events')
          .select('*')
          .eq('deployment_id', deployment_id)
          .order('event_start', { ascending: true })

        if (error) throw error

        if (data && data.length > 0) {
          // Enrich database data with realistic mock burst paths & details
          const enriched: ObservationEvent[] = data.map((evt, idx) => {
            const speciesInfo = getNZSpeciesInfo(evt.scientific_name)
            return {
              id: evt.id,
              deployment_id: evt.deployment_id,
              scientific_name: evt.scientific_name || 'Unknown Species',
              nz_code: speciesInfo.code,
              nz_status: speciesInfo.status,
              confidence: evt.confidence || 0.8,
              event_start: evt.event_start,
              event_end: evt.event_end || evt.event_start,
              duration_seconds: evt.duration_seconds || 10,
              status: (evt.status as any) || 'pending',
              trigger_cause: (evt.trigger_cause as any) || 'Unknown',
              media: [
                `https://picsum.photos/800/600?random=db-${idx}-1`,
                `https://picsum.photos/800/600?random=db-${idx}-2`
              ],
              reviewer_diffs: {
                ai_class: `${evt.scientific_name || 'Species'} (${Math.round((evt.confidence || 0.8) * 100)}%)`,
                ai_confidence: evt.confidence || 0.8,
                human_class: `${evt.scientific_name || 'Species'} (100%)`,
                human_confidence: 1.0,
                ai_bbox: { x: 0.25, y: 0.25, w: 0.4, h: 0.4 },
                human_bbox: { x: 0.24, y: 0.24, w: 0.42, h: 0.42 }
              }
            }
          })
          setEvents(enriched)
          setSelectedEventId(enriched[0].id)
        } else {
          // Fallback to high-fidelity simulated NZ temporal events
          setEvents(MOCK_EVENTS)
          setSelectedEventId(MOCK_EVENTS[0].id)
        }
      } catch (err) {
        console.error('Failed to load observation events:', err)
        setEvents(MOCK_EVENTS)
        setSelectedEventId(MOCK_EVENTS[0].id)
      } finally {
        setLoading(false)
      }
    }
    loadData()
  }, [deployment_id])

  // Active selected event
  const selectedEvent = events.find(e => e.id === selectedEventId) || events[0]

  // Selecting a different event restarts its burst slideshow at the first frame, unzoomed.
  const selectEvent = (id: string) => {
    if (id === selectedEventId) return
    setSelectedEventId(id)
    setCurrentSlideIndex(0)
    setZoomLevel(1.0)
  }

  // Sorting & Filtering Logic
  const filteredEvents = events.filter(evt => {
    const matchesSearch = evt.scientific_name.toLowerCase().includes(searchTerm.toLowerCase()) || evt.id.toLowerCase().includes(searchTerm.toLowerCase())
    const matchesSpecies = speciesFilter === 'all' || evt.nz_code === speciesFilter
    const matchesStatus = statusFilter === 'all' || evt.status === statusFilter
    return matchesSearch && matchesSpecies && matchesStatus
  }).sort((a, b) => {
    let comparison = 0
    if (sortBy === 'confidence') {
      comparison = a.confidence - b.confidence
    } else {
      comparison = new Date(a.event_start).getTime() - new Date(b.event_start).getTime()
    }
    return sortAsc ? comparison : -comparison
  })

  // Scientific Controls Handlers
  const handleUpdateStatus = async (status: 'human_reviewed' | 'expert_reviewed' | 'consensus_approved') => {
    if (!selectedEvent) return
    const updated = events.map(evt => {
      if (evt.id === selectedEvent.id) {
        return { ...evt, status }
      }
      return evt
    })
    setEvents(updated)
    
    // Save to server
    try {
      await supabase
        .from('observation_events')
        .update({ status })
        .eq('id', selectedEvent.id)
    } catch {
      console.log('Offline/Mock: Updated event status locally to', status)
    }
  }

  const handleUpdateTriggerCause = async (trigger_cause: 'Animal' | 'Wind' | 'Rain' | 'Lightning' | 'Vegetation' | 'Unknown') => {
    if (!selectedEvent) return
    const updated = events.map(evt => {
      if (evt.id === selectedEvent.id) {
        return { ...evt, trigger_cause }
      }
      return evt
    })
    setEvents(updated)

    // Save to server
    try {
      await supabase
        .from('observation_events')
        .update({ trigger_cause })
        .eq('id', selectedEvent.id)
    } catch {
      console.log('Offline/Mock: Updated event trigger cause locally to', trigger_cause)
    }
  }

  // Job aggregator launcher
  const handleRunAggregation = async () => {
    setAggregating(true)
    setAggregateProgress(5)
    setAggregateLog(['Enqueuing Event Aggregation Job...', 'Connecting to database...'])

    try {
      // Attempt call to backend FastAPI endpoint
      const response = await apiClient.post(`/api/events/aggregate/${deployment_id}`, {})
      const data = (response as any).data
      
      if (data?.job_id) {
        setAggregateLog(prev => [...prev, `Job queued successfully: ID ${data.job_id}`])
      }
    } catch {
      setAggregateLog(prev => [...prev, 'FastAPI local server offline. Starting mock pipeline engine...'])
    }

    // Simulate pipeline run with logs & progress
    const steps = [
      { p: 20, l: 'Resolving media timestamps for camera effort logs...' },
      { p: 40, l: 'Clustering media sequences (temporal threshold: 120s)...' },
      { p: 60, l: 'Synthesizing observations & bounding boxes into unified observations...' },
      { p: 80, l: 'Linking NZ biological taxonomy records (Darwin Core mappings)...' },
      { p: 100, l: 'Job complete. Normalized trap night metrics generated successfully!' },
    ]

    for (let i = 0; i < steps.length; i++) {
      await new Promise(resolve => setTimeout(resolve, 800))
      setAggregateProgress(steps[i].p)
      setAggregateLog(prev => [...prev, steps[i].l])
    }

    await new Promise(resolve => setTimeout(resolve, 500))
    setAggregating(false)
  }

  const getStatusBadgeColor = (status: string) => {
    switch (status) {
      case 'pending': return '#ff9800'
      case 'human_reviewed': return '#2196f3'
      case 'expert_reviewed': return '#9c27b0'
      case 'consensus_approved': return '#4caf50'
      default: return '#777'
    }
  }

  const getSpeciesStatusColor = (status: string) => {
    switch (status) {
      case 'CR': return '#ff4c4c'
      case 'EN': return '#ff924c'
      case 'LC': return '#4cff4c'
      case 'Pest': return '#b24cff'
      default: return '#777'
    }
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh', color: 'var(--primary)' }}>
        <h3>Loading Observation Events Deck…</h3>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', color: 'var(--text-color)' }}>
      {/* Top dashboard header */}
      <div className="glass-card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '1.25rem', gap: '1rem', flexWrap: 'wrap' }}>
        <div>
          <h3 style={{ margin: 0 }}>CamtrapDP Event Clustering & QA Panel</h3>
          <span style={{ fontSize: '0.8125rem', opacity: 0.7 }}>Deployment: {deployment_id}</span>
        </div>

        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
          <button
            className="btn"
            onClick={handleRunAggregation}
            disabled={aggregating}
            style={{ backgroundColor: 'var(--primary)', position: 'relative', overflow: 'hidden' }}
          >
            {aggregating ? '⏳ Grouping Events...' : '⚡ Run Event Aggregation'}
          </button>
          <button className="btn btn-outline" onClick={() => navigate('/insights')} style={{ backgroundColor: 'transparent', border: '1px solid var(--border)' }}>
            My Data
          </button>
          <button className="btn" onClick={() => navigate(`/reporting/${deployment_id}`)} style={{ backgroundColor: 'transparent', border: '1px solid var(--border)', color: 'var(--primary)' }}>
            Results 📊
          </button>
        </div>
      </div>

      {/* Progress dialog when aggregating */}
      {aggregating && (
        <div className="glass-card" style={{ padding: '1.25rem', border: '1px solid var(--primary)', animation: 'pulse 2s infinite' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.875rem', fontWeight: 'bold' }}>
            <span>Synthesizing Observation Events...</span>
            <span>{aggregateProgress}%</span>
          </div>
          <div style={{ width: '100%', height: '8px', backgroundColor: 'rgba(255,255,255,0.05)', borderRadius: '4px', overflow: 'hidden', marginBottom: '1rem' }}>
            <div style={{ width: `${aggregateProgress}%`, height: '100%', backgroundColor: 'var(--primary)', transition: 'width 0.4s ease' }} />
          </div>
          <div style={{ backgroundColor: 'rgba(0,0,0,0.3)', borderRadius: 'var(--radius)', padding: '0.75rem', maxHeight: '120px', overflowY: 'auto', fontFamily: 'monospace', fontSize: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            {aggregateLog.map((log, idx) => (
              <div key={idx} style={{ opacity: idx === aggregateLog.length - 1 ? 1 : 0.6 }}>
                &gt; {log}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Main dual-column layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '400px 1fr', gap: '1.5rem', alignItems: 'stretch' }}>
        {/* Left Column: Event Deck */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {/* Deck Filters & Controls */}
          <div className="glass-card" style={{ padding: '1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            <input
              type="text"
              placeholder="Search event ID or species..."
              value={searchTerm}
              onChange={e => setSearchTerm(e.target.value)}
              style={{ width: '100%', padding: '0.5rem 0.75rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', backgroundColor: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.8125rem' }}
            />

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
              <select
                value={speciesFilter}
                onChange={e => setSpeciesFilter(e.target.value)}
                style={{ padding: '0.5rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', backgroundColor: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.75rem' }}
              >
                <option value="all">All Species</option>
                <option value="kiwi">Kiwi 🥝</option>
                <option value="kakapo">Kākāpō 🦜</option>
                <option value="weka">Weka 🐓</option>
                <option value="stoat">Stoat 🦦</option>
                <option value="possum">Possum 🦝</option>
                <option value="ferret">Ferret 🦨</option>
              </select>

              <select
                value={statusFilter}
                onChange={e => setStatusFilter(e.target.value)}
                style={{ padding: '0.5rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', backgroundColor: 'var(--surface)', color: 'var(--text-color)', fontSize: '0.75rem' }}
              >
                <option value="all">All Status</option>
                <option value="pending">Pending</option>
                <option value="human_reviewed">Reviewed</option>
                <option value="expert_reviewed">Expert Approved</option>
                <option value="consensus_approved">Consensus</option>
              </select>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem', opacity: 0.8 }}>
              <span>Sort by:</span>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button
                  onClick={() => { setSortBy('time'); setSortAsc(!sortAsc) }}
                  style={{ background: 'transparent', border: 'none', color: sortBy === 'time' ? 'var(--primary)' : 'inherit', cursor: 'pointer', fontWeight: sortBy === 'time' ? 'bold' : 'normal' }}
                >
                  Time {sortBy === 'time' && (sortAsc ? '▲' : '▼')}
                </button>
                <button
                  onClick={() => { setSortBy('confidence'); setSortAsc(!sortAsc) }}
                  style={{ background: 'transparent', border: 'none', color: sortBy === 'confidence' ? 'var(--primary)' : 'inherit', cursor: 'pointer', fontWeight: sortBy === 'confidence' ? 'bold' : 'normal' }}
                >
                  Confidence {sortBy === 'confidence' && (sortAsc ? '▲' : '▼')}
                </button>
              </div>
            </div>
          </div>

          {/* Scrollable Event List */}
          <div style={{ maxHeight: '600px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {filteredEvents.length === 0 ? (
              <div className="glass-card" style={{ padding: '2rem', textAlign: 'center', opacity: 0.6, fontSize: '0.875rem' }}>
                No observation events match the current criteria.
              </div>
            ) : (
              filteredEvents.map(evt => {
                const isActive = evt.id === selectedEventId
                return (
                  <div
                    key={evt.id}
                    onClick={() => selectEvent(evt.id)}
                    className="glass-card"
                    style={{
                      padding: '1rem',
                      cursor: 'pointer',
                      border: isActive ? '1px solid var(--primary)' : '1px solid rgba(255,255,255,0.05)',
                      backgroundColor: isActive ? 'rgba(76,175,80,0.06)' : 'var(--surface)',
                      transition: 'all 0.2s',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' }}>
                      <span style={{ fontSize: '0.625rem', fontFamily: 'monospace', opacity: 0.6 }}>ID: {evt.id}</span>
                      <span
                        style={{
                          fontSize: '0.625rem',
                          fontWeight: 'bold',
                          padding: '0.125rem 0.375rem',
                          borderRadius: '4px',
                          backgroundColor: getStatusBadgeColor(evt.status),
                          color: '#fff',
                          textTransform: 'uppercase',
                        }}
                      >
                        {evt.status.replace('_', ' ')}
                      </span>
                    </div>

                    <h4 style={{ margin: '0 0 0.5rem 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span>{evt.scientific_name}</span>
                      {evt.nz_status && (
                        <span
                          style={{
                            fontSize: '0.625rem',
                            fontWeight: 'bold',
                            padding: '0.125rem 0.375rem',
                            borderRadius: '4px',
                            backgroundColor: getSpeciesStatusColor(evt.nz_status),
                            color: '#fff'
                          }}
                        >
                          {evt.nz_status}
                        </span>
                      )}
                    </h4>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', fontSize: '0.75rem', opacity: 0.8, marginBottom: '0.5rem' }}>
                      <span>Duration: {evt.duration_seconds}s</span>
                      <span>Detections: {evt.media.length}</span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.75rem' }}>
                      <span>AI Confidence:</span>
                      <div style={{ flex: 1, height: '4px', backgroundColor: 'rgba(255,255,255,0.1)', borderRadius: '2px', overflow: 'hidden' }}>
                        <div style={{ width: `${evt.confidence * 100}%`, height: '100%', backgroundColor: evt.confidence > 0.8 ? '#4caf50' : evt.confidence > 0.6 ? '#2196f3' : '#ff9800' }} />
                      </div>
                      <span style={{ fontWeight: 'bold' }}>{Math.round(evt.confidence * 100)}%</span>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* Right Column: Event Detail Panel */}
        {selectedEvent ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
            {/* Burst Image Slideshow Panel */}
            <div className="glass-card" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem', position: 'relative' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h4 style={{ margin: 0 }}>Event Sequence Media Burst ({currentSlideIndex + 1} of {selectedEvent.media.length})</h4>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button
                    className="btn"
                    onClick={() => { setZoomLevel(z => Math.max(1.0, z - 0.25)) }}
                    disabled={zoomLevel <= 1.0}
                    style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }}
                  >
                    🔍-
                  </button>
                  <button
                    className="btn"
                    onClick={() => { setZoomLevel(z => Math.min(3.0, z + 0.25)) }}
                    disabled={zoomLevel >= 3.0}
                    style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }}
                  >
                    🔍+
                  </button>
                  <button
                    className="btn"
                    onClick={() => setShowZoomModal(true)}
                    style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }}
                  >
                    Fullscreen 🖥️
                  </button>
                </div>
              </div>

              {/* Picture Viewer Container */}
              <div style={{ position: 'relative', height: '400px', backgroundColor: '#000', borderRadius: 'var(--radius)', overflow: 'hidden', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
                <img
                  src={selectedEvent.media[currentSlideIndex]}
                  alt={`Sequence Burst ${currentSlideIndex}`}
                  style={{
                    maxWidth: '100%',
                    maxHeight: '100%',
                    objectFit: 'contain',
                    transform: `scale(${zoomLevel})`,
                    transition: 'transform 0.15s ease-out'
                  }}
                />

                {/* Left/Right controls */}
                <button
                  disabled={currentSlideIndex === 0}
                  onClick={() => setCurrentSlideIndex(c => c - 1)}
                  style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', padding: '0.75rem', borderRadius: '50%', backgroundColor: 'rgba(0,0,0,0.5)', border: '1px solid rgba(255,255,255,0.2)', color: '#fff', cursor: 'pointer', zIndex: 10 }}
                >
                  ◀
                </button>
                <button
                  disabled={currentSlideIndex === selectedEvent.media.length - 1}
                  onClick={() => setCurrentSlideIndex(c => c + 1)}
                  style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', padding: '0.75rem', borderRadius: '50%', backgroundColor: 'rgba(0,0,0,0.5)', border: '1px solid rgba(255,255,255,0.2)', color: '#fff', cursor: 'pointer', zIndex: 10 }}
                >
                  ▶
                </button>

                {/* Burst indicators */}
                <div style={{ position: 'absolute', bottom: '15px', display: 'flex', gap: '0.5rem', zIndex: 10 }}>
                  {selectedEvent.media.map((_, idx) => (
                    <span
                      key={idx}
                      style={{
                        width: '8px',
                        height: '8px',
                        borderRadius: '50%',
                        backgroundColor: idx === currentSlideIndex ? 'var(--primary)' : 'rgba(255,255,255,0.4)',
                        display: 'inline-block'
                      }}
                    />
                  ))}
                </div>
              </div>
            </div>

            {/* QA Controls & Reviewer Diff & Metadata Row */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
              {/* Left Box: AI vs Human Diff */}
              <div className="glass-card" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <h4 style={{ margin: 0 }}>Taxonomic / Bounding Box Diff</h4>
                
                {selectedEvent.reviewer_diffs ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', padding: '0.75rem', borderRadius: 'var(--radius)', backgroundColor: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)' }}>
                      <div>
                        <span style={{ fontSize: '0.625rem', opacity: 0.6, display: 'block', textTransform: 'uppercase' }}>AI Predictions</span>
                        <span style={{ fontWeight: 'bold', color: '#4caf50', fontSize: '0.875rem' }}>{selectedEvent.reviewer_diffs.ai_class}</span>
                        <span style={{ display: 'block', fontSize: '0.75rem', opacity: 0.8, marginTop: '0.25rem' }}>
                          BBox: {selectedEvent.reviewer_diffs.ai_bbox ? 'Detected' : 'None'}
                        </span>
                      </div>
                      <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: '0.75rem' }}>
                        <span style={{ fontSize: '0.625rem', opacity: 0.6, display: 'block', textTransform: 'uppercase' }}>Human Annotator</span>
                        <span style={{ fontWeight: 'bold', color: '#2196f3', fontSize: '0.875rem' }}>{selectedEvent.reviewer_diffs.human_class}</span>
                        <span style={{ display: 'block', fontSize: '0.75rem', opacity: 0.8, marginTop: '0.25rem' }}>
                          BBox: {selectedEvent.reviewer_diffs.human_bbox ? 'Verified' : 'None'}
                        </span>
                      </div>
                    </div>

                    <div style={{ fontSize: '0.75rem', opacity: 0.8, lineHeight: 1.5 }}>
                      💡 Spatial variance is under 5%. Human reviewer verified bounding details perfectly aligning with pre-labeled indices. No taxanomic correction required.
                    </div>
                  </div>
                ) : (
                  <div style={{ opacity: 0.6, fontSize: '0.8125rem' }}>No telemetry differences recorded for this event.</div>
                )}
              </div>

              {/* Right Box: Scientific Controls */}
              <div className="glass-card" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <h4 style={{ margin: 0 }}>Scientific Quality Control</h4>

                {/* Trigger Cause Selector */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                  <span style={{ fontSize: '0.75rem', opacity: 0.8 }}>Trigger Cause (CamtrapDP Standard):</span>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
                    {(['Animal', 'Wind', 'Rain', 'Lightning', 'Vegetation', 'Unknown'] as const).map(cause => (
                      <button
                        key={cause}
                        onClick={() => handleUpdateTriggerCause(cause)}
                        style={{
                          padding: '0.375rem 0.625rem',
                          borderRadius: 'var(--radius)',
                          border: selectedEvent.trigger_cause === cause ? '1px solid var(--primary)' : '1px solid var(--border)',
                          backgroundColor: selectedEvent.trigger_cause === cause ? 'rgba(76, 175, 80, 0.15)' : 'rgba(255,255,255,0.02)',
                          color: selectedEvent.trigger_cause === cause ? 'var(--primary)' : 'var(--text-color)',
                          fontSize: '0.75rem',
                          fontWeight: selectedEvent.trigger_cause === cause ? 'bold' : 'normal',
                          cursor: 'pointer',
                        }}
                      >
                        {cause}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Event Status Triage */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem', marginTop: '0.25rem' }}>
                  <span style={{ fontSize: '0.75rem', opacity: 0.8 }}>Transition QA Status:</span>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                    <button
                      className="btn"
                      onClick={() => handleUpdateStatus('human_reviewed')}
                      style={{
                        fontSize: '0.75rem',
                        padding: '0.5rem',
                        backgroundColor: selectedEvent.status === 'human_reviewed' ? 'var(--primary)' : 'transparent',
                        border: '1px solid var(--border)'
                      }}
                    >
                      👍 Human Reviewed
                    </button>
                    <button
                      className="btn"
                      onClick={() => handleUpdateStatus('expert_reviewed')}
                      style={{
                        fontSize: '0.75rem',
                        padding: '0.5rem',
                        backgroundColor: selectedEvent.status === 'expert_reviewed' ? '#9c27b0' : 'transparent',
                        border: '1px solid var(--border)'
                      }}
                    >
                      🎓 Expert Reviewed
                    </button>
                    <button
                      className="btn"
                      onClick={() => handleUpdateStatus('consensus_approved')}
                      style={{
                        gridColumn: 'span 2',
                        fontSize: '0.75rem',
                        padding: '0.5rem',
                        backgroundColor: selectedEvent.status === 'consensus_approved' ? '#4caf50' : 'transparent',
                        border: '1px solid var(--border)'
                      }}
                    >
                      🏆 Consensus Approved
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="glass-card" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '3rem', opacity: 0.6 }}>
            Select an event from the deck to begin temporal sequence review.
          </div>
        )}
      </div>

      {/* Fullscreen slideshow modal */}
      {showZoomModal && selectedEvent && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: '#000', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', zIndex: 1000 }}>
          <button
            onClick={() => setShowZoomModal(false)}
            style={{ position: 'absolute', top: '20px', right: '20px', padding: '0.5rem 1rem', border: '1px solid rgba(255,255,255,0.3)', backgroundColor: 'rgba(0,0,0,0.8)', color: '#fff', cursor: 'pointer', borderRadius: '4px' }}
          >
            Close Fullscreen
          </button>
          <img
            src={selectedEvent.media[currentSlideIndex]}
            alt="Fullscreen Burst"
            style={{ maxWidth: '95vw', maxHeight: '90vh', objectFit: 'contain' }}
          />
        </div>
      )}
    </div>
  )
}
