/**
 * A deployment select grouped by project, with a search box once it holds more than ten
 * options (UI audit 05 #6). Only deployments the user can upload to are passed in.
 */
import { useState } from 'react'
import { filterPickerGroups, optionCount, type PickerGroup } from '../../lib/uploadTargets'

const SEARCH_ABOVE = 10

export function DeploymentPicker({
  groups, value, onChange, placeholder, label,
}: {
  groups: PickerGroup[]
  value: string
  onChange: (deploymentId: string) => void
  placeholder: string
  /** The accessible name of the select. */
  label: string
}) {
  const [query, setQuery] = useState('')
  const searchable = optionCount(groups) > SEARCH_ABOVE
  const shown = searchable ? filterPickerGroups(groups, query) : groups
  const grouped = groups.length > 1
  const noMatch = searchable && query.trim() !== '' && optionCount(shown) === 0

  const search = (q: string) => {
    setQuery(q)
    // A choice the search hides would still be the select's value, unseen: clear it.
    if (value && !filterPickerGroups(groups, q).some((g) => g.options.some((o) => o.value === value))) onChange('')
  }

  return (
    <div className="triage-picker">
      {searchable && (
        <input
          type="search"
          className="triage-select"
          placeholder="Search deployments or projects"
          aria-label={`Search: ${label}`}
          value={query}
          onChange={(e) => search(e.target.value)}
        />
      )}
      <select
        className="triage-select"
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{noMatch ? 'No deployment matches the search' : placeholder}</option>
        {grouped
          ? shown.map((g) => (
            <optgroup key={g.projectId} label={g.projectName}>
              {g.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </optgroup>
          ))
          : shown.flatMap((g) => g.options).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  )
}
