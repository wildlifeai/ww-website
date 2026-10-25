// Copyright (c) 2026
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Plain names for the review_status keys of Dataset Health's review funnel (UI audit 03 #21).
// The funnel counts `observations.review_status`, with "unreviewed" for a row that has none
// (backend `domain/intelligence.py::dataset_health`).

const LABELS: Record<string, string> = {
  unreviewed: 'Not reviewed',
  ai_reviewed: 'Labelled by AI',
  consensus_approved: 'Approved by consensus',
  human_reviewed: 'Checked by a person',
  expert_reviewed: 'Checked by an expert',
}

/** The label for one funnel key; an unknown key reads as its words. */
export function funnelLabel(status: string): string {
  if (LABELS[status]) return LABELS[status]
  const words = status.replace(/_/g, ' ').trim()
  return words ? words[0].toUpperCase() + words.slice(1) : status
}
