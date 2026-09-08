import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, Check, Download, Loader2, Network, Search, X } from 'lucide-react'
import { searchTableEnzymes, searchTableEnzymesByIds } from '../api'
import type { BlastHit, BlastSession, TableEnzymeRow } from '../api'
import { StructureSearchDrawer } from '../components/StructureSearchDrawer'
import type { Entity } from '../types'

/**
 * Table-form search results page ("Table" half of the home Map/Table toggle).
 *
 * Reuses the home page top bar (brand + Map/Table search box) plus a
 * download-list button to its left. Below it, a filter column (data source,
 * organism, EC prefixes) only narrows the enzyme rows returned for the current
 * query; the right region lists one enzyme card per match. Each card can be
 * added to the download list.
 */

export const SOURCE_LABELS: Record<string, string> = {
  swiss_prot: 'Swiss-Prot',
  trembl: 'TrEMBL',
}

const HOME_SEARCH_PLACEHOLDER = 'Search limonene, germacrene D synthase, EC 4.2.3.75, Q9ZSY2…'

function entityField(label: string, rawValue: string | number | null | undefined) {
  if (rawValue === null || rawValue === undefined || rawValue === '') return null
  return { label, value: String(rawValue) }
}

function rowToEntity(row: TableEnzymeRow): Entity {
  const ecs = presentEcNumbers(row)
  return {
    id: row.enzymeId,
    kind: 'enzyme',
    name: row.primaryName,
    subtitle: [row.uniprotId || row.enzymeId, ecs[0]].filter(Boolean).join(' · '),
    description: ecs.length > 0 ? `Catalyses EC ${ecs.join(', ')}.` : 'Enzyme record from the terpene pathway library.',
    tags: (row.sourceTypes.length > 0 ? row.sourceTypes.map((source) => SOURCE_LABELS[source] || source) : ['Enzyme']),
    species: row.organismName || undefined,
    fields: [
      entityField('UniProt', row.uniprotId),
      entityField('Organism', row.organismName),
      entityField('Gene name', row.geneName),
      entityField('EC numbers', ecs.join(', ') || null),
      entityField('Data source', row.sourceTypes.map((source) => SOURCE_LABELS[source] || source).join(', ')),
      entityField('Reactions', row.reactionCount > 0 ? `${row.reactionCount} reaction(s)` : null),
    ].filter(Boolean) as Array<{ label: string; value: string }>,
    related: [],
  }
}

/** EC strings that are only placeholders ("--", "-") are not real EC numbers. */
function isEcPlaceholder(ec: string) {
  return /^-+$/.test(ec.trim())
}

function presentEcNumbers(row: TableEnzymeRow) {
  return (row.ecNumbers || []).filter((ec) => ec && !isEcPlaceholder(ec))
}

function ecGroups(ec: string): string[] {
  const value = ec.replace(/^EC\s*/i, '').trim()
  if (!value) return []
  return value.split('.').map((part) => part.trim()).filter(Boolean)
}

function prefixMatches(ec: string, prefix: string[]): boolean {
  const groups = ecGroups(ec)
  if (groups.length < prefix.length) return false
  return prefix.every((part, index) => groups[index] === part)
}

/**
 * Parse the EC filter box. Entries are separated by commas / spaces / newlines.
 * Each entry is a left-to-right EC prefix (up to 4 dot-separated digits, e.g.
 * `4.2`, `4.2.3`, `4.2.3.77`); unfilled trailing digits do not narrow results.
 * A trailing dot after the last typed digit is tolerated.
 */
function parseEcFilter(text: string): { prefixes: string[][]; invalid: string | null } {
  const prefixes: string[][] = []
  let invalid: string | null = null
  text
    .split(/[\s,，;；、]+/)
    .map((token) => token.trim())
    .filter(Boolean)
    .forEach((token) => {
      if (invalid) return
      if (!/^[\d.]+$/.test(token)) {
        invalid = token
        return
      }
      let groups = token.split('.')
      if (groups[groups.length - 1] === '') groups = groups.slice(0, -1)
      if (groups.length === 0 || groups.length > 4) {
        invalid = token
        return
      }
      if (!groups.every((part) => /^\d+$/.test(part))) {
        invalid = token
        return
      }
      prefixes.push(groups)
    })
  return { prefixes, invalid }
}

function rowMatchesEc(row: TableEnzymeRow, prefixes: string[][]): boolean {
  if (prefixes.length === 0) return true
  const ecs = presentEcNumbers(row)
  if (ecs.length === 0) return false
  return ecs.some((ec) => prefixes.some((prefix) => prefixMatches(ec, prefix)))
}

/** One table row rendered in this page — either a keyword hit or a BLAST hit
 *  (the latter merges the rich enzyme row with the per-subject BLAST metrics). */
export type DisplayRow = TableEnzymeRow & {
  blastHit?: {
    rank: number
    eValue: number
    identity: number
    queryCover: number
    alignmentLength: number
    bitscore: number
    subjectType: 'canonical' | 'isoform'
    isoformId?: string | null
    subjectLength: number
  }
}

function blastThresholdLabel(value: number): string {
  return value === 10 ? '10' : value.toExponential(0)
}

function formatEValue(value: number): string {
  if (value === 0) return '0'
  if (value >= 0.01) return value.toFixed(3)
  return value.toExponential(2)
}

function displayRowToEntity(row: DisplayRow): Entity {
  const base = rowToEntity(row)
  const hit = row.blastHit
  if (!hit) return base
  return {
    ...base,
    fields: [
      ...base.fields,
      entityField('BLAST identity', `${hit.identity.toFixed(1)}%`),
      entityField('BLAST E-value', formatEValue(hit.eValue)),
      entityField('BLAST query coverage', `${hit.queryCover.toFixed(1)}%`),
      entityField('BLAST bitscore', hit.bitscore.toFixed(1)),
    ].filter(Boolean) as Array<{ label: string; value: string }>,
  }
}

/** Fallback rich row built from the representative card when the by-ids fetch fails. */
function cardFallbackRow(hit: BlastHit): TableEnzymeRow {
  const card = hit.card
  return {
    enzymeId: hit.enzymeId,
    primaryName: card?.primaryName || hit.enzymeId,
    uniprotId: card?.uniprotId ?? null,
    organismName: card?.organismName ?? null,
    geneName: card?.geneName ?? null,
    ecNumbers: card?.ecNumber && !isEcPlaceholder(card.ecNumber) ? [card.ecNumber] : [],
    sourceTypes: card?.sourceType ? [card.sourceType] : [],
    reactionCount: card ? 1 : 0,
  }
}

export function SearchResultsPage({
  query,
  setQuery,
  onOpenMap,
  onOpenDownloads,
  onOpenEnzyme,
  onOpenBlast,
  onToggleQueue,
  isQueued,
  queueCount,
  blastSession,
  onExitBlast,
  onOpenBlastMap,
  onResetHome,
}: {
  query: string
  setQuery: (value: string) => void
  onOpenMap: (query: string) => void
  onOpenDownloads: () => void
  onOpenEnzyme: (enzymeId: string) => void
  onOpenBlast: () => void
  onToggleQueue: (entry: string | Entity) => void
  isQueued: (id: string) => boolean
  queueCount: number
  /** When set, this page shows the BLAST run through the keyword result views
   *  instead of the text-query table: rows arrive in E-value order and each
   *  card is annotated with the hit E-value. */
  blastSession?: BlastSession | null
  onExitBlast: () => void
  onOpenBlastMap: () => void
  /** Brand click → clear app-wide search state and land on the home map. */
  onResetHome: () => void
}) {
  const [draft, setDraft] = useState(query)
  const [payload, setPayload] = useState<{ items: TableEnzymeRow[]; total: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runNonce, setRunNonce] = useState(0)

  const [blastRows, setBlastRows] = useState<DisplayRow[] | null>(null)
  const [blastLoading, setBlastLoading] = useState(false)
  const [blastRowsError, setBlastRowsError] = useState<string | null>(null)

  const [selectedSources, setSelectedSources] = useState<string[]>([])
  const [selectedOrganisms, setSelectedOrganisms] = useState<string[]>([])
  const [organismQuery, setOrganismQuery] = useState('')
  const [ecText, setEcText] = useState('')
  const [structureOpen, setStructureOpen] = useState(false)

  const blastMode = Boolean(blastSession)
  const blastPayload = blastSession?.payload ?? null
  const blastHitTotal = blastPayload?.hits.length ?? 0

  // Keep the editable box in sync when the active query changes elsewhere.
  useEffect(() => {
    setDraft(query)
  }, [query])

  // Entering/leaving a BLAST session resets the client-side filters.
  useEffect(() => {
    setSelectedSources([])
    setSelectedOrganisms([])
    setOrganismQuery('')
    setEcText('')
  }, [blastSession?.id])

  const activeQuery = query.trim()

  // Keyword table search — suspended while a BLAST session is being shown.
  useEffect(() => {
    if (blastSession) {
      setPayload(null)
      setLoading(false)
      setError(null)
      return
    }
    const q = query.trim()
    if (!q) {
      setPayload(null)
      setLoading(false)
      setError(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    const timer = window.setTimeout(() => {
      searchTableEnzymes({ q })
        .then((result) => {
          if (cancelled) return
          setPayload(result)
        })
        .catch((err) => {
          if (cancelled) return
          setError(err instanceof Error ? err.message : 'Table search failed')
          setPayload(null)
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }, 120)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [query, runNonce, blastSession])

  // Fetch the rich per-enzyme rows for the BLAST hits (same aggregation the
  // keyword table filters on), merged with per-hit E-value / alignment metrics.
  useEffect(() => {
    const session = blastSession
    if (!session) {
      setBlastRows(null)
      setBlastRowsError(null)
      setBlastLoading(false)
      return
    }
    let cancelled = false
    setBlastLoading(true)
    setBlastRowsError(null)
    const hits = session.payload.hits
    const ids = hits.map((hit) => hit.enzymeId)
    const mergeHits = (source: TableEnzymeRow[]): DisplayRow[] => {
      const pool = source.slice()
      return hits.map((hit, index) => {
        const poolIndex = pool.findIndex((row) => row.enzymeId === hit.enzymeId)
        const row = poolIndex >= 0 ? pool.splice(poolIndex, 1)[0] : cardFallbackRow(hit)
        return {
          ...row,
          blastHit: {
            rank: index + 1,
            eValue: hit.eValue,
            identity: hit.identity,
            queryCover: hit.queryCover,
            alignmentLength: hit.alignmentLength,
            bitscore: hit.bitscore,
            subjectType: hit.subjectType,
            isoformId: hit.isoformId,
            subjectLength: hit.subjectLength,
          },
        }
      })
    }
    searchTableEnzymesByIds(ids)
      .then((result) => {
        if (cancelled) return
        setBlastRows(mergeHits(result.items))
      })
      .catch((err) => {
        if (cancelled) return
        setBlastRowsError(err instanceof Error ? err.message : 'Could not enrich the BLAST hits.')
        setBlastRows(mergeHits([]))
      })
      .finally(() => {
        if (!cancelled) setBlastLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [blastSession])

  const baseRows = blastSession ? (blastRows ?? []) : (payload?.items ?? [])

  const organismCounts = useMemo(() => {
    const counts = new Map<string, number>()
    baseRows.forEach((row) => {
      const name = row.organismName || 'Unknown organism'
      counts.set(name, (counts.get(name) || 0) + 1)
    })
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [baseRows])

  const visibleOrganisms = useMemo(() => {
    const needle = organismQuery.trim().toLowerCase()
    if (!needle) return organismCounts
    return organismCounts.filter(([name]) => name.toLowerCase().includes(needle))
  }, [organismCounts, organismQuery])

  const ecFilter = useMemo(() => parseEcFilter(ecText), [ecText])
  const anyFilterActive = selectedSources.length > 0 || selectedOrganisms.length > 0 || ecFilter.prefixes.length > 0

  const filteredItems = useMemo(() => {
    const sourceSet = new Set(selectedSources)
    const organismSet = new Set(selectedOrganisms)
    return baseRows.filter((row) => {
      if (selectedSources.length > 0 && !row.sourceTypes.some((source) => sourceSet.has(source))) return false
      if (selectedOrganisms.length > 0 && !(row.organismName && organismSet.has(row.organismName))) return false
      if (!rowMatchesEc(row, ecFilter.prefixes)) return false
      return true
    })
  }, [baseRows, selectedSources, selectedOrganisms, ecFilter.prefixes])

  const resetFilters = () => {
    setSelectedSources([])
    setSelectedOrganisms([])
    setOrganismQuery('')
    setEcText('')
  }

  const submitSearch = (text: string) => {
    const trimmed = text.trim()
    setDraft(trimmed)
    if (trimmed === activeQuery) {
      setRunNonce((nonce) => nonce + 1)
    } else {
      setQuery(trimmed)
    }
  }

  /** Search-box submit: leaving the BLAST session falls back to keyword mode. */
  const submitKeywordSearch = (text: string) => {
    if (blastSession) {
      onExitBlast()
      submitSearch(text)
      return
    }
    submitSearch(text)
  }

  const toggleOrganism = (organism: string) => {
    setSelectedOrganisms((current) => (current.includes(organism) ? current.filter((item) => item !== organism) : [...current, organism]))
  }

  const goToMap = () => {
    if (blastSession) {
      onOpenBlastMap()
      return
    }
    onOpenMap(draft.trim() || activeQuery)
  }

  const hasQuery = activeQuery.length > 0

  // One result card, shared by the keyword and BLAST branches. A BLAST row adds
  // its E-value (and rank / isoform / alignment stats) so the hit is traceable.
  const renderRowCard = (row: DisplayRow) => {
    const queued = isQueued(row.enzymeId)
    const ecs = presentEcNumbers(row)
    const hit = row.blastHit
    return (
      <article key={`${row.enzymeId}:${hit ? hit.rank : 'row'}`} className={`search-table-card ${hit ? 'has-blast-hit' : ''}`}>
        <div className="search-table-card-head">
          <div className="search-table-card-titles">
            <h3>{row.primaryName}</h3>
            <p>{row.organismName || 'Unknown organism'}</p>
          </div>
          <button
            type="button"
            className={`search-table-card-check ${queued ? 'checked' : ''}`}
            onClick={() => onToggleQueue(queued ? row.enzymeId : displayRowToEntity(row))}
            title={queued ? 'Remove from download list' : 'Add to download list'}
          >
            {queued ? <Check size={18} /> : <Download size={18} />}
          </button>
        </div>

        {hit && (
          <div className="blast-table-hit">
            <span className="blast-table-rank">#{hit.rank}</span>
            <span className={`blast-subject-tag ${hit.subjectType}`}>
              {hit.subjectType === 'isoform' ? `isoform ${hit.isoformId} · ${hit.subjectLength} aa` : 'canonical'}
            </span>
            <span className="blast-table-evalue" title="BLAST E-value">E-value {formatEValue(hit.eValue)}</span>
            <span className="blast-table-align">
              {hit.identity.toFixed(1)}% identity · {hit.queryCover.toFixed(0)}% query cover · score {hit.bitscore.toFixed(1)}
            </span>
          </div>
        )}

        <div className="search-table-card-id">
          <div>
            <small>UniProt entry</small>
            <strong>{row.uniprotId || row.enzymeId}</strong>
          </div>
          {row.geneName ? (
            <div>
              <small>Gene</small>
              <strong>{row.geneName}</strong>
            </div>
          ) : null}
        </div>

        <div className="search-table-ec-panel">
          <div className="search-table-ec-caption">Catalyses (EC)</div>
          {ecs.length > 0 ? (
            <div className="search-table-ec-chips">
              {ecs.map((ec) => (
                <span key={ec} className="search-table-ec-chip">{ec}</span>
              ))}
            </div>
          ) : (
            <span className="search-table-ec-na">Not annotated</span>
          )}
        </div>

        <div className="search-table-card-foot">
          <span className="search-table-card-meta">
            {row.sourceTypes.length > 0 && row.sourceTypes.map((source) => <span key={source} className={`search-table-source-tag ${source}`}>{SOURCE_LABELS[source] || source}</span>)}
            {row.reactionCount > 0 && <span className="search-table-reaction-count">{row.reactionCount} reaction{row.reactionCount === 1 ? '' : 's'}</span>}
          </span>
          <button type="button" className="search-table-open-link" onClick={() => onOpenEnzyme(row.enzymeId)}>
            Open detail <ArrowUpRight size={13} />
          </button>
        </div>
      </article>
    )
  }

  return (
    <div className="home-map-page search-table-page">
      <section className="search-table-stage">
        <header className="graph-top-nav search-table-topnav">
          <button type="button" className="atlas-brand search-table-brand" onClick={onResetHome} title="Back to the Atlas home map" aria-label="Starase Atlas home">
            <span className="atlas-logo">
              <Network size={18} />
            </span>
            <span>Starase Atlas</span>
          </button>

          <div className="search-table-search-slot">
            <button className="download-list-button" type="button" onClick={onOpenDownloads} title="Open download list">
              <Download size={15} />
              <span>Downloading table</span>
              {queueCount > 0 && <span className="download-list-badge">{queueCount}</span>}
            </button>

            <div className="home-search-bar search-table-search-bar">
              <span className="home-search-enzyme-tag">Search</span>
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') submitKeywordSearch(draft)
                }}
                placeholder={HOME_SEARCH_PLACEHOLDER}
                aria-label="Search the enzyme library"
              />
              <div className="home-result-toggle" role="group" aria-label="Search result view">
                <button type="button" onClick={goToMap}>Map</button>
                <button type="button" className="is-active" aria-current="page">Table</button>
              </div>
              <button className="home-search-submit" type="button" onClick={() => submitKeywordSearch(draft)} title="Search">
                <Search size={18} />
              </button>
            </div>
          </div>

          <nav className="graph-primary-nav" aria-label="Table results navigation">
            <button type="button" onClick={onOpenBlast}>BLAST</button>
            <button type="button" onClick={() => setStructureOpen(true)}>Structure search</button>
            <span className="graph-user-chip">NJU - China 2026</span>
          </nav>
        </header>

        <div className="search-table-body">
          <aside className="search-table-filter" aria-label="Table result filters">
            <div className="search-table-filter-title">Filters</div>

            <div className="search-table-filter-group">
              <div className="search-table-filter-label">Data source</div>
              <div className="search-table-source-row">
                {Object.entries(SOURCE_LABELS).map(([value, label]) => {
                  const active = selectedSources.includes(value)
                  return (
                    <button
                      key={value}
                      type="button"
                      className={`search-table-chip ${active ? 'on' : ''}`}
                      onClick={() => setSelectedSources((current) => (current.includes(value) ? current.filter((item) => item !== value) : [...current, value]))}
                      title={active ? `Remove ${label}` : `Filter by ${label}`}
                    >
                      {label}
                      <small>{value === 'trembl' ? 'unreviewed' : 'reviewed'}</small>
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="search-table-filter-group">
              <div className="search-table-filter-label">Organism</div>
              <div className="search-table-org-search">
                <Search size={13} />
                <input value={organismQuery} onChange={(event) => setOrganismQuery(event.target.value)} placeholder="Search organisms…" />
                {organismQuery && (
                  <button type="button" onClick={() => setOrganismQuery('')} title="Clear organism search">
                    <X size={12} />
                  </button>
                )}
              </div>
              <div className="search-table-org-list">
                {visibleOrganisms.length > 0 ? (
                  visibleOrganisms.map(([organism, count]) => {
                    const checked = selectedOrganisms.includes(organism)
                    return (
                      <button key={organism} type="button" className={`search-table-org-item ${checked ? 'is-checked' : ''}`} onClick={() => toggleOrganism(organism)} title={checked ? `Remove ${organism}` : `Filter by ${organism}`}>
                        <span className={`search-table-check ${checked ? 'checked' : ''}`}>{checked && <Check size={11} />}</span>
                        <span className="search-table-org-name">{organism}</span>
                        <span className="search-table-org-count">{count}</span>
                      </button>
                    )
                  })
                ) : (
                  <div className="search-table-filter-empty">No organisms in the current results.</div>
                )}
              </div>
            </div>

            <div className="search-table-filter-group search-table-ec-group">
              <div className="search-table-filter-label">EC number</div>
              <input
                className={`search-table-ec-input ${ecFilter.invalid ? 'invalid' : ''}`}
                value={ecText}
                onChange={(event) => setEcText(event.target.value)}
                placeholder="e.g. 4.2.3, 1.14.13.100"
                aria-label="EC number prefix filter"
              />
              <p className="search-table-ec-hint">Enter one or more 4-part EC numbers. Fill digits left to right; empty trailing positions do not filter.</p>
              {ecFilter.invalid && <p className="search-table-ec-error">“{ecFilter.invalid}” is not a valid EC prefix (digits and dots only, up to 4 digits).</p>}
            </div>

            <button className="search-table-reset" type="button" onClick={resetFilters} disabled={!anyFilterActive}>
              <X size={13} />
              Reset filters
            </button>
          </aside>

          <div className="search-table-main">
            {blastMode ? (
              <>
                {blastPayload && (
                  <div className="blast-results-context">
                    <div className="blast-results-context-copy">
                      <span className="blast-results-context-kicker">Sequence search · BLASTp</span>
                      <strong>BLAST results — {blastHitTotal} hit{blastHitTotal === 1 ? '' : 's'} from a {blastPayload.queryLength} aa query</strong>
                      <span>threshold E-value ≤ {blastThresholdLabel(blastPayload.threshold)} · {blastPayload.searchedSubjects} subjects searched · ranked by E-value · same table and filters as a keyword search.</span>
                    </div>
                    <div className="blast-results-context-actions">
                      <button className="blast-results-context-map" type="button" onClick={onOpenBlastMap} title="Show the hit enzymes as a scope subgraph on the map">
                        View on map <ArrowUpRight size={14} />
                      </button>
                      <button className="blast-results-context-exit" type="button" onClick={onExitBlast} title="Back to plain keyword library results">
                        <X size={13} />
                        Exit BLAST results
                      </button>
                    </div>
                  </div>
                )}

                {blastRowsError && (
                  <div className="search-table-feedback blast-enrich-note">
                    <X size={15} />
                    {blastRowsError} Showing per-hit cards.
                  </div>
                )}

                <div className="search-table-summary">
                  <div className="search-table-summary-query">
                    <span>BLAST hits</span>
                    {anyFilterActive && (
                      <button type="button" onClick={resetFilters} title="Clear all filters">
                        <X size={12} />
                      </button>
                    )}
                  </div>
                  <div className="search-table-result-count">
                    {blastLoading && blastRows === null ? (
                      <>
                        <Loader2 size={14} className="spin" />
                        Enriching…
                      </>
                    ) : (
                      <>
                        <strong>{filteredItems.length}</strong>
                        {blastHitTotal > 0 && filteredItems.length < blastHitTotal ? ` of ${blastHitTotal}` : ''} hit{filteredItems.length === 1 ? '' : 's'}
                      </>
                    )}
                  </div>
                </div>

                {blastLoading && blastRows === null && (
                  <div className="search-table-feedback">
                    <Loader2 size={18} className="spin" />
                    Enriching BLAST hits with reaction rows…
                  </div>
                )}

                {blastRows && blastRows.length === 0 && (
                  <div className="search-table-no-results">
                    <h3>No BLAST hits</h3>
                    <p>The alignment returned no significant hits above the chosen threshold.</p>
                  </div>
                )}

                {blastRows && blastRows.length > 0 && filteredItems.length === 0 && (
                  <div className="search-table-no-results">
                    <h3>No enzymes match the current filters</h3>
                    <button type="button" onClick={resetFilters}>Reset filters</button>
                  </div>
                )}

                {blastRows && blastRows.length > 0 && filteredItems.length > 0 && (
                  <div className="search-table-card-grid">
                    {filteredItems.map(renderRowCard)}
                  </div>
                )}
              </>
            ) : !hasQuery ? (
              <div className="search-table-empty-state">
                <div className="search-table-empty-icon">
                  <Search size={26} />
                </div>
                <h2>Search the enzyme library</h2>
                <p>Type a compound name, enzyme name, UniProt entry or EC number above and press Enter to list the matching enzymes as table cards.</p>
              </div>
            ) : (
              <>
                <div className="search-table-summary">
                  <div className="search-table-summary-query">
                    <span>Results for</span>
                    <strong>“{activeQuery}”</strong>
                    {anyFilterActive && (
                      <button type="button" onClick={resetFilters} title="Clear all filters">
                        <X size={12} />
                      </button>
                    )}
                  </div>
                  <div className="search-table-result-count">
                    {loading ? (
                      <>
                        <Loader2 size={14} className="spin" />
                        Searching…
                      </>
                    ) : (
                      <>
                        <strong>{filteredItems.length}</strong>
                        {payload && filteredItems.length < payload.total ? ` of ${payload.total}` : ''} enzyme result{filteredItems.length === 1 ? '' : 's'}
                      </>
                    )}
                  </div>
                </div>

                {loading && !payload && (
                  <div className="search-table-feedback">
                    <Loader2 size={18} className="spin" />
                    Searching the enzyme library…
                  </div>
                )}

                {error && !loading && (
                  <div className="search-table-feedback error-state">
                    <X size={18} />
                    {error}
                  </div>
                )}

                {!loading && payload && payload.items.length === 0 && (
                  <div className="search-table-no-results">
                    <h3>No matching enzymes</h3>
                    <p>Nothing in the database matches “{activeQuery}”. Try a compound name, enzyme name, UniProt entry or EC number.</p>
                  </div>
                )}

                {!loading && payload && payload.items.length > 0 && filteredItems.length === 0 && (
                  <div className="search-table-no-results">
                    <h3>No enzymes match the current filters</h3>
                    <button type="button" onClick={resetFilters}>Reset filters</button>
                  </div>
                )}

                {!loading && filteredItems.length > 0 && (
                  <div className="search-table-card-grid">
                    {filteredItems.map(renderRowCard)}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </section>

      <StructureSearchDrawer
        open={structureOpen}
        onClose={() => setStructureOpen(false)}
        onTransferChebi={(chebiId) => {
          // Put the matched compound's ChEBI in the keyword box only — the user
          // presses Enter / the search button to actually run it.
          setDraft(chebiId)
        }}
      />
    </div>
  )
}
