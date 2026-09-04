import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDownToLine, ArrowUpRight, Check, ChevronDown, Download, ExternalLink, Loader2, X } from 'lucide-react'
import type { Entity } from '../types'
import { kindIcons, kindLabels } from '../lib/entities'
import type { DownloadFormat, DownloadPreviewData, DownloadQueueItem } from '../api'
import { createDownloadFile, previewDownloadFile } from '../api'

const DOWNLOAD_TYPE = 'download_queue'
const DOWNLOAD_FIELDS = [
  'primaryName',
  'databaseCode',
  'sequence',
  'uniprotId',
  'organismName',
  'ecNumber',
  'reactionEquation',
  'direction',
  'smiles',
  'geneName',
  'genbankId',
  'doi',
  'pubmedId',
  'chebiId',
  'averageMass',
  'inchiKey',
  'sourceType',
  'reviewStatus',
] as const

const FORMAT_LABELS: Record<DownloadFormat, string> = {
  csv: 'CSV',
  fasta: 'FASTA',
  tsv: 'TSV',
  txt: 'TXT',
  xlsx: 'XLSX',
}

const FORMAT_OPTIONS: DownloadFormat[] = ['csv', 'fasta', 'tsv', 'txt', 'xlsx']

export function DownloadsPage({
  downloadedItems,
  removeFromQueue,
  clearQueue,
  onOpenEntity,
  openRecord,
}: {
  downloadedItems: Entity[]
  removeFromQueue: (id: string) => void
  clearQueue: () => void
  onOpenEntity: (id: string) => void
  openRecord: (entity: Entity) => void
}) {
  const menuRef = useRef<HTMLDivElement | null>(null)
  const [activeTab, setActiveTab] = useState<'enzymes' | 'pathways'>('enzymes')
  const [selectedFormat, setSelectedFormat] = useState<DownloadFormat>('csv')
  const [menuOpen, setMenuOpen] = useState(false)
  const [preview, setPreview] = useState<DownloadPreviewData | null>(null)
  const [previewState, setPreviewState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [exportState, setExportState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const enzymeItems = downloadedItems.filter((item) => item.kind === 'enzyme')
  const pathwayItems = downloadedItems.filter((item) => item.kind !== 'enzyme')
  const activeItems = activeTab === 'enzymes' ? enzymeItems : pathwayItems
  const emptyMessage = activeTab === 'enzymes' ? 'No enzyme records yet.' : 'No pathway records yet.'
  const displayPreview = preview ?? {
    columns: [],
    rowCount: downloadedItems.length,
    estimatedFileName: `${DOWNLOAD_TYPE}.${selectedFormat}`,
  }
  const downloadItems = useMemo<DownloadQueueItem[]>(
    () => downloadedItems.map((entity) => ({
      entityType: entity.kind,
      entityId: entity.id,
      displayLabel: entity.name,
    })),
    [downloadedItems],
  )
  const formatLabel = FORMAT_LABELS[selectedFormat]

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (!menuRef.current) return
      if (event.target instanceof Node && !menuRef.current.contains(event.target)) {
        setMenuOpen(false)
      }
    }

    window.addEventListener('mousedown', handleClickOutside)
    return () => window.removeEventListener('mousedown', handleClickOutside)
  }, [])

  useEffect(() => {
    if (downloadedItems.length === 0) {
      setPreview(null)
      setPreviewState('idle')
      setMenuOpen(false)
      return
    }

    let cancelled = false
    setPreviewState('loading')
    const timer = window.setTimeout(() => {
      previewDownloadFile({
        downloadType: DOWNLOAD_TYPE,
        items: downloadItems,
        fields: [...DOWNLOAD_FIELDS],
        format: selectedFormat,
        includeExternalLinks: false,
        includeGraphImage: false,
      })
        .then((payload) => {
          if (cancelled) return
          setPreview(payload)
          setPreviewState('ready')
        })
        .catch(() => {
          if (cancelled) return
          setPreview(null)
          setPreviewState('error')
        })
    }, 180)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [downloadItems, downloadedItems.length, selectedFormat])

  const handleExport = async () => {
    if (downloadedItems.length === 0) return
    setExportState('loading')
    try {
      const payload = await createDownloadFile({
        downloadType: DOWNLOAD_TYPE,
        items: downloadItems,
        fields: [...DOWNLOAD_FIELDS],
        format: selectedFormat,
        includeExternalLinks: false,
        includeGraphImage: false,
      })
      if (payload.fileUrl) {
        window.open(payload.fileUrl, '_blank', 'noopener,noreferrer')
        setExportState('ready')
      } else {
        setExportState('error')
      }
    } catch {
      setExportState('error')
    }
  }

  const renderRow = (entity: Entity) => {
    const Icon = kindIcons[entity.kind]
    return (
      <article key={entity.id} className={`download-row ${entity.kind}`}>
        <span className={`queue-icon ${entity.kind}`}>
          <Icon size={15} />
        </span>
        <span className="queue-id">{entity.id}</span>
        <span className="queue-copy">
          <strong>{entity.name}</strong>
        </span>
        <span className="queue-subtitle">{entity.subtitle}</span>
        <span className="queue-kind">{kindLabels[entity.kind]}</span>
        <span className="queue-actions">
          <button className="icon-button" title="Open record" onClick={() => openRecord(entity)}>
            <ExternalLink size={15} />
          </button>
          <button className="icon-button" title="Open in network" onClick={() => onOpenEntity(entity.id)}>
            <ArrowUpRight size={15} />
          </button>
          <button className="icon-button" title="Remove from queue" onClick={() => removeFromQueue(entity.id)}>
            <X size={15} />
          </button>
        </span>
      </article>
    )
  }

  return (
    <div className="content-wrap downloads-page">
      <section className="page-heading downloads-page-heading">
        <div>
          <div className="eyebrow">
            <Download size={14} />
            Saved output
          </div>
          <h1>Downloading table</h1>
        </div>
        <div className="heading-actions downloads-page-actions">
          <div className="downloads-format-dropdown" ref={menuRef}>
            <button
              type="button"
              className="downloads-format-trigger downloads-toolbar-button"
              onClick={() => setMenuOpen((open) => !open)}
              disabled={downloadedItems.length === 0}
            >
              <span>Format: {formatLabel}</span>
              <ChevronDown size={15} />
            </button>
            {menuOpen && (
              <div className="downloads-format-menu">
                {FORMAT_OPTIONS.map((format) => (
                  <button
                    key={format}
                    type="button"
                    className={`downloads-format-item ${selectedFormat === format ? 'active' : ''}`}
                    onClick={() => {
                      setSelectedFormat(format)
                      setMenuOpen(false)
                    }}
                  >
                    <span>{FORMAT_LABELS[format]}</span>
                    {selectedFormat === format && <Check size={14} />}
                  </button>
                ))}
              </div>
            )}
          </div>
          <button className="download-submit-pill downloads-export-button downloads-toolbar-button" onClick={handleExport} disabled={downloadedItems.length === 0 || exportState === 'loading'}>
            {exportState === 'loading' ? <Loader2 size={15} className="spin" /> : <ArrowDownToLine size={15} />}
            {exportState === 'loading' ? 'Exporting...' : `Export ${formatLabel}`}
          </button>
          <button className="outline-button downloads-clear-button downloads-toolbar-button" onClick={clearQueue} disabled={downloadedItems.length === 0}>
            <X size={15} />
            Clear queue
          </button>
        </div>
      </section>

      <div className="downloads-summary-line">
        <span>Filename: {displayPreview.estimatedFileName}</span>
        <span>{previewState === 'loading' ? 'Updating preview...' : `${displayPreview.rowCount} rows`}</span>
      </div>

      <section className="downloads-shell">
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
              <span />
              <span>ID</span>
              <span>Record</span>
              <span>Subtitle</span>
              <span>Category</span>
              <span>Actions</span>
            </div>
            {activeItems.length > 0 ? (
              activeItems.map(renderRow)
            ) : (
              <div className="empty-home">
                <p>{emptyMessage}</p>
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
