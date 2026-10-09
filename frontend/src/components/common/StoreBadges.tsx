/**
 * StoreBadges: the App Store and Google Play download badges, side by side.
 *
 * The App Store badge is Apple's own SVG, served from public/badges so the
 * page does not depend on developer.apple.com answering. The Google Play
 * badge comes from Google's badge host, the one their guidelines point to;
 * until October 2026 it was hotlinked from Wikimedia, which no page should
 * rely on. Explicit sizes keep the layout from shifting while they load.
 */
export const APP_STORE_URL = 'https://apps.apple.com/app/id6480342929'
export const PLAY_STORE_URL = 'https://play.google.com/store/apps/details?id=com.wildlife.wildlifewatcher&pcampaignid=web_share'

const APP_STORE_BADGE = '/badges/app-store.svg'
const PLAY_STORE_BADGE = 'https://play.google.com/intl/en_us/badges/static/images/badges/en_badge_web_generic.png'

export function StoreBadges({ height = 40, style }: { height?: number; style?: React.CSSProperties }) {
  // Apple's badge is 119.66 x 40; Google's PNG carries its own padding and
  // reads as the same size at 1.35x the height.
  return (
    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center', ...style }}>
      <a href={APP_STORE_URL} target="_blank" rel="noreferrer">
        <img src={APP_STORE_BADGE} alt="Download on the App Store" width={Math.round(height * 2.9916)} height={height} style={{ display: 'block' }} />
      </a>
      <a href={PLAY_STORE_URL} target="_blank" rel="noreferrer" style={{ margin: `-${height * 0.175}px` }}>
        <img src={PLAY_STORE_BADGE} alt="Get it on Google Play" width={Math.round(height * 1.35 * 2.5833)} height={Math.round(height * 1.35)} style={{ display: 'block' }} />
      </a>
    </div>
  )
}
