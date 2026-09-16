import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowUpRight,
  Check,
  Download,
  ExternalLink,
  Loader2,
  Network,
  Route,
  Search,
  X,
} from 'lucide-react'
import type { Entity } from '../types'
import { getExternalRecordUrl, kindIcons, kindLabels } from '../lib/entities'
import { createDownload, fetchDownloadFields, previewDownload } from '../api'
import type {
  DownloadFieldCatalog,
  DownloadItem,
  DownloadPreview,
  DownloadRequest,
  DownloadResult,
} from '../api'
import { fileNameFromUrl, saveFile } from '../lib/saveFile'
import { StructureSearchDrawer } from '../components/StructureSearchDrawer'

type Tab = 'enzymes' | 'pathways'

/** The tabs are plural, the backend's page names are singular — `formats` and
 *  `downloadType` both key off the singular form. */
const PAGE: Record<Tab, 'enzyme' | 'pathway'> = { enzymes: 'enzyme', pathways: 'pathway' }

/** The main table is a fixed-column queue overview and deliberately does not
 *  mirror the export setup, so each tab has to say what its download produces. */
const TAB_EXPORT_NOTE: Record<Tab, string> = {
  enzymes:
    'One row per queued enzyme. Pick the columns on the left; enzymes that fail to resolve are simply absent.',
  pathways:
    'A ZIP folder tree: one folder per route, holding a table for each step, a combined table with a Step column, and a Markdown diagram of the route. Steps with no chosen enzyme fall back to every enzyme the database has for that step.',
}

/** Format behaviour the column picker cannot express. */
const FORMAT_NOTE: Record<string, string> = {
  fasta:
    'FASTA is one record per enzyme: a header line built from the columns you pick (joined with |), then the sequence. Enzymes with no sequence are skipped.',
  zip: 'The chosen columns apply to every enzyme table inside the ZIP.',
}

/** FASTA's header is a line, not a table, so its picker offers only the four
 *  fields that read well in one — in the order the format is documented in.
 *  `sequence` is not a column: the backend appends it to every record. */
const FASTA_HEADER_FIELDS = ['uniprotId', 'organismName', 'primaryName', 'geneName']

/** Group key: startId + endId of the route. Routes sharing the same pair are
 *  drawn as one big card so the user sees "all the ways from A to B". */
const pairKey = (item: Entity) =>
  item.pathway ? `${item.pathway.startId}::${item.pathway.endId}` : item.id

/** An enzyme row only needs its id — the backend resolves everything else. */
const toEnzymeItem = (entity: Entity): DownloadItem => ({
  entityType: 'enzyme',
  entityId: entity.id,
  displayLabel: entity.name,
})

/** A route has no server-side id to resolve against, so its compound chain and
 *  any per-step enzyme choices travel in the request. `displayLabel` becomes
 *  both the ZIP folder name and the diagram title. */
const toPathwayItem = (entity: Entity): DownloadItem | null => {
  const meta = entity.pathway
  if (!meta) return null
  return {
    entityType: 'pathway',
    entityId: entity.id,
    displayLabel: entity.name,
    compoundIds: meta.compoundIds,
    compoundNames: meta.compoundNames,
    steps: meta.enzymesByStep?.map((step) => ({
      step: step.step,
      sourceId: step.sourceId,
      sourceName: step.sourceName,
      targetId: step.targetId,
      targetName: step.targetName,
      enzymes: step.enzymes.map((enzyme) => ({
        enzymeId: enzyme.enzymeId,
        name: enzyme.name,
        organismName: enzyme.organismName ?? null,
        uniprotId: enzyme.uniprotId ?? null,
      })),
    })),
  }
}

/** Read back what the writer actually produced, in the file's own terms. */
function describeStats(stats: Record<string, number>, tab: Tab): string {
  const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`

  if (tab === 'pathways') {
    const parts = [
      plural(stats.pathways ?? 0, 'route'),
      plural(stats.steps ?? 0, 'step'),
      `${plural(stats.enzymes ?? 0, 'enzyme row')} across the step tables`,
    ]
    if (stats.enzyme_rows) parts.push(`${plural(stats.enzyme_rows, 'row')} in the enzyme-page table`)
    return parts.join(' · ')
  }

  const parts = [plural(stats.enzyme_rows ?? 0, 'row')]
  if (stats.sequences !== undefined) parts.push(`${plural(stats.sequences, 'sequence')} written`)
  return parts.join(' · ')
}

export function DownloadsPage({
  downloadedItems,
  removeFromQueue,
  clearQueue,
  onOpenEntity,
  openRecord,
  queueCount,
  onResetHome,
  onOpenSearch,
  onOpenBlast,
  onOpenMap,
  onOpenPathwaySearch,
}: {
  downloadedItems: Entity[]
  removeFromQueue: (id: string) => void
  clearQueue: () => void
  /** The entity itself, not its id: see the call site for why. */
  onOpenEntity: (entity: Entity) => void
  openRecord: (entity: Entity) => void
  /** For the home-style top bar: the queue badge, the brand button and the
   *  search box / nav all leave this page. */
  queueCount: number
  onResetHome: () => void
  onOpenSearch: (query: string) => void
  onOpenBlast: () => void
  /** The Map half of the bar's Map|Table toggle: hand the query to the home map. */
  onOpenMap: (query: string) => void
  /** The Pathway half of the mode toggle: the map opens its chain composer. */
  onOpenPathwaySearch: () => void
}) {
  const [activeTab, setActiveTab] = useState<Tab>('enzymes')
  const [catalog, setCatalog] = useState<DownloadFieldCatalog | null>(null)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [formats, setFormats] = useState<Record<Tab, string>>({ enzymes: 'csv', pathways: 'zip' })
  const [fields, setFields] = useState<string[]>([])
  /** FASTA's pick is kept apart from the general one: the two offer different
   *  fields, so switching format back and forth must not rewrite the other's. */
  const [fastaFields, setFastaFields] = useState<string[]>(FASTA_HEADER_FIELDS)
  const [searchDraft, setSearchDraft] = useState('')
  const [structureOpen, setStructureOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{
    kind: 'done' | 'error'
    text: string
    /** Where the built file can be fetched again, so the report of a download
     *  that the browser swallowed is still actionable. */
    fileUrl?: string
    fileName?: string
  } | null>(null)
  const [preview, setPreview] = useState<DownloadPreview | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewStale, setPreviewStale] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchDownloadFields()
      .then((next) => {
        if (cancelled) return
        setCatalog(next)
        setFields(next.defaultFields)
        // The four FASTA header fields, minus any the catalog does not offer —
        // the picker must never be able to arm a field the backend will ignore.
        const offered = new Set(next.groups.flatMap((group) => group.fields.map((f) => f.key)))
        setFastaFields(FASTA_HEADER_FIELDS.filter((key) => offered.has(key)))
        setFormats((current) => ({
          enzymes: next.formats.enzyme?.some((f) => f.key === current.enzymes)
            ? current.enzymes
            : next.formats.enzyme?.[0]?.key ?? '',
          pathways: next.formats.pathway?.some((f) => f.key === current.pathways)
            ? current.pathways
            : next.formats.pathway?.[0]?.key ?? '',
        }))

      })
      .catch((error: Error) => !cancelled && setCatalogError(error.message))
    return () => {
      cancelled = true
    }
  }, [])

  const enzymeItems = downloadedItems.filter((item) => item.kind === 'enzyme')
  const pathwayEntities = downloadedItems.filter((item) => item.kind === 'pathway')
  const pathwayItems = pathwayEntities.filter((item) => item.pathway)
  const unexportable = downloadedItems.length - enzymeItems.length - pathwayItems.length

  // Records produced from pathway search cards (kind==='pathway' with the
  // PathwayQueueMeta payload) are grouped by their start/end pair.
  const groups: Array<{ key: string; name: string; routes: Entity[] }> = []
  for (const item of pathwayItems) {
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

  const tabFormats = catalog?.formats[PAGE[activeTab]] ?? []
  const format = formats[activeTab]
  const items = activeTab === 'enzymes' ? enzymeItems : pathwayItems
  const formatNote = FORMAT_NOTE[format]
  // FASTA is only ever offered for enzymes, so the restricted picker cannot be
  // armed while the pathway archive is being set up.
  const isFasta = format === 'fasta'
  const activeFields = isFasta ? fastaFields : fields

  const labelByKey = useMemo(() => {
    const map = new Map<string, string>()
    for (const group of catalog?.groups ?? []) {
      for (const field of group.fields) map.set(field.key, field.label)
    }
    return map
  }, [catalog])

  /** What the picker lists. FASTA writes a header line rather than a table, so
   *  it offers the four header fields instead of the library-wide column list. */
  const pickerGroups = useMemo(() => {
    if (!catalog) return []
    if (!isFasta) return catalog.groups
    return [
      {
        key: 'fasta-header',
        label: 'FASTA header',
        fields: FASTA_HEADER_FIELDS.filter((key) => labelByKey.has(key)).map((key) => ({
          key,
          label: labelByKey.get(key) as string,
        })),
      },
    ]
  }, [catalog, isFasta, labelByKey])

  const totalFields = isFasta
    ? FASTA_HEADER_FIELDS.filter((key) => labelByKey.has(key)).length
    : (catalog?.groups ?? []).reduce((sum, group) => sum + group.fields.length, 0)
  const chosenFields = useMemo(() => new Set(activeFields), [activeFields])

  /** The request for the current setup. `/download/preview` and
   *  `/download/files` take the same body, so the page previews exactly what it
   *  would write. */
  const buildPayload = (): DownloadRequest | null => {
    if (!format) return null
    const pathwayPayload = pathwayItems.map(toPathwayItem).filter(Boolean) as DownloadItem[]
    return {
      downloadType: PAGE[activeTab],
      format,
      fields: activeFields,
      items: activeTab === 'enzymes' ? enzymeItems.map(toEnzymeItem) : pathwayPayload,
      // A pathway download is the whole folder tree, so the enzyme page's own
      // table rides along in it whenever the queue is holding enzymes.
      ...(activeTab === 'pathways' ? { enzymeItems: enzymeItems.map(toEnzymeItem) } : {}),
    }
  }

  /** Everything the export depends on, as one string. The queue arrays are rebuilt
   *  on every render, so the effect below cannot depend on them directly — this is
   *  the content signature that says whether the request actually changed. */
  const requestKey = useMemo(
    () =>
      JSON.stringify([
        activeTab,
        format,
        activeFields,
        downloadedItems.map((item) => [
          item.id,
          item.kind,
          item.pathway?.startId ?? null,
          item.pathway?.endId ?? null,
          (item.pathway?.enzymesByStep ?? []).map((step) => [
            step.step,
            step.enzymes.map((enzyme) => enzyme.enzymeId),
          ]),
        ]),
      ]),
    [activeTab, format, activeFields, downloadedItems],
  )

  useEffect(() => {
    const payload = buildPayload()
    if (!payload || payload.fields.length === 0 || payload.items.length === 0) {
      // The download button is disabled for these same reasons; there is nothing
      // to describe.
      setPreview(null)
      setPreviewError(null)
      setPreviewStale(false)
      return
    }
    let cancelled = false
    // The column checkboxes fire in bursts, so hold off until the state settles.
    // The previous preview stays on screen (marked stale) rather than blanking.
    setPreviewStale(true)
    const timer = window.setTimeout(() => {
      previewDownload(payload)
        .then((next) => {
          if (cancelled) return
          setPreview(next)
          setPreviewError(null)
          setPreviewStale(false)
        })
        .catch((error: Error) => {
          if (cancelled) return
          setPreview(null)
          setPreviewError(error.message)
          setPreviewStale(false)
        })
    }, 350)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
    // `requestKey` is the content signature of every input read above, which is
    // what keeps this from re-running on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey])

  /** FASTA adds the sequence to every record whatever was picked, so the file's
   *  own list is not necessarily the pick. */
  const headerDiffers =
    preview !== null &&
    (preview.columns.length !== activeFields.length ||
      activeFields.some((key, index) => preview.columns[index] !== labelByKey.get(key)))

  const setPick = (next: string[] | ((current: string[]) => string[])) => {
    setResult(null)
    if (isFasta) setFastaFields(next)
    else setFields(next)
  }

  const toggleField = (key: string) => {
    setPick((current) => (current.includes(key) ? current.filter((f) => f !== key) : [...current, key]))
  }

  const download = async () => {
    const request = buildPayload()
    if (!request || !request.items.length) return
    setBusy(true)
    setResult(null)
    try {
      const payload: DownloadResult = await createDownload(request)
      const fileName = fileNameFromUrl(payload.fileUrl)
      saveFile(payload.fileUrl, fileName)
      const unknown = payload.unknownFields.length
        ? ` · ${payload.unknownFields.length} unrecognised column(s) ignored`
        : ''
      setResult({
        kind: 'done',
        text: `${describeStats(payload.stats, activeTab)}${unknown}`,
        fileUrl: payload.fileUrl,
        fileName,
      })
    } catch (error) {
      setResult({ kind: 'error', text: error instanceof Error ? error.message : 'Download failed' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="home-map-page downloads-atlas-page">
      {/* Same anatomy as the home map / search table / enzyme detail bars. The
          queue pill is this page, so it is a label rather than a button —
          as a button it navigated to the view it was already on, which reads
          as a download control that does nothing. */}
      <header className="graph-top-nav downloads-topnav">
        <button
          type="button"
          className="atlas-brand"
          onClick={onResetHome}
          title="Back to the Atlas home map"
          aria-label="Starase Atlas home"
        >
          <span className="atlas-logo">
            <Network size={18} />
          </span>
          <span>Starase Atlas</span>
        </button>

        <div className="downloads-topnav-slot">
          <span className="download-list-button is-current" aria-current="page" title="This is the download list">
            <Download size={15} />
            <span>Downloading table</span>
            {queueCount > 0 && <span className="download-list-badge">{queueCount}</span>}
          </span>

          <div className="home-search-bar downloads-search-bar">
            <div className="home-mode-toggle" role="group" aria-label="Search mode">
              <button type="button" className="is-active" aria-current="page" title="Search compounds and enzymes by keyword / BLAST">Enzyme</button>
              <button type="button" onClick={onOpenPathwaySearch} title="Find compound chains from a start through optional waypoints to an end">Pathway</button>
            </div>
            <input
              value={searchDraft}
              onChange={(event) => setSearchDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') onOpenSearch(searchDraft)
              }}
              placeholder="Search enzymes by name, UniProt, EC or gene…"
              aria-label="Search the enzyme library"
            />
            <div className="home-result-toggle" role="group" aria-label="Search result view">
              <button type="button" onClick={() => onOpenMap(searchDraft)} title="Show these results on the map">Map</button>
              <button type="button" className="is-active" aria-current="page">Table</button>
            </div>
            <button className="home-search-submit" type="button" onClick={() => onOpenSearch(searchDraft)} title="Search">
              <Search size={18} />
            </button>
          </div>
        </div>

        <nav className="graph-primary-nav" aria-label="Download page navigation">
          <button type="button" onClick={() => onOpenSearch('')}>
            Data Browser
          </button>
          <button type="button" onClick={onOpenBlast}>
            BLAST
          </button>
          <button type="button" onClick={() => setStructureOpen(true)}>
            Structure search
          </button>
          <span className="graph-user-chip">NJU - China 2026</span>
        </nav>
      </header>

      {/* No page heading: the bar above already names the page, and every pixel
          it would take comes off the bottom of the export panel — whose docked
          download button has to be on screen the moment the page opens. */}
      <div className="content-wrap downloads-page">
      <section className="downloads-shell">
        <aside className="downloads-sidebar section-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">
                <span className="live-line" />
                {activeTab === 'enzymes' ? 'Enzyme export' : 'Pathway export'}
              </div>
              <h2>Export setup</h2>
            </div>
            <button
              className="downloads-clear-button"
              onClick={clearQueue}
              disabled={downloadedItems.length === 0}
              title="Empty the whole queue, both kinds"
            >
              <X size={13} />
              Clear queue
            </button>
          </div>

          <p className="downloads-export-note">{TAB_EXPORT_NOTE[activeTab]}</p>

          <div className="downloads-option-group">
            <div className="downloads-option-label">Format</div>
            <div className="downloads-format-list">
              {tabFormats.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  className={`downloads-format-item ${format === option.key ? 'is-selected' : ''}`}
                  aria-pressed={format === option.key}
                  onClick={() => {
                    setResult(null)
                    setFormats((current) => ({ ...current, [activeTab]: option.key }))
                  }}
                >
                  <span className="downloads-format-name">
                    {option.key.toUpperCase()}
                    {format === option.key && <Check size={14} />}
                  </span>
                  <small>{option.label}</small>
                </button>
              ))}
            </div>
            {formatNote && <p className="downloads-format-note">{formatNote}</p>}
          </div>

          <div className="downloads-option-group">
            <div className="downloads-option-label">
              Columns
              <span className="downloads-column-count">
                {activeFields.length}/{totalFields || '—'}
              </span>
            </div>
            <div className="downloads-column-quick">
              <button
                type="button"
                onClick={() => setPick(isFasta ? [...FASTA_HEADER_FIELDS] : catalog?.defaultFields ?? [])}
              >
                Defaults
              </button>
              <button
                type="button"
                onClick={() =>
                  setPick(
                    isFasta
                      ? [...FASTA_HEADER_FIELDS]
                      : (catalog?.groups ?? []).flatMap((group) => group.fields.map((f) => f.key)),
                  )
                }
              >
                All
              </button>
              <button type="button" onClick={() => setPick([])}>
                None
              </button>
            </div>
            {catalogError ? (
              <p className="downloads-format-note is-error">Could not load the column list: {catalogError}</p>
            ) : !catalog ? (
              <p className="downloads-format-note">Loading columns…</p>
            ) : (
              <div className="downloads-columns-list">
                {pickerGroups.map((group) => {
                  const on = group.fields.filter((field) => chosenFields.has(field.key)).length
                  return (
                    <details key={group.key} className="downloads-field-group" open>
                      <summary>
                        {group.label}
                        <span className={on ? 'is-on' : ''}>
                          {on}/{group.fields.length}
                        </span>
                      </summary>
                      <div className="downloads-field-items">
                        {group.fields.map((field) => (
                          <label
                            key={field.key}
                            className={`downloads-field-item ${chosenFields.has(field.key) ? 'is-on' : ''}`}
                          >
                            <input
                              type="checkbox"
                              checked={chosenFields.has(field.key)}
                              onChange={() => toggleField(field.key)}
                            />
                            <span>{field.label}</span>
                          </label>
                        ))}
                      </div>
                    </details>
                  )
                })}
              </div>
            )}
          </div>

          <div className="downloads-option-group">
            <div className="downloads-option-label">
              Will produce
              {previewStale && <Loader2 size={11} className="spin" />}
            </div>
            {previewError ? (
              <p className="downloads-format-note is-error">
                <AlertTriangle size={14} />
                {previewError}
              </p>
            ) : !preview ? (
              <p className="downloads-format-note">
                {items.length === 0
                  ? 'Nothing queued yet, so there is nothing to describe.'
                  : 'Pick at least one column to see what the file would hold.'}
              </p>
            ) : (
              <>
                {/* A pathway archive holds several tables, so its count is the
                    enzyme rows across all of them — "rows" would read as one
                    table's length. */}
                <p className="downloads-preview-summary">
                  <strong>{preview.rowCount}</strong>
                  {activeTab === 'pathways' ? ' enzyme rows in the archive' : ` row${preview.rowCount === 1 ? '' : 's'}`}
                  <span>·</span>
                  <strong>{preview.columns.length}</strong> column
                  {preview.columns.length === 1 ? '' : 's'}
                </p>
                <p className="downloads-preview-file">{preview.estimatedFileName}</p>
                {/* The picker already shows the selection, so the column list is
                    only worth the room when the file's header disagrees with it
                    — which is what a format like FASTA does. */}
                {headerDiffers && (
                  <details className="downloads-preview-columns" open>
                    <summary>Adds columns of its own</summary>
                    <div className="downloads-preview-chip-list">
                      {preview.columns.map((column) => (
                        <span key={column} className="downloads-preview-chip">
                          {column}
                        </span>
                      ))}
                    </div>
                  </details>
                )}
                {preview.unknownFields.length > 0 && (
                  <p className="downloads-format-note is-error">
                    <AlertTriangle size={14} />
                    {preview.unknownFields.length} unrecognised column
                    {preview.unknownFields.length === 1 ? '' : 's'} will be ignored.
                  </p>
                )}
              </>
            )}
          </div>

          {/* Both the action and the report of its outcome stay docked to the
              panel's bottom edge. The column list and the preview's own column
              list scroll inside the panel, so without this the button can sit
              below a scroll — two scrolls away from the page that exists to
              press it. */}
          <div className="downloads-submit-dock">
            {result && (
              <p className={`downloads-export-status is-${result.kind}`}>
                {result.kind === 'error' ? <AlertTriangle size={14} /> : <Check size={14} />}
                <span>
                  {result.text}
                  {/* The file, by name and by link. A browser can take a download
                      and give nothing back — a blocked download, a save dialog
                      opened behind the window, a folder that is not the one the
                      user is looking in — and the page cannot tell that apart
                      from a download that worked. Naming the file says what to
                      look for; the link is the retry that does not rebuild it. */}
                  {result.fileName && (
                    <a className="downloads-export-file" href={result.fileUrl} download={result.fileName}>
                      {result.fileName}
                    </a>
                  )}
                </span>
              </p>
            )}

            <button
              className="download-submit-pill"
              onClick={download}
              disabled={busy || items.length === 0 || activeFields.length === 0 || !format}
            >
              {busy ? <Loader2 size={16} className="spin" /> : <Download size={16} />}
              {busy
                ? 'Building…'
                : items.length === 0
                  ? `Nothing queued in ${activeTab}`
                  : `Download ${activeTab} (${items.length})`}
            </button>
          </div>
        </aside>

        <div className="downloads-main section-panel">
          <div className="downloads-tabbar">
            <button type="button" className={activeTab === 'enzymes' ? 'active' : ''} onClick={() => { setResult(null); setActiveTab('enzymes') }}>
              Enzymes <span>({enzymeItems.length} chosen)</span>
            </button>
            <button type="button" className={activeTab === 'pathways' ? 'active' : ''} onClick={() => { setResult(null); setActiveTab('pathways') }}>
              Pathways <span>({pathwayItems.length} chosen)</span>
            </button>
          </div>

          {unexportable > 0 && (
            <p className="downloads-format-note is-error">
              <AlertTriangle size={14} />
              {unexportable} queued record{unexportable === 1 ? '' : 's'} cannot be exported — only enzymes and
              pathways are downloadable. Remove {unexportable === 1 ? 'it' : 'them'} with the × button.
            </p>
          )}

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
                  const recordUrl = getExternalRecordUrl(entity)
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
                      {/* The record link is disabled rather than silently doing
                          nothing: a queued enzyme whose UniProt accession is
                          unknown has no record to open. */}
                      <button
                        className="icon-button"
                        title={recordUrl ? `Open ${entity.name} on UniProt` : 'No UniProt record for this enzyme'}
                        disabled={!recordUrl}
                        onClick={() => openRecord(entity)}
                      >
                        <ExternalLink size={15} />
                      </button>
                      <button
                        className="icon-button"
                        title="Open enzyme page"
                        onClick={() => onOpenEntity(entity)}
                      >
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
              groups.map((group) => (
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
                      // omit them and export using the database's own enzymes for
                      // each step. The enzyme listing is the LAST child on purpose: it
                      // is pinned to a second grid row by CSS (explicit row/col), so
                      // appending it after the remove button never disturbs the header
                      // row's auto-placement.
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
                            {hasEnzymes ? (
                              <span className="downloads-pw-route-badge" title="已为每步选定具体酶">
                                已选酶
                              </span>
                            ) : (
                              <span className="downloads-pw-route-badge is-auto" title="未逐步选酶，导出时自动使用数据库里能催化该步的全部酶">
                                自动选酶
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
              ))
            ) : (
              <div className="empty-home">
                <p>No pathway records yet.</p>
              </div>
            )}
          </div>
        </div>
        </section>
      </div>

      <StructureSearchDrawer open={structureOpen} onClose={() => setStructureOpen(false)} />
    </div>
  )
}
