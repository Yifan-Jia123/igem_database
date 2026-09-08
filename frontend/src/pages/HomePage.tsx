import { CompoundGraphHome } from '../graphExperience'
import type { BlastSession } from '../api'
import type { Entity } from '../types'

export function HomePage({
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
  openRecord: _openRecord,
  isQueued,
  autoMapSearch,
  onAutoMapSearchConsumed,
  blastSession,
  autoBlastScope,
  onAutoBlastScopeConsumed,
  onResetHome,
}: {
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
  openRecord: (entity: Entity) => void
  isQueued: (id: string) => boolean
  autoMapSearch?: { query: string; nonce: number } | null
  onAutoMapSearchConsumed?: () => void
  /** Last completed BLAST run; lets the map render the hit enzymes as a scope subgraph. */
  blastSession?: BlastSession | null
  autoBlastScope?: { sessionId: number; nonce: number } | null
  onAutoBlastScopeConsumed?: () => void
  /** Brand click → reset any app-wide search state and head back to home. */
  onResetHome?: () => void
}) {
  return (
    <CompoundGraphHome
      onOpenSearch={onOpenSearch}
      onOpenDownloads={onOpenDownloads}
      onOpenEnzyme={onOpenEnzyme}
      onOpenBlast={onOpenBlast}
      onOpenBlastTable={onOpenBlastTable}
      onToggleQueue={onToggleQueue}
      isQueued={isQueued}
      queueCount={queueCount}
      autoMapSearch={autoMapSearch}
      onAutoMapSearchConsumed={onAutoMapSearchConsumed}
      blastSession={blastSession}
      autoBlastScope={autoBlastScope}
      onAutoBlastScopeConsumed={onAutoBlastScopeConsumed}
      onResetHome={onResetHome}
    />
  )
}
