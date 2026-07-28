import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronDown,
  ChevronRight,
  Dna,
  Download,
  ExternalLink,
  Link2,
  Loader2,
  Network,
  Search,
  X,
} from 'lucide-react'
import {
  createEnzymeDownload,
  loadExpandedEdgeGroup,
  loadEnzymeDetail,
  loadHomeGraph,
  type EnzymeDetailData,
  type HomeGraphCompound,
  type HomeGraphData,
  type HomeGraphEdge,
} from './api'

const HOME_NODE_LIMIT = 10
const HOME_MAX_EXPANDED_EDGES = 10
const HOME_VIEWBOX_HEIGHT = 118
const homeSearchModes = [
  { id: 'enzymeItems', label: 'Enzyme items' },
  { id: 'pathways', label: 'Pathways' },
  { id: 'blast', label: 'Blast / homology' },
  { id: 'mapsearch', label: 'Map search' },
] as const
const homeDatasetOptions = [
  { id: 'terpene_synthase', label: 'Terpene synthase', detail: 'Live backend', disabled: false },
  { id: 'comparative_sets', label: 'Comparative sets', detail: 'Coming soon', disabled: true },
  { id: 'literature_merge', label: 'Literature merge', detail: 'Coming soon', disabled: true },
] as const
type HomeSearchMode = (typeof homeSearchModes)[number]['id']

type Point = { x: number; y: number }

type PairEntry = {
  key: string
  sourceId: string
  targetId: string
  label: string
  count: number
  edgeGroupId?: string | null
  edgeIds: string[]
  edges: HomeGraphEdge[]
}

type NodeCard = HomeGraphCompound & {
  degree: number
  x: number
  y: number
}

export function CompoundGraphHome({
  onOpenSearch,
  onOpenNetwork,
  onOpenDownloads,
  onOpenEnzyme,
  onToggleQueue,
  isQueued,
  queueCount,
}: {
  onOpenSearch: (query?: string) => void
  onOpenNetwork: () => void
  onOpenDownloads: () => void
  onOpenEnzyme: (enzymeId: string) => void
  onToggleQueue: (id: string) => void
  isQueued: (id: string) => boolean
  queueCount: number
}) {
  const [graph, setGraph] = useState<HomeGraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [positions, setPositions] = useState<Record<string, Point>>({})
  const [selectedPairKey, setSelectedPairKey] = useState<string | null>(null)
  const [expandedEdges, setExpandedEdges] = useState<HomeGraphEdge[]>([])
  const [expandedLoading, setExpandedLoading] = useState(false)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [mode, setMode] = useState<HomeSearchMode>('enzymeItems')
  const [modeOpen, setModeOpen] = useState(false)
  const [datasetOpen, setDatasetOpen] = useState(false)
  const [controlsOpen, setControlsOpen] = useState(false)
  const [searchValue, setSearchValue] = useState('')
  const [selectedDatasetId, setSelectedDatasetId] = useState<(typeof homeDatasetOptions)[number]['id']>(homeDatasetOptions[0].id)
  const [nodeSize, setNodeSize] = useState(2.55)
  const [labelScale, setLabelScale] = useState(1)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const dragRef = useRef<{ id: string; offsetX: number; offsetY: number } | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    loadHomeGraph()
      .then((payload) => {
        if (cancelled) return
        setGraph(payload)
        const layout = createHomeLayout(payload)
        setPositions(layout.positions)
        setSelectedNodeId(layout.nodes[0]?.compoundId ?? null)
        setSelectedPairKey(null)
        setExpandedEdges([])
        setSelectedEdgeId(null)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Unable to load graph data')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const move = (event: PointerEvent) => {
      if (!dragRef.current || !svgRef.current) return
      const next = toSvgPoint(svgRef.current, event.clientX, event.clientY)
      const { id, offsetX, offsetY } = dragRef.current
      setPositions((current) => ({
        ...current,
        [id]: { x: clamp(next.x + offsetX, 6, 94), y: clamp(next.y + offsetY, 12, HOME_VIEWBOX_HEIGHT - 8) },
      }))
    }
    const up = () => {
      dragRef.current = null
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [])

  const viewModel = useMemo(() => createHomeViewModel(graph, positions, selectedPairKey, expandedEdges), [graph, positions, selectedPairKey, expandedEdges])
  const selectedPair = viewModel.pairs.find((pair) => pair.key === selectedPairKey) ?? null
  const selectedNode = viewModel.nodes.find((node) => node.compoundId === selectedNodeId) ?? null
  const pairEdges = selectedPairKey ? (expandedEdges.length > 0 ? expandedEdges : selectedPair?.edges ?? []).slice(0, HOME_MAX_EXPANDED_EDGES) : []
  const selectedPairTotal = selectedPair ? Math.max(selectedPair.count, selectedPair.edges.length) : 0
  const visibleEdgeCount = viewModel.pairs.reduce((sum, pair) => sum + Math.max(pair.count, pair.edges.length || 0), 0)
  const compoundName = (compoundId: string) => viewModel.nodes.find((node) => node.compoundId === compoundId)?.name || compoundId
  const selectedDataset = homeDatasetOptions.find((item) => item.id === selectedDatasetId) ?? homeDatasetOptions[0]

  useEffect(() => {
    if (selectedEdgeId && pairEdges.some((edge) => edge.edgeId === selectedEdgeId)) return
    setSelectedEdgeId(pairEdges[0]?.edgeId ?? null)
  }, [pairEdges, selectedEdgeId])

  const handlePairClick = async (pair: PairEntry) => {
    setSelectedPairKey(pair.key)
    setSelectedNodeId(null)
    if (pair.edges.length > 0 && pair.edges.length === pair.count) {
      const nextEdges = pair.edges.slice(0, HOME_MAX_EXPANDED_EDGES)
      setExpandedEdges(nextEdges)
      setSelectedEdgeId(nextEdges[0]?.edgeId ?? null)
      return
    }
    if (pair.edgeGroupId) {
      setExpandedLoading(true)
      try {
        const edges = await loadExpandedEdgeGroup(pair.edgeGroupId)
        const nextEdges = (edges.length > 0 ? edges : pair.edges).slice(0, HOME_MAX_EXPANDED_EDGES)
        setExpandedEdges(nextEdges)
        setSelectedEdgeId(nextEdges[0]?.edgeId ?? null)
      } finally {
        setExpandedLoading(false)
      }
      return
    }
    const nextEdges = pair.edges.slice(0, HOME_MAX_EXPANDED_EDGES)
    setExpandedEdges(nextEdges)
    setSelectedEdgeId(nextEdges[0]?.edgeId ?? null)
  }

  const clearPairSelection = () => {
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setSelectedNodeId(viewModel.nodes[0]?.compoundId ?? null)
  }

  const handleNodeSelect = (compoundId: string) => {
    setSelectedNodeId(compoundId)
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
  }

  const resetLayout = () => {
    if (!graph) return
    const layout = createHomeLayout(graph)
    setPositions(layout.positions)
    setSelectedNodeId(layout.nodes[0]?.compoundId ?? null)
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
  }

  const handleSearchSubmit = () => {
    const trimmed = searchValue.trim()
    setModeOpen(false)
    if (mode === 'pathways' || mode === 'mapsearch') {
      onOpenNetwork()
      return
    }
    onOpenSearch(trimmed || undefined)
  }

  const compoundImageUrl = (compound: HomeGraphCompound) => {
    const chebiId = compound.chebiId || compound.compoundId
    if (chebiId?.startsWith('CHEBI:')) return `/api/v1/assets/compounds/${encodeURIComponent(chebiId)}/structure.svg?v=4`
    return compound.structureImageUrl || null
  }

  const searchPlaceholder =
    mode === 'blast'
      ? 'Paste a protein sequence or accession'
      : mode === 'pathways'
        ? 'Search pathways, compound pairs, or reactions'
        : 'Search enzymes, substrates, or products'

  const selectedNeighborIds = new Set<string>()
  if (selectedNodeId) {
    selectedNeighborIds.add(selectedNodeId)
    viewModel.pairs.forEach((pair) => {
      if (pair.sourceId === selectedNodeId) selectedNeighborIds.add(pair.targetId)
      if (pair.targetId === selectedNodeId) selectedNeighborIds.add(pair.sourceId)
    })
  }
  if (selectedPair) {
    selectedNeighborIds.add(selectedPair.sourceId)
    selectedNeighborIds.add(selectedPair.targetId)
  }

  return (
    <div className="home-map-page">
      <section className="atlas-map-stage atlas-live-stage" aria-label="Interactive compound graph homepage">
        <div className="atlas-brand">
          <span className="atlas-logo">
            <Network size={18} />
          </span>
          <span>Starase Atlas</span>
        </div>

        <div className="atlas-year">NJU - China 2026</div>

        <div className={`floating-pill dataset-pill dataset-pill-static ${datasetOpen ? 'is-open' : ''}`}>
          <button className="dataset-pill-button" type="button" onClick={() => setDatasetOpen((open) => !open)}>
            <span>Dataset</span>
            <strong>{selectedDataset.label}</strong>
            <ChevronDown size={18} />
          </button>
          {datasetOpen && (
            <div className="floating-menu dataset-menu dataset-select-menu">
              {homeDatasetOptions.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  disabled={item.disabled}
                  className={item.id === selectedDatasetId ? 'is-active' : ''}
                  onClick={() => {
                    if (item.disabled) return
                    setSelectedDatasetId(item.id)
                    setDatasetOpen(false)
                  }}
                >
                  <span>{item.label}</span>
                  <small>{item.detail}</small>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="home-search-bar">
          <button className="home-search-mode" type="button" onClick={() => setModeOpen((open) => !open)}>
            <ChevronDown size={22} />
            <span>{homeSearchModes.find((item) => item.id === mode)?.label}</span>
          </button>
          {modeOpen && (
            <div className="floating-menu search-mode-menu">
              {homeSearchModes.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setMode(item.id)
                    setModeOpen(false)
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
          <input
            value={searchValue}
            onChange={(event) => setSearchValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') handleSearchSubmit()
            }}
            placeholder={searchPlaceholder}
          />
          <button className="home-search-submit" type="button" onClick={handleSearchSubmit} title="Search">
            <Search size={34} />
          </button>
        </div>

        <button className="floating-pill download-pill home-pill-button" type="button" onClick={onOpenDownloads}>
          Downloading table
          {queueCount > 0 && <span>{queueCount}</span>}
        </button>

        <div className={`floating-pill mapping-pill ${controlsOpen ? 'is-open' : ''}`}>
          <button type="button" onClick={() => setControlsOpen((open) => !open)}>
            <span>Graph controls</span>
            <ChevronDown size={18} />
          </button>
          {controlsOpen && (
            <div className="floating-menu source-menu compact-home-menu control-home-menu">
              <div className="control-group">
                <label htmlFor="home-node-size">Node size</label>
                <div className="control-slider-row">
                  <input id="home-node-size" className="control-slider" type="range" min="2" max="4.2" step="0.1" value={nodeSize} onChange={(event) => setNodeSize(Number(event.target.value))} />
                  <span className="control-value">{nodeSize.toFixed(1)}</span>
                </div>
              </div>
              <div className="control-group">
                <label htmlFor="home-label-size">Label size</label>
                <div className="control-slider-row">
                  <input id="home-label-size" className="control-slider" type="range" min="0.8" max="1.45" step="0.05" value={labelScale} onChange={(event) => setLabelScale(Number(event.target.value))} />
                  <span className="control-value">{labelScale.toFixed(2)}</span>
                </div>
              </div>
              <div className="control-menu-actions">
                <button type="button" onClick={() => { resetLayout(); setControlsOpen(false) }}>Reset layout</button>
                <button type="button" onClick={() => { clearPairSelection(); setControlsOpen(false) }}>Clear selection</button>
                <button type="button" onClick={() => { onOpenSearch(searchValue.trim() || undefined); setControlsOpen(false) }}>Open search library</button>
              </div>
            </div>
          )}
        </div>

        {loading && <div className="home-map-feedback"><Loader2 size={18} className="spin" /> Loading backend graph...</div>}
        {error && !loading && <div className="home-map-feedback error-state"><X size={18} /> {error}</div>}

        {!loading && !error && graph && (
          <svg ref={svgRef} className="home-map-svg home-live-map" viewBox={`0 0 100 ${HOME_VIEWBOX_HEIGHT}`} role="img" aria-label="Draggable compound graph">
            <defs>
              <marker id="home-map-arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" fill="rgba(249, 238, 201, 0.82)" />
              </marker>
              <filter id="home-node-glow" x="-60%" y="-60%" width="220%" height="220%">
                <feDropShadow dx="0" dy="0" stdDeviation="1.4" floodColor="rgba(247, 240, 214, 0.65)" />
              </filter>
              <filter id="selected-node-glow" x="-80%" y="-80%" width="260%" height="260%">
                <feDropShadow dx="0" dy="0" stdDeviation="2.1" floodColor="rgba(250, 214, 242, 0.75)" />
              </filter>
            </defs>

            <g className="home-map-edges live-map-edges">
              {viewModel.pairs.map((pair) => {
                const source = positions[pair.sourceId]
                const target = positions[pair.targetId]
                if (!source || !target) return null
                const isExpanded = selectedPairKey === pair.key && pairEdges.length > 0
                const edgeItems = isExpanded ? pairEdges : pair.edges
                const offsets = edgeItems.length > 1 ? edgeItems.map((_, index) => (index - (edgeItems.length - 1) / 2) * 3.2) : [0]
                const pairLineLabel = pair.count > 1 ? `enzyme*${pair.count}` : pair.edges[0]?.card?.primaryName || 'enzyme'
                return (
                  <g key={pair.key}>
                    {!isExpanded && (
                      <>
                        <path d={edgePath(source, target, 0)} className={`home-map-path ${pair.count > 1 ? 'multi' : ''} ${selectedPairKey === pair.key ? 'active' : ''}`} markerEnd="url(#home-map-arrow)" onClick={() => void handlePairClick(pair)} />
                        <path d={edgePath(source, target, 0)} className="home-map-hit" onClick={() => void handlePairClick(pair)} />
                        <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 1.8} className="home-edge-label" fontSize={1.02 * labelScale}>{pairLineLabel}</text>
                      </>
                    )}
                    {isExpanded && edgeItems.map((edge, index) => {
                      const offset = offsets[index] ?? 0
                      const label = edge.card?.primaryName || edge.label
                      return (
                        <g key={edge.edgeId}>
                          <path d={edgePath(source, target, offset)} className={`expanded-edge live-expanded-edge ${selectedEdgeId === edge.edgeId ? 'selected' : ''}`} markerEnd="url(#home-map-arrow)" onClick={() => setSelectedEdgeId(edge.edgeId)} />
                          <path d={edgePath(source, target, offset)} className="home-map-hit" onClick={() => setSelectedEdgeId(edge.edgeId)} />
                          <text x={(source.x + target.x) / 2 + offset * 0.34} y={(source.y + target.y) / 2 + offset * 0.45 - 1.4} className="expanded-edge-label" fontSize={0.94 * labelScale}>{label}</text>
                        </g>
                      )
                    })}
                  </g>
                )
              })}
            </g>

            <g className="home-map-nodes">
              {viewModel.nodes.map((node) => {
                const selected = node.compoundId === selectedNodeId || selectedPair?.sourceId === node.compoundId || selectedPair?.targetId === node.compoundId
                const neighbor = selectedNeighborIds.has(node.compoundId) && !selected
                const pos = positions[node.compoundId]
                if (!pos) return null
                return (
                  <g key={node.compoundId} className={`home-map-node ${selected ? 'selected' : neighbor ? 'neighbor' : ''}`}>
                    {selected && <circle className="selected-ring" cx={pos.x} cy={pos.y} r={nodeSize + 1.3} />}
                    <circle
                      cx={pos.x}
                      cy={pos.y}
                      r={nodeSize}
                      filter="url(#home-node-glow)"
                      onPointerDown={(event) => {
                        const point = toSvgPoint(svgRef.current!, event.clientX, event.clientY)
                        dragRef.current = { id: node.compoundId, offsetX: pos.x - point.x, offsetY: pos.y - point.y }
                        handleNodeSelect(node.compoundId)
                      }}
                      onClick={() => handleNodeSelect(node.compoundId)}
                    />
                    <text x={pos.x} y={pos.y + nodeSize + 4.8} className="home-map-node-name" fontSize={1.2 * labelScale}>{shortCompoundLabel(node)}</text>
                  </g>
                )
              })}
            </g>
          </svg>
        )}

        {!selectedPair && selectedNode && (
          <div className="compound-popover live-compound-popover">
            <div className="popover-heading">
              <strong>{selectedNode.name}</strong>
              <div className="popover-heading-actions">
                {selectedNode.chebiUrl ? (
                  <a className="popover-open-link" href={selectedNode.chebiUrl} target="_blank" rel="noreferrer" title="Open in ChEBI">
                    <ArrowUpRight size={20} />
                  </a>
                ) : null}
                <button className="popover-close-button" type="button" onClick={() => setSelectedNodeId(null)} title="Close compound card">
                  <X size={18} />
                </button>
              </div>
            </div>
            <div className="popover-id">ChEBI ID : {selectedNode.chebiId || selectedNode.compoundId}</div>
            <div className="compound-structure">
              {compoundImageUrl(selectedNode) ? <img src={compoundImageUrl(selectedNode) || undefined} alt={`${selectedNode.name} structure`} /> : <div className="structure-unavailable">No structure</div>}
            </div>
            <div className="popover-fields">
              <p><span>ID :</span><strong>{selectedNode.compoundId}</strong></p>
              {selectedNode.averageMass && <p><span>Mass :</span><strong>{selectedNode.averageMass}</strong></p>}
              {selectedNode.formula && <p><span>Formula :</span><strong>{selectedNode.formula}</strong></p>}
              {selectedNode.smiles && <p className="popover-smiles-row"><span>Smiles :</span><strong>{selectedNode.smiles}</strong></p>}
            </div>
            <button className="popover-cart" type="button" onClick={() => onToggleQueue(selectedNode.compoundId)}>
              <span className={`check-box ${isQueued(selectedNode.compoundId) ? 'checked' : ''}`}>{isQueued(selectedNode.compoundId) && <Check size={17} />}</span>
              {isQueued(selectedNode.compoundId) ? 'In downloading table' : 'Add to downloading table'}
            </button>
          </div>
        )}

        {selectedPair && (
          <div className="enzyme-card-stack live-enzyme-stack">
            <div className="stack-heading">
              <div>
                <strong>{compoundName(selectedPair.sourceId)} <ChevronRight size={14} /> {compoundName(selectedPair.targetId)}</strong>
                <span>{expandedLoading ? 'Loading enzyme paths...' : `${pairEdges.length} / ${selectedPairTotal} enzyme paths`}</span>
              </div>
              <button className="stack-close-button" type="button" onClick={clearPairSelection} title="Close enzyme list">
                <X size={18} />
              </button>
            </div>
            {pairEdges.map((edge) => {
              const enzymeId = edge.card?.enzymeId || edge.enzymeId
              const queued = isQueued(enzymeId)
              return (
                <article key={edge.edgeId} className={`enzyme-card ${selectedEdgeId === edge.edgeId ? 'selected' : ''}`}>
                  <button className="card-check" type="button" onClick={() => onToggleQueue(enzymeId)}>
                    {queued ? <Check size={18} /> : <Download size={18} />}
                  </button>
                  <button className="enzyme-card-copy" type="button" onClick={() => setSelectedEdgeId(edge.edgeId)}>
                    <h3>{edge.card?.primaryName || edge.label}</h3>
                    <p>{edge.card?.organismName || 'Unknown organism'}</p>
                    <p>{edge.card?.reactionEquation || edge.label}</p>
                  </button>
                  <div className="enzyme-card-meta">
                    <strong>{edge.card?.uniprotId || edge.card?.databaseCode || enzymeId}</strong>
                    <span>{edge.card?.ecNumber || 'EC n/a'}</span>
                    <small>{edge.card?.databaseCode || enzymeId}</small>
                    <button type="button" onClick={() => onOpenEnzyme(enzymeId)}>Open detail</button>
                  </div>
                </article>
              )
            })}
          </div>
        )}

        <div className="map-footer-stats home-map-stats">
          <span>Total compounds: {graph?.nodes.length ?? 0}</span>
          <span>Total enzyme edges: {graph?.edges.length ?? 0}</span>
          <span>Visible compound pairs: {viewModel.pairs.length}</span>
          <span>Visible map edges: {visibleEdgeCount}</span>
        </div>
      </section>
    </div>
  )
}

export function EnzymeDetailView({ enzymeId, onBack, onToggleQueue, isQueued }: { enzymeId: string | null; onBack: () => void; onToggleQueue: (id: string) => void; isQueued: (id: string) => boolean }) {
  const [detail, setDetail] = useState<EnzymeDetailData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloadState, setDownloadState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')

  useEffect(() => {
    if (!enzymeId) return
    let cancelled = false
    setLoading(true)
    setError(null)
    setDownloadState('idle')
    loadEnzymeDetail(enzymeId)
      .then((payload) => { if (!cancelled) setDetail(payload) })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : 'Unable to load enzyme detail') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [enzymeId])

  if (!enzymeId) return <div className="detail-page empty-detail-page"><div className="detail-empty-card"><Dna size={30} /><h2>No enzyme selected</h2><button className="primary-button" type="button" onClick={onBack}><ArrowLeft size={15} /> Back home</button></div></div>

  const queued = isQueued(enzymeId)
  const names = detail ? [detail.primaryName, ...detail.secondaryNames].filter(Boolean) : []
  const handleDownload = async () => {
    if (!detail) return
    setDownloadState('loading')
    try {
      const payload = await createEnzymeDownload(detail.enzymeId, detail.primaryName)
      if (payload.fileUrl) {
        window.open(payload.fileUrl, '_blank', 'noopener,noreferrer')
        setDownloadState('ready')
      } else {
        setDownloadState('error')
      }
    } catch {
      setDownloadState('error')
    }
  }

  return (
    <div className="enzyme-detail-page">
      <section className="detail-atlas-hero">
        <div>
          <div className="eyebrow"><Dna size={14} /> Enzyme detail</div>
          <h1>{detail?.primaryName || enzymeId}</h1>
          <p>{detail?.organismName || 'Loading detail from the backend...'}</p>
        </div>
        <div className="detail-hero-actions atlas-detail-actions">
          <button className="secondary-button" type="button" onClick={onBack}><ArrowLeft size={15} /> Back</button>
          <button className="secondary-button" type="button" onClick={() => onToggleQueue(enzymeId)}>{queued ? <Check size={15} /> : <Download size={15} />}{queued ? 'Queued' : 'Download'}</button>
          <button className="secondary-button" type="button" onClick={handleDownload} disabled={downloadState === 'loading'}>{downloadState === 'loading' ? <Loader2 size={15} className="spin" /> : <Download size={15} />} Export record</button>
        </div>
      </section>

      {loading && <div className="detail-status"><Loader2 size={18} className="spin" /> Loading enzyme detail...</div>}
      {error && <div className="detail-status error-state"><X size={18} /> {error}</div>}
      {detail && (
        <div className="enzyme-detail-grid">
          <section className="detail-card main-detail-card">
            <div className="detail-card-topline">
              <span className="detail-chip"><Link2 size={13} /> {detail.databaseCode}</span>
              {detail.uniprotId && <a className="detail-link" href={`https://www.uniprot.org/uniprotkb/${detail.uniprotId}`} target="_blank" rel="noreferrer">UniProt {detail.uniprotId} <ExternalLink size={12} /></a>}
            </div>
            <div className="detail-name-stack"><h2>{detail.primaryName}</h2><p>{detail.organismName || 'Unknown organism'}</p></div>
            <div className="tag-row compact">{names.map((name) => <span key={name} className="tag">{name}</span>)}</div>
            <dl className="detail-facts">
              <div><dt>Library code</dt><dd>{detail.databaseCode}</dd></div>
              <div><dt>Species</dt><dd>{detail.organismName || 'n/a'}</dd></div>
              <div><dt>UniProt</dt><dd>{detail.uniprotId || 'n/a'}</dd></div>
              <div><dt>Sequence</dt><dd>{detail.sequence ? `${detail.sequence.length} aa` : 'n/a'}</dd></div>
            </dl>
          </section>

          <section className="detail-card detail-stack-card"><div className="section-title-row"><h3>Gene</h3></div>{detail.gene ? <div className="detail-copy-list"><div><span>Gene name</span><strong>{detail.gene.geneName || 'n/a'}</strong></div><div><span>Gene ID</span><strong>{detail.gene.geneId ? <a href={detail.gene.ncbiUrl || `https://www.ncbi.nlm.nih.gov/gene/${detail.gene.geneId}`} target="_blank" rel="noreferrer">{detail.gene.geneId}</a> : 'n/a'}</strong></div><div><span>GenBank</span><strong>{detail.gene.genbankId || 'n/a'}</strong></div><div><span>ENA accession</span><strong>{detail.gene.enaAccession || 'n/a'}</strong></div><div><span>Protein accession</span><strong>{detail.gene.proteinAccession || 'n/a'}</strong></div></div> : <p className="muted-copy">No gene record available.</p>}</section>

          <section className="detail-card detail-stack-card"><div className="section-title-row"><h3>Evidence</h3></div><div className="detail-reference-list">{detail.evidence.length > 0 ? detail.evidence.map((item, index) => <div key={`${item.doi || item.pubmedId || index}`} className="reference-row"><div><strong>{item.sourceDescription || 'Evidence record'}</strong><p>{item.reviewStatus || 'official'}</p></div><div className="reference-links">{item.doi && <a href={`https://doi.org/${item.doi}`} target="_blank" rel="noreferrer">DOI</a>}{item.pubmedId && <a href={`https://pubmed.ncbi.nlm.nih.gov/${item.pubmedId}/`} target="_blank" rel="noreferrer">PubMed</a>}</div></div>) : <p className="muted-copy">No evidence links available.</p>}</div></section>

          <section className="detail-card detail-stack-card reactions-card"><div className="section-title-row"><h3>Reactions</h3></div><div className="reaction-list">{detail.reactions.map((reaction) => <article key={reaction.reactionId} className="reaction-card"><div className="reaction-card-head"><div><strong>{reaction.equation}</strong><p>{reaction.direction}</p></div>{reaction.rheaUrl ? <a href={reaction.rheaUrl} target="_blank" rel="noreferrer">{reaction.rheaId || 'Rhea'} <ExternalLink size={12} /></a> : <span>{reaction.rheaId || 'Rhea n/a'}</span>}</div><div className="reaction-meta-grid"><div><span>EC</span><strong>{reaction.ecNumber || 'n/a'}</strong></div><div><span>SMILES</span><strong>{reaction.smiles || 'n/a'}</strong></div><div><span>Source type</span><strong>{reaction.sourceType}</strong></div><div><span>Review</span><strong>{reaction.reviewStatus}</strong></div></div><div className="reaction-compounds"><div><span>Substrates</span><div className="tag-row compact">{reaction.substrates.map((compound) => <span key={compound.compoundId} className="tag">{compound.name}</span>)}</div></div><div><span>Products</span><div className="tag-row compact">{reaction.products.map((compound) => <span key={compound.compoundId} className="tag">{compound.name}</span>)}</div></div></div>{reaction.atomMapImageUrl && <div className="atom-map-wrap"><img src={reaction.atomMapImageUrl} alt={`${reaction.reactionId} atom map`} /></div>}</article>)}</div></section>

          <section className="detail-card detail-stack-card"><div className="section-title-row"><h3>Links</h3></div><div className="link-list">{detail.links.map((link) => <a key={`${link.label}:${link.url}`} href={link.url} target="_blank" rel="noreferrer"><span>{link.label}</span><ExternalLink size={12} /></a>)}</div></section>
        </div>
      )}
    </div>
  )
}

function createHomeLayout(graph: HomeGraphData | null) {
  if (!graph || graph.nodes.length === 0) return { nodes: [] as HomeGraphCompound[], positions: {} as Record<string, Point>, pairs: [] as PairEntry[] }
  const score = new Map<string, number>()
  const bump = (id: string, value = 1) => score.set(id, (score.get(id) || 0) + value)
  graph.edges.forEach((edge) => { bump(edge.sourceCompoundId); bump(edge.targetCompoundId) })
  graph.edgeGroups.forEach((group) => { bump(group.sourceCompoundId, group.count); bump(group.targetCompoundId, group.count) })
  const nodes = [...graph.nodes].sort((a, b) => (score.get(b.compoundId) || 0) - (score.get(a.compoundId) || 0) || a.name.localeCompare(b.name)).slice(0, HOME_NODE_LIMIT)
  const positions: Record<string, Point> = {}
  if (nodes.length > 0) positions[nodes[0].compoundId] = { x: 48, y: 58 }
  const remaining = nodes.slice(1)
  const ringBreak = Math.min(4, remaining.length)
  remaining.forEach((node, index) => {
    const outer = index >= ringBreak
    const ringIndex = outer ? index - ringBreak : index
    const ringCount = outer ? Math.max(remaining.length - ringBreak, 1) : Math.max(ringBreak, 1)
    const radius = outer ? 38 : 24
    const baseAngle = outer ? -Math.PI / 2 + Math.PI / 7 : -Math.PI / 2 - Math.PI / 6
    const spread = outer ? Math.PI * 1.86 : Math.PI * 1.52
    const angle = ringCount === 1 ? baseAngle + spread / 2 : baseAngle + (spread * ringIndex) / (ringCount - 1)
    positions[node.compoundId] = {
      x: clamp(48 + Math.cos(angle) * radius, 10, 90),
      y: clamp(58 + Math.sin(angle) * radius, 14, HOME_VIEWBOX_HEIGHT - 10),
    }
  })
  const selectedIds = new Set(nodes.map((node) => node.compoundId))
  const pairMap = new Map<string, PairEntry>()
  graph.edgeGroups.forEach((group) => {
    if (!selectedIds.has(group.sourceCompoundId) || !selectedIds.has(group.targetCompoundId)) return
    pairMap.set(pairKey(group.sourceCompoundId, group.targetCompoundId), { key: pairKey(group.sourceCompoundId, group.targetCompoundId), sourceId: group.sourceCompoundId, targetId: group.targetCompoundId, label: group.label, count: group.count, edgeGroupId: group.edgeGroupId, edgeIds: group.edgeIds, edges: [] })
  })
  graph.edges.forEach((edge) => {
    if (!selectedIds.has(edge.sourceCompoundId) || !selectedIds.has(edge.targetCompoundId)) return
    const key = pairKey(edge.sourceCompoundId, edge.targetCompoundId)
    const current = pairMap.get(key)
    const next: PairEntry = current || { key, sourceId: edge.sourceCompoundId, targetId: edge.targetCompoundId, label: edge.card?.primaryName || edge.label, count: 0, edgeIds: [], edges: [] }
    next.count = Math.max(next.count, 1)
    next.edgeIds = Array.from(new Set([...next.edgeIds, edge.edgeId]))
    next.edges = Array.from(new Map([...next.edges, edge].map((item) => [item.edgeId, item])).values())
    next.label = next.label || edge.card?.primaryName || edge.label
    pairMap.set(key, next)
  })
  return { nodes, positions, pairs: [...pairMap.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)) }
}

function createHomeViewModel(graph: HomeGraphData | null, positions: Record<string, Point>, selectedPairKey: string | null, expandedEdges: HomeGraphEdge[]) {
  if (!graph) return { nodes: [] as NodeCard[], pairs: [] as PairEntry[] }
  const base = createHomeLayout(graph)
  const nodes = base.nodes.map((node) => ({ ...node, degree: graph.edges.filter((edge) => edge.sourceCompoundId === node.compoundId || edge.targetCompoundId === node.compoundId).length, x: positions[node.compoundId]?.x ?? base.positions[node.compoundId]?.x ?? 50, y: positions[node.compoundId]?.y ?? base.positions[node.compoundId]?.y ?? 50 }))
  const pairMap = new Map(base.pairs.map((pair) => [pair.key, pair]))
  if (selectedPairKey && expandedEdges.length > 0) {
    const selectedPair = pairMap.get(selectedPairKey)
    if (selectedPair) pairMap.set(selectedPairKey, { ...selectedPair, edges: expandedEdges, count: Math.max(expandedEdges.length, selectedPair.count) })
  }
  return { nodes, pairs: [...pairMap.values()] }
}

function pairKey(sourceId: string, targetId: string) { return `${sourceId}::${targetId}` }
function edgePath(source: Point, target: Point, offset = 0) {
  const midX = (source.x + target.x) / 2
  const midY = (source.y + target.y) / 2
  const dx = target.x - source.x
  const dy = target.y - source.y
  const length = Math.max(Math.hypot(dx, dy), 0.001)
  const nx = -dy / length
  const ny = dx / length
  return `M ${source.x} ${source.y} Q ${midX + nx * offset} ${midY + ny * offset} ${target.x} ${target.y}`
}
function toSvgPoint(svg: SVGSVGElement, clientX: number, clientY: number) {
  const rect = svg.getBoundingClientRect()
  return { x: ((clientX - rect.left) / rect.width) * 100, y: ((clientY - rect.top) / rect.height) * 100 }
}
function clamp(value: number, min: number, max: number) { return Math.min(max, Math.max(min, value)) }
function shortCompoundLabel(compound: HomeGraphCompound) { return compound.name.length <= 14 ? compound.name : compound.name.split(/\s+/).slice(0, 2).join(' ').replace(/,.*$/, '') }



















