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
  onQueueMany,
  openRecord: _openRecord,
  isQueued,
  autoMapSearch,
  onAutoMapSearchConsumed,
  blastSession,
  autoBlastScope,
  onAutoBlastScopeConsumed,
  onResetHome,
  searchSet,
  onSearchSetChange,
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
  /** 批量入队（合并抽屉的「Queue all」）。 */
  onQueueMany: (entries: Entity[]) => void
  openRecord: (entity: Entity) => void
  isQueued: (id: string) => boolean
  autoMapSearch?: { query: string; mode: 'enzyme' | 'pathway'; nonce: number } | null
  onAutoMapSearchConsumed?: () => void
  /** Last completed BLAST run; lets the map render the hit enzymes as a scope subgraph. */
  blastSession?: BlastSession | null
  autoBlastScope?: { sessionId: number; nonce: number } | null
  onAutoBlastScopeConsumed?: () => void
  /** Brand click → reset any app-wide search state and head back to home. */
  onResetHome?: () => void
  /** 搜索集（检索范围）。空数组 = 全部。 */
  searchSet: string[]
  onSearchSetChange: (next: string[]) => void
}) {
  return (
    <CompoundGraphHome
      onOpenSearch={onOpenSearch}
      onOpenDownloads={onOpenDownloads}
      onOpenEnzyme={onOpenEnzyme}
      onOpenBlast={onOpenBlast}
      onOpenBlastTable={onOpenBlastTable}
      onToggleQueue={onToggleQueue}
      onQueueMany={onQueueMany}
      isQueued={isQueued}
      queueCount={queueCount}
      autoMapSearch={autoMapSearch}
      onAutoMapSearchConsumed={onAutoMapSearchConsumed}
      blastSession={blastSession}
      autoBlastScope={autoBlastScope}
      onAutoBlastScopeConsumed={onAutoBlastScopeConsumed}
      onResetHome={onResetHome}
      searchSet={searchSet}
      onSearchSetChange={onSearchSetChange}
    />
  )
}
