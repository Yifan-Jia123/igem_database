import { Check, ChevronRight, Download, ExternalLink } from 'lucide-react'
import type { Entity } from '../types'
import { kindIcons, kindLabels } from '../lib/entities'

export function SearchResult({
  entity,
  selected,
  onSelect,
  addToQueue,
  openRecord,
  isQueued,
}: {
  entity: Entity
  selected: boolean
  onSelect: () => void
  addToQueue: (id: string) => void
  openRecord: (entity: Entity) => void
  isQueued: boolean
}) {
  const Icon = kindIcons[entity.kind]

  return (
    <article className={`search-result ${selected ? 'selected' : ''} ${isQueued ? 'queued' : ''}`}>
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
        {isQueued && <span className="queue-state">Queued</span>}
        <button className="icon-button" title="Open record" onClick={() => openRecord(entity)}>
          <ExternalLink size={16} />
        </button>
        <button className={`icon-button queue-action ${isQueued ? 'is-queued' : ''}`} title={isQueued ? 'Remove from queue' : 'Add to queue'} aria-label={isQueued ? 'Remove from queue' : 'Add to queue'} onClick={() => addToQueue(entity.id)}>
          {isQueued ? <Check size={16} /> : <Download size={16} />}
        </button>
        <ChevronRight size={17} className="result-chevron" />
      </div>
    </article>
  )
}
