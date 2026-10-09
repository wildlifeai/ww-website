import { Link } from 'react-router-dom'
import { usePageMeta } from '../lib/pageMeta'

export function TermsOfServicePage() {
  usePageMeta({ title: 'Terms of Service' })
  return (
    <div className="container" style={{ maxWidth: '800px', margin: '0 auto', padding: '2rem 0', lineHeight: '1.6' }}>
      <h1 style={{ marginBottom: '1.5rem' }}>Terms of Service</h1>

      <p>These Terms of Service ("Terms") govern your use of the Wildlife Watcher platform, including the mobile application ("App") and the web application at wildlifewatcher.ai ("Website"), operated by Wildlife.ai Trust ("we", "us", "our"), a New Zealand Charity (CC57052).</p>

      <p>By creating an account or using the platform, you agree to these Terms. If you do not agree, please do not use the platform.</p>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>1. About the Platform</h2>
      <p>Wildlife Watcher is an open-source wildlife monitoring platform that enables conservation researchers and practitioners to:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li>Deploy and manage AI-powered camera traps in the field.</li>
        <li>Record and share wildlife observation data within organisations.</li>
        <li>Upload, manage, and deploy machine learning models to camera devices.</li>
        <li>Analyse wildlife images using AI-powered species identification.</li>
        <li>Export data in standardised formats (CamtrapDP) for interoperability with biodiversity databases.</li>
      </ul>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>2. Accounts and Access</h2>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Eligibility:</strong> You must be at least 13 years old to create an account.</li>
        <li><strong>Account security:</strong> You are responsible for maintaining the confidentiality of your login credentials. Notify us immediately at <a href="mailto:contact@wildlife.ai">contact@wildlife.ai</a> if you suspect unauthorised access to your account.</li>
        <li><strong>Accurate information:</strong> You agree to provide accurate and current information when creating your account.</li>
        <li><strong>Organisation membership:</strong> Access to projects and data is governed by your organisation's administrators. We are not responsible for access decisions made by your organisation.</li>
      </ul>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>3. Acceptable Use</h2>
      <p>You agree to use the platform only for lawful wildlife monitoring, conservation research, and related purposes. You must not:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li>Upload content that is illegal, harmful, threatening, abusive, or that violates the rights of others.</li>
        <li>Upload AI models or images containing malware, viruses, or malicious code.</li>
        <li>Attempt to gain unauthorised access to other users' accounts, projects, or data.</li>
        <li>Use the platform to track, surveil, or monitor people without their knowledge and consent.</li>
        <li>Use automated tools (bots, scrapers) to access the platform in a manner that disrupts the service or exceeds reasonable use.</li>
        <li>Misrepresent your identity or affiliation with an organisation.</li>
        <li>Use the platform in any way that could damage, disable, or impair the service.</li>
      </ul>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>4. Content and Intellectual Property</h2>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>4.1 Your Content</h3>
      <p>You retain ownership of all content you upload to the platform, including:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li>Wildlife images and observation data.</li>
        <li>AI models and associated metadata.</li>
        <li>Deployment records and field notes.</li>
      </ul>
      <p>By uploading content, you grant Wildlife.ai a non-exclusive, royalty-free licence to store, process, and display your content as necessary to provide the platform's services. This licence terminates when you delete your content or account.</p>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>4.2 Shared Project Data</h3>
      <p>Content uploaded to a shared project is visible to all members of that project within your organisation. You are responsible for ensuring you have the right to share any content you upload to shared projects.</p>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>4.3 Open-Source Software</h3>
      <p>The Wildlife Watcher platform software is open source and licensed under the <a href="https://www.gnu.org/licenses/gpl-3.0.en.html" target="_blank" rel="noreferrer">GNU General Public License v3.0</a>. These Terms govern your use of the hosted service, not the underlying software.</p>

      <h3 style={{ marginTop: '1.5rem', marginBottom: '0.5rem' }}>4.4 Scientific Data and Attribution</h3>
      <p>Wildlife observation data exported from the platform (including CamtrapDP packages) should be attributed in accordance with applicable scientific citation standards. We encourage users to cite Wildlife.ai and the Wildlife Watcher platform when publishing research based on data collected through the platform.</p>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>5. AI Models and Image Analysis</h2>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Model uploads:</strong> Organisation managers may upload custom AI models for deployment to camera devices. You are responsible for ensuring you have the right to use and distribute any models you upload.</li>
        <li><strong>Pre-trained models:</strong> We provide access to pre-trained models from third-party sources (e.g. SenseCap, Edge Impulse). These models are subject to their own licences and terms.</li>
        <li><strong>No guarantee of accuracy:</strong> AI-powered species identification is provided as a tool to assist conservation work. We do not guarantee the accuracy of AI predictions and they should not be relied upon as the sole basis for conservation decisions.</li>
        <li><strong>File size limits:</strong> Uploads are limited to 50 MB per file. We reserve the right to adjust these limits.</li>
      </ul>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>6. Privacy</h2>
      <p>Your use of the platform is also governed by our <Link to="/privacy">Privacy Policy</Link>, which describes how we collect, use, and protect your personal information.</p>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>7. Service Availability</h2>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li>We aim to keep the platform available at all times but do not guarantee uninterrupted access.</li>
        <li>We may perform maintenance, updates, or modifications that temporarily affect availability.</li>
        <li>We reserve the right to modify, suspend, or discontinue features of the platform with reasonable notice.</li>
      </ul>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>8. Limitation of Liability</h2>
      <p>To the maximum extent permitted by law:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li>The platform is provided <strong>"as is"</strong> and <strong>"as available"</strong> without warranties of any kind, express or implied.</li>
        <li>Wildlife.ai is not liable for any indirect, incidental, special, consequential, or punitive damages arising from your use of the platform.</li>
        <li>Wildlife.ai is not liable for any loss or corruption of data, including wildlife observation data, uploaded models, or images.</li>
        <li>Our total liability to you for any claim arising from these Terms shall not exceed NZ$100.</li>
      </ul>
      <p>Nothing in these Terms excludes or limits liability that cannot be excluded or limited under New Zealand law, including the Consumer Guarantees Act 1993 (where applicable).</p>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>9. Account Termination</h2>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>By you:</strong> You may delete your account at any time by submitting our <a href="https://forms.gle/aasjsW5N26giYDk96" target="_blank" rel="noreferrer">Account Deletion Request Form</a> or emailing <a href="mailto:contact@wildlife.ai">contact@wildlife.ai</a>.</li>
        <li><strong>By us:</strong> We may suspend or terminate your account if you violate these Terms, with or without notice depending on the severity of the violation.</li>
        <li><strong>Effect of termination:</strong> Upon account deletion, your personal data is removed in accordance with our <Link to="/privacy#account-deletion">Privacy Policy</Link>. Anonymised scientific data may be retained.</li>
      </ul>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>10. Changes to These Terms</h2>
      <p>We may update these Terms from time to time. Material changes will be communicated via email or a prominent notice on the platform. Continued use of the platform after changes take effect constitutes acceptance of the updated Terms.</p>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>11. Governing Law</h2>
      <p>These Terms are governed by the laws of New Zealand. Any disputes arising from these Terms shall be subject to the exclusive jurisdiction of the courts of New Zealand.</p>

      <h2 style={{ marginTop: '2rem', marginBottom: '1rem' }}>12. Contact</h2>
      <p>If you have questions about these Terms, contact us at:</p>
      <ul style={{ paddingLeft: '1.5rem', marginBottom: '1rem' }}>
        <li><strong>Email:</strong> <a href="mailto:contact@wildlife.ai">contact@wildlife.ai</a></li>
        <li><strong>Organisation:</strong> Wildlife.ai Trust, New Zealand Charity CC57052</li>
      </ul>

      <p style={{ marginTop: '2rem', fontStyle: 'italic', color: 'var(--text-muted, #666)' }}>
        Version 1.0 — Effective: May 18, 2026
      </p>
    </div>
  )
}
