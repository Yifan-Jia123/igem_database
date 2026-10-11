import { useEffect, useRef, useState } from 'react'
import { Layers } from 'lucide-react'
import { loadMetadataFilters } from '../api'
import { sourceLabel } from '../lib/sourceLabels'

/**
 * Search set: pins down the data sources **before** the search.
 *
 * A different layer from the page's existing "Data source" filter -- that one
 * filters rows **already fetched**, this one decides **which rows to fetch**.
 * Both are kept: when the search scope is wide, a display filter is still useful.
 * So this control replaces no existing filter; it is a new layer.
 *
 * Empty array = all (following the repo-wide "empty array means no filter"
 * convention; there is no `all` sentinel value).
 *
 * It is also the "small always-on module showing the current search set" the user
 * asked for: one control both shows the current value and can be opened to change
 * it. Not two (that would be two controls saying the same thing).
 */

export type SearchSetOption = { value: string; count: number }

/**
 * Fetching `/metadata/filter-options` once per page is wasteful -- all three pages
 * mount this control. Once per session is enough (the search-set candidates only
 * change with the ETL). On failure it is not cached, and the next mount retries.
 */
let optionsPromise: Promise<SearchSetOption[]> | null = null

function loadSearchSetOptions(): Promise<SearchSetOption[]> {
  if (!optionsPromise) {
    optionsPromise = loadMetadataFilters()
      .then((payload) => payload.searchSets ?? [])
      .catch(() => {
        optionsPromise = null
        return []
      })
  }
  return optionsPromise
}

export function SearchSetControl({
  value,
  onChange,
}: {
  /** The current search set, `[]` = all. */
  value: string[]
  onChange: (next: string[]) => void
}) {
  const [options, setOptions] = useState<SearchSetOption[]>([])
  const detailsRef = useRef<HTMLDetailsElement | null>(null)

  useEffect(() => {
    let cancelled = false
    loadSearchSetOptions().then((next) => {
      if (!cancelled) setOptions(next)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const total = options.reduce((sum, option) => sum + option.count, 0)
  const current = value[0]
  const currentLabel = current ? sourceLabel(current) : 'All sources'
  const currentCount = current ? options.find((option) => option.value === current)?.count : total

  // List only the sources that **really have rows** (see the backend's `searchSets`
  // note): picking a 0-row set is an empty search, and the user cannot tell it was
  // their own choice.
  const choices: Array<{ key: string; label: string; count?: number; next: string[] }> = [
    { key: '', label: 'All sources', count: total || undefined, next: [] },
    ...options.map((option) => ({
      key: option.value,
      label: sourceLabel(option.value),
      count: option.count,
      next: [option.value],
    })),
  ]

  const pick = (next: string[]) => {
    // Native <details> (consistent with the page's other filter dropdowns); close
    // it by hand on pick.
    if (detailsRef.current) detailsRef.current.open = false
    if (next[0] !== current || next.length !== value.length) onChange(next)
  }

  return (
    <details className="search-table-filter-dropdown search-set-dropdown" ref={detailsRef}>
      <summary
        title={
          current
            ? `Searching only ${currentLabel} — ${currentCount?.toLocaleString() ?? '?'} entries`
            : `Searching every source — ${total.toLocaleString()} entries`
        }
      >
        <Layers size={13} />
        <span>Search set</span>
        <small className="search-set-current">
          {currentLabel}
          {currentCount ? ` (${currentCount.toLocaleString()})` : ''}
        </small>
      </summary>

      <div className="search-table-filter-popover search-set-popover">
        <p className="search-set-title">Search set</p>
        <p className="search-set-hint">Limits where every search looks. Downloads are not affected.</p>
        <div className="search-set-row">
          {choices.map((choice) => {
            const active = choice.key === (current ?? '')
            return (
              <button
                key={choice.key || 'all'}
                type="button"
                className={`search-set-option ${active ? 'on' : ''}`}
                onClick={() => pick(choice.next)}
                aria-pressed={active}
              >
                <span>{choice.label}</span>
                {choice.count !== undefined && <small>{choice.count.toLocaleString()}</small>}
              </button>
            )
          })}
        </div>
      </div>
    </details>
  )
}
