import { useState } from 'react'

const sections = [
  { id: 'whats-in-box',   label: "📦 What's in the Box",   emoji: '📦' },
  { id: 'camera-setup',   label: '📷 Camera Setup',         emoji: '📷' },
  { id: 'mobile-app',     label: '📱 Mobile App',           emoji: '📱' },
  { id: 'deployment',     label: '🚀 Start Monitoring',     emoji: '🚀' },
  { id: 'image-analysis', label: '🔬 Image Annotation',     emoji: '🔬' },
  { id: 'maintenance',    label: '🔧 Maintenance',           emoji: '🔧' },
]

function SidebarLink({ label, active, onClick }: { id: string; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'block', width: '100%', textAlign: 'left',
        padding: '0.6rem 1rem', border: 'none', borderRadius: 'var(--radius)',
        background: active ? 'var(--primary)' : 'transparent',
        color: active ? '#fff' : 'var(--text-color)',
        cursor: 'pointer', fontSize: '0.875rem', fontWeight: active ? 600 : 400,
        transition: 'background 0.15s',
      }}
    >
      {label}
    </button>
  )
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} style={{ marginBottom: '3rem' }}>
      <h2 style={{ fontSize: '1.5rem', marginBottom: '1.25rem', paddingBottom: '0.5rem', borderBottom: '2px solid var(--primary)' }}>
        {title}
      </h2>
      {children}
    </section>
  )
}

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem', alignItems: 'flex-start' }}>
      <span style={{
        minWidth: '2rem', height: '2rem', borderRadius: '50%',
        background: 'var(--primary)', color: '#fff',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: '0.8rem', fontWeight: 700, flexShrink: 0,
      }}>{n}</span>
      <div style={{ paddingTop: '0.2rem', lineHeight: 1.6 }}>{children}</div>
    </div>
  )
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      padding: '0.75rem 1rem', borderLeft: '3px solid var(--primary)',
      background: 'var(--surface)', borderRadius: '0 var(--radius) var(--radius) 0',
      fontSize: '0.875rem', margin: '1rem 0', lineHeight: 1.6,
    }}>{children}</div>
  )
}

function H3({ children }: { children: React.ReactNode }) {
  return <h3 style={{ fontSize: '1.1rem', marginTop: '1.5rem', marginBottom: '0.75rem' }}>{children}</h3>
}

function Ul({ items }: { items: string[] }) {
  return (
    <ul style={{ paddingLeft: '1.25rem', margin: '0.5rem 0 1rem', display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
      {items.map((item, i) => <li key={i} style={{ lineHeight: 1.6 }}>{item}</li>)}
    </ul>
  )
}

export function ResourcesPage() {
  const [active, setActive] = useState('whats-in-box')

  const scrollTo = (id: string) => {
    setActive(id)
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div>
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>📚 Resources & How-To Guides</h1>
        <p style={{ color: 'var(--text-muted)', maxWidth: '640px', lineHeight: 1.6 }}>
          Everything you need to set up, monitor, and analyse data from your Wildlife Watcher camera.
          From unboxing to AI-powered species identification — all in one place.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: '2rem', alignItems: 'start' }}>

        {/* Sidebar */}
        <aside style={{
          position: 'sticky', top: '1rem',
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', padding: '0.75rem',
          display: 'flex', flexDirection: 'column', gap: '0.25rem',
        }}>
          <div style={{ fontSize: '0.7rem', fontWeight: 700, color: 'var(--text-muted)', padding: '0.25rem 1rem 0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Contents</div>
          {sections.map(s => (
            <SidebarLink key={s.id} id={s.id} label={s.label} active={active === s.id} onClick={() => scrollTo(s.id)} />
          ))}
        </aside>

        {/* Content */}
        <div style={{ minWidth: 0 }}>

          <Section id="whats-in-box" title="📦 What's in the Box">
            <p style={{ lineHeight: 1.7, marginBottom: '1rem' }}>
              Your Wildlife Watcher package includes everything needed to get started monitoring wildlife straight away.
            </p>
            <H3>Included in the box</H3>
            <Ul items={['Wildlife Watcher Smart AI camera unit', 'Stand and mounting bracket']} />
            <H3>What you will need</H3>
            <Ul items={[
              '4x high-quality AA batteries (non-rechargeable premium alkaline or lithium are recommended).',
              'A FAT32-formatted microSD card, 32–64 GB (Class 10 or higher).'
            ]} />
          </Section>

          <Section id="camera-setup" title="📷 Camera Setup">
            <H3>1. Powering the camera</H3>
            <p style={{ lineHeight: 1.7, marginBottom: '0.75rem' }}>
              The Wildlife Watcher currently only supports AA battery power. We have not extensively tested rechargeable batteries, though they should work. In the future, we plan to add solar panels and a proprietary battery pack, but currently, only standard AA batteries are supported.
            </p>

            <H3>2. Inserting the SD card</H3>
            <Step n={1}>Open the cap at the bottom where the batteries are located.</Step>
            <Step n={2}>The microSD slot is in the same place, located on the top part of the enclosure.</Step>
            <Step n={3}>Insert the microSD card with the contacts facing opposite from the batteries.</Step>
            <Step n={4}>Close the cap securely.</Step>

            <H3>3. Mounting the camera</H3>
            <p style={{ lineHeight: 1.7, marginBottom: '0.75rem' }}>Choose your mounting strategy based on the target species and terrain:</p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
              {[
                { title: 'Ground-facing (downward view)', steps: ['Use the integrated adjustable stand to place the device securely facing the ground', 'Adjust the angle to cover the desired field of view', 'Fix the wildlife platform in the camera view', 'Set up any bait or attractants'] },
                { title: 'Tree / post mount', steps: ['Use straps and mounting clasps to attach to a tree trunk or post trunk', 'Adjust the angle to capture the target zone', 'Ensure the camera is level and stable', 'Use the dedicated holes to screw the device securely in place'] },
              ].map(m => (
                <div key={m.title} style={{ padding: '1rem', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
                  <strong style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.9rem' }}>{m.title}</strong>
                  <ul style={{ paddingLeft: '1rem', margin: 0, fontSize: '0.85rem', lineHeight: 1.7 }}>
                    {m.steps.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              ))}
            </div>
          </Section>

          <Section id="mobile-app" title="📱 Mobile App">
            <p style={{ lineHeight: 1.7, marginBottom: '1rem' }}>
              The Wildlife Watcher companion app is the only way to configure, control, and monitor your cameras.
              It connects via Bluetooth and is available for iOS and Android.
            </p>

            <H3>Installing the app</H3>
            <Step n={1}>Open the App Store (iOS) or Google Play (Android) on your phone.</Step>
            <Step n={2}>Search for <strong>Wildlife Watcher</strong> and install the app.</Step>
            <Step n={3}>Open the app and create an account or log in.</Step>

            <H3>Creating an account</H3>
            <Ul items={[
              'Tap "Create Account" on the login screen.',
              'Enter your email address and choose a strong password.',
              'Submit — you will be automatically logged in and taken to the app\'s home screen with instructions on how to use it.',
            ]} />

            <H3>Before your first monitoring session</H3>
            <Ul items={[
              'Check the batteries — ensure fresh premium alkaline or lithium AA batteries are installed.',
              'Check SD card available space — a FAT32-formatted microSD card, 32–64 GB (Class 10 or higher), is recommended.',
              'Test the camera preview to verify field of view.',
            ]} />
            <Note>💡 Always test the camera before heading into the field to avoid wasted trips.</Note>
          </Section>

          <Section id="deployment" title="🚀 Start Monitoring">
            <p style={{ lineHeight: 1.7, marginBottom: '1rem' }}>
              Monitoring is a camera recording session linked to a specific project and location.
              All images captured during the session are stored locally and can be reviewed in the app.
            </p>

            <H3>Starting monitoring</H3>
            <Step n={1}>
              <strong>Preparation:</strong> Ensure the camera has batteries and a microSD card installed. Mount the camera in the field.
            </Step>
            <Step n={2}>
              <strong>Connect:</strong> Open the Mobile App. Tap "Search for devices" in the mobile app and physically press the button at the bottom of the device to connect to it.
            </Step>
            <Step n={3}>
              <strong>Configure:</strong> The app displays the configuration screen. The mandatory steps are to associate the session with a Project and optionally add monitoring notes.
            </Step>
            <Step n={4}>
              <strong>Advanced Settings (Optional):</strong> Access the expandable section to set specific location data, camera height, preview the field of view, perform a quick motion detection test, or update firmware (though firmware should ideally be done before the field).
            </Step>
            <Step n={5}>
              <strong>Start:</strong> Click Start Monitoring.
            </Step>
            <Step n={6}>
              <strong>Visualize:</strong> The app transitions to a Live Stream of the active monitoring. The display indicates motion detections and shows how long the camera has been running for.
            </Step>
            <Step n={7}>
              <strong>Disconnect:</strong> You may disconnect the app; the camera will continue monitoring independently.
            </Step>

            <H3>Ending monitoring</H3>
            <Step n={1}>
              <strong>Connect:</strong> Tap "Search for devices" in the mobile app and physically press the button at the bottom of the device to connect to it. The app connects and goes directly to the live stream view.
            </Step>
            <Step n={2}>
              <strong>Select Stop:</strong> Once connected, you can stop the monitoring. Below the live stream, tap the Stop Monitoring button.
            </Step>
            <Step n={3}>
              <strong>Confirm:</strong> Add any final notes regarding the monitoring session, then click Confirm Stop.
            </Step>
            <Step n={4}>
              <strong>Data:</strong> All information is stored securely on both the centralized database and the local device.
            </Step>
          </Section>

          <Section id="image-analysis" title="🔬 Wildlife Watcher Image Annotation">
            <p style={{ lineHeight: 1.7, marginBottom: '1rem' }}>
              Instead of external software, users analyze images directly through the Wildlife Watcher Website.
            </p>

            <H3>Step-by-step workflow</H3>
            <Step n={1}>Copy the media folder from the SD card to your computer.</Step>
            <Step n={2}>Log in to the Wildlife Watcher web toolkit.</Step>
            <Step n={3}>Drag and drop the entire media folder into the interface. The website automatically understands which photos they are and their source monitoring session.</Step>
            <Step n={4}>The user reviews and annotates the images.</Step>
            <Step n={5}>Once completed, you can visualize all animal observations and data distributions directly on the platform.</Step>

            <Note>
              💡 Images recorded by Wildlife Watchers follow the CamTrapDP standards and remain fully compatible with other major camera trap software pipelines.
            </Note>
          </Section>

          <Section id="maintenance" title="🔧 Maintenance & Troubleshooting">
            <H3>Regular maintenance</H3>
            <Ul items={[
              'Clean the lens and sensor area regularly with a soft, dry cloth.',
              'Format the SD card after each session to free up space.',
              'Firmware updates should be performed before you head into the field.',
              'Inspect mounting straps and clasps for wear after extended outdoor use.',
            ]} />

            <H3>Troubleshooting common issues</H3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {[
                {
                  issue: 'Camera not turning on',
                  fix: 'Ensure that fresh, premium alkaline or lithium AA batteries are fully and properly installed. Check battery terminals for cleanliness.',
                },
                {
                  issue: 'App not connecting via Bluetooth',
                  fix: 'Restart the mobile app. Make sure Bluetooth is enabled on your phone and you physically tap the button at the bottom of the device to activate Bluetooth advertising.',
                },
                {
                  issue: 'Blurred or dark images',
                  fix: 'Clean the lens with a soft cloth. Ensure the camera is mounted securely without vibration. Check that no branches or vegetation are in the field of view.',
                },
                {
                  issue: 'SD card not detected',
                  fix: 'Remove and reinsert the microSD card. Ensure it is formatted as FAT32 and the contacts are facing opposite from the batteries. Try formatting the card via the app settings.',
                },
              ].map(({ issue, fix }) => (
                <div key={issue} style={{ padding: '1rem', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
                  <strong style={{ color: 'var(--primary)', display: 'block', marginBottom: '0.35rem' }}>⚠️ {issue}</strong>
                  <span style={{ fontSize: '0.875rem', lineHeight: 1.6 }}>{fix}</span>
                </div>
              ))}
            </div>

            <H3>Still need help?</H3>
            <p style={{ lineHeight: 1.7 }}>
              Check the <a href="/faq" style={{ color: 'var(--primary)' }}>FAQ</a> for common questions,
              browse our <a href="/guides" style={{ color: 'var(--primary)' }}>advanced guides</a>,
              or contact us at <a href="mailto:contact@wildlife.ai" style={{ color: 'var(--primary)' }}>contact@wildlife.ai</a>.
              Our support team is available Monday to Friday, New Zealand time (UTC+12, UTC+13 in summer).
            </p>
          </Section>

        </div>
      </div>
    </div>
  )
}
