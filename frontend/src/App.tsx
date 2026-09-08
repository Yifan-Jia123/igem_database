import { useEffect, useMemo, useState } from 'react'
import {
  ArrowDownToLine,
  ChevronRight,
  CircleHelp,
  Database,
  Download,
  Menu,
  Network,
  Search,
  Settings2,
  Sparkles,
  X,
} from 'lucide-react'
import { entities as mockEntities, filterOptions as mockFilterOptions, graphEdges as mockGraphEdges, graphNodes as mockGraphNodes } from './data'
import { loadApiDataset } from './api'
import type { BlastPayload, BlastSession } from './api'
import { EnzymeDetailView } from './graphExperience'
import { BlastDrawer } from './components/BlastDrawer'
import { DownloadsPage } from './pages/DownloadsPage'
import { HomePage } from './pages/HomePage'
import { SearchResultsPage } from './pages/SearchResultsPage'
import { csvCell, getExternalRecordUrl, looksLikeProteinSequence, matchesFilters } from './lib/entities'
import type { FilterState, SearchKind, View } from './lib/entities'
import type { Entity } from './types'

let entities = mockEntities
let filterOptions = mockFilterOptions
let graphEdges = mockGraphEdges
let graphNodes = mockGraphNodes

const getEntity = (id: string) => entities.find((entity) => entity.id === id)
type QueueEntry = string | Entity

const navigation = [
  { view: 'home', label: 'Overview', icon: Sparkles },
  { view: 'search', label: 'Search library', icon: Search },
  { view: 'downloads', label: 'Download queue', icon: Download },
] as const

function App() {
  const [view, setView] = useState<View>('home')
  const [query, setQuery] = useState('')
  const [searchKind, setSearchKind] = useState<SearchKind>('all')
  const [selectedId, setSelectedId] = useState<string | null>('CHEBI:15377')
  const [selectedSpecies, setSelectedSpecies] = useState(filterOptions.species[0])
  const [selectedClass, setSelectedClass] = useState(filterOptions.classes[0])
  const [selectedFamily, setSelectedFamily] = useState(filterOptions.families[0])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [downloadedIds, setDownloadedIds] = useState<string[]>(['CHEBI:17115', 'ENZ:Q9ZSY2'])
  const [queuedEntitiesById, setQueuedEntitiesById] = useState<Record<string, Entity>>({})
  const [datasetRevision, setDatasetRevision] = useState(0)
  const [autoMapSearch, setAutoMapSearch] = useState<{ query: string; nonce: number } | null>(null)
  const [blastOpen, setBlastOpen] = useState(false)
  /** Last completed BLAST run, shown through the keyword-search table/map result views. */
  const [blastSession, setBlastSession] = useState<BlastSession | null>(null)
  /** One-shot hand-off telling the home map to scope itself to the active BLAST session. */
  const [autoBlastScope, setAutoBlastScope] = useState<{ sessionId: number; nonce: number } | null>(null)

  useEffect(() => {
    let cancelled = false

    loadApiDataset()
      .then((dataset) => {
        if (cancelled || dataset.entities.length === 0 || dataset.graphNodes.length === 0) return

        entities = dataset.entities
        filterOptions = dataset.filterOptions
        graphEdges = dataset.graphEdges
        graphNodes = dataset.graphNodes

        setSelectedSpecies(dataset.filterOptions.species[0] || mockFilterOptions.species[0])
        setSelectedClass(dataset.filterOptions.classes[0] || mockFilterOptions.classes[0])
        setSelectedFamily(dataset.filterOptions.families[0] || mockFilterOptions.families[0])
        setSelectedId((current) => (current && dataset.entities.some((entity) => entity.id === current) ? current : dataset.entities[0]?.id ?? null))
        setDownloadedIds((current) => current.filter((id) => dataset.entities.some((entity) => entity.id === id)))
        setDatasetRevision((revision) => revision + 1)
      })
      .catch((error) => {
        console.warn('Unable to load backend dataset; using mock data.', error)
      })

    return () => {
      cancelled = true
    }
  }, [])

  const filters = { query, searchKind, species: selectedSpecies, compoundClass: selectedClass, enzymeFamily: selectedFamily }
  const selected = selectedId ? getEntity(selectedId) : undefined
  const downloadedItems = useMemo(
    () => downloadedIds.map((id) => getEntity(id) || queuedEntitiesById[id]).filter((entity): entity is Entity => Boolean(entity)),
    [downloadedIds, queuedEntitiesById, datasetRevision],
  )
  const queuedIds = useMemo(() => new Set(downloadedIds), [downloadedIds])

  const visibleNodeIds = useMemo(
    () =>
      new Set(
        graphNodes
          .filter((node) => matchesFilters(getEntity(node.id), filters, filterOptions))
          .map((node) => node.id),
      ),
    [filters, datasetRevision],
  )

  const routeCount = new Set(graphEdges.map((edge) => edge.edgeGroupId || edge.reactionId)).size
  const queueCount = downloadedItems.length
  const visibleNodeCount = visibleNodeIds.size
  const visibleEdgeCount = graphEdges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)).length

  const rememberQueuedEntity = (entry: QueueEntry) => {
    if (typeof entry === 'string' || getEntity(entry.id)) return
    setQueuedEntitiesById((current) => ({ ...current, [entry.id]: entry }))
  }

  const forgetQueuedEntity = (id: string) => {
    setQueuedEntitiesById((current) => {
      if (!current[id]) return current
      const { [id]: _removed, ...rest } = current
      return rest
    })
  }

  const addToQueue = (entry: QueueEntry) => {
    const id = typeof entry === 'string' ? entry : entry.id
    rememberQueuedEntity(entry)
    setDownloadedIds((current) => (current.includes(id) ? current : [...current, id]))
  }

  const removeFromQueue = (id: string) => {
    setDownloadedIds((current) => current.filter((item) => item !== id))
    forgetQueuedEntity(id)
  }

  const toggleQueue = (entry: QueueEntry) => {
    const id = typeof entry === 'string' ? entry : entry.id
    if (downloadedIds.includes(id)) {
      forgetQueuedEntity(id)
    } else {
      rememberQueuedEntity(entry)
    }
    setDownloadedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]))
  }

  const clearQueue = () => {
    setDownloadedIds([])
    setQueuedEntitiesById({})
  }

  const openRecord = (entity: Entity) => {
    const url = getExternalRecordUrl(entity)
    // A pathway queued from the map is a search artifact with no external record.
    if (!url) return
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  const exportQueue = () => {
    if (downloadedItems.length === 0) return

    const rows = [
      ['id', 'kind', 'name', 'subtitle', 'species', 'compoundClass', 'enzymeFamily', 'tags', 'description'],
      ...downloadedItems.map((entity) => [
        entity.id,
        entity.kind,
        entity.name,
        entity.subtitle,
        entity.species ?? '',
        entity.compoundClass ?? '',
        entity.enzymeFamily ?? '',
        entity.tags.join(' | '),
        entity.description,
      ]),
    ]

    const csv = rows.map((row) => row.map(csvCell).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'terpene-atlas-download-queue.csv'
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const goTo = (nextView: View, id?: string) => {
    setView(nextView)
    if (id) setSelectedId(id)
    setSidebarOpen(false)
  }

  const openBlast = () => {
    setBlastOpen(true)
  }

  const closeBlast = () => {
    setBlastOpen(false)
  }

  /** Leave the BLAST drawer and jump elsewhere (close it first). */
  const goFromBlast = (nextView: View, id?: string) => {
    setBlastOpen(false)
    goTo(nextView, id)
  }

  /**
   * Drawer CTA: close the drawer and open this BLAST run inside the shared
   * keyword-search result views — the table form, or the map scoped to the
   * hit enzymes. ``payload`` is only echoed here (never applied) because the
   * drawer keeps drawing its own hit list; the shared session is ``blastSession``.
   */
  const enterBlastResults = (payload: BlastPayload, mode: 'table' | 'map') => {
    const session: BlastSession = { id: Date.now(), payload }
    setBlastSession(session)
    setBlastOpen(false)
    if (mode === 'map') {
      setAutoBlastScope({ sessionId: session.id, nonce: Date.now() })
      goTo('home')
    } else {
      goTo('search')
    }
  }

  /** Drop the BLAST session (back to plain keyword library results). */
  const exitBlastSession = () => {
    setBlastSession(null)
    setAutoBlastScope(null)
  }

  /** Table-results banner -> map: scope the home map to the active BLAST hits. */
  const openBlastMap = () => {
    if (!blastSession) return
    setAutoBlastScope({ sessionId: blastSession.id, nonce: Date.now() })
    goTo('home')
  }

  /** Map scope banner -> table: keep the session, jump to its table form. */
  const openBlastTable = () => {
    goTo('search')
  }

  const consumeBlastScope = () => {
    setAutoBlastScope(null)
  }

  const clearFilters = () => {
    setQuery('')
    setSearchKind('all')
    setSelectedSpecies(filterOptions.species[0])
    setSelectedClass(filterOptions.classes[0])
    setSelectedFamily(filterOptions.families[0])
  }

  /** Brand click: land on the plain browse home map with every piece of search
   *  state — keyword query, filters, BLAST session and pending auto-scopes —
   *  dropped. The map's own compound-scope/edge selection is cleared by the
   *  map component itself (it owns that state). */
  const resetHome = () => {
    exitBlastSession()
    setAutoMapSearch(null)
    clearFilters()
    goTo('home')
  }

  return (
    <div className={`app-shell ${view === 'home' || view === 'search' ? 'home-shell' : ''}`}>
      {view !== 'home' && view !== 'search' && <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
        <div className="brand-lockup">
          <div className="brand-mark">
            <Network size={19} strokeWidth={2.4} />
          </div>
          <div>
            <div className="brand-name">Terpene Atlas</div>
            <div className="brand-subtitle">NJU-CHINA 2026</div>
          </div>
          <button className="icon-button sidebar-close" onClick={() => setSidebarOpen(false)} title="Close navigation">
            <X size={17} />
          </button>
        </div>

        <div className="sidebar-section-label">Workspace</div>
        <nav className="primary-nav">
          {navigation.map(({ view: itemView, label, icon: Icon }) => (
            <button key={itemView} className={`nav-item ${view === itemView ? 'active' : ''}`} onClick={() => goTo(itemView)}>
              <Icon size={18} />
              <span>{label}</span>
              {itemView === 'downloads' && queueCount > 0 && <span className="nav-count accent">{queueCount}</span>}
            </button>
          ))}
        </nav>

        <div className="sidebar-section-label sidebar-data-label">Dataset</div>
        <div className="dataset-card">
          <div className="dataset-icon">
            <Database size={16} />
          </div>
          <div className="dataset-copy">
            <strong>Curated terpene reference set</strong>
            <span>{entities.length} records · {routeCount} routes</span>
          </div>
          <span className="status-dot" title="Dataset ready" />
        </div>
        <div className="dataset-meta">
          <span>Last sync</span>
          <strong>2026.07.22</strong>
        </div>

        <div className="sidebar-footer">
          <button className="footer-link">
            <CircleHelp size={16} />
            Data dictionary
          </button>
          <button className="footer-link">
            <Settings2 size={16} />
            Workspace settings
          </button>
          <div className="version-chip">
            {entities.length} entries · {graphNodes.length} nodes
          </div>
        </div>
      </aside>}

      <main className="main-area">
        {view !== 'home' && view !== 'search' && <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setSidebarOpen(true)} title="Open navigation">
            <Menu size={20} />
          </button>
          <div className="crumbs">
            <span>Terpene Atlas</span>
            <ChevronRight size={14} />
            <strong>{viewLabel(view)}</strong>
          </div>
          <div className="topbar-actions">
            <div className="sync-state">
              <span className="status-dot" />
              Live dataset
            </div>
            <button className="topbar-download" onClick={() => goTo('downloads')}>
              <Download size={16} />
              {queueCount > 0 ? `${queueCount} queued` : 'Queue empty'}
            </button>
            <button className="topbar-secondary" onClick={exportQueue} disabled={queueCount === 0}>
              <ArrowDownToLine size={16} />
              Export CSV
            </button>
          </div>
        </header>}

        {view === 'home' && (
          <HomePage
            queueCount={queueCount}
            entityCount={entities.length}
            nodeCount={visibleNodeCount}
            edgeCount={visibleEdgeCount}
            downloadedItems={downloadedItems}
            onOpenSearch={(nextQuery) => {
              const nextSearch = nextQuery || ''
              exitBlastSession()
              setQuery(nextSearch)
              setSearchKind(looksLikeProteinSequence(nextSearch) ? 'enzyme' : 'all')
              goTo('search')
            }}
            onOpenDownloads={() => goTo('downloads')}
            onOpenEnzyme={(id) => goTo('enzyme', id)}
            onOpenBlast={openBlast}
            onOpenBlastTable={openBlastTable}
            onToggleQueue={toggleQueue}
            openRecord={openRecord}
            isQueued={(id) => queuedIds.has(id)}
            autoMapSearch={autoMapSearch}
            onAutoMapSearchConsumed={() => setAutoMapSearch(null)}
            blastSession={blastSession}
            autoBlastScope={autoBlastScope}
            onAutoBlastScopeConsumed={consumeBlastScope}
            onResetHome={resetHome}
          />
        )}

        {view === 'enzyme' && (
          <EnzymeDetailView
            enzymeId={selectedId}
            onBack={() => goTo('home')}
            onToggleQueue={toggleQueue}
            isQueued={(id) => queuedIds.has(id)}
          />
        )}

        {view === 'search' && (
          <SearchResultsPage
            query={query}
            setQuery={setQuery}
            onOpenMap={(nextQuery) => {
              exitBlastSession()
              const trimmed = (nextQuery || '').trim()
              setAutoMapSearch(trimmed ? { query: trimmed, nonce: Date.now() } : null)
              goTo('home')
            }}
            onOpenDownloads={() => goTo('downloads')}
            onOpenEnzyme={(id) => goTo('enzyme', id)}
            onOpenBlast={openBlast}
            onToggleQueue={toggleQueue}
            isQueued={(id) => queuedIds.has(id)}
            queueCount={queueCount}
            blastSession={blastSession}
            onExitBlast={exitBlastSession}
            onOpenBlastMap={openBlastMap}
            onResetHome={resetHome}
          />
        )}

        {view === 'downloads' && (
          <DownloadsPage
            downloadedItems={downloadedItems}
            removeFromQueue={removeFromQueue}
            clearQueue={clearQueue}
            exportQueue={exportQueue}
            onOpenEntity={(id) => { const entity = getEntity(id); if (entity?.kind === 'enzyme') goTo('enzyme', id); else goTo('search', id) }}
            openRecord={openRecord}
          />
        )}
      </main>

      <BlastDrawer
        open={blastOpen}
        onClose={closeBlast}
        onOpenDownloads={() => goFromBlast('downloads')}
        onOpenEnzyme={(id) => goFromBlast('enzyme', id)}
        onToggleQueue={toggleQueue}
        isQueued={(id) => queuedIds.has(id)}
        queueCount={queueCount}
        onOpenResults={enterBlastResults}
      />
    </div>
  )
}

function viewLabel(view: View) {
  switch (view) {
    case 'home':
      return 'Overview'

    case 'search':
      return 'Search library'
    case 'downloads':
      return 'Download queue'
    case 'enzyme':
      return 'Enzyme detail'
  }
}

export default App














