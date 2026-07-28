import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ArrowDownToLine,
  ArrowUpRight,
  Beaker,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Database,
  Dna,
  Download,
  ExternalLink,
  FlaskConical,
  Focus,
  Menu,
  Network,
  PanelRight,
  RefreshCw,
  Search,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { entities as mockEntities, filterOptions as mockFilterOptions, graphEdges as mockGraphEdges, graphNodes as mockGraphNodes } from './data'
import { loadApiDataset, searchApiEntries, searchHomologyEntries } from './api'
import { CompoundGraphHome, EnzymeDetailView } from './graphExperience'
import type { Entity, EntityKind, GraphNode } from './types'

let entities = mockEntities
let filterOptions = mockFilterOptions
let graphEdges = mockGraphEdges
let graphNodes = mockGraphNodes

const getEntity = (id: string) => entities.find((entity) => entity.id === id)

type View = 'home' | 'search' | 'downloads' | 'enzyme'
type SearchKind = 'all' | EntityKind
type HomeSearchMode = 'enzymeItems' | 'pathways' | 'blast' | 'mapsearch'

const kindLabels: Record<EntityKind, string> = {
  compound: 'Compound',
  enzyme: 'Enzyme',
  reaction: 'Reaction',
}

const kindIcons: Record<EntityKind, typeof FlaskConical> = {
  compound: Beaker,
  enzyme: Dna,
  reaction: FlaskConical,
}

const navigation = [
  { view: 'home', label: 'Overview', icon: Sparkles },
  { view: 'search', label: 'Search library', icon: Search },
  { view: 'downloads', label: 'Download queue', icon: Download },
] as const

const homeSearchModes: Array<{ id: HomeSearchMode; label: string }> = [
  { id: 'enzymeItems', label: 'Enzyme items' },
  { id: 'pathways', label: 'Pathways' },
  { id: 'blast', label: 'BLAST' },
  { id: 'mapsearch', label: 'Enzyme items (mapsearch)' },
]

type FilterState = {
  query: string
  searchKind: SearchKind
  species: string
  compoundClass: string
  enzymeFamily: string
}

function App() {
  const [view, setView] = useState<View>('home')
  const [query, setQuery] = useState('')
  const [searchKind, setSearchKind] = useState<SearchKind>('all')
  const [selectedId, setSelectedId] = useState<string | null>('CHEBI:15377')
  const [selectedSpecies, setSelectedSpecies] = useState(filterOptions.species[0])
  const [selectedClass, setSelectedClass] = useState(filterOptions.classes[0])
  const [selectedFamily, setSelectedFamily] = useState(filterOptions.families[0])
  const [zoom, setZoom] = useState(1)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [downloadedIds, setDownloadedIds] = useState<string[]>(['CHEBI:17115', 'ENZ:Q9ZSY2'])
  const [showEdgeLabels, setShowEdgeLabels] = useState(true)
  const [apiSearchResults, setApiSearchResults] = useState<Entity[] | null>(null)
  const [apiSearchLoading, setApiSearchLoading] = useState(false)
  const [apiSearchError, setApiSearchError] = useState<string | null>(null)
  const [, setDatasetRevision] = useState(0)

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

  useEffect(() => {
    const trimmedQuery = query.trim()
    if (!trimmedQuery || searchKind === 'compound' || searchKind === 'reaction') {
      setApiSearchResults(null)
      setApiSearchLoading(false)
      setApiSearchError(null)
      return
    }

    let cancelled = false
    setApiSearchLoading(true)
    setApiSearchError(null)

    const timer = window.setTimeout(() => {
      const organismName = selectedSpecies && selectedSpecies !== filterOptions.species[0] ? selectedSpecies : undefined

      const isHomologySearch = looksLikeProteinSequence(trimmedQuery)
      const searchPromise = isHomologySearch
        ? searchHomologyEntries(trimmedQuery)
        : searchApiEntries({ q: trimmedQuery, organismName })

      searchPromise
        .then((results) => {
          if (cancelled) return

          if (results.length > 0) {
            const knownIds = new Set(entities.map((entity) => entity.id))
            entities = [...entities, ...results.filter((entity) => !knownIds.has(entity.id))]
            setSelectedId((current) => (current && results.some((entity) => entity.id === current) ? current : results[0].id))
            setDatasetRevision((revision) => revision + 1)
          }

          setApiSearchResults(results)
        })
        .catch((error) => {
          if (cancelled) return
          console.warn('Backend search failed; showing loaded graph records.', error)
          setApiSearchResults(null)
          setApiSearchError(isHomologySearch ? 'Homology search unavailable; showing loaded graph records.' : 'Backend search unavailable; showing loaded graph records.')
        })
        .finally(() => {
          if (!cancelled) setApiSearchLoading(false)
        })
    }, 250)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [query, searchKind, selectedSpecies])

  const filters = { query, searchKind, species: selectedSpecies, compoundClass: selectedClass, enzymeFamily: selectedFamily }
  const selected = selectedId ? getEntity(selectedId) : undefined
  const downloadedItems = useMemo(() => downloadedIds.map((id) => getEntity(id)).filter((entity): entity is Entity => Boolean(entity)), [downloadedIds])
  const queuedIds = useMemo(() => new Set(downloadedIds), [downloadedIds])

  const localFilteredEntities = useMemo(() => entities.filter((entity) => matchesFilters(entity, filters)), [filters])
  const filteredEntities = apiSearchResults ? apiSearchResults.filter((entity) => matchesFilters(entity, { ...filters, query: '' })) : localFilteredEntities
  const visibleNodeIds = useMemo(
    () =>
      new Set(
        graphNodes
          .filter((node) => matchesFilters(getEntity(node.id), filters))
          .map((node) => node.id),
      ),
    [filters],
  )

  const routeCount = new Set(graphEdges.map((edge) => edge.edgeGroupId || edge.reactionId)).size
  const queueCount = downloadedItems.length
  const visibleNodeCount = visibleNodeIds.size
  const visibleEdgeCount = graphEdges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)).length

  const addToQueue = (id: string) => {
    setDownloadedIds((current) => (current.includes(id) ? current : [...current, id]))
  }

  const removeFromQueue = (id: string) => {
    setDownloadedIds((current) => current.filter((item) => item !== id))
  }

  const toggleQueue = (id: string) => {
    setDownloadedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]))
  }

  const clearQueue = () => {
    setDownloadedIds([])
  }

  const openRecord = (entity: Entity) => {
    const url = getExternalRecordUrl(entity)
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

  const clearFilters = () => {
    setQuery('')
    setSearchKind('all')
    setSelectedSpecies(filterOptions.species[0])
    setSelectedClass(filterOptions.classes[0])
    setSelectedFamily(filterOptions.families[0])
  }

  return (
    <div className={`app-shell ${view === 'home' ? 'home-shell' : ''}`}>
      {view !== 'home' && <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
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
        {view !== 'home' && <header className="topbar">
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
          <HomeView
            queueCount={queueCount}
            entityCount={entities.length}
            nodeCount={visibleNodeCount}
            edgeCount={visibleEdgeCount}
            downloadedItems={downloadedItems}
            onOpenSearch={(nextQuery) => {
              const nextSearch = nextQuery || ''
              setQuery(nextSearch)
              setSearchKind(looksLikeProteinSequence(nextSearch) ? 'enzyme' : 'all')
              goTo('search')
            }}
            onOpenNetwork={() => goTo('home')}
            onOpenDownloads={() => goTo('downloads')}
            onOpenEnzyme={(id) => goTo('enzyme', id)}
            onToggleQueue={toggleQueue}
            openRecord={openRecord}
            isQueued={(id) => queuedIds.has(id)}
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
          <SearchView
            query={query}
            setQuery={setQuery}
            searchKind={searchKind}
            setSearchKind={setSearchKind}
            selectedSpecies={selectedSpecies}
            setSelectedSpecies={setSelectedSpecies}
            selectedClass={selectedClass}
            setSelectedClass={setSelectedClass}
            selectedFamily={selectedFamily}
            setSelectedFamily={setSelectedFamily}
            clearFilters={clearFilters}
            filteredEntities={filteredEntities}
            apiSearchLoading={apiSearchLoading}
            apiSearchError={apiSearchError}
            selectedId={selectedId}
            setSelectedId={setSelectedId}
            addToQueue={toggleQueue}
            openRecord={openRecord}
            isQueued={selected ? queuedIds.has(selected.id) : false}
          />
        )}

        {view === 'downloads' && (
          <DownloadsView
            downloadedItems={downloadedItems}
            removeFromQueue={removeFromQueue}
            clearQueue={clearQueue}
            exportQueue={exportQueue}
            onOpenNetwork={() => goTo('home')}
            onOpenSearch={() => goTo('search')}
            onOpenEntity={(id) => { const entity = getEntity(id); if (entity?.kind === 'enzyme') goTo('enzyme', id); else goTo('search', id) }}
            openRecord={openRecord}
          />
        )}
      </main>
    </div>
  )
}

function HomeView({
  queueCount,
  entityCount: _entityCount,
  nodeCount: _nodeCount,
  edgeCount: _edgeCount,
  downloadedItems: _downloadedItems,
  onOpenSearch,
  onOpenNetwork,
  onOpenDownloads,
  onOpenEnzyme,
  onToggleQueue,
  openRecord: _openRecord,
  isQueued,
}: {
  queueCount: number
  entityCount: number
  nodeCount: number
  edgeCount: number
  downloadedItems: Entity[]
  onOpenSearch: (query?: string) => void
  onOpenNetwork: () => void
  onOpenDownloads: () => void
  onOpenEnzyme: (id: string) => void
  onToggleQueue: (id: string) => void
  openRecord: (entity: Entity) => void
  isQueued: (id: string) => boolean
}) {
  return (
    <CompoundGraphHome
      onOpenSearch={onOpenSearch}
      onOpenNetwork={onOpenNetwork}
      onOpenDownloads={onOpenDownloads}
      onOpenEnzyme={onOpenEnzyme}
      onToggleQueue={onToggleQueue}
      isQueued={isQueued}
      queueCount={queueCount}
    />
  )
}
function NetworkView({
  query,
  setQuery,
  selectedId,
  setSelectedId,
  selectedSpecies,
  setSelectedSpecies,
  selectedClass,
  setSelectedClass,
  selectedFamily,
  setSelectedFamily,
  clearFilters,
  zoom,
  setZoom,
  showEdgeLabels,
  setShowEdgeLabels,
  selected,
  addToQueue,
  openRecord,
  visibleNodeIds,
  onSelectRelated,
  isQueued,
}: {
  query: string
  setQuery: (value: string) => void
  selectedId: string | null
  setSelectedId: (value: string | null) => void
  selectedSpecies: string
  setSelectedSpecies: (value: string) => void
  selectedClass: string
  setSelectedClass: (value: string) => void
  selectedFamily: string
  setSelectedFamily: (value: string) => void
  clearFilters: () => void
  zoom: number
  setZoom: (value: number) => void
  showEdgeLabels: boolean
  setShowEdgeLabels: (value: boolean) => void
  selected?: Entity
  addToQueue: (id: string) => void
  openRecord: (entity: Entity) => void
  visibleNodeIds: Set<string>
  onSelectRelated: (value: string | null) => void
  isQueued: boolean
}) {
  return (
    <div className="content-wrap workspace-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow">
            <Sparkles size={14} />
            Dataset explorer
          </div>
          <h1>Network map</h1>
          <p>Track precursor, enzyme, and product relationships across the curated terpene graph.</p>
        </div>
        <div className="heading-actions">
          <span className="data-updated">
            <span className="status-dot" />
            {visibleNodeIds.size} nodes visible
          </span>
          <button className="outline-button" onClick={clearFilters}>
            <RefreshCw size={15} />
            Reset filters
          </button>
        </div>
      </section>

      <section className="stats-row">
      <Stat label="Total records" value={entities.length.toString()} delta="Curated entries" icon={<Database size={18} />} tone="amber" />
      <Stat label="Visible nodes" value={visibleNodeIds.size.toString()} delta="After filtering" icon={<Network size={18} />} tone="purple" />
      <Stat label="Visible edges" value={String(visibleEdgeCount(visibleNodeIds))} delta="Linked pathways" icon={<FlaskConical size={18} />} tone="coral" />
      <Stat label="Current selection" value={selected ? selected.id : 'None'} delta="Details panel target" icon={<PanelRight size={18} />} tone="teal" />
      </section>

      <section className="workspace-grid">
        <div className="network-column">
          <div className="filter-toolbar">
            <div className="search-field">
              <Search size={17} />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search compounds, enzymes, reactions, or IDs"
              />
              <kbd>/</kbd>
            </div>
            <button className="filter-toggle">
              <SlidersHorizontal size={16} />
              Filters
            </button>
          </div>

          <div className="filter-row">
            <FilterSelect label="Species" value={selectedSpecies} options={filterOptions.species} onChange={setSelectedSpecies} />
            <FilterSelect label="Compound class" value={selectedClass} options={filterOptions.classes} onChange={setSelectedClass} />
            <FilterSelect label="Enzyme family" value={selectedFamily} options={filterOptions.families} onChange={setSelectedFamily} />
          </div>

          <div className="network-panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">
                  <span className="live-line" />
                  Terpene Atlas
                </div>
                <h2>Relationship overview</h2>
              </div>
              <div className="panel-header-actions">
                <span className="node-count">
                  {visibleNodeIds.size} nodes · {visibleEdgeCount(visibleNodeIds)} edges
                </span>
                <button className="icon-button" title="Focus selected node" onClick={() => setZoom(1)}>
                  <Focus size={17} />
                </button>
                <button className="icon-button" title="Network options">
                  <Settings2 size={17} />
                </button>
              </div>
            </div>

            <div className="network-canvas-wrap">
              <NetworkCanvas
                selectedId={selectedId}
                setSelectedId={setSelectedId}
                zoom={zoom}
                showEdgeLabels={showEdgeLabels}
                visibleNodeIds={visibleNodeIds}
              />
              <div className="zoom-controls">
                <button className="icon-button" title="Zoom in" onClick={() => setZoom(Math.min(1.45, zoom + 0.1))}>
                  <ZoomIn size={16} />
                </button>
                <span>{Math.round(zoom * 100)}%</span>
                <button className="icon-button" title="Zoom out" onClick={() => setZoom(Math.max(0.8, zoom - 0.1))}>
                  <ZoomOut size={16} />
                </button>
              </div>
              <div className="canvas-legend">
                <LegendDot tone="teal" label="Compound" />
                <LegendDot tone="amber" label="Enzyme" />
                <LegendDot tone="coral" label="Product" />
                <label className="legend-toggle">
                  <input type="checkbox" checked={showEdgeLabels} onChange={(event) => setShowEdgeLabels(event.target.checked)} />
                  Labels
                </label>
              </div>
            </div>

            <div className="network-footer">
              <span>
                <span className="legend-line solid" />
                Curated relation
              </span>
              <span>
                <span className="legend-line dashed" />
                Secondary product
              </span>
              <button className="text-button">
                View pathway map
                <ArrowUpRight size={14} />
              </button>
            </div>
          </div>
        </div>

        <DetailPanel
          entity={selected}
          isQueued={isQueued}
          onToggleQueue={addToQueue}
          onOpenRecord={openRecord}
          onSelectRelated={onSelectRelated}
          onClose={() => setSelectedId(null)}
        />
      </section>
    </div>
  )
}

function SearchView({
  query,
  setQuery,
  searchKind,
  setSearchKind,
  selectedSpecies,
  setSelectedSpecies,
  selectedClass,
  setSelectedClass,
  selectedFamily,
  setSelectedFamily,
  clearFilters,
  filteredEntities,
  apiSearchLoading,
  apiSearchError,
  selectedId,
  setSelectedId,
  addToQueue,
  openRecord,
  isQueued,
}: {
  query: string
  setQuery: (value: string) => void
  searchKind: SearchKind
  setSearchKind: (value: SearchKind) => void
  selectedSpecies: string
  setSelectedSpecies: (value: string) => void
  selectedClass: string
  setSelectedClass: (value: string) => void
  selectedFamily: string
  setSelectedFamily: (value: string) => void
  clearFilters: () => void
  filteredEntities: Entity[]
  apiSearchLoading: boolean
  apiSearchError: string | null
  selectedId: string | null
  setSelectedId: (value: string | null) => void
  addToQueue: (id: string) => void
  openRecord: (entity: Entity) => void
  isQueued: boolean
}) {
  const selected = selectedId ? getEntity(selectedId) : undefined

  return (
    <div className="content-wrap search-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow">
            <Search size={14} />
            Library search
          </div>
          <h1>Search records</h1>
          <p>Search by name, identifier, species, family, or keyword and inspect the matching record on the right.</p>
        </div>
        <div className="heading-actions">
          <span className="result-total">{filteredEntities.length} results</span>
          <button className="outline-button" onClick={clearFilters}>
            <RefreshCw size={15} />
            Clear filters
          </button>
        </div>
      </section>

      <section className="search-shell">
        <div className="search-main">
          <div className="search-hero">
            <Search size={20} />
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search linalool, Q9ZSY2, RHEA:24464, or any related term"
            />
            <span>/</span>
          </div>

          <div className="search-toolbar">
            <div className="search-kind-group">
              {(['all', 'compound', 'enzyme', 'reaction'] as const).map((kind) => (
                <button key={kind} className={`chip-button ${searchKind === kind ? 'active' : ''}`} onClick={() => setSearchKind(kind)}>
                  {kind === 'all' ? 'All types' : kindLabels[kind]}
                </button>
              ))}
            </div>
            <div className="search-summary">
              {apiSearchLoading ? 'Searching backend...' : <>Showing <strong>{filteredEntities.length}</strong> curated entries</>}
              {apiSearchError && <span>{apiSearchError}</span>}
            </div>
          </div>

          <div className="filter-row search-filter-row">
            <FilterSelect label="Species" value={selectedSpecies} options={filterOptions.species} onChange={setSelectedSpecies} />
            <FilterSelect label="Compound class" value={selectedClass} options={filterOptions.classes} onChange={setSelectedClass} />
            <FilterSelect label="Enzyme family" value={selectedFamily} options={filterOptions.families} onChange={setSelectedFamily} />
          </div>

          <div className="result-list">
            {filteredEntities.map((entity) => (
              <SearchResult
                key={entity.id}
                entity={entity}
                selected={selectedId === entity.id}
                onSelect={() => setSelectedId(entity.id)}
                addToQueue={addToQueue}
                openRecord={openRecord}
              />
            ))}
          </div>
        </div>

        <DetailPanel
          entity={selected}
          isQueued={isQueued}
          onToggleQueue={addToQueue}
          onOpenRecord={openRecord}
          onSelectRelated={setSelectedId}
          onClose={() => setSelectedId(null)}
        />
      </section>
    </div>
  )
}

function DownloadsView({
  downloadedItems,
  removeFromQueue,
  clearQueue,
  exportQueue,
  onOpenNetwork,
  onOpenSearch,
  onOpenEntity,
  openRecord,
}: {
  downloadedItems: Entity[]
  removeFromQueue: (id: string) => void
  clearQueue: () => void
  exportQueue: () => void
  onOpenNetwork: () => void
  onOpenSearch: () => void
  onOpenEntity: (id: string) => void
  openRecord: (entity: Entity) => void
}) {
  const counts = {
    compound: downloadedItems.filter((item) => item.kind === 'compound').length,
    enzyme: downloadedItems.filter((item) => item.kind === 'enzyme').length,
    reaction: downloadedItems.filter((item) => item.kind === 'reaction').length,
  }

  return (
    <div className="content-wrap downloads-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow">
            <Download size={14} />
            Saved output
          </div>
          <h1>Download queue</h1>
          <p>Collect records here, export them to CSV, or remove anything you no longer need.</p>
        </div>
        <div className="heading-actions">
          <button className="outline-button" onClick={exportQueue} disabled={downloadedItems.length === 0}>
            <ArrowDownToLine size={15} />
            Export CSV
          </button>
          <button className="outline-button" onClick={clearQueue} disabled={downloadedItems.length === 0}>
            <X size={15} />
            Clear queue
          </button>
        </div>
      </section>

      <section className="downloads-shell">
        <div className="section-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">
                <span className="live-line" />
                Queue contents
              </div>
              <h2>{downloadedItems.length} queued records</h2>
            </div>
            <button className="icon-button" title="Open search" onClick={onOpenSearch}>
              <Search size={17} />
            </button>
          </div>

          {downloadedItems.length > 0 ? (
            <div className="download-list">
              {downloadedItems.map((entity) => {
                const Icon = kindIcons[entity.kind]
                return (
                  <article key={entity.id} className="download-row">
                    <span className={`queue-icon ${entity.kind}`}>
                      <Icon size={15} />
                    </span>
                    <span className="queue-copy">
                      <strong>{entity.name}</strong>
                      <small>{entity.subtitle}</small>
                    </span>
                    <span className="queue-kind">{kindLabels[entity.kind]}</span>
                    <button className="icon-button" title="Open record" onClick={() => openRecord(entity)}>
                      <ExternalLink size={15} />
                    </button>
                    <button className="icon-button" title="Open in network" onClick={() => onOpenEntity(entity.id)}>
                      <ArrowUpRight size={15} />
                    </button>
                    <button className="icon-button" title="Remove from queue" onClick={() => removeFromQueue(entity.id)}>
                      <X size={15} />
                    </button>
                  </article>
                )
              })}
            </div>
          ) : (
            <div className="empty-home">
              <p>The queue is empty.</p>
              <button className="secondary-button" onClick={onOpenNetwork}>
                <Network size={15} />
                Back to atlas
              </button>
            </div>
          )}
        </div>

        <aside className="downloads-summary">
          <div className="section-panel summary-panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">
                  <span className="live-line" />
                  Queue summary
                </div>
                <h2>Type breakdown</h2>
              </div>
            </div>
            <div className="summary-list">
              <SummaryRow label="Compounds" value={counts.compound} />
              <SummaryRow label="Enzymes" value={counts.enzyme} />
              <SummaryRow label="Reactions" value={counts.reaction} />
            </div>
          </div>

          <div className="section-panel summary-panel">
            <div className="panel-header">
              <div>
                <div className="panel-kicker">
                  <span className="live-line" />
                  Quick actions
                </div>
                <h2>Next steps</h2>
              </div>
            </div>
            <div className="summary-actions">
              <button className="secondary-button" onClick={onOpenSearch}>
                <Search size={15} />
                Add more records
              </button>
              <button className="secondary-button" onClick={onOpenNetwork}>
                <Network size={15} />
                Return home
              </button>
            </div>
          </div>
        </aside>
      </section>
    </div>
  )
}

function DetailPanel({
  entity,
  isQueued,
  onToggleQueue,
  onOpenRecord,
  onSelectRelated,
  onClose,
}: {
  entity?: Entity
  isQueued: boolean
  onToggleQueue: (id: string) => void
  onOpenRecord: (entity: Entity) => void
  onSelectRelated: (value: string | null) => void
  onClose: () => void
}) {
  if (!entity) {
    return (
      <aside className="detail-panel empty-detail">
        <PanelRight size={27} />
        <h3>Select a record</h3>
        <p>Click a compound, enzyme, or reaction to open the full detail panel.</p>
      </aside>
    )
  }

  const Icon = kindIcons[entity.kind]
  const metaRows = [
    entity.species ? { label: 'Species', value: entity.species } : null,
    entity.compoundClass ? { label: 'Compound class', value: entity.compoundClass } : null,
    entity.enzymeFamily ? { label: 'Enzyme family', value: entity.enzymeFamily } : null,
    ...entity.fields,
  ].filter(Boolean) as Array<{ label: string; value: string }>

  return (
    <aside className="detail-panel">
      <div className="detail-topline">
        <span className={`entity-type ${entity.kind}`}>
          <Icon size={14} />
          {kindLabels[entity.kind]}
        </span>
        <div className="detail-top-actions">
          <button className="icon-button" title="Open record" onClick={() => onOpenRecord(entity)}>
            <ExternalLink size={16} />
          </button>
          <button className="icon-button" title={isQueued ? 'Remove from queue' : 'Add to queue'} onClick={() => onToggleQueue(entity.id)}>
            {isQueued ? <X size={16} /> : <ArrowDownToLine size={16} />}
          </button>
          <button className="icon-button" title="Close detail" onClick={onClose}>
            <X size={17} />
          </button>
        </div>
      </div>

      <div className="detail-title">
        <div>
          <h2>{entity.name}</h2>
          <p>{entity.subtitle}</p>
        </div>
        <span className={`detail-mark ${entity.kind}`}>
          <Icon size={21} />
        </span>
      </div>

      <p className="detail-description">{entity.description}</p>

      <div className="tag-row">
        {entity.tags.map((tag) => (
          <span className="tag" key={tag}>
            {tag}
          </span>
        ))}
      </div>

      {entity.imageLabel && (
        <div className="structure-preview">
          {entity.imageUrl ? (
            <img className="structure-image" src={entity.imageUrl} alt={`${entity.name} 2D structure`} />
          ) : (
            <>
              <div className="structure-grid" />
              <div className="structure-placeholder">
                <span className="ring-shape" />
                <span className="bond bond-a" />
                <span className="bond bond-b" />
                <span className="structure-label">{entity.imageLabel}</span>
              </div>
            </>
          )}
          <button className="preview-link" onClick={() => window.open(entity.imageUrl || getExternalRecordUrl(entity), '_blank', 'noopener,noreferrer')}>
            Open asset
            <ExternalLink size={12} />
          </button>
        </div>
      )}

      <div className="field-list">
        {metaRows.map((field) => (
          <div className="field-row" key={field.label}>
            <span>{field.label}</span>
            <strong>{field.value}</strong>
          </div>
        ))}
      </div>

      <div className="related-section">
        <div className="section-title">
          <span>Related entities</span>
          <span className="related-count">{entity.related.length}</span>
        </div>
        {entity.related.map((item) => {
          const RelatedIcon = kindIcons[item.kind]
          return (
            <button className="related-row" key={item.id} onClick={() => onSelectRelated(item.id)}>
              <span className={`related-icon ${item.kind}`}>
                <RelatedIcon size={14} />
              </span>
              <span>
                <strong>{item.name}</strong>
                <small>{item.id}</small>
              </span>
              <ChevronRight size={15} />
            </button>
          )
        })}
      </div>

      <div className="detail-actions">
        <button className="primary-button" onClick={() => onToggleQueue(entity.id)}>
          <ArrowDownToLine size={15} />
          {isQueued ? 'Remove from queue' : 'Add to queue'}
        </button>
        <button className="icon-button bordered" title="Open record" onClick={() => onOpenRecord(entity)}>
          <ExternalLink size={16} />
        </button>
      </div>
    </aside>
  )
}

function SearchResult({
  entity,
  selected,
  onSelect,
  addToQueue,
  openRecord,
}: {
  entity: Entity
  selected: boolean
  onSelect: () => void
  addToQueue: (id: string) => void
  openRecord: (entity: Entity) => void
}) {
  const Icon = kindIcons[entity.kind]

  return (
    <article className={`search-result ${selected ? 'selected' : ''}`}>
      <button className="result-main" onClick={onSelect}>
        <span className={`result-icon ${entity.kind}`}>
          <Icon size={18} />
        </span>
        <span className="result-copy">
          <span className="result-name">{entity.name}</span>
          <span className="result-subtitle">{entity.subtitle}</span>
          <span className="result-description">{entity.description}</span>
        </span>
      </button>
      <div className="result-actions">
        <span className="result-tag">{kindLabels[entity.kind]}</span>
        <button className="icon-button" title="Open record" onClick={() => openRecord(entity)}>
          <ExternalLink size={16} />
        </button>
        <button className="icon-button" title="Add to queue" onClick={() => addToQueue(entity.id)}>
          <Download size={16} />
        </button>
        <ChevronRight size={17} className="result-chevron" />
      </div>
    </article>
  )
}

function NetworkCanvas({
  selectedId,
  setSelectedId,
  zoom,
  showEdgeLabels,
  visibleNodeIds,
}: {
  selectedId: string | null
  setSelectedId: (value: string) => void
  zoom: number
  showEdgeLabels: boolean
  visibleNodeIds: Set<string>
}) {
  const getNode = (id: string) => graphNodes.find((node) => node.id === id)!
  const point = (node: GraphNode) => ({ x: 48 + node.x * 5.85, y: 14 + node.y * 1.42 })

  const visibleEdges = graphEdges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))
  const visibleNodes = graphNodes.filter((node) => visibleNodeIds.has(node.id))

  return (
    <svg className="network-canvas" viewBox="0 0 620 138" role="img" aria-label="Terpene metabolic network">
      <defs>
        <filter id="node-shadow" x="-40%" y="-40%" width="180%" height="180%">
          <feDropShadow dx="0" dy="3" stdDeviation="3" floodColor="#07243a" floodOpacity="0.18" />
        </filter>
        <marker id="arrow" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto">
          <path d="M0,0 L7,3.5 L0,7 z" fill="#98adbd" />
        </marker>
      </defs>
      <g className="grid-lines">
        <path d="M0 34 H620 M0 69 H620 M0 104 H620" />
        <path d="M125 0 V138 M280 0 V138 M435 0 V138" />
      </g>
      <g transform={`translate(${310 - 310 * zoom} ${69 - 69 * zoom}) scale(${zoom})`}>
        {visibleEdges.map((edge) => {
          const source = point(getNode(edge.source))
          const target = point(getNode(edge.target))
          const midX = (source.x + target.x) / 2
          const midY = (source.y + target.y) / 2 + (edge.curved ? 17 : 0)
          const d = edge.curved ? `M ${source.x} ${source.y} Q ${midX} ${midY} ${target.x} ${target.y}` : `M ${source.x} ${source.y} L ${target.x} ${target.y}`
          const highlighted = selectedId === edge.source || selectedId === edge.target || selectedId === edge.reactionId

          return (
            <g key={edge.id} className={`graph-edge ${highlighted ? 'highlighted' : ''}`}>
              <path d={d} markerEnd="url(#arrow)" />
              <path d={d} className="edge-hit" onClick={() => setSelectedId(edge.reactionId)} />
              {showEdgeLabels && (
                <text x={midX} y={edge.curved ? midY - 4 : midY - 3} className="edge-label">
                  {edge.label.replace('RHEA:', 'R-')}
                </text>
              )}
            </g>
          )
        })}

        {visibleNodes.map((node) => {
          const { x, y } = point(node)
          const selected = node.id === selectedId
          return (
            <g key={node.id} className={`graph-node ${node.tone} ${selected ? 'selected' : ''}`} onClick={() => setSelectedId(node.id)} tabIndex={0} role="button">
              <circle cx={x} cy={y} r={node.kind === 'enzyme' ? 11 : 10} filter="url(#node-shadow)" />
              <text x={x} y={y + 3.5} className="node-short-label">
                {node.shortLabel}
              </text>
              <text x={x} y={y + 21} className="node-name">
                {node.shortLabel}
              </text>
              <text x={x} y={y + 30} className="node-meta">
                {node.meta}
              </text>
            </g>
          )
        })}
      </g>
    </svg>
  )
}

type HomeMapNode = {
  id: string
  label: string
  x: number
  y: number
  size: number
  kind: EntityKind
}

type HomeMapEdge = {
  id: string
  sourceId: string
  targetId: string
  label: string
  entityId: string
  reactionId: string
  multi: boolean
}

type HomeEdgeCard = {
  id: string
  entityId: string
  name: string
  organism: string
  equation: string
  recordCode: string
  reactionId: string
  extra?: string
}

function buildHomeMap(nodes: GraphNode[], edges: typeof graphEdges, activeSources: string[]) {
  const sourceSet = new Set(activeSources.map(normalizeSourceLabel))
  const nodeById = new Map(nodes.map((node) => [node.id, node]))
  const candidateEdges = edges.filter((edge) => nodeById.has(edge.source) && nodeById.has(edge.target))
  const sourceFilteredEdges = sourceSet.size
    ? candidateEdges.filter((edge) => {
        const enzyme = getEntity(edge.enzymeId)
        return enzyme?.tags.some((tag) => sourceSet.has(normalizeSourceLabel(tag)))
      })
    : candidateEdges
  const displayEdges = (sourceFilteredEdges.length > 0 ? sourceFilteredEdges : candidateEdges).slice(0, 28)
  const includedNodeIds = new Set(displayEdges.flatMap((edge) => [edge.source, edge.target]))
  const displayNodes = (includedNodeIds.size > 0 ? nodes.filter((node) => includedNodeIds.has(node.id)) : nodes).slice(0, 22)
  const displayNodeIds = new Set(displayNodes.map((node) => node.id))
  const boundedEdges = displayEdges.filter((edge) => displayNodeIds.has(edge.source) && displayNodeIds.has(edge.target))
  const degrees = new Map<string, number>()

  boundedEdges.forEach((edge) => {
    degrees.set(edge.source, (degrees.get(edge.source) || 0) + 1)
    degrees.set(edge.target, (degrees.get(edge.target) || 0) + 1)
  })

  const xs = displayNodes.map((node) => node.x)
  const ys = displayNodes.map((node) => node.y)
  const minX = Math.min(...xs, 0)
  const maxX = Math.max(...xs, 1)
  const minY = Math.min(...ys, 0)
  const maxY = Math.max(...ys, 1)
  const spanX = Math.max(maxX - minX, 1)
  const spanY = Math.max(maxY - minY, 1)
  const pairCounts = new Map<string, number>()

  boundedEdges.forEach((edge) => {
    const pairKey = `${edge.source}->${edge.target}`
    pairCounts.set(pairKey, (pairCounts.get(pairKey) || 0) + 1)
  })

  return {
    nodes: displayNodes.map((node) => ({
      id: node.id,
      label: node.kind,
      x: 7 + ((node.x - minX) / spanX) * 86,
      y: 18 + ((node.y - minY) / spanY) * 58,
      size: 12 + Math.min(degrees.get(node.id) || 0, 8),
      kind: node.kind,
    })),
    edges: boundedEdges.map((edge) => ({
      id: edge.id,
      sourceId: edge.source,
      targetId: edge.target,
      label: edge.label.replace('RHEA:', 'R-'),
      entityId: edge.enzymeId,
      reactionId: edge.reactionId,
      multi: (pairCounts.get(`${edge.source}->${edge.target}`) || 0) > 1 || Boolean(edge.curved),
    })),
  }
}

function deriveSourceFilters(items: Entity[]) {
  const sourceTags = new Set<string>()
  items.forEach((entity) => {
    entity.tags.forEach((tag) => {
      const normalized = normalizeSourceLabel(tag)
      if (normalized && !normalized.includes('grouped') && !normalized.includes('compound')) sourceTags.add(formatSourceLabel(tag))
    })
  })
  return Array.from(sourceTags).slice(0, 6)
}

function buildHomeEdgeCards(edgeId: string | null, edges: HomeMapEdge[]): HomeEdgeCard[] {
  const selectedEdge = edges.find((edge) => edge.id === edgeId)
  const sourceEdges = selectedEdge
    ? edges.filter((edge) => edge.sourceId === selectedEdge.sourceId && edge.targetId === selectedEdge.targetId).slice(0, 3)
    : edges.slice(0, 3)

  return sourceEdges.map((edge, index) => {
    const enzyme = getEntity(edge.entityId)
    const reaction = getEntity(edge.reactionId)
    const organism = enzyme?.species || enzyme?.fields.find((field) => field.label === 'Organism')?.value || 'Unknown organism'
    const recordCode = enzyme?.subtitle || edge.entityId

    return {
      id: `${edge.id}:${index}`,
      entityId: edge.entityId,
      name: enzyme?.name || edge.entityId,
      organism,
      equation: reaction?.description || enzyme?.description || edge.label,
      recordCode,
      reactionId: edge.reactionId,
      extra: sourceEdges.length > 1 && index === 0 ? `+${sourceEdges.length - 1}` : undefined,
    }
  })
}

function normalizeSourceLabel(value: string) {
  return value.toLowerCase().replace(/[_\s-]+/g, '')
}

function formatSourceLabel(value: string) {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join('-')
}

function HomeMapGraphic({
  nodes,
  edges,
  selectedNodeId,
  expandedEdgeId,
  searchActive,
  onNodeSelect,
  onEdgeSelect,
}: {
  nodes: HomeMapNode[]
  edges: HomeMapEdge[]
  selectedNodeId: string | null
  expandedEdgeId: string | null
  searchActive: boolean
  onNodeSelect: (id: string) => void
  onEdgeSelect: (edge: HomeMapEdge) => void
}) {
  const nodeById = (id: string) => nodes.find((node) => node.id === id)
  const selected = (selectedNodeId ? nodeById(selectedNodeId) : undefined) || nodes[0]
  const neighborIds = new Set(
    edges
      .filter((edge) => edge.sourceId === selected?.id || edge.targetId === selected?.id)
      .flatMap((edge) => [edge.sourceId, edge.targetId]),
  )

  if (!selected) return null

  return (
    <svg className="home-map-svg" viewBox="0 0 100 100" role="img" aria-label="Interactive terpene network map">
      <defs>
        <radialGradient id="home-node-gradient" cx="45%" cy="35%" r="62%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="52%" stopColor="#ebe7a2" />
          <stop offset="100%" stopColor="#cfc77a" />
        </radialGradient>
        <filter id="home-node-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feDropShadow dx="0" dy="0" stdDeviation="1.2" floodColor="#ebe7a2" floodOpacity="0.55" />
        </filter>
        <filter id="selected-node-glow" x="-100%" y="-100%" width="300%" height="300%">
          <feDropShadow dx="0" dy="0" stdDeviation="4" floodColor="#ebe7a2" floodOpacity="0.55" />
        </filter>
      </defs>

      <g className="map-star-field">
        {Array.from({ length: 150 }).map((_, index) => (
          <circle key={index} cx={(index * 37) % 100} cy={(index * 19) % 88 + 5} r={index % 5 === 0 ? 0.1 : 0.06} />
        ))}
      </g>

      <g className="home-map-edges">
        {edges.map((edge) => {
          const source = nodeById(edge.sourceId)
          const target = nodeById(edge.targetId)
          if (!source || !target) return null

          const selectedEdge = edge.id === expandedEdgeId || edge.sourceId === selectedNodeId || edge.targetId === selectedNodeId
          const labelVisible = edge.multi || selectedEdge || searchActive

          return (
            <g key={edge.id}>
              <line
                x1={source.x}
                y1={source.y}
                x2={target.x}
                y2={target.y}
                className={`${selectedEdge ? 'active' : ''} ${edge.multi ? 'multi' : ''}`}
              />
              <g role="button" tabIndex={0} aria-label={`Open ${edge.label}`} onClick={() => onEdgeSelect(edge)}>
                {labelVisible && (
                  <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 1.4} className="home-edge-label">
                    {edge.multi ? `${edge.label}*` : edge.label}
                  </text>
                )}
              </g>
            </g>
          )
        })}
      </g>

      <g className="home-map-nodes">
        {nodes.map((node) => {
          const isSelected = node.id === selectedNodeId
          const isNeighbor = neighborIds.has(node.id) && !isSelected
          const radius = isSelected ? node.size * 0.28 : isNeighbor ? node.size * 0.22 : node.size * 0.18
          return (
            <g key={node.id} className={`home-map-node ${isSelected ? 'selected' : ''} ${isNeighbor ? 'neighbor' : ''}`}>
              <circle cx={node.x} cy={node.y} r={radius} onClick={() => onNodeSelect(node.id)} />
              <text x={node.x} y={node.y + radius + 2.2}>
                {node.label}
              </text>
            </g>
          )
        })}
      </g>

      <circle cx={selected.x} cy={selected.y} r="1" className="selected-pulse" />
    </svg>
  )
}
function OverviewGraphic() {
  return (
    <svg className="overview-graphic" viewBox="0 0 960 620" role="img" aria-label="Dataset overview illustration">
      <defs>
        <linearGradient id="overview-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#08131f" />
          <stop offset="100%" stopColor="#0f1b2b" />
        </linearGradient>
        <linearGradient id="overview-accent" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#ffcf72" />
          <stop offset="50%" stopColor="#7ad4ff" />
          <stop offset="100%" stopColor="#ff8f8f" />
        </linearGradient>
        <filter id="overview-shadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="10" stdDeviation="14" floodColor="#02060a" floodOpacity="0.45" />
        </filter>
      </defs>
      <rect x="0" y="0" width="960" height="620" rx="28" fill="url(#overview-bg)" />
      <g opacity="0.25">
        {Array.from({ length: 11 }).map((_, index) => (
          <path key={`h-${index}`} d={`M40 ${60 + index * 46} H920`} stroke="#d9efff" strokeWidth="1" />
        ))}
        {Array.from({ length: 12 }).map((_, index) => (
          <path key={`v-${index}`} d={`M70 ${42 + index * 70} V575`} stroke="#d9efff" strokeWidth="1" />
        ))}
      </g>

      <g filter="url(#overview-shadow)">
        <rect x="38" y="36" width="330" height="154" rx="18" fill="#ffffff" opacity="0.06" stroke="#d2ecff" strokeOpacity="0.14" />
        <text x="60" y="72" className="overview-label">Dataset coverage</text>
        <text x="60" y="114" className="overview-title">Curated terpene network</text>
        <text x="60" y="148" className="overview-copy">Compounds, enzymes, and reactions remain linked in one place.</text>
        <path d="M60 172 H336" stroke="url(#overview-accent)" strokeWidth="3" />
      </g>

      <g filter="url(#overview-shadow)">
        <rect x="388" y="36" width="224" height="154" rx="18" fill="#ffffff" opacity="0.06" stroke="#d2ecff" strokeOpacity="0.14" />
        <text x="412" y="73" className="overview-label">Queue</text>
        <text x="412" y="114" className="overview-title">Ready for export</text>
        <text x="412" y="148" className="overview-copy">Save records, then export them to CSV.</text>
        <path d="M412 172 H586" stroke="#7ad4ff" strokeWidth="3" />
      </g>

      <g filter="url(#overview-shadow)">
        <rect x="636" y="36" width="286" height="154" rx="18" fill="#ffffff" opacity="0.06" stroke="#d2ecff" strokeOpacity="0.14" />
        <text x="658" y="72" className="overview-label">Live filters</text>
        <text x="658" y="114" className="overview-title">Species, class, family</text>
        <text x="658" y="148" className="overview-copy">Tight filtering keeps browsing focused.</text>
        <path d="M658 172 H896" stroke="#ff8f8f" strokeWidth="3" />
      </g>

      <g>
        <path d="M92 342 C166 262, 230 262, 304 342 S442 422, 514 342 S654 262, 730 342 S866 422, 922 290" fill="none" stroke="#6fd4ff" strokeWidth="4" opacity="0.75" />
        <path d="M92 404 C164 324, 230 324, 304 404 S442 484, 514 404 S654 324, 730 404 S866 484, 922 352" fill="none" stroke="#ffae78" strokeWidth="4" opacity="0.7" />

        {[
          { x: 124, y: 340, label: 'GPP', tone: '#ffcf72' },
          { x: 286, y: 342, label: 'LIS', tone: '#7ad4ff' },
          { x: 468, y: 342, label: 'Linalool', tone: '#ff8f8f' },
          { x: 650, y: 342, label: 'GAS', tone: '#ffcf72' },
          { x: 832, y: 342, label: 'Taxadiene', tone: '#7ad4ff' },
        ].map((node) => (
          <g key={node.label}>
            <circle cx={node.x} cy={node.y} r="24" fill={node.tone} opacity="0.22" />
            <circle cx={node.x} cy={node.y} r="15" fill={node.tone} />
            <text x={node.x} y={node.y + 42} textAnchor="middle" className="overview-node-label">
              {node.label}
            </text>
          </g>
        ))}
      </g>

      <g filter="url(#overview-shadow)">
        <rect x="72" y="462" width="240" height="106" rx="16" fill="#ffffff" opacity="0.06" stroke="#d2ecff" strokeOpacity="0.14" />
        <text x="92" y="500" className="overview-label">Records by type</text>
        <rect x="92" y="520" width="36" height="22" rx="6" fill="#ffcf72" />
        <rect x="136" y="506" width="36" height="36" rx="6" fill="#7ad4ff" />
        <rect x="180" y="514" width="36" height="28" rx="6" fill="#ff8f8f" />
        <rect x="224" y="498" width="36" height="44" rx="6" fill="#b9c4d8" />
        <text x="92" y="557" className="overview-mini-label">Compounds</text>
        <text x="136" y="557" className="overview-mini-label">Enzymes</text>
        <text x="180" y="557" className="overview-mini-label">Reactions</text>
        <text x="224" y="557" className="overview-mini-label">Saved</text>
      </g>

      <g filter="url(#overview-shadow)">
        <rect x="338" y="462" width="550" height="106" rx="16" fill="#ffffff" opacity="0.06" stroke="#d2ecff" strokeOpacity="0.14" />
        <text x="360" y="500" className="overview-label">Search profile</text>
        <path d="M360 548 C420 518, 480 530, 540 510 S660 486, 712 520 S820 548, 860 500" fill="none" stroke="url(#overview-accent)" strokeWidth="4" />
        <circle cx="360" cy="548" r="6" fill="#ffcf72" />
        <circle cx="540" cy="510" r="6" fill="#7ad4ff" />
        <circle cx="712" cy="520" r="6" fill="#ff8f8f" />
        <circle cx="860" cy="500" r="6" fill="#b9c4d8" />
      </g>

      <text x="58" y="602" className="overview-footer">Terpene Atlas live workspace</text>
    </svg>
  )
}

function Metric({ label, value, delta }: { label: string; value: string; delta: string }) {
  return (
    <div className="metric-card">
      <span className="metric-label">{label}</span>
      <strong>{value}</strong>
      <small>{delta}</small>
    </div>
  )
}

function Stat({ label, value, delta, icon, tone }: { label: string; value: string; delta: string; icon: ReactNode; tone: string }) {
  return (
    <div className="stat-card">
      <div className={`stat-icon ${tone}`}>{icon}</div>
      <div>
        <div className="stat-label">{label}</div>
        <div className="stat-value">{value}</div>
        <div className="stat-delta">{delta}</div>
      </div>
    </div>
  )
}

function FilterSelect({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return (
    <label className="filter-select">
      <span>{label}</span>
      <div className="select-wrap">
        <select value={value} onChange={(event) => onChange(event.target.value)}>
          {options.map((option) => (
            <option key={option}>{option}</option>
          ))}
        </select>
        <ChevronDown size={14} />
      </div>
    </label>
  )
}

function LegendDot({ tone, label }: { tone: string; label: string }) {
  return (
    <span>
      <i className={`legend-dot ${tone}`} />
      {label}
    </span>
  )
}

function SummaryRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="summary-row">
      <span>{label}</span>
      <strong>{value}</strong>
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
  }
}

function looksLikeProteinSequence(value: string) {
  const compact = value
    .replace(/^>.*$/gm, '')
    .replace(/[^A-Za-z]/g, '')
    .toUpperCase()

  return compact.length >= 30 && /^[ACDEFGHIKLMNPQRSTVWYBXZJUO]+$/.test(compact)
}

function matchesFilters(entity: Entity | undefined, filters: FilterState) {
  if (!entity) return false

  if (filters.searchKind !== 'all' && entity.kind !== filters.searchKind) return false
  if (filters.species !== filterOptions.species[0] && entity.species !== filters.species) return false
  if (filters.compoundClass !== filterOptions.classes[0] && entity.compoundClass !== filters.compoundClass) return false
  if (filters.enzymeFamily !== filterOptions.families[0] && entity.enzymeFamily !== filters.enzymeFamily) return false

  const normalizedQuery = filters.query.trim().toLowerCase()
  if (!normalizedQuery) return true

  return [entity.id, entity.name, entity.subtitle, entity.description, ...entity.tags, ...entity.fields.map((field) => `${field.label} ${field.value}`)]
    .join(' ')
    .toLowerCase()
    .includes(normalizedQuery)
}

function getExternalRecordUrl(entity: Entity) {
  if (entity.kind === 'enzyme') return `https://www.uniprot.org/uniprotkb/${entity.id.replace('ENZ:', '')}`
  if (entity.kind === 'compound') return `https://www.ebi.ac.uk/chebi/searchId.do?chebiId=${entity.id}`
  return `https://www.rhea-db.org/reaction?id=${entity.id.replace('RHEA:', '')}`
}

function csvCell(value: string) {
  return `"${value.replace(/"/g, '""')}"`
}

function visibleEdgeCount(visibleNodeIds: Set<string>) {
  return graphEdges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)).length
}

export default App














