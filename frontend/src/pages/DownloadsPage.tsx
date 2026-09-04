import { useState } from 'react'
import { ArrowDownToLine, ArrowUpRight, Download, ExternalLink, X } from 'lucide-react'
import type { Entity } from '../types'
import { kindIcons, kindLabels } from '../lib/entities'

export function DownloadsPage({
  downloadedItems,
  removeFromQueue,
  clearQueue,
  exportQueue,
  onOpenEntity,
  openRecord,
}: {
  downloadedItems: Entity[]
  removeFromQueue: (id: string) => void
  clearQueue: () => void
  exportQueue: () => void
  onOpenEntity: (id: string) => void
  openRecord: (entity: Entity) => void
}) {
  const [activeTab, setActiveTab] = useState<'enzymes' | 'pathways'>('enzymes')
  const enzymeItems = downloadedItems.filter((item) => item.kind === 'enzyme')
  const pathwayItems = downloadedItems.filter((item) => item.kind !== 'enzyme')

  return (
    <div className="content-wrap downloads-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow">
            <Download size={14} />
            Saved output
          </div>
          <h1>Downloading table</h1>
          <p>Collect selected records here, then export them when the queue is ready.</p>
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
        <aside className="downloads-sidebar section-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">
                <span className="live-line" />
                Downloading options
              </div>
              <h2>Export setup</h2>
            </div>
          </div>

          <div className="downloads-option-group">
            <div className="downloads-option-label">Format</div>
            <div className="downloads-format-list">
              {['FASTA', 'TSV', 'TXT', 'XLSX'].map((format) => (
                <button key={format} type="button" className="downloads-format-item" onClick={() => void exportQueue()}>
                  {format}
                </button>
              ))}
            </div>
          </div>

          <div className="downloads-option-group">
            <div className="downloads-option-label">Custom columns</div>
            <div className="downloads-columns-list">
              {['ID', 'Name', 'Subtitle', 'Species', 'Tags', 'Description'].map((column) => (
                <button key={column} type="button" className="downloads-column-item">
                  {column}
                </button>
              ))}
            </div>
          </div>

          <button className="download-submit-pill" onClick={exportQueue} disabled={downloadedItems.length === 0}>
            Download archive
          </button>
        </aside>

        <div className="downloads-main section-panel">
          <div className="downloads-tabbar">
            <button type="button" className={activeTab === 'enzymes' ? 'active' : ''} onClick={() => setActiveTab('enzymes')}>
              Enzymes <span>({enzymeItems.length} chosen)</span>
            </button>
            <button type="button" className={activeTab === 'pathways' ? 'active' : ''} onClick={() => setActiveTab('pathways')}>
              Pathways <span>({pathwayItems.length} chosen)</span>
            </button>
          </div>

          <div className="downloads-panel-group">
            <div className="download-table-header" aria-hidden="true">
              <span>Record</span>
              <span>Category</span>
              <span>Actions</span>
            </div>
            {activeTab === 'enzymes' ? (
              enzymeItems.length > 0 ? (
                enzymeItems.map((entity) => {
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
                })
              ) : (
                <div className="empty-home">
                  <p>No enzyme records yet.</p>
                </div>
              )
            ) : pathwayItems.length > 0 ? (
              pathwayItems.map((entity) => {
                const Icon = kindIcons[entity.kind]
                return (
                  <article key={entity.id} className={`download-row ${entity.kind}`}>
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
                    <button className="icon-button" title="Remove from queue" onClick={() => removeFromQueue(entity.id)}>
                      <X size={15} />
                    </button>
                  </article>
                )
              })
            ) : (
              <div className="empty-home">
                <p>No pathway records yet.</p>
              </div>
            )}
          </div>
        </div>

      </section>
    </div>
  )
}
