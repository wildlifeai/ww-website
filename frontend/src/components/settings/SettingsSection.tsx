// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// A titled block on the Settings page.
export function SettingsSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: '2rem' }}>
      <h3 style={{ margin: '0 0 0.75rem 0', fontSize: '1.0625rem' }}>{title}</h3>
      {children}
    </section>
  )
}
