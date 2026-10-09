/**
 * FaqPage — FAQ for prospects and existing users (/faq).
 *
 * The top half answers the questions someone *considering* Wildlife Watcher
 * asks: what it is, how it differs from other camera traps, how the AI works,
 * and how to get one. The "Already using Wildlife Watcher?" half absorbs the
 * former Support page (/support redirects here): troubleshooting, account,
 * and export questions, grouped by topic.
 */
import { useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { FaqItem } from '../components/common/FaqItem'
import { usePageMeta } from '../lib/pageMeta'

export function FaqPage() {
  usePageMeta({ title: 'FAQ', description: 'What makes the Wildlife Watcher different from other camera traps, how its AI works, battery life, SD cards, exports, and how to get a device.' })
  const { hash } = useLocation()

  // React Router doesn't scroll to hashes, and a <details> target stays
  // collapsed — open and scroll to the linked question (e.g. /faq#buy).
  useEffect(() => {
    if (!hash) return
    const el = document.getElementById(hash.slice(1))
    if (el) {
      el.setAttribute('open', '')
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [hash])

  return (
    <div className="container" style={{ maxWidth: '800px', margin: '0 auto', padding: '2rem 0', lineHeight: '1.6' }}>
      <h1 style={{ marginBottom: '0.75rem' }}>Frequently Asked Questions</h1>

      <p style={{ opacity: 0.8 }}>
        The Wildlife Watcher is a compact camera designed to monitor invertebrates and small
        animals that traditional camera traps miss. It uses on-device AI to identify target
        species in the field, and an open-source website for easy data analysis and reporting.
      </p>

      {/* Project status — set expectations before anyone reaches for a credit card */}
      <div style={{
        padding: '0.875rem 1.125rem', margin: '1.25rem 0 2rem',
        borderLeft: '3px solid var(--primary)', background: 'var(--surface)',
        borderRadius: '0 var(--radius) var(--radius) 0', fontSize: '0.9375rem',
      }}>
        <strong>Project status:</strong> Wildlife Watcher is currently in a prototype phase.
        Devices are available to Beta testers — see{' '}
        <a href="#buy" style={{ color: 'var(--primary)' }}>Where can I buy one?</a>
      </div>

      <FaqItem q="What makes it different from other camera traps?">
        This compact camera is specifically designed for collecting data on small invertebrates
        and cold-blooded animals such as skinks and frogs — species that conventional
        thermal-trigger camera traps miss. The full workflow is open source, from the hardware
        designs to the web analysis platform.
      </FaqItem>

      <FaqItem q="How are the images and data processed?">
        Images are captured on a microSD card. The camera can run local AI models to flag target
        species in real time, and you can later upload the media folder to our{' '}
        <Link to="/" style={{ color: 'var(--primary)' }}>web toolkit</Link> for advanced
        analysis, annotation, and data visualisation.
      </FaqItem>

      <FaqItem q="How does the AI component work?">
        Wildlife Watcher uses AI in three complementary places, so you can process images
        efficiently without draining your camera's battery:
        <ul style={{ paddingLeft: '1.25rem', margin: '0.75rem 0' }}>
          <li style={{ marginBottom: '0.5rem' }}>
            <strong>Camera AI (on the device):</strong> the camera runs a lightweight, highly
            specialised model — your project's <em>Species Brain</em> — that flags your target
            species right in the field, with no internet. Its result is saved inside each photo
            and shows on the website as a <strong>📟 Camera AI</strong> label.
          </li>
          <li style={{ marginBottom: '0.5rem' }}>
            <strong>Cloud AI (on the website):</strong> when you upload your media folder, a much
            more powerful model identifies a wide variety of animals and marks empty photos as
            blank. It acts as an assistant — human confirmation is still required during review to
            ensure data accuracy.
          </li>
          <li>
            <strong>Wildlife Brain (on the website):</strong> groups visually similar animals so
            you can review large datasets quickly — clusters, "find similar", and a map. It
            organises your data rather than naming species. See the{' '}
            <Link to="/guides/wildlife-brain" style={{ color: 'var(--primary)' }}>Wildlife Brain guide</Link>.
          </li>
        </ul>
      </FaqItem>

      <FaqItem q="What do the 📟 Camera AI, ☁ Cloud AI and 👤 labels on a photo mean?">
        Each result on a photo carries a small label showing where it came from:{' '}
        <strong>📟 Camera AI</strong> — your camera decided it in the field;{' '}
        <strong>☁ Cloud AI</strong> — the website's model decided it on upload;{' '}
        <strong>👤 Reviewed</strong> — a person has checked it. A single photo can show more than
        one. When they disagree, a person decides: human review always wins, then Cloud AI, then
        Camera AI. See{' '}
        <Link to="/guides/camera-ai-and-cloud-ai" style={{ color: 'var(--primary)' }}>How the AIs work together</Link>.
      </FaqItem>

      <FaqItem q="What is the camera's focal length, and can I change it?">
        The main camera has a fixed <strong>3.04 mm</strong> lens (f/2.0) with a ~62° field of
        view — chosen for close-range monitoring: positioned 30–50 cm above a platform, it
        captures the whole platform at around 5–9 pixels per millimetre, enough detail to
        identify small invertebrates and lizards. The focal length itself can't be swapped
        (there's no lens mount), but the <strong>focus is adjustable</strong>: the threaded lens
        barrel can be rotated to focus at your platform distance instead of the factory setting
        (~1 m–infinity). Use the mobile app's live preview to fine-tune sharpness when you set up.
      </FaqItem>

      <FaqItem q="How long does the battery last?">
        Currently about <strong>one month</strong> in the field, on 4× double-A batteries.
      </FaqItem>

      <FaqItem q="Does it have a mobile app?">
        Yes — the app is available on the iOS App Store and Google Play. It connects to the
        camera via Bluetooth and is required to configure, control, and start your monitoring
        sessions. Installing it is step three of{' '}
        <Link to="/resources#mobile-app" style={{ color: 'var(--primary)' }}>Get started</Link>.
      </FaqItem>

      <FaqItem q="Can it monitor rats and other pests?">
        Yes — you can use rat-detection models and other applications, but the camera is more
        specifically designed to capture images of invertebrates and cold-blooded creatures
        such as reptiles.
      </FaqItem>

      <FaqItem q="What SD card should I use?">
        We recommend a <strong>FAT32-formatted</strong> microSD card, <strong>32–64 GB</strong>{' '}
        (Class 10 or higher). The mobile app includes a "Format SD Card" option (accessible from
        the Engineer Console) if you need to reformat in the field.
      </FaqItem>

      <FaqItem q="Is it free to use?">
        Yes. Wildlife.ai is a charity whose mission is to accelerate conservation using AI, open
        source, and community-driven tools, and the platform is free for conservation projects.
        As more projects join the movement, we may introduce a paid tier for users with
        large-scale requirements (thousands of photos, custom ML algorithms, heavy cloud usage),
        but we are committed to always offering affordable or free services for most conservation
        projects.
      </FaqItem>

      <FaqItem q="Where can I buy one?" id="buy">
        We are currently in a prototype phase. If you would like to get a device as a Beta
        tester, reach out to the team at{' '}
        <a href="mailto:contact@wildlife.ai" style={{ color: 'var(--primary)' }}>contact@wildlife.ai</a>{' '}
        or learn more at{' '}
        <a href="https://wildlife.ai/wildlife-watcher" target="_blank" rel="noreferrer" style={{ color: 'var(--primary)' }}>wildlife.ai</a>.
      </FaqItem>

      {/* ── Existing users (formerly the Support page) ───────────────────── */}
      <h2 style={{ marginTop: '2.5rem', marginBottom: '0.75rem' }}>Already using Wildlife Watcher?</h2>
      <p>
        Quick answers about the camera, the app, your data and your account. Anything that is a
        procedure, from unboxing to your first monitoring session to troubleshooting, lives under{' '}
        <Link to="/resources" style={{ color: 'var(--primary)' }}>Get started</Link>, once, with
        the steps in order.
      </p>

      <h3 style={{ marginTop: '2rem', marginBottom: '0.75rem', color: 'var(--primary)' }}>Getting Started</h3>

      <FaqItem q="How do I start a monitoring session?">
        From the mobile app, connected to the camera over Bluetooth: pick the project, optionally
        the location and settings, and tap Start Monitoring. The camera then runs on its own until
        you connect again and stop it. The eleven steps, start to stop, are under{' '}
        <Link to="/resources#deployment" style={{ color: 'var(--primary)' }}>Get started: Start Monitoring</Link>.
      </FaqItem>

      <FaqItem q="What is an organisation?">
        Every user currently belongs to a single default organisation. As we expand the platform, we will roll out a full
        organisations feature where users can create their own organisation, manage members, and share AI models and data across
        projects within that organisation.
      </FaqItem>

      <h3 style={{ marginTop: '2rem', marginBottom: '0.75rem', color: 'var(--primary)' }}>Camera & Hardware</h3>

      <FaqItem q="What does the button on the camera do?">
        A short press of the button at the bottom of the device turns on Bluetooth so the mobile
        app can connect — the blue light flashes while the camera is discoverable. Holding the
        button for 10 seconds or more puts the camera into firmware-update (recovery) mode.
      </FaqItem>

      <FaqItem q="Can I use Wildlife Watcher photos with other software?">
        Yes. The Wildlife Watcher is built for an end-to-end approach — all information collected in the field
        (GPS coordinates, timestamps, AI detections) is embedded with the images to save time during data analysis
        and preparation. We also provide APIs so you can retrieve your field data programmatically and use it with
        any analysis software you prefer.
      </FaqItem>

      <FaqItem q="Does the camera support LoRaWAN?">
        LoRaWAN support is rolling out in early access. Cameras send tiny status heartbeats (battery,
        trigger counts) and instant detection alerts over LoRaWAN — never photos, which stay on the SD card
        until you collect them or sync with the mobile app. See the{' '}
        <Link to="/guides/lorawan-in-new-zealand" style={{ color: 'var(--primary)' }}>LoRaWAN in New Zealand guide</Link>{' '}
        for the three ways to get connected.
      </FaqItem>

      <FaqItem q="How do I update the AI model on my camera?">
        You can prepare an SD card with the latest software and AI model via the "Prepare SD Card" option on the website.
        Download the package, extract it to the root of your SD card, and insert it into your camera before powering on.
        Alternatively, you can transfer models via Bluetooth from the mobile app.
      </FaqItem>

      <h3 style={{ marginTop: '2rem', marginBottom: '0.75rem', color: 'var(--primary)' }}>Mobile App</h3>

      <FaqItem q="Does the app work offline?">
        Yes. The mobile app works without an internet connection using the data it has from the last time it was online.
        You can view projects, monitoring sessions, and cached data while offline. Any new data or changes made offline will be
        automatically uploaded to the cloud when an internet connection becomes available.
      </FaqItem>

      <FaqItem q="The app can't connect to my camera via Bluetooth. What should I do?">
        Nine times out of ten the camera is not advertising: press the button at the bottom and
        check the blue light flashes before you scan. The rest of the checklist, in order, is under{' '}
        <Link to="/resources#maintenance" style={{ color: 'var(--primary)' }}>Get started: Maintenance &amp; Troubleshooting</Link>.
      </FaqItem>

      <h3 style={{ marginTop: '2rem', marginBottom: '0.75rem', color: 'var(--primary)' }}>Data & Export</h3>

      <FaqItem q="What data formats can I export?">
        You can export your data as <strong>CSV files</strong> or in <strong>CamtrapDP format</strong> — a standardised
        camera trap data package used by biodiversity databases worldwide. CamtrapDP exports include monitoring sessions,
        media records, and observations in a single ZIP file.
      </FaqItem>

      <FaqItem q="Can I upload data from other cameras?">
        The site currently accepts external data if it is labelled and in <a href="https://camtrap-dp.tdwg.org/" target="_blank" rel="noreferrer" style={{ color: 'var(--primary)' }}>CamtrapDP format</a>.
        We are actively building support for uploading unlabelled footage from other cameras — users will be able to
        upload their images and manually add the missing metadata (location, species, timestamps) through the web interface.
      </FaqItem>

      <h3 style={{ marginTop: '2rem', marginBottom: '0.75rem', color: 'var(--primary)' }}>Account</h3>

      <FaqItem q="How do I reset my password?">
        On the login screen, tap "Forgot Password" and enter your email address. You will receive a password reset link
        via email. Follow the link to set a new password.
      </FaqItem>

      <FaqItem q="How do I delete my account?">
        You can request account deletion at any time by filling out our{' '}
        <a href="https://forms.gle/aasjsW5N26giYDk96" target="_blank" rel="noreferrer" style={{ color: 'var(--primary)' }}>Account Deletion Request Form</a>{' '}
        or by emailing <a href="mailto:contact@wildlife.ai" style={{ color: 'var(--primary)' }}>contact@wildlife.ai</a>.
        Your request will be processed within 7–14 business days. Personal data is deleted; anonymised wildlife
        observation data is retained for research purposes.
        See our <Link to="/privacy#account-deletion" style={{ color: 'var(--primary)' }}>Privacy Policy</Link> for full details.
      </FaqItem>

      {/* ── Contact ───────────────────────────────────────────────────────── */}
      <h2 style={{ marginTop: '2.5rem', marginBottom: '1rem' }}>Still Need Help?</h2>
      <p>If you cannot find the answer to your question, or need further technical assistance, please reach out to our team:</p>

      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Email:</strong> <a href="mailto:contact@wildlife.ai" style={{ color: 'var(--primary)' }}>contact@wildlife.ai</a></li>
        <li><strong>Get started:</strong> <Link to="/resources" style={{ color: 'var(--primary)' }}>the setup and troubleshooting steps</Link>, in order.</li>
      </ul>

      <p style={{ marginTop: '2rem', fontStyle: 'italic', color: 'var(--text-muted, #666)' }}>
        Our support team is available Monday to Friday, New Zealand time (UTC+12, UTC+13 in summer).
      </p>
    </div>
  )
}
