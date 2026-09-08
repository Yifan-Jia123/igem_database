import { useState } from 'react'
import { ArrowDownToLine, ArrowUpRight, Download, ExternalLink, Route, X } from 'lucide-react'
import type { Entity } from '../types'
import { kindIcons, kindLabels } from '../lib/entities'

/** Group key: startId + endId of the route. Routes sharing the same pair are
 *  drawn as one big card so the user sees "all the ways from A to B". */
const pairKey = (item: Entity) =>
  item.pathway ? `${item.pathway.startId}::${item.pathway.endId}` : item.id

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

  // Records produced from pathway search cards (kind==='pathway' with the
  // PathwayQueueMeta payload) are grouped by their start/end pair. Everything
  // else that lands in the Pathways tab (compounds, reactions) stays a plain row.
  const groupedPathwayItems = pathwayItems.filter((item) => item.kind === 'pathway' && item.pathway)
  const otherPathwayItems = pathwayItems.filter((item) => !(item.kind === 'pathway' && item.pathway))
  const groups: Array<{ key: string; name: string; routes: Entity[] }> = []
  for (const item of groupedPathwayItems) {
    const key = pairKey(item)
    let group = groups.find((g) => g.key === key)
    if (!group) {
      group = { key, name: item.name, routes: [] }
      groups.push(group)
    }
    group.routes.push(item)
  }
  // within a group sort the shorter (fewer compounds) routes first, stable
  for (const group of groups) {
    group.routes.sort((a, b) => (a.pathway?.compoundIds.length ?? 0) - (b.pathway?.compoundIds.length ?? 0))
  }

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
              <>
                {/* non-pathway records that land in this tab (compounds / reactions) stay plain rows */}
                {otherPathwayItems.map((entity) => {
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
                })}
                {/* one big card per start→end pair; each route inside is a small card */}
                {groups.map((group) => (
                  <article key={group.key} className="downloads-pw-group">
                    <div className="downloads-pw-group-head">
                      <span className="queue-icon pathway">
                        <Route size={15} />
                      </span>
                      <span className="queue-copy">
                        <strong>{group.name}</strong>
                        <small>
                          {group.routes.length} route{group.routes.length === 1 ? '' : 's'} from the same start to the same end
                        </small>
                      </span>
                      <span className="queue-kind">{kindLabels.pathway}</span>
                      <span className="downloads-pw-group-count">{group.routes.length}</span>
                      <button
                        className="icon-button"
                        title="Remove every route in this group"
                        onClick={() => group.routes.forEach((route) => removeFromQueue(route.id))}
                      >
                        <X size={15} />
                      </button>
                    </div>
                    <div className="downloads-pw-group-body">
                      {group.routes.map((route, routeIndex) => {
                        const meta = route.pathway
                        if (!meta) return null
                        // Enzyme-picked variants (created via the pathway detail page's
                        // download picker) carry per-step chosen enzymes; plain routes
                        // omit them and render exactly as before. The enzyme listing is
                        // the LAST child on purpose: it is pinned to a second grid row
                        // by CSS (explicit row/col), so appending it after the remove
                        // button never disturbs the header row's auto-placement.
                        const hasEnzymes = Boolean(meta.enzymesByStep && meta.enzymesByStep.length > 0)
                        return (
                          <div key={route.id} className={`downloads-pw-route ${hasEnzymes ? 'has-enzymes' : ''}`}>
                            <span className="downloads-pw-route-index">{routeIndex + 1}</span>
                            <span className="downloads-pw-chain">
                              {meta.compoundNames.map((name, i) => (
                                <span key={`${route.id}-${i}`} className="downloads-pw-node" title={meta.compoundIds[i]}>
                                  {name}
                                </span>
                              ))}
                            </span>
                            <span className="downloads-pw-route-meta">
                              {meta.stepCount} step{meta.stepCount === 1 ? '' : 's'} · {meta.compoundIds.length} compound
                              {meta.compoundIds.length === 1 ? '' : 's'}
                              {hasEnzymes && (
                                <span className="downloads-pw-route-badge" title="已为每步选定具体酶">
                                  已选酶
                                </span>
                              )}
                            </span>
                            <button
                              className="icon-button"
                              title="Remove this route from the queue"
                              onClick={() => removeFromQueue(route.id)}
                            >
                              <X size={15} />
                            </button>
                            {hasEnzymes && meta.enzymesByStep && (
                              <div className="downloads-pw-route-enzymes">
                                {meta.enzymesByStep.map((step) => (
                                  <div key={step.step} className="downloads-pw-enzyme-step">
                                    <span className="downloads-pw-enzyme-step-label">
                                      {step.step}. {step.sourceName} → {step.targetName}
                                    </span>
                                    <span className="downloads-pw-enzyme-row">
                                      {step.enzymes.length === 0 ? (
                                        <span className="downloads-pw-enzyme-missing">(该步无已选酶)</span>
                                      ) : (
                                        step.enzymes.map((enzyme) => (
                                          <span
                                            key={enzyme.enzymeId}
                                            className="downloads-pw-enzyme-chip"
                                            title={enzyme.sourceType ?? enzyme.enzymeId}
                                          >
                                            {enzyme.name}
                                            {enzyme.organismName ? <em>{enzyme.organismName}</em> : null}
                                          </span>
                                        ))
                                      )}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </article>
                ))}
              </>
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
