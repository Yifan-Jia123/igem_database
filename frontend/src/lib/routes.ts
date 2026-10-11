import { useCallback, useEffect, useState } from 'react'
import type { View } from './entities'

/**
 * One search on the home map. It goes into the URL so that **browser Back undoes
 * this search**, rather than leaving the site outright because `/` is the first
 * entry in the tab.
 *
 * The shape mirrors the backend contract one-to-one: a keyword search has only
 * `query`; a pathway search carries the token string the composer handed the API
 * verbatim (`start`/`end`/`via`), and the server can already parse an id, a bare
 * ChEBI number or a compound name -- so replaying from the URL gives the same
 * result as running it by hand.
 */
export type MapSearchRoute = {
  mode: 'enzyme' | 'pathway'
  /** The keyword search's word. A pathway search is always the empty string -- a
   *  chain is not something a single word can describe. */
  query: string
  start: string
  end: string
  via: string[]
}

export type AppRoute = {
  view: Exclude<View, 'structure'>
  enzymeId?: string
  query?: string
  blast?: boolean
  /**
   * Only meaningful when `view === 'home'`. **All three states are required**:
   * - `undefined`: not home -- no signal, the consumer must not touch anything;
   * - `null`: home with no search params -- clear the search state;
   * - a value: run the search to this spec.
   *
   * Merging `undefined` into `null` immediately brings back the "open an enzyme
   * detail page and it wipes the map search results" bug: going from `/?q=x` to
   * `/enzymes/X` would turn the spec into "should clear" out of nowhere.
   */
  mapSearch?: MapSearchRoute | null
}

/** `?q=` / `mode` / `start` / `end` / `via` → search spec. None of them present means `null`. */
function parseMapSearch(params: URLSearchParams): MapSearchRoute | null {
  const query = params.get('q') ?? ''
  const start = params.get('start') ?? ''
  const end = params.get('end') ?? ''
  const via = params.getAll('via').filter((token) => token !== '')
  const wantsPathway = params.get('mode') === 'pathway'
  // A `?mode=enzyme` with no word to search is not a search; normalise to a bare
  // home so the URL and the state cannot tell different stories.
  if (!query && !wantsPathway && !start && !end && via.length === 0) return null
  // `mode` is derived from whether there is a keyword, not stored separately --
  // that way `routeUrl(parseRoute(url))` is always the canonical form, and
  // `syncRoute` does not replaceState on every popstate.
  return { mode: query ? 'enzyme' : 'pathway', query, start, end, via }
}

/** Fixed order `q, mode, start, end, via` -- an unstable order makes the previous
 *  entry's canonicalisation fire over and over. */
function serializeMapSearch(spec: MapSearchRoute, params: URLSearchParams) {
  if (spec.query) params.set('q', spec.query)
  params.set('mode', spec.mode)
  if (spec.start) params.set('start', spec.start)
  if (spec.end) params.set('end', spec.end)
  // `via` uses repeated params rather than comma-joining: a token here may be raw
  // text that never got parsed, and real compound names contain commas (measured
  // label `2-cis,6-cis-farnesyl diphosphate(3−)`).
  spec.via.filter((token) => token !== '').forEach((token) => params.append('via', token))
}

export function parseRoute(url: URL): AppRoute {
  const path = url.pathname.replace(/\/+$/, '') || '/'
  if (path === '/') return { view: 'home', mapSearch: parseMapSearch(url.searchParams) }
  if (path === '/downloads') return { view: 'downloads' }
  if (path === '/search') {
    return { view: 'search', query: url.searchParams.get('q') ?? '', blast: url.searchParams.get('mode') === 'blast' }
  }
  const match = /^\/enzymes\/([^/]+)$/.exec(path)
  if (match) {
    try {
      const enzymeId = decodeURIComponent(match[1])
      if (enzymeId.trim()) return { view: 'enzyme', enzymeId }
    } catch {
      return { view: 'home' }
    }
  }
  return { view: 'home' }
}

export function routeUrl(route: AppRoute): string {
  if (route.view === 'downloads') return '/downloads'
  if (route.view === 'enzyme' && route.enzymeId) return `/enzymes/${encodeURIComponent(route.enzymeId)}`
  if (route.view === 'search') {
    const params = new URLSearchParams()
    if (route.query) params.set('q', route.query)
    if (route.blast) params.set('mode', 'blast')
    const search = params.toString()
    return `/search${search ? `?${search}` : ''}`
  }
  if (route.view === 'home' && route.mapSearch) {
    const params = new URLSearchParams()
    serializeMapSearch(route.mapSearch, params)
    const search = params.toString()
    return `/${search ? `?${search}` : ''}`
  }
  // A home with no search params must **still** be a bare `'/'` -- `resetHome` and
  // `goTo('home')` both rely on it to mean "back to a clean browse map".
  return '/'
}

/**
 * The app stores an index of "how many levels we pushed" in `history.state`, to
 * tell whether there is still in-app history to go back to. **Do not replace it
 * with a counter that +1s on push and -1s on popstate**: `popstate` also fires on
 * **forward**, so "back one, forward one" drives the count to 0 and it is judged
 * "cannot go back" though there are entries behind. With the index in state,
 * forward and back are both correct -- re-reading it on popstate gives the
 * authoritative value.
 */
type RouteHistoryState = { atlasIndex?: number }

function currentIndex(): number {
  const state = window.history.state as RouteHistoryState | null
  return typeof state?.atlasIndex === 'number' ? state.atlasIndex : 0
}

export function useAppRoute() {
  const [route, setRoute] = useState(() => parseRoute(new URL(window.location.href)))
  const [historyIndex, setHistoryIndex] = useState(currentIndex)

  useEffect(() => {
    const syncRoute = () => {
      const next = parseRoute(new URL(window.location.href))
      const canonical = routeUrl(next)
      if (`${window.location.pathname}${window.location.search}` !== canonical) {
        // This in-place canonicalisation is not a navigation, so the index must be
        // **carried over as-is**, not written as null to wipe it.
        window.history.replaceState({ atlasIndex: currentIndex() }, '', canonical)
      }
      setRoute(next)
      setHistoryIndex(currentIndex())
    }
    syncRoute()
    window.addEventListener('popstate', syncRoute)
    return () => window.removeEventListener('popstate', syncRoute)
  }, [])

  const navigate = useCallback((next: AppRoute, replace = false) => {
    const target = routeUrl(next)
    if (`${window.location.pathname}${window.location.search}` !== target) {
      const index = currentIndex()
      if (replace) {
        // replace adds no entry, so the index stays -- "can go back" must not change.
        window.history.replaceState({ atlasIndex: index }, '', target)
      } else {
        window.history.pushState({ atlasIndex: index + 1 }, '', target)
        setHistoryIndex(index + 1)
      }
    }
    setRoute(parseRoute(new URL(target, window.location.origin)))
  }, [])

  /** Go back one in-app record. Returns false when there is nothing to pop, and
   *  the caller handles the fallback. */
  const goBack = useCallback(() => {
    if (currentIndex() <= 0) return false
    window.history.back()
    return true
  }, [])

  return { route, navigate, goBack, canGoBack: historyIndex > 0 }
}
