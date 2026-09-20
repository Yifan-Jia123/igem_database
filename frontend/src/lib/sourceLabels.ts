/**
 * Data-source labels and filter options (single source of truth).
 *
 * The source enum is owned by the backend (`/metadata/filter-options` →
 * `sourceTypes`, generated from `SourceType`), but the UI needs a display name
 * before that request lands — and it must never silently drop a value it does
 * not recognise. Three separate inline copies of this map existed before
 * (graph page, search-results page, BLAST drawer) and they had drifted: the
 * search-results and BLAST copies listed only 2 of the 4 values, so an
 * `ai_literature` / `manual_literature` enzyme was unfilterable on one page and
 * rendered with no source tag at all in the other.
 */

const SOURCE_LABELS: Record<string, string> = {
  swiss_prot: 'Swiss-Prot',
  trembl: 'TrEMBL',
  ai_literature: 'AI (literature)',
  manual_literature: 'Manual (literature)',
}

/** Backend `SourceType` order; used when the API has not answered yet. */
export const DEFAULT_SOURCE_ORDER = ['swiss_prot', 'trembl', 'ai_literature', 'manual_literature']

/**
 * Display name for one source value.
 *
 * Unknown values fall back to the raw value with underscores opened up rather
 * than being hidden — a source the backend adds later should still be visible
 * (and filterable) instead of disappearing from the UI.
 */
export function sourceLabel(value: string): string {
  return SOURCE_LABELS[value] || value.replace(/_/g, ' ')
}

/**
 * Table-side filter options: the backend's own list when available, otherwise
 * the built-in order. Any value present in the rows but missing from the API
 * list is appended so a filter can always reach every source on screen.
 */
export function sourceOptions(fromApi?: string[] | null, seen?: Iterable<string>): string[] {
  const options = fromApi && fromApi.length > 0 ? [...fromApi] : [...DEFAULT_SOURCE_ORDER]
  for (const value of seen || []) {
    if (value && !options.includes(value)) options.push(value)
  }
  return options
}

/**
 * Graph-side filter options: only the sources that the current graph actually
 * carries.
 *
 * The map can only draw enzymes that have a reaction annotation, so a source no
 * edge carries would hand the user an empty map the moment they picked it —
 * showing the full enum here is the same mistake as taking the organism list
 * from the whole table. Falls back to the API list before the graph has loaded.
 * Order follows `fromApi` so the chips do not reshuffle as the graph changes.
 */
export function sourceOptionsFromUnits(
  fromApi: string[] | null | undefined,
  seen: Iterable<string>,
): string[] {
  const present = [...new Set(seen)].filter(Boolean)
  if (present.length === 0) return sourceOptions(fromApi)
  const order = fromApi && fromApi.length > 0 ? fromApi : DEFAULT_SOURCE_ORDER
  const rank = (value: string) => {
    const index = order.indexOf(value)
    return index < 0 ? order.length : index
  }
  return present.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
}
