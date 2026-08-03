import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
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
  searchHomePathways,
  type EnzymeDetailData,
  type EnzymeSequenceLink,
  type HomeGraphCompound,
  type HomeGraphData,
  type HomeGraphEdge,
  type HomePathwayCard,
} from './api'

const HOME_MAX_EXPANDED_EDGES = 10
const HOME_EXPANSION_LIMIT = 36
const HOME_VIEWBOX_WIDTH = 100
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

type ExpansionDirection = 'left' | 'right' | 'top' | 'bottom'

type PanState = {
  pointerId: number
  startClientX: number
  startClientY: number
  originCamera: Point
  moved: boolean
}

type GraphSearchMatch =
  | { kind: 'node'; nodeId: string }
  | { kind: 'pair'; pair: PairEntry; edges: HomeGraphEdge[] }
  | { kind: 'none' }

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
  const [camera, setCamera] = useState<Point>({ x: 0, y: 0 })
  const [selectedPairKey, setSelectedPairKey] = useState<string | null>(null)
  const [expandedEdges, setExpandedEdges] = useState<HomeGraphEdge[]>([])
  const [expandedLoading, setExpandedLoading] = useState(false)
  const [mapExpanding, setMapExpanding] = useState(false)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [highlightedNodeIds, setHighlightedNodeIds] = useState<Set<string>>(new Set())
  const [highlightedEdgeIds, setHighlightedEdgeIds] = useState<Set<string>>(new Set())
  const [highlightedEdgeGroupIds, setHighlightedEdgeGroupIds] = useState<Set<string>>(new Set())
  const [activePathway, setActivePathway] = useState<HomePathwayCard | null>(null)
  const [searchFeedback, setSearchFeedback] = useState<string | null>(null)
  const [mode, setMode] = useState<HomeSearchMode>('enzymeItems')
  const [modeOpen, setModeOpen] = useState(false)
  const [datasetOpen, setDatasetOpen] = useState(false)
  const [controlsOpen, setControlsOpen] = useState(false)
  const [searchValue, setSearchValue] = useState('')
  const [selectedDatasetId, setSelectedDatasetId] = useState<(typeof homeDatasetOptions)[number]['id']>(homeDatasetOptions[0].id)
  const [nodeSize, setNodeSize] = useState(2.55)
  const [labelScale, setLabelScale] = useState(1)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const panRef = useRef<PanState | null>(null)
  const graphRef = useRef<HomeGraphData | null>(null)
  const positionsRef = useRef<Record<string, Point>>({})
  const cameraRef = useRef<Point>({ x: 0, y: 0 })
  const expansionKeysRef = useRef<Set<string>>(new Set())
  const expandingRef = useRef(false)

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
        setCamera({ x: 0, y: 0 })
        setSelectedNodeId(null)
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
    graphRef.current = graph
  }, [graph])

  useEffect(() => {
    positionsRef.current = positions
  }, [positions])

  useEffect(() => {
    cameraRef.current = camera
  }, [camera])

  const viewModel = useMemo(() => createHomeViewModel(graph, positions, selectedPairKey, expandedEdges), [graph, positions, selectedPairKey, expandedEdges])
  const selectedPair = viewModel.pairs.find((pair) => pair.key === selectedPairKey) ?? null
  const selectedNode = viewModel.nodes.find((node) => node.compoundId === selectedNodeId) ?? null
  const pairEdges = selectedPairKey ? (expandedEdges.length > 0 ? expandedEdges : selectedPair?.edges ?? []).slice(0, HOME_MAX_EXPANDED_EDGES) : []
  const selectedPairTotal = selectedPair ? Math.max(selectedPair.count, selectedPair.edges.length) : 0
  const visibleEdgeCount = viewModel.pairs.reduce((sum, pair) => sum + Math.max(pair.count, pair.edges.length || 0), 0)
  const compoundName = (compoundId: string) => viewModel.nodes.find((node) => node.compoundId === compoundId)?.name || compoundId
  const selectedDataset = homeDatasetOptions.find((item) => item.id === selectedDatasetId) ?? homeDatasetOptions[0]
  const selectedEdge = pairEdges.find((edge) => edge.edgeId === selectedEdgeId) || pairEdges[0] || null

  const focusCameraOnPoint = (point: Point, target: Point = { x: 58, y: 56 }) => {
    const nextCamera = { x: target.x - point.x, y: target.y - point.y }
    setCamera(nextCamera)
    cameraRef.current = nextCamera
  }

  const focusCameraOnNode = (compoundId: string, target: Point = { x: 38, y: 54 }) => {
    const point = positionsRef.current[compoundId]
    if (point) focusCameraOnPoint(point, target)
  }

  const focusCameraOnPair = (pair: PairEntry, target: Point = { x: 38, y: 54 }) => {
    const source = positionsRef.current[pair.sourceId]
    const targetNode = positionsRef.current[pair.targetId]
    if (!source || !targetNode) return
    focusCameraOnPoint({ x: (source.x + targetNode.x) / 2, y: (source.y + targetNode.y) / 2 }, target)
  }

  const expandFromViewportEdge = async (direction: ExpansionDirection) => {
    if (expandingRef.current) return
    const currentGraph = graphRef.current
    if (!currentGraph) return
    const seedId = chooseExpansionSeed(currentGraph, positionsRef.current, direction, expansionKeysRef.current)
    if (!seedId) return
    const expansionKey = `${direction}:${seedId}`
    if (expansionKeysRef.current.has(expansionKey)) return
    expandingRef.current = true
    expansionKeysRef.current.add(expansionKey)
    setMapExpanding(true)
    try {
      const payload = await loadHomeGraph({ centerCompoundId: seedId, depth: 1, limitNodes: HOME_EXPANSION_LIMIT })
      const merged = mergeHomeGraph(graphRef.current, payload)
      const previousPositionCount = Object.keys(positionsRef.current).length
      const nextPositions = addExpansionPositions(positionsRef.current, payload, seedId, direction)
      const addedCount = Object.keys(nextPositions).length - previousPositionCount
      graphRef.current = merged
      positionsRef.current = nextPositions
      setGraph(merged)
      setPositions(nextPositions)
      setSearchFeedback(addedCount > 0 ? `Expanded around ${compoundName(seedId)} (+${addedCount})` : `No new compounds beyond ${compoundName(seedId)}`)
    } catch (err) {
      setSearchFeedback(err instanceof Error ? err.message : 'Unable to expand this map area.')
    } finally {
      expandingRef.current = false
      setMapExpanding(false)
    }
  }

  const maybeExpandMapAtViewportEdge = () => {
    const direction = getViewportExpansionDirection(positionsRef.current, cameraRef.current)
    if (direction) void expandFromViewportEdge(direction)
  }

  const handleMapPointerDown = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return
    panRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      originCamera: cameraRef.current,
      moved: false,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handleMapPointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const panState = panRef.current
    const svg = svgRef.current
    if (!panState || panState.pointerId !== event.pointerId || !svg) return
    const rect = svg.getBoundingClientRect()
    const deltaX = ((event.clientX - panState.startClientX) / Math.max(rect.width, 1)) * HOME_VIEWBOX_WIDTH
    const deltaY = ((event.clientY - panState.startClientY) / Math.max(rect.height, 1)) * HOME_VIEWBOX_HEIGHT
    if (Math.abs(deltaX) > 0.8 || Math.abs(deltaY) > 0.8) panState.moved = true
    const nextCamera = { x: panState.originCamera.x + deltaX, y: panState.originCamera.y + deltaY }
    cameraRef.current = nextCamera
    setCamera(nextCamera)
  }

  const finishMapPan = (event: ReactPointerEvent<SVGSVGElement>) => {
    const panState = panRef.current
    if (!panState || panState.pointerId !== event.pointerId) return
    panRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    if (panState.moved) maybeExpandMapAtViewportEdge()
  }

  useEffect(() => {
    if (selectedEdgeId && pairEdges.some((edge) => edge.edgeId === selectedEdgeId)) return
    setSelectedEdgeId(pairEdges[0]?.edgeId ?? null)
  }, [pairEdges, selectedEdgeId])

  const handlePairClick = async (pair: PairEntry) => {
    setSelectedPairKey(pair.key)
    setSelectedNodeId(null)
    setActivePathway(null)
    setHighlightedNodeIds(new Set([pair.sourceId, pair.targetId]))
    setHighlightedEdgeGroupIds(new Set([pair.edgeGroupId || pair.key]))
    setSearchFeedback(null)
    focusCameraOnPair(pair)
    if (pair.edges.length > 0 && pair.edges.length === pair.count) {
      const nextEdges = pair.edges.slice(0, HOME_MAX_EXPANDED_EDGES)
      setExpandedEdges(nextEdges)
      setSelectedEdgeId(nextEdges[0]?.edgeId ?? null)
      setHighlightedEdgeIds(new Set(nextEdges.map((edge) => edge.edgeId)))
      return
    }
    if (pair.edgeGroupId) {
      setExpandedLoading(true)
      try {
        const edges = await loadExpandedEdgeGroup(pair.edgeGroupId)
        const nextEdges = (edges.length > 0 ? edges : pair.edges).slice(0, HOME_MAX_EXPANDED_EDGES)
        setExpandedEdges(nextEdges)
        setSelectedEdgeId(nextEdges[0]?.edgeId ?? null)
        setHighlightedEdgeIds(new Set(nextEdges.map((edge) => edge.edgeId)))
      } finally {
        setExpandedLoading(false)
      }
      return
    }
    const nextEdges = pair.edges.slice(0, HOME_MAX_EXPANDED_EDGES)
    setExpandedEdges(nextEdges)
    setSelectedEdgeId(nextEdges[0]?.edgeId ?? null)
    setHighlightedEdgeIds(new Set(nextEdges.map((edge) => edge.edgeId)))
  }

  const clearPairSelection = () => {
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setSelectedNodeId(null)
    setHighlightedNodeIds(new Set())
    setHighlightedEdgeIds(new Set())
    setHighlightedEdgeGroupIds(new Set())
    setActivePathway(null)
    setSearchFeedback(null)
  }

  const handleNodeSelect = (compoundId: string) => {
    setSelectedNodeId(compoundId)
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setActivePathway(null)
    setHighlightedNodeIds(new Set([compoundId]))
    setHighlightedEdgeIds(new Set())
    setHighlightedEdgeGroupIds(new Set())
    setSearchFeedback(null)
    focusCameraOnNode(compoundId)
  }

  const resetLayout = () => {
    if (!graph) return
    const layout = createHomeLayout(graph)
    setPositions(layout.positions)
    setCamera({ x: 0, y: 0 })
    setSelectedNodeId(null)
    setSelectedPairKey(null)
    setExpandedEdges([])
    setSelectedEdgeId(null)
    setHighlightedNodeIds(new Set())
    setHighlightedEdgeIds(new Set())
    setHighlightedEdgeGroupIds(new Set())
    setActivePathway(null)
    setSearchFeedback(null)
  }

  const handleSearchSubmit = async () => {
    const trimmed = searchValue.trim()
    setModeOpen(false)
    if (!trimmed) {
      clearPairSelection()
      return
    }
    if (mode === 'blast') {
      onOpenSearch(trimmed)
      return
    }
    if (mode === 'pathways') {
      await handlePathwaySearch(trimmed)
      return
    }
    handleGraphSearch(trimmed)
  }

  const handleGraphSearch = (query: string) => {
    if (!graph) return
    const match = findGraphSearchMatch(query, graph, viewModel.pairs)
    if (match.kind === 'node') {
      setSelectedNodeId(match.nodeId)
      setSelectedPairKey(null)
      setExpandedEdges([])
      setSelectedEdgeId(null)
      setActivePathway(null)
      setHighlightedNodeIds(new Set([match.nodeId]))
      setHighlightedEdgeIds(new Set())
      setHighlightedEdgeGroupIds(new Set())
      setSearchFeedback(`Focused compound: ${compoundName(match.nodeId)}`)
      focusCameraOnNode(match.nodeId)
      return
    }
    if (match.kind === 'pair') {
      const pair = match.pair
      setSelectedPairKey(pair.key)
      setSelectedNodeId(null)
      setActivePathway(null)
      const nextEdges = (match.edges.length > 0 ? match.edges : pair.edges).slice(0, HOME_MAX_EXPANDED_EDGES)
      setExpandedEdges(nextEdges)
      setSelectedEdgeId(nextEdges[0]?.edgeId ?? null)
      setHighlightedNodeIds(new Set([pair.sourceId, pair.targetId]))
      setHighlightedEdgeIds(new Set(nextEdges.map((edge) => edge.edgeId)))
      setHighlightedEdgeGroupIds(new Set([pair.edgeGroupId || pair.key]))
      setSearchFeedback(`Focused edge: ${compoundName(pair.sourceId)} -> ${compoundName(pair.targetId)}`)
      focusCameraOnPair(pair)
      return
    }
    setSearchFeedback('No match in the loaded map. Drag the map edge to expand, or open the search library.')
  }

  const handlePathwaySearch = async (query: string) => {
    if (!graph) return
    const endpoints = resolvePathwayEndpoints(query, graph.nodes)
    if (!endpoints) {
      setSearchFeedback('Pathway mode expects two compounds, for example CHEBI:15422 -> CHEBI:10280.')
      return
    }
    setLoading(true)
    try {
      const cards = await searchHomePathways(endpoints.startId, endpoints.endId)
      const pathway = cards[0]
      if (!pathway) {
        setSearchFeedback('No pathway found for those compounds.')
        return
      }
      const expansions = await Promise.all([
        loadHomeGraph({ centerCompoundId: endpoints.startId, depth: 1, limitNodes: HOME_EXPANSION_LIMIT }),
        loadHomeGraph({ centerCompoundId: endpoints.endId, depth: 1, limitNodes: HOME_EXPANSION_LIMIT }),
      ])
      const merged = expansions.reduce((current, payload) => mergeHomeGraph(current, payload), graphRef.current || graph)
      const withStart = addExpansionPositions(positionsRef.current, expansions[0], endpoints.startId, 'right')
      const nextPositions = addExpansionPositions(withStart, expansions[1], endpoints.endId, 'left')
      graphRef.current = merged
      positionsRef.current = nextPositions
      setGraph(merged)
      setPositions(nextPositions)
      setActivePathway(pathway)
      setSelectedNodeId(null)
      setSelectedPairKey(null)
      setExpandedEdges([])
      setSelectedEdgeId(null)
      setHighlightedNodeIds(new Set(pathway.compoundIds))
      setHighlightedEdgeIds(new Set(pathway.edgeIds))
      setHighlightedEdgeGroupIds(new Set(pathway.edgeGroupIds))
      setSearchFeedback(`Highlighted pathway: ${pathway.stepCount} steps`)
      focusCameraOnPath(pathway.compoundIds)
    } catch (err) {
      setSearchFeedback(err instanceof Error ? err.message : 'Unable to search pathway.')
    } finally {
      setLoading(false)
    }
  }

  const focusCameraOnPath = (compoundIds: string[]) => {
    const points = compoundIds.map((id) => positionsRef.current[id]).filter(Boolean)
    if (points.length === 0) return
    const center = {
      x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
      y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
    }
    focusCameraOnPoint(center, { x: 42, y: 56 })
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
              if (event.key === 'Enter') void handleSearchSubmit()
            }}
            placeholder={searchPlaceholder}
          />
          <button className="home-search-submit" type="button" onClick={() => void handleSearchSubmit()} title="Search">
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
        {mapExpanding && !loading && !error && <div className="home-map-feedback map-expanding-feedback"><Loader2 size={18} className="spin" /> Expanding map...</div>}
        {searchFeedback && !loading && !error && <div className="home-search-feedback">{searchFeedback}</div>}

        {!loading && !error && graph && (
          <svg
            ref={svgRef}
            className="home-map-svg home-live-map"
            viewBox={`0 0 ${HOME_VIEWBOX_WIDTH} ${HOME_VIEWBOX_HEIGHT}`}
            role="img"
            aria-label="Draggable compound graph"
            onPointerDown={handleMapPointerDown}
            onPointerMove={handleMapPointerMove}
            onPointerUp={finishMapPan}
            onPointerCancel={finishMapPan}
          >
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
            <rect className="home-map-pan-layer" x="0" y="0" width={HOME_VIEWBOX_WIDTH} height={HOME_VIEWBOX_HEIGHT} />

            <g className="home-map-camera" transform={`translate(${camera.x} ${camera.y})`}>
              <g className="home-map-edges live-map-edges">
                {viewModel.pairs.map((pair) => {
                  const source = positions[pair.sourceId]
                  const target = positions[pair.targetId]
                  if (!source || !target) return null
                  const pairGroupId = pair.edgeGroupId || pair.key
                  const isExpanded = selectedPairKey === pair.key && pairEdges.length > 0
                  const edgeItems = isExpanded ? pairEdges : pair.edges
                  const offsets = edgeItems.length > 1 ? edgeItems.map((_, index) => (index - (edgeItems.length - 1) / 2) * 3.2) : [0]
                  const pairLineLabel = pair.count > 1 ? `enzyme*${pair.count}` : pair.edges[0]?.card?.primaryName || 'enzyme'
                  const highlightedPair = highlightedEdgeGroupIds.has(pairGroupId) || pair.edgeIds.some((edgeId) => highlightedEdgeIds.has(edgeId))
                  const pathwayPair = Boolean(activePathway && (activePathway.edgeGroupIds.includes(pairGroupId) || pair.edgeIds.some((edgeId) => activePathway.edgeIds.includes(edgeId))))
                  return (
                    <g key={pair.key} className="home-map-edge-group">
                      {!isExpanded && (
                        <>
                          <path
                            d={edgePath(source, target, 0)}
                            className={`home-map-path ${pair.count > 1 ? 'multi' : ''} ${selectedPairKey === pair.key ? 'active' : ''} ${highlightedPair ? 'highlighted' : ''} ${pathwayPair ? 'pathway' : ''}`}
                            markerEnd="url(#home-map-arrow)"
                            onPointerDown={(event) => event.stopPropagation()}
                            onClick={(event) => { event.stopPropagation(); void handlePairClick(pair) }}
                          />
                          <path d={edgePath(source, target, 0)} className="home-map-hit" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); void handlePairClick(pair) }} />
                          <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 1.8} className="home-edge-label" fontSize={1.02 * labelScale}>{pairLineLabel}</text>
                        </>
                      )}
                      {isExpanded && edgeItems.map((edge, index) => {
                        const offset = offsets[index] ?? 0
                        const label = edge.card?.primaryName || edge.label
                        const highlightedEdge = highlightedPair || highlightedEdgeIds.has(edge.edgeId)
                        const pathwayEdge = Boolean(activePathway?.edgeIds.includes(edge.edgeId))
                        return (
                          <g key={edge.edgeId}>
                            <path
                              d={edgePath(source, target, offset)}
                              className={`expanded-edge live-expanded-edge ${selectedEdgeId === edge.edgeId ? 'selected' : ''} ${highlightedEdge ? 'highlighted' : ''} ${pathwayEdge ? 'pathway' : ''}`}
                              markerEnd="url(#home-map-arrow)"
                              onPointerDown={(event) => event.stopPropagation()}
                              onClick={(event) => { event.stopPropagation(); setSelectedEdgeId(edge.edgeId) }}
                            />
                            <path d={edgePath(source, target, offset)} className="home-map-hit" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); setSelectedEdgeId(edge.edgeId) }} />
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
                  const pairEndpoint = selectedPair?.sourceId === node.compoundId || selectedPair?.targetId === node.compoundId
                  const selected = node.compoundId === selectedNodeId
                  const highlighted = highlightedNodeIds.has(node.compoundId)
                  const pathway = Boolean(activePathway?.compoundIds.includes(node.compoundId))
                  const neighbor = selectedNeighborIds.has(node.compoundId) && !selected && !pairEndpoint
                  const pos = positions[node.compoundId]
                  if (!pos) return null
                  return (
                    <g key={node.compoundId} className={`home-map-node ${selected || pairEndpoint ? 'selected' : ''} ${highlighted ? 'highlighted' : ''} ${pathway ? 'pathway' : ''} ${neighbor ? 'neighbor' : ''}`}>
                      <circle
                        cx={pos.x}
                        cy={pos.y}
                        r={nodeSize}
                        filter={selected || highlighted || pathway || pairEndpoint ? 'url(#selected-node-glow)' : 'url(#home-node-glow)'}
                        onPointerDown={(event) => event.stopPropagation()}
                        onClick={(event) => { event.stopPropagation(); handleNodeSelect(node.compoundId) }}
                      />
                      <title>{node.name}</title>
                      <text x={pos.x} y={pos.y + nodeSize + 4.8} className="home-map-node-name" fontSize={1.05 * labelScale}>
                        {wrapCompoundLabel(node.name).map((line, lineIndex) => (
                          <tspan key={`${node.compoundId}:label:${lineIndex}`} x={pos.x} dy={lineIndex === 0 ? 0 : '1.2em'}>{line}</tspan>
                        ))}
                      </text>
                    </g>
                  )
                })}
              </g>
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

        {activePathway && !selectedPair && !selectedNode && (
          <div className="pathway-result-card live-pathway-card">
            <div className="stack-heading pathway-heading">
              <div>
                <strong>Pathway result</strong>
                <span>{activePathway.stepCount} steps</span>
              </div>
              <button className="stack-close-button" type="button" onClick={clearPairSelection} title="Close pathway card">
                <X size={18} />
              </button>
            </div>
            <p>{activePathway.summary}</p>
            <div className="pathway-route-list">
              {activePathway.compoundIds.map((compoundId, index) => (
                <span key={`${activePathway.pathwayId}:${compoundId}:${index}`}>{compoundName(compoundId)}</span>
              ))}
            </div>
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
  const sequenceRows = detail?.sequence ? formatSequenceRows(detail.sequence) : []
  const sequenceLength = detail?.length || detail?.sequence?.length || null
  const groupedSequenceLinks = groupSequenceLinks(detail?.sequenceLinks || [])
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
              <div><dt>Length</dt><dd>{sequenceLength ? `${sequenceLength} aa` : 'n/a'}</dd></div>
              <div><dt>Mass (Da)</dt><dd>{detail.mass ? Math.round(detail.mass).toLocaleString() : 'n/a'}</dd></div>
            </dl>
          </section>

          <section className="detail-card detail-stack-card">
            <div className="section-title-row"><h3>Gene</h3></div>
            {detail.gene ? (
              <div className="detail-copy-list">
                <div><span>Gene name</span><strong>{detail.gene.geneName || 'n/a'}</strong></div>
                <div><span>GenBank</span><strong>{detail.gene.genbankId || 'n/a'}</strong></div>
                <div><span>ENA accession</span><strong>{detail.gene.enaAccession || 'n/a'}</strong></div>
                <div><span>Protein accession</span><strong>{detail.gene.proteinAccession || 'n/a'}</strong></div>
              </div>
            ) : <p className="muted-copy">No gene record available.</p>}
          </section>

          <section className="detail-card detail-stack-card sequence-links-card">
            <div className="section-title-row"><h3>Sequence links</h3></div>
            {groupedSequenceLinks.length > 0 ? (
              <div className="sequence-link-groups">
                {groupedSequenceLinks.map((group) => (
                  <div key={group.category} className="sequence-link-group">
                    <span>{group.category}</span>
                    <div>
                      {group.links.map((link) => (
                        <a key={`${link.category}:${link.accession}:${link.relatedAccession || ''}`} href={link.url || link.relatedUrl || '#'} target="_blank" rel="noreferrer">
                          <strong>{link.accession}</strong>
                          {link.relatedAccession && <small>{link.relatedAccession}</small>}
                          <ExternalLink size={12} />
                        </a>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : <p className="muted-copy">No sequence links available.</p>}
          </section>

          <section className="detail-card detail-stack-card amino-sequence-card">
            <div className="section-title-row">
              <h3>Amino acid sequence</h3>
              {detail.sequence && <button className="small-text-button" type="button" onClick={() => void navigator.clipboard?.writeText(detail.sequence || '')}>Copy</button>}
            </div>
            {detail.sequence ? (
              <>
                <div className="sequence-summary">
                  <div><span>Length</span><strong>{sequenceLength || detail.sequence.length}</strong></div>
                  <div><span>Mass (Da)</span><strong>{detail.mass ? Math.round(detail.mass).toLocaleString() : 'n/a'}</strong></div>
                </div>
                <div className="amino-sequence-view" aria-label="Amino acid sequence">
                  {sequenceRows.map((row) => (
                    <div key={row.start} className="amino-sequence-row">
                      <div className="sequence-ruler"><span />{row.chunks.map((chunk, index) => <span key={`${row.start}:${index}`}>{row.start + index * 10 + chunk.length - 1}</span>)}</div>
                      <div className="sequence-line"><span>{row.start}</span><code>{row.chunks.join(' ')}</code></div>
                    </div>
                  ))}
                </div>
              </>
            ) : <p className="muted-copy">No amino acid sequence available.</p>}
          </section>

          <section className="detail-card detail-stack-card"><div className="section-title-row"><h3>Evidence</h3></div><div className="detail-reference-list">{detail.evidence.length > 0 ? detail.evidence.map((item, index) => <div key={`${item.doi || item.pubmedId || index}`} className="reference-row"><div><strong>{item.sourceDescription || 'Evidence record'}</strong><p>{item.reviewStatus || 'official'}</p></div><div className="reference-links">{item.doi && <a href={`https://doi.org/${item.doi}`} target="_blank" rel="noreferrer">DOI</a>}{item.pubmedId && <a href={`https://pubmed.ncbi.nlm.nih.gov/${item.pubmedId}/`} target="_blank" rel="noreferrer">PubMed</a>}</div></div>) : <p className="muted-copy">No evidence links available.</p>}</div></section>

          <section className="detail-card detail-stack-card reactions-card"><div className="section-title-row"><h3>Reactions</h3></div><div className="reaction-list">{detail.reactions.map((reaction) => <article key={reaction.reactionId} className="reaction-card"><div className="reaction-card-head"><div><strong>{reaction.equation}</strong><p>{reaction.direction}</p></div>{reaction.rheaUrl ? <a href={reaction.rheaUrl} target="_blank" rel="noreferrer">{reaction.rheaId || 'Rhea'} <ExternalLink size={12} /></a> : <span>{reaction.rheaId || 'Rhea n/a'}</span>}</div><div className="reaction-meta-grid"><div><span>EC</span><strong>{reaction.ecNumber || 'n/a'}</strong></div><div><span>SMILES</span><strong>{reaction.smiles || 'n/a'}</strong></div><div><span>Source type</span><strong>{reaction.sourceType}</strong></div><div><span>Review</span><strong>{reaction.reviewStatus}</strong></div></div><div className="reaction-compounds"><div><span>Substrates</span><div className="tag-row compact">{reaction.substrates.map((compound) => <span key={compound.compoundId} className="tag">{compound.name}</span>)}</div></div><div><span>Products</span><div className="tag-row compact">{reaction.products.map((compound) => <span key={compound.compoundId} className="tag">{compound.name}</span>)}</div></div></div>{reaction.atomMapImageUrl && <div className="atom-map-wrap"><img src={reaction.atomMapImageUrl} alt={`${reaction.reactionId} atom map`} /></div>}</article>)}</div></section>

          <section className="detail-card detail-stack-card"><div className="section-title-row"><h3>Links</h3></div><div className="link-list">{detail.links.map((link) => <a key={`${link.label}:${link.url}`} href={link.url} target="_blank" rel="noreferrer"><span>{link.label}</span><ExternalLink size={12} /></a>)}</div></section>
        </div>
      )}
    </div>
  )
}

type SequenceRow = {
  start: number
  chunks: string[]
}

function formatSequenceRows(sequence: string): SequenceRow[] {
  const clean = sequence.replace(/\s+/g, '').toUpperCase()
  const rows: SequenceRow[] = []
  for (let index = 0; index < clean.length; index += 60) {
    const line = clean.slice(index, index + 60)
    const chunks = line.match(/.{1,10}/g) || []
    rows.push({ start: index + 1, chunks })
  }
  return rows
}

function groupSequenceLinks(links: EnzymeSequenceLink[]) {
  const grouped = new Map<string, EnzymeSequenceLink[]>()
  links.forEach((link) => {
    if (!link.accession) return
    const current = grouped.get(link.category) || []
    current.push(link)
    grouped.set(link.category, current)
  })
  return Array.from(grouped.entries()).map(([category, groupLinks]) => ({ category, links: groupLinks }))
}

function createHomeLayout(graph: HomeGraphData | null) {
  if (!graph || graph.nodes.length === 0) return { nodes: [] as HomeGraphCompound[], positions: {} as Record<string, Point>, pairs: [] as PairEntry[] }
  const score = buildHomeDegreeScore(graph)
  const nodes = [...graph.nodes].sort((a, b) => (score.get(b.compoundId) || 0) - (score.get(a.compoundId) || 0) || a.name.localeCompare(b.name))
  const positions = createInitialHomePositions(nodes)
  const visibleIds = new Set(nodes.map((node) => node.compoundId))
  return { nodes, positions, pairs: buildHomePairs(graph, visibleIds) }
}

function buildHomePairs(graph: HomeGraphData, visibleIds: Set<string>) {
  const pairMap = new Map<string, PairEntry>()
  graph.edgeGroups.forEach((group) => {
    if (!visibleIds.has(group.sourceCompoundId) || !visibleIds.has(group.targetCompoundId)) return
    pairMap.set(pairKey(group.sourceCompoundId, group.targetCompoundId), { key: pairKey(group.sourceCompoundId, group.targetCompoundId), sourceId: group.sourceCompoundId, targetId: group.targetCompoundId, label: group.label, count: group.count, edgeGroupId: group.edgeGroupId, edgeIds: group.edgeIds, edges: [] })
  })
  graph.edges.forEach((edge) => {
    if (!visibleIds.has(edge.sourceCompoundId) || !visibleIds.has(edge.targetCompoundId)) return
    const key = pairKey(edge.sourceCompoundId, edge.targetCompoundId)
    const current = pairMap.get(key)
    const next: PairEntry = current || { key, sourceId: edge.sourceCompoundId, targetId: edge.targetCompoundId, label: edge.card?.primaryName || edge.label, count: 0, edgeIds: [], edges: [] }
    next.count = Math.max(next.count, 1)
    next.edgeIds = Array.from(new Set([...next.edgeIds, edge.edgeId]))
    next.edges = Array.from(new Map([...next.edges, edge].map((item) => [item.edgeId, item])).values())
    next.label = next.label || edge.card?.primaryName || edge.label
    pairMap.set(key, next)
  })
  return [...pairMap.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}

function buildHomeDegreeScore(graph: HomeGraphData) {
  const score = new Map<string, number>()
  const bump = (id: string, value = 1) => score.set(id, (score.get(id) || 0) + value)
  graph.edges.forEach((edge) => { bump(edge.sourceCompoundId); bump(edge.targetCompoundId) })
  graph.edgeGroups.forEach((group) => { bump(group.sourceCompoundId, group.count); bump(group.targetCompoundId, group.count) })
  return score
}

function createInitialHomePositions(nodes: HomeGraphCompound[]) {
  const positions: Record<string, Point> = {}
  const center = { x: 50, y: 58 }
  nodes.forEach((node, index) => {
    if (index === 0) {
      positions[node.compoundId] = center
      return
    }
    let ringStart = 1
    let ring = 1
    while (index >= ringStart + homeRingCapacity(ring)) {
      ringStart += homeRingCapacity(ring)
      ring += 1
    }
    const ringIndex = index - ringStart
    const ringCount = Math.min(homeRingCapacity(ring), nodes.length - ringStart)
    const angleOffset = ring % 2 === 0 ? Math.PI / Math.max(ringCount, 1) : 0
    const angle = -Math.PI / 2 + angleOffset + (Math.PI * 2 * ringIndex) / Math.max(ringCount, 1)
    const radius = 18 + ring * 12
    positions[node.compoundId] = {
      x: clamp(center.x + Math.cos(angle) * radius * 0.82, 5.5, HOME_VIEWBOX_WIDTH - 5.5),
      y: clamp(center.y + Math.sin(angle) * radius * 0.94, 7, HOME_VIEWBOX_HEIGHT - 7),
    }
  })
  return positions
}

function homeRingCapacity(ring: number) {
  if (ring === 1) return 8
  if (ring === 2) return 14
  return 20 + (ring - 3) * 8
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

function mergeHomeGraph(base: HomeGraphData | null, addition: HomeGraphData | null): HomeGraphData {
  const seed = base || { nodes: [], edges: [], edgeGroups: [] }
  if (!addition) return seed
  const nodes = new Map(seed.nodes.map((node) => [node.compoundId, node]))
  addition.nodes.forEach((node) => nodes.set(node.compoundId, { ...nodes.get(node.compoundId), ...node }))

  const edges = new Map(seed.edges.map((edge) => [edge.edgeId, edge]))
  addition.edges.forEach((edge) => edges.set(edge.edgeId, { ...edges.get(edge.edgeId), ...edge }))

  const edgeGroups = new Map(seed.edgeGroups.map((group) => [group.edgeGroupId, { ...group, edgeIds: [...group.edgeIds] }]))
  addition.edgeGroups.forEach((group) => {
    const current = edgeGroups.get(group.edgeGroupId)
    if (!current) {
      edgeGroups.set(group.edgeGroupId, { ...group, edgeIds: [...group.edgeIds] })
      return
    }
    const edgeIds = Array.from(new Set([...current.edgeIds, ...group.edgeIds]))
    edgeGroups.set(group.edgeGroupId, { ...current, ...group, edgeIds, count: Math.max(current.count, group.count, edgeIds.length) })
  })

  return {
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    edgeGroups: [...edgeGroups.values()],
  }
}

function addExpansionPositions(current: Record<string, Point>, payload: HomeGraphData, seedId: string, direction: ExpansionDirection) {
  const next = { ...current }
  const seed = next[seedId] || averageHomePosition(next) || { x: HOME_VIEWBOX_WIDTH / 2, y: HOME_VIEWBOX_HEIGHT / 2 }
  const score = buildHomeDegreeScore(payload)
  const incomingNodes = payload.nodes
    .filter((node) => !next[node.compoundId])
    .sort((a, b) => (score.get(b.compoundId) || 0) - (score.get(a.compoundId) || 0) || a.name.localeCompare(b.name))

  const normal = expansionNormal(direction)
  const tangent = { x: -normal.y, y: normal.x }
  const laneCount = Math.min(9, Math.max(1, incomingNodes.length))

  incomingNodes.forEach((node, index) => {
    const row = Math.floor(index / laneCount)
    const rowStart = row * laneCount
    const rowItems = Math.min(laneCount, incomingNodes.length - rowStart)
    const slot = index - rowStart
    const lateral = (slot - (rowItems - 1) / 2) * 9.5
    const depth = 19 + row * 16 + Math.abs(slot - (rowItems - 1) / 2) * 0.8
    const jitter = stableJitter(node.compoundId)
    next[node.compoundId] = {
      x: seed.x + normal.x * depth + tangent.x * lateral + jitter.x,
      y: seed.y + normal.y * depth + tangent.y * lateral + jitter.y,
    }
  })

  return next
}

function expansionNormal(direction: ExpansionDirection): Point {
  if (direction === 'left') return { x: -1, y: 0 }
  if (direction === 'right') return { x: 1, y: 0 }
  if (direction === 'top') return { x: 0, y: -1 }
  return { x: 0, y: 1 }
}

function stableJitter(value: string): Point {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) hash = (hash * 31 + value.charCodeAt(index)) >>> 0
  return {
    x: ((hash % 17) - 8) * 0.12,
    y: (((hash >> 5) % 17) - 8) * 0.12,
  }
}

function averageHomePosition(positions: Record<string, Point>) {
  const points = Object.values(positions)
  if (points.length === 0) return null
  return {
    x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
    y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
  }
}

function getViewportExpansionDirection(positions: Record<string, Point>, camera: Point): ExpansionDirection | null {
  const points = Object.values(positions)
  if (points.length === 0) return null
  const minX = Math.min(...points.map((point) => point.x))
  const maxX = Math.max(...points.map((point) => point.x))
  const minY = Math.min(...points.map((point) => point.y))
  const maxY = Math.max(...points.map((point) => point.y))
  const visible = {
    left: -camera.x,
    right: HOME_VIEWBOX_WIDTH - camera.x,
    top: -camera.y,
    bottom: HOME_VIEWBOX_HEIGHT - camera.y,
  }
  const expansionPadding = 10
  const candidates: Array<{ direction: ExpansionDirection; overflow: number }> = [
    { direction: 'left', overflow: minX - visible.left },
    { direction: 'right', overflow: visible.right - maxX },
    { direction: 'top', overflow: minY - visible.top },
    { direction: 'bottom', overflow: visible.bottom - maxY },
  ]
  const winner = candidates.filter((candidate) => candidate.overflow > expansionPadding).sort((a, b) => b.overflow - a.overflow)[0]
  return winner?.direction ?? null
}

function chooseExpansionSeed(graph: HomeGraphData, positions: Record<string, Point>, direction: ExpansionDirection, attemptedKeys: Set<string> = new Set()) {
  const score = buildHomeDegreeScore(graph)
  const nodes = graph.nodes.filter((node) => positions[node.compoundId] && !attemptedKeys.has(`${direction}:${node.compoundId}`))
  const axis = direction === 'left' || direction === 'right' ? 'x' : 'y'
  const ascending = direction === 'left' || direction === 'top'
  nodes.sort((a, b) => {
    const aPoint = positions[a.compoundId]
    const bPoint = positions[b.compoundId]
    const axisDelta = ascending ? aPoint[axis] - bPoint[axis] : bPoint[axis] - aPoint[axis]
    if (Math.abs(axisDelta) > 0.001) return axisDelta
    return (score.get(b.compoundId) || 0) - (score.get(a.compoundId) || 0) || a.name.localeCompare(b.name)
  })
  return nodes[0]?.compoundId ?? null
}

function findGraphSearchMatch(query: string, graph: HomeGraphData, pairs: PairEntry[]): GraphSearchMatch {
  const normalizedQuery = normalizeSearchText(query)
  if (!normalizedQuery) return { kind: 'none' }
  const exactNode = graph.nodes.find((node) => [node.compoundId, node.chebiId, node.name].some((value) => normalizeSearchText(value) === normalizedQuery))
  if (exactNode) return { kind: 'node', nodeId: exactNode.compoundId }
  const fuzzyNode = graph.nodes.find((node) => [node.compoundId, node.chebiId, node.name, node.formula, node.smiles].some((value) => normalizeSearchText(value).includes(normalizedQuery)))
  if (fuzzyNode) return { kind: 'node', nodeId: fuzzyNode.compoundId }

  const compoundNames = new Map(graph.nodes.map((node) => [node.compoundId, node.name]))
  for (const pair of pairs) {
    const pairValues = [
      pair.key,
      pair.edgeGroupId,
      pair.label,
      pair.sourceId,
      pair.targetId,
      compoundNames.get(pair.sourceId),
      compoundNames.get(pair.targetId),
    ]
    const edgeMatches = pair.edges.filter((edge) => homeEdgeMatches(edge, normalizedQuery))
    if (edgeMatches.length > 0 || pairValues.some((value) => normalizeSearchText(value).includes(normalizedQuery))) {
      return { kind: 'pair', pair, edges: edgeMatches }
    }
  }
  return { kind: 'none' }
}

function homeEdgeMatches(edge: HomeGraphEdge, normalizedQuery: string) {
  const values = [
    edge.edgeId,
    edge.edgeGroupId,
    edge.reactionId,
    edge.enzymeId,
    edge.label,
    edge.direction,
    edge.sourceType,
    edge.reviewStatus,
    edge.card?.primaryName,
    edge.card?.uniprotId,
    edge.card?.databaseCode,
    edge.card?.organismName,
    edge.card?.ecNumber,
    edge.card?.reactionId,
    edge.card?.reactionEquation,
  ]
  return values.some((value) => normalizeSearchText(value).includes(normalizedQuery))
}

function resolvePathwayEndpoints(query: string, nodes: HomeGraphCompound[]) {
  const separators = [/\s*(?:->|=>|-->|→|到|至)\s*/i, /\s+\bto\b\s+/i, /\s*[，,;；]\s*/]
  for (const separator of separators) {
    const parts = query.split(separator).map((part) => part.trim()).filter(Boolean)
    if (parts.length >= 2) {
      const [startToken, ...endTokens] = parts
      if (!startToken || endTokens.length === 0) continue
      const startId = resolveHomeCompoundToken(startToken, nodes)
      const endId = resolveHomeCompoundToken(endTokens.join(' '), nodes)
      if (startId && endId && startId !== endId) return { startId, endId }
    }
  }
  const idMatches = query.match(/CHEBI:\d+|[A-Z]{2,}[-_:]?\d{2,}/gi) || []
  if (idMatches.length >= 2) {
    const [startToken, endToken] = idMatches
    if (!startToken || !endToken) return null
    const startId = resolveHomeCompoundToken(startToken, nodes)
    const endId = resolveHomeCompoundToken(endToken, nodes)
    if (startId && endId && startId !== endId) return { startId, endId }
  }
  return null
}

function resolveHomeCompoundToken(token: string, nodes: HomeGraphCompound[]) {
  const normalizedToken = normalizeSearchText(token)
  if (!normalizedToken) return null
  const exact = nodes.find((node) => [node.compoundId, node.chebiId, node.name].some((value) => normalizeSearchText(value) === normalizedToken))
  if (exact) return exact.compoundId
  const fuzzy = nodes.find((node) => [node.compoundId, node.chebiId, node.name].some((value) => normalizeSearchText(value).includes(normalizedToken)))
  return fuzzy?.compoundId ?? null
}

function normalizeSearchText(value: string | number | null | undefined) {
  return String(value ?? '').toLowerCase().replace(/\s+/g, ' ').trim()
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
function clamp(value: number, min: number, max: number) { return Math.min(max, Math.max(min, value)) }
function wrapCompoundLabel(name: string) {
  const clean = name.replace(/\s+/g, ' ').trim()
  if (!clean) return ['Unknown compound']
  const maxLineLength = 20
  const rows: string[] = []
  let current = ''
  const pushCurrent = () => {
    if (!current.trim()) return
    rows.push(current.trim())
    current = ''
  }
  const appendPart = (part: string) => {
    let rest = part
    while (rest.length > 0) {
      const next = current ? `${current}${rest}` : rest.trimStart()
      if (next.length <= maxLineLength) {
        current = next
        return
      }
      if (current.trim()) {
        pushCurrent()
        continue
      }
      rows.push(rest.slice(0, maxLineLength))
      rest = rest.slice(maxLineLength)
    }
  }

  clean.split(/(\s+|-)/).forEach((part) => {
    if (!part) return
    appendPart(part)
  })
  pushCurrent()
  return rows.length > 0 ? rows : [clean]
}



















