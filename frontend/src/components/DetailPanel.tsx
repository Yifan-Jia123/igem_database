import { ArrowDownToLine, ChevronRight, ExternalLink, PanelRight, X } from 'lucide-react'
import type { Entity } from '../types'
import { getExternalRecordUrl, kindIcons, kindLabels } from '../lib/entities'

export function DetailPanel({
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
