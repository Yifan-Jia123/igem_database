import { useEffect, useMemo, useState } from 'react'
import {
  ChevronRight,
  CircleHelp,
  Database,
  Download,
  FlaskConical,
  Menu,
  Network,
  Search,
  Settings2,
  Sparkles,
  X,
} from 'lucide-react'
import { entities as mockEntities, filterOptions as mockFilterOptions, graphEdges as mockGraphEdges, graphNodes as mockGraphNodes } from './data'
import { loadApiDataset, searchApiEntries, searchHomologyEntries } from './api'
import { EnzymeDetailView } from './graphExperience'
import { DownloadsPage } from './pages/DownloadsPage'
import { HomePage } from './pages/HomePage'
import { SearchPage } from './pages/SearchPage'
import { StructureSearchPage } from './pages/StructureSearchPage'
import { getExternalRecordUrl, looksLikeProteinSequence, matchesFilters } from './lib/entities'
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
  { view: 'structure', label: 'Structure search', icon: FlaskConical },
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
  const [apiSearchResults, setApiSearchResults] = useState<Entity[] | null>(null)
  const [apiSearchLoading, setApiSearchLoading] = useState(false)
  const [apiSearchError, setApiSearchError] = useState<string | null>(null)
  const [datasetRevision, setDatasetRevision] = useState(0)

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
  const downloadedItems = useMemo(
    () => downloadedIds.map((id) => getEntity(id) || queuedEntitiesById[id]).filter((entity): entity is Entity => Boolean(entity)),
    [downloadedIds, queuedEntitiesById, datasetRevision],
  )
  const queuedIds = useMemo(() => new Set(downloadedIds), [downloadedIds])

  const localFilteredEntities = useMemo(() => entities.filter((entity) => matchesFilters(entity, filters, filterOptions)), [filters, datasetRevision])
  const filteredEntities = apiSearchResults ? apiSearchResults.filter((entity) => matchesFilters(entity, { ...filters, query: '' }, filterOptions)) : localFilteredEntities
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
    window.open(url, '_blank', 'noopener,noreferrer')
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
              setQuery(nextSearch)
              setSearchKind(looksLikeProteinSequence(nextSearch) ? 'enzyme' : 'all')
              goTo('search')
            }}
            onOpenNetwork={() => goTo('home')}
            onOpenStructure={() => goTo('structure')}
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
          <SearchPage
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
            selectedEntity={selected}
            setSelectedId={setSelectedId}
            addToQueue={toggleQueue}
            openRecord={openRecord}
            isQueued={selected ? queuedIds.has(selected.id) : false}
            filterOptions={filterOptions}
          />
        )}

        {view === 'structure' && (
          <StructureSearchPage
            onOpenCompound={(id) => goTo('search', id)}
          />
        )}

        {view === 'downloads' && (
          <DownloadsPage
            downloadedItems={downloadedItems}
            removeFromQueue={removeFromQueue}
            clearQueue={clearQueue}
            onOpenEntity={(id) => { const entity = getEntity(id); if (entity?.kind === 'enzyme') goTo('enzyme', id); else goTo('search', id) }}
            openRecord={openRecord}
          />
        )}
      </main>
    </div>
  )
}

function viewLabel(view: View) {
  switch (view) {
    case 'home':
      return 'Overview'

    case 'search':
      return 'Search library'
    case 'structure':
      return 'Structure search'
    case 'downloads':
      return 'Download queue'
    case 'enzyme':
      return 'Enzyme detail'
  }
}

export default App














