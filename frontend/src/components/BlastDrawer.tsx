import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { ArrowUpRight, Check, Download, ExternalLink, FlaskConical, Loader2, ScanSearch, X } from 'lucide-react'
import { loadEnzymeDetail, runBlastSearch } from '../api'
import type { BlastHit, BlastPayload } from '../api'
import type { Entity } from '../types'
import '../styles/blast.css'

/**
 * Slide-out BLAST drawer (mirrors the Structure-search drawer).
 *
 * Paste a protein/FASTA sequence and run a local NCBI BLAST+ search against the
 * enzyme library (996 canonical + 29 isoform variants). Each hit renders real
 * alignment metrics; hits open the enzyme detail page and can be added to the
 * download list. State survives close/reopen so a previous result is still
 * there when the drawer is opened again.
 */

const SOURCE_LABELS: Record<string, string> = { swiss_prot: 'Swiss-Prot', trembl: 'TrEMBL' }
const MIN_AA = 15

const THRESHOLDS: Array<{ label: string; value: number }> = [
  { label: '1e-3', value: 1e-3 },
  { label: '1e-5', value: 1e-5 },
  { label: '1e-10', value: 1e-10 },
  { label: '10 (lenient)', value: 10 },
]

function compactProtein(value: string): string {
  return value
    .replace(/^>.*$/gm, '')
    .replace(/[^A-Za-z]/g, '')
    .toUpperCase()
}

function entityField(label: string, rawValue: string | number | null | undefined) {
  if (rawValue === null || rawValue === undefined || rawValue === '') return null
  return { label, value: String(rawValue) }
}

function hitToEntity(hit: BlastHit): Entity {
  const card = hit.card
  return {
    id: hit.enzymeId,
    kind: 'enzyme',
    name: card?.primaryName || hit.enzymeId,
    subtitle: card?.uniprotId || card?.databaseCode || hit.enzymeId,
    description: card?.reactionEquation || 'Enzyme record from the terpene pathway database.',
    tags: ['Enzyme'],
    species: card?.organismName || undefined,
    fields: [
      entityField('UniProt', card?.uniprotId),
      entityField('Organism', card?.organismName),
      entityField('Gene name', card?.geneName),
      entityField('EC number', card?.ecNumber),
      entityField('Identity', `${hit.identity.toFixed(1)}%`),
      entityField('E-value', hit.eValue.toExponential(2)),
      entityField('Query coverage', `${hit.queryCover.toFixed(1)}%`),
      entityField('Bitscore', hit.bitscore.toFixed(1)),
    ].filter(Boolean) as Array<{ label: string; value: string }>,
    related: [],
  }
}

function thresholdLabel(value: number): string {
  return value === 10 ? '10' : value.toExponential(0)
}

export function BlastDrawer({
  open,
  onClose,
  onOpenDownloads,
  onOpenEnzyme,
  onToggleQueue,
  isQueued,
  queueCount,
  onOpenResults,
}: {
  open: boolean
  onClose: () => void
  onOpenDownloads: () => void
  onOpenEnzyme: (enzymeId: string) => void
  onToggleQueue: (entry: string | Entity) => void
  isQueued: (id: string) => boolean
  queueCount: number
  /** Open this run in the shared keyword-style result views (table form or map). */
  onOpenResults: (payload: BlastPayload, mode: 'table' | 'map') => void
}) {
  const [draft, setDraft] = useState('')
  const [threshold, setThreshold] = useState(1e-5)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [payload, setPayload] = useState<BlastPayload | null>(null)
  const [loadedOnce, setLoadedOnce] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  // Keep the box editable while browsing results for a different query.
  const invalidReason = useMemo(() => {
    const compact = compactProtein(draft)
    if (draft.trim() && compact.length < MIN_AA) return `Sequence too short (${compact.length} aa) — need at least ${MIN_AA} residues.`
    return null
  }, [draft])

  const loadExample = async () => {
    setError(null)
    try {
      const detail = await loadEnzymeDetail('ENZ000097')
      setDraft(detail.sequence || '')
      setLoadedOnce(true)
    } catch {
      setError('Could not load an example sequence.')
    }
  }

  const runSearch = async () => {
    const seq = draft.trim()
    if (!seq) {
      setError('Paste a protein sequence first.')
      return
    }
    if (compactProtein(seq).length < MIN_AA) {
      setError(invalidReason || 'Sequence too short.')
      return
    }
    setRunning(true)
    setError(null)
    try {
      const result = await runBlastSearch({ sequence: seq, eValueThreshold: threshold, maxResults: 100 })
      setPayload(result)
      setLoadedOnce(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'BLAST search failed.')
      setPayload(null)
    } finally {
      setRunning(false)
    }
  }

  const hits = payload?.hits ?? []
  const hitCount = hits.length

  return createPortal(
    <div className={`blast-drawer-layer ${open ? 'is-open' : ''}`} aria-hidden={!open}>
      <div className="blast-drawer-backdrop" onClick={onClose} />
      <aside className="blast-drawer" role="dialog" aria-modal="true" aria-label="Sequence search (BLASTp)">
        <div className="blast-drawer-head">
          <div className="blast-drawer-title">
            <span className="blast-drawer-icon">
              <ScanSearch size={17} />
            </span>
            <div>
              <div className="blast-drawer-kicker">Sequence search · BLASTp</div>
              <h2>Find homologous enzymes</h2>
            </div>
          </div>
          <button className="blast-drawer-close" type="button" onClick={onClose} title="Close">
            <X size={18} />
          </button>
        </div>

        <div className="blast-query-card">
          <div className="blast-query-head">
            <div className="blast-query-title">
              <strong>Paste a protein sequence</strong>
              <small>FASTA header optional · at least {MIN_AA} amino acids</small>
            </div>
            <span className="blast-pool-chip">1,025 subjects</span>
          </div>

          <textarea
            className="blast-query-input"
            rows={6}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) void runSearch()
            }}
            placeholder={'e.g.\n\n>putative terpene synthase\nMS… (paste the whole protein)'}
            aria-label="Protein sequence to search"
            spellCheck={false}
          />

          <div className="blast-query-actions">
            <div className="blast-threshold" role="group" aria-label="E-value threshold">
              <span className="blast-threshold-label">E-value ≤</span>
              {THRESHOLDS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={threshold === option.value ? 'on' : ''}
                  onClick={() => setThreshold(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <div className="blast-query-buttons">
              {draft.trim() && (
                <button type="button" className="blast-clear" onClick={() => { setDraft(''); setPayload(null); setError(null) }} title="Clear query">
                  <X size={13} />
                  Clear
                </button>
              )}
              <button type="button" className="blast-example" onClick={() => void loadExample()} title="Fill the box with a library enzyme sequence">
                <FlaskConical size={14} />
                Example
              </button>
              <button type="button" className="blast-run" onClick={() => void runSearch()} disabled={running || !draft.trim()}>
                {running ? <Loader2 size={15} className="spin" /> : <ScanSearch size={15} />}
                {running ? 'Searching…' : 'Run BLAST'}
              </button>
            </div>
          </div>
          {invalidReason && <p className="blast-query-error">{invalidReason}</p>}
        </div>

        <div className="blast-drawer-scroll">
          {error && !running && (
            <div className="blast-error-panel">
              <X size={16} />
              <span>{error}</span>
            </div>
          )}

          {running && (
            <div className="blast-feedback">
              <Loader2 size={18} className="spin" />
              Aligning against 1,025 enzyme subjects…
            </div>
          )}

          {!running && payload && hitCount === 0 && (
            <div className="blast-empty-panel">
              <ScanSearch size={22} />
              <h3>No significant hits</h3>
              <p>Nothing matched above the E-value threshold (≤ {thresholdLabel(threshold)}). Try a looser threshold or a longer sequence.</p>
            </div>
          )}

          {!running && payload && hitCount > 0 && (
            <div className="blast-results">
              <div className="blast-result-summary">
                <span>
                  Query <strong>{payload.queryLength}</strong> aa · searched <strong>{payload.searchedSubjects}</strong> subjects · threshold ≤ {thresholdLabel(payload.threshold)}
                </span>
                <strong>{hitCount} hit{hitCount === 1 ? '' : 's'}</strong>
              </div>

              <div className="blast-view-bar">
                <div className="blast-view-copy">
                  <strong>Review in the library results</strong>
                  <span>Shown with the same table / map views as a keyword search — the filters work there too.</span>
                </div>
                <div className="blast-view-actions">
                  <button type="button" className="blast-view-action" onClick={() => onOpenResults(payload, 'table')}>
                    Table results <ArrowUpRight size={13} />
                  </button>
                  <button type="button" className="blast-view-action map" onClick={() => onOpenResults(payload, 'map')}>
                    View on map <ArrowUpRight size={13} />
                  </button>
                </div>
              </div>

              <div className="blast-hit-list">
                {hits.map((hit, index) => {
                  const queued = isQueued(hit.enzymeId)
                  const card = hit.card
                  return (
                    <article key={`${hit.enzymeId}:${hit.isoformId || 'canonical'}`} className={`blast-hit ${hit.subjectType === 'isoform' ? 'isoform' : ''}`}>
                      <div className="blast-hit-title-row">
                        <span className="blast-hit-rank">{index + 1}</span>
                        <h3 title={card?.primaryName || hit.enzymeId}>{card?.primaryName || hit.enzymeId}</h3>
                        {hit.subjectType === 'isoform' ? (
                          <span className="blast-subject-tag isoform" title={`Isoform variant ${hit.isoformId}`}>isoform {hit.isoformId} · {hit.subjectLength} aa</span>
                        ) : (
                          <span className="blast-subject-tag canonical">canonical</span>
                        )}
                      </div>
                      <div className="blast-hit-idrow">
                        {card?.uniprotId ? (
                          <a className="blast-hit-uniprot" href={`https://www.uniprot.org/uniprotkb/${card.uniprotId}`} target="_blank" rel="noreferrer" title={`Open ${card.uniprotId} on UniProt`}>
                            <span className="blast-hit-uniprot-key">UniProt</span>
                            <strong>{card.uniprotId}</strong>
                            <ExternalLink size={11} />
                          </a>
                        ) : (
                          <span className="blast-hit-uniprot is-missing" title="No UniProt entry in the library">
                            <span className="blast-hit-uniprot-key">Library</span>
                            <strong>{card?.databaseCode || hit.enzymeId}</strong>
                          </span>
                        )}
                        {card?.geneName && <span className="blast-hit-gene"><small>gene</small> {card.geneName}</span>}
                      </div>
                      <p className="blast-hit-meta">
                        {[card?.organismName].filter(Boolean).join(' · ') || hit.enzymeId}
                      </p>
                      <div className="blast-hit-stats">
                        <span title="Percent identity"><strong>{hit.identity.toFixed(1)}%</strong> identity</span>
                        <span title="Query coverage"><strong>{hit.queryCover.toFixed(0)}%</strong> query cover</span>
                        <span title="Alignment length"><strong>{hit.alignmentLength}</strong> aligned aa</span>
                        <span title="E-value"><strong>{hit.eValue.toExponential(2)}</strong> E-value</span>
                        <span title="Bit score"><strong>{hit.bitscore.toFixed(1)}</strong> bitscore</span>
                      </div>
                      <div className="blast-hit-actions">
                        {card?.sourceType && card.sourceType in SOURCE_LABELS && (
                          <span className={`search-table-source-tag ${card.sourceType}`}>{SOURCE_LABELS[card.sourceType]}</span>
                        )}
                        <button
                          type="button"
                          className={`blast-queue ${queued ? 'checked' : ''}`}
                          onClick={() => onToggleQueue(queued ? hit.enzymeId : hitToEntity(hit))}
                          title={queued ? 'Remove from download list' : 'Add to download list'}
                        >
                          {queued ? <Check size={15} /> : <Download size={15} />}
                        </button>
                        <button type="button" className="blast-open-link" onClick={() => onOpenEnzyme(hit.enzymeId)}>
                          Open detail <ArrowUpRight size={13} />
                        </button>
                      </div>
                    </article>
                  )
                })}
              </div>
            </div>
          )}

          {!running && !payload && !error && (
            <div className="blast-drawer-idle">
              <h3>Sequence search against the enzyme library</h3>
              <p>
                Paste a protein and hit <strong>Run BLAST</strong>. The local NCBI BLASTp aligns it against every reviewed enzyme in the library
                (996 canonical sequences plus 29 isoform variants), then lists the top hits by E-value.
              </p>
              <button type="button" className="blast-example-inline" onClick={() => void loadExample()}>
                <FlaskConical size={14} />
                Try a library sequence
              </button>
            </div>
          )}
        </div>

        <div className="blast-drawer-footer">
          <span className="blast-drawer-footer-copy">
            {loadedOnce || payload
              ? queueCount > 0
                ? `${queueCount} enzyme${queueCount === 1 ? '' : 's'} in the download list`
                : 'Download list is empty'
              : 'No enzymes selected yet'}
          </span>
          <button className="blast-drawer-download" type="button" onClick={onOpenDownloads} title="Open download list">
            <Download size={14} />
            <span>Downloading table</span>
            {queueCount > 0 && <span className="download-list-badge">{queueCount}</span>}
          </button>
        </div>
      </aside>
    </div>,
    document.body,
  )
}
