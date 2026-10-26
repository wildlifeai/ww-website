/**
 * PrototypeBanner: sets expectations on the public home page.
 *
 * Wildlife Watcher is in active development and people are testing it with
 * real field data, so the site says so plainly rather than letting a rough
 * edge come as a surprise. Signed in, the same notice is one line in the footer.
 */
import { Construction } from 'lucide-react'
import { Banner } from '../ui/Banner'

const FEEDBACK_MAILTO = 'mailto:contact@wildlife.ai?subject=Wildlife%20Watcher%20feedback'

export function PrototypeBanner() {
  return (
    <Banner tone="info" icon={Construction}>
      <strong>This is a prototype, we&apos;re still building it.</strong>{' '}
      Features change week to week and you may hit rough edges. Keep your own copy of anything
      important, and tell us what breaks: <a href={FEEDBACK_MAILTO}>contact@wildlife.ai</a>.
    </Banner>
  )
}
