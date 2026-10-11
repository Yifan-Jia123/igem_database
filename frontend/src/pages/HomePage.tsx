import { CompoundGraphHome } from '../graphExperience'
import type { BlastSession } from '../api'
import type { Entity } from '../types'
import type { MapSearchRoute } from '../lib/routes'

export function HomePage({
  hidden,
  resetNonce,
  queueCount,
  entityCount: _entityCount,
  nodeCount: _nodeCount,
  edgeCount: _edgeCount,
  downloadedItems: _downloadedItems,
  onOpenSearch,
  onOpenDownloads,
  onOpenEnzyme,
  onOpenBlast,
  onOpenBlastTable,
  onToggleQueue,
  onQueueMany,
  openRecord: _openRecord,
  isQueued,
  mapSearch,
  mapSearchNonce,
  onMapSearch,
  blastSession,
  autoBlastScope,
  onAutoBlastScopeConsumed,
  onResetHome,
  searchSet,
  onSearchSetChange,
}: {
  /** The map stays mounted across routes; this only takes it out of the layout. */
  hidden?: boolean
  /** Bumped by App's `resetHome`; the map watches it to drop its own scope state. */
  resetNonce?: number
  queueCount: number
  entityCount: number
  nodeCount: number
  edgeCount: number
  downloadedItems: Entity[]
  onOpenSearch: (query?: string) => void
  onOpenDownloads: () => void
  onOpenEnzyme: (id: string) => void
  onOpenBlast: () => void
  onOpenBlastTable: () => void
  onToggleQueue: (entry: string | Entity) => void
  /** Bulk enqueue (the merge drawer's "Queue all"). */
  onQueueMany: (entries: Entity[]) => void
  openRecord: (entity: Entity) => void
  isQueued: (id: string) => boolean
  /** The search spec on the URL (`undefined` = not home, no signal; `null` = bare home). */
  mapSearch?: MapSearchRoute | null
  /** Bumped when the same spec is submitted again; only it triggers a re-run. */
  mapSearchNonce?: number
  /** The map asks to write a search into the URL (App navigates; the map only
   *  consumes the result). */
  onMapSearch?: (spec: MapSearchRoute | null) => void
  /** Last completed BLAST run; lets the map render the hit enzymes as a scope subgraph. */
  blastSession?: BlastSession | null
  autoBlastScope?: { sessionId: number; nonce: number } | null
  onAutoBlastScopeConsumed?: () => void
  /** Brand click → reset any app-wide search state and head back to home. */
  onResetHome?: () => void
  /** Search set (the search scope). Empty array = all. */
  searchSet: string[]
  onSearchSetChange: (next: string[]) => void
}) {
  return (
    <CompoundGraphHome
      hidden={hidden}
      resetNonce={resetNonce}
      onOpenSearch={onOpenSearch}
      onOpenDownloads={onOpenDownloads}
      onOpenEnzyme={onOpenEnzyme}
      onOpenBlast={onOpenBlast}
      onOpenBlastTable={onOpenBlastTable}
      onToggleQueue={onToggleQueue}
      onQueueMany={onQueueMany}
      isQueued={isQueued}
      queueCount={queueCount}
      mapSearch={mapSearch}
      mapSearchNonce={mapSearchNonce}
      onMapSearch={onMapSearch}
      blastSession={blastSession}
      autoBlastScope={autoBlastScope}
      onAutoBlastScopeConsumed={onAutoBlastScopeConsumed}
      onResetHome={onResetHome}
      searchSet={searchSet}
      onSearchSetChange={onSearchSetChange}
    />
  )
}
