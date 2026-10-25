import { UploadFlow } from '../components/upload/UploadFlow'
import { PageHelp } from '../components/common/PageHelp'

/**
 * /upload-data, the single upload surface. The header Upload button, Home and
 * the three-step guide all link here. The flow itself (drop zone, deployment
 * resolution, triage, CamtrapDP import) lives in UploadFlow; the "?" beside the
 * heading summarises the page and links the guide. The progress dock and the
 * redirect to Media take over once an upload starts.
 */
export function UploadDataPage() {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem', margin: '0 0 1.25rem' }}>
        <h2 style={{ margin: 0, fontSize: '1.5rem' }}>Upload data</h2>
        <PageHelp label="About uploading" guide="/guides/uploading-photos" guideLabel="Read the upload guide">
          Drop the MEDIA folder from a Wildlife Watcher SD card, or a CamtrapDP ZIP. Each photo goes
          to the deployment stamped in it, and the AI labels the photos after upload.
        </PageHelp>
      </div>
      <UploadFlow />
    </div>
  )
}
