import { usePageMeta } from '../lib/pageMeta'


export function PrivacyPolicyPage() {
  usePageMeta({ title: 'Privacy Policy' })
  return (
    <div className="container" style={{ maxWidth: '800px', margin: '0 auto', padding: '2rem 0', lineHeight: '1.6' }}>
      <h1 style={{ marginBottom: '1.5rem' }}>Privacy Policy</h1>

      <p>Wildlife.ai ("we", "us", "our") values your privacy. This privacy policy explains how the Wildlife Watcher platform — including the mobile application ("App") and the web application at wildlifewatcher.ai ("Website") — collects, uses, stores, and protects your personal information.</p>

      <p>Where you provide personal information to us, we will comply with the New Zealand Privacy Act 2020 and other applicable privacy and data protection laws ("Privacy Laws"). This privacy policy does not limit or exclude any of your rights under Privacy Laws. For further information on the New Zealand Privacy Act 2020, see <a href="https://www.privacy.org.nz" target="_blank" rel="noreferrer">www.privacy.org.nz</a>.</p>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>1. Information We Collect</h2>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>Account Information</h3>
      <p>When you create an account on the App or Website, we collect:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Email address</strong> – used for authentication and account identification.</li>
        <li><strong>First name and surname</strong> – used for display within the platform and to identify you to your organisation members.</li>
      </ul>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>Location Data</h3>
      <p>The App collects location data to support wildlife monitoring operations:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Precise location (GPS)</strong> – used to record GPS coordinates for camera trap deployment sites, enabling accurate habitat mapping and wildlife tracking.</li>
        <li><strong>Approximate location (network-based)</strong> – used as a fallback when GPS is unavailable.</li>
        <li><strong>Background location</strong> – with your explicit permission, the App may access your location while minimised to maintain GPS coordinates during active field deployments.</li>
      </ul>
      <p><em>Note: Location data is stored alongside your deployment records and is shared with other members of your organisation who have access to the same project.</em></p>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>Bluetooth Data (Mobile App Only)</h3>
      <p>The App uses Bluetooth Low Energy (BLE) to:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li>Discover and connect to Wildlife Watcher camera trap devices.</li>
        <li>Transfer configuration settings (e.g. detection sensitivity, timelapse intervals) to devices.</li>
        <li>Receive device status information (battery level, firmware version, LoRaWAN signal strength).</li>
        <li>Perform firmware updates on devices.</li>
      </ul>
      <p>No Bluetooth data is transmitted to external parties. Device identifiers are used solely for communicating with camera hardware.</p>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>Camera and Photo Library (Mobile App Only)</h3>
      <p>With your permission, the App may access:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Camera</strong> – to capture photos of deployment sites for documentation.</li>
        <li><strong>Photo library</strong> – to select existing photos for deployment records.</li>
      </ul>
      <p>Photos you capture or select are uploaded to your organisation's project storage and are visible to members with access to that project.</p>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>File Uploads (Website)</h3>
      <p>Through the Website, you may upload:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>AI models</strong> – machine learning model files (ZIP, TFLite) for deployment to camera devices. File metadata (filename, size, MIME type) is recorded.</li>
        <li><strong>Images for analysis</strong> – wildlife images submitted for AI-powered species identification. These are processed server-side and results are returned to you.</li>
        <li><strong>CamtrapDP data packages</strong> – standardised camera trap data packages imported into the platform for mapping and reporting.</li>
      </ul>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>Device and Network Information</h3>
      <p>The platform collects standard diagnostics, including:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Wi-Fi and Network status</strong> – to manage offline-first data behaviour and synchronisation (App).</li>
        <li><strong>Device information</strong> – used for crash reporting and compatibility diagnostics.</li>
      </ul>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>Browser Storage (Website)</h3>
      <p>The Website uses your browser's local storage and session storage to:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li>Store authentication tokens for your logged-in session.</li>
        <li>Cache user preferences and UI state.</li>
      </ul>
      <p>The Website does not use third-party tracking cookies. Authentication tokens are managed by Supabase Auth and stored locally in your browser.</p>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>Usage Data</h3>
      <p>We may collect information about how you interact with the platform:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li>Features accessed and actions taken within the App or Website.</li>
        <li>Error logs, crash reports, and performance metrics.</li>
      </ul>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>2. How We Use Your Information</h2>
      <p>We use the information we collect to:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Provide the platform:</strong> Authenticate your account, display projects/deployments, and synchronise data between your devices and the cloud.</li>
        <li><strong>Enable wildlife monitoring:</strong> Record camera trap locations, device configurations, and field observations.</li>
        <li><strong>Process AI models:</strong> Store, convert, and distribute machine learning models you upload for use on camera devices.</li>
        <li><strong>Analyse images:</strong> Run AI-powered species identification on images you submit through the Website.</li>
        <li><strong>Facilitate collaboration:</strong> Share project data with authorised members of your organisation.</li>
        <li><strong>Support data standards:</strong> Import and export data in CamtrapDP v1.0 format for interoperability with biodiversity databases.</li>
        <li><strong>Improve the platform:</strong> Analyse usage patterns, diagnose technical issues, and develop new features.</li>
        <li><strong>Communicate with you:</strong> Send important service notifications (e.g. account changes, system updates).</li>
        <li><strong>Comply with legal obligations:</strong> Respond to lawful requests from authorities as required by law.</li>
      </ul>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>3. How We Store and Protect Your Information</h2>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>3.1 Data Storage</h3>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Cloud storage:</strong> Your account data, project records, and uploaded files are stored securely on Supabase (hosted on Amazon Web Services infrastructure).</li>
        <li><strong>Local storage (App):</strong> The App uses an offline-first architecture. A local database on your device stores a synchronised copy of your data for offline use.</li>
        <li><strong>Browser storage (Website):</strong> Authentication tokens are stored in your browser's local storage. No sensitive personal data is persisted client-side beyond session tokens.</li>
        <li><strong>Secure credentials:</strong> Authentication tokens are stored in your device's secure storage (Keychain on iOS, Encrypted SharedPreferences on Android) when using the App.</li>
      </ul>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>3.2 Data Security</h3>
      <p>We implement technical and organisational measures including:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Row Level Security (RLS):</strong> Database policies that enforce multi-tenant organisation isolation, ensuring you only access data belonging to your own organisation.</li>
        <li><strong>Role-based access controls:</strong> Permissions limited by role (System Admin, Organisation Manager, Project Admin, Project Member).</li>
        <li><strong>Encrypted transmission:</strong> HTTPS/TLS encryption for all communication between your device/browser and our servers.</li>
        <li><strong>JWT-based authentication:</strong> Cryptographically signed tokens for all API requests.</li>
        <li><strong>Upload limits:</strong> File size restrictions (50 MB) to prevent abuse of the upload system.</li>
      </ul>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>3.3 International Data Transfers</h3>
      <p>Your data may be stored in data centres outside of New Zealand. By using the platform, you consent to the transfer of information to facilities where data protection laws may differ.</p>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>4. Data Sharing and Disclosure</h2>
      <p>We do not sell your personal information. We may share data with:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Organisation members:</strong> Project records and your name are visible to members with shared access.</li>
        <li><strong>Service providers:</strong> We use third parties to operate the platform, including:
          <ul style={{ paddingLeft: '1.5rem', marginTop: '0.25rem' }}>
            <li><strong>Supabase</strong> – database, authentication, and file storage</li>
            <li><strong>Amazon Web Services</strong> – cloud infrastructure hosting</li>
            <li><strong>Google Maps</strong> – mapping and location services</li>
            <li><strong>Expo</strong> – mobile app build and update distribution</li>
            <li><strong>GitHub</strong> – pre-trained AI model hosting and source code</li>
            <li><strong>Cloudflare</strong> – website hosting, CDN, and anonymous web analytics</li>
          </ul>
          These providers are contractually obligated to protect your data.
        </li>
        <li><strong>Law Enforcement:</strong> If required by law or valid legal requests.</li>
        <li><strong>Business transfers:</strong> In the event of a merger or acquisition, your information may be transferred to the acquiring entity.</li>
      </ul>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>5. Data Retention and Deletion</h2>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>5.1 Retention Policy</h3>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Active accounts:</strong> We retain your information as long as your account is active.</li>
        <li><strong>Scientific data:</strong> Camera trap records and wildlife observations may be retained indefinitely for research purposes. However, this data will be permanently anonymised and no longer linked to your identity upon account deletion.</li>
        <li><strong>Uploaded files:</strong> AI model files and analysis images are retained as long as the associated organisation account is active.</li>
      </ul>

      <h3 id="account-deletion" style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>5.2 Account Deletion</h3>
      <p>You have the right to request deletion of your account and personal data at any time.</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>How to Request:</strong> Submit our <a href="https://forms.gle/aasjsW5N26giYDk96" target="_blank" rel="noreferrer">Account Deletion Request Form</a>, or email <a href="mailto:contact@wildlife.ai">contact@wildlife.ai</a>.</li>
        <li><strong>Process:</strong> Requests are processed within 7–14 business days.</li>
        <li><strong>What is Deleted:</strong> User profile (name, email), credentials, role assignments, and personal settings.</li>
        <li><strong>What is Retained (Anonymised):</strong> Deployment locations, dates, and wildlife metadata. All audit trails (e.g., "created by") are set to null.</li>
      </ul>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>6. Your Rights</h2>
      <p>Under the New Zealand Privacy Act 2020, you have the right to:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li>Access your personal information held by us.</li>
        <li>Request correction of inaccurate or incomplete information.</li>
        <li>Request deletion of your account.</li>
        <li>Withdraw consent for optional data (e.g. GPS, camera) via device settings.</li>
      </ul>
      <p>If you are located in the European Union, you may also have rights under the General Data Protection Regulation (GDPR), including the right to data portability and the right to object to processing. To exercise these rights, contact <a href="mailto:contact@wildlife.ai">contact@wildlife.ai</a>.</p>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>7. Additional Disclosures</h2>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Children's Privacy:</strong> The platform is not directed at children under 13. We do not knowingly collect their data.</li>
        <li><strong>Cookies:</strong> The mobile App does not use cookies. The Website uses browser local storage for authentication tokens only — no third-party tracking cookies are used.</li>
        <li><strong>Complaints:</strong> If you believe your privacy has been breached, email <a href="mailto:contact@wildlife.ai">contact@wildlife.ai</a>. If unsatisfied, you may contact the <a href="https://www.privacy.org.nz/your-rights/making-a-complaint/" target="_blank" rel="noreferrer">Office of the Privacy Commissioner</a>.</li>
        <li><strong>Governing Law:</strong> This policy is governed by the laws of New Zealand. Wildlife.ai means Wildlife.ai Trust, a New Zealand Charity (CC57052).</li>
        <li><strong>Changes:</strong> We may update this policy from time to time. Material changes will be communicated via email or a prominent notice on the platform.</li>
      </ul>

      <p style={{ marginTop: '2rem', fontStyle: 'italic', color: 'var(--text-muted, #666)' }}>
        Version 2.0 — Effective: May 18, 2026
      </p>
    </div>
  )
}
