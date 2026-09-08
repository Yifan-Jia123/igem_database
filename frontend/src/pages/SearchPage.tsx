import { RefreshCw, Search } from 'lucide-react'
import { DetailPanel } from '../components/DetailPanel'
import { FilterSelect } from '../components/FilterSelect'
import { SearchResult } from '../components/SearchResult'
import type { Entity } from '../types'
import type { FilterOptions, SearchKind } from '../lib/entities'
import { kindLabels } from '../lib/entities'

export function SearchPage({
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
  selectedEntity,
  setSelectedId,
  addToQueue,
  openRecord,
  isQueued,
  filterOptions,
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
  selectedEntity?: Entity
  setSelectedId: (value: string | null) => void
  addToQueue: (id: string) => void
  openRecord: (entity: Entity) => void
  isQueued: boolean
  filterOptions: FilterOptions
}) {
  return (
    <div className="content-wrap search-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow">
            <Search size={14} />
            Library search
          </div>
          <h1>Table search</h1>
          <p>Use the filters at left to narrow the table, scan matches in the center, and inspect one record on the right.</p>
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
        <aside className="search-sidebar section-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">
                <span className="live-line" />
                Searching filters
              </div>
              <h2>Refine results</h2>
            </div>
          </div>

          <div className="search-sidebar-stack">
            <div className="search-sidebar-group">
              <div className="search-sidebar-label">Record type</div>
              <div className="search-kind-group sidebar-kind-group">
                {(['all', 'compound', 'enzyme', 'reaction'] as const).map((kind) => (
                  <button key={kind} className={`chip-button ${searchKind === kind ? 'active' : ''}`} onClick={() => setSearchKind(kind)}>
                    {kind === 'all' ? 'All types' : kindLabels[kind]}
                  </button>
                ))}
              </div>
            </div>

            <div className="search-sidebar-group">
              <FilterSelect label="Species" value={selectedSpecies} options={filterOptions.species} onChange={setSelectedSpecies} />
            </div>

            <div className="search-sidebar-group">
              <FilterSelect label="Compound class" value={selectedClass} options={filterOptions.classes} onChange={setSelectedClass} />
            </div>

            <div className="search-sidebar-group">
              <FilterSelect label="Enzyme family" value={selectedFamily} options={filterOptions.families} onChange={setSelectedFamily} />
            </div>
          </div>

          <button className="outline-button search-sidebar-reset" onClick={clearFilters}>
            <RefreshCw size={15} />
            Clear filters
          </button>
        </aside>

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
            <div className="search-summary">
              {apiSearchLoading ? 'Searching backend...' : <>Showing <strong>{filteredEntities.length}</strong> curated entries</>}
              {apiSearchError && <span>{apiSearchError}</span>}
            </div>
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
          entity={selectedEntity}
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
