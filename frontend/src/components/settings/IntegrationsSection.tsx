// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// IntegrationsSection: iNaturalist and organisation API keys on Settings. It renders nothing when
// neither applies (UI audit 05 #16): iNaturalist is off without FF_INAT_ENABLED and for the
// shared demo account, API keys need an organisation the user manages and the public API on.
import { useAuth } from '../../hooks/useAuth'
import { useINat } from '../../hooks/useINat'
import { useApiKeys, useManagedOrgs } from '../../hooks/useManagedOrgs'
import { isPublicApiDisabled } from '../../lib/apiKeys'
import { ApiKeysPanel } from './ApiKeysPanel'
import { InaturalistPanel } from './InaturalistPanel'
import { SettingsSection } from './SettingsSection'

export function IntegrationsSection() {
  const { isDemo } = useAuth()
  const inat = useINat()
  const orgs = useManagedOrgs()
  // The flag is global, so the first organisation's answer stands for all of them.
  const probe = useApiKeys(orgs[0]?.id ?? '')

  const showInat = !isDemo && inat.enabled
  const showKeys = orgs.length > 0 && !isPublicApiDisabled(probe.error)
  if (!showInat && !showKeys) return null

  return (
    <SettingsSection title="Integrations">
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        {showInat && <InaturalistPanel inat={inat} />}
        {showKeys && <ApiKeysPanel orgs={orgs} />}
      </div>
    </SettingsSection>
  )
}
