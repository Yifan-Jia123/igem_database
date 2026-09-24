import { useCallback, useEffect, useState } from 'react'
import type { View } from './entities'

export type AppRoute = {
  view: Exclude<View, 'structure'>
  enzymeId?: string
  query?: string
  blast?: boolean
}

export function parseRoute(url: URL): AppRoute {
  const path = url.pathname.replace(/\/+$/, '') || '/'
  if (path === '/') return { view: 'home' }
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
  return '/'
}

export function useAppRoute() {
  const [route, setRoute] = useState(() => parseRoute(new URL(window.location.href)))

  useEffect(() => {
    const syncRoute = () => {
      const next = parseRoute(new URL(window.location.href))
      const canonical = routeUrl(next)
      if (`${window.location.pathname}${window.location.search}` !== canonical) {
        window.history.replaceState(null, '', canonical)
      }
      setRoute(next)
    }
    syncRoute()
    window.addEventListener('popstate', syncRoute)
    return () => window.removeEventListener('popstate', syncRoute)
  }, [])

  const navigate = useCallback((next: AppRoute, replace = false) => {
    const target = routeUrl(next)
    if (`${window.location.pathname}${window.location.search}` !== target) {
      if (replace) window.history.replaceState(null, '', target)
      else window.history.pushState(null, '', target)
    }
    setRoute(parseRoute(new URL(target, window.location.origin)))
  }, [])

  return [route, navigate] as const
}
