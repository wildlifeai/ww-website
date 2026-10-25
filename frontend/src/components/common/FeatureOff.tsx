// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
import { Link } from 'react-router-dom'

/** What a feature-flagged page shows when its route answers FEATURE_DISABLED (UI audit decision 6). */
export function FeatureOff({ backTo, backLabel }: { backTo: string; backLabel: string }) {
  return (
    <div className="glass-card" style={{ padding: '1.5rem', maxWidth: 560, margin: '2rem auto', textAlign: 'center' }}>
      <p style={{ margin: '0 0 0.75rem', fontSize: '0.875rem', color: 'var(--text-muted)' }}>
        This feature is not switched on for this site.
      </p>
      <Link to={backTo} style={{ fontSize: '0.875rem', color: 'var(--primary)' }}>{backLabel}</Link>
    </div>
  )
}
