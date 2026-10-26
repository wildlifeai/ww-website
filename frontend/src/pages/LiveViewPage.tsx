import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { PageHelp } from '../components/common/PageHelp'
import { MotionLiveView } from '../components/toolkit/MotionLiveView'

/** /live-view: a WW500's live picture and motion grid over its USB cable (#400), lazy-loaded. */
export function LiveViewPage() {
  return (
    <div>
      <Link to="/toolkit" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.8125rem', color: 'var(--text-muted)', textDecoration: 'none', marginBottom: '0.75rem' }}>
        <ArrowLeft size={14} aria-hidden="true" /> Toolkit
      </Link>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem', marginBottom: '1.5rem' }}>
        <h2 style={{ margin: 0 }}>Motion live view</h2>
        <PageHelp label="About the motion live view" guide="/guides/tuning-motion-detection" guideLabel="Read the motion detection guide">
          What a camera on its USB cable sees, where it detects motion, and whether that would wake it.
          Settings you change here are saved on the camera at once.
        </PageHelp>
      </div>
      <MotionLiveView />
    </div>
  )
}
