import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import {
  ArrowUpRight,
  Beaker,
  ExternalLink,
  FlaskConical,
  Loader2,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { searchStructureByInchikey, type StructureSearchResult } from '../api'

type KetcherApi = {
  getSmiles: () => Promise<string> | string
  getInChIKey: () => Promise<string> | string
  setMolecule: (smiles?: string) => Promise<void> | void
}

const SAMPLE_SMILES = 'CC1=CCC2CC1C2(C)C'

export function StructureSearchPage({
  onOpenCompound,
}: {
  onOpenCompound: (id: string) => void
}) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [editorReady, setEditorReady] = useState(false)
  const [status, setStatus] = useState('Loading Ketcher editor...')
  const [currentSmiles, setCurrentSmiles] = useState('')
  const [currentInchikey, setCurrentInchikey] = useState('')
  const [result, setResult] = useState<StructureSearchResult | null>(null)
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

  const ketcher = useMemo(() => ({
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
  }), [])

  const analyzeCurrentStructure = async () => {
    setLoading(true)
    setError(null)
    setStatus('Resolving molecule...')

    try {
      const smiles = String(await ketcher.getSmiles()).trim()
      if (!smiles) {
        setCurrentSmiles('')
        setCurrentInchikey('')
        setResult(null)
        setStatus('Draw a molecule first')
        setError('Please draw a molecule in the editor.')
        return
      }

      const inchikey = String(await ketcher.getInChIKey()).trim()
      if (!inchikey) {
        setCurrentSmiles(smiles)
        setCurrentInchikey('')
        setResult(null)
        setStatus('No InChIKey returned')
        setError('The editor did not return an InChIKey for this molecule.')
        return
      }

      const payload = await searchStructureByInchikey(inchikey)
      setCurrentSmiles(smiles)
      setCurrentInchikey(inchikey)
      setResult(payload)
      setStatus(`Matched ${payload.compounds.length} compounds`)
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
    setResult(null)
    setError(null)
    setStatus('Canvas cleared')
  }

  const loadExample = async () => {
    await ketcher.setMolecule(SAMPLE_SMILES)
    setCurrentSmiles(SAMPLE_SMILES)
    setCurrentInchikey('')
    setResult(null)
    setError(null)
    setStatus('Example loaded')
  }

  return (
    <div className="content-wrap structure-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow">
            <FlaskConical size={14} />
            Structure search
          </div>
          <h1>Ketcher search</h1>
          <p>Draw a molecule, resolve its InChIKey in the editor, and jump straight to matching compounds and reactions.</p>
        </div>
        <div className="heading-actions structure-actions">
          <span className="result-total">
            {result ? `${result.compounds.length} compounds · ${result.reactions.length} reactions` : 'No search yet'}
          </span>
          <button className="outline-button" onClick={loadExample}>
            <Sparkles size={15} />
            Load example
          </button>
          <button className="outline-button" onClick={clearCanvas}>
            <Trash2 size={15} />
            Clear canvas
          </button>
          <button className="primary-button" onClick={analyzeCurrentStructure} disabled={loading || !editorReady}>
            {loading ? <Loader2 size={15} className="spin-icon" /> : <Search size={15} />}
            Search database
          </button>
        </div>
      </section>

      <section className="structure-shell">
        <div className="structure-editor section-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">
                <span className="live-line" />
                Local editor
              </div>
              <h2>Ketcher canvas</h2>
            </div>
            <div className="structure-status">{status}</div>
          </div>
          <div className="structure-editor-frame">
            <iframe
              ref={iframeRef}
              title="Ketcher structure editor"
              src="/ketcher_standalone/index.html"
            />
          </div>
        </div>

        <aside className="structure-results section-panel">
          <div className="panel-header">
            <div>
              <div className="panel-kicker">
                <span className="live-line" />
                Search output
              </div>
              <h2>Resolved structure</h2>
            </div>
            <button className="outline-button compact-button" onClick={analyzeCurrentStructure} disabled={loading || !editorReady}>
              <RefreshCw size={15} />
              Refresh
            </button>
          </div>

          <div className="structure-stack">
            <div className="structure-field">
              <span>SMILES</span>
              <strong>{currentSmiles || '—'}</strong>
            </div>
            <div className="structure-field">
              <span>InChIKey</span>
              <strong>{currentInchikey || '—'}</strong>
            </div>

            {error && <div className="structure-alert">{error}</div>}

            <div className="structure-section">
              <div className="structure-section-head">
                <h3>Matched compounds</h3>
                <span>{result?.compounds.length || 0}</span>
              </div>
              {result?.compounds.length ? (
                <div className="structure-hit-list">
                  {result.compounds.map((compound) => (
                    <article key={compound.compoundId} className="structure-hit-row">
                      <div className="structure-hit-icon compound">
                        <Beaker size={15} />
                      </div>
                      <div className="structure-hit-main">
                        <strong>{compound.name}</strong>
                        <span>{compound.compoundId}</span>
                        <small>
                          {compound.chebiId || 'ChEBI n/a'}
                          {compound.smiles ? ` · ${compound.smiles}` : ''}
                        </small>
                      </div>
                      <div className="structure-hit-actions">
                        <button type="button" className="icon-button" title="Open in atlas" onClick={() => onOpenCompound(compound.compoundId)}>
                          <ArrowUpRight size={15} />
                        </button>
                        {compound.chebiUrl ? (
                          <a className="icon-button" title="Open ChEBI" href={compound.chebiUrl} target="_blank" rel="noreferrer">
                            <ExternalLink size={15} />
                          </a>
                        ) : null}
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="structure-empty">No compound hit yet.</div>
              )}
            </div>

            <div className="structure-section">
              <div className="structure-section-head">
                <h3>Related reactions</h3>
                <span>{result?.reactions.length || 0}</span>
              </div>
              {result?.reactions.length ? (
                <div className="structure-hit-list">
                  {result.reactions.map((reaction, index) => (
                    <article key={`${reaction.reactionId}:${reaction.compoundId}:${reaction.role}:${index}`} className="structure-hit-row reaction">
                      <div className="structure-hit-icon reaction">
                        <FlaskConical size={15} />
                      </div>
                      <div className="structure-hit-main">
                        <strong>{reaction.equation}</strong>
                        <span>
                          {reaction.reactionId}
                          {reaction.rheaId ? ` · ${reaction.rheaId}` : ''}
                        </span>
                        <small>
                          {reaction.role} · {reaction.compoundName}
                        </small>
                      </div>
                      <div className="structure-hit-actions">
                        {reaction.rheaUrl ? (
                          <a className="icon-button" title="Open Rhea" href={reaction.rheaUrl} target="_blank" rel="noreferrer">
                            <ExternalLink size={15} />
                          </a>
                        ) : null}
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="structure-empty">No reaction hit yet.</div>
              )}
            </div>
          </div>
        </aside>
      </section>
    </div>
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
