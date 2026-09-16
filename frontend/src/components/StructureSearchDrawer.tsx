import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { Beaker, CornerDownLeft, ExternalLink, FlaskConical, Loader2, Search, Sparkles, Trash2, X } from 'lucide-react'
import { searchStructureByInchikey, type StructureSearchCompoundHit } from '../api'

type KetcherApi = {
  getSmiles: () => Promise<string> | string
  getInChIKey: () => Promise<string> | string
  setMolecule: (smiles?: string) => Promise<void> | void
}

const SAMPLE_SMILES = 'CC1=CCC2CC1C2(C)C'

/** The ChEBI accession to hand to the page's search box, when the hit has one. */
function chebiTarget(compound: StructureSearchCompoundHit): string | null {
  if (compound.chebiId?.startsWith('CHEBI:')) return compound.chebiId
  if (compound.compoundId?.startsWith('CHEBI:')) return compound.compoundId
  return null
}

export function StructureSearchDrawer({
  open,
  onClose,
  onTransferChebi,
}: {
  open: boolean
  onClose: () => void
  /** Quick-transfer: drop a matched compound's ChEBI into the page's search box (no search is run). */
  onTransferChebi?: (chebiId: string) => void
}) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [editorReady, setEditorReady] = useState(false)
  const [status, setStatus] = useState('Loading Ketcher editor...')
  const [currentSmiles, setCurrentSmiles] = useState('')
  const [currentInchikey, setCurrentInchikey] = useState('')
  const [compounds, setCompounds] = useState<StructureSearchCompoundHit[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return
      if (event.data?.eventType !== 'init') return
      setEditorReady(true)
      setStatus('Ketcher ready')
    }

    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [])

  const ketcher = useMemo(
    () => ({
      async getSmiles() {
        const api = await waitForKetcher(iframeRef)
        return api ? api.getSmiles() : ''
      },
      async getInChIKey() {
        const api = await waitForKetcher(iframeRef)
        return api ? api.getInChIKey() : ''
      },
      async setMolecule(smiles?: string) {
        const api = await waitForKetcher(iframeRef)
        if (api) await api.setMolecule(smiles || '')
      },
    }),
    [],
  )

  const analyzeCurrentStructure = async () => {
    setLoading(true)
    setError(null)
    setStatus('Resolving molecule...')

    try {
      const smiles = String(await ketcher.getSmiles()).trim()
      if (!smiles) {
        setCurrentSmiles('')
        setCurrentInchikey('')
        setCompounds(null)
        setStatus('Draw a molecule first')
        setError('Please draw a molecule in the editor.')
        return
      }

      const inchikey = String(await ketcher.getInChIKey()).trim()
      if (!inchikey) {
        setCurrentSmiles(smiles)
        setCurrentInchikey('')
        setCompounds(null)
        setStatus('No InChIKey returned')
        setError('The editor did not return an InChIKey for this molecule.')
        return
      }

      const payload = await searchStructureByInchikey(inchikey)
      setCurrentSmiles(smiles)
      setCurrentInchikey(inchikey)
      setCompounds(payload.compounds)
      setStatus(payload.compounds.length > 0 ? `Compound dictionary: ${payload.compounds.length} hit(s)` : 'No compound matched this structure')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      setError(message)
      setStatus('Search failed')
    } finally {
      setLoading(false)
    }
  }

  const clearCanvas = async () => {
    await ketcher.setMolecule('')
    setCurrentSmiles('')
    setCurrentInchikey('')
    setCompounds(null)
    setError(null)
    setStatus('Canvas cleared')
  }

  const loadExample = async () => {
    await ketcher.setMolecule(SAMPLE_SMILES)
    setCurrentSmiles(SAMPLE_SMILES)
    setCurrentInchikey('')
    setCompounds(null)
    setError(null)
    setStatus('Example loaded — press Search')
  }

  const structureImageUrl = (compound: StructureSearchCompoundHit) => {
    const chebiId = compound.chebiId || compound.compoundId
    if (chebiId?.startsWith('CHEBI:')) return `/api/v1/assets/compounds/${encodeURIComponent(chebiId)}/structure.svg?v=4`
    return compound.structureImageUrl || null
  }

  return createPortal(
    <div className={`structure-drawer-layer ${open ? 'is-open' : ''}`} aria-hidden={!open}>
      <div className="structure-drawer-backdrop" onClick={onClose} />
      <aside className="structure-drawer" role="dialog" aria-modal="true" aria-label="Structure search — compound dictionary">
        <div className="structure-drawer-head">
          <div className="structure-drawer-title">
            <span className="structure-drawer-icon">
              <FlaskConical size={16} />
            </span>
            <div>
              <div className="structure-drawer-kicker">Structure search · compound dictionary</div>
              <h2>Draw a molecule, find the compound</h2>
            </div>
          </div>
          <button className="structure-drawer-close" type="button" onClick={onClose} title="Close">
            <X size={18} />
          </button>
        </div>

        <div className="structure-drawer-body">
          <div className={`structure-drawer-status ${error ? 'is-error' : ''}`}>{status}</div>

          <div className="structure-editor-block">
            <iframe ref={iframeRef} title="Ketcher structure editor" src="/ketcher_standalone/index.html" />
          </div>

          <div className="structure-drawer-actions">
            <button type="button" className="structure-drawer-btn ghost" onClick={() => void loadExample()}>
              <Sparkles size={15} />
              Load example
            </button>
            <button type="button" className="structure-drawer-btn ghost" onClick={() => void clearCanvas()}>
              <Trash2 size={15} />
              Clear
            </button>
            <button
              type="button"
              className="structure-drawer-btn primary"
              onClick={() => void analyzeCurrentStructure()}
              disabled={loading || !editorReady}
            >
              {loading ? <Loader2 size={15} className="spin" /> : <Search size={15} />}
              Search database
            </button>
          </div>

          <div className="structure-result-fields">
            <div className="structure-result-field">
              <span>SMILES</span>
              <strong>{currentSmiles || '—'}</strong>
            </div>
            <div className="structure-result-field">
              <span>InChIKey</span>
              <strong>{currentInchikey || '—'}</strong>
            </div>
          </div>

          {error && <div className="structure-drawer-alert">{error}</div>}

          <div className="structure-drawer-section">
            <div className="structure-drawer-section-head">
              <h3>Matched compounds</h3>
              <span>{compounds?.length ?? 0}</span>
            </div>
            {compounds && compounds.length > 0 ? (
              <div className="structure-drawer-hit-list">
                {compounds.map((compound) => (
                  <article key={compound.compoundId} className="structure-drawer-hit">
                    <div className="structure-drawer-hit-img">
                      {structureImageUrl(compound) ? (
                        <img src={structureImageUrl(compound) || undefined} alt={`${compound.name} structure`} />
                      ) : (
                        <Beaker size={16} />
                      )}
                    </div>
                    <div className="structure-drawer-hit-main">
                      <strong>{compound.name}</strong>
                      <span>{compound.compoundId}</span>
                      <small>
                        {compound.chebiId || 'ChEBI n/a'}
                        {compound.smiles ? ` · ${compound.smiles}` : ''}
                      </small>
                    </div>
                    <div className="structure-drawer-hit-actions">
                      {onTransferChebi && chebiTarget(compound) && (
                        <button
                          type="button"
                          className="structure-drawer-hit-transfer"
                          onClick={() => onTransferChebi(chebiTarget(compound) as string)}
                          title={`Put ${chebiTarget(compound)} in the search box`}
                          aria-label={`Put ${chebiTarget(compound)} in the search box`}
                        >
                          <CornerDownLeft size={15} />
                        </button>
                      )}
                      {compound.chebiUrl ? (
                        <a className="structure-drawer-hit-link" href={compound.chebiUrl} target="_blank" rel="noreferrer" title="Open in ChEBI">
                          <ExternalLink size={15} />
                        </a>
                      ) : null}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="structure-drawer-empty">
                {compounds === null ? 'Search results will appear here.' : 'No compound in the dictionary matches this structure yet.'}
              </div>
            )}
          </div>
        </div>
      </aside>
    </div>,
    document.body,
  )
}

async function waitForKetcher(iframeRef: RefObject<HTMLIFrameElement | null>) {
  for (let i = 0; i < 150; i += 1) {
    const contentWindow = iframeRef.current?.contentWindow as (Window & { ketcher?: KetcherApi }) | null
    const api = contentWindow?.ketcher
    if (api) return api
    await new Promise((resolve) => window.setTimeout(resolve, 100))
  }
  const contentWindow = iframeRef.current?.contentWindow as (Window & { ketcher?: KetcherApi }) | null
  return contentWindow?.ketcher
}
