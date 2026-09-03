import { CompoundGraphHome } from '../graphExperience'
import type { Entity } from '../types'

export function HomePage({
  queueCount,
  entityCount: _entityCount,
  nodeCount: _nodeCount,
  edgeCount: _edgeCount,
  downloadedItems: _downloadedItems,
  onOpenSearch,
  onOpenNetwork,
  onOpenStructure,
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
  onOpenStructure: () => void
  onOpenDownloads: () => void
  onOpenEnzyme: (id: string) => void
  onToggleQueue: (entry: string | Entity) => void
  openRecord: (entity: Entity) => void
  isQueued: (id: string) => boolean
}) {
  return (
    <CompoundGraphHome
      onOpenSearch={onOpenSearch}
      onOpenNetwork={onOpenNetwork}
      onOpenStructure={onOpenStructure}
      onOpenDownloads={onOpenDownloads}
      onOpenEnzyme={onOpenEnzyme}
      onToggleQueue={onToggleQueue}
      isQueued={isQueued}
      queueCount={queueCount}
    />
  )
}
