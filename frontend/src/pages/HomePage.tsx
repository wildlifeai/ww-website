import { AnalyseImages } from '../components/toolkit/AnalyseImages'

export function HomePage() {
  return (
    <div>
      <div style={{ textAlign: 'center', maxWidth: '800px', margin: '0 auto', padding: '0 0 3rem 0' }}>
        <h1 style={{ fontSize: '3rem', color: 'var(--primary)', marginBottom: '1rem' }}>Monitor Wildlife Globally</h1>
        <p style={{ fontSize: '1.25rem', opacity: 0.8 }}>
          Deploy robust camera trap models, monitor real-time telemetry via LoRaWAN, and generate firmware config—all in one place.
        </p>
      </div>

      <AnalyseImages />
    </div>
  )
}
