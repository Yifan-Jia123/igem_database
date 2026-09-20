import { useEffect, useMemo, useState } from 'react'
import {
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
import { getExternalRecordUrl, isExportableKind, looksLikeProteinSequence, matchesFilters } from './lib/entities'
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
  const [downloadedIds, setDownloadedIds] = useState<string[]>([])
  const [queuedEntitiesById, setQueuedEntitiesById] = useState<Record<string, Entity>>({})
  const [datasetRevision, setDatasetRevision] = useState(0)
  const [autoMapSearch, setAutoMapSearch] = useState<{ query: string; mode: 'enzyme' | 'pathway'; nonce: number } | null>(null)
  const [blastOpen, setBlastOpen] = useState(false)
  /** Last completed BLAST run, shown through the keyword-search table/map result views. */
  const [blastSession, setBlastSession] = useState<BlastSession | null>(null)
  /** One-shot hand-off telling the home map to scope itself to the active BLAST session. */
  const [autoBlastScope, setAutoBlastScope] = useState<{ sessionId: number; nonce: number } | null>(null)
  /**
   * 搜索集（search set）：**检索范围**，不是显示筛选。空数组 = 全部 ——
   * 沿用全仓库「空数组即不过滤」的约定（没有 `all` 哨兵值）。
   *
   * 刻意不给 localStorage：用户选的是「会话内跨页保持」，刷新浏览器回到「全部」。
   * 它管的是取数，不管下载 —— 下载路径一行都不看这个值。
   */
  const [searchSet, setSearchSet] = useState<string[]>([])

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

  const removeFromQueue = (id: string) => {
    setDownloadedIds((current) => current.filter((item) => item !== id))
    forgetQueuedEntity(id)
  }

  const toggleQueue = (entry: QueueEntry) => {
    const id = typeof entry === 'string' ? entry : entry.id
    const alreadyQueued = downloadedIds.includes(id)

    // Guard at the queue itself: a compound or bare reaction has no exportable
    // payload, so nothing may put one in front of the download pages. Removal is
    // never gated — an id the dataset cannot resolve any more must still leave.
    if (!alreadyQueued) {
      const kind = typeof entry === 'string' ? getEntity(entry)?.kind : entry.kind
      if (!kind || !isExportableKind(kind)) return
    }

    if (alreadyQueued) {
      forgetQueuedEntity(id)
    } else {
      rememberQueuedEntity(entry)
    }
    setDownloadedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]))
  }

  /**
   * 批量入队 —— 合并抽屉的「Queue all N entries」用。
   *
   * 不能循环调 `toggleQueue`：那里每次都要 `downloadedIds.includes(id)`（O(n)）
   * 并展开整个 `queuedEntitiesById`（O(n)），3,595 条串行就是 O(n²) ≈ 1,300 万次
   * 操作 —— 本身就成了新的卡顿源。这里一次 `setDownloadedIds` + 一次
   * `setQueuedEntitiesById`。
   *
   * `isExportableKind` 那道闸门照旧（与 `toggleQueue` 同一判据）：
   * 队列不许出现没有导出载荷的条目。
   * 已经在队列里的条目保持原样（不重复、也不被移除）—— 「Queue all」是补充动作。
   */
  const queueEntities = (entries: Entity[]) => {
    if (entries.length === 0) return
    const incoming = new Map<string, Entity>()
    entries.forEach((entry) => {
      if (!isExportableKind(entry.kind)) return
      if (getEntity(entry.id)) return // 数据集里有的，靠 id 就能解析出来，不必存整份
      if (!incoming.has(entry.id)) incoming.set(entry.id, entry)
    })
    if (incoming.size === 0) return

    setDownloadedIds((current) => [...current, ...[...incoming.keys()].filter((id) => !current.includes(id))])
    setQueuedEntitiesById((current) => {
      const next = { ...current }
      incoming.forEach((entry, id) => {
        if (!next[id]) next[id] = entry
      })
      return next
    })
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

  /** Detail-page search box / "Data Browser": straight to the library results
   *  table, mirroring the home map's own submit behaviour. */
  const openLibrarySearch = (nextQuery: string) => {
    exitBlastSession()
    setQuery(nextQuery || '')
    setSearchKind(looksLikeProteinSequence(nextQuery || '') ? 'enzyme' : 'all')
    goTo('search')
  }

  /** Hand a query to the home map — the Map half of every page's Map|Table
   *  toggle, and the destination of their Pathway half.
   *
   * `mode` selects what the map should do with it: an enzyme search run on
   * arrival, or the pathway composer opened for a chain that no single keyword
   * could express. Pathway mode therefore carries an empty query without being
   * a no-op, which is why the "nothing to search for" guard only applies to the
   * enzyme half. */
  const openMapSearch = (nextQuery: string, mode: 'enzyme' | 'pathway' = 'enzyme') => {
    exitBlastSession()
    const trimmed = (nextQuery || '').trim()
    setAutoMapSearch(trimmed || mode === 'pathway' ? { query: trimmed, mode, nonce: Date.now() } : null)
    goTo('home')
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

  // The workspace sidebar only renders on views that keep the workspace chrome,
  // which narrows `view` at the point of use — but its nav compares against
  // every destination, so it needs the unnarrowed value.
  const currentView: View = view

  return (
    <div className={`app-shell ${view === 'home' || view === 'search' ? 'home-shell' : ''}`}>
      {/* The enzyme detail and downloads pages bring their own chrome (a
          home-style top nav, plus a module rail on the detail page), so the
          workspace sidebar/topbar stay out of their way. On the downloads page
          the topbar's "N queued" button was the worst of both: it rendered
          there but only navigated to the page it was already on. */}
      {view !== 'home' && view !== 'search' && view !== 'enzyme' && view !== 'downloads' && <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
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
            <button key={itemView} className={`nav-item ${currentView === itemView ? 'active' : ''}`} onClick={() => goTo(itemView)}>
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
        {view !== 'home' && view !== 'search' && view !== 'enzyme' && view !== 'downloads' && <header className="topbar">
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
            onQueueMany={queueEntities}
            openRecord={openRecord}
            isQueued={(id) => queuedIds.has(id)}
            autoMapSearch={autoMapSearch}
            onAutoMapSearchConsumed={() => setAutoMapSearch(null)}
            blastSession={blastSession}
            autoBlastScope={autoBlastScope}
            onAutoBlastScopeConsumed={consumeBlastScope}
            onResetHome={resetHome}
            searchSet={searchSet}
            onSearchSetChange={setSearchSet}
          />
        )}

        {view === 'enzyme' && (
          <EnzymeDetailView
            enzymeId={selectedId}
            onBack={() => goTo('home')}
            onToggleQueue={toggleQueue}
            isQueued={(id) => queuedIds.has(id)}
            queueCount={queueCount}
            onOpenDownloads={() => goTo('downloads')}
            onOpenBlast={openBlast}
            onOpenSearch={openLibrarySearch}
            onOpenMapScoped={openMapSearch}
            onOpenMap={(nextQuery) => openMapSearch(nextQuery)}
            onOpenPathwaySearch={() => openMapSearch('', 'pathway')}
            searchSet={searchSet}
            onSearchSetChange={setSearchSet}
          />
        )}

        {view === 'search' && (
          <SearchResultsPage
            query={query}
            setQuery={setQuery}
            onOpenMap={(nextQuery) => openMapSearch(nextQuery)}
            onOpenPathwaySearch={() => openMapSearch('', 'pathway')}
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
            searchSet={searchSet}
            onSearchSetChange={setSearchSet}
          />
        )}

        {view === 'downloads' && (
          <DownloadsPage
            downloadedItems={downloadedItems}
            removeFromQueue={removeFromQueue}
            clearQueue={clearQueue}
            // The queue hands back the entity it holds rather than an id to
            // re-resolve: `getEntity` reads the graph sample, which is 60
            // compound nodes and no enzymes, so every enzyme queued from the
            // search page missed the lookup and fell through to the search view.
            // Every row in that tab is an enzyme, so the destination is settled.
            onOpenEntity={(entity) => goTo('enzyme', entity.id)}
            openRecord={openRecord}
            queueCount={queueCount}
            onResetHome={resetHome}
            onOpenSearch={openLibrarySearch}
            onOpenBlast={openBlast}
            onOpenMap={(nextQuery) => openMapSearch(nextQuery)}
            onOpenPathwaySearch={() => openMapSearch('', 'pathway')}
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
        // BLAST 算搜索，所以搜索集对它**真的**生效（后端按搜索集另建序列库）。
        searchSet={searchSet}
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














